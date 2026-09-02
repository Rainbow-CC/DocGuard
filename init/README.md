# DocGuard 运维初始化

本目录是部署交付物，不被 `src/docguard` 中的应用代码导入或执行。它负责创建 SQLite 数据库文件、表、索引、WAL 设置和初始审核类型数据；应用只连接已经准备好的数据库。

首次部署或受控升级时，由运维人员执行：

```bash
python init/apply_sql.py --database-path /var/lib/docguard/docguard.sqlite3
```

Windows PowerShell 示例：

```powershell
uv run python init/apply_sql.py --database-path .\data\docguard.sqlite3
```

脚本按文件名顺序执行 `init/sql/*.sql`。当前 SQL 使用 `CREATE ... IF NOT EXISTS` 和 `INSERT OR IGNORE`，可安全重跑：不会覆盖已有审核类型或 Agent 定义。它还会把旧版 JSON 内嵌 Agent 定义一次性转为关联表数据，并为历史审核任务补充 `default` 项目关联；这些升级均不在应用启动时执行。

变更数据库结构或初始配置时，新增有序 SQL 文件，并将其纳入部署变更流程。执行前应按正常运维要求备份持久化 SQLite 文件。
