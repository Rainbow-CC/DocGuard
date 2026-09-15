# DocGuard 服务器 Docker 部署手册

本文适用于 Ubuntu 24.04 x86_64 单机部署 DocGuard 与 Docker 管理的 OpenClaw Gateway。服务默认仅监听服务器本机 `127.0.0.1:8000`；如需公网访问，应在 Nginx/Caddy 等反向代理后提供 TLS 和访问控制，而非直接暴露 DocGuard 端口。

> 本文中的示例以 `/opt/docguard` 为项目目录、`root` 为部署用户。不要将 API Key、Gateway Token 或 `deploy/.env` 提交到 Git。

## 1. 前置条件

- Ubuntu 24.04，建议至少 4 GiB 内存；小内存服务器建议配置 2 GiB Swap。
- 可访问 GitHub、Docker 官方 APT 仓库及所使用的模型服务。
- 已准备 MiniMax API Key；启用视觉预处理或完整 DOCX 审核时还需要 DashScope API Key。
- 使用 HTTPS 方式克隆公开仓库；私有仓库请改用已配置的 SSH 地址或访问令牌方式。

### 可选：配置 2 GiB Swap

仅在服务器尚未启用 Swap 且磁盘空间充足时执行。先检查：

```bash
swapon --show
free -h
df -h /
ls -l /swapfile 2>/dev/null || true
grep -nE '^[^#].*\bswap\b' /etc/fstab || true
```

确认无现有 `/swapfile` 后创建并永久启用：

```bash
set -euo pipefail
fallocate -l 2G /swapfile
chmod 600 /swapfile
mkswap /swapfile
swapon /swapfile
cp -a /etc/fstab /etc/fstab.docguard-before-swap
printf '%s\n' '/swapfile none swap sw 0 0' >> /etc/fstab

swapon --show
free -h
```

## 2. 安装 Docker Engine 与 Compose

按 Docker 官方 Ubuntu APT 仓库安装：

```bash
apt-get update
apt-get install -y ca-certificates curl git
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo \"${UBUNTU_CODENAME:-$VERSION_CODENAME}\") stable" \
  > /etc/apt/sources.list.d/docker.list
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io \
  docker-buildx-plugin docker-compose-plugin
systemctl enable --now docker
docker --version
docker compose version
```

## 3. 获取源码

以下以部署目标分支 `ui-optimization` 为例；请按实际发布分支或 tag 替换。目标目录必须不存在，避免混入旧文件：

```bash
git clone --branch ui-optimization --single-branch \
  https://github.com/Rainbow-CC/DocGuard.git /opt/docguard
cd /opt/docguard
git rev-parse HEAD
```

记录输出的提交 SHA，作为此次部署的版本依据。

## 4. 配置服务器专用状态与环境变量

复制环境模板，限制密钥文件权限，并写入服务器绝对路径与 Docker socket 的 GID：

```bash
cd /opt/docguard
cp deploy/.env.example deploy/.env
chmod 600 deploy/.env

sed -i 's#^OPENCLAW_HOST_HOME=.*#OPENCLAW_HOST_HOME=/opt/openclaw-docguard-state#' deploy/.env
sed -i 's#^DOCGUARD_REPO_HOST=.*#DOCGUARD_REPO_HOST=/opt/docguard#' deploy/.env
sed -i 's#^DOCGUARD_RUNTIME_HOST=.*#DOCGUARD_RUNTIME_HOST=/opt/docguard/deploy/runtime#' deploy/.env
sed -i "s#^DOCKER_GID=.*#DOCKER_GID=$(stat -c '%g' /var/run/docker.sock)#" deploy/.env
```

编辑 `deploy/.env`，至少填写以下值：

```dotenv
OPENCLAW_API_TOKEN=<使用 openssl rand -hex 32 生成的独立随机 Token>
MINIMAX_API_KEY=<MiniMax API Key>
DASHSCOPE_API_KEY=<启用视觉预处理/完整 DOCX 审核时必填>
DOCGUARD_DEFAULT_AGENT_BACKEND=openclaw
```

建议用以下命令生成 Token，再粘贴到 `.env`：

```bash
openssl rand -hex 32
```

创建持久化目录和初始 OpenClaw 配置。UID/GID 不可互换：OpenClaw Gateway 镜像中的 `node` 用户是 `1000:1000`，而 DocGuard 审核 sandbox 以 `10001:10001` 运行。

```bash
install -d -m 750 -o 10001 -g 10001 /opt/docguard/deploy/runtime
install -d -m 700 -o 1000 -g 1000 /opt/openclaw-docguard-state
install -m 600 -o 1000 -g 1000 \
  deploy/openclaw-config/docguard-openclaw.json5 \
  /opt/openclaw-docguard-state/docguard-openclaw.json5
install -m 600 -o 1000 -g 1000 \
  deploy/openclaw-config/audit-runtime.agents.json5 \
  /opt/openclaw-docguard-state/audit-runtime.agents.json5
```

## 5. 构建、初始化数据库并启动

先渲染 Compose 配置；该步骤会尽早发现缺失变量或 YAML 错误。随后构建 OpenClaw、sandbox 和应用镜像，初始化 SQLite，最后后台启动服务：

```bash
cd /opt/docguard
docker compose --env-file deploy/.env -f deploy/compose.yaml config --quiet

docker build -t openclaw-docguard-sandbox:2026.9.3 \
  -f deploy/Dockerfile.openclaw-sandbox deploy
docker build -t docguard-openclaw:2026.9.3 \
  -f deploy/Dockerfile.openclaw deploy
docker compose --env-file deploy/.env -f deploy/compose.yaml build docguard

docker compose --env-file deploy/.env -f deploy/compose.yaml run --rm --no-deps \
  docguard /app/.venv/bin/python /app/init/apply_sql.py \
  --database-path /var/lib/docguard/docguard.sqlite3

docker compose --env-file deploy/.env -f deploy/compose.yaml up -d
docker compose --env-file deploy/.env -f deploy/compose.yaml ps
```

首次启动时，等待 `docguard` 与 `openclaw` 都显示为 `healthy` 后再继续。

## 6. 安装审核 Skill 并修复 sandbox 读取权限

在 Gateway 容器内安装两个随仓库提供的 Skill：

```bash
cd /opt/docguard
docker compose --env-file deploy/.env -f deploy/compose.yaml exec -T openclaw \
  node openclaw.mjs skills install /opt/docguard/doc-audit-integrate-skill \
  --agent audit-runtime --as docx-tech-architecture-audit

docker compose --env-file deploy/.env -f deploy/compose.yaml exec -T openclaw \
  node openclaw.mjs skills install /opt/docguard/tect-doc-structure-audit-skill \
  --agent tech-audit-structure-reviewer --as docx-tech-format-audit
```

已安装 Skill 默认仅 Gateway 用户可进入；必须运行仓库脚本，使 sandbox 可以读取 Skill 契约、参考文件和校验脚本。脚本只增加读/执行权限，不授予写权限：

```bash
bash deploy/fix-openclaw-skill-permissions.sh /opt/openclaw-docguard-state

docker compose --env-file deploy/.env -f deploy/compose.yaml exec -T openclaw \
  openclaw sandbox recreate --agent audit-runtime
docker compose --env-file deploy/.env -f deploy/compose.yaml exec -T openclaw \
  openclaw sandbox recreate --agent tech-audit-structure-reviewer
```

如果遗漏此步骤，模型请求可能仍返回 HTTP 200，但 Agent 会因无法读取 `SKILL.md` 生成不符合当前契约的结果，进而被 DocGuard 拒绝。

## 7. 部署验证

### 服务健康与 Gateway 鉴权

```bash
cd /opt/docguard
docker compose --env-file deploy/.env -f deploy/compose.yaml ps
curl -fsS http://127.0.0.1:8000/healthz

docker compose --env-file deploy/.env -f deploy/compose.yaml exec -T openclaw \
  node -e "fetch('http://127.0.0.1:18789/healthz').then(async r => { console.log(r.status, await r.text()); process.exit(r.ok ? 0 : 1) })"
docker compose --env-file deploy/.env -f deploy/compose.yaml exec -T openclaw \
  node -e "fetch('http://127.0.0.1:18789/v1/models', { headers: { Authorization: 'Bearer ' + process.env.OPENCLAW_GATEWAY_TOKEN } }).then(async r => { console.log(r.status, await r.text()); process.exit(r.ok ? 0 : 1) })"
```

DocGuard 健康接口应返回：

```json
{"status":"ok"}
```

`/v1/models` 应返回 HTTP 200。注意：Compose 将 `.env` 中的 `OPENCLAW_API_TOKEN` 映射为 Gateway 容器内的 `OPENCLAW_GATEWAY_TOKEN`。

### 检查 sandbox 用户

验证 sandbox 是否使用预期的 `10001:10001` 用户：

```bash
docker ps -aq --filter ancestor=openclaw-docguard-sandbox:2026.9.3 | \
while read -r container_id; do
  docker inspect "$container_id" \
    --format 'name={{.Name}} user={{.Config.User}} status={{.State.Status}}'
done
```

新建或运行中的审核 sandbox 应显示 `user=10001:10001`。也可使用以下命令检查某 Agent 的 sandbox 配置：

```bash
docker compose --env-file deploy/.env -f deploy/compose.yaml exec -T openclaw \
  openclaw sandbox explain --agent audit-runtime
```

当前 OpenClaw 版本不提供 `openclaw config check`；不要将该命令作为验证步骤，应以 `sandbox explain` 和实际审核任务为准。

## 8. 升级与日常运维

所有更新均从以下命令开始。`git pull --ff-only` 不会自动合并服务器上的本地修改；若它失败，先检查 `git status`，不要用 `reset --hard` 覆盖运行中的文件。

```bash
cd /opt/docguard
git status
git pull --ff-only
git rev-parse HEAD
```

根据实际变更，执行下面对应的一类或多类操作。

### 8.1 更新 Python 应用代码

适用于 `src/`、`pyproject.toml`、`uv.lock`、`deploy/Dockerfile`、数据库初始化脚本或其他由 `docguard` 镜像使用的应用代码变更。重新构建并替换 `docguard` 服务即可；SQLite、上传文件和审核工件保留在运行目录中。

```bash
cd /opt/docguard
docker compose --env-file deploy/.env -f deploy/compose.yaml build docguard
docker compose --env-file deploy/.env -f deploy/compose.yaml up -d docguard
docker compose --env-file deploy/.env -f deploy/compose.yaml ps docguard
curl -fsS http://127.0.0.1:8000/healthz
```

若变更包含新的数据库迁移或初始化 SQL，在启动前额外执行：

```bash
docker compose --env-file deploy/.env -f deploy/compose.yaml run --rm --no-deps \
  docguard /app/.venv/bin/python /app/init/apply_sql.py \
  --database-path /var/lib/docguard/docguard.sqlite3
```

### 8.2 更新审核 Skill

适用于 `doc-audit-integrate-skill/` 或 `tect-doc-structure-audit-skill/` 内容变更。Skill 是安装到 OpenClaw 持久化状态目录的副本，仅 `git pull` 不会让运行中的 sandbox 获得更新。

重新安装受影响的 Skill；为避免漏项，以下命令同时更新两个 Skill：

```bash
cd /opt/docguard
docker compose --env-file deploy/.env -f deploy/compose.yaml exec -T openclaw \
  node openclaw.mjs skills install /opt/docguard/doc-audit-integrate-skill \
  --agent audit-runtime --as docx-tech-architecture-audit

docker compose --env-file deploy/.env -f deploy/compose.yaml exec -T openclaw \
  node openclaw.mjs skills install /opt/docguard/tect-doc-structure-audit-skill \
  --agent tech-audit-structure-reviewer --as docx-tech-format-audit

# 新安装/更新的 Skill 必须对 UID 10001 的 sandbox 可读。
bash deploy/fix-openclaw-skill-permissions.sh /opt/openclaw-docguard-state

# sandbox 的 /workspace 是快照，必须重建后才会使用新版 Skill。
docker compose --env-file deploy/.env -f deploy/compose.yaml exec -T openclaw \
  openclaw sandbox recreate --agent audit-runtime
docker compose --env-file deploy/.env -f deploy/compose.yaml exec -T openclaw \
  openclaw sandbox recreate --agent tech-audit-structure-reviewer
```

完成后用实际审核任务验证；至少可先检查 sandbox 配置：

```bash
docker compose --env-file deploy/.env -f deploy/compose.yaml exec -T openclaw \
  openclaw sandbox explain --agent audit-runtime
```

### 8.3 更新 OpenClaw 或 DeepSeek Harness 配置

配置文件分为两类，更新后都要重新创建受影响的容器，不能只执行 `restart`。

**OpenClaw Gateway 与 Agent 配置**：适用于 `deploy/openclaw-config/*.json5`、`deploy/compose.yaml` 中 OpenClaw 配置、`OPENCLAW_*`、`MINIMAX_API_KEY` 或 Docker socket GID 的变更。先同步持久化配置，再强制重建 Gateway；如果 Agent 配置或 sandbox 相关项有变更，同时重建两个 sandbox。

```bash
cd /opt/docguard
install -m 600 -o 1000 -g 1000 \
  deploy/openclaw-config/docguard-openclaw.json5 \
  /opt/openclaw-docguard-state/docguard-openclaw.json5
install -m 600 -o 1000 -g 1000 \
  deploy/openclaw-config/audit-runtime.agents.json5 \
  /opt/openclaw-docguard-state/audit-runtime.agents.json5

docker compose --env-file deploy/.env -f deploy/compose.yaml up -d --force-recreate openclaw
docker compose --env-file deploy/.env -f deploy/compose.yaml ps openclaw

docker compose --env-file deploy/.env -f deploy/compose.yaml exec -T openclaw \
  openclaw sandbox recreate --agent audit-runtime
docker compose --env-file deploy/.env -f deploy/compose.yaml exec -T openclaw \
  openclaw sandbox recreate --agent tech-audit-structure-reviewer
```

如修改的是 `deploy/Dockerfile.openclaw` 或 `deploy/Dockerfile.openclaw-sandbox`，还需先构建指定镜像，再执行上述 `up -d --force-recreate openclaw`：

```bash
docker build -t openclaw-docguard-sandbox:2026.9.3 \
  -f deploy/Dockerfile.openclaw-sandbox deploy
docker build -t docguard-openclaw:2026.9.3 \
  -f deploy/Dockerfile.openclaw deploy
```

**DeepSeek Harness 配置**：适用于 `.env` 中的 `DOCGUARD_DSH_*`、`MINIMAX_CN_API_KEY`、`DOCGUARD_RUNTIME_ROUTES_PATH` 或 DeepSeek Harness 使用的模型/Provider 配置。编辑服务器上的 `deploy/.env` 后，先检查 Compose 渲染结果，再强制重建 `docguard`。这不会影响 OpenClaw 或已存在的 sandbox。

```bash
cd /opt/docguard
nano deploy/.env
chmod 600 deploy/.env
docker compose --env-file deploy/.env -f deploy/compose.yaml config --quiet
docker compose --env-file deploy/.env -f deploy/compose.yaml up -d --force-recreate docguard
docker compose --env-file deploy/.env -f deploy/compose.yaml ps docguard
curl -fsS http://127.0.0.1:8000/healthz
```

`MINIMAX_API_KEY` 属于 OpenClaw 审核 Agent；`MINIMAX_CN_API_KEY` 和 `DOCGUARD_DSH_*` 属于 DeepSeek Harness。修改其中任一类时，只重建其所属服务即可。

### 常用命令

```bash
cd /opt/docguard

docker compose --env-file deploy/.env -f deploy/compose.yaml logs -f --tail=200 docguard
docker compose --env-file deploy/.env -f deploy/compose.yaml logs -f --tail=200 openclaw
docker compose --env-file deploy/.env -f deploy/compose.yaml restart

# 停止服务，但保留 SQLite、上传文件和审核工件。
docker compose --env-file deploy/.env -f deploy/compose.yaml down
```

不要执行 `docker compose down -v`，也不要删除 `/opt/docguard/deploy/runtime` 或 `/opt/openclaw-docguard-state`；这些目录分别保存 DocGuard 持久化数据和 OpenClaw 状态。
