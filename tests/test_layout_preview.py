import sys
from pathlib import Path

from docx import Document

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from layout_preview import build_overlay_html, build_plain_docx


def test_overlay_html_uses_absolute_percent_positions():
    result = {
        "case": "case1",
        "model_name": "server",
        "pages": [{
            "page_no": 1,
            "width": 1000,
            "height": 500,
            "lines": [{"text": "ABC", "confidence": 0.99, "bbox": [[100, 50], [200, 50], [200, 90], [100, 90]]}],
        }],
    }

    output = build_overlay_html(result)

    assert "position: absolute" in output
    assert "left:10.0000%" in output
    assert "top:10.0000%" in output
    assert "ABC" in output


def test_overlay_html_marks_tall_cjk_text_as_vertical():
    result = {
        "pages": [{
            "width": 1000,
            "height": 500,
            "lines": [{"text": "\u5907\u6ce8", "confidence": 0.99, "bbox": [[10, 10], [50, 10], [50, 100], [10, 100]]}],
        }],
    }

    output = build_overlay_html(result)

    assert 'class="ocr-line vertical"' in output
    assert "writing-mode: vertical-rl" in output


def test_plain_docx_keeps_separate_rows(tmp_path):
    result = {
        "pages": [{
            "lines": [
                {"text": "row one", "confidence": 0.99, "bbox": [10, 10, 80, 10, 80, 30, 10, 30]},
                {"text": "row two", "confidence": 0.99, "bbox": [10, 80, 80, 80, 80, 100, 10, 100]},
            ],
        }],
    }
    path = tmp_path / "plain.docx"
    path.write_bytes(build_plain_docx(result))

    paragraphs = [p.text for p in Document(str(path)).paragraphs]

    assert "row one" in paragraphs
    assert "row two" in paragraphs
