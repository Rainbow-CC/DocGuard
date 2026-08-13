from typing import Any

from docguard.domain.models import AuditProfile, EvidenceRef, Finding


def render_markdown(
    profile: AuditProfile,
    findings: list[Finding],
    evidence: dict[str, Any] | None = None,
    findings_by_dimension: dict[str, list[Finding]] | None = None,
) -> str:
    """Deterministic report renderer. Agents never create the final report."""
    lines = [
        "# 技术文档审核报告",
        "",
        f"- 审核 Profile：`{profile.profile_id}@{profile.version}`",
        f"- 报告模板：`{profile.report_template}`",
        f"- 问题总数：{len(findings)}",
        "",
        "## 问题台账",
        "",
    ]
    if not findings:
        lines.append("未发现可验证的问题。")
    groups = findings_by_dimension or {"综合": findings}
    for dimension, group in groups.items():
        if findings_by_dimension:
            lines.extend([f"## {dimension}", ""])
        for finding in group:
            lines.extend(_render_finding(finding, evidence))
    return "\n".join(lines)


def _render_finding(finding: Finding, evidence: dict[str, Any] | None) -> list[str]:
    return [
        f"### {finding.title}",
        "",
        f"- 编号：`{finding.finding_id}`",
        f"- 严重级别：{finding.severity}",
        f"- 规则：`{finding.rule_id}`",
        "- 核查依据：",
        *(
            [f"  - {_display_evidence(ref, evidence)}" for ref in finding.evidence_refs]
            or ["  - 未提供可展示的核查依据。"]
        ),
        f"- 判断：{finding.claim}",
        f"- 建议：{finding.recommendation}",
        f"- 完成标准：{finding.acceptance_criteria}",
        "",
    ]


def _display_evidence(ref: EvidenceRef, evidence: dict[str, Any] | None) -> str:
    """生成面向读者的证据描述，不展示内部证据 ID。

    例如，`EvidenceRef(evidence_id="block:42", quote="系统采用同步调用方式")`
    会使用 `quote` 字段生成 `文档原文：“系统采用同步调用方式”`。
    `image:<图片 ID>` 引用会在 `evidence["candidate_images"]` 中查找对应图片，
    并使用其 `chapter_number` 和 `chapter_title` 字段生成
    `第 4 章《可选技术方案》中的插图`。
    """
    if ref.evidence_id.startswith("image:"):
        image_id = ref.evidence_id.removeprefix("image:")
        image = next(
            (
                item
                for item in (evidence or {}).get("candidate_images", [])
                if isinstance(item, dict) and item.get("image_id") == image_id
            ),
            None,
        )
        if image:
            number = image.get("chapter_number")
            title = image.get("chapter_title")
            if isinstance(number, int) and isinstance(title, str) and title:
                return f"第 {number} 章《{title}》中的插图"
            if isinstance(title, str) and title:
                return f"《{title}》中的插图"
        return "文档中的相关插图"
    return f"文档原文：“{ref.quote}”"
