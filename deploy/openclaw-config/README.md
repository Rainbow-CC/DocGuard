# OpenClaw 最小迁移配置

本目录保存 DocGuard 验证部署所需的可版本管理配置，不是本机
`~/.openclaw` 的完整备份。

## 文件

- `docguard-openclaw.json5`：Gateway、HTTP API、MiniMax 模型供应商和插件配置。
- `audit-runtime.agents.json5`：`audit-runtime` 与
  `tech-audit-structure-reviewer` 两个 Agent，以及共用的受限 sandbox 策略。

两个 Agent 在本机的实际定义来自 `~/.openclaw/openclaw.json` 的
`agents.list`。以下文件属于凭据或运行状态，迁移时不要复制，也不要提交 Git：

- `~/.openclaw/openclaw.json` 原文件；
- `agents/*/agent/models.json`；
- `agents/*/agent/*.sqlite*`；
- `agents/*/sessions/`；
- `workspace*/openclaw-workspace-state.json`；
- 工作区中的临时图片、scratch、memory 和其他会话产物。

## 运行时变量

Compose 从 `deploy/.env` 向 OpenClaw 注入以下变量：

```dotenv
OPENCLAW_GATEWAY_TOKEN=<生成一个新的 Gateway Token>
MINIMAX_API_KEY=<MiniMax API Key>
OPENCLAW_HOST_HOME=/root/.openclaw-docguard
DOCGUARD_RUNTIME_HOST=/opt/docguard/runtime
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

真实值只能写入服务器的 `deploy/.env` 或密钥管理系统，不能写进本目录。

## 安装到服务器

在仓库根目录执行：

```bash
install -d -m 700 /root/.openclaw-docguard
install -m 600 deploy/openclaw-config/docguard-openclaw.json5 \
  /root/.openclaw-docguard/docguard-openclaw.json5
install -m 600 deploy/openclaw-config/audit-runtime.agents.json5 \
  /root/.openclaw-docguard/audit-runtime.agents.json5
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
