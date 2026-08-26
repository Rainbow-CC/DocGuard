from __future__ import annotations

import importlib.util
import sys
import zipfile
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "doc-audit-integrate-skill" / "scripts" / "extract_docx_structure.py"
SPEC = importlib.util.spec_from_file_location("extract_docx_structure", SCRIPT)
assert SPEC and SPEC.loader
extractor = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = extractor
SPEC.loader.exec_module(extractor)


DOCUMENT_XML = '''<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p>
      <w:pPr><w:pStyle w:val="Child"/><w:jc w:val="center"/></w:pPr>
      <w:r><w:rPr><w:rFonts w:eastAsia="Microsoft YaHei"/><w:b w:val="0"/></w:rPr><w:t>标题</w:t></w:r>
    </w:p>
    <w:tbl><w:tr><w:tc><w:p><w:pPr><w:spacing w:before="60" w:after="120" w:line="240" w:lineRule="auto"/></w:pPr><w:r><w:t>单元格</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
  </w:body>
</w:document>'''

STYLES_XML = '''<?xml version="1.0" encoding="UTF-8"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:eastAsia="SimSun"/><w:sz w:val="21"/></w:rPr></w:rPrDefault></w:docDefaults>
  <w:style w:type="paragraph" w:styleId="Base"><w:pPr><w:spacing w:before="120"/></w:pPr><w:rPr><w:b/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="Child"><w:basedOn w:val="Base"/><w:pPr><w:outlineLvl w:val="2"/><w:spacing w:after="240" w:line="360" w:lineRule="auto"/></w:pPr><w:rPr><w:sz w:val="32"/></w:rPr></w:style>
</w:styles>'''


def write_docx(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", DOCUMENT_XML)
        archive.writestr("word/styles.xml", STYLES_XML)


def test_extract_writes_resolved_run_and_table_cell_formatting(tmp_path, monkeypatch) -> None:
    source = tmp_path / "sample.docx"
    output = tmp_path / "extracted"
    write_docx(source)

    monkeypatch.setattr(sys, "argv", [str(SCRIPT), str(source), "--output", str(output)])
    assert extractor.main() == 0

    formatting = extractor.json.loads((output / "block-formatting.json").read_text(encoding="utf-8"))
    context = extractor.json.loads((output / "block-formatting-context.json").read_text(encoding="utf-8"))
    title = formatting["blocks"][0]
    run = title["runs"][0]

    assert title["block_index"] == 0
    assert title["heading"] == {"level": 3, "basis": "style_outline_level"}
    assert title["paragraph"]["alignment"] == "center"
    assert title["paragraph"]["spacing"] == {"after": "240", "line": "360", "lineRule": "auto"}
    assert run["text"] == "标题"
    assert run["font"] == {
        "font_east_asia": "Microsoft YaHei",
        "size_pt": 16.0,
        "bold": False,
    }
    assert run["source"] == {
        "font_east_asia": "direct",
        "size_pt": "style:Child",
        "bold": "direct",
    }

    cell_run = formatting["blocks"][1]["cells"][0][0]["paragraphs"][0]["runs"][0]
    assert cell_run["font"] == {"font_east_asia": "SimSun", "size_pt": 10.5}
    assert cell_run["source"] == {"font_east_asia": "doc_default", "size_pt": "doc_default"}
    assert formatting["blocks"][1]["cells"][0][0]["paragraphs"][0]["paragraph"]["spacing"] == {
        "before": "60", "after": "120", "line": "240", "lineRule": "auto"
    }

    assert context["schema_version"] == "2.0"
    assert context["content_scope"] == "text_only"
    section = context["items"][0]
    assert section["kind"] == "section"
    assert section["level"] == 3
    assert section["heading_basis"] == "style_outline_level"
    assert section["items"] == []  # 表格不进入文本审核树。
    assert section["title"] == {
        "block_index": 0,
        "text": "标题",
        "style_id": "Child",
        "paragraph": {"alignment": "center", "spacing": {"after": "240", "line": "360", "lineRule": "auto"}},
        "runs": [{
            "text": "标题",
            "start": 0,
            "end": 2,
            "font": {"font_east_asia": "Microsoft YaHei", "size_pt": 16.0, "bold": False},
        }],
    }


def test_context_builds_nested_text_only_tree() -> None:
    font = {"font_east_asia": "FangSong", "size_pt": 12.0}
    formatting = {
        "source": "sample.docx",
        "revisions": {"mode": "accept"},
        "blocks": [
            {
                "block_index": 0,
                "type": "paragraph",
                "style_id": "Heading1",
                "heading": {"level": 1, "basis": "style_outline_level"},
                "paragraph": {},
                "runs": [{"text": "第一章", "font": font, "source": {"font_east_asia": "style:Heading1"}}],
            },
            {
                "block_index": 1,
                "type": "paragraph",
                "style_id": None,
                "heading": None,
                "paragraph": {"spacing": {"line": "360", "lineRule": "auto"}},
                "runs": [{"text": "第一章正文", "font": font, "source": {"font_east_asia": "direct"}}],
            },
            {
                "block_index": 2,
                "type": "paragraph",
                "style_id": "Heading2",
                "heading": {"level": 2, "basis": "style_outline_level"},
                "paragraph": {},
                "runs": [{"text": "1.1 小节", "font": font, "source": {"font_east_asia": "style:Heading2"}}],
            },
            {
                "block_index": 3,
                "type": "paragraph",
                "style_id": None,
                "heading": None,
                "paragraph": {},
                "runs": [{"text": "小节正文", "font": font, "source": {"font_east_asia": "direct"}}],
            },
            {
                "block_index": 4,
                "type": "paragraph",
                "style_id": "Heading1",
                "heading": {"level": 1, "basis": "style_outline_level"},
                "paragraph": {},
                "runs": [{"text": "第二章", "font": font, "source": {"font_east_asia": "style:Heading1"}}],
            },
        ],
    }

    context = extractor.build_block_formatting_context(formatting)

    first, second = context["items"]
    first_body, child = first["items"]
    child_body = child["items"][0]

    assert first["kind"] == "section"
    assert first["title"]["text"] == "第一章"
    assert first_body["kind"] == "paragraph"
    assert first_body["block_index"] == 1
    assert first_body["text"] == "第一章正文"
    assert first_body["paragraph"]["spacing"] == {"line": "360", "lineRule": "auto"}
    assert first_body["runs"][0]["font"] == font
    assert "source" not in first_body["runs"][0]
    assert child["kind"] == "section"
    assert child["level"] == 2
    assert child["title"]["text"] == "1.1 小节"
    assert child_body["text"] == "小节正文"
    assert second["title"]["text"] == "第二章"
