# -*- coding: utf-8 -*-
"""
Evaluate and visualize a Colab OCR lab result.

Example:
  python scripts/evaluate_ocr_lab_result.py ^
    --result tests/output/ocr_lab/red_header_v1/ocr_result.json ^
    --out tests/output/ocr_lab/red_header_v1_local
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def polygon_to_rect(points: list[list[float]]) -> tuple[float, float, float, float]:
    xs = [float(p[0]) for p in points]
    ys = [float(p[1]) for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def line_features(line: dict) -> dict:
    x1, y1, x2, y2 = polygon_to_rect(line["bbox"])
    width = max(1.0, x2 - x1)
    height = max(1.0, y2 - y1)
    return {
        "text": line.get("text", ""),
        "confidence": float(line.get("confidence", 0.0)),
        "x1": x1,
        "y1": y1,
        "x2": x2,
        "y2": y2,
        "width": width,
        "height": height,
        "is_tall": height > width * 2 and height > 40,
    }


def cluster_rows(lines: list[dict], y_threshold: float = 18.0) -> list[list[dict]]:
    features = sorted((line_features(line) for line in lines), key=lambda item: (item["y1"], item["x1"]))
    rows: list[list[dict]] = []
    for item in features:
        cy = (item["y1"] + item["y2"]) / 2
        if not rows:
            rows.append([item])
            continue
        last = rows[-1]
        last_cy = sum((x["y1"] + x["y2"]) / 2 for x in last) / len(last)
        if abs(cy - last_cy) <= y_threshold:
            last.append(item)
        else:
            rows.append([item])
    for row in rows:
        row.sort(key=lambda item: item["x1"])
    return rows


def evaluate_page(page: dict) -> dict:
    lines = page.get("lines", [])
    features = [line_features(line) for line in lines]
    confidences = [item["confidence"] for item in features]
    tall = [item for item in features if item["is_tall"]]
    low_conf = [item for item in features if item["confidence"] < 0.75]
    rows = cluster_rows(lines)
    sorted_text = "\n".join(" ".join(item["text"] for item in row) for row in rows)
    return {
        "page_no": page["page_no"],
        "line_count": len(lines),
        "row_count": len(rows),
        "avg_confidence": round(sum(confidences) / len(confidences), 4) if confidences else 0,
        "low_confidence_count": len(low_conf),
        "tall_box_count": len(tall),
        "sorted_text": sorted_text,
        "warnings": build_warnings(page, features, rows),
    }


def build_warnings(page: dict, features: list[dict], rows: list[list[dict]]) -> list[str]:
    warnings = []
    if not features:
        return ["no OCR lines"]
    if len(features) < 8:
        warnings.append("line_count_low")
    low_ratio = len([item for item in features if item["confidence"] < 0.75]) / len(features)
    if low_ratio > 0.25:
        warnings.append("many_low_confidence_lines")
    tall_ratio = len([item for item in features if item["is_tall"]]) / len(features)
    if tall_ratio > 0.2:
        warnings.append("many_tall_boxes_check_vertical_or_merged_text")
    if rows and max(len(row) for row in rows) >= 6:
        warnings.append("dense_row_maybe_table_or_bad_line_merge")
    if page.get("duration_ms", 0) > 30000:
        warnings.append("slow_page")
    return warnings


def draw_boxes(result_dir: Path, page: dict, output_dir: Path) -> str | None:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception:
        return None

    image_path = result_dir / page["image_file"]
    if not image_path.exists():
        return None
    image = Image.open(image_path).convert("RGB")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("arial.ttf", 16)
    except Exception:
        font = None
    for index, line in enumerate(page.get("lines", []), start=1):
        x1, y1, x2, y2 = polygon_to_rect(line["bbox"])
        draw.rectangle((x1, y1, x2, y2), outline="red", width=2)
        draw.text((x1, max(0, y1 - 18)), str(index), fill="blue", font=font)
    output_name = f"page_{page['page_no']:03d}_boxes.png"
    output_path = output_dir / output_name
    image.save(output_path)
    return output_name


def grade(summary: dict) -> str:
    if summary["line_count"] == 0:
        return "D"
    if summary["avg_confidence"] >= 0.90 and summary["low_confidence_count"] <= 2 and len(summary["warnings"]) <= 1:
        return "A"
    if summary["avg_confidence"] >= 0.82 and len(summary["warnings"]) <= 3:
        return "B"
    if summary["avg_confidence"] >= 0.70:
        return "C"
    return "D"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result", required=True, help="ocr_result.json downloaded from Colab")
    parser.add_argument("--out", default=None, help="local evaluation output directory")
    args = parser.parse_args()

    result_path = Path(args.result).resolve()
    result_dir = result_path.parent
    output_dir = Path(args.out).resolve() if args.out else result_dir / "local_eval"
    output_dir.mkdir(parents=True, exist_ok=True)

    payload = json.loads(result_path.read_text(encoding="utf-8-sig"))
    page_summaries = []
    for page in payload.get("pages", []):
        summary = evaluate_page(page)
        summary["box_image"] = draw_boxes(result_dir, page, output_dir)
        summary["grade"] = grade(summary)
        page_summaries.append(summary)
        (output_dir / f"page_{page['page_no']:03d}_sorted.txt").write_text(
            summary["sorted_text"], encoding="utf-8"
        )

    report = {
        "case": payload.get("case"),
        "input_file": payload.get("input_file"),
        "model": payload.get("model"),
        "dpi": payload.get("dpi"),
        "total_duration_ms": payload.get("total_duration_ms"),
        "pages": page_summaries,
    }
    (output_dir / "evaluation.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    markdown = [
        f"# Local OCR Evaluation: {report['case']}",
        "",
        f"- input: `{report['input_file']}`",
        f"- model: `{report['model']}`",
        f"- dpi: `{report['dpi']}`",
        f"- total_duration_ms: `{report['total_duration_ms']}`",
        "",
        "| Page | Grade | Lines | Rows | Avg Conf | Low Conf | Tall Boxes | Warnings | Box Image |",
        "|---:|---|---:|---:|---:|---:|---:|---|---|",
    ]
    for page in page_summaries:
        markdown.append(
            f"| {page['page_no']} | {page['grade']} | {page['line_count']} | {page['row_count']} | "
            f"{page['avg_confidence']} | {page['low_confidence_count']} | {page['tall_box_count']} | "
            f"{', '.join(page['warnings']) or '-'} | {page['box_image'] or '-'} |"
        )
    markdown.extend([
        "",
        "## How To Judge",
        "",
        "- A: OCR text and bbox are good enough to continue service experiments.",
        "- B: usable, but inspect sorted text and box image before trusting layout recovery.",
        "- C: useful as reference text only; improve model, DPI, preprocessing, or layout rules.",
        "- D: not usable for this sample.",
        "",
        "Manual check remains required: compare the box image and sorted text with the original page.",
    ])
    report_path = output_dir / "evaluation.md"
    report_path.write_text("\n".join(markdown), encoding="utf-8")
    print(f"Evaluation report: {report_path}")


if __name__ == "__main__":
    main()
