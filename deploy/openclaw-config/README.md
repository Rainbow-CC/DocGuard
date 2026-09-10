# OpenClaw 最小迁移配置

本目录保存 DocGuard 验证部署所需的可版本管理配置，不是本机
`~/.openclaw` 的完整备份。

## 文件

- `docguard-openclaw.json5`：Gateway、HTTP API、MiniMax 模型供应商和插件配置。
- `audit-runtime.agents.json5`：`audit-runtime` 与
  `tech-audit-structure-reviewer` 两个 Agent，以及共用的受限 sandbox 策略。
  Sandbox 显式以 `10001:10001` 运行，与 DocGuard runtime 的属主一致；不要
  删除 `docker.user`，否则 OpenClaw 会以 UID 1000 创建容器并导致工件目录
  `Permission denied`。

两个 Agent 在本机的实际定义来自 `~/.openclaw/openclaw.json` 的
`agents.entries`。以下文件属于凭据或运行状态，迁移时不要复制，也不要提交 Git：

- `~/.openclaw/openclaw.json` 原文件；
- `agents/*/agent/models.json`；
- `agents/*/agent/*.sqlite*`；
- `agents/*/sessions/`；
- `workspace*/openclaw-workspace-state.json`；
- 工作区中的临时图片、scratch、memory 和其他会话产物。

## 运行时变量

Compose 从 `deploy/.env` 向 OpenClaw 注入以下变量。路径必须按服务器实际
用户名和仓库位置修改，不能照抄：

```dotenv
OPENCLAW_GATEWAY_TOKEN=<生成一个新的 Gateway Token>
MINIMAX_API_KEY=<MiniMax API Key>
OPENCLAW_HOST_HOME=/opt/openclaw-docguard-state
DOCGUARD_RUNTIME_HOST=/absolute/path/to/DocGuard/deploy/runtime
OPENCLAW_SANDBOX_MODE=all
OPENCLAW_EXEC_HOST=sandbox
OPENCLAW_GATEWAY_BIND=lan
```

原生验证部署没有 Docker sandbox 时改为：

```dotenv
OPENCLAW_SANDBOX_MODE=off
OPENCLAW_EXEC_HOST=gateway
OPENCLAW_GATEWAY_BIND=loopback
```

`OPENCLAW_API_TOKEN` 与 Gateway 收到的 `OPENCLAW_GATEWAY_TOKEN` 是同一个值；
应使用 `openssl rand -hex 32` 等方式生成，不能沿用示例固定 Token。真实值只能
写入服务器的 `deploy/.env` 或密钥管理系统，不能写进本目录。

## Compose 部署

在仓库根目录执行：

```bash
install -d -m 700 /home/<user>/.openclaw-docguard
install -m 600 deploy/openclaw-config/docguard-openclaw.json5 \
  /home/<user>/.openclaw-docguard/docguard-openclaw.json5
install -m 600 deploy/openclaw-config/audit-runtime.agents.json5 \
  /home/<user>/.openclaw-docguard/audit-runtime.agents.json5
```

配置文件只声明 Skill 名称，不包含 Skill 内容。启动 Gateway 后还要分别安装：

```bash
docker compose -f deploy/compose.yaml exec -T openclaw openclaw skills install \
  /path/to/DocGuard/doc-audit-integrate-skill \
  --agent audit-runtime \
  --as docx-tech-architecture-audit

docker compose -f deploy/compose.yaml exec -T openclaw openclaw skills install \
  /path/to/DocGuard/tect-doc-structure-audit-skill \
  --agent tech-audit-structure-reviewer \
  --as docx-tech-format-audit
```

最后验证配置和 Agent：

```bash
docker compose -f deploy/compose.yaml exec -T openclaw openclaw config validate
docker compose -f deploy/compose.yaml exec -T openclaw openclaw agents list
docker compose -f deploy/compose.yaml exec -T openclaw openclaw skills check \
  --agent audit-runtime
docker compose -f deploy/compose.yaml exec -T openclaw openclaw skills check \
  --agent tech-audit-structure-reviewer
```
