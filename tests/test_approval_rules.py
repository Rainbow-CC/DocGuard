from pathlib import Path

from docguard.services.approval_rules import ApprovalRuleCatalog


def test_catalog_uses_one_markdown_file_per_rule_type_and_orders_documents(tmp_path: Path) -> None:
    (tmp_path / "overview-design.md").write_text(
        "---\ntitle: 概要设计审核\ndescription: 第二个示例\norder: 20\n---\n# 概要设计\n## 范围\n",
        encoding="utf-8",
    )
    (tmp_path / "technical-architecture.md").write_text(
        "---\ntitle: 技术架构审核\norder: 10\n---\n# 技术架构\n## <script>不可执行</script>\n",
        encoding="utf-8",
    )
    (tmp_path / "not a rule.md").write_text("# 忽略", encoding="utf-8")
    catalog = ApprovalRuleCatalog(tmp_path)

    rules = catalog.list()
    detail = catalog.get("technical-architecture")

    assert [rule.rule_id for rule in rules] == ["technical-architecture", "overview-design"]
    assert [item.title for item in detail.outline] == ["技术架构", "<script>不可执行</script>"]
    assert 'id="rule-technical-architecture-2"' in detail.html
    assert "<script>不可执行</script>" not in detail.html
    assert "&lt;script&gt;不可执行&lt;/script&gt;" in detail.html
