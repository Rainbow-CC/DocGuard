# 视觉事实提取提示词

每次仅附上一张已提取的图片。此阶段不要附带章节正文，避免文字描述影响对图片事实的判断。

```text
你是一名企业架构图事实提取专家。仅检查所附图片，提取图片中可见的客观事实；不得评价架构质量、推断图片未展示的技术信息，或使用外部知识补全内容。

识别图类型、带标签的组件、可见技术、实例数量、高可用模式、连接关系、协议，以及包围组件的方框、层和区域。对于嵌套方框图，将每一个可见方框建模为一个 zone。为每个 zone 设置唯一 ID；parent_zone_id 填直接包围它的 zone ID，顶层方框填 null。组件 ID 只能放在直接包含该组件的 zone 中。不得仅因组件位于同一方框或层内就创建连接关系。

图片不能确定的事实请使用 unknown、ambiguous 或 null。每个组件、连接和 zone 都必须带简洁的图片证据以及 0 到 1 的置信度；Schema 中的每个必填字段都必须出现。连接的 source 和 target 必须是组件 ID；`direction` 只能是 `source_to_target`、`target_to_source`、`bidirectional` 或 `unknown`，禁止输出 down、up、left、right、forward 等空间方向词。若只能看出空间位置而不能确定源和目标，使用 `unknown`。

只返回一个 JSON 对象，不要使用 Markdown、自然语言说明或 Schema 本身；输出必须符合以下 Schema：
<PASTE architecture-facts.schema.json HERE>
```

若模型供应商支持结构化输出或 JSON Schema 响应格式，也应在接口参数中原生传入该 Schema。

## 示例

以下仅为格式示例；除非所附图片中确实可见，否则不得复用其中的事实。

对于一个外层为“应用层”、内部包含“基础框架”和“基础服务”两个子方框的图片，嵌套 zone 应如下表示：

```json
{
  "diagram_type": "system_architecture",
  "components": [
    {"id": "spring_boot", "name": "Spring Boot", "category": "framework", "technology": "Spring Boot", "instances": "unknown", "availability_mode": "unknown", "evidence": "“基础框架”方框内标注 Spring Boot。", "confidence": 0.96},
    {"id": "gateway", "name": "Gateway", "category": "application_service", "technology": null, "instances": "unknown", "availability_mode": "unknown", "evidence": "“基础服务”方框内标注 Gateway。", "confidence": 0.96}
  ],
  "connections": [],
  "zones": [
    {"id": "application_layer", "name": "应用层", "zone_type": "layer", "parent_zone_id": null, "component_ids": [], "evidence": "最外层方框标注“应用层”。", "confidence": 0.98},
    {"id": "basic_framework", "name": "基础框架", "zone_type": "group", "parent_zone_id": "application_layer", "component_ids": ["spring_boot"], "evidence": "位于“应用层”方框内的“基础框架”子方框。", "confidence": 0.97},
    {"id": "basic_service", "name": "基础服务", "zone_type": "group", "parent_zone_id": "application_layer", "component_ids": ["gateway"], "evidence": "位于“应用层”方框内的“基础服务”子方框。", "confidence": 0.97}
  ],
  "uncertainties": [
    {"subject": "spring_boot_to_gateway", "reason": "分层包含关系本身不表示调用关系，图中未见箭头。"}
  ]
}
```
