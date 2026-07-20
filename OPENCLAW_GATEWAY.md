# OpenClaw Gateway 后端 Endpoint 配置

本文说明如何将 WSL 中的 OpenClaw Gateway 作为本项目后端可调用的 OpenAI 兼容服务。不要把 Gateway Token 提交到仓库、日志或截图中。

## 已完成的本机配置

本机 WSL 的 `~/.openclaw/openclaw.json` 已应用以下设置，并已重启 `openclaw-gateway.service`：

```json
{
  "gateway": {
    "mode": "local",
    "bind": "lan",
    "port": 18789,
    "auth": { "mode": "token" },
    "controlUi": { "allowInsecureAuth": false },
    "http": {
      "endpoints": {
        "chatCompletions": { "enabled": true }
      }
    }
  }
}
```

`bind: "lan"` 让 Gateway 监听 WSL 的所有网络接口；Token 认证保持开启。OpenAI 兼容 HTTP 接口原本默认关闭，`chatCompletions.enabled: true` 才会打开 `/v1/*` 路由。

## 后端连接信息

同一台 Windows 主机上的后端优先使用：

```text
OPENCLAW_GATEWAY_URL=http://127.0.0.1:18789/v1
OPENCLAW_API_TOKEN=<Gateway Token>
```

WSL 访问使用同一地址。若 Windows 端的 WSL localhost 转发不可用，先在 WSL 里运行 `hostname -I`，取第一个 IPv4 地址，再将 URL 改成 `http://<WSL_IP>:18789/v1`。WSL 重启后 IP 可能变化，不应写死到源码。若运行环境配置了 HTTP(S) 代理，务必将 `127.0.0.1` 和实际 WSL IP 加入 `NO_PROXY`，否则请求可能被代理转发而失败。

可用路由：

| 用途 | 方法与路径 |
| --- | --- |
| 存活检查 | `GET /healthz` |
| 就绪检查 | `GET /readyz` |
| 模型/Agent 列表 | `GET /v1/models` |
| OpenAI Chat Completions | `POST /v1/chat/completions` |
| OpenAI Responses | `POST /v1/responses` |
| 单工具调用 | `POST /tools/invoke` |

应用侧的 OpenAI SDK `base_url` 必须包含 `/v1`。请求的 `model` 是 OpenClaw Agent 目标；推荐稳定值为 `openclaw/default`，而不是底层模型供应商 ID。

## 验证

在 WSL 中设置好 Token 后执行：

```bash
export OPENCLAW_API_TOKEN='<Gateway Token>'

curl --noproxy '*' -sS http://127.0.0.1:18789/v1/models \
  -H "Authorization: Bearer $OPENCLAW_API_TOKEN"

curl --noproxy '*' -sS http://127.0.0.1:18789/v1/chat/completions \
  -H "Authorization: Bearer $OPENCLAW_API_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "openclaw/default",
    "messages": [{"role": "user", "content": "请回复 OK"}]
  }'
```

Windows PowerShell（pwsh）：

```powershell
$env:OPENCLAW_API_TOKEN = '<Gateway Token>'

curl.exe --noproxy "*" -sS http://127.0.0.1:18789/v1/models `
  -H "Authorization: Bearer $env:OPENCLAW_API_TOKEN"

$body = @'
{
  "model": "openclaw/default",
  "messages": [{"role": "user", "content": "请回复 OK"}]
}
'@

curl.exe --noproxy "*" -sS http://127.0.0.1:18789/v1/chat/completions `
  -H "Authorization: Bearer $env:OPENCLAW_API_TOKEN" `
  -H "Content-Type: application/json" `
  --data-raw $body
```

查看服务状态、验证配置及重启：

```bash
openclaw config validate
openclaw gateway status
openclaw gateway restart
```

本次配置后已验证：`/healthz` 返回 `200`，未携带 Token 的 `/v1/models` 返回 `401`。这表示服务已启动、OpenAI 兼容路由已开启且认证仍生效。

## 调整或恢复监听范围

仅让 WSL/Windows 本机后端调用时，建议恢复最小暴露面：

```bash
openclaw config set gateway.bind '"loopback"' --strict-json
openclaw gateway restart
```

需要跨设备访问时，优先使用 Tailscale 或受 TLS 和身份认证保护的反向代理；不要把 `18789` 直接暴露到公网。WSL 的 `bind: "lan"` 只表示 Gateway 在 WSL 内监听所有接口，Windows 防火墙、WSL 网络模式和端口转发仍可能决定其他局域网设备是否可达。

## 安全边界

Gateway Token 是该 Gateway 的运营者凭据，而不是普通、低权限 API Key。持有者可在既有 Agent 工具策略允许的范围内执行高权限操作。因此：

- 不要将 Token 写入 `.env.example`、源码、Git 提交、浏览器前端或聊天记录。
- 后端通过部署环境变量或密钥管理器读取 `OPENCLAW_API_TOKEN`。
- 不要向不受信任的调用方暴露 `/tools/invoke`；它会受工具策略约束，但仍是强权限接口。
- 需要跨信任边界时，部署独立 Gateway/OS 用户，不要共用同一个 Token。

## DocGuard 当前接入状态

本项目已有 `OPENCLAW_GATEWAY_URL` 和 `OPENCLAW_API_TOKEN` 环境变量占位符，但 `src/docguard/adapters/agents.py` 中的 `OpenClawAgentGateway` 仍是待实现的适配边界。当前将任务 `agent_backend` 设为 `openclaw` 会抛出“not configured”错误；开启 Gateway endpoint 并不会自动完成 `Finding[]` 的 JSON 契约转换。

实施适配器时，应由服务端请求上述 `/v1/chat/completions`（或受限的 `/tools/invoke`），将返回内容解析为 `Finding[]`，并继续保留本项目的证据校验与报告渲染流程。

## DOCX 上传到 Agent 可读目录

DocGuard 提供 `POST /api/v1/agents/{agent_id}/uploads`，以 `multipart/form-data` 的 `file` 字段接收 DOCX。服务端将文件保存为 `<write-root>/<agent_id>/<upload_id>/source.docx`，响应中的 `agent_path` 则是应写入 OpenClaw 提示词的 Linux 路径。

默认开发配置为：

```text
DOCGUARD_UPLOAD_WRITE_ROOT=\\wsl.localhost\Ubuntu\home\ubuntu\docguard-inbox
DOCGUARD_UPLOAD_AGENT_ROOT=/home/ubuntu/docguard-inbox
```

示例：

```bash
curl -X POST http://127.0.0.1:8000/api/v1/agents/reviewer/uploads \
  -F "file=@./方案.docx;type=application/vnd.openxmlformats-officedocument.wordprocessingml.document"
```

Windows PowerShell（pwsh）：

```powershell
curl.exe -X POST http://127.0.0.1:8000/api/v1/agents/reviewer/uploads `
  -F "file=@.\方案.docx;type=application/vnd.openxmlformats-officedocument.wordprocessingml.document"
```

接口仅接受 DOCX，默认上限为 100 MiB。返回的 `content_sha256` 和 `source_uri` 可直接填入创建任务请求的 `document` 字段；向 Agent 发消息时提供返回的 `agent_path`。生产环境中将两个根目录环境变量都设置为 Linux 原生目录（例如 `/srv/docguard/inbox`）。

## 验证openclaw
curl --noproxy '*' -i   http://127.0.0.1:18789/v1/models   -H "Authorization: Bearer 1749b6cb454a449683c063951721b2cacc7b98f45a10af0e9f505d453fde8fc7"

Windows PowerShell（pwsh）：

```powershell
curl.exe --noproxy "*" -i http://127.0.0.1:18789/v1/models `
  -H "Authorization: Bearer $env:OPENCLAW_API_TOKEN"
```


## 参考

- [OpenClaw OpenAI Chat Completions API](https://docs.openclaw.ai/gateway/openai-http-api)
- [OpenClaw Tools Invoke API](https://docs.openclaw.ai/gateway/tools-invoke-http-api)
- [OpenClaw Gateway CLI](https://docs.openclaw.ai/cli/gateway)
