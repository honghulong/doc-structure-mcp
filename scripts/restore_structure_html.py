# -*- coding: utf-8 -*-
"""
Build coordinate-faithful HTML restore previews from a PDF/image or /structure JSON.

This is the HTML companion to restore_structure_docx.py. It reuses the
structure loading path from the DOCX script and the visual restore rules from
restore_structure_preview.py, then writes a browser-inspectable overlay.

Examples:
    python scripts/restore_structure_html.py ^
      --input tests/fixtures/test_page2.pdf ^
      --save-json

    python scripts/restore_structure_html.py ^
      --json tests/fixtures/test_page2.structure.json ^
      --source tests/fixtures/test_page2.pdf ^
      --out tests/fixtures/test_page2.html
"""
from __future__ import annotations

import argparse
import base64
import html
import io
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from restore_structure_docx import DEFAULT_STRUCTURE_URL, load_payload
from restore_structure_preview import (
    _overall_ocr_lines,
    _restore_items,
    assign_lines_to_regions,
    lines_inside_block,
    normalize_lines,
    render_background,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", help="Single PDF/image file to send to public /structure service")
    parser.add_argument(
        "--url",
        default=os.environ.get("GPU_OCR_PUBLIC_URL", DEFAULT_STRUCTURE_URL),
        help="Public /structure service URL",
    )
    parser.add_argument("--json", help="Single OCR/structure JSON file")
    parser.add_argument("--source", default="", help="Original PDF/image used as HTML background when --json is used")
    parser.add_argument("--out", help="Output HTML path; defaults to input/json path with .html suffix")
    parser.add_argument("--save-json", action="store_true", help="Save service JSON beside the HTML")
    parser.add_argument("--dpi", type=int, default=220)
    args = parser.parse_args()

    payload, source_path = load_payload(args)
    background_path = background_source_path(args, payload, source_path)
    out_path = output_path(args, source_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    out_path.write_text(build_restore_html(payload, background_path), encoding="utf-8")
    if args.save_json and args.input:
        json_path = out_path.with_suffix(".structure.json")
        json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json_path)
    print(out_path)
    return 0


def output_path(args, source_path: Path) -> Path:
    if args.out:
        return Path(args.out).resolve()
    return source_path.with_suffix(".html")


def background_source_path(args, payload: dict, source_path: Path) -> Path | None:
    if args.source:
        return Path(args.source).resolve()
    if args.input:
        return Path(payload.get("_restore_docx_source_path", source_path)).resolve()
    return None


def build_restore_html(payload: dict, source_path: Path | None = None) -> str:
    pages = payload.get("pages", [])
    page_parts = []
    for page_index, page in enumerate(pages, start=1):
        width = int(page.get("width") or 1)
        height = int(page.get("height") or 1)
        image = render_background(source_path, page_index, width, height)
        page_parts.append(build_page_html(page, page_index, width, height, image_data_uri(image)))

    title = html.escape(str(payload.get("case") or payload.get("input_file") or "restore structure"))
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>{title} HTML restore</title>
<style>
body {{
  margin: 0;
  padding: 24px;
  background: #eef2f7;
  color: #111827;
  font-family: Arial, "Microsoft YaHei", sans-serif;
}}
.toolbar {{
  max-width: 1180px;
  margin: 0 auto 16px;
  font-size: 14px;
  color: #334155;
}}
.page {{
  position: relative;
  max-width: 1180px;
  margin: 0 auto 28px;
  background: white;
  box-shadow: 0 2px 14px rgba(15, 23, 42, 0.16);
  overflow: hidden;
}}
.page-image {{
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: contain;
  opacity: 0.55;
}}
.region,
.cell-hint,
.text-item,
.page-block {{
  position: absolute;
  box-sizing: border-box;
}}
.region {{
  border: 2px solid rgba(220, 38, 38, 0.88);
  background: rgba(254, 226, 226, 0.22);
}}
.region.detail {{
  border-color: rgba(37, 99, 235, 0.88);
  background: rgba(219, 234, 254, 0.22);
}}
.cell-hint {{
  border: 1px solid rgba(37, 99, 235, 0.46);
  background: rgba(219, 234, 254, 0.12);
  color: rgba(37, 99, 235, 0.82);
  font-size: 11px;
}}
.text-item {{
  border: 1px solid rgba(22, 163, 74, 0.56);
  color: rgba(15, 23, 42, 0.92);
  overflow: visible;
  white-space: nowrap;
  line-height: 1.05;
}}
.text-item.vertical {{
  writing-mode: vertical-rl;
  text-orientation: upright;
  display: flex;
  align-items: center;
  justify-content: center;
  white-space: normal;
}}
.page-block {{
  border: 2px solid rgba(126, 34, 206, 0.72);
  background: rgba(243, 232, 255, 0.18);
}}
.label {{
  position: absolute;
  left: 4px;
  top: 2px;
  color: inherit;
  font-size: 12px;
  line-height: 1;
}}
</style>
</head>
<body>
<div class="toolbar">{title} | HTML restore overlay from /structure data.</div>
{''.join(page_parts)}
</body>
</html>
"""


def build_page_html(page: dict, page_index: int, width: int, height: int, background_uri: str) -> str:
    restored = page.get("restore", {}).get("region_restore")
    if isinstance(restored, dict) and restored.get("regions"):
        lines = _restore_items(restored)
        page_lines = normalize_lines(_overall_ocr_lines(page) or page.get("lines", []))
        regions = restored.get("regions", [])
        assigned = {int(region["index"]): list(region.get("items", [])) for region in regions}
        page_blocks = restored.get("page_blocks", [])
        source_label = "restore.region_restore"
    else:
        lines = normalize_lines(page.get("lines", []))
        page_lines = lines
        regions = page.get("structure", {}).get("summary", {}).get("regions", [])
        assigned = assign_lines_to_regions(lines, regions)
        page_blocks = []
        source_label = "global OCR fallback"

    tables = page.get("structure", {}).get("summary", {}).get("tables", [])
    cell_boxes = tables[0].get("cell_boxes", []) if tables else []

    parts = [
        f'<section class="page" style="aspect-ratio:{width}/{height}">',
        f'<img class="page-image" src="{background_uri}" alt="page {page_index} background">',
        f'<div class="label" style="left:1.2%;top:1.2%;color:#334155">page={page_index} | {html.escape(source_label)} | regions={len(regions)} | cells={len(cell_boxes)} | text={len(lines)}</div>',
    ]
    parts.extend(cell_hint_html(rect, width, height, index) for index, rect in enumerate(cell_boxes, start=1))
    for region in sorted(regions, key=lambda value: ((value.get("coordinate") or value.get("rect"))[1], (value.get("coordinate") or value.get("rect"))[0])):
        rect = region.get("coordinate") or region.get("rect")
        index = int(region["index"])
        parts.append(region_html(region, rect, width, height, len(assigned.get(index, []))))
        parts.extend(text_item_html(item, width, height) for item in assigned.get(index, []))
    for block in page_blocks:
        parts.append(page_block_html(block, page_lines, width, height))
    parts.append("</section>")
    return "".join(parts)


def region_html(region: dict, rect: list[float], width: int, height: int, item_count: int) -> str:
    class_name = "region detail" if region.get("kind") == "detail" else "region"
    label = f'R{int(region["index"])} items={item_count}'
    return div_html(class_name, rect, width, height, f'<span class="label">{html.escape(label)}</span>')


def cell_hint_html(rect: list[float], width: int, height: int, index: int) -> str:
    return div_html("cell-hint", rect, width, height, html.escape(f"C{index}"))


def text_item_html(item: dict, width: int, height: int) -> str:
    rect = item.get("rect")
    text = str(item.get("text", "")).strip()
    if not rect or not text:
        return ""
    class_name = "text-item vertical" if looks_vertical_text(text, rect) else "text-item"
    size = font_size_for(text, rect)
    return div_html(class_name, rect, width, height, html.escape(text), extra_style=f"font-size:{size}px")


def page_block_html(block: dict, lines: list[dict], width: int, height: int) -> str:
    rect = block.get("rect")
    text = str(block.get("text", "")).strip()
    if not rect or not text:
        return ""
    block_lines = lines_inside_block(text, rect, lines)
    if block_lines:
        children = "".join(text_item_html(item, width, height) for item in block_lines)
        return div_html("page-block", rect, width, height, "") + children
    return div_html("page-block", rect, width, height, html.escape(text), extra_style=f"font-size:{font_size_for(text, rect)}px")


def div_html(class_name: str, rect: list[float], width: int, height: int, body: str, extra_style: str = "") -> str:
    left, top, div_width, div_height = rect_to_percent(rect, width, height)
    style = f"left:{left:.4f}%;top:{top:.4f}%;width:{div_width:.4f}%;height:{div_height:.4f}%;{extra_style}"
    return f'<div class="{class_name}" style="{style}">{body}</div>'


def rect_to_percent(rect: list[float], width: int, height: int) -> tuple[float, float, float, float]:
    x1, y1, x2, y2 = [float(value) for value in rect]
    return (
        100 * x1 / width,
        100 * y1 / height,
        100 * max(1.0, x2 - x1) / width,
        100 * max(1.0, y2 - y1) / height,
    )


def font_size_for(text: str, rect: list[float]) -> int:
    x1, y1, x2, y2 = [float(value) for value in rect]
    if looks_vertical_text(text, rect):
        return max(10, min(22, int((x2 - x1) * 0.56)))
    return max(10, min(28, int((y2 - y1) * 0.45)))


def looks_vertical_text(text: str, rect: list[float]) -> bool:
    text = text.strip()
    x1, y1, x2, y2 = [float(value) for value in rect]
    width = max(1.0, x2 - x1)
    height = max(1.0, y2 - y1)
    if len(text) < 2 or len(text) > 8:
        return False
    if height / width < 1.8:
        return False
    return cjk_ratio(text) >= 0.75


def cjk_ratio(text: str) -> float:
    chars = [char for char in text if not char.isspace()]
    if not chars:
        return 0.0
    return sum(1 for char in chars if "\u4e00" <= char <= "\u9fff") / len(chars)


def image_data_uri(image) -> str:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


if __name__ == "__main__":
    raise SystemExit(main())
