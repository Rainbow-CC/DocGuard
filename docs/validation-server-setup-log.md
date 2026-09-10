# 验证服务器初始化操作记录

本文记录 DocGuard 验证性部署服务器的人工初始化命令，方便复查和学习。文档不记录 SSH 密码、API Key 或其他密钥。

## 服务器信息

- 公网地址：`47.79.35.184`
- SSH 用户：`root`
- 操作系统：Ubuntu 24.04，x86_64
- 根分区：约 49 GB
- 内存：约 3.4 GiB
- 新增 Swap：2 GiB

## SSH 连接与基础检查

从本地终端连接：

```bash
ssh -o ConnectTimeout=10 -o ServerAliveInterval=5 root@47.79.35.184
```

登录后执行的只读检查：

```bash
whoami
hostname
uname -a
. /etc/os-release && printf '%s %s\n' "$NAME" "$VERSION_ID"
df -h /
free -h
command -v docker || true
command -v git || true
command -v python3 || true
```

检查结果：Git 和 Python 3 已安装；Docker 尚未安装或不在 `PATH` 中。

## 创建 Swap 前检查

```bash
swapon --show
free -h
df -h /
ls -l /swapfile 2>/dev/null || true
grep -nE '^[^#].*\bswap\b' /etc/fstab || true
```

执行时服务器没有已启用的 Swap，不存在 `/swapfile`，`/etc/fstab` 也没有有效 Swap 条目。

## 创建并永久启用 2 GiB Swap

以下命令由 `root` 用户执行：

```bash
set -e
fallocate -l 2G /swapfile
chmod 600 /swapfile
mkswap /swapfile
swapon /swapfile
cp -a /etc/fstab /etc/fstab.docguard-before-swap
printf '%s\n' '/swapfile none swap sw 0 0' >> /etc/fstab
```

各命令含义：

- `fallocate`：快速分配一个 2 GiB 文件。
- `chmod 600`：只允许 root 读写，防止其他用户读取可能被换出的内存内容。
- `mkswap`：把文件格式化为 Swap 区域。
- `swapon`：立即启用，无需重启。
- `cp -a`：修改前备份 `/etc/fstab`，保留原有属性。
- 最后一行：配置系统开机自动启用 `/swapfile`。

## 验证命令和结果

```bash
swapon --show
free -h
grep -nF '/swapfile none swap sw 0 0' /etc/fstab
ls -lh /swapfile /etc/fstab.docguard-before-swap
```

本次验证结果：

```text
NAME      TYPE SIZE USED PRIO
/swapfile file   2G   0B   -2

Swap:          2.0Gi          0B       2.0Gi

12:/swapfile none swap sw 0 0
-rw------- 1 root root 2.0G /swapfile
```

如需验证重启后的自动挂载，可在维护窗口重启服务器，然后再次执行：

```bash
swapon --show
free -h
```

## 回滚方法

仅在确定不再需要 Swap 时，以 `root` 用户执行：

```bash
swapoff /swapfile
cp -a /etc/fstab.docguard-before-swap /etc/fstab
rm /swapfile
```

`rm /swapfile` 会永久删除 Swap 文件，应当在 `swapoff` 成功且 `/etc/fstab` 已恢复后执行。

## 安装并配置 OpenClaw

服务器采用 OpenClaw 官方 Linux 安装脚本，跳过交互式 onboarding：

```bash
curl -fsSL https://openclaw.ai/install.sh | bash -s -- --no-onboard
openclaw --version
```

本次安装结果：

```text
Node.js v24.20.0
npm 11.19.0
OpenClaw 2026.9.3
```

迁移包在本地项目根目录创建，排除本机状态和缓存：

```bash
tar --exclude='.obsidian' --exclude='__pycache__' --exclude='*.pyc' \
  --exclude='.git' \
  -czf tmp/openclaw-migration-clean-20260909.tar.gz \
  deploy/openclaw-config \
  doc-audit-integrate-skill \
  tect-doc-structure-audit-skill

scp tmp/openclaw-migration-clean-20260909.tar.gz \
  root@47.79.35.184:/root/openclaw-migration-clean-20260909.tar.gz
```

服务器上创建独立的 DocGuard profile 和目录：

```bash
install -d -m 700 /root/docguard-openclaw-migration
tar -xzf /root/openclaw-migration-clean-20260909.tar.gz \
  -C /root/docguard-openclaw-migration

install -d -m 700 /root/.openclaw-docguard
install -m 600 \
  /root/docguard-openclaw-migration/deploy/openclaw-config/docguard-openclaw.json5 \
  /root/.openclaw-docguard/openclaw.json
install -m 600 \
  /root/docguard-openclaw-migration/deploy/openclaw-config/audit-runtime.agents.json5 \
  /root/.openclaw-docguard/audit-runtime.agents.json5

install -d -m 700 \
  /root/.openclaw-docguard/workspace-audit-runtime \
  /root/.openclaw-docguard/workspace/tech-audit-structure-reviewer \
  /root/.openclaw-docguard/agents/audit-runtime/agent \
  /root/.openclaw-docguard/agents/tech-audit-structure-reviewer/agent \
  /root/docguard-runtime
```

在 `/root/.openclaw-docguard/.env` 中配置以下变量，并将权限设为 `600`。真实
Token 和 API Key 不记录在本文：

```dotenv
OPENCLAW_GATEWAY_TOKEN=<随机生成的 Gateway Token>
MINIMAX_API_KEY=<MiniMax API Key>
OPENCLAW_HOST_HOME=/root/.openclaw-docguard
DOCGUARD_RUNTIME_HOST=/root/docguard-runtime
OPENCLAW_SANDBOX_MODE=off
OPENCLAW_EXEC_HOST=gateway
OPENCLAW_GATEWAY_BIND=loopback
```

验证配置并安装两个 Skill：

```bash
openclaw --profile docguard config validate
openclaw --profile docguard agents list

openclaw --profile docguard skills install \
  /root/docguard-openclaw-migration/doc-audit-integrate-skill \
  --agent audit-runtime \
  --as docx-tech-architecture-audit

openclaw --profile docguard skills install \
  /root/docguard-openclaw-migration/tect-doc-structure-audit-skill \
  --agent tech-audit-structure-reviewer \
  --as docx-tech-format-audit

openclaw --profile docguard skills check --agent audit-runtime
openclaw --profile docguard skills check --agent tech-audit-structure-reviewer
```

安装并启动 systemd 用户服务。启用 linger 后，即使 root 没有保持 SSH 登录，服务也会继续运行：

```bash
loginctl enable-linger root
openclaw --profile docguard gateway install
openclaw --profile docguard gateway start
openclaw --profile docguard gateway status
```

健康检查：

```bash
. /root/.openclaw-docguard/.env

curl --noproxy '*' http://127.0.0.1:18789/healthz
curl --noproxy '*' http://127.0.0.1:18789/readyz
curl --noproxy '*' http://127.0.0.1:18789/v1/models \
  -H "Authorization: Bearer $OPENCLAW_GATEWAY_TOKEN"
```

本次验证中，三个接口均返回 HTTP 200；随后通过 `POST /v1/responses` 调用
`openclaw/audit-runtime`，MiniMax M3 成功返回 `OK`。

## 2026-09-10：从 Git 克隆并执行 Docker 验证部署

服务器从 GitHub 的 `ui-optimization` 分支克隆到 `/opt/docguard`，检出的版本为
`836ae4910d9cd8fd4b311f5bba21e65ac3530417`：

```bash
git clone --branch ui-optimization --single-branch \
  https://github.com/Rainbow-CC/DocGuard.git /opt/docguard
cd /opt/docguard
git rev-parse HEAD
```

按 Docker 官方 Ubuntu APT 仓库安装 Engine 与 Compose plugin，安装后验证并启用服务：

```bash
apt-get update
apt-get install -y ca-certificates curl
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

创建仅保存在服务器上的环境文件。真实 Token 和 Key 不记录在本文：

```bash
cd /opt/docguard
cp deploy/.env.example deploy/.env
chmod 600 deploy/.env
sed -i 's#^OPENCLAW_HOST_HOME=.*#OPENCLAW_HOST_HOME=/opt/openclaw-docguard-state#' deploy/.env
sed -i 's#^DOCGUARD_REPO_HOST=.*#DOCGUARD_REPO_HOST=/opt/docguard#' deploy/.env
sed -i 's#^DOCGUARD_RUNTIME_HOST=.*#DOCGUARD_RUNTIME_HOST=/opt/docguard/deploy/runtime#' deploy/.env
sed -i "s#^DOCKER_GID=.*#DOCKER_GID=$(stat -c '%g' /var/run/docker.sock)#" deploy/.env

install -d -m 750 -o 10001 -g 10001 /opt/docguard/deploy/runtime
install -d -m 700 -o 1000 -g 1000 /opt/openclaw-docguard-state
install -m 600 -o 1000 -g 1000 \
  deploy/openclaw-config/docguard-openclaw.json5 \
  /opt/openclaw-docguard-state/docguard-openclaw.json5
install -m 600 -o 1000 -g 1000 \
  deploy/openclaw-config/audit-runtime.agents.json5 \
  /opt/openclaw-docguard-state/audit-runtime.agents.json5
```

`deploy/.env` 还需人工填写：

```dotenv
OPENCLAW_API_TOKEN=<随机生成的独立 Gateway Token>
MINIMAX_API_KEY=<MiniMax API Key>
DASHSCOPE_API_KEY=<启用视觉预处理/完整 DOCX 审核时必填>
DOCGUARD_DEFAULT_AGENT_BACKEND=openclaw
```

构建、初始化和启动命令：

```bash
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
```

首次启动发现镜像用户是 `node`（UID 1000）。因此 Compose 的 `HOME` 修正为
`/home/node`，持久化状态目录使用 `/opt/openclaw-docguard-state` 并归属
`1000:1000`。修正后安装两个 Skill：

```bash
docker compose --env-file deploy/.env -f deploy/compose.yaml exec -T openclaw \
  node openclaw.mjs skills install /opt/docguard/doc-audit-integrate-skill \
  --agent audit-runtime --as docx-tech-architecture-audit
docker compose --env-file deploy/.env -f deploy/compose.yaml exec -T openclaw \
  node openclaw.mjs skills install /opt/docguard/tect-doc-structure-audit-skill \
  --agent tech-audit-structure-reviewer --as docx-tech-format-audit
```

最终 `docker compose ps` 显示 `docguard` 与 `openclaw` 均为 `healthy`；
`curl http://127.0.0.1:8000/healthz` 返回 `{"status":"ok"}`。DocGuard 容器携带
内部 Bearer Token 访问 OpenClaw `/v1/models` 返回 HTTP 200，并通过
`openclaw/audit-runtime` 实际请求 MiniMax，得到 `200 completed OK`。

### 修复 sandbox 工件目录权限（2026-09-10）

实际审核发现 OpenClaw 创建 sandbox 时将镜像的 `USER docguard` 覆盖为
`1000:1000`，而 `deploy/runtime` 属于 `10001:10001` 且权限为 `750`，导致
Agent 无法读取任务输入或写入 `findings`。配置已为两个 Agent 显式增加
`docker.user: "10001:10001"`，并让只读 skill workspace 对 sandbox 可读。

服务器修复与验证命令：

```bash
cd /opt/docguard
git pull --ff-only

install -m 600 -o 1000 -g 1000 \
  deploy/openclaw-config/audit-runtime.agents.json5 \
  /opt/openclaw-docguard-state/audit-runtime.agents.json5
chmod 755 \
  /opt/openclaw-docguard-state/workspace-audit-runtime \
  /opt/openclaw-docguard-state/workspace/tech-audit-structure-reviewer

docker compose --env-file deploy/.env -f deploy/compose.yaml exec -T openclaw \
  openclaw sandbox recreate --agent audit-runtime
docker compose --env-file deploy/.env -f deploy/compose.yaml exec -T openclaw \
  openclaw sandbox recreate --agent tech-audit-structure-reviewer

docker ps -aq --filter ancestor=openclaw-docguard-sandbox:2026.9.3 | \
while read -r container_id; do
  docker inspect "$container_id" \
    --format 'name={{.Name}} user={{.Config.User}}'
done
```

预期新 sandbox 显示 `user=10001:10001`。

曾尝试以下配置校验命令，但 OpenClaw 2026.9.3 不提供 `config check`，返回
`OpenClaw config has no command "check"`，因此改用 `sandbox explain` 和实际
Agent 工具调用验证：

```bash
docker compose --env-file deploy/.env -f deploy/compose.yaml exec -T openclaw \
  openclaw config check
docker compose --env-file deploy/.env -f deploy/compose.yaml exec -T openclaw \
  openclaw sandbox explain --agent audit-runtime
```

通过两个 `/v1/responses` 最小请求，分别让 `audit-runtime` 和
`tech-audit-structure-reviewer` 在 sandbox 中运行 `id`、读取已有任务的
`input-manifest.json`，并在 `findings` 中创建后删除权限测试文件。两个请求均
返回 `completed` 和 `PERMISSION_OK`。随后检查实际容器：

```bash
for container_id in $(docker ps -q \
  --filter ancestor=openclaw-docguard-sandbox:2026.9.3); do
  docker inspect "$container_id" \
    --format 'name={{.Name}} user={{.Config.User}} groups={{json .HostConfig.GroupAdd}} status={{.State.Status}}'
done
```

两个运行中的 sandbox 均显示 `user=10001:10001`；测试文件确认已删除，
`docguard` 与 `openclaw` 服务仍为 `healthy`。
