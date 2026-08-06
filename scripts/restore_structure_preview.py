# -*- coding: utf-8 -*-
"""
Render a structure-restore preview image from GPU /structure JSON.

Design rule:
- Text and text bounding boxes come from global OCR `pages[].lines`.
- TableCellsDetection regions are used as the main restore areas.
- PPStructureV3 cell boxes are rendered only as auxiliary structure hints.

Example:
    python scripts/restore_structure_preview.py ^
      --json tests/testContent/public_gpu_structure_test_page2.json ^
      --source tests/fixtures/test_page2.pdf ^
      --out tests/testContent/public_gpu_structure_test_page2_restore.png
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", required=True, help="JSON returned by GPU /structure")
    parser.add_argument("--source", default="", help="Original PDF/image used as background")
    parser.add_argument("--out", required=True, help="Output preview image path")
    parser.add_argument("--page", type=int, default=1, help="1-based page number")
    args = parser.parse_args()

    json_path = Path(args.json).resolve()
    source_path = Path(args.source).resolve() if args.source else None
    out_path = Path(args.out).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    page = payload["pages"][args.page - 1]
    width, height = int(page["width"]), int(page["height"])

    image = render_background(source_path, args.page, width, height)
    preview = build_preview(image, page)
    preview.save(out_path, quality=95)
    print(out_path)
    return 0


def build_preview(background: Image.Image, page: dict) -> Image.Image:
    image = background.convert("RGBA")
    image = Image.alpha_composite(image, Image.new("RGBA", image.size, (255, 255, 255, 105)))
    overlay = Image.new("RGBA", image.size, (255, 255, 255, 0))
    draw = ImageDraw.Draw(overlay)

    font = load_font(17)
    block_font = load_font(max(17, round(15 * image.size[0] / 1180)))
    small = load_font(13)

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
    cell_boxes = []
    tables = page.get("structure", {}).get("summary", {}).get("tables", [])
    if tables:
        cell_boxes = tables[0].get("cell_boxes", [])

    draw_cell_hints(draw, cell_boxes, small)
    draw_regions_and_text(draw, regions, assigned, font, small)
    draw_page_blocks(draw, page_blocks, page_lines, block_font)
    draw_header(draw, source_label, len(regions), len(cell_boxes), len(lines), font, small)

    return Image.alpha_composite(image, overlay).convert("RGB")


def render_background(source_path: Path | None, page_no: int, width: int, height: int) -> Image.Image:
    if not source_path or not source_path.exists():
        return Image.new("RGB", (width, height), "white")
    suffix = source_path.suffix.lower()
    try:
        if suffix == ".pdf":
            import fitz

            doc = fitz.open(str(source_path))
            try:
                page = doc[page_no - 1]
                pix = page.get_pixmap(matrix=fitz.Matrix(220 / 72, 220 / 72), alpha=False)
                image = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            finally:
                doc.close()
        else:
            image = Image.open(source_path).convert("RGB")
        if image.size != (width, height):
            image = image.resize((width, height))
        return image
    except Exception:
        return Image.new("RGB", (width, height), "white")


def normalize_lines(lines: list[dict]) -> list[dict]:
    normalized = []
    for line in lines:
        text = str(line.get("text", "")).strip()
        if not text:
            continue
        rect = line_rect(line)
        if not rect:
            continue
        normalized.append({
            "text": text,
            "rect": rect,
            "confidence": float(line.get("confidence", 0.0)),
        })
    return normalized


def _restore_items(restored: dict) -> list[dict]:
    items = []
    for region in restored.get("regions", []):
        items.extend(region.get("items", []))
    if items:
        return items
    return restored.get("ocr_items", [])


def _overall_ocr_lines(page: dict) -> list[dict]:
    raw_results = page.get("structure", {}).get("raw_results", [])
    if not raw_results:
        return []
    result = raw_results[0].get("res", raw_results[0]) if isinstance(raw_results[0], dict) else {}
    overall = result.get("overall_ocr_res", {}) if isinstance(result, dict) else {}
    texts = overall.get("rec_texts", []) if isinstance(overall, dict) else []
    boxes = overall.get("rec_boxes") or overall.get("rec_polys") or []
    scores = overall.get("rec_scores", [])
    lines = []
    for index, text in enumerate(texts):
        if index >= len(boxes):
            continue
        rect = box_to_rect(boxes[index])
        if not rect:
            continue
        lines.append({
            "text": str(text).strip(),
            "bbox": boxes[index],
            "x1": rect[0],
            "y1": rect[1],
            "x2": rect[2],
            "y2": rect[3],
            "confidence": float(scores[index]) if index < len(scores) else 0.0,
        })
    return lines


def line_rect(line: dict):
    if all(key in line for key in ["x1", "y1", "x2", "y2"]):
        return [float(line["x1"]), float(line["y1"]), float(line["x2"]), float(line["y2"])]
    return box_to_rect(line.get("bbox"))


def box_to_rect(box):
    if not box:
        return None
    if len(box) == 4 and all(isinstance(value, (int, float)) for value in box):
        return [float(value) for value in box]
    points = []
    for item in box:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            points.append([float(item[0]), float(item[1])])
    if not points:
        return None
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return [min(xs), min(ys), max(xs), max(ys)]


def assign_lines_to_regions(lines: list[dict], regions: list[dict]) -> dict[int, list[dict]]:
    assigned = {int(region["index"]): [] for region in regions}
    for line in lines:
        region = best_region(line["rect"], regions)
        if region:
            assigned[int(region["index"])].append(line)
    for items in assigned.values():
        items.sort(key=lambda item: (item["rect"][1], item["rect"][0]))
    return assigned


def best_region(rect, regions):
    best = None
    best_score = 0.0
    for region in regions:
        region_rect = region.get("coordinate") or region.get("rect")
        score = overlap_ratio(rect, region_rect)
        if score > best_score:
            best = region
            best_score = score
    if best and (best_score >= 0.18 or center_inside(rect, best.get("coordinate") or best.get("rect"))):
        return best
    return None


def overlap_ratio(a, b):
    x1 = max(a[0], b[0])
    y1 = max(a[1], b[1])
    x2 = min(a[2], b[2])
    y2 = min(a[3], b[3])
    if x2 <= x1 or y2 <= y1:
        return 0.0
    return ((x2 - x1) * (y2 - y1)) / max(1.0, (a[2] - a[0]) * (a[3] - a[1]))


def center_inside(a, b):
    cx = (a[0] + a[2]) / 2
    cy = (a[1] + a[3]) / 2
    return b[0] <= cx <= b[2] and b[1] <= cy <= b[3]


def draw_cell_hints(draw: ImageDraw.ImageDraw, cell_boxes: list[list[float]], font) -> None:
    for index, rect in enumerate(cell_boxes, start=1):
        draw.rectangle(rect, outline=(37, 99, 235, 170), width=2, fill=(219, 234, 254, 28))
        draw.text((rect[0] + 2, rect[1] + 1), f"C{index}", fill=(37, 99, 235, 180), font=font)


def draw_regions_and_text(draw: ImageDraw.ImageDraw, regions: list[dict], assigned: dict[int, list[dict]], font, small) -> None:
    for region in sorted(regions, key=lambda value: ((value.get("coordinate") or value.get("rect"))[1], (value.get("coordinate") or value.get("rect"))[0])):
        rect = region.get("coordinate") or region.get("rect")
        index = int(region["index"])
        color = (37, 99, 235, 245) if region.get("kind") == "detail" else (220, 38, 38, 245)
        fill = (219, 234, 254, 36) if region.get("kind") == "detail" else (254, 226, 226, 35)
        draw.rectangle(rect, outline=color, width=4, fill=fill)
        draw.text((rect[0] + 5, rect[1] + 4), f"R{index} items={len(assigned.get(index, []))}", fill=color, font=small)
        for item in assigned.get(index, []):
            text_rect = item["rect"]
            draw.rectangle(text_rect, outline=(22, 163, 74, 180), width=2)
            draw_text_at_bbox(draw, item["text"], text_rect, font)


def draw_page_blocks(draw: ImageDraw.ImageDraw, page_blocks: list[dict], lines: list[dict], font) -> None:
    for block in page_blocks:
        rect = block.get("rect")
        text = str(block.get("text", "")).strip()
        if not rect or not text:
            continue
        draw.rectangle(rect, outline=(126, 34, 206, 210), width=3, fill=(243, 232, 255, 30))
        block_lines = lines_inside_block(text, rect, lines)
        if block_lines:
            for item in block_lines:
                draw_text_at_bbox(draw, item["text"], item["rect"], font)
        else:
            draw_wrapped_block_text(draw, text, rect, font)


def lines_inside_block(block_text: str, block_rect: list[float], lines: list[dict]) -> list[dict]:
    matches = []
    compact_block = compact_text(block_text)
    for line in lines:
        text = str(line.get("text", "")).strip()
        rect = line.get("rect")
        if not text or not rect:
            continue
        if not (compact_text(text) in compact_block or overlap_ratio(rect, block_rect) >= 0.35 or center_inside(rect, block_rect)):
            continue
        if not _same_area(rect, block_rect) and (overlap_ratio(rect, block_rect) > 0 or center_inside(rect, block_rect)):
            matches.append(line)
    matches.sort(key=lambda item: (item["rect"][1], item["rect"][0]))
    return matches


def compact_text(text: str) -> str:
    return "".join(str(text).split())


def _same_area(a: list[float], b: list[float]) -> bool:
    a_area = max(1.0, (a[2] - a[0]) * (a[3] - a[1]))
    b_area = max(1.0, (b[2] - b[0]) * (b[3] - b[1]))
    return abs(a_area - b_area) / max(a_area, b_area) < 0.08


def draw_wrapped_block_text(draw: ImageDraw.ImageDraw, text: str, rect: list[float], font) -> None:
    x1, y1, x2, y2 = rect
    max_width = max(1, x2 - x1 - 4)
    bbox = draw.textbbox((0, 0), "国", font=font)
    line_height = max(16, round((bbox[3] - bbox[1]) * 1.35))
    y = y1 + 2
    for row in wrap_text_by_pixel(draw, text, font, max_width):
        if y > y2 + line_height:
            break
        draw.text((x1 + 2, y), row, fill=(127, 29, 29, 255), font=font)
        y += line_height


def wrap_text_by_pixel(draw: ImageDraw.ImageDraw, text: str, font, max_width: float) -> list[str]:
    rows = []
    current = ""
    for char in text:
        candidate = current + char
        bbox = draw.textbbox((0, 0), candidate, font=font)
        if current and (bbox[2] - bbox[0]) > max_width:
            rows.append(current)
            current = char
        else:
            current = candidate
    if current:
        rows.append(current)
    return rows


def draw_text_at_bbox(draw: ImageDraw.ImageDraw, text: str, rect: list[float], font) -> None:
    if looks_vertical_text(text, rect):
        draw_vertical_text(draw, text, rect, font)
        return
    x1, y1, x2, y2 = rect
    if text_fits(draw, text, font, x2 - x1):
        draw.text((x1 + 2, y1 + 2), text, fill=(15, 23, 42, 255), font=font)
        return
    max_chars = max(2, int(max(24, x2 - x1) / 10))
    y = y1 + 2
    max_rows = max(1, int(max(18, y2 - y1) / 19))
    for part in wrap_text(text, max_chars)[:max_rows]:
        if y < y2 + 18:
            draw.text((x1 + 2, y), part, fill=(15, 23, 42, 255), font=font)
            y += 19


def looks_vertical_text(text: str, rect: list[float]) -> bool:
    text = text.strip()
    x1, y1, x2, y2 = rect
    width = max(1.0, x2 - x1)
    height = max(1.0, y2 - y1)
    if len(text) < 2 or len(text) > 8:
        return False
    if height / width < 1.8:
        return False
    return cjk_ratio(text) >= 0.75


def draw_vertical_text(draw: ImageDraw.ImageDraw, text: str, rect: list[float], font) -> None:
    x1, y1, x2, y2 = rect
    chars = [char for char in text.strip() if not char.isspace()]
    if not chars:
        return
    line_height = max(1, int((y2 - y1) / max(1, len(chars))))
    y = y1 + max(0, ((y2 - y1) - line_height * len(chars)) / 2)
    for char in chars:
        bbox = draw.textbbox((0, 0), char, font=font)
        char_width = bbox[2] - bbox[0]
        char_height = bbox[3] - bbox[1]
        x = x1 + max(0, ((x2 - x1) - char_width) / 2)
        draw.text((x, y + max(0, (line_height - char_height) / 2)), char, fill=(15, 23, 42, 255), font=font)
        y += line_height


def text_fits(draw: ImageDraw.ImageDraw, text: str, font, width: float) -> bool:
    bbox = draw.textbbox((0, 0), text, font=font)
    return (bbox[2] - bbox[0]) <= max(1, width - 4)


def cjk_ratio(text: str) -> float:
    chars = [char for char in text if not char.isspace()]
    if not chars:
        return 0.0
    return sum(1 for char in chars if "\u4e00" <= char <= "\u9fff") / len(chars)


def draw_header(draw: ImageDraw.ImageDraw, source_label: str, region_count: int, cell_count: int, line_count: int, font, small) -> None:
    draw.rectangle([18, 18, 1035, 84], fill=(255, 255, 255, 230), outline=(148, 163, 184, 255))
    draw.text((30, 28), f"{source_label} | regions={region_count} | ppstructure cells={cell_count} | text items={line_count}", fill=(15, 23, 42, 255), font=font)
    draw.text((30, 56), "Red=restore region, Blue=detail/PPStructure cell hint, Green=assigned OCR text bbox, Purple=page block", fill=(71, 85, 105, 255), font=small)


def wrap_text(text: str, max_chars: int) -> list[str]:
    rows = []
    current = ""
    for char in text:
        current += char
        if len(current) >= max_chars:
            rows.append(current)
            current = ""
    if current:
        rows.append(current)
    return rows


def load_font(size: int):
    for path in [
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\simsun.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
    ]:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


if __name__ == "__main__":
    raise SystemExit(main())
