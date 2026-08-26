# 图像理解 Provider（应用部署配置）

Skill 和审核 Agent 不绑定任何视觉模型 Provider。应用进程通过 `VisionAdapter` 调用视觉模型；Provider SDK、认证和模型选择均由应用配置承担。Agent 不得安装客户端、写入密钥、调用视觉模型或猜测 Provider 调用方式。

| Provider | 状态 | 能力 | 客户端 |
| --- | --- | --- | --- |
| 由应用配置 | 图片理解、文本提示词、JSON 文本输出 | `VisionAdapter` |

## 视觉适配器约定

### 调用前准备

1. 使用 `rendered_png_file` 指向的 PNG 作为 `IMAGE_FILE`，不得重新编码或修改。
2. 应用从当前规则包生成 `VISION_PROMPT_FILE`；不把章节正文传给本次调用。
3. 适配器的认证、模型选择和 Provider SDK/CLI 均由部署配置承担，不能进入 Skill。

### 调用方法

```bash
VisionAdapter.describe(原始 PNG 字节, 提示词文本)
```

将标准输出原样保存为响应文件，例如 `vision-response.raw.txt`。Shell 调用必须使用 `set -euo pipefail`，并在调用前确认图片和提示词文件均存在且非空。不在命令行中输出或记录认证密钥。

### 响应处理

将适配器返回的标准输出原样作为视觉反馈使用。响应可能是 JSON、含 JSON 的文本或非结构化文本；应用可额外生成校验后的事实 JSON，但不得改写原始响应或通过后处理猜测图片事实。仅在调用命令本身失败时记录局限性并继续。
