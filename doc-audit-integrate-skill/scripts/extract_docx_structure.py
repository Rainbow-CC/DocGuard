#!/usr/bin/env python3
"""Extract the accepted-view DOCX structure, images, and embedded OLE previews.

The default revision mode is ``accept``: deleted and moved-from OOXML content
is excluded before text, tables, images, and OLE previews are extracted. The
source DOCX is never modified.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree as ET

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
V = "urn:schemas-microsoft-com:vml"
OFFICE = "urn:schemas-microsoft-com:office:office"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"
NS = {"w": W, "r": R, "a": A, "v": V, "o": OFFICE}

HEADING_RE = re.compile(r"^(?:第[一二三四五六七八九十百零]+[章节篇]|\d+(?:\.\d+)+)[、.．\s]*\S+")
REVISION_HEADING_RE = re.compile(r"(?:修订|修訂|版本|变更|變更).{0,4}(?:记录|記錄|历史|歷史)")
RASTER_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tif", ".tiff", ".webp"}
OFFICE_VECTOR_SUFFIXES = {".emf", ".wmf"}
DEFAULT_VECTOR_RENDER_DPI = 300
MIN_VECTOR_RENDER_LONG_EDGE = 2000
VECTOR_RENDER_PADDING_RATIO = 0.02
REMOVED_REVISION_TAGS = {f"{{{W}}}del", f"{{{W}}}moveFrom"}
REVISION_TAGS = {
    "insertions": ".//w:ins",
    "deletions": ".//w:del",
    "moves_from": ".//w:moveFrom",
    "moves_to": ".//w:moveTo",
}


def is_hidden_by_revision(element: ET.Element, revision_mode: str) -> bool:
    return revision_mode == "accept" and element.tag in REMOVED_REVISION_TAGS


def visible_descendants(element: ET.Element, tags: set[str], revision_mode: str):
    """Yield matching descendants that are visible after accepting revisions."""
    for child in element:
        if is_hidden_by_revision(child, revision_mode):
            continue
        if child.tag in tags:
            yield child
        yield from visible_descendants(child, tags, revision_mode)


def text_of(element: ET.Element, revision_mode: str) -> str:
    """Extract visible Word text without VML/OLE implementation text."""
    fragments: list[str] = []

    def visit(node: ET.Element) -> None:
        if is_hidden_by_revision(node, revision_mode):
            return
        if node.tag == f"{{{W}}}t":
            fragments.append(node.text or "")
        for child in node:
            visit(child)

    visit(element)
    return "".join(fragments).strip()


def style_of(paragraph: ET.Element) -> str | None:
    style = paragraph.find("w:pPr/w:pStyle", NS)
    return style.get(f"{{{W}}}val") if style is not None else None


def word_attr(element: ET.Element | None, name: str) -> str | None:
    """Read a WordprocessingML attribute without leaking namespace details."""
    return element.get(f"{{{W}}}{name}") if element is not None else None


def enabled(element: ET.Element | None) -> bool | None:
    """Interpret OOXML on/off values; an absent property remains unspecified."""
    if element is None:
        return None
    return word_attr(element, "val") not in {"0", "false", "off"}


def half_points(element: ET.Element | None) -> float | None:
    value = word_attr(element, "val")
    return int(value) / 2 if value and value.isdigit() else None


def color_of(element: ET.Element | None) -> str | None:
    value = word_attr(element, "val")
    return None if value in (None, "auto") else value


def parse_run_properties(properties: ET.Element | None) -> dict[str, object]:
    """Return only values explicitly declared by one rPr element.

    The caller merges this sparse mapping from document defaults through direct
    run formatting, which mirrors Word's formatting precedence.
    """
    if properties is None:
        return {}
    fonts = properties.find("w:rFonts", NS)
    result: dict[str, object] = {}
    for name, attribute in (
        ("font_ascii", "ascii"),
        ("font_h_ansi", "hAnsi"),
        ("font_east_asia", "eastAsia"),
        ("font_cs", "cs"),
        ("font_ascii_theme", "asciiTheme"),
        ("font_h_ansi_theme", "hAnsiTheme"),
        ("font_east_asia_theme", "eastAsiaTheme"),
        ("font_cs_theme", "cstheme"),
    ):
        if value := word_attr(fonts, attribute):
            result[name] = value
    for name, tag in (
        ("size_pt", "sz"),
        ("size_cs_pt", "szCs"),
    ):
        if value := half_points(properties.find(f"w:{tag}", NS)):
            result[name] = value
    for name, tag in (
        ("bold", "b"),
        ("bold_cs", "bCs"),
        ("italic", "i"),
        ("italic_cs", "iCs"),
    ):
        if (value := enabled(properties.find(f"w:{tag}", NS))) is not None:
            result[name] = value
    if value := word_attr(properties.find("w:u", NS), "val"):
        result["underline"] = value
    if value := color_of(properties.find("w:color", NS)):
        result["color"] = value
    if value := word_attr(properties.find("w:highlight", NS), "val"):
        result["highlight"] = value
    if value := word_attr(properties.find("w:vertAlign", NS), "val"):
        result["vertical_align"] = value
    return result


def parse_paragraph_properties(properties: ET.Element | None) -> dict[str, object]:
    """Extract paragraph-level layout properties that are useful in template checks."""
    if properties is None:
        return {}
    result: dict[str, object] = {}
    if value := word_attr(properties.find("w:jc", NS), "val"):
        result["alignment"] = value
    spacing = properties.find("w:spacing", NS)
    if spacing is not None:
        result["spacing"] = {
            name: value
            for name in ("before", "after", "line", "lineRule")
            if (value := word_attr(spacing, name)) is not None
        }
    indent = properties.find("w:ind", NS)
    if indent is not None:
        result["indent"] = {
            name: value
            for name in ("left", "right", "firstLine", "hanging", "leftChars", "rightChars", "firstLineChars", "hangingChars")
            if (value := word_attr(indent, name)) is not None
        }
    for name, tag in (
        ("keep_next", "keepNext"),
        ("keep_lines", "keepLines"),
        ("page_break_before", "pageBreakBefore"),
    ):
        if (value := enabled(properties.find(f"w:{tag}", NS))) is not None:
            result[name] = value
    return result


def outline_level_of(properties: ET.Element | None) -> int | None:
    """Return a one-based Word outline level, if the property explicitly declares one."""
    value = word_attr(properties.find("w:outlineLvl", NS) if properties is not None else None, "val")
    return int(value) + 1 if value and value.isdigit() and 0 <= int(value) <= 8 else None


def build_style_index(styles_xml: bytes | None) -> tuple[dict[str, dict[str, object]], dict[str, object]]:
    """Index paragraph styles and document defaults, retaining their OOXML inheritance."""
    if not styles_xml:
        return {}, {}
    root = ET.fromstring(styles_xml)
    defaults = parse_run_properties(root.find("w:docDefaults/w:rPrDefault/w:rPr", NS))
    styles: dict[str, dict[str, object]] = {}
    for item in root.findall(f"{{{W}}}style"):
        if item.get(f"{{{W}}}type") != "paragraph":
            continue
        style_id = item.get(f"{{{W}}}styleId")
        if not style_id:
            continue
        styles[style_id] = {
            "style_id": style_id,
            "based_on": word_attr(item.find("w:basedOn", NS), "val"),
            "run": parse_run_properties(item.find("w:rPr", NS)),
            "paragraph": parse_paragraph_properties(item.find("w:pPr", NS)),
            "outline_level": outline_level_of(item.find("w:pPr", NS)),
        }
    return styles, defaults


def style_chain(style_id: str | None, styles: dict[str, dict[str, object]]) -> list[dict[str, object]]:
    """Return style ancestors first, protecting extraction from malformed cycles."""
    chain: list[dict[str, object]] = []
    seen: set[str] = set()
    current = style_id
    while current and current not in seen:
        seen.add(current)
        style = styles.get(current)
        if style is None:
            break
        chain.append(style)
        based_on = style.get("based_on")
        current = based_on if isinstance(based_on, str) else None
    return list(reversed(chain))


def heading_of(paragraph: ET.Element, styles: dict[str, dict[str, object]]) -> dict[str, object] | None:
    """Identify headings only from Word's explicit outline-level semantics.

    Deliberately do not infer a heading from text numbering or style names: that
    is useful for loose navigation, but not reliable enough for formatting audit.
    """
    paragraph_properties = paragraph.find("w:pPr", NS)
    if level := outline_level_of(paragraph_properties):
        return {"level": level, "basis": "paragraph_outline_level"}
    level: int | None = None
    for style in style_chain(style_of(paragraph), styles):
        style_level = style.get("outline_level")
        if isinstance(style_level, int):
            level = style_level
    return {"level": level, "basis": "style_outline_level"} if level is not None else None


def resolved_run_format(
    paragraph: ET.Element,
    run: ET.Element,
    styles: dict[str, dict[str, object]],
    defaults: dict[str, object],
) -> tuple[dict[str, object], dict[str, str]]:
    """Resolve font attributes from defaults, style inheritance, pPr and direct rPr."""
    values = dict(defaults)
    sources = {name: "doc_default" for name in values}
    for style in style_chain(style_of(paragraph), styles):
        for name, value in style["run"].items():
            values[name] = value
            sources[name] = f"style:{style['style_id']}"
    paragraph_properties = paragraph.find("w:pPr", NS)
    for name, value in parse_run_properties(paragraph_properties.find("w:rPr", NS) if paragraph_properties is not None else None).items():
        values[name] = value
        sources[name] = "paragraph_direct"
    for name, value in parse_run_properties(run.find("w:rPr", NS)).items():
        values[name] = value
        sources[name] = "direct"
    return values, sources


def visible_runs(paragraph: ET.Element, revision_mode: str):
    """Yield text runs in document order, excluding accepted-away revisions."""
    if is_hidden_by_revision(paragraph, revision_mode):
        return
    if paragraph.tag == f"{{{W}}}r":
        yield paragraph
        return
    for child in paragraph:
        yield from visible_runs(child, revision_mode)


def format_paragraph(
    paragraph: ET.Element,
    styles: dict[str, dict[str, object]],
    defaults: dict[str, object],
    revision_mode: str,
) -> dict[str, object]:
    style_id = style_of(paragraph)
    paragraph_values: dict[str, object] = {}
    for style in style_chain(style_id, styles):
        paragraph_values.update(style["paragraph"])
    paragraph_values.update(parse_paragraph_properties(paragraph.find("w:pPr", NS)))
    runs: list[dict[str, object]] = []
    cursor = 0
    for run in visible_runs(paragraph, revision_mode):
        text = text_of(run, revision_mode)
        if not text:
            continue
        font, source = resolved_run_format(paragraph, run, styles, defaults)
        if runs and runs[-1]["end"] == cursor and runs[-1]["font"] == font and runs[-1]["source"] == source:
            runs[-1]["text"] += text
            runs[-1]["end"] = cursor + len(text)
        else:
            runs.append({"text": text, "start": cursor, "end": cursor + len(text), "font": font, "source": source})
        cursor += len(text)
    return {"style_id": style_id, "heading": heading_of(paragraph, styles), "paragraph": paragraph_values, "runs": runs}


def format_table(
    table: ET.Element,
    styles: dict[str, dict[str, object]],
    defaults: dict[str, object],
    revision_mode: str,
) -> list[list[dict[str, object]]]:
    formatted_rows: list[list[dict[str, object]]] = []
    for row in table.findall("w:tr", NS):
        if revision_mode == "accept" and (
            row.find("./w:trPr/w:del", NS) is not None or row.find("./w:trPr/w:moveFrom", NS) is not None
        ):
            continue
        formatted_rows.append([
            {"paragraphs": [format_paragraph(paragraph, styles, defaults, revision_mode) for paragraph in cell.findall("./w:p", NS)]}
            for cell in row.findall("w:tc", NS)
        ])
    return formatted_rows


def style_levels(styles_xml: bytes | None) -> dict[str, int]:
    if not styles_xml:
        return {}
    root = ET.fromstring(styles_xml)
    levels: dict[str, int] = {}
    for item in root.findall(f"{{{W}}}style"):
        if item.get(f"{{{W}}}type") != "paragraph":
            continue
        style_id = item.get(f"{{{W}}}styleId")
        outline = item.find(f"./{{{W}}}pPr/{{{W}}}outlineLvl")
        if style_id and outline is not None and outline.get(f"{{{W}}}val", "").isdigit():
            levels[style_id] = int(outline.get(f"{{{W}}}val")) + 1
    return levels


def level_of(style: str | None, text: str, levels: dict[str, int]) -> int | None:
    if style in levels:
        return levels[style]
    match = re.search(r"(?:heading|标题)\s*([1-9])", style or "", re.I)
    return int(match.group(1)) if match else (1 if HEADING_RE.match(text) else None)


def table_rows(table: ET.Element, revision_mode: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for row in table.findall("w:tr", NS):
        if revision_mode == "accept" and (
            row.find("./w:trPr/w:del", NS) is not None or row.find("./w:trPr/w:moveFrom", NS) is not None
        ):
            continue
        rows.append([text_of(cell, revision_mode) for cell in row.findall("w:tc", NS)])
    return rows


def revision_summary(root: ET.Element, revision_mode: str) -> dict[str, int | str]:
    return {
        "mode": revision_mode,
        **{name: len(root.findall(path, NS)) for name, path in REVISION_TAGS.items()},
    }


def package_path(target: str) -> str:
    path = PurePosixPath("word") / PurePosixPath(target.replace("\\", "/"))
    normal = PurePosixPath("/")
    for part in path.parts:
        if part in ("", "/", "."):
            continue
        if part == "..":
            normal = normal.parent
        else:
            normal /= part
    result = str(normal).lstrip("/")
    if not result.startswith("word/"):
        raise ValueError(f"Unsafe relationship target: {target}")
    return result


def content_type(name: str) -> str:
    return {".emf": "image/emf", ".wmf": "image/wmf"}.get(
        Path(name).suffix.lower(), mimetypes.guess_type(name)[0] or "application/octet-stream"
    )


def copy_part(archive: zipfile.ZipFile, package: str, output_dir: Path) -> tuple[Path, bytes]:
    data = archive.read(package)
    destination = output_dir / Path(package).name
    if not destination.exists():
        destination.write_bytes(data)
    return destination, data


def crop_vector_canvas(image, min_long_edge: int = MIN_VECTOR_RENDER_LONG_EDGE):
    """Remove the white office page around a vector preview and preserve a safety margin.

    LibreOffice Draw places standalone EMF/WMF files on a default A4 page.  Passing
    that page directly to a vision model makes the actual diagram unnecessarily
    small.  A small threshold retains anti-aliased edges while ignoring an otherwise
    white page background.
    """
    from PIL import Image, ImageChops

    rgb = image.convert("RGB")
    difference = ImageChops.difference(rgb, Image.new("RGB", rgb.size, "white"))
    # Ignore near-white anti-aliasing/compression noise but retain all diagram ink.
    mask = difference.point(lambda value: 255 if value > 16 else 0)
    bounds = mask.getbbox()
    if bounds is None:
        return rgb

    left, top, right, bottom = bounds
    padding = max(12, round(max(rgb.size) * VECTOR_RENDER_PADDING_RATIO))
    cropped = rgb.crop((
        max(0, left - padding),
        max(0, top - padding),
        min(rgb.width, right + padding),
        min(rgb.height, bottom + padding),
    ))
    long_edge = max(cropped.size)
    if long_edge >= min_long_edge:
        return cropped
    scale = min_long_edge / long_edge
    return cropped.resize(
        (round(cropped.width * scale), round(cropped.height * scale)),
        Image.Resampling.LANCZOS,
    )


def convert_vector_to_pdf(source: Path, destination: Path, soffice: str) -> tuple[bool, str]:
    """Convert a standalone EMF/WMF preview to PDF through LibreOffice Draw."""
    with tempfile.TemporaryDirectory(prefix="docx-render-profile-") as profile:
        command = [
            soffice, "--headless", "--norestore", "--nolockcheck", "--nodefault", "--nofirststartwizard",
            f"-env:UserInstallation=file://{Path(profile).as_posix()}",
            "--convert-to", "pdf:draw_pdf_Export", "--outdir", str(destination.parent), str(source),
        ]
        try:
            completed = subprocess.run(command, text=True, capture_output=True, timeout=90, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return False, str(exc)
    if completed.returncode == 0 and destination.exists():
        return True, ""
    return False, (completed.stderr or completed.stdout).strip().replace("\n", " ")


def rasterize_pdf(pdf: Path, target: Path, pdftoppm: str, dpi: int) -> tuple[bool, str]:
    """Render the first PDF page at a controlled DPI using Poppler."""
    try:
        completed = subprocess.run(
            [pdftoppm, "-f", "1", "-l", "1", "-r", str(dpi), "-png", "-singlefile", str(pdf), str(target.with_suffix(""))],
            text=True,
            capture_output=True,
            timeout=90,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    if completed.returncode == 0 and target.exists():
        return True, ""
    return False, (completed.stderr or completed.stdout).strip().replace("\n", " ")


def fallback_vector_png(source: Path, target: Path, soffice: str) -> tuple[bool, str]:
    """Use LibreOffice's PNG export only when the high-resolution path is unavailable."""
    with tempfile.TemporaryDirectory(prefix="docx-render-profile-") as profile:
        command = [
            soffice, "--headless", "--norestore", "--nolockcheck", "--nodefault", "--nofirststartwizard",
            f"-env:UserInstallation=file://{Path(profile).as_posix()}",
            "--convert-to", "png", "--outdir", str(target.parent), str(source),
        ]
        try:
            completed = subprocess.run(command, text=True, capture_output=True, timeout=90, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return False, str(exc)
    generated = target.parent / f"{source.stem}.png"
    if completed.returncode == 0 and generated.exists():
        if generated != target:
            shutil.move(str(generated), target)
        return True, ""
    return False, (completed.stderr or completed.stdout).strip().replace("\n", " ")


def render_png(media: list[dict], output: Path, soffice: str, pdftoppm: str, vector_dpi: int, warnings: list[str]) -> None:
    try:
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError("--render-png requires Pillow. Install python3-pil or pip install Pillow.") from exc

    render_dir = output / "rendered"
    render_dir.mkdir(exist_ok=True)
    for item in media:
        if item["kind"] != "image":
            continue
        source = output / item["file"]
        suffix = source.suffix.lower()
        target = render_dir / f"{item['id']}.png"
        if suffix in RASTER_SUFFIXES:
            try:
                with Image.open(source) as image:
                    if image.mode not in ("RGB", "RGBA"):
                        image = image.convert("RGBA" if "transparency" in image.info else "RGB")
                    image.save(target, "PNG")
                item["rendered_png_file"] = str(target.relative_to(output)).replace("\\", "/")
            except Exception as exc:
                warnings.append(f"{item['id']}: Pillow could not render {source.name}: {exc}")
        elif suffix in OFFICE_VECTOR_SUFFIXES:
            with tempfile.TemporaryDirectory(prefix="docx-vector-render-") as temp_dir:
                temporary = Path(temp_dir)
                pdf = temporary / f"{source.stem}.pdf"
                rendered = temporary / "rendered.png"
                pdf_ok, pdf_detail = convert_vector_to_pdf(source, pdf, soffice)
                png_ok, png_detail = rasterize_pdf(pdf, rendered, pdftoppm, vector_dpi) if pdf_ok else (False, "PDF conversion failed")
                if pdf_ok and png_ok:
                    with Image.open(rendered) as image:
                        crop_vector_canvas(image).save(target, "PNG")
                    item["rendered_png_file"] = str(target.relative_to(output)).replace("\\", "/")
                    continue

            # Keep extraction available on a minimal server, but still crop the
            # default A4 page so the fallback gives the vision model the largest
            # possible view of the diagram.
            fallback_ok, fallback_detail = fallback_vector_png(source, target, soffice)
            if fallback_ok:
                with Image.open(target) as image:
                    crop_vector_canvas(image).save(target, "PNG")
                item["rendered_png_file"] = str(target.relative_to(output)).replace("\\", "/")
                warnings.append(
                    f"{item['id']}: rendered {source.name} through the low-resolution PNG fallback; "
                    f"PDF path unavailable ({pdf_detail or png_detail})."
                )
            else:
                warnings.append(
                    f"{item['id']}: could not render {source.name}; PDF path ({pdf_detail or png_detail}); "
                    f"PNG fallback ({fallback_detail})."
                )
        else:
            warnings.append(f"{item['id']}: unsupported image format for PNG rendering: {source.name}")


def extract(
    docx: Path,
    output: Path,
    render: bool = False,
    soffice: str = "soffice",
    revision_mode: str = "accept",
    pdftoppm: str = "pdftoppm",
    vector_dpi: int = DEFAULT_VECTOR_RENDER_DPI,
) -> dict:
    if docx.suffix.lower() != ".docx":
        raise ValueError("Expected a .docx file, not a legacy .doc file.")
    output.mkdir(parents=True, exist_ok=True)
    media_dir = output / "media"
    media_dir.mkdir(exist_ok=True)
    embedded_dir = output / "embeddings"
    embedded_dir.mkdir(exist_ok=True)

    with zipfile.ZipFile(docx) as archive:
        if "word/document.xml" not in archive.namelist():
            raise ValueError("Missing word/document.xml.")
        document_root = ET.fromstring(archive.read("word/document.xml"))
        body = document_root.find("w:body", NS)
        revisions = revision_summary(document_root, revision_mode)
        relationships: dict[str, str] = {}
        if "word/_rels/document.xml.rels" in archive.namelist():
            rel_root = ET.fromstring(archive.read("word/_rels/document.xml.rels"))
            relationships = {item.get("Id"): item.get("Target") for item in rel_root.findall(f"{{{REL}}}Relationship")}
        styles_xml = archive.read("word/styles.xml") if "word/styles.xml" in archive.namelist() else None
        levels = style_levels(styles_xml)
        styles, default_run_format = build_style_index(styles_xml)
        blocks: list[dict] = []
        formatting_blocks: list[dict] = []
        chapters: list[dict] = []
        media_by_id: dict[str, dict] = {}
        warnings: list[str] = []
        stack: list[dict] = []

        def register_image(rid: str, block: dict) -> str | None:
            target = relationships.get(rid)
            if not target:
                warnings.append(f"Block {block['index']}: unavailable image relationship {rid}.")
                return None
            try:
                package = package_path(target)
            except ValueError as exc:
                warnings.append(f"Block {block['index']}: {exc}")
                return None
            if package not in archive.namelist():
                warnings.append(f"Block {block['index']}: image target is absent: {package}.")
                return None
            destination, data = copy_part(archive, package, media_dir)
            image_id = "image-" + hashlib.sha256(data).hexdigest()[:16]
            item = media_by_id.setdefault(image_id, {
                "id": image_id, "kind": "image", "file": f"media/{destination.name}",
                "content_type": content_type(destination.name), "sha256": hashlib.sha256(data).hexdigest(),
                "source_blocks": [], "chapter_ids": [],
            })
            if block["index"] not in item["source_blocks"]:
                item["source_blocks"].append(block["index"])
            if block["chapter_id"] and block["chapter_id"] not in item["chapter_ids"]:
                item["chapter_ids"].append(block["chapter_id"])
            return image_id

        def register_ole(rid: str, object_element: ET.Element, block: dict, preview_ids: list[str]) -> str | None:
            target = relationships.get(rid)
            if not target:
                warnings.append(f"Block {block['index']}: unavailable OLE relationship {rid}.")
                return None
            try:
                package = package_path(target)
            except ValueError as exc:
                warnings.append(f"Block {block['index']}: {exc}")
                return None
            if package not in archive.namelist():
                warnings.append(f"Block {block['index']}: OLE target is absent: {package}.")
                return None
            destination, data = copy_part(archive, package, embedded_dir)
            object_id = "ole-" + hashlib.sha256(data).hexdigest()[:16]
            item = media_by_id.setdefault(object_id, {
                "id": object_id, "kind": "embedded_object", "file": f"embeddings/{destination.name}",
                "content_type": "application/octet-stream", "sha256": hashlib.sha256(data).hexdigest(),
                "source_blocks": [], "chapter_ids": [], "prog_id": object_element.get("ProgID"),
                "class_id": object_element.get("Type"), "preview_image_ids": [],
            })
            if block["index"] not in item["source_blocks"]:
                item["source_blocks"].append(block["index"])
            if block["chapter_id"] and block["chapter_id"] not in item["chapter_ids"]:
                item["chapter_ids"].append(block["chapter_id"])
            for preview_id in preview_ids:
                if preview_id not in item["preview_image_ids"]:
                    item["preview_image_ids"].append(preview_id)
            return object_id

        image_tags = {f"{{{A}}}blip", f"{{{V}}}imagedata"}
        object_tag = f"{{{W}}}object"
        ole_tag = f"{{{OFFICE}}}OLEObject"
        for index, child in enumerate(list(body) if body is not None else []):
            tag = child.tag.rsplit("}", 1)[-1]
            if tag == "p":
                content = text_of(child, revision_mode)
                style = style_of(child)
                heading_level = level_of(style, content, levels)
                level = 1 if heading_level == 1 and not REVISION_HEADING_RE.search(content) else None
                if level and content:
                    while stack and stack[-1]["level"] >= level:
                        stack.pop()
                    chapter = {
                        "id": f"chapter-{len(chapters) + 1}", "level": level, "title": content,
                        "block_start": index, "block_end": None, "parent_id": stack[-1]["id"] if stack else None,
                    }
                    chapters.append(chapter)
                    stack.append(chapter)
                block = {"index": index, "type": "paragraph", "text": content, "style": style, "chapter_id": stack[-1]["id"] if stack else None}
                image_ids: list[str] = []
                for image in visible_descendants(child, image_tags, revision_mode):
                    rid = image.get(f"{{{R}}}embed") or image.get(f"{{{R}}}id")
                    if rid and (image_id := register_image(rid, block)) and image_id not in image_ids:
                        image_ids.append(image_id)
                if image_ids:
                    block["image_ids"] = image_ids
                ole_ids: list[str] = []
                for object_node in visible_descendants(child, {object_tag}, revision_mode):
                    object_preview_ids: list[str] = []
                    for preview in visible_descendants(object_node, {f"{{{V}}}imagedata"}, revision_mode):
                        preview_rid = preview.get(f"{{{R}}}id")
                        if preview_rid and (preview_id := register_image(preview_rid, block)) and preview_id not in object_preview_ids:
                            object_preview_ids.append(preview_id)
                    for ole in visible_descendants(object_node, {ole_tag}, revision_mode):
                        ole_rid = ole.get(f"{{{R}}}id")
                        if ole_rid and (object_id := register_ole(ole_rid, ole, block, object_preview_ids)):
                            ole_ids.append(object_id)
                if ole_ids:
                    block["embedded_object_ids"] = ole_ids
                blocks.append(block)
                formatting_blocks.append({
                    "block_index": index,
                    "type": "paragraph",
                    **format_paragraph(child, styles, default_run_format, revision_mode),
                })
            elif tag == "tbl":
                blocks.append({"index": index, "type": "table", "rows": table_rows(child, revision_mode), "chapter_id": stack[-1]["id"] if stack else None})
                formatting_blocks.append({
                    "block_index": index,
                    "type": "table",
                    "cells": format_table(child, styles, default_run_format, revision_mode),
                })
        for position, chapter in enumerate(chapters):
            chapter["block_end"] = chapters[position + 1]["block_start"] - 1 if position + 1 < len(chapters) else len(blocks) - 1
        media = list(media_by_id.values())
        if render:
            render_png(media, output, soffice, pdftoppm, vector_dpi, warnings)
        return {
            "source": str(docx.resolve()),
            "revisions": revisions,
            "blocks": blocks,
            "chapters": chapters,
            "media": media,
            "warnings": warnings,
            "_formatting": {
                "schema_version": "1.0",
                "source": str(docx.resolve()),
                "revisions": revisions,
                "blocks": formatting_blocks,
            },
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("docx", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--render-png", action="store_true", help="Create vision-ready PNG files; EMF/WMF are cropped and rendered at high resolution.")
    parser.add_argument("--soffice", default="soffice", help="LibreOffice executable (default: soffice).")
    parser.add_argument("--pdftoppm", default="pdftoppm", help="Poppler pdftoppm executable for high-resolution vector rendering (default: pdftoppm).")
    parser.add_argument("--vector-dpi", type=int, default=DEFAULT_VECTOR_RENDER_DPI, help=f"DPI for EMF/WMF rendering (default: {DEFAULT_VECTOR_RENDER_DPI}).")
    parser.add_argument("--revision-mode", choices=("accept", "all"), default="accept", help="Use accepted revisions (default) or include all revision content.")
    args = parser.parse_args()
    try:
        if args.vector_dpi <= 0:
            raise ValueError("--vector-dpi must be greater than zero.")
        result = extract(
            args.docx,
            args.output,
            args.render_png,
            args.soffice,
            revision_mode=args.revision_mode,
            pdftoppm=args.pdftoppm,
            vector_dpi=args.vector_dpi,
        )
    except (ValueError, RuntimeError, zipfile.BadZipFile, ET.ParseError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    formatting = result.pop("_formatting")
    output = args.output / "document-structure.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    formatting_output = args.output / "block-formatting.json"
    formatting_output.write_text(json.dumps(formatting, ensure_ascii=False, indent=2), encoding="utf-8")
    print(output)
    print(formatting_output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
