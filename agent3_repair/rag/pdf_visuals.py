"""Render cited source PDF pages for vision inputs and illustrated reports."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator

ROOT_DIR = Path(__file__).resolve().parent.parent


def render_source_page(
    source: dict[str, Any], *, dpi: int = 110
) -> tuple[str, int, bytes] | None:
    """Return (title, page number, JPEG bytes) for a source's cited PDF page.

    Paths are project-relative and are resolved beneath ROOT_DIR. This also
    prevents edited source JSON in the UI from asking the renderer to read an
    arbitrary local file.
    """
    document_path = str(source.get("document_path") or "").strip()
    page_number = source.get("page")
    if not document_path or not isinstance(page_number, int) or page_number < 1:
        return None

    path = (ROOT_DIR / document_path).resolve()
    try:
        path.relative_to(ROOT_DIR.resolve())
    except ValueError:
        return None
    if not path.is_file() or path.suffix.lower() != ".pdf":
        return None

    try:
        import fitz
    except ImportError as exc:
        raise RuntimeError(
            "PDF page images require PyMuPDF. Install it with: "
            "python -m pip install -r requirements-v4.txt"
        ) from exc

    with fitz.open(path) as pdf:
        if page_number > len(pdf):
            return None
        page = pdf.load_page(page_number - 1)
        pixmap = page.get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72), alpha=False)
        image_bytes = pixmap.tobytes("jpeg")
    return str(source.get("document_title") or path.name), page_number, image_bytes


def iter_source_pdf_pages(
    sources: list[dict[str, Any]], *, max_pages: int = 3, dpi: int = 110
) -> Iterator[tuple[dict[str, Any], bytes]]:
    """Render unique PDF source/page pairs, preserving retrieval order."""
    seen: set[tuple[str, int]] = set()
    rendered = 0
    for source in sources:
        page = source.get("page")
        path = str(source.get("document_path") or "")
        if not path or not isinstance(page, int):
            continue
        key = (path.lower(), page)
        if key in seen:
            continue
        seen.add(key)
        result = render_source_page(source, dpi=dpi)
        if result is None:
            continue
        _, _, image_bytes = result
        yield source, image_bytes
        rendered += 1
        if rendered >= max_pages:
            return
