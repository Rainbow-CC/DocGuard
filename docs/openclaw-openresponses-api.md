# OpenClaw `POST /v1/responses` 接口契约

本文汇总 DocGuard 接入 OpenClaw Gateway 的 OpenResponses 兼容接口：请求输入、普通 JSON 输出、SSE 流式事件和会话续接方式。本文面向 OpenClaw `2026.6.10`；升级 Gateway 后应以官方接口文档和实际 Gateway 响应为准。

## 1. 启用与访问

接口地址与 Gateway 共用端口：

```text
POST http://<gateway-host>:18789/v1/responses
```

默认关闭。Gateway 配置需要包含：

```json
{
  "gateway": {
    "http": {
      "endpoints": {
        "responses": { "enabled": true }
      }
    }
  }
}
```

使用 Token 鉴权时，请求头为：

```http
Authorization: Bearer <OPENCLAW_GATEWAY_TOKEN>
Content-Type: application/json
```

Gateway Token 是运营者级凭据。浏览器前端不应直接持有它；应由 DocGuard 后端代为调用。

## 2. 请求结构

最小请求：

```json
{
  "model": "openclaw/default",
  "input": "请开始审核。"
}
```

常用字段如下：

| 字段 | 类型 | 作用 |
| --- | --- | --- |
| `model` | string | Agent 目标：`openclaw`、`openclaw/default` 或 `openclaw/<agentId>`。它不是底层模型 ID。 |
| `input` | string 或 item 数组 | 当前消息与可选历史。必须最终包含一条用户消息。 |
| `user` | string | 稳定会话标识。对同一 DocGuard 对话始终复用该值。 |
| `stream` | boolean | `true` 时响应为 SSE；默认返回一个完整 JSON Response。 |
| `instructions` | string | 追加至 Agent 的系统提示词。不要在此放置密钥。 |
| `previous_response_id` | string | 在相同鉴权主体、Agent、`user`/会话范围内复用上一响应的会话。 |
| `tools` | array | 调用方提供的 function tools；Agent 需要调用时会返回 `function_call` output item。 |
| `tool_choice` | string/object | `auto`、`none`、`required`，或指定函数名称。 |
| `max_output_tokens` | integer | 尽力限制输出 token；实际支持度取决于上游模型。 |
| `temperature` / `top_p` | number | 尽力传递给上游模型；部分后端会忽略。 |

可选 Gateway 请求头：

| 请求头 | 作用 |
| --- | --- |
| `x-openclaw-agent-id` | Agent 选择的兼容写法；通常优先在 `model` 中明确 Agent。 |
| `x-openclaw-model` | 覆盖选中 Agent 的底层模型；身份代理模式下需要 `operator.admin`。 |
| `x-openclaw-session-key` | 显式指定会话 key；不得使用 `subagent:`、`cron:`、`acp:` 前缀。 |
| `x-openclaw-message-channel` | 指定合成的入站消息通道上下文。 |

### 2.1 `input` item 数组

`input` 可直接是字符串；需要文本、图片、文件或工具结果混合时使用 item 数组。常见的用户消息为：

```json
{
  "type": "message",
  "role": "user",
  "content": [
    { "type": "input_text", "text": "请审核附件。" },
    {
      "type": "input_file",
      "source": {
        "type": "base64",
        "media_type": "application/pdf",
        "filename": "方案.pdf",
        "data": "<Base64 内容，不含 data: 前缀>"
      }
    }
  ]
}
```

支持的 item/内容部分：

| 类型 | 结构与用途 |
| --- | --- |
| `message` | `role` 为 `system`、`developer`、`user` 或 `assistant`；`content` 为字符串或内容部分数组。 |
| `input_text` | `{ "type": "input_text", "text": "..." }`。 |
| `input_image` | `source` 为 `{ "type": "base64", "media_type", "data" }` 或 `{ "type": "url", "url" }`。 |
| `input_file` | `source` 为 Base64 或 URL 文件。默认仅接受 TXT、Markdown、HTML、CSV、JSON、PDF，单文件默认最大 5 MiB。 |
| `function_call_output` | `{ "type": "function_call_output", "call_id": "...", "output": "..." }`；用于把调用方执行的 function tool 结果回传给下一轮。 |
| `reasoning` / `item_reference` | 为兼容性接受；当前不参与提示词构建。 |

`input_file` 解码后的文本仅在当前请求中作为不受信任的外部内容注入，不会保存到 Agent 会话历史。它不是 DOCX 上传通道。

### 2.2 DocGuard 的 DOCX 调用方式

DocGuard 的上传接口会将 DOCX 保存到 Agent 可读取的 WSL 路径。调用 OpenResponses 时传文本路径，而不是将 DOCX 放入 `input_file`：

```json
{
  "model": "openclaw/reviewer",
  "user": "docguard:task:7d8d8fd3:attempt:<attempt-id>:agent:<agent-id>",
  "stream": true,
  "input": "待审 DOCX 已保存至 /home/ubuntu/docguard-inbox/reviewer/<upload-id>/source.docx。请按既有工作流和必备工具读取、解析并继续审核。"
}
```

Agent 必须具有该目录的只读访问权限，以及解析 DOCX 的已批准工具。若 Agent 使用 Docker sandbox，应将收件目录只读挂载到容器内，并在提示词中使用容器内路径。

## 3. 非流式输出

未设置 `stream`（或设为 `false`）时，成功响应为一个 Response resource。典型结构：

```json
{
  "id": "resp_<uuid>",
  "object": "response",
  "created_at": 1760000000,
  "status": "completed",
  "model": "openclaw/reviewer",
  "output": [
    {
      "id": "msg_<uuid>",
      "type": "message",
      "role": "assistant",
      "content": [
        { "type": "output_text", "text": "审核结果……" }
      ],
      "phase": "final_answer",
      "status": "completed"
    }
  ],
  "usage": {
    "input_tokens": 123,
    "output_tokens": 456,
    "total_tokens": 579
  }
}
```

字段说明：

| 字段 | 说明 |
| --- | --- |
| `id` | 本次 Response ID；可保存为下一轮的 `previous_response_id`。 |
| `status` | 常见值为 `completed`、`in_progress`、`incomplete`、`failed`。 |
| `output` | Agent 输出 item 数组。普通文本在 `message.content[]` 的 `output_text.text` 中。 |
| `usage` | 上游模型报告时返回 token 统计；无统计时为零值。 |
| `error` | 失败时可包含 `{ "code": "...", "message": "..." }`。 |

如果 Agent 选择了调用方定义的 function tool，响应可能为 `status: "incomplete"`，并在 `output` 中携带：

```json
{
  "type": "function_call",
  "id": "call_<uuid>",
  "call_id": "call_123",
  "name": "lookup_evidence",
  "arguments": "{\"evidence_id\":\"txt_001\"}",
  "status": "completed"
}
```

调用方执行该函数后，以 `function_call_output` 作为下一轮 `input` 发送回 Gateway。

## 4. 流式输出（SSE）

设置 `stream: true` 后，响应为 `Content-Type: text/event-stream`。每个事件由 `event: <事件名>` 与 `data: <JSON>` 组成，最后以 `data: [DONE]` 结束。

常见事件顺序：

```text
response.created
response.in_progress
response.output_item.added
response.content_part.added
response.output_text.delta  # 可重复，增量文本在 data.delta
response.output_text.done
response.content_part.done
response.output_item.done
response.completed          # data.response 是最终 Response resource
data: [DONE]
```

异常时可能收到 `response.failed`，其 `data.response.status` 为 `failed`。前端应逐个拼接 `response.output_text.delta.data.delta`，但以 `response.completed.data.response` 为最终、可保存的完整结果。

## 5. 会话续接规则

DocGuard 为每个专项 Agent 的每次尝试创建一个稳定会话；推荐将该三元组映射为 `user` 值：

```text
docguard:task:<task-id>:attempt:<attempt-id>:agent:<agent-id>
```

同一个 AgentRun 的后续请求保持相同的 `model` 和 `user`，Gateway 会派生并复用同一 Agent 会话。`attempt-id` 能隔离任务重试，`agent-id` 能隔离并行专项审核。不要把账号级 ID 直接作为 `user`，否则不同文档/对话会共享上下文。

若使用 `previous_response_id`，必须保持相同的鉴权主体、Agent 和会话范围；它复用之前 Response 对应的会话，而非复制完整历史到当前请求。

## 6. 错误与安全要求

常见 HTTP 错误：

| 状态码 | 含义 |
| --- | --- |
| `400` | 请求体不合法、缺少用户消息或不支持的输入。 |
| `401` | 未提供或提供了错误的 Gateway Token。 |
| `403` | 身份代理模式下缺少所需 operator scope。 |
| `405` | 使用了错误的 HTTP 方法。 |
| `429` | 鉴权失败触发限流。 |
| `5xx` | Gateway 或上游模型调用失败。 |

共享 Token 模式下，HTTP Bearer Token 代表完整 Gateway 运营者权限，而不是租户级或用户级 API Key。DocGuard 应把 Token 保存在服务端密钥配置中；不要发送给浏览器，也不要把它写进任务记录、提示词、日志或测试样例。

## 7. 最小 cURL 示例

```bash
curl -N  http://127.0.0.1:18789/v1/responses \
  -H "Authorization: Bearer $OPENCLAW_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "openclaw/default",
    "user": "docguard:task:7d8d8fd3:attempt:<attempt-id>:agent:<agent-id>",
    "stream": true,
    "input": "请开始审核。"
  }'
```

PowerShell 7（非流式，使用 `curl.exe`）：

```powershell
curl.exe "http://127.0.0.1:18789/v1/responses" `
  -H "Authorization: Bearer $env:OPENCLAW_API_TOKEN" `
  -H "Content-Type: application/json" `
  --data-raw '{"model":"openclaw/default","user":"docguard:task:7d8d8fd3:attempt:<attempt-id>:agent:<agent-id>","stream":false,"input":"你安装了文档审核相关的技能。"}'
```

PowerShell 7（SSE 流式）：

```powershell
curl.exe -N --noproxy "*"  "http://127.0.0.1:18789/v1/responses" `
  -H "Authorization: Bearer $env:OPENCLAW_API_TOKEN" `
  -H "Content-Type: application/json" `
  --data-raw '{"model":"openclaw/default","user":"docguard:task:7d8d8fd3:attempt:<attempt-id>:agent:<agent-id>","stream":true,"input":"待审核文档位于 /home/ubuntu/docguard-inbox/reviewer/afda189f-f5a9-46ab-af41-c13a6e26316a/source.docx  请开始审核"}'
```

## 来源

- [OpenClaw OpenResponses API（官方）](https://docs.openclaw.ai/gateway/openresponses-http-api)：启用方式、鉴权、Agent 路由、请求字段、会话规则、文件限制、SSE 事件和错误码。
- [OpenClaw OpenAI Chat Completions API（官方）](https://docs.openclaw.ai/gateway/openai-http-api)：Agent 目标模型命名与 Gateway Bearer Token 语义。
- [OpenClaw Gateway Protocol（官方）](https://docs.openclaw.ai/gateway/protocol)：Dashboard 使用的 WebSocket 控制面、会话与聊天 RPC；需要完全复刻 Dashboard 的会话管理、工具生命周期或中止/插队能力时应使用此协议。
- 本机安装的 OpenClaw `2026.6.10`：`/home/ubuntu/.npm-global/lib/node_modules/openclaw/dist/openresponses-http-*.js`，用于核对本文列出的 Response resource、output item 与 SSE JSON 字段形状。

PowerShell 7：

```powershell
curl.exe --noproxy "*" "http://127.0.0.1:18789/v1/models" `
  -H "Authorization: Bearer $env:OPENCLAW_API_TOKEN"
```
