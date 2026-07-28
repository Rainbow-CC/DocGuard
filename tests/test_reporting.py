from docguard.domain.models import Finding
from docguard.services.profiles import ProfileRegistry
from docguard.services.reporting import render_markdown


def _finding(evidence_refs: list[dict[str, str]]) -> Finding:
    return Finding.model_validate(
        {
            "finding_id": "fd_example",
            "schema_version": "finding-v1",
            "rule_id": "DG-001",
            "category": "一致性",
            "review_dimension": "一致性与可读性",
            "judgment": "文本不一致",
            "severity": "一般",
            "confidence": 0.9,
            "title": "术语前后不一致",
            "text_evidence": ["内部定位字段"],
            "image_evidence": ["不适用（纯文本审核）"],
            "problem_description": "同一对象使用两个名称。",
            "impact": "影响评审理解。",
            "revision_suggestion": "统一术语。",
            "revision_location": "第 1 章",
            "completion_criteria": "全文仅保留一个术语。",
            "evidence_ids": ["block:42"],
            "evidence_refs": evidence_refs,
            "root_cause_key": "terminology:example",
            "agent_backend": "openclaw",
        }
    )


def test_report_uses_quotes_instead_of_internal_text_evidence_ids() -> None:
    report = render_markdown(
        ProfileRegistry().get("technical-audit"),
        [_finding([{"evidence_id": "block:42", "quote": "系统采用同步调用方式", "explanation": "调用方式未说明。"}])],
    )

    assert "文档原文：“系统采用同步调用方式”" in report
    assert "block:42" not in report


def test_report_describes_image_by_its_chapter_without_internal_id() -> None:
    report = render_markdown(
        ProfileRegistry().get("technical-audit"),
        [_finding([{"evidence_id": "image:image-abc", "quote": "网关", "explanation": "图中包含网关。"}])],
        {
            "candidate_images": [
                {"image_id": "image-abc", "chapter_number": 4, "chapter_title": "可选技术方案"}
            ]
        },
    )

    assert "第 4 章《可选技术方案》中的插图" in report
    assert "image:image-abc" not in report
