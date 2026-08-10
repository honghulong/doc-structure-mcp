# -*- coding: utf-8 -*-
"""
Build coordinate-faithful DOCX restore previews from a PDF/image or /structure JSON.

This is the DOCX companion to restore_structure_preview.py. It intentionally
keeps the output simple and inspectable: OCR text is placed into a coarse page
grid according to its original bbox coordinates.

Important boundary:
- DOCX generation uses python-docx through docx_builder.build_honest_docx.
- LibreOffice is not part of this generation path. It may be used later only
  for optional rendering/visual verification.
- The public service does OCR/structure analysis only; this script owns DOCX
  reconstruction and output naming.

Examples:
    python scripts/restore_structure_docx.py ^
      --input tests/fixtures/test_page2.pdf ^
      --save-json

    python scripts/restore_structure_docx.py ^
      --json tests/testContent/public_gpu_structure_test_page2.json ^
      --out tests/testContent/restore_docx_preview/public_gpu_structure_test_page2.docx
"""
from __future__ import annotations

import argparse
import io
import json
import os
import tempfile
import sys
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Inches, Pt
from docx.enum.table import WD_ROW_HEIGHT_RULE

from docx_builder import build_honest_docx
from layout_preview import build_plain_docx as build_plain_docx_from_layout

# Default public GPU OCR service verified in docs/GPU服务器实施记录.md.
# Keep this overrideable with --url/GPU_OCR_PUBLIC_URL because the public
# mapping is an environment detail, not a document reconstruction rule.
DEFAULT_STRUCTURE_URL = "http://120.234.136.122:18769/structure"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", help="Single PDF/image file to send to public /structure service")
    parser.add_argument(
        "--url",
        default=os.environ.get("GPU_OCR_PUBLIC_URL", DEFAULT_STRUCTURE_URL),
        help="Public /structure service URL",
    )
    parser.add_argument("--json", help="Single OCR/structure JSON file")
    parser.add_argument("--out", help="Output DOCX path; defaults to input/json path with .docx suffix")
    parser.add_argument("--save-json", action="store_true", help="Save service JSON beside the DOCX")
    parser.add_argument("--table-only", action="store_true", help="Restore only table geometry; do not place OCR text")
    parser.add_argument("--dpi", type=int, default=220)
    parser.add_argument("--grid-cols", type=int, default=36)
    parser.add_argument("--grid-rows", type=int, default=48)
    args = parser.parse_args()

    payload, source_path = load_payload(args)
    out_path = output_path(args, source_path)
    pages = extract_docx_pages(payload)
    if not pages:
        print("No usable OCR lines found.")
        return 2

    out_path.parent.mkdir(parents=True, exist_ok=True)
    if args.table_only:
        docx_bytes = build_table_only_docx(payload)
        if not docx_bytes:
            print("No usable table geometry found.")
            return 2
    else:
        docx_source = Path(payload.get("_restore_docx_source_path", source_path)) if args.input else None
        docx_bytes = build_best_docx(payload, pages, grid_cols=args.grid_cols, grid_rows=args.grid_rows, source_path=docx_source)
    out_path.write_bytes(docx_bytes)
    if args.save_json and args.input:
        json_path = out_path.with_suffix(".structure.json")
        json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json_path)
    print(out_path)
    return 0


def load_payload(args) -> tuple[dict, Path]:
    if bool(args.input) == bool(args.json):
        raise SystemExit("Use exactly one of --input or --json.")

    if args.json:
        json_path = Path(args.json).resolve()
        return json.loads(json_path.read_text(encoding="utf-8")), json_path

    input_path = Path(args.input).resolve()
    if input_path.suffix.lower() not in {".pdf", ".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}:
        raise SystemExit(f"Only PDF/image input is supported here: {input_path}")
    if not args.url:
        raise SystemExit("Missing --url or GPU_OCR_PUBLIC_URL.")
    service_path = input_path
    payload = call_structure_service(args.url, service_path, args.dpi)
    rotated_path = rotated_input_for_retry(input_path)
    if rotated_path and _should_try_rotated_payload(payload):
        rotated_payload = call_structure_service(args.url, rotated_path, args.dpi)
        if _rotated_payload_is_better(payload, rotated_payload):
            service_path = rotated_path
            payload = rotated_payload
            payload["_restore_docx_rotated"] = True
    payload["_restore_docx_source_path"] = str(service_path)
    return payload, input_path


def rotated_input_for_retry(input_path: Path) -> Path | None:
    """Create a rotated retry image for portrait photos.

    The caller first OCRs the original file and only uses this retry image when
    the result looks like a sideways page. That avoids rotating normal portrait
    documents such as scanned notices.
    """
    if input_path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}:
        return None
    try:
        from PIL import Image

        image = Image.open(input_path)
        if image.width >= image.height:
            return None
        rotated = image.rotate(-90, expand=True)
        temp_dir = Path(tempfile.gettempdir())
        out_path = temp_dir / f"restore_docx_rotated_{input_path.stem}.png"
        rotated.save(out_path)
        return out_path
    except Exception:
        return None


def _looks_sideways_ocr(payload: dict) -> bool:
    """Detect a page that OCR interpreted mostly as tall vertical text blocks."""
    pages = payload.get("pages", []) if isinstance(payload, dict) else []
    if not pages:
        return False
    page = pages[0]
    raw = _raw_structure_res(page)
    labels = [box.get("label") for box in raw.get("layout_det_res", {}).get("boxes", []) if isinstance(box, dict)]
    if "table" in labels or "image" in labels:
        return False
    lines = _plain_line_items(page)
    if len(lines) < 8:
        return False
    vertical = sum(1 for line in lines if _looks_vertical_line(line))
    return vertical / len(lines) >= 0.45


def _should_try_rotated_payload(payload: dict) -> bool:
    """Return True when a portrait-image OCR result deserves a rotated retry."""
    pages = payload.get("pages", []) if isinstance(payload, dict) else []
    if not pages:
        return False
    page = pages[0]
    if float(page.get("width") or 1) >= float(page.get("height") or 1):
        return False
    raw = _raw_structure_res(page)
    if _layout_label_count(raw, "table"):
        return False
    # Sideways table photos often produce many readable text lines but no
    # layout table. Try the rotated candidate and keep it only if structure
    # improves.
    return _looks_sideways_ocr(payload) or len(_plain_line_items(page)) >= 20


def _rotated_payload_is_better(original: dict, rotated: dict) -> bool:
    original_raw = _raw_structure_res((original.get("pages") or [{}])[0])
    rotated_raw = _raw_structure_res((rotated.get("pages") or [{}])[0])
    original_tables = _layout_label_count(original_raw, "table")
    rotated_tables = _layout_label_count(rotated_raw, "table")
    if rotated_tables > original_tables:
        return True
    return False


def _layout_label_count(raw: dict, label: str) -> int:
    return sum(
        1
        for box in raw.get("layout_det_res", {}).get("boxes", []) if isinstance(box, dict)
        if box.get("label") == label
    )


def call_structure_service(url: str, input_path: Path, dpi: int) -> dict:
    import requests

    # Accept both a service root and the concrete /structure endpoint to make
    # ad-hoc command-line use less brittle.
    endpoint = url.rstrip("/")
    if not endpoint.endswith("/structure"):
        endpoint = f"{endpoint}/structure"
    with input_path.open("rb") as handle:
        response = requests.post(
            endpoint,
            files={"file": (input_path.name, handle, content_type_for(input_path))},
            # Match the known-good curl call: one rendered page, 220 dpi by
            # default, with region/cell restore data included when available.
            data={"dpi": str(dpi), "max_pages": "1", "cells": "1", "restore": "1"},
            timeout=180,
        )
    response.raise_for_status()
    payload = response.json()
    if payload.get("status") != "ok":
        raise RuntimeError(payload.get("error") or f"service returned status={payload.get('status')}")
    return payload


def content_type_for(path: Path) -> str:
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".bmp": "image/bmp",
        ".tif": "image/tiff",
        ".tiff": "image/tiff",
    }.get(path.suffix.lower(), "application/octet-stream")


def output_path(args, source_path: Path) -> Path:
    if args.out:
        return Path(args.out).resolve()
    return source_path.with_suffix(".docx")


def build_best_docx(payload: dict, pages: list[dict], grid_cols: int, grid_rows: int, source_path: Path | None = None) -> bytes:
    """Use the least lossy DOCX route that fits the detected structure.

    The important guardrail is not to force every document into a coordinate
    table. A dense table/form page benefits from visible Word table geometry,
    but letters, notices, scanned prose, and photos are usually closer to the
    source when emitted as OCR rows. This mirrors the Hermes script's general
    strategy: use structure when it is clearly table-like, otherwise preserve
    reading order as paragraphs.

    Route order:
    1. region geometry, only when restore regions overlap a detected table box.
    2. pred_html semantic tables, when PPStructure supplied table HTML.
    3. plain OCR rows, for general documents.
    4. coordinate grid, only if there are no usable plain OCR lines.
    """
    data = build_region_geometry_docx(payload, source_path=source_path)
    if data:
        return data
    data = build_semantic_docx(payload)
    if data:
        return data
    data = build_plain_docx_with_image_blocks(payload, source_path)
    if data:
        return data
    data = build_plain_docx_from_layout(_payload_with_plain_lines(payload))
    if _plain_payload_has_lines(payload):
        return data
    return build_honest_docx(pages, grid_cols=grid_cols, grid_rows=grid_rows)


def build_plain_docx_with_image_blocks(payload: dict, source_path: Path | None) -> bytes | None:
    """Build a plain DOCX, preserving complex top image blocks as pictures.

    The OCR service returns coordinates for layout images, not the cropped
    image bytes. When the original input is available locally, we crop images
    directly or render the first PDF page before cropping. This path is only
    reached after table/form reconstruction and pred_html have both declined,
    so table-heavy documents keep their earlier, editable routes.
    """
    if not source_path or source_path.suffix.lower() not in {".pdf", ".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}:
        return None
    pages = payload.get("pages", []) if isinstance(payload, dict) else []
    if not pages:
        return None
    doc = Document()
    _set_default_font(doc)
    made_content = False
    for page_index, page in enumerate(pages):
        if page_index:
            doc.add_page_break()
        _set_page_orientation(doc, page.get("width"), page.get("height"))
        blocks = _top_complex_image_blocks(page)
        if not blocks:
            continue
        for block in blocks:
            if _add_cropped_image_block(doc, source_path, block["rect"], page):
                made_content = True
        filtered = _plain_lines_outside_rects(page, [block["rect"] for block in blocks])
        for row in _cluster_plain_docx_rows(filtered):
            _add_paragraph(doc, "  ".join(item["text"] for item in row))
            made_content = True

    if not made_content:
        return None
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def build_table_only_docx(payload: dict) -> bytes | None:
    """Build a blank DOCX table from detected table geometry only.

    This diagnostic mode intentionally ignores all OCR text. Its only job is
    to answer: can the JSON table/cell coordinates reproduce the source grid?

    Important: table-only prefers large restore regions. PPStructure
    cell_box_list is only a fallback because those boxes often describe
    detected content/cell candidates rather than real source-document lines.
    """
    pages = payload.get("pages", []) if isinstance(payload, dict) else []
    if not pages:
        return None
    doc = Document()
    _set_default_font(doc)
    made_table = False
    for page_index, page in enumerate(pages):
        boxes = _table_region_boxes(page)
        if not boxes:
            boxes = _table_cell_boxes(page)
        if not boxes:
            continue
        if page_index:
            doc.add_page_break()
        _set_page_orientation(doc, page.get("width"), page.get("height"))
        _add_blank_geometry_table(doc, boxes)
        made_table = True
    if not made_table:
        return None
    import io

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _table_cell_boxes(page: dict) -> list[list[float]]:
    tables = page.get("structure", {}).get("summary", {}).get("tables", [])
    boxes = []
    for table in tables:
        for box in table.get("cell_boxes", []):
            rect = box_to_rect(box)
            if rect:
                boxes.append(rect)
    return boxes


def _table_region_boxes(page: dict) -> list[list[float]]:
    """Return large detected table regions, not OCR/cell candidate boxes.

    These boxes represent large form/table areas. They are the right source for
    a blank table-geometry check. The finer cell_box_list is deliberately only
    a fallback because it describes detected cell/text candidates and creates a
    fragmented grid if used as table lines.
    """
    restore_regions = [region["rect"] for region in _restore_regions(page)]
    if restore_regions:
        return restore_regions

    regions = page.get("structure", {}).get("summary", {}).get("regions", [])
    boxes = []
    for region in regions:
        rect = box_to_rect(region.get("coordinate") or region.get("rect"))
        if rect and _looks_like_table_body_region(rect, page):
            boxes.append(rect)
    return boxes


def _looks_like_table_body_region(rect: list[float], page: dict) -> bool:
    """Keep broad body regions and skip tiny decorative/header detections."""
    page_height = float(page.get("height") or 0) or max(rect[3], 1.0)
    width = max(1.0, rect[2] - rect[0])
    height = max(1.0, rect[3] - rect[1])
    return width >= 30 and height >= 12 and rect[1] >= page_height * 0.12 and rect[3] <= page_height * 0.97


def _add_blank_geometry_table(doc, boxes: list[list[float]]) -> None:
    x_edges = _cluster_edges([value for box in boxes for value in (box[0], box[2])], tolerance=18.0)
    y_edges = _cluster_edges([value for box in boxes for value in (box[1], box[3])], tolerance=18.0)
    if len(x_edges) < 2 or len(y_edges) < 2:
        return
    table = doc.add_table(rows=len(y_edges) - 1, cols=len(x_edges) - 1)
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    _apply_geometry_sizes(doc, table, x_edges, y_edges)

    occupied = set()
    for box in sorted(boxes, key=lambda value: (value[1], value[0])):
        start_col, end_col = _edge_span(box[0], box[2], x_edges)
        start_row, end_row = _edge_span(box[1], box[3], y_edges)
        if (start_row, start_col) in occupied:
            continue
        cell = table.cell(start_row, start_col)
        if end_row > start_row or end_col > start_col:
            cell = cell.merge(table.cell(end_row, end_col))
        for row in range(start_row, end_row + 1):
            for col in range(start_col, end_col + 1):
                occupied.add((row, col))


def _apply_geometry_sizes(doc, table, x_edges: list[float], y_edges: list[float]) -> None:
    usable_width = doc.sections[-1].page_width - doc.sections[-1].left_margin - doc.sections[-1].right_margin
    source_width = max(1.0, x_edges[-1] - x_edges[0])
    for col_index, column in enumerate(table.columns):
        width = int(usable_width * (x_edges[col_index + 1] - x_edges[col_index]) / source_width)
        for cell in column.cells:
            cell.width = width

    source_height = max(1.0, y_edges[-1] - y_edges[0])
    # Use about 60% of page height for the detected table area; Word
    # cannot express absolute page-coordinate placement without text boxes, so
    # proportional row heights are the useful part for this table-only check.
    usable_height = doc.sections[-1].page_height - doc.sections[-1].top_margin - doc.sections[-1].bottom_margin
    target_height = int(usable_height * 0.62)
    for row_index, row in enumerate(table.rows):
        row.height_rule = WD_ROW_HEIGHT_RULE.EXACTLY
        row.height = int(target_height * (y_edges[row_index + 1] - y_edges[row_index]) / source_height)


def build_region_geometry_docx(payload: dict, source_path: Path | None = None) -> bytes | None:
    """Build a DOCX table from /structure restore.region_restore coordinates.

    This path is only used when the OCR result contains a credible table/form
    body. The outer Word table is visible and uses the region rectangles as
    merged cells. Dense text groups can use borderless nested tables for
    alignment; those helper tables are not meant to create visible source lines.

    Outside-table text is emitted as ordinary paragraphs with approximate
    indentation. This avoids Word's editable table-grid overlays, which remain
    visible in some clients even when borders are hidden.
    """
    pages = payload.get("pages", []) if isinstance(payload, dict) else []
    if not pages:
        return None
    doc = Document()
    _set_default_font(doc)
    made_content = False

    for page_index, page in enumerate(pages):
        regions = _restore_regions(page)
        raw = _raw_structure_res(page)
        cell_boxes = _table_cell_boxes(page)
        # Cell boxes are only a high-confidence route when restore regions are
        # dense enough to stabilize the grid. If they are not, still try the
        # region geometry below: HTML restore already proves region/items keep
        # the trustworthy source-page coordinates for forms such as invoices
        # and incoming-document registration sheets.
        if len(cell_boxes) >= 8 and _should_use_cell_box_table(page, cell_boxes):
            if page_index:
                doc.add_page_break()
            _set_page_orientation(doc, page.get("width"), page.get("height"))
            table_rect = _main_table_rect(raw, regions) or _bounding_rect(cell_boxes)
            before, after = _outside_table_paragraphs_by_position(raw, table_rect)
            for row in before:
                _add_approx_positioned_text_row(doc, row, page.get("width"))
                made_content = True
            _add_cell_box_table(doc, page, cell_boxes)
            made_content = True
            for row in after:
                _add_approx_positioned_text_row(doc, row, page.get("width"))
                made_content = True
            continue
        if not regions:
            continue
        table_rect = _main_table_rect(raw, regions)
        if not _should_use_region_geometry(page, regions, table_rect):
            continue
        if page_index:
            doc.add_page_break()
        _set_page_orientation(doc, page.get("width"), page.get("height"))
        before, after = _outside_table_paragraphs_by_position(raw, table_rect)
        for row in before:
            _add_approx_positioned_text_row(doc, row, page.get("width"))
            made_content = True
        _add_region_table(doc, regions)
        made_content = True
        for row in after:
            _add_approx_positioned_text_row(doc, row, page.get("width"))
            made_content = True

    if not made_content:
        return None
    import io

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _should_use_region_geometry(page: dict, regions: list[dict], table_rect: list[float] | None) -> bool:
    """Decide whether restore regions are table geometry or just text groups.

    The service may return region_restore for many document types. For a Word
    reconstruction, those regions should become visible table cells only when
    they are anchored by a detected table layout box and several regions occupy
    that box. Otherwise ordinary OCR paragraphs are usually more faithful.
    """
    if not table_rect or not regions:
        return False
    page_width = float(page.get("width") or max(table_rect[2], 1.0))
    page_height = float(page.get("height") or max(table_rect[3], 1.0))
    table_width = max(1.0, table_rect[2] - table_rect[0])
    table_height = max(1.0, table_rect[3] - table_rect[1])
    if table_width < page_width * 0.03 or table_height < page_height * 0.05:
        return False

    overlapping = [region for region in regions if _overlap_ratio(region["rect"], table_rect) >= 0.45]
    if len(overlapping) >= 3:
        return True
    if any(region["items"] for region in overlapping):
        return True

    # A single large region can still contain a real internal table, but only
    # use this route when item coordinates reveal row/column structure.
    return any(_should_use_positioned_nested_table(region) for region in overlapping)


class _TableParser(HTMLParser):
    """Parse PPStructure pred_html into rows of (text, colspan) cells."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows = []
        self._row = None
        self._cell = None
        self._span = 1

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._row = []
        elif tag in {"td", "th"}:
            attrs_dict = dict(attrs)
            self._span = int(attrs_dict.get("colspan", "1") or 1)
            self._cell = []

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag):
        if tag in {"td", "th"}:
            if self._row is not None and self._cell is not None:
                self._row.append(("".join(self._cell).strip(), self._span))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None


def build_semantic_docx(payload: dict) -> bytes | None:
    """Build a readable DOCX when /structure includes table pred_html."""
    pages = payload.get("pages", []) if isinstance(payload, dict) else []
    if not pages:
        return None
    doc = Document()
    _set_default_font(doc)
    made_content = False

    for page_index, page in enumerate(pages):
        raw = _raw_structure_res(page)
        table_rows = [_parse_pred_html(html) for html in _pred_html_list(raw)]
        table_rows = [rows for rows in table_rows if rows]
        if not table_rows:
            continue
        if page_index:
            doc.add_page_break()
        _set_page_orientation(doc, page.get("width"), page.get("height"))
        for text in _outside_table_paragraphs(raw):
            _add_paragraph(doc, text)
            made_content = True
        for rows in table_rows:
            _add_table(doc, rows)
            made_content = True

    if not made_content:
        return None
    import io

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _payload_with_plain_lines(payload: dict) -> dict:
    """Return a payload whose pages use global OCR lines for paragraph output.

    `extract_docx_pages()` intentionally prefers restore-region items because
    that is useful for geometry reconstruction. The plain fallback should not
    use those grouped items; it should use the normal page OCR lines, matching
    the simpler Hermes path for non-table documents.
    """
    if not isinstance(payload, dict):
        return {"pages": []}
    pages = []
    for page in payload.get("pages", []) or []:
        if not isinstance(page, dict):
            continue
        plain_lines = normalize_lines(page.get("lines") or page.get("ocr_lines") or _overall_ocr_lines(page) or [])
        pages.append({
            "page_no": page.get("page_no") or page.get("page") or len(pages) + 1,
            "width": page.get("width"),
            "height": page.get("height"),
            "lines": plain_lines,
        })
    return {"pages": pages}


def _plain_payload_has_lines(payload: dict) -> bool:
    plain = _payload_with_plain_lines(payload)
    return any(page.get("lines") for page in plain.get("pages", []))


def _set_default_font(doc) -> None:
    style = doc.styles["Normal"]
    style.font.name = "Times New Roman"
    style.font.size = Pt(10.5)
    style.element.rPr.rFonts.set(qn("w:eastAsia"), "SimSun")


def _set_run_font(run, size: float = 10.5) -> None:
    run.font.name = "Times New Roman"
    run.font.size = Pt(size)
    run._element.rPr.rFonts.set(qn("w:eastAsia"), "SimSun")


def _set_page_orientation(doc, page_width, page_height) -> None:
    section = doc.sections[-1]
    if page_width and page_height and float(page_width) > float(page_height):
        section.orientation = WD_ORIENT.LANDSCAPE
        section.page_width, section.page_height = section.page_height, section.page_width
        section.left_margin = Cm(1.0)
        section.right_margin = Cm(1.0)


def _raw_structure_res(page: dict) -> dict:
    raw_results = page.get("structure", {}).get("raw_results", [])
    if not raw_results:
        return {}
    first = raw_results[0]
    return first.get("res", first) if isinstance(first, dict) else {}


def _pred_html_list(raw: dict) -> list[str]:
    htmls = []
    for table in raw.get("table_res_list", []) if isinstance(raw, dict) else []:
        html = table.get("pred_html") or table.get("html") or ""
        if html:
            htmls.append(html)
    return htmls


def _parse_pred_html(html: str) -> list[list[tuple[str, int]]]:
    parser = _TableParser()
    parser.feed(html)
    # PPStructure sometimes emits spacer rows such as <td colspan="8"></td>.
    # Dropping fully empty rows keeps the Word table closer to the source form.
    return [row for row in parser.rows if row and any(text.strip() for text, _ in row)]


def _outside_table_paragraphs(raw: dict) -> list[str]:
    """Return non-table OCR text in the same coordinate space as layout boxes."""
    table_rects = [
        rect
        for box in raw.get("layout_det_res", {}).get("boxes", [])
        if isinstance(box, dict) and box.get("label") == "table"
        for rect in [box_to_rect(box.get("coordinate"))]
        if rect
    ]
    overall = raw.get("overall_ocr_res", {}) if isinstance(raw, dict) else {}
    texts = overall.get("rec_texts", []) or []
    boxes = overall.get("rec_boxes") or overall.get("rec_polys") or []
    scores = overall.get("rec_scores", []) or []
    items = []
    for index, text in enumerate(texts):
        text = str(text or "").strip()
        if not text or index >= len(boxes):
            continue
        rect = box_to_rect(boxes[index])
        if not rect:
            continue
        score = float(scores[index]) if index < len(scores) else 0.0
        if score < 0.5 or any(_inside_rect(rect, table) for table in table_rects):
            continue
        items.append((rect[1], rect[0], text))
    return _merge_same_row_text(items)


def _outside_table_paragraphs_by_position(raw: dict, table_rect: list[float] | None) -> tuple[list[list[tuple[float, str]]], list[list[tuple[float, str]]]]:
    items = _outside_table_items(raw, table_rect)
    if not table_rect:
        return _cluster_positioned_text_rows(items), []
    before = [item for item in items if item[0] < table_rect[1]]
    after = [item for item in items if item[0] > table_rect[3]]
    return _cluster_positioned_text_rows(before), _cluster_positioned_text_rows(after)


def _outside_table_items(raw: dict, table_rect: list[float] | None) -> list[tuple[float, float, str]]:
    overall = raw.get("overall_ocr_res", {}) if isinstance(raw, dict) else {}
    texts = overall.get("rec_texts", []) or []
    boxes = overall.get("rec_boxes") or overall.get("rec_polys") or []
    scores = overall.get("rec_scores", []) or []
    items = []
    for index, text in enumerate(texts):
        text = str(text or "").strip()
        if not text or index >= len(boxes):
            continue
        rect = box_to_rect(boxes[index])
        if not rect:
            continue
        score = float(scores[index]) if index < len(scores) else 0.0
        if score < 0.5:
            continue
        if table_rect and _inside_rect(rect, table_rect):
            continue
        items.append((rect[1], rect[0], text))
    return items


def _image_blocks(raw: dict) -> list[dict]:
    blocks = []
    for block in raw.get("parsing_res_list", []) if isinstance(raw, dict) else []:
        if block.get("block_label") != "image":
            continue
        rect = box_to_rect(block.get("block_bbox"))
        if rect:
            blocks.append({"rect": rect})
    if blocks:
        return blocks
    for box in raw.get("layout_det_res", {}).get("boxes", []) if isinstance(raw, dict) else []:
        if isinstance(box, dict) and box.get("label") == "image":
            rect = box_to_rect(box.get("coordinate"))
            if rect:
                blocks.append({"rect": rect})
    return blocks


def _top_complex_image_blocks(page: dict) -> list[dict]:
    """Find top-page image blocks that are better preserved as pictures.

    The conservative trigger is: a large image block near the page top that
    contains at least two tall/narrow OCR lines. That matches complex letterhead
    or seal-like areas while avoiding ordinary inline illustrations.
    """
    raw = _raw_structure_res(page)
    page_width = float(page.get("width") or 1)
    page_height = float(page.get("height") or 1)
    lines = _plain_line_items(page, prefer_overall=True)
    blocks = []
    for block in _image_blocks(raw):
        rect = block["rect"]
        width = rect[2] - rect[0]
        height = rect[3] - rect[1]
        if rect[1] > page_height * 0.45:
            continue
        if width < page_width * 0.25 or height < page_height * 0.12:
            continue
        inside = [line for line in lines if _inside_rect(line["rect"], rect, pad=8.0)]
        vertical_count = sum(1 for line in inside if _looks_vertical_line(line))
        if vertical_count >= 2:
            blocks.append(block)
    return blocks


def _add_cropped_image_block(doc, source_path: Path, rect: list[float], page: dict) -> bool:
    crop_path = _crop_source_region(source_path, rect, int(page.get("width") or 1), int(page.get("height") or 1))
    if not crop_path:
        return False
    page_width = float(page.get("width") or 1)
    section = doc.sections[-1]
    usable_width = section.page_width - section.left_margin - section.right_margin
    target_width = min(usable_width, int(usable_width * (rect[2] - rect[0]) / max(1.0, page_width)))
    paragraph = doc.add_paragraph()
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = paragraph.add_run()
    run.add_picture(str(crop_path), width=target_width)
    return True


def _plain_lines_outside_rects(page: dict, rects: list[list[float]]) -> list[dict]:
    return [
        line
        for line in _plain_line_items(page, prefer_overall=True)
        if not any(_inside_rect(line["rect"], rect, pad=8.0) or _overlap_ratio(line["rect"], rect) >= 0.5 for rect in rects)
    ]


def _plain_line_items(page: dict, prefer_overall: bool = False) -> list[dict]:
    source_lines = _overall_ocr_lines(page) if prefer_overall else []
    if not source_lines:
        source_lines = page.get("lines") or page.get("ocr_lines") or _overall_ocr_lines(page) or []
    lines = normalize_lines(source_lines)
    items = []
    for line in lines:
        rect = box_to_rect(line.get("bbox"))
        if rect:
            items.append({
                "text": line["text"],
                "confidence": line.get("confidence", 0.0),
                "rect": rect,
                "cy": (rect[1] + rect[3]) / 2,
            })
    return items


def _line_items_from_lines(lines: list[dict]) -> list[dict]:
    items = []
    for line in normalize_lines(lines):
        rect = box_to_rect(line.get("bbox"))
        if rect:
            items.append({
                "text": line["text"],
                "confidence": line.get("confidence", 0.0),
                "rect": rect,
                "cy": (rect[1] + rect[3]) / 2,
            })
    return items


def _table_assignment_items(page: dict) -> list[dict]:
    """Return OCR items for assigning text into table cells.

    Do not use `page.lines` as the first choice here. On some rotated/table
    images, `page.lines` has readable text but coordinates from a different
    preprocessed space, while `cell_box_list` and `table_ocr_pred` share the
    table coordinate space. `page.lines` remains useful for plain-document
    reading order, but table cell membership must prefer table OCR boxes.
    """
    table_lines = _table_ocr_lines(page)
    if table_lines:
        return _line_items_from_lines(table_lines)
    overall_lines = _overall_ocr_lines(page)
    if overall_lines:
        return _line_items_from_lines(overall_lines)
    return _plain_line_items(page)


def _cluster_plain_docx_rows(lines: list[dict]) -> list[list[dict]]:
    rows = []
    for line in sorted(lines, key=lambda item: item["cy"]):
        height = max(1.0, line["rect"][3] - line["rect"][1])
        threshold = max(8.0, height * 0.55)
        if rows and abs(line["cy"] - _plain_row_center(rows[-1])) <= threshold:
            rows[-1].append(line)
        else:
            rows.append([line])
    return [sorted(row, key=lambda item: item["rect"][0]) for row in rows]


def _plain_row_center(row: list[dict]) -> float:
    return sum(item["cy"] for item in row) / len(row)


def _looks_vertical_line(line: dict) -> bool:
    text = line["text"].strip()
    width = max(1.0, line["rect"][2] - line["rect"][0])
    height = max(1.0, line["rect"][3] - line["rect"][1])
    if len(text) < 2 or len(text) > 12:
        return False
    if height / width < 1.8:
        return False
    chars = [char for char in text if not char.isspace()]
    if not chars:
        return False
    return sum(1 for char in chars if "\u4e00" <= char <= "\u9fff") / len(chars) >= 0.75


def _restore_regions(page: dict) -> list[dict]:
    """Read large table regions produced by the service-side restore step.

    Each region has its own rectangle and a list of OCR items that still keep
    original page-coordinate rectangles. That lets us first draw the large cell
    and then do a second pass inside selected cells using item.rect.
    """
    restored = page.get("restore", {}).get("region_restore", {})
    regions = []
    for index, region in enumerate(restored.get("regions", []), start=1):
        rect = box_to_rect(region.get("rect") or region.get("coordinate"))
        if not rect:
            continue
        regions.append({
            "index": int(region.get("index") or index),
            "rect": rect,
            "items": _region_items(region),
            "text": _region_text_from_items(region),
        })
    return regions


def _region_items(region: dict) -> list[dict]:
    items = []
    for item in region.get("items", []):
        text = str(item.get("text", "")).strip()
        rect = box_to_rect(item.get("rect") or item.get("bbox"))
        if text and rect:
            items.append({"text": text, "rect": rect})
    items.sort(key=lambda item: (item["rect"][1], item["rect"][0]))
    return items


def _region_text_from_items(region: dict) -> str:
    items = [(item["rect"][1], item["rect"][0], item["text"]) for item in _region_items(region)]
    if items:
        return "\n".join(_merge_same_row_text(items))
    return str(region.get("text", "")).strip()


def _main_table_rect(raw: dict, regions: list[dict]) -> list[float] | None:
    table_boxes = raw.get("layout_det_res", {}).get("boxes", []) if isinstance(raw, dict) else []
    for box in table_boxes:
        if isinstance(box, dict) and box.get("label") == "table":
            rect = box_to_rect(box.get("coordinate"))
            if rect:
                return rect
    return None


def _bounding_rect(boxes: list[list[float]]) -> list[float] | None:
    if not boxes:
        return None
    return [
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    ]


def _should_use_cell_box_table(page: dict, cell_boxes: list[list[float]]) -> bool:
    """Use coordinate cell boxes only when their companion regions are stable.

    Some OCR results have good `pred_html` but incomplete/shifted restore
    regions. In those cases a coordinate table assigns text to the wrong cells;
    semantic HTML is the safer route. A healthy coordinate table has enough
    large restore regions to infer the main columns and body rows.
    """
    return len(_restore_regions(page)) >= 15 and len(cell_boxes) >= 8


def _add_cell_box_table(doc, page: dict, cell_boxes: list[list[float]]) -> None:
    """Create the visible table from detected cell boxes, then assign OCR by bbox.

    `cell_boxes` supply the grid geometry. Text placement is intentionally
    derived from OCR line center points instead of region text order, so a line
    lands in the cell whose rectangle contains its center.
    """
    restore_regions = _restore_regions(page)
    assignment_items = _table_assignment_items(page)
    if len(restore_regions) >= 4:
        x_edges = _cluster_edges([value for region in restore_regions for value in (region["rect"][0], region["rect"][2])], tolerance=45.0)
    else:
        x_edges = _cluster_edges([value for box in cell_boxes for value in (box[0], box[2])], tolerance=45.0)
    x_edges = _refine_right_quantity_edges(x_edges, assignment_items)
    y_edges = _table_y_edges_from_boxes_and_regions(cell_boxes, restore_regions, assignment_items)
    if len(x_edges) < 2 or len(y_edges) < 2:
        return
    table = doc.add_table(rows=len(y_edges) - 1, cols=len(x_edges) - 1)
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    _apply_region_table_sizes(doc, table, x_edges, y_edges)

    cell_map = [
        {
            "rect": [x_edges[col], y_edges[row], x_edges[col + 1], y_edges[row + 1]],
            "cell": table.cell(row, col),
            "row": row,
            "col": col,
        }
        for row in range(len(y_edges) - 1)
        for col in range(len(x_edges) - 1)
    ]
    assignments = {id(entry["cell"]): [] for entry in cell_map}
    for item in assignment_items:
        target = _cell_entry_for_item(item, cell_map)
        if target is not None:
            assignments[id(target["cell"])].append(item)

    _clean_table_assignments(cell_map, assignments)
    for entry in cell_map:
        _fill_cell_from_assigned_items(entry["cell"], assignments[id(entry["cell"])])


def _cell_entry_for_item(item: dict, cell_map: list[dict]) -> dict | None:
    cx = (item["rect"][0] + item["rect"][2]) / 2
    cy = (item["rect"][1] + item["rect"][3]) / 2
    strict_containing = [
        entry for entry in cell_map
        if entry["rect"][0] <= cx <= entry["rect"][2] and entry["rect"][1] <= cy <= entry["rect"][3]
    ]
    if strict_containing:
        return min(strict_containing, key=lambda entry: _rect_area(entry["rect"]))

    padded_containing = [
        entry for entry in cell_map
        if entry["rect"][0] - 24 <= cx <= entry["rect"][2] + 48 and entry["rect"][1] - 48 <= cy <= entry["rect"][3] + 48
    ]
    if padded_containing:
        return min(padded_containing, key=lambda entry: _distance_to_rect_center(cx, cy, entry["rect"]))
    return None


def _fill_cell_from_assigned_items(cell, items: list[dict]) -> None:
    if not items:
        return
    cell.text = ""
    rows = _cluster_plain_docx_rows(items)
    for row_index, row in enumerate(rows):
        paragraph = cell.paragraphs[0] if row_index == 0 else cell.add_paragraph()
        paragraph.paragraph_format.space_before = Pt(0)
        paragraph.paragraph_format.space_after = Pt(0)
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER if len(row) == 1 and len(row[0]["text"]) <= 8 else WD_ALIGN_PARAGRAPH.LEFT
        text = " ".join(item["text"] for item in sorted(row, key=lambda value: value["rect"][0]))
        run = paragraph.add_run(text)
        _set_run_font(run, size=8.5)


def _clean_table_assignments(cell_map: list[dict], assignments: dict[int, list[dict]]) -> None:
    """Remove common table OCR duplicates after coordinate assignment.

    Table OCR can emit the right-side unit/quantity text twice: once correctly
    in the unit/quantity cells and once as tiny tail fragments inside the
    previous description cell. This cleanup is row-local and coordinate-free
    after assignment: if the right cells already contain short values, identical
    short fragments are removed from the description cell. It also normalizes
    common OCR variants of quantity "1" in the rightmost column.
    """
    if not cell_map:
        return
    max_col = max(entry["col"] for entry in cell_map)
    by_position = {(entry["row"], entry["col"]): entry for entry in cell_map}
    for entry in cell_map:
        if entry["col"] == max_col:
            for item in assignments[id(entry["cell"])]:
                if item["text"].strip() in {"一", "I", "l", "|"}:
                    item["text"] = "1"

    for row in sorted({entry["row"] for entry in cell_map}):
        unit_entry = by_position.get((row, max_col - 1))
        qty_entry = by_position.get((row, max_col))
        desc_entry = by_position.get((row, max_col - 2))
        if not unit_entry or not qty_entry or not desc_entry:
            continue
        right_values = {
            item["text"].strip()
            for entry in (unit_entry, qty_entry)
            for item in assignments[id(entry["cell"])]
            if len(item["text"].strip()) <= 3
        }
        if not right_values:
            continue
        desc_items = assignments[id(desc_entry["cell"])]
        assignments[id(desc_entry["cell"])] = [
            item for item in desc_items
            if not (len(item["text"].strip()) <= 3 and item["text"].strip() in right_values)
        ]


def _rect_area(rect: list[float]) -> float:
    return max(1.0, rect[2] - rect[0]) * max(1.0, rect[3] - rect[1])


def _distance_to_rect_center(x: float, y: float, rect: list[float]) -> float:
    cx = (rect[0] + rect[2]) / 2
    cy = (rect[1] + rect[3]) / 2
    return (x - cx) ** 2 + (y - cy) ** 2


def _table_y_edges_from_boxes_and_regions(cell_boxes: list[list[float]], regions: list[dict], assignment_items: list[dict] | None = None) -> list[float]:
    if not regions:
        return _cluster_edges([value for box in cell_boxes for value in (box[1], box[3])], tolerance=26.0)
    box_edges = _cluster_edges([value for box in cell_boxes for value in (box[1], box[3])], tolerance=26.0)
    region_edges = _cluster_edges([value for region in regions for value in (region["rect"][1], region["rect"][3])], tolerance=45.0)
    if not box_edges or not region_edges:
        return box_edges or region_edges
    table_top = box_edges[0]
    table_bottom = box_edges[-1]
    first_region_top = region_edges[0]
    last_region_bottom = region_edges[-1]
    header_breaks = [edge for edge in box_edges[1:-1] if table_top + 35 <= edge < first_region_top - 35]
    header_bottom = header_breaks[0] if header_breaks else first_region_top
    trailing_breaks, trailing_bottom = _trailing_row_edges_from_sequence_items(region_edges, table_bottom, regions, assignment_items)
    if not trailing_breaks:
        trailing_breaks = [edge for edge in box_edges[1:-1] if last_region_bottom + 35 <= edge < table_bottom - 35]
    edges = _dedupe_edges([table_top, header_bottom, *region_edges, *trailing_breaks, trailing_bottom], tolerance=35.0)
    return _refine_y_edges_with_sequence_centers(edges, assignment_items)


def _trailing_row_edges_from_sequence_items(region_edges: list[float], table_bottom: float, regions: list[dict], assignment_items: list[dict] | None) -> tuple[list[float], float]:
    if not regions:
        return [], table_bottom
    left_edge = min(region["rect"][0] for region in regions)
    first_col_right = sorted({region["rect"][2] for region in regions if region["rect"][0] <= left_edge + 40})
    if not first_col_right:
        return [], table_bottom
    seq_col_right = first_col_right[0] + 80
    last_region_bottom = region_edges[-1]
    centers = []
    max_bottom = table_bottom
    candidates = assignment_items or []
    for item in candidates:
        text = item["text"].strip()
        rect = item["rect"]
        cx = (rect[0] + rect[2]) / 2
        cy = (rect[1] + rect[3]) / 2
        if len(text) <= 2 and cx <= seq_col_right and cy > last_region_bottom + 20:
            centers.append(cy)
            max_bottom = max(max_bottom, rect[3] + 8)
    centers = sorted(centers)
    if len(centers) < 2:
        return [], table_bottom
    breaks = [(centers[index] + centers[index + 1]) / 2 for index in range(len(centers) - 1)]
    return breaks, max_bottom


def _refine_y_edges_with_sequence_centers(edges: list[float], assignment_items: list[dict] | None) -> list[float]:
    if not assignment_items or len(edges) < 3:
        return edges
    seq_centers = []
    for item in assignment_items:
        text = item["text"].strip()
        rect = item["rect"]
        cx = (rect[0] + rect[2]) / 2
        cy = (rect[1] + rect[3]) / 2
        if text.isdigit() and len(text) <= 2 and cx < 420:
            seq_centers.append(cy)
    seq_centers = sorted(seq_centers)
    if len(seq_centers) < 2:
        return edges
    seq_boundaries = [(seq_centers[index] + seq_centers[index + 1]) / 2 for index in range(len(seq_centers) - 1)]
    refined = [edges[0]]
    for edge in edges[1:-1]:
        if edge < 1000:
            refined.append(edge)
            continue
        closest = min(seq_boundaries, key=lambda boundary: abs(boundary - edge))
        refined.append(closest if abs(closest - edge) <= 80 else edge)
    refined.append(edges[-1])
    return _dedupe_edges(refined, tolerance=20.0)


def _refine_right_quantity_edges(x_edges: list[float], assignment_items: list[dict]) -> list[float]:
    """Split the right-side unit/quantity columns from OCR short-text centers."""
    if len(x_edges) < 5:
        return x_edges
    right_zone_start = x_edges[-3]
    short_items = [
        item for item in assignment_items
        if len(item["text"].strip()) <= 3 and (item["rect"][0] + item["rect"][2]) / 2 >= right_zone_start
    ]
    centers = sorted((item["rect"][0] + item["rect"][2]) / 2 for item in short_items)
    clusters = []
    for center in centers:
        if clusters and abs(center - clusters[-1][-1]) <= 45:
            clusters[-1].append(center)
        else:
            clusters.append([center])
    cluster_centers = [sum(cluster) / len(cluster) for cluster in clusters if len(cluster) >= 2]
    if len(cluster_centers) < 2:
        return x_edges
    unit_center, qty_center = cluster_centers[-2], cluster_centers[-1]
    if qty_center <= unit_center:
        return x_edges
    boundary = (unit_center + qty_center) / 2
    right_edge = max(item["rect"][2] for item in short_items) + 8
    return [*x_edges[:-2], boundary, right_edge]


def _dedupe_edges(edges: list[float], tolerance: float) -> list[float]:
    deduped = []
    for edge in sorted(edges):
        if deduped and abs(edge - deduped[-1]) <= tolerance:
            deduped[-1] = (deduped[-1] + edge) / 2
        else:
            deduped.append(edge)
    return deduped


def _add_region_table(doc, regions: list[dict]) -> None:
    """Create the visible outer table from large region rectangles.

    Columns and rows are sized proportionally from source coordinates. This is
    why narrow label cells such as buyer/seller/remark stay narrow instead of
    becoming equal-width Word columns.
    """
    x_edges = _cluster_edges([value for region in regions for value in (region["rect"][0], region["rect"][2])])
    y_edges = _cluster_edges([value for region in regions for value in (region["rect"][1], region["rect"][3])])
    if len(x_edges) < 2 or len(y_edges) < 2:
        return
    table = doc.add_table(rows=len(y_edges) - 1, cols=len(x_edges) - 1)
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    _apply_region_table_sizes(doc, table, x_edges, y_edges)

    occupied = set()
    for region in sorted(regions, key=lambda item: ((item["rect"][1], item["rect"][0]))):
        start_col, end_col = _edge_span(region["rect"][0], region["rect"][2], x_edges)
        start_row, end_row = _edge_span(region["rect"][1], region["rect"][3], y_edges)
        if (start_row, start_col) in occupied:
            continue
        cell = table.cell(start_row, start_col)
        if end_row > start_row or end_col > start_col:
            cell = cell.merge(table.cell(end_row, end_col))
        for row in range(start_row, end_row + 1):
            for col in range(start_col, end_col + 1):
                occupied.add((row, col))
        _fill_region_cell(cell, region)


def _apply_region_table_sizes(doc, table, x_edges: list[float], y_edges: list[float]) -> None:
    usable_width = doc.sections[-1].page_width - doc.sections[-1].left_margin - doc.sections[-1].right_margin
    source_width = max(1.0, x_edges[-1] - x_edges[0])
    for col_index, column in enumerate(table.columns):
        width = int(usable_width * (x_edges[col_index + 1] - x_edges[col_index]) / source_width)
        for cell in column.cells:
            cell.width = width

    usable_height = doc.sections[-1].page_height - doc.sections[-1].top_margin - doc.sections[-1].bottom_margin
    source_height = max(1.0, y_edges[-1] - y_edges[0])
    target_height = int(usable_height * 0.62)
    for row_index, row in enumerate(table.rows):
        row.height_rule = WD_ROW_HEIGHT_RULE.EXACTLY
        row.height = int(target_height * (y_edges[row_index + 1] - y_edges[row_index]) / source_height)


def _fill_region_cell(cell, region: dict) -> None:
    """Fill one outer-region cell.

    Most regions are simple text. Regions whose OCR items form a clear row/col
    grid get a second-level borderless table because Word does not support
    HTML/canvas-style absolute positioning in ordinary document flow.
    """
    if _should_use_positioned_nested_table(region):
        _fill_positioned_region_cell(cell, region)
        return
    text_rows = _region_text_rows(region)
    if not text_rows:
        return
    cell.text = ""
    align = WD_ALIGN_PARAGRAPH.CENTER if _is_region_label_cell(region) else WD_ALIGN_PARAGRAPH.LEFT
    for row_index, text in enumerate(text_rows):
        if _is_region_label_cell(region):
            text = "\n".join(text)
        paragraph = cell.paragraphs[0] if row_index == 0 else cell.add_paragraph()
        paragraph.alignment = align
        run = paragraph.add_run(text)
        _set_run_font(run, size=8.5)


def _region_text_rows(region: dict) -> list[str]:
    items = region.get("items", [])
    if items:
        return _merge_same_row_text([(item["rect"][1], item["rect"][0], item["text"]) for item in items])
    text = str(region.get("text", "")).strip()
    return text.splitlines() if text else []


def _is_region_label_cell(region: dict) -> bool:
    rect = region["rect"]
    text = str(region.get("text", "")).strip()
    width = max(1.0, rect[2] - rect[0])
    height = max(1.0, rect[3] - rect[1])
    return len(text) <= 8 and (height / width > 1.5 or width < 80)


def _should_use_positioned_nested_table(region: dict) -> bool:
    """Detect logical inner tables from coordinates only, not document text."""
    items = region.get("items", [])
    if len(items) < 5:
        return False
    rows = _cluster_detail_rows(items)
    col_centers = _cluster_item_axis_centers(items, axis="x")
    if len(rows) < 1 or len(col_centers) < 4:
        return False
    multi_item_rows = sum(1 for row in rows if len(row) >= 4)
    return multi_item_rows >= 1


def _fill_positioned_region_cell(cell, region: dict) -> None:
    """Place OCR items into a borderless nested table using item coordinates."""
    items = region.get("items", [])
    col_centers = _cluster_item_axis_centers(items, axis="x")
    rows = _cluster_detail_rows(items)
    if not rows or len(col_centers) < 2:
        _fill_region_cell_as_text(cell, region)
        return

    cell.text = ""
    nested = cell.add_table(rows=len(rows), cols=len(col_centers))
    _hide_table_borders(nested)
    for row_index, row in enumerate(rows):
        for item in row:
            col_index = _nearest_index((item["rect"][0] + item["rect"][2]) / 2, col_centers)
            target = nested.cell(row_index, col_index)
            paragraph = target.paragraphs[0]
            if paragraph.text:
                paragraph.add_run(" ")
            paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER if len(item["text"]) <= 8 else WD_ALIGN_PARAGRAPH.LEFT
            run = paragraph.add_run(item["text"])
            _set_run_font(run, size=8)


def _hide_table_borders(table) -> None:
    """Hide all real table borders and remove cell padding.

    This suppresses printed/exported borders. Some Word clients can still show
    editing gridlines when "View gridlines" is enabled; those are UI overlays,
    not document borders.
    """
    tbl_pr = table._tbl.tblPr
    borders = tbl_pr.first_child_found_in("w:tblBorders")
    if borders is None:
        borders = OxmlElement("w:tblBorders")
        tbl_pr.append(borders)
    for edge in ["top", "left", "bottom", "right", "insideH", "insideV"]:
        element = borders.find(qn(f"w:{edge}"))
        if element is None:
            element = OxmlElement(f"w:{edge}")
            borders.append(element)
        element.set(qn("w:val"), "nil")
    for row in table.rows:
        for cell in row.cells:
            tc_pr = cell._tc.get_or_add_tcPr()
            cell_borders = tc_pr.first_child_found_in("w:tcBorders")
            if cell_borders is None:
                cell_borders = OxmlElement("w:tcBorders")
                tc_pr.append(cell_borders)
            for edge in ["top", "left", "bottom", "right", "insideH", "insideV"]:
                element = cell_borders.find(qn(f"w:{edge}"))
                if element is None:
                    element = OxmlElement(f"w:{edge}")
                    cell_borders.append(element)
                element.set(qn("w:val"), "nil")
            margins = tc_pr.first_child_found_in("w:tcMar")
            if margins is None:
                margins = OxmlElement("w:tcMar")
                tc_pr.append(margins)
            for side in ["top", "left", "bottom", "right"]:
                margin = margins.find(qn(f"w:{side}"))
                if margin is None:
                    margin = OxmlElement(f"w:{side}")
                    margins.append(margin)
                margin.set(qn("w:w"), "0")
                margin.set(qn("w:type"), "dxa")


def _fill_region_cell_as_text(cell, region: dict) -> None:
    text_rows = _region_text_rows(region)
    if not text_rows:
        return
    cell.text = ""
    for row_index, text in enumerate(text_rows):
        paragraph = cell.paragraphs[0] if row_index == 0 else cell.add_paragraph()
        run = paragraph.add_run(text)
        _set_run_font(run, size=8.5)


def _cluster_detail_rows(items: list[dict]) -> list[list[dict]]:
    rows = []
    for item in sorted(items, key=lambda value: ((value["rect"][1] + value["rect"][3]) / 2, value["rect"][0])):
        cy = (item["rect"][1] + item["rect"][3]) / 2
        if rows and abs(cy - _detail_row_center(rows[-1])) <= 24:
            rows[-1].append(item)
        else:
            rows.append([item])
    for row in rows:
        row.sort(key=lambda value: value["rect"][0])
    return rows


def _cluster_item_axis_centers(items: list[dict], axis: str, tolerance: float = 45.0) -> list[float]:
    indexes = (0, 2) if axis == "x" else (1, 3)
    centers = sorted((item["rect"][indexes[0]] + item["rect"][indexes[1]]) / 2 for item in items)
    clusters = []
    for center in centers:
        if clusters and abs(center - clusters[-1][-1]) <= tolerance:
            clusters[-1].append(center)
        else:
            clusters.append([center])
    return [sum(cluster) / len(cluster) for cluster in clusters]


def _detail_row_center(row: list[dict]) -> float:
    return sum((item["rect"][1] + item["rect"][3]) / 2 for item in row) / len(row)


def _nearest_index(value: float, centers: list[float]) -> int:
    return min(range(len(centers)), key=lambda index: abs(value - centers[index]))


def _cluster_edges(values: list[float], tolerance: float = 22.0) -> list[float]:
    clusters = []
    for value in sorted(float(v) for v in values):
        if clusters and abs(value - clusters[-1][-1]) <= tolerance:
            clusters[-1].append(value)
        else:
            clusters.append([value])
    return [sum(cluster) / len(cluster) for cluster in clusters]


def _edge_span(start: float, end: float, edges: list[float]) -> tuple[int, int]:
    start_index = _nearest_edge_index(start, edges)
    end_index = _nearest_edge_index(end, edges)
    if end_index <= start_index:
        end_index = min(len(edges) - 1, start_index + 1)
    return start_index, end_index - 1


def _nearest_edge_index(value: float, edges: list[float]) -> int:
    return min(range(len(edges)), key=lambda index: abs(edges[index] - value))


def _merge_same_row_text(items: list[tuple[float, float, str]]) -> list[str]:
    rows = []
    for y, x, text in sorted(items, key=lambda item: (item[0], item[1])):
        if rows and abs(y - rows[-1][0]) <= 10:
            rows[-1][2].append((x, text))
        else:
            rows.append([y, x, [(x, text)]])
    return ["".join(text for _, text in sorted(row[2], key=lambda item: item[0])) for row in rows]


def _cluster_positioned_text_rows(items: list[tuple[float, float, str]]) -> list[list[tuple[float, str]]]:
    rows = []
    for y, x, text in sorted(items, key=lambda item: (item[0], item[1])):
        if rows and abs(y - rows[-1][0]) <= 10:
            rows[-1][1].append((x, text))
        else:
            rows.append([y, [(x, text)]])
    return [sorted(row[1], key=lambda item: item[0]) for row in rows]


def _inside_rect(rect: list[float], outer: list[float], pad: float = 5.0) -> bool:
    return (
        rect[0] >= outer[0] - pad
        and rect[1] >= outer[1] - pad
        and rect[2] <= outer[2] + pad
        and rect[3] <= outer[3] + pad
    )


def _overlap_ratio(rect: list[float], outer: list[float]) -> float:
    """Return how much of rect is covered by outer."""
    left = max(rect[0], outer[0])
    top = max(rect[1], outer[1])
    right = min(rect[2], outer[2])
    bottom = min(rect[3], outer[3])
    if right <= left or bottom <= top:
        return 0.0
    rect_area = max(1.0, (rect[2] - rect[0]) * (rect[3] - rect[1]))
    return ((right - left) * (bottom - top)) / rect_area


def _add_paragraph(doc, text: str) -> None:
    paragraph = doc.add_paragraph()
    run = paragraph.add_run(text)
    _set_run_font(run, size=10.5)


def _add_positioned_text_row(doc, row: list[tuple[float, str]], page_width) -> None:
    if not row:
        return
    if not page_width:
        _add_paragraph(doc, "".join(text for _, text in row))
        return
    columns = 12
    table = doc.add_table(rows=1, cols=columns)
    _hide_table_borders(table)
    usable_width = doc.sections[-1].page_width - doc.sections[-1].left_margin - doc.sections[-1].right_margin
    col_width = int(usable_width / columns)
    for column in table.columns:
        for cell in column.cells:
            cell.width = col_width
    for x, text in row:
        col_index = min(columns - 1, max(0, int(float(x) / max(1.0, float(page_width)) * columns)))
        paragraph = table.cell(0, col_index).paragraphs[0]
        if paragraph.text:
            paragraph.add_run(" ")
        run = paragraph.add_run(text)
        _set_run_font(run, size=10.5)


def _add_approx_positioned_text_row(doc, row: list[tuple[float, str]], page_width) -> None:
    """Place outside-table text without helper tables.

    Word table gridlines are visible in editing mode even when borders are
    hidden, so outside text uses ordinary paragraphs plus indentation. This is
    less exact than an invisible grid, but it avoids helper gridlines and keeps
    the main table from being pushed down by positioning tables.
    """
    if not row:
        return
    usable_width = doc.sections[-1].page_width - doc.sections[-1].left_margin - doc.sections[-1].right_margin
    if len(row) == 1 and page_width:
        x, text = row[0]
        paragraph = doc.add_paragraph()
        paragraph.paragraph_format.space_before = Pt(0)
        paragraph.paragraph_format.space_after = Pt(0)
        paragraph.paragraph_format.line_spacing = 1.0
        paragraph.paragraph_format.left_indent = int(usable_width * float(x) / max(1.0, float(page_width)))
        run = paragraph.add_run(text)
        _set_run_font(run, size=10.5)
        return

    paragraph = doc.add_paragraph()
    paragraph.paragraph_format.space_before = Pt(0)
    paragraph.paragraph_format.space_after = Pt(0)
    paragraph.paragraph_format.line_spacing = 1.0
    last_x = 0.0
    for x, text in row:
        if paragraph.text:
            paragraph.add_run("  ")
        run = paragraph.add_run(text)
        _set_run_font(run, size=10.5)
        last_x = x


def _add_positioned_image(doc, block: dict, source_path: Path | None, page: dict) -> bool:
    if not source_path or not source_path.exists():
        return False
    image_path = _crop_source_region(source_path, block["rect"], int(page.get("width") or 1), int(page.get("height") or 1))
    if not image_path:
        return False
    columns = 12
    table = doc.add_table(rows=1, cols=columns)
    _hide_table_borders(table)
    usable_width = doc.sections[-1].page_width - doc.sections[-1].left_margin - doc.sections[-1].right_margin
    col_width = int(usable_width / columns)
    for column in table.columns:
        for cell in column.cells:
            cell.width = col_width
    x1, _, x2, _ = block["rect"]
    page_width = max(1.0, float(page.get("width") or x2))
    col_index = min(columns - 1, max(0, int(float(x1) / page_width * columns)))
    width_inches = max(0.35, min(1.2, (x2 - x1) / page_width * 10.0))
    run = table.cell(0, col_index).paragraphs[0].add_run()
    run.add_picture(str(image_path), width=Inches(width_inches))
    return True


def _crop_source_region(source_path: Path, rect: list[float], target_width: int, target_height: int) -> Path | None:
    try:
        from PIL import Image
        if source_path.suffix.lower() == ".pdf":
            image = _render_first_pdf_page(source_path)
            if image is None:
                return None
        else:
            image = Image.open(source_path).convert("RGB")
        scale_x = image.width / max(1, target_width)
        scale_y = image.height / max(1, target_height)
        x1, y1, x2, y2 = rect
        crop = image.crop((int(x1 * scale_x), int(y1 * scale_y), int(x2 * scale_x), int(y2 * scale_y)))
        out_dir = source_path.parent / ".restore_docx_assets"
        out_dir.mkdir(exist_ok=True)
        out_path = out_dir / f"{source_path.stem}_image_{int(x1)}_{int(y1)}_{int(x2)}_{int(y2)}.png"
        crop.save(out_path)
        return out_path
    except Exception:
        return None


def _render_first_pdf_page(source_path: Path):
    """Render the first PDF page to a PIL image for local region cropping."""
    try:
        from PIL import Image
        import fitz

        doc = fitz.open(str(source_path))
        try:
            pix = doc[0].get_pixmap(matrix=fitz.Matrix(220 / 72, 220 / 72), alpha=False)
            return Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        finally:
            doc.close()
    except Exception:
        pass

    try:
        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(str(source_path))
        try:
            page = pdf[0]
            bitmap = page.render(scale=220 / 72)
            return bitmap.to_pil().convert("RGB")
        finally:
            pdf.close()
    except Exception:
        return None


def _add_table(doc, rows: list[list[tuple[str, int]]]) -> None:
    ncols = max(sum(span for _, span in row) for row in rows)
    table = doc.add_table(rows=len(rows), cols=ncols)
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    usable_width = doc.sections[-1].page_width - doc.sections[-1].left_margin - doc.sections[-1].right_margin
    col_width = int(usable_width / ncols)

    for row_index, row in enumerate(rows):
        col_index = 0
        for text, span in row:
            cell = table.cell(row_index, col_index)
            if span > 1:
                cell = cell.merge(table.cell(row_index, col_index + span - 1))
            cell.width = col_width * span
            paragraph = cell.paragraphs[0]
            paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER if len(text) <= 6 else WD_ALIGN_PARAGRAPH.LEFT
            run = paragraph.add_run(text)
            _set_run_font(run, size=9)
            col_index += span


def extract_docx_pages(payload: dict) -> list[dict]:
    """Convert service JSON into the compact page shape expected by docx_builder."""
    if not isinstance(payload, dict):
        return []
    pages = payload.get("pages")
    if isinstance(pages, list):
        return [
            page
            for page in (_page_to_docx(page, index + 1) for index, page in enumerate(pages))
            if page["ocr_lines"]
        ]

    lines = extract_lines(payload)
    if not lines:
        return []
    return [{"page": 1, "ocr_lines": lines, "ocr_text": "\n".join(line["text"] for line in lines)}]


def _page_to_docx(page: dict, page_no: int) -> dict:
    if not isinstance(page, dict):
        return {"page": page_no, "ocr_lines": [], "ocr_text": ""}
    lines = extract_lines(page)
    return {
        "page": int(page.get("page_no") or page.get("page") or page_no),
        "ocr_lines": lines,
        "ocr_text": "\n".join(line["text"] for line in lines),
    }


def extract_lines(node: dict) -> list[dict]:
    if not isinstance(node, dict):
        return []
    # Preference order mirrors restore_structure_preview.py:
    # 1) region_restore: service-side best effort at grouping OCR into restore
    #    regions; this is the most useful source for document reconstruction.
    # 2) global OCR lines: stable fallback when structure/restore is absent.
    # 3) PPStructure overall/table OCR: compatibility fallback for raw or older
    #    JSON files saved during earlier experiments.
    for candidate in [
        _restore_items(node),
        node.get("lines"),
        node.get("ocr_lines"),
        _overall_ocr_lines(node),
        _table_ocr_lines(node),
    ]:
        lines = normalize_lines(candidate or [])
        if lines:
            return lines
    return []


def _overall_ocr_lines(page: dict) -> list[dict]:
    raw_results = page.get("structure", {}).get("raw_results", [])
    if not raw_results:
        return []
    result = raw_results[0].get("res", raw_results[0]) if isinstance(raw_results[0], dict) else {}
    overall = result.get("overall_ocr_res", {}) if isinstance(result, dict) else {}
    return _ocr_pred_to_lines(overall)


def _restore_items(page: dict) -> list[dict]:
    restored = page.get("restore", {}).get("region_restore", {})
    items = []
    for region in restored.get("regions", []):
        items.extend(region.get("items", []))
    return items or restored.get("ocr_items", [])


def _table_ocr_lines(page: dict) -> list[dict]:
    raw = _raw_structure_res(page)
    for table in raw.get("table_res_list", []) if isinstance(raw, dict) else []:
        lines = _ocr_pred_to_lines(table.get("table_ocr_pred", {}))
        if lines:
            return lines
    tables = page.get("structure", {}).get("summary", {}).get("tables", [])
    for table in tables:
        lines = _ocr_pred_to_lines(table.get("table_ocr_pred", {}))
        if lines:
            return lines
    return []


def _ocr_pred_to_lines(pred: dict) -> list[dict]:
    texts = pred.get("rec_texts", []) if isinstance(pred, dict) else []
    boxes = pred.get("rec_boxes") or pred.get("rec_polys") or []
    scores = pred.get("rec_scores", [])
    lines = []
    for index, text in enumerate(texts):
        if index >= len(boxes):
            continue
        lines.append({
            "text": text,
            "confidence": scores[index] if index < len(scores) else 0.0,
            "bbox": boxes[index],
        })
    return lines


def normalize_lines(lines: list[dict]) -> list[dict]:
    """Normalize bbox variants into python-docx builder's OCR line format."""
    normalized = []
    for line in lines:
        text = str(line.get("text", "")).strip()
        if not text:
            continue
        bbox = line.get("bbox")
        if not bbox and all(key in line for key in ["x1", "y1", "x2", "y2"]):
            bbox = [line["x1"], line["y1"], line["x2"], line["y1"], line["x2"], line["y2"], line["x1"], line["y2"]]
        if not bbox and line.get("rect"):
            x1, y1, x2, y2 = line["rect"]
            bbox = [x1, y1, x2, y1, x2, y2, x1, y2]
        if not bbox:
            continue
        normalized.append({
            "text": text,
            "confidence": float(line.get("confidence", 0.0)),
            "bbox": rect_to_poly(bbox),
        })
    return normalized


def rect_to_poly(box) -> list[float]:
    """Return an 8-number polygon for rect, polygon, or nested point formats."""
    if len(box) == 4 and all(isinstance(value, (int, float)) for value in box):
        x1, y1, x2, y2 = [float(value) for value in box]
        return [x1, y1, x2, y1, x2, y2, x1, y2]
    if box and isinstance(box[0], (list, tuple)):
        flat = []
        for point in box:
            if len(point) >= 2:
                flat.extend([float(point[0]), float(point[1])])
        return flat
    return [float(value) for value in box]


def box_to_rect(box) -> list[float] | None:
    """Return [x1, y1, x2, y2] for rect, polygon, or nested point formats."""
    if not box:
        return None
    if len(box) == 4 and all(isinstance(value, (int, float)) for value in box):
        return [float(value) for value in box]
    if isinstance(box[0], (list, tuple)):
        points = [(float(point[0]), float(point[1])) for point in box if len(point) >= 2]
    else:
        points = [(float(box[index]), float(box[index + 1])) for index in range(0, len(box) - 1, 2)]
    if not points:
        return None
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return [min(xs), min(ys), max(xs), max(ys)]


if __name__ == "__main__":
    raise SystemExit(main())
