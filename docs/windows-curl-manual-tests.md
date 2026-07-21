# DocGuard Windows curl 手工测试用例

本文覆盖 `src/docguard/api/app.py` 当前暴露的全部 HTTP endpoint。示例使用 Windows 自带的 `curl.exe`；在 PowerShell 中不要省略 `.exe`，否则 `curl` 可能会被解析为 `Invoke-WebRequest` 别名。

## 1. 准备

安装依赖并启动服务：

```powershell
uv sync --group dev
uv run fastapi dev src/docguard/api/app.py
```

另开一个 PowerShell 窗口，设置服务地址和待上传文件。路径请替换为实际存在的 DOCX 文件。

```powershell
$baseUrl = "http://127.0.0.1:8000"
$docxPath = "C:\Code\信托公司测试\TC-COMB-SJ\TC-COMB-SJ\TC-COMB-SJ-003.docx"
```

默认上传目录是 `\\wsl.localhost\Ubuntu\home\ubuntu\docguard-inbox`，并返回供 Agent 使用的 Linux 路径。如本机未安装或未运行 Ubuntu WSL，请先设置一个可写目录后再启动服务：

```powershell
$env:DOCGUARD_UPLOAD_WRITE_ROOT = "C:\\DocGuardUploads"
$env:DOCGUARD_UPLOAD_AGENT_ROOT = "/docguard-inbox"
uv run fastapi dev src/docguard/api/app.py
```

## 2. OpenClaw 完整操作流程：上传、创建、状态与结果查询

本节可独立执行，适用于真实 OpenClaw 审核。DocGuard 将上传的 DOCX 与每次任务的结果目录映射给 OpenClaw Agent；Agent 运行 `docx-tech-architecture-audit` skill 后，必须向指定位置原子写入 `findings.json`。DocGuard 只接受该工件，不会从 Agent 的聊天答复中解析审核结果。

### 2.1 配置共享目录和 Gateway

在**启动 DocGuard 服务的 PowerShell 窗口**设置以下变量，再启动服务。以下路径对应默认 Ubuntu WSL 发行版；请按实际发行版、Gateway 地址和 Token 替换。`OPENCLAW_GATEWAY_URL` 必须以 `/v1` 结尾，应用会追加 `/responses`。

```powershell
$env:DOCGUARD_UPLOAD_WRITE_ROOT = "\\wsl.localhost\Ubuntu\home\ubuntu\docguard-inbox"
$env:DOCGUARD_UPLOAD_AGENT_ROOT = "/home/ubuntu/docguard-inbox"
$env:DOCGUARD_RESULT_WRITE_ROOT = "\\wsl.localhost\Ubuntu\home\ubuntu\docguard-results"
$env:DOCGUARD_RESULT_AGENT_ROOT = "/home/ubuntu/docguard-results"
$env:OPENCLAW_GATEWAY_URL = "http://127.0.0.1:18789/v1"
$env:OPENCLAW_API_TOKEN = "<OpenClaw Gateway Token>"
uv run fastapi dev src/docguard/api/app.py
```

开始前确认 `openclaw/audit-runtime` Agent 已安装 `docx-tech-architecture-audit` skill，并拥有：上传目录和 `input-manifest.json` 的只读权限、结果目录的写入权限。不要向 Agent 授予 shell、任意文件写入或外网写入权限。

在另一个 PowerShell 窗口设置调用变量：

```powershell
$baseUrl = "http://127.0.0.1:8000"
$docxPath = "C:\Code\信托公司测试\TC-COMB-SJ\TC-COMB-SJ\TC-COMB-SJ-001.docx"
$agentId = "reviewer"
```

### 2.2 上传 DOCX

```powershell
$upload = curl.exe -sS "$baseUrl/api/v1/agents/$agentId/uploads" `
  -F "file=@$docxPath;type=application/vnd.openxmlformats-officedocument.wordprocessingml.document" |
  ConvertFrom-Json

$upload
```

确认返回值中的 `content_sha256`、`source_uri` 和 `agent_path` 均有值。后两者应分别指向 Agent 可见的 Linux 路径与 `file://` URI；创建任务时须原样使用这份 `$upload` 响应。

### 2.3 创建 OpenClaw 审核任务

```powershell
$openClawTaskBody = @{
  document = @{
    filename = $upload.filename
    content_sha256 = $upload.content_sha256
    source_uri = $upload.source_uri
  }
  profile_id = "technical-audit"
  agent_backend = "openclaw"
} | ConvertTo-Json -Depth 3 -Compress

$openClawTask = curl.exe -sS "$baseUrl/api/v1/tasks" `
  -X POST `
  -H "Content-Type: application/json" `
  --data-raw $openClawTaskBody |
  ConvertFrom-Json

$openClawTask
```

预期为 `202 Accepted`，其中 `status` 是 `queued`，并返回 `task_id` 和 `status_url`。保存 `$openClawTask`，后续所有查询均使用它。

### 2.4 查询状态和执行信息

一次性查询：

```powershell
$currentTask = curl.exe -sS "$baseUrl$($openClawTask.status_url)" | ConvertFrom-Json
$currentTask
```

持续轮询直至终态：

```powershell
do {
  $currentTask = curl.exe -sS "$baseUrl$($openClawTask.status_url)" | ConvertFrom-Json
  "{0:u}  {1}" -f (Get-Date), $currentTask.status
  if ($currentTask.status -in @("completed", "failed", "cancelled")) { break }
  Start-Sleep -Seconds 3
} while ($true)

$currentTask.attempts
```

状态会依次经过 `queued`、`running` 与 `collecting`，成功后成为 `completed`。`attempts` 中可查看 `gateway_response_id`、`input_manifest_uri`、`result_uri` 及 `error`；任务失败时也应同时检查 `$currentTask.error`。

### 2.5 获取审核结果，或重新采集结果工件

当状态为 `completed` 时，任务详情中的 `findings` 是结构化审核发现，`report_markdown` 是 DocGuard 根据发现生成的报告：

```powershell
$currentTask.findings | ConvertTo-Json -Depth 10
$currentTask.report_markdown
```

若状态停留在 `collecting`（例如 Gateway SSE 已中断，但 Agent 仍可能已写入工件），请求重新采集：

```powershell
$currentTask = curl.exe -sS "$baseUrl/api/v1/tasks/$($openClawTask.task_id)/collect" `
  -X POST |
  ConvertFrom-Json

$currentTask.status
$currentTask.findings | ConvertTo-Json -Depth 10
$currentTask.report_markdown
```

`/collect` 返回 `409 Conflict` 表示任务当前不可采集；请等待其进入 `collecting`，并检查 Agent 是否已将结果按“同目录临时文件 + 原子重命名”的方式交付到 `attempts[0].result_uri` 指向的 `findings.json`。结果工件必须匹配 `docguard-agent-result-v1` 契约、当前任务和 attempt 的元数据，且每个 finding 的 `agent_backend` 必须为 `openclaw`。

## 3. 健康检查：`GET /healthz`

```powershell
curl.exe -i "$baseUrl/healthz"
```

预期为 `HTTP/1.1 200 OK`，响应体：

```json
{"status":"ok"}
```

## 4. 上传 DOCX：`POST /api/v1/agents/{agent_id}/uploads`

`agent_id` 仅支持字母、数字、下划线和连字符，长度为 1–64；上传内容必须是 DOCX ZIP 容器。此请求返回的 `source_uri` 与 `content_sha256` 可直接用于创建任务。

```powershell
$upload = curl.exe -sS "$baseUrl/api/v1/agents/reviewer/uploads" `
  -F "file=@$docxPath;type=application/vnd.openxmlformats-officedocument.wordprocessingml.document" |
  ConvertFrom-Json
$upload
```

预期为 `201 Created`，并包含 `upload_id`、`content_sha256`、`agent_path` 与 `source_uri`。确认变量可用：

```powershell
$upload.content_sha256
$upload.source_uri
```

### 上传失败用例

非 DOCX 文件应返回 `422 Unprocessable Content`：

```powershell
curl.exe -i "$baseUrl/api/v1/agents/reviewer/uploads" `
  -F "file=@C:\Code\信托公司测试\数据中台审核报告-1.pdf;type=text/plain"
```

非法 `agent_id` 应返回 `422 Unprocessable Content`：

```powershell
curl.exe -i "$baseUrl/api/v1/agents/reviewer%21/uploads" `
  -F "file=@$docxPath;type=application/vnd.openxmlformats-officedocument.wordprocessingml.document"
```

文件超过 `DOCGUARD_UPLOAD_MAX_BYTES`（默认 100 MiB）时应返回 `413 Payload Too Large`。

## 5. 创建审核任务：`POST /api/v1/tasks`

以下用例使用 `stub` 后端，无需 OpenClaw。后台任务很快完成，随后可按下一节查询状态。

```powershell
$taskBody = @{
  document = @{
    filename = $upload.filename
    content_sha256 = $upload.content_sha256
    source_uri = $upload.source_uri
  }
  agent_backend = "stub"
} | ConvertTo-Json -Depth 3 -Compress

$task = curl.exe -sS "$baseUrl/api/v1/tasks" `
  -X POST `
  -H "Content-Type: application/json" `
  --data-raw $taskBody |
  ConvertFrom-Json
$task
```

预期为 `202 Accepted`，响应中 `status` 为 `queued`，并包含 `task_id` 和 `status_url`。

### 创建 OpenClaw 审核任务

先在**启动 DocGuard 服务的 PowerShell 窗口**配置 Gateway。`OPENCLAW_GATEWAY_URL` 必须包含 `/v1`，因为应用会在其后追加 `/responses`。同时，`openclaw/audit-runtime` Agent 必须已安装 `docx-tech-architecture-audit` skill，并且可只读访问上传目录和结果目录。

```powershell
$env:OPENCLAW_GATEWAY_URL = "http://127.0.0.1:18789/v1"
$env:OPENCLAW_API_TOKEN = "<OpenClaw Gateway Token>"
uv run fastapi dev src/docguard/api/app.py
```

上传 DOCX 后，将同一份 `$upload` 响应用于以下请求：

```powershell
$openClawTaskBody = @{
  document = @{
    filename = $upload.filename
    content_sha256 = $upload.content_sha256
    source_uri = $upload.source_uri
  }
  profile_id = "technical-audit"
  agent_backend = "openclaw"
} | ConvertTo-Json -Depth 3 -Compress

$openClawTask = curl.exe -sS "$baseUrl/api/v1/tasks" `
  -X POST `
  -H "Content-Type: application/json" `
  --data-raw $openClawTaskBody |
  ConvertFrom-Json
$openClawTask
```

预期为 `202 Accepted`。随后使用下列请求查看状态：

```powershell
curl.exe -i "$baseUrl/api/v1/tasks/$($openClawTask.task_id)"
```

任务会依次进入 `running` 和 `collecting`；当 Agent 将已校验的 `findings.json` 原子写入结果目录后，状态变为 `completed`。若 SSE 连接中断但 Agent 仍可能完成写入，可调用第 7 节的 `/collect` 接口重新采集。

### 创建失败用例

缺少必填 `document` 字段应返回 `422 Unprocessable Content`：

```powershell
curl.exe -i "$baseUrl/api/v1/tasks" `
  -X POST -H "Content-Type: application/json" --data-raw '{}'
```

未知 `profile_id` 应返回 `422 Unprocessable Content`：

```powershell
curl.exe -i "$baseUrl/api/v1/tasks" `
  -X POST -H "Content-Type: application/json" `
  --data-raw '{"document":{"filename":"方案.docx","content_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","source_uri":"file:///docguard-inbox/reviewer/example/source.docx"},"profile_id":"unknown"}'
```

## 6. 查询任务：`GET /api/v1/tasks/{task_id}`

当前项目未提供任务列表或 Web 管理界面。创建任务响应中的 `status_url`（或 `task_id`）是查看该任务状态的入口；请在创建响应中保存它。

```powershell
curl.exe -i "$baseUrl/api/v1/tasks/$($task.task_id)"
```

预期为 `200 OK`。`stub` 任务最终应为 `completed`，并包含 `findings` 和 `report_markdown`。若刚创建时仍在运行，可稍后重试。

创建 OpenClaw 任务时，将上述变量替换为 `$openClawTask`：

```powershell
curl.exe -i "$baseUrl$($openClawTask.status_url)"
```

也可轮询直到任务完成或失败：

```powershell
do {
  $currentTask = curl.exe -sS "$baseUrl$($openClawTask.status_url)" | ConvertFrom-Json
  "{0:u}  {1}" -f (Get-Date), $currentTask.status
  if ($currentTask.status -in @("completed", "failed", "cancelled")) { break }
  Start-Sleep -Seconds 3
} while ($true)

$currentTask
```

状态含义：`queued` 表示已创建待执行，`running` 表示审核执行中，`collecting` 表示等待或读取 Agent 生成的 `findings.json`，`completed` 表示报告已生成；`failed` 和 `cancelled` 为终态。任务详情响应中的 `error` 字段可用于排查失败原因，`attempts` 可查看 OpenClaw 调用及产物收集信息。

不存在的任务应返回 `404 Not Found`：

```powershell
curl.exe -i "$baseUrl/api/v1/tasks/00000000-0000-0000-0000-000000000000"
```

## 7. 补采集任务产物：`POST /api/v1/tasks/{task_id}/collect`

此接口供 OpenClaw 任务在 SSE 中断后重新收集 `findings.json` 产物；正常完成的 `stub` 任务不需要调用。

```powershell
curl.exe -i "$baseUrl/api/v1/tasks/$($task.task_id)/collect" -X POST
```

对可采集的 OpenClaw 任务，预期为 `200 OK` 并返回最新任务对象。对不存在的任务，预期为 `404 Not Found`：

```powershell
curl.exe -i "$baseUrl/api/v1/tasks/00000000-0000-0000-0000-000000000000/collect" -X POST
```

任务当前不可采集时，接口返回 `409 Conflict`；请等待任务进入 `collecting` 状态，或检查该任务的 OpenClaw 工件配置。
