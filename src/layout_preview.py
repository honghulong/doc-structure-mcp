import base64
import html
import io
from pathlib import Path

from docx import Document
from docx.shared import Pt


def build_overlay_html(result, image_root=None, min_confidence=0.3):
    """Build a browser preview that places OCR text by absolute bbox position."""
    pages = result.get("pages", [])
    case = result.get("case", "")
    model_name = result.get("model_name", "")
    image_root = Path(image_root) if image_root else None

    body_parts = []
    for page in pages:
        width = int(page.get("width") or 1)
        height = int(page.get("height") or 1)
        image_src = _image_data_uri(image_root, page.get("image_file"))
        line_parts = []
        for line in _normalized_lines(page.get("lines", [])):
            if line["confidence"] < min_confidence or not line["text"].strip():
                continue
            vertical = _looks_vertical_text(line)
            left = 100 * line["x1"] / width
            top = 100 * line["y1"] / height
            w = 100 * max(1.0, line["x2"] - line["x1"]) / width
            h = 100 * max(1.0, line["y2"] - line["y1"]) / height
            if vertical:
                size = max(10, min(22, int((line["x2"] - line["x1"]) * 0.56)))
                extra_class = " vertical"
            else:
                size = max(10, min(28, int((line["y2"] - line["y1"]) * 0.45)))
                extra_class = ""
            text = html.escape(line["text"])
            conf = html.escape(f'{line["confidence"]:.3f}')
            line_parts.append(
                f'<div class="ocr-line{extra_class}" title="conf={conf}" '
                f'style="left:{left:.4f}%;top:{top:.4f}%;width:{w:.4f}%;'
                f'height:{h:.4f}%;font-size:{size}px">{text}</div>'
            )

        background = (
            f'<img class="page-image" src="{image_src}" alt="page image">'
            if image_src else ""
        )
        body_parts.append(
            f'<section class="page" style="aspect-ratio:{width}/{height}">'
            f"{background}{''.join(line_parts)}</section>"
        )

    title = html.escape(" ".join(part for part in [case, model_name] if part))
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>{title} OCR overlay</title>
<style>
body {{
  margin: 0;
  padding: 24px;
  background: #f3f4f6;
  color: #111827;
  font-family: Arial, "Microsoft YaHei", sans-serif;
}}
.toolbar {{
  max-width: 1180px;
  margin: 0 auto 16px;
  font-size: 14px;
}}
.page {{
  position: relative;
  max-width: 1180px;
  margin: 0 auto 28px;
  background: white;
  box-shadow: 0 2px 12px rgba(15, 23, 42, 0.16);
  overflow: hidden;
}}
.page-image {{
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: contain;
}}
.ocr-line {{
  position: absolute;
  box-sizing: border-box;
  border: 1px solid rgba(220, 38, 38, 0.45);
  background: rgba(255, 247, 237, 0.42);
  color: rgba(17, 24, 39, 0.88);
  line-height: 1.05;
  white-space: nowrap;
  overflow: visible;
}}
.ocr-line.vertical {{
  writing-mode: vertical-rl;
  text-orientation: upright;
  display: flex;
  align-items: center;
  justify-content: center;
  white-space: normal;
  line-height: 1.08;
}}
</style>
</head>
<body>
<div class="toolbar">{title} | overlay uses original OCR image coordinates.</div>
{''.join(body_parts)}
</body>
</html>
"""


def build_plain_docx(result, min_confidence=0.3):
    """Build an editable DOCX text draft from OCR lines, grouped only by rows.

    Tall, narrow OCR boxes are emitted one character per line. This keeps
    vertical labels readable in Word without forcing the whole plain-document
    fallback into helper tables.
    """
    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "SimSun"
    style.font.size = Pt(10.5)

    for page_index, page in enumerate(result.get("pages", [])):
        if page_index:
            doc.add_page_break()
        lines = [
            line for line in _normalized_lines(page.get("lines", []))
            if line["confidence"] >= min_confidence and line["text"].strip()
        ]
        for row in _cluster_rows(lines):
            text = "  ".join(_plain_docx_text(item) for item in sorted(row, key=lambda x: x["x1"]))
            if text:
                doc.add_paragraph(text)

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _normalized_lines(lines):
    normalized = []
    for line in lines:
        bbox = line.get("bbox", [])
        if len(bbox) < 4:
            continue
        if isinstance(bbox[0], (list, tuple)):
            xs = [float(point[0]) for point in bbox if len(point) >= 2]
            ys = [float(point[1]) for point in bbox if len(point) >= 2]
        else:
            xs = [float(x) for x in bbox[0::2]]
            ys = [float(y) for y in bbox[1::2]]
        if not xs or not ys:
            continue
        normalized.append({
            "text": str(line.get("text", "")),
            "confidence": float(line.get("confidence", 0)),
            "x1": min(xs),
            "y1": min(ys),
            "x2": max(xs),
            "y2": max(ys),
            "cy": (min(ys) + max(ys)) / 2,
        })
    return normalized


def _cluster_rows(lines):
    rows = []
    for line in sorted(lines, key=lambda item: item["cy"]):
        height = max(1.0, line["y2"] - line["y1"])
        threshold = max(8.0, height * 0.55)
        if rows and abs(line["cy"] - _row_center(rows[-1])) <= threshold:
            rows[-1].append(line)
        else:
            rows.append([line])
    return rows


def _looks_vertical_text(line):
    text = line["text"].strip()
    width = max(1.0, line["x2"] - line["x1"])
    height = max(1.0, line["y2"] - line["y1"])
    if len(text) < 2 or len(text) > 8:
        return False
    if height / width < 1.8:
        return False
    return _cjk_ratio(text) >= 0.75


def _plain_docx_text(line):
    text = line["text"].strip()
    if not _looks_vertical_text(line):
        return text
    return "\n".join(char for char in text if not char.isspace())


def _cjk_ratio(text):
    chars = [char for char in text if not char.isspace()]
    if not chars:
        return 0.0
    cjk_count = sum(1 for char in chars if "\u4e00" <= char <= "\u9fff")
    return cjk_count / len(chars)


def _row_center(row):
    return sum(item["cy"] for item in row) / len(row)


def _image_data_uri(image_root, image_file):
    if not image_root or not image_file:
        return ""
    path = image_root / image_file
    if not path.exists():
        return ""
    suffix = path.suffix.lower()
    mime = "image/jpeg" if suffix in {".jpg", ".jpeg"} else "image/png"
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{data}"
