"""Fixed-layout, evidence-first PDF report rendering."""

from __future__ import annotations

from collections import Counter
from io import BytesIO
from pathlib import Path
from typing import Any, Callable
from xml.sax.saxutils import escape

from PIL import Image as PilImage
from PIL import ImageDraw
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import (
    HRFlowable,
    Image,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from docguard.domain.models import AuditProfile, EvidenceRef, Finding

_W, _H = A4
_MARGIN = 1.7 * cm
_CONTENT = _W - _MARGIN * 2
_SEVERITY_COLORS = {
    "重大": colors.HexColor("#B42318"),
    "一般": colors.HexColor("#B54708"),
    "优化": colors.HexColor("#175CD3"),
}


def render_pdf(
    profile: AuditProfile,
    findings: list[Finding],
    evidence: dict[str, Any] | None = None,
    image_path_for: Callable[[str], Path | None] | None = None,
) -> bytes:
    """Render a standalone report; agent-declared primary evidence comes first."""
    _font()
    output = BytesIO()
    doc = SimpleDocTemplate(
        output,
        pagesize=A4,
        leftMargin=_MARGIN,
        rightMargin=_MARGIN,
        topMargin=1.8 * cm,
        bottomMargin=1.6 * cm,
        title="技术文档审核报告",
        author="DocGuard",
    )
    s = _styles()
    story: list[Any] = (
        _cover(profile, findings, s)
        + _summary(findings, s)
        + [PageBreak(), Paragraph("问题台账", s["h1"]), Spacer(1, 0.18 * cm)]
    )
    if not findings:
        story.append(Paragraph("未发现可验证的问题。", s["body"]))
    for index, finding in enumerate(_sort(findings), 1):
        story.extend(_finding(index, finding, evidence, image_path_for, s))
    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return output.getvalue()


def _font() -> None:
    if "STSong-Light" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()

    def style(
        name: str, parent: str, size: float, leading: float, color: str, **kw: Any
    ) -> ParagraphStyle:
        return ParagraphStyle(
            name,
            parent=base[parent],
            fontName="STSong-Light",
            fontSize=size,
            leading=leading,
            textColor=colors.HexColor(color),
            **kw,
        )

    return {
        "cover": style("cover", "Title", 26, 34, "#172B4D", alignment=TA_CENTER),
        "subtitle": style("subtitle", "Normal", 11, 17, "#52637A", alignment=TA_CENTER),
        "h1": style(
            "h1", "Heading1", 17, 24, "#172B4D", spaceBefore=0.1 * cm, spaceAfter=0.25 * cm
        ),
        "h2": style(
            "h2", "Heading2", 13, 19, "#172B4D", spaceBefore=0.28 * cm, spaceAfter=0.14 * cm
        ),
        "body": style("body", "BodyText", 9.6, 15, "#243B53", spaceAfter=0.1 * cm),
        "small": style("small", "BodyText", 8.2, 12, "#627D98"),
        "label": style("label", "BodyText", 9.2, 14, "#52637A"),
    }


def _cover(
    profile: AuditProfile, findings: list[Finding], s: dict[str, ParagraphStyle]
) -> list[Any]:
    rows = [
        [Paragraph(label, s["label"]), Paragraph(_safe(value), s["body"])]
        for label, value in [
            ("审核 Profile", f"{profile.profile_id}@{profile.version}"),
            ("报告模板", profile.report_template),
            ("发现项总数", str(len(findings))),
        ]
    ]
    table = Table(rows, colWidths=[3.4 * cm, 9.6 * cm], hAlign="CENTER")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#F2F6FC")),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#D9E2EC")),
                ("INNERGRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#D9E2EC")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    return [
        Spacer(1, 5.2 * cm),
        Paragraph("技术文档审核报告", s["cover"]),
        Paragraph("DocGuard · 证据驱动审核", s["subtitle"]),
        Spacer(1, 1.4 * cm),
        HRFlowable(width="74%", thickness=1.2, color=colors.HexColor("#2F80ED"), hAlign="CENTER"),
        Spacer(1, 1.1 * cm),
        table,
        Spacer(1, 2.3 * cm),
        Paragraph("本报告由系统基于结构化审核发现和原始证据生成。", s["subtitle"]),
        PageBreak(),
    ]


def _summary(findings: list[Finding], s: dict[str, ParagraphStyle]) -> list[Any]:
    counts = Counter(finding.severity for finding in findings)
    table = Table(
        [
            [Paragraph(name, s["label"]) for name in ("问题总数", "重大", "一般", "优化")],
            [
                Paragraph(str(value), s["h1"])
                for value in (len(findings), counts["重大"], counts["一般"], counts["优化"])
            ],
        ],
        colWidths=[_CONTENT / 4] * 4,
    )
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F2F6FC")),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#D9E2EC")),
                ("INNERGRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#D9E2EC")),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    return [Paragraph("审核概要", s["h1"]), table, Spacer(1, 0.5 * cm)]


def _sort(findings: list[Finding]) -> list[Finding]:
    return sorted(
        findings,
        key=lambda item: (
            {"重大": 0, "一般": 1, "优化": 2}.get(item.severity, 99),
            item.finding_id,
        ),
    )


def _finding(
    index: int,
    finding: Finding,
    evidence: dict[str, Any] | None,
    resolve_image: Callable[[str], Path | None] | None,
    s: dict[str, ParagraphStyle],
) -> list[Any]:
    badge = Table([[Paragraph(str(finding.severity), s["body"])]], colWidths=[1.35 * cm])
    badge.setStyle(
        TableStyle(
            [
                (
                    "BACKGROUND",
                    (0, 0),
                    (-1, -1),
                    _SEVERITY_COLORS.get(str(finding.severity), colors.HexColor("#52637A")),
                ),
                ("TEXTCOLOR", (0, 0), (-1, -1), colors.white),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    header = Table(
        [[Paragraph(f"{index:02d}. {_safe(finding.title)}", s["h2"]), badge]],
        colWidths=[_CONTENT - 1.55 * cm, 1.55 * cm],
    )
    header.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    result: list[Any] = [
        header,
        Paragraph(
            f"<b>规则：</b>{_safe(finding.rule_id)}　　<b>修订位置：</b>{_safe(finding.revision_location)}",
            s["small"],
        ),
        Paragraph(f"<b>判断：</b>{_safe(finding.claim)}", s["body"]),
        Paragraph(f"<b>影响：</b>{_safe(finding.impact)}", s["body"]),
        Paragraph(f"<b>建议：</b>{_safe(finding.recommendation)}", s["body"]),
        Paragraph(f"<b>完成标准：</b>{_safe(finding.acceptance_criteria)}", s["body"]),
    ]
    refs = sorted(finding.evidence_refs, key=lambda ref: 0 if ref.role == "primary" else 1)
    if refs:
        result.append(Paragraph("证据", s["h2"]))
        for ref in refs:
            result.extend(_evidence(ref, evidence, resolve_image, s))
    else:
        result.append(Paragraph("未提供可展示的结构化证据。", s["small"]))
    return result + [
        Spacer(1, 0.2 * cm),
        HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#D9E2EC")),
        Spacer(1, 0.22 * cm),
    ]


def _evidence(
    ref: EvidenceRef,
    evidence: dict[str, Any] | None,
    resolve_image: Callable[[str], Path | None] | None,
    s: dict[str, ParagraphStyle],
) -> list[Any]:
    result: list[Any] = [
        Paragraph(
            f"<b>{'主证据' if ref.role == 'primary' else '辅助证据'}</b>　{_safe(ref.explanation)}",
            s["body"],
        )
    ]
    if not ref.evidence_id.startswith("image:"):
        return result + [
            Paragraph(f'原文摘录：<font color="#52637A">“{_safe(ref.quote)}”</font>', s["body"]),
            Spacer(1, 0.08 * cm),
        ]
    image_id = ref.evidence_id.removeprefix("image:")
    item = next(
        (
            row
            for row in (evidence or {}).get("candidate_images", [])
            if row.get("image_id") == image_id
        ),
        {},
    )
    caption = (
        f"图像位置：第 {item.get('chapter_number')} 章《{_safe(item.get('chapter_title'))}》"
        if item.get("chapter_number") and item.get("chapter_title")
        else "图像位置：文档插图"
    )
    result.append(Paragraph(caption, s["small"]))
    path = resolve_image(image_id) if resolve_image else None
    if path is None:
        return result + [Paragraph("图像证据文件不可用。", s["small"])]
    try:
        image, width, height = _mark(path, ref)
        return result + [
            Spacer(1, 0.08 * cm),
            Image(image, width=width, height=height),
            Spacer(1, 0.12 * cm),
        ]
    except (OSError, ValueError):
        return result + [Paragraph("图像证据无法渲染。", s["small"])]


def _mark(path: Path, ref: EvidenceRef) -> tuple[BytesIO, float, float]:
    image = PilImage.open(path).convert("RGB")
    if ref.region:
        draw = ImageDraw.Draw(image)
        draw.rectangle(
            (
                round(ref.region.x * image.width),
                round(ref.region.y * image.height),
                round((ref.region.x + ref.region.width) * image.width),
                round((ref.region.y + ref.region.height) * image.height),
            ),
            outline="#D92D20",
            width=max(3, round(min(image.size) / 180)),
        )
    image.thumbnail((15.5 * cm, 10.5 * cm), PilImage.Resampling.LANCZOS)
    encoded = BytesIO()
    image.save(encoded, format="PNG", optimize=True)
    encoded.seek(0)
    return encoded, image.width, image.height


def _footer(canvas: Any, doc: Any) -> None:
    canvas.saveState()
    canvas.setStrokeColor(colors.HexColor("#D9E2EC"))
    canvas.line(_MARGIN, 1.15 * cm, _W - _MARGIN, 1.15 * cm)
    canvas.setFont("STSong-Light", 8)
    canvas.setFillColor(colors.HexColor("#627D98"))
    canvas.drawString(_MARGIN, 0.75 * cm, "DocGuard · 技术文档审核报告")
    canvas.drawRightString(_W - _MARGIN, 0.75 * cm, f"第 {doc.page} 页")
    canvas.restoreState()


def _safe(value: object) -> str:
    return escape(str(value)).replace("\n", "<br/>")
