# -*- coding: utf-8 -*-
import sys
from pathlib import Path

from docx import Document

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from docx_builder import build_honest_docx


def test_honest_docx_places_distant_blocks_in_different_cells(tmp_path):
    pages = [{
        "page": 1,
        "ocr_text": "左侧\n右侧",
        "ocr_lines": [
            {"text": "左侧", "confidence": 0.99, "bbox": [10, 10, 80, 10, 80, 40, 10, 40]},
            {"text": "右侧", "confidence": 0.99, "bbox": [900, 10, 980, 10, 980, 40, 900, 40]},
        ],
    }]
    output = tmp_path / "honest.docx"
    output.write_bytes(build_honest_docx(pages, grid_cols=12, grid_rows=8))

    doc = Document(str(output))
    cells = [cell.text.strip() for table in doc.tables for row in table.rows for cell in row.cells]

    assert "左侧" in cells
    assert "右侧" in cells
    assert "左侧 右侧" not in cells
