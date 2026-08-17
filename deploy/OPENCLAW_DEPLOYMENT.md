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

以下命令对应项目 `deploy/README.md` 的单机 Linux 部署准备过程。Docker Gateway 使用独立状态目录 `~/.openclaw-docguard`，不会读取或修改本机原生 OpenClaw 的 `~/.openclaw`。

```bash
# 1. 确认 Docker Engine 与 Docker Compose v2 已可用。
docker --version
docker compose version

# 2. 拉取项目并进入仓库根目录。
git clone https://github.com/Rainbow-CC/DocGuard.git \
  /home/ubuntu/docguard-deploy-test/DocGuard
cd /home/ubuntu/docguard-deploy-test/DocGuard

# 3. 创建仅供本机使用的环境配置和持久化运行目录。
# 已有 .env 时不覆盖，按后文将新增变量合并进去。
test -f deploy/.env || cp deploy/.env.example deploy/.env
mkdir -p deploy/runtime
sudo chown -R 10001:10001 deploy/runtime
sudo chmod 750 deploy/runtime

# 4. 验证运行目录权限。
stat -c '%u:%g %a %n' deploy/runtime

# 5. 初始化 Docker 专用的 OpenClaw 状态目录和配置文件。
# 若这两个文件已存在，停止而非覆盖，避免误改已部署的 Docker Gateway。
OPENCLAW_STATE_HOME="$HOME/.openclaw-docguard"
if [ -e "$OPENCLAW_STATE_HOME/docguard-openclaw.json5" ] \
  || [ -e "$OPENCLAW_STATE_HOME/audit-runtime.agents.json5" ]; then
  echo "专用 OpenClaw 配置已存在：$OPENCLAW_STATE_HOME" >&2
  exit 1
fi
install -d -m 700 "$OPENCLAW_STATE_HOME"
install -d -m 700 "$OPENCLAW_STATE_HOME/workspace-audit-runtime"
install -m 600 deploy/openclaw-config/docguard-openclaw.json5 \
  "$OPENCLAW_STATE_HOME/docguard-openclaw.json5"
install -m 600 deploy/openclaw-config/audit-runtime.agents.json5 \
  "$OPENCLAW_STATE_HOME/audit-runtime.agents.json5"
```

预期 `stat` 输出以 `10001:10001 750` 开头。`openclaw` 服务会加入补充组 `10001`，以便校验和创建指向 `runtime` 的 sandbox bind mount；目录仍不对 Gateway 开放写权限，因此不要把它放宽为 `755`。若将 `.env` 中的 `OPENCLAW_HOST_HOME` 改为其他目录，必须将上面 `OPENCLAW_STATE_HOME` 改为完全相同的宿主机路径。专用目录由 Compose 挂载给 Docker Gateway；本机的 `~/.openclaw/openclaw.json`、Agent、skill、认证和 session 都不会被复用。

## 文件与环境变量

在 `deploy/.env` 中配置以下路径和密钥。`OPENCLAW_API_TOKEN` 应是此 Docker Gateway 的独立随机 Token，不要填写本机原生 Gateway 的 Token：

```dotenv
OPENCLAW_GATEWAY_URL=http://openclaw:18789/v1
OPENCLAW_API_TOKEN=<为 Docker Gateway 单独生成的 Token>
OPENCLAW_HOST_HOME=/home/ubuntu/.openclaw-docguard
MINIMAX_API_KEY=<MiniMax API Key>
DOCGUARD_REPO_HOST=/home/ubuntu/docguard-deploy-test/DocGuard
DOCGUARD_RUNTIME_HOST=/home/ubuntu/docguard-deploy-test/DocGuard/deploy/runtime
DOCKER_GID=<stat -c '%g' /var/run/docker.sock 的输出>
```

`compose.yaml` 将 `OPENCLAW_API_TOKEN` 映射为 Gateway 端的 `OPENCLAW_GATEWAY_TOKEN`，并在专用配置文件中引用它；因此 DocGuard 发出的 Bearer Token 与 Docker Gateway 完全一致。`MINIMAX_API_KEY` 传给 `openclaw` 服务以供审核 Agent 调用模型。当前 `docguard` 服务也通过 `env_file: .env` 读取该变量；不要将 Token、模型 API Key 或视觉模型 API Key 提交到 Git。

若已有旧版 `deploy/.env`，不要重新复制或覆盖它；至少手动补入 `OPENCLAW_HOST_HOME`、`MINIMAX_API_KEY`、`DOCGUARD_REPO_HOST`、`DOCGUARD_RUNTIME_HOST` 和 `DOCKER_GID`。其中 `OPENCLAW_HOST_HOME` 必须是 `~/.openclaw-docguard`（或你选择的另一个新目录），不能是本机的 `~/.openclaw`。

### 改动位置

要调整 Docker 审核实例，只改以下位置：

- `deploy/.env`：状态目录、Gateway Token、MiniMax Key、仓库与运行目录的宿主机路径。
- `~/.openclaw-docguard/docguard-openclaw.json5`：Gateway 级配置；通常无需手改。
- `~/.openclaw-docguard/audit-runtime.agents.json5`：审核 Agent、sandbox 和工具策略。

不要为本部署修改 `~/.openclaw/openclaw.json`，也不要对本机原生 Agent 运行 `agents add` 或 `skills install`。

## 镜像构建

构建 sandbox 镜像：

```bash
cd /home/ubuntu/docguard-deploy-test/DocGuard/deploy
docker build -t openclaw-docguard-sandbox:2026.6.10 -f Dockerfile.openclaw-sandbox .
```

构建 Gateway 镜像：

```bash
docker build -t docguard-openclaw:2026.6.10 -f Dockerfile.openclaw .
```

`Dockerfile.openclaw` 基于官方 OpenClaw 镜像，额外安装 Docker CLI。Gateway 需要该 CLI 和 Docker socket 来创建 sibling sandbox；socket 不会挂载进 sandbox。

`Dockerfile.openclaw-sandbox` 使用 UID/GID `10001:10001`，并只安装审核所需的 `bash`、`python3`、`jq` 与 `ripgrep`。

## 启动服务

Compose Gateway 不发布宿主机端口，且使用独立状态目录，因此无需停用本机原生 `openclaw-gateway.service`。两者可以同时运行；DocGuard 只会通过 Compose 网络中的 `openclaw:18789` 访问 Docker Gateway。

启动或更新整个栈：

```bash
cd /home/ubuntu/docguard-deploy-test/DocGuard/deploy
docker compose up -d --build --force-recreate
docker compose ps
```

预期结果：

- `docguard` 健康，发布 `127.0.0.1:8000`；
- `openclaw` 健康，仅在 Compose 网络中暴露 `18789`。

## 恢复原生 systemd Gateway

本方案不会停用或改写本机原生 Gateway，因此通常无需恢复操作。若你按旧版本文档执行过 `systemctl --user disable --now openclaw-gateway.service`，可用以下命令恢复其原有的持续启用状态：

```bash
cd /home/ubuntu/docguard-deploy-test/DocGuard/deploy
docker compose down
systemctl --user enable --now openclaw-gateway.service
systemctl --user is-enabled openclaw-gateway.service
systemctl --user status --no-pager openclaw-gateway.service
```

预期 `is-enabled` 返回 `enabled`。如果 Docker daemon 已关闭，跳过 `docker compose down`，直接执行后面的 `enable --now` 即可。

该操作恢复的是原生 systemd 服务的持续启用状态，而不是只做一次临时启动。Docker Gateway 的状态和配置位于 `~/.openclaw-docguard`，与原生 `~/.openclaw` 隔离，所以无需回滚本机的 `openclaw.json`、认证、Agent 或 skill。

## Gateway 与 Agent 配置

`deploy/openclaw-config/docguard-openclaw.json5` 已启用 OpenResponses，并通过 `$include` 引入 `audit-runtime.agents.json5`。后者声明了 `audit-runtime`、skill 白名单和 sandbox 安全策略；复制到 `~/.openclaw-docguard/` 后即是 Docker Gateway 唯一读取的 Agent 配置。

因此不要再执行 `openclaw agents add audit-runtime`：模板已创建该 Agent，而本机 `~/.openclaw` 中已有的 Agent 或 skill 不会出现在这个独立实例中。

首次部署时安装项目 skill：

```bash
docker compose exec -T openclaw openclaw skills install \
  /home/ubuntu/docguard-deploy-test/DocGuard/doc-audit-integrate-skill \
  --agent audit-runtime \
  --as docx-tech-architecture-audit
```

重复部署前先检查；若 skill 已存在，不要重复执行安装命令或附加 `--force`：

```bash
docker compose exec -T openclaw openclaw config validate
docker compose exec -T openclaw openclaw agents list
docker compose exec -T openclaw openclaw skills check --agent audit-runtime
docker compose exec -T openclaw openclaw sandbox explain --agent audit-runtime
```

若需要调整安全策略，只编辑 `~/.openclaw-docguard/audit-runtime.agents.json5`，不编辑 `~/.openclaw/openclaw.json`。该文件中的 `${OPENCLAW_HOST_HOME}` 和 `${DOCGUARD_RUNTIME_HOST}` 由 Compose 注入；改动 `.env` 的对应路径后无需再硬编码修改策略文件。改完策略后重建 sandbox：

```bash
docker compose exec -T openclaw openclaw sandbox recreate --agent audit-runtime
```

由于当前 DocGuard 将证据与结果置于同一 attempt 目录，策略仍将整个 `runtime` 以读写方式挂载。skill 和 DocGuard 工件校验会约束写入行为，但这不是证据目录的强制只读边界。

## 验证

```bash
cd /home/ubuntu/docguard-deploy-test/DocGuard/deploy
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
- `deploy/openclaw-config/docguard-openclaw.json5`：Docker Gateway 的专用入口配置，启用 Responses 并引入 Agent 配置。
- `deploy/openclaw-config/audit-runtime.agents.json5`：`audit-runtime` 的 skill 白名单、per-session sandbox 和工具权限策略。
- `~/.openclaw-docguard/`：运行时复制的 Docker 专用状态和配置；本机 `~/.openclaw/` 与 `openclaw-gateway.service` 保持不变。
