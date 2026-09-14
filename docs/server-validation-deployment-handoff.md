# 服务器验证部署交接说明

本文记录 DocGuard 在 Linux 服务器上的验证性部署布局，供后续排障、迁移和继续设计使用。本文不包含 SSH 密码、API Key、Gateway Token 等秘密值。

## 1. 部署概况

- 服务器：`47.79.35.184`  root/ubuntuTokyo!
- 操作系统：Ubuntu 24.04 LTS，Linux x86_64
- 项目目录：`/opt/docguard`
- Git 分支：`ui-optimization`
- 本次核对的服务器提交：`22db2de`
- Compose 文件：`/opt/docguard/deploy/compose.yaml`
- Compose 环境文件：`/opt/docguard/deploy/.env`
- Compose 项目名：`deploy`
- DocGuard 监听地址：`127.0.0.1:8000`

当前主要容器：

| 容器 | 镜像 | 用途 |
| --- | --- | --- |
| `deploy-docguard-1` | `docguard:demo` | FastAPI、预处理、SQLite、报告工件以及容器内 DSH SDK/runtime |
| `deploy-openclaw-1` | `docguard-openclaw:2026.9.3` | OpenClaw Gateway，接收 DocGuard 的 OpenResponses 请求 |

OpenClaw 审核时还会按 session 创建基于 `openclaw-docguard-sandbox:2026.9.3` 的临时 sandbox 容器。它不是常驻的第三个业务服务。

## 2. 项目与部署配置

仓库内的重要部署文件：

| 路径 | 作用 |
| --- | --- |
| `/opt/docguard/deploy/compose.yaml` | DocGuard 与 OpenClaw 的 Compose 编排 |
| `/opt/docguard/deploy/Dockerfile` | DocGuard 镜像，包含 Python SDK、预处理依赖及两个审核 Skill |
| `/opt/docguard/deploy/docker-entrypoint.sh` | 初始化容器内 DSH Home、缓存目录和 Skill 链接 |
| `/opt/docguard/deploy/Dockerfile.openclaw` | OpenClaw Gateway 镜像 |
| `/opt/docguard/deploy/Dockerfile.openclaw-sandbox` | OpenClaw 审核 sandbox 镜像 |
| `/opt/docguard/deploy/dsh-settings.yaml` | DSH 的 MiniMax provider 与默认模型模板 |
| `/opt/docguard/deploy/openclaw-config/docguard-openclaw.json5` | OpenClaw Gateway 主配置模板 |
| `/opt/docguard/deploy/openclaw-config/audit-runtime.agents.json5` | 两个 OpenClaw Agent、Skill 白名单和 sandbox 策略 |
| `/opt/docguard/deploy/.env` | 服务器真实环境变量；包含秘密，不得提交 Git |
| `/opt/docguard/deploy/.env.example` | 可提交的环境变量示例，不含真实秘密 |

更新代码并重建：

```bash
cd /opt/docguard
git pull --ff-only origin ui-optimization
docker compose --env-file deploy/.env -f deploy/compose.yaml up -d --build
docker compose --env-file deploy/.env -f deploy/compose.yaml ps
```

不要执行 `docker compose down -v`，也不要删除 `deploy/runtime/`。

## 3. 环境变量

真实值位于 `/opt/docguard/deploy/.env`。当前使用的重要变量分组如下。

### DocGuard

- `DOCGUARD_BIND_ADDRESS`
- `DOCGUARD_PORT`
- `DOCGUARD_DATABASE_PATH`
- `DOCGUARD_LOG_FILE`
- `DOCGUARD_LOG_LEVEL`
- `DOCGUARD_LOG_RETENTION_DAYS`
- `DOCGUARD_RUNTIME_HOST`
- `DOCGUARD_RESULT_WRITE_ROOT`
- `DOCGUARD_RESULT_AGENT_ROOT`
- `DOCGUARD_UPLOAD_WRITE_ROOT`
- `DOCGUARD_UPLOAD_AGENT_ROOT`
- `DOCGUARD_SKILL_AGENT_ROOT`
- `DOCGUARD_PREPROCESS_COMMAND`
- `DOCGUARD_DEFAULT_AGENT_BACKEND`

### 视觉模型

- `DASHSCOPE_API_KEY`
- `DOCGUARD_QWEN_BASE_URL`
- `DOCGUARD_QWEN_VISION_MODEL`

### OpenClaw

- `OPENCLAW_GATEWAY_URL`
- `OPENCLAW_API_TOKEN`
- `OPENCLAW_HOST_HOME`
- `OPENCLAW_SANDBOX_MODE`
- `OPENCLAW_EXEC_HOST`
- `OPENCLAW_GATEWAY_BIND`
- `MINIMAX_API_KEY`
- `DOCKER_GID`

### DeepSeek Harness（DSH）

- `DOCGUARD_DSH_HOME=/var/lib/docguard/dsh`
- `DOCGUARD_DSH_PROVIDER=minimax-cn`
- `DOCGUARD_DSH_MODEL=MiniMax-M3`
- `DOCGUARD_DSH_PROFILE=sdk`
- `DOCGUARD_DSH_MAX_TOKENS=49152`
- `DOCGUARD_DSH_SKILL_SET_ROOT=/app/agent-skill-sets`
- `DOCGUARD_RUNTIME_ROUTES_PATH=/app/deploy/runtime-routes.json`
- `MINIMAX_CN_API_KEY`：DSH 的 MiniMax 凭据，不得写入 `settings.yaml`

修改 `.env` 后，仅执行 `docker compose restart` 不会刷新容器环境。应执行：

```bash
cd /opt/docguard
docker compose --env-file deploy/.env -f deploy/compose.yaml \
  up -d --no-deps --force-recreate docguard
```

## 4. 持久化数据与 SQLite

宿主机持久化根目录：

```text
/opt/docguard/deploy/runtime
```

主要内容：

| 路径 | 作用 |
| --- | --- |
| `/opt/docguard/deploy/runtime/docguard.sqlite3` | 当前业务数据库 |
| `/opt/docguard/deploy/runtime/uploads/` | 上传的源文件 |
| `/opt/docguard/deploy/runtime/results/` | attempt 输入、证据、Agent findings 和最终报告 |
| `/opt/docguard/deploy/runtime/logs/docguard.log` | DocGuard 文件日志 |
| `/opt/docguard/deploy/runtime/backups/` | 手工创建的 SQLite 备份 |
| `/opt/docguard/deploy/runtime/dsh/` | 容器内 DSH 的 Home、profile、session、缓存和 Skill 链接 |

当前保留的数据库备份：

```text
/opt/docguard/deploy/runtime/backups/docguard-before-config-sync-20260910-074547.sqlite3
```

该备份创建于同步审核类型与 Agent 配置表之前。当前业务库已经包含两个审核 Agent 及其审核类型绑定。

查看 SQLite 配置记录时，应优先使用只读查询，不要直接复制本地整库覆盖服务器业务库。例如：

```bash
sqlite3 /opt/docguard/deploy/runtime/docguard.sqlite3 \
  'SELECT agent_id, version, enabled FROM agent_definitions;'

sqlite3 /opt/docguard/deploy/runtime/docguard.sqlite3 \
  'SELECT review_type_id, review_type_version, agent_definition_pk, position FROM review_type_agent_definitions ORDER BY position;'
```

## 5. OpenClaw

OpenClaw 的服务器专用状态目录：

```text
/opt/openclaw-docguard-state
```

重要路径：

| 路径 | 作用 |
| --- | --- |
| `/opt/openclaw-docguard-state/docguard-openclaw.json5` | Gateway 当前主配置 |
| `/opt/openclaw-docguard-state/audit-runtime.agents.json5` | 当前 Agent 配置 |
| `/opt/openclaw-docguard-state/agents/audit-runtime/` | 内容审核 Agent 状态 |
| `/opt/openclaw-docguard-state/agents/tech-audit-structure-reviewer/` | 结构审核 Agent 状态 |
| `/opt/openclaw-docguard-state/workspace-audit-runtime/` | 内容审核 workspace |
| `/opt/openclaw-docguard-state/workspace/tech-audit-structure-reviewer/` | 结构审核 workspace |
| `/opt/openclaw-docguard-state/sandboxes/` | session sandbox 的状态目录 |

两个 OpenClaw Agent：

- `audit-runtime` → `docx-tech-architecture-audit`
- `tech-audit-structure-reviewer` → `docx-tech-format-audit`

项目 Skill 源目录：

```text
/opt/docguard/doc-audit-integrate-skill
/opt/docguard/tect-doc-structure-audit-skill
```

更新或重新安装 OpenClaw Skill 后，需要重新规范化 sandbox 可读权限：

```bash
cd /opt/docguard
sudo bash deploy/fix-openclaw-skill-permissions.sh /opt/openclaw-docguard-state
```

更完整的 OpenClaw 部署说明见 `/opt/docguard/deploy/OPENCLAW_DEPLOYMENT.md`。

## 6. 容器内 DeepSeek Harness

DocGuard 使用镜像内安装的 `deepseek-harness-sdk` 和匹配的原生 `dsh` runtime。Python SDK 与 `dsh` 位于同一容器，通过 stdio 通信，不调用宿主机进程。

容器内重要路径：

| 路径 | 作用 |
| --- | --- |
| `/app/.venv/bin/dsh` | 容器内 DSH CLI/runtime |
| `/var/lib/docguard/dsh/settings.yaml` | 从仓库模板复制的实际 DSH 配置 |
| `/var/lib/docguard/dsh/profiles/` | DSH profile |
| `/var/lib/docguard/dsh/sessions/` | DSH session |
| `/var/lib/docguard/dsh/cache/` | UID 10001 可写的原生组件缓存 |
| `/var/lib/docguard/dsh/skills/` | 当前全局 Skill 链接 |

当前 provider 配置：

```yaml
llm-pi-ai:
  providers:
    minimax-cn:
      apiKeyEnv: MINIMAX_CN_API_KEY
agent-default-model:
  provider: minimax-cn
  model: MiniMax-M3
agent-presets:
  default: cordis
```

验证 DSH 版本、Skill 和 SDK stdio：

```bash
cd /opt/docguard

docker compose --env-file deploy/.env -f deploy/compose.yaml exec -T docguard \
  /bin/sh -lc '/app/.venv/bin/dsh --version && find -L "$DOCGUARD_DSH_HOME/skills" -name SKILL.md -print'

docker compose --env-file deploy/.env -f deploy/compose.yaml exec -T docguard \
  /app/.venv/bin/python - <<'PY'
import os
from deepseek_harness import DeepSeekHarness

with DeepSeekHarness(
    cwd="/var/lib/docguard",
    dsh_home=os.environ["DOCGUARD_DSH_HOME"],
    profile="sdk",
):
    print("DSH SDK stdio handshake: OK")
PY
```

### 当前 DSH 多 Agent 配置

2026-09-14 已将正式验证环境默认审核后端切换为 DSH：

- `technical-audit/content-reviewer` 默认解析到 `dsh/sdk`，使用
  `docx-tech-architecture-audit@1.0.0` SkillSet。
- `technical-audit/structure-reviewer` 默认解析到 `dsh/sdk`，使用
  `docx-tech-format-audit@1.0.0` SkillSet。
- 每个 `AgentRun` 使用独立 `agent-work/<dimension>` 作为 SDK `cwd`、独立
  session id 和独立 Skill 隔离 patch。
- 隔离 patch 设置 `includeDefaultRoots: false`，只暴露当前 Agent 的
  `customSkillDirs`；镜像中的版本化 SkillSet catalog 位于
  `/app/agent-skill-sets`。
- 数据库升级会把服务器早期使用的结构审核 Skill 标识
  `docx-tech-architecture-audit-structure-reviewer` 规范化为
  `docx-tech-format-audit`。

本次升级前已使用 SQLite backup API 创建备份：

```text
/opt/docguard/deploy/runtime/backups/docguard-before-dsh-20260914-144240.sqlite3
```

部署后已验证：

- DocGuard 和 OpenClaw 容器均为 healthy，`/healthz` 返回成功。
- Linux `deepseek-harness-sdk` 与 `deepseek-harness-runtime-bin` 均为
  `0.1.2rc1`。
- SDK 已完成 runtime `start`、stdio initialize 和 `close`，退出码为 0。
- 数据库中的内容审核与结构审核 Agent 均解析到 DSH 默认 route，两个
  SkillSet 的 `SKILL.md` 均可从镜像 catalog 读取。

仍待后续生产强化的边界包括全局并发限制、AgentRun 超时、错误分类、
`collecting` 截止时间，以及将每个 Agent 的输出写权限进一步收敛到私有目录。

### 宿主机早期验证环境

服务器还保留早期独立安装：

```text
/opt/deepseek-harness
```

该目录不被当前 Docker 化 DocGuard 使用，只用于独立 CLI/SDK 调试。不要把它与 `/opt/docguard/deploy/runtime/dsh` 混淆。确认不再需要后，可在单独维护操作中备份记录后删除；本次交接不执行删除。

## 7. 日志和排障命令

查看 DocGuard 容器日志：

```bash
cd /opt/docguard
docker compose --env-file deploy/.env -f deploy/compose.yaml logs -f --tail=200 docguard
```

查看 OpenClaw Gateway 日志：

```bash
cd /opt/docguard
docker compose --env-file deploy/.env -f deploy/compose.yaml logs -f --tail=200 openclaw
```

查看持久化应用日志：

```bash
tail -f /opt/docguard/deploy/runtime/logs/docguard.log
```

查看服务与健康状态：

```bash
cd /opt/docguard
docker compose --env-file deploy/.env -f deploy/compose.yaml ps
curl -fsS http://127.0.0.1:8000/healthz
```

查看最近审核工件：

```bash
find /opt/docguard/deploy/runtime/results -maxdepth 3 -type f \
  -printf '%T@ %p\n' | sort -nr | head -n 50
```

## 8. 安全与迁移注意事项

- 不要提交 `/opt/docguard/deploy/.env`。
- 不要提交或复制 DSH `.credentials.yaml`；当前部署使用环境变量注入凭据。
- 不要在文档、日志或 issue 中粘贴 API Key、Gateway Token 或 SSH 密码。
- 迁移前至少备份 `docguard.sqlite3`、`uploads/`、`results/`、OpenClaw 状态目录和必要的 `.env`；秘密文件应通过安全通道单独传输。
- SQLite 在线备份应使用 SQLite backup API 或停服后复制，不要在数据库写入期间直接复制主文件。
- OpenClaw sandbox 使用非 root UID/GID `10001:10001`；修改 Skill 后要再次检查目录可读权限。
- 当前服务只绑定 `127.0.0.1:8000`。若开放公网，应增加反向代理、TLS 和应用层鉴权。
