# -*- coding: utf-8 -*-
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_evaluate_colab_result_generates_report(tmp_path):
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    payload = {
        "case": "unit_case",
        "input_file": "sample.png",
        "dpi": 220,
        "model": {"det": "PP-OCRv5_server_det", "rec": "PP-OCRv5_server_rec"},
        "total_duration_ms": 1234,
        "pages": [
            {
                "page_no": 1,
                "image_file": "missing.png",
                "width": 1000,
                "height": 1200,
                "duration_ms": 500,
                "plain_text": "测试\n文本",
                "lines": [
                    {
                        "text": "测试",
                        "confidence": 0.96,
                        "bbox": [[10, 10], [100, 10], [100, 40], [10, 40]],
                    },
                    {
                        "text": "文本",
                        "confidence": 0.93,
                        "bbox": [[10, 60], [100, 60], [100, 90], [10, 90]],
                    },
                ],
            }
        ],
    }
    result_path = case_dir / "ocr_result.json"
    result_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    out_dir = tmp_path / "eval"
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "evaluate_ocr_lab_result.py"),
            "--result",
            str(result_path),
            "--out",
            str(out_dir),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )

    assert "Evaluation report" in completed.stdout
    report = (out_dir / "evaluation.md").read_text(encoding="utf-8")
    sorted_text = (out_dir / "page_001_sorted.txt").read_text(encoding="utf-8")
    assert "unit_case" in report
    assert "| 1 |" in report
    assert "测试" in sorted_text
    assert "文本" in sorted_text
