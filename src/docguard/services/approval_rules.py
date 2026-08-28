"""Markdown-backed approval-rule documents for the operator console.

Rule authors add one ``*.md`` file per review type to the configured directory.
The catalog reads files on every request because rules are small and wording
changes should become visible without restarting the web process.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from markdown_it import MarkdownIt
from markdown_it.token import Token


_RULE_ID = re.compile(r"^[a-z][a-z0-9-]*$")


@dataclass(frozen=True)
class ApprovalRuleOutlineItem:
    """A heading that can be used as a document-navigation target."""

    anchor: str
    level: int
    title: str

    def as_dict(self) -> dict[str, str | int]:
        return {"anchor": self.anchor, "level": self.level, "title": self.title}


@dataclass(frozen=True)
class ApprovalRuleDocument:
    """A safely rendered approval-rule Markdown document."""

    rule_id: str
    title: str
    description: str
    order: int
    html: str
    outline: tuple[ApprovalRuleOutlineItem, ...]

    def as_summary(self) -> dict[str, str]:
        return {
            "rule_id": self.rule_id,
            "title": self.title,
            "description": self.description,
        }

    def as_detail(self) -> dict[str, object]:
        return {
            **self.as_summary(),
            "html": self.html,
            "outline": [item.as_dict() for item in self.outline],
        }


class ApprovalRuleCatalog:
    """Discover and render independently maintained approval-rule documents.

    A document file name is its stable API identifier. For example,
    ``technical-architecture.md`` becomes ``technical-architecture``. Optional
    YAML-like front matter supports ``title``, ``description`` and ``order``.
    The small parser avoids requiring a second configuration file or a YAML
    dependency for rule authors.
    """

    def __init__(self, directory: Path | str) -> None:
        self.directory = Path(directory)
        self._markdown = MarkdownIt(
            "commonmark",
            {"html": False, "linkify": False, "typographer": False},
        ).enable("table").enable("strikethrough")

    def list(self) -> list[ApprovalRuleDocument]:
        if not self.directory.is_dir():
            return []
        documents = [
            self._read(path)
            for path in self.directory.glob("*.md")
            if _RULE_ID.fullmatch(path.stem)
        ]
        return sorted(documents, key=lambda item: (item.order, item.title, item.rule_id))

    def get(self, rule_id: str) -> ApprovalRuleDocument:
        if not _RULE_ID.fullmatch(rule_id):
            raise KeyError(rule_id)
        path = self.directory / f"{rule_id}.md"
        if not path.is_file():
            raise KeyError(rule_id)
        return self._read(path)

    def _read(self, path: Path) -> ApprovalRuleDocument:
        metadata, source = _split_front_matter(path.read_text(encoding="utf-8"))
        rule_id = path.stem
        html, outline = self._render(source, rule_id)
        title = metadata.get("title") or next(
            (item.title for item in outline if item.level == 1), rule_id.replace("-", " ").title()
        )
        return ApprovalRuleDocument(
            rule_id=rule_id,
            title=title,
            description=metadata.get("description", ""),
            order=_as_order(metadata.get("order")),
            html=html,
            outline=tuple(outline),
        )

    def _render(self, source: str, rule_id: str) -> tuple[str, list[ApprovalRuleOutlineItem]]:
        tokens = self._markdown.parse(source)
        outline: list[ApprovalRuleOutlineItem] = []
        for index, token in enumerate(tokens):
            if token.type != "heading_open" or not token.tag.startswith("h"):
                continue
            inline = tokens[index + 1] if index + 1 < len(tokens) else Token("inline", "", 0)
            title = _heading_text(inline)
            anchor = f"rule-{rule_id}-{len(outline) + 1}"
            token.attrSet("id", anchor)
            outline.append(
                ApprovalRuleOutlineItem(
                    anchor=anchor,
                    level=int(token.tag[1:]),
                    title=title or f"第 {len(outline) + 1} 节",
                )
            )
        return self._markdown.renderer.render(tokens, self._markdown.options, {}), outline


def _split_front_matter(source: str) -> tuple[dict[str, str], str]:
    lines = source.lstrip("\ufeff").splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, source
    for end_index in range(1, len(lines)):
        if lines[end_index].strip() != "---":
            continue
        metadata: dict[str, str] = {}
        for line in lines[1:end_index]:
            key, separator, value = line.partition(":")
            if not separator:
                continue
            normalized_key = key.strip().lower()
            if normalized_key in {"title", "description", "order"}:
                metadata[normalized_key] = value.strip().strip("\"'")
        return metadata, "\n".join(lines[end_index + 1 :]).lstrip()
    return {}, source


def _heading_text(token: Token) -> str:
    if token.children is None:
        return token.content.strip()
    parts = [child.content for child in token.children if child.type in {"text", "code_inline", "image"}]
    return "".join(parts).strip()


def _as_order(value: str | None) -> int:
    try:
        return int(value) if value is not None else 1000
    except ValueError:
        return 1000
