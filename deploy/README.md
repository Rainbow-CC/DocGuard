# 单机 Linux 演示部署

此目录通过 Docker Compose 启动一个 DocGuard 容器。它保留当前演示技术栈：FastAPI、`BackgroundTasks`、SQLite 和本地文件工件，不会引入 Redis、PostgreSQL 或独立 Worker。

## 前置条件

- Linux 主机已安装 Docker Engine 和 Docker Compose v2；确认命令 `docker compose version` 可运行。
- 服务器有足够磁盘空间保存上传文件和提取出的图片。`runtime/` 是全部持久化状态，不要随意删除。
- 若需要执行真实 OpenClaw 审核，容器必须能够访问 `OPENCLAW_GATEWAY_URL`，并且该 Gateway/Agent 必须能访问同一个结果目录或采用已有的工件交付方式。

## 部署步骤

1. 将整个项目（包含 `doc-audit-integrate-skill/`）复制或拉取到 Linux 服务器：

   ```bash
   git clone <repository-url> DocGuard
   cd DocGuard
   ```
  
2. 创建部署配置和持久化目录，并使容器内的非 root 用户可写：

   ```bash
   cp deploy/.env.example deploy/.env
   mkdir -p deploy/runtime
   sudo chown -R 10001:10001 deploy/runtime
   chmod 750 deploy/runtime
   ```

3. 编辑 `deploy/.env`，至少按实际场景设置：

   - 仅演示页面/API：创建任务时在请求中指定 `agent_backend: "stub"`。
   - 调用视觉审核：填写 `DASHSCOPE_API_KEY`。
   - 调用 OpenClaw：填写 `OPENCLAW_GATEWAY_URL` 与 `OPENCLAW_API_TOKEN`。

4. 构建镜像并执行一次数据库初始化：

   ```bash
   docker compose -f deploy/compose.yaml build docguard
   docker compose -f deploy/compose.yaml run --rm --no-deps docguard \
     /app/.venv/bin/python /app/init/apply_sql.py \
     --database-path /var/lib/docguard/docguard.sqlite3
   ```

   这是运维步骤：它创建 SQLite 文件、表、索引、缓存表和初始审核类型。DocGuard 应用不会在启动时检测、建库或补写初始化数据。

5. 在后台启动服务：

   ```bash
   docker compose -f deploy/compose.yaml up -d
   ```

6. 检查运行状态和健康检查：

   ```bash
   docker compose -f deploy/compose.yaml ps
   curl http://127.0.0.1:8000/healthz
   docker compose -f deploy/compose.yaml logs -f docguard
   ```

默认仅监听服务器本机的 `127.0.0.1:8000`。若要从外部访问，应优先用 Nginx 或 Caddy 反代并配置 TLS；本项目当前没有应用层登录鉴权。只有在受信任内网中才可将 `DOCGUARD_BIND_ADDRESS` 改为 `0.0.0.0`。

## 日常操作

```bash
# 停止（不会删除 deploy/runtime 中的数据）
docker compose -f deploy/compose.yaml down

# 更新代码后重新构建启动
docker compose -f deploy/compose.yaml up -d --build

# 查看日志
docker compose -f deploy/compose.yaml logs -f --tail=200 docguard
```

如果本次发布变更了 `init/sql/`，应在启动新版本前按“部署步骤”第 4 步重新执行初始化脚本；应用本身不会执行这些 SQL。

不要使用 `docker compose down -v`，也不要删除 `deploy/runtime/`；其中包含 SQLite 数据库、上传文件、审核工件和应用日志。
