from docguard.domain.models import AuditProfile, Finding


def render_markdown(profile: AuditProfile, findings: list[Finding]) -> str:
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
    for finding in findings:
        lines.extend(
            [
                f"### {finding.title}",
                "",
                f"- 编号：`{finding.finding_id}`",
                f"- 严重级别：{finding.severity}",
                f"- 规则：`{finding.rule_id}`",
                f"- 证据：{', '.join(finding.evidence_ids)}",
                f"- 判断：{finding.claim}",
                f"- 建议：{finding.recommendation}",
                f"- 完成标准：{finding.acceptance_criteria}",
                "",
            ]
        )
    return "\n".join(lines)
