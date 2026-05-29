from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from docx import Document
from docx.document import Document as DocumentObject
from docx.table import _Cell, Table
from docx.text.paragraph import Paragraph

SUPPORTED_TEMPLATE_SUFFIX = ".docx"
SEPARATORS = ("=>", "->", "=", ":")


@dataclass(frozen=True)
class ReplacementResult:
    output_path: Path
    replacements_requested: int
    replacements_made: int
    missing_sources: tuple[str, ...]


class ReplacementParseError(ValueError):
    """Raised when a replacement message cannot be parsed."""


def find_docx_templates(template_dir: Path) -> list[Path]:
    """Return DOCX templates from a directory, excluding temporary Office files."""
    return sorted(
        path
        for path in template_dir.glob(f"*{SUPPORTED_TEMPLATE_SUFFIX}")
        if path.is_file() and not path.name.startswith("~$")
    )


def parse_replacements(text: str) -> OrderedDict[str, str]:
    """Parse user text into ordered exact-text replacements.

    Supported line formats:
    - old => new
    - old -> new
    - old = new
    - old: new

    Empty lines and comment lines beginning with # are ignored.
    """
    replacements: OrderedDict[str, str] = OrderedDict()
    bad_lines: list[str] = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        separator = next((sep for sep in SEPARATORS if sep in line), None)
        if separator is None:
            bad_lines.append(raw_line)
            continue

        source, target = line.split(separator, 1)
        source = source.strip()
        target = target.strip()
        if not source:
            bad_lines.append(raw_line)
            continue
        replacements[source] = target

    if bad_lines:
        preview = "\n".join(f"• {line}" for line in bad_lines[:5])
        raise ReplacementParseError(
            "Не смог разобрать строки замен. Используйте формат `что заменить => на что заменить`.\n"
            f"Проблемные строки:\n{preview}"
        )

    if not replacements:
        raise ReplacementParseError(
            "Я не нашёл ни одной замены. Отправьте строки в формате `что заменить => на что заменить`."
        )

    return replacements


def extract_docx_text(template_path: Path) -> str:
    """Extract searchable text from a DOCX template for AI replacement planning."""
    document = Document(template_path)
    parts = [paragraph.text for paragraph in _iter_all_paragraphs(document) if paragraph.text.strip()]
    return "\n".join(parts)


def render_docx(template_path: Path, output_path: Path, replacements: dict[str, str]) -> ReplacementResult:
    """Apply exact text replacements to a DOCX file and save a new DOCX."""
    document = Document(template_path)
    counts = {source: 0 for source in replacements}

    for paragraph in _iter_all_paragraphs(document):
        made = _replace_in_paragraph(paragraph, replacements)
        for source, count in made.items():
            counts[source] += count

    output_path.parent.mkdir(parents=True, exist_ok=True)
    document.save(output_path)

    missing = tuple(source for source, count in counts.items() if count == 0)
    return ReplacementResult(
        output_path=output_path,
        replacements_requested=len(replacements),
        replacements_made=sum(counts.values()),
        missing_sources=missing,
    )


def safe_filename(value: str, max_length: int = 80) -> str:
    """Build a filesystem-safe filename stem from user-facing text."""
    normalized = re.sub(r"[^\wа-яА-ЯёЁ.-]+", "_", value, flags=re.UNICODE).strip("._")
    return (normalized or "document")[:max_length]


def _iter_all_paragraphs(document: DocumentObject) -> Iterable[Paragraph]:
    yield from document.paragraphs
    for table in document.tables:
        yield from _iter_table_paragraphs(table)

    for section in document.sections:
        for part in (section.header, section.footer):
            yield from part.paragraphs
            for table in part.tables:
                yield from _iter_table_paragraphs(table)


def _iter_table_paragraphs(table: Table) -> Iterable[Paragraph]:
    for row in table.rows:
        for cell in row.cells:
            yield from _iter_cell_paragraphs(cell)


def _iter_cell_paragraphs(cell: _Cell) -> Iterable[Paragraph]:
    yield from cell.paragraphs
    for table in cell.tables:
        yield from _iter_table_paragraphs(table)


def _replace_in_paragraph(paragraph: Paragraph, replacements: dict[str, str]) -> dict[str, int]:
    """Replace text inside a paragraph, including text split across Word runs.

    If Word split the source phrase across multiple runs, the paragraph text is rebuilt into the
    first run. That keeps the document readable, but formatting of the replaced paragraph may use
    the first run's style for the rebuilt text.
    """
    if not paragraph.runs:
        return {source: 0 for source in replacements}

    original = paragraph.text
    updated = original
    counts: dict[str, int] = {}

    for source, target in replacements.items():
        count = updated.count(source)
        counts[source] = count
        if count:
            updated = updated.replace(source, target)

    if updated == original:
        return counts

    first_run = paragraph.runs[0]
    first_run.text = updated
    for run in paragraph.runs[1:]:
        run.text = ""
    return counts
