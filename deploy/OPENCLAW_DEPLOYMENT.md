# DocGuard 与 OpenClaw 部署记录

本文记录在 WSL Ubuntu 上将 DocGuard 与 OpenClaw Gateway 部署到同一个 Docker Compose 栈的实际配置步骤。

## 运行架构

```text
浏览器 -> 127.0.0.1:8000 -> DocGuard -> http://openclaw:18789/v1 -> audit-runtime
                                                        |
                                                        +-> 每次审核一个 Docker sandbox
```

OpenClaw Gateway 不发布宿主机端口，仅通过 Compose 内部 DNS 名称 `openclaw` 为 DocGuard 提供服务。审核 Agent 的工具调用在独立 sandbox 中执行。

## 前置条件

以下命令对应项目 `deploy/README.md` 的单机 Linux 部署准备过程。已安装 Docker 和已初始化 OpenClaw 时，重复执行检查命令不会影响已有配置。

```bash
# 1. 确认 Docker Engine 与 Docker Compose v2 已可用。
docker --version
docker compose version

# 2. 拉取项目并进入仓库根目录。
git clone https://github.com/Rainbow-CC/DocGuard.git \
  /home/ubuntu/docgurad-deploy-test/DocGuard
cd /home/ubuntu/docgurad-deploy-test/DocGuard

# 3. 创建仅供本机使用的环境配置和持久化运行目录。
cp deploy/.env.example deploy/.env
mkdir -p deploy/runtime
sudo chown -R 10001:10001 deploy/runtime
chmod 750 deploy/runtime

# 4. 验证运行目录权限。
stat -c '%u:%g %a %n' deploy/runtime

# 5. 确认 OpenClaw 已初始化并存在状态配置。
test -s ~/.openclaw/openclaw.json
```

预期 `stat` 输出以 `10001:10001 750` 开头。OpenClaw 的模型认证应在完成初始化后已可调用；本部署复用 `~/.openclaw` 中的状态、认证信息和 Gateway Token。

## 文件与环境变量

在 `.env` 中配置以下非密钥项：

```dotenv
OPENCLAW_GATEWAY_URL=http://openclaw:18789/v1
OPENCLAW_HOST_HOME=/home/ubuntu/.openclaw
DOCGUARD_REPO_HOST=/home/ubuntu/docgurad-deploy-test/DocGuard
DOCGUARD_RUNTIME_HOST=/home/ubuntu/docgurad-deploy-test/DocGuard/deploy/runtime
DOCKER_GID=1001
```

`OPENCLAW_API_TOKEN` 必须与 `~/.openclaw/openclaw.json` 的 Gateway token 一致。不要将 Token、模型 API Key 或视觉模型 API Key 提交到 Git。

## 镜像构建

构建 sandbox 镜像：

```bash
cd /home/ubuntu/docgurad-deploy-test/DocGuard/deploy
docker build -t openclaw-docguard-sandbox:2026.6.10 -f Dockerfile.openclaw-sandbox .
```

构建 Gateway 镜像：

```bash
docker build -t docguard-openclaw:2026.6.10 -f Dockerfile.openclaw .
```

`Dockerfile.openclaw` 基于官方 OpenClaw 镜像，额外安装 Docker CLI。Gateway 需要该 CLI 和 Docker socket 来创建 sibling sandbox；socket 不会挂载进 sandbox。

`Dockerfile.openclaw-sandbox` 使用 UID/GID `10001:10001`，并只安装审核所需的 `bash`、`python3`、`jq` 与 `ripgrep`。

## 启动服务

原生 systemd Gateway 会与 Compose Gateway 争用端口，首次迁移时停用它：

```bash
systemctl --user disable --now openclaw-gateway.service
```

启动或更新整个栈：

```bash
cd /home/ubuntu/docgurad-deploy-test/DocGuard/deploy
docker compose up -d --force-recreate
docker compose ps
```

预期结果：

- `docguard` 健康，发布 `127.0.0.1:8000`；
- `openclaw` 健康，仅在 Compose 网络中暴露 `18789`。

## Gateway 与 Agent 配置

启用 OpenResponses：

```bash
docker compose exec -T openclaw \
  openclaw config set gateway.http.endpoints.responses.enabled true
```

创建审核 Agent：

```bash
docker compose exec -T openclaw openclaw agents add audit-runtime \
  --workspace /home/ubuntu/.openclaw/workspace-audit-runtime \
  --model minimax/MiniMax-M3 \
  --non-interactive
```

安装项目 skill：

```bash
docker compose exec -T openclaw openclaw skills install \
  /home/ubuntu/docgurad-deploy-test/DocGuard/doc-audit-integrate-skill \
  --agent audit-runtime \
  --as docx-tech-architecture-audit
```

在 `~/.openclaw/openclaw.json` 的 `audit-runtime` 条目中，配置以下安全策略：

```json5
{
  skills: ["docx-tech-architecture-audit"],
  sandbox: {
    mode: "all",
    backend: "docker",
    scope: "session",
    workspaceAccess: "ro",
    docker: {
      image: "openclaw-docguard-sandbox:2026.6.10",
      readOnlyRoot: true,
      network: "none",
      capDrop: ["ALL"],
      binds: [
        "/home/ubuntu/docgurad-deploy-test/DocGuard/deploy/runtime:/var/lib/docguard:rw"
      ],
      dangerouslyAllowExternalBindSources: true
    }
  },
  tools: {
    allow: ["read", "exec"],
    deny: ["write", "edit", "apply_patch", "process", "browser", "canvas", "nodes", "cron", "gateway", "image"],
    exec: { host: "sandbox" },
    elevated: { enabled: false }
  }
}
```

由于当前 DocGuard 将证据与结果置于同一 attempt 目录，第一档方案将整个 `runtime` 以读写方式挂载。skill 和 DocGuard 工件校验会约束写入行为，但这不是证据目录的强制只读边界。

## 验证

```bash
cd /home/ubuntu/docgurad-deploy-test/DocGuard/deploy
docker compose exec -T openclaw openclaw skills check --agent audit-runtime
docker compose exec -T openclaw openclaw sandbox explain --agent audit-runtime
```

从 DocGuard 容器验证 Gateway：

```bash
docker compose exec -T docguard /app/.venv/bin/python -c '
from urllib.request import Request, urlopen
import os
request = Request(
    "http://openclaw:18789/v1/models",
    headers={"Authorization": "Bearer " + os.environ["OPENCLAW_API_TOKEN"]},
)
print(urlopen(request, timeout=10).status)
'
```

预期返回 `200`。随后上传真实 DOCX 并创建 `agent_backend: "openclaw"` 的任务，即可验证完整审核链路。

## 本次变更

- `compose.yaml`：新增 OpenClaw 服务，并让 DocGuard 等待 Gateway 健康后启动。
- `.env`：配置 DocGuard 到 Compose 内 Gateway 的访问地址、运行时宿主机路径与 Docker socket GID。
- `Dockerfile.openclaw`：新增携带 Docker CLI 的 Gateway 镜像。
- `Dockerfile.openclaw-sandbox`：新增受限审核 sandbox 镜像。
- `~/.openclaw/openclaw.json`：开启 Responses、创建 `audit-runtime`、安装并限制审核 skill、启用 per-session sandbox。
- 旧的 `openclaw-gateway.service`：已禁用，避免与 Docker Gateway 发生端口冲突。
