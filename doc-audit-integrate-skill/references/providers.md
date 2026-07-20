# 图像理解 Provider

选择标记为“可用”的 Provider。不得在 Skill 中安装客户端、写入密钥或猜测未列出的 Provider 调用方式。

| Provider | 状态 | 能力 | 客户端 |
| --- | --- | --- | --- |
| `minimax` | 可用（由服务器部署并配置） | 图片理解、文本提示词、JSON 文本输出 | `mmx` |

## minimax

### 调用前准备

1. 使用 `rendered_png_file` 指向的 PNG 作为 `IMAGE_FILE`。
2. 读取 `vision-extraction-prompt.md` 和 `architecture-facts.schema.json`，将提示词中的 `<PASTE architecture-facts.schema.json HERE>` 替换为完整 Schema，得到 `VISION_PROMPT`。
3. 不要把章节正文传给本次调用。

### 调用方法

```bash
mmx vision describe --image "$IMAGE_FILE" --prompt "$VISION_PROMPT"
```

将标准输出原样保存为响应文件，例如 `vision-response.raw.txt`。Shell 调用必须使用 `set -euo pipefail`，并在调用前确认图片和提示词文件均存在且非空。不在命令行中输出或记录认证密钥。

### 响应处理

将 `mmx` 返回的标准输出原样作为视觉反馈使用。响应可能是 JSON、含 JSON 的文本或非结构化文本；不得解析或提取 JSON 段，也不得依据 `architecture-facts.schema.json` 校验、拒绝或重试响应。仅在调用命令本身失败时记录局限性并继续。不得通过后处理猜测或改变图片事实。
