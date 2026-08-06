# -*- coding: utf-8 -*-
"""
GPU server local structure pipeline diagnostic.

Run on the GPU server from the project root:

    .venv\\Scripts\\python.exe scripts\\test_gpu_structure_local.py --input tests\\fixtures\\test_page2.pdf --dpi 220

This script does not start Flask. It checks whether the local Python
environment can import and create PPStructureV3 / TableCellsDetection, then
runs one rendered page and writes raw JSON diagnostics under logs\\structure_diag.
"""
from __future__ import annotations

import argparse
import importlib
import importlib.metadata as metadata
import json
import os
import sys
import time
import traceback
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

os.environ.setdefault("PPOCR_HOME", str(ROOT / ".paddleocr_home"))
os.environ.setdefault("PADDLEX_HOME", str(ROOT / ".paddlex_home"))


def version_of(package: str) -> str:
    try:
        return metadata.version(package)
    except Exception as exc:
        return f"missing ({exc})"


def jsonable(obj):
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, dict):
        return {str(k): jsonable(v) for k, v in obj.items() if k != "img"}
    if isinstance(obj, (list, tuple)):
        return [jsonable(v) for v in obj]
    if hasattr(obj, "tolist"):
        return jsonable(obj.tolist())
    if hasattr(obj, "json"):
        return jsonable(obj.json)
    return str(obj)


def render_first_page(input_path: Path, dpi: int, out_dir: Path) -> Path:
    import cv2
    import fitz

    if input_path.suffix.lower() == ".pdf":
        doc = fitz.open(str(input_path))
        try:
            page = doc[0]
            pix = page.get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72), alpha=False)
            image_path = out_dir / "page_001.png"
            pix.save(str(image_path))
            return image_path
        finally:
            doc.close()

    image = cv2.imread(str(input_path))
    if image is None:
        raise RuntimeError(f"cannot read image: {input_path}")
    image_path = out_dir / "page_001.png"
    cv2.imwrite(str(image_path), image)
    return image_path


def try_imports() -> dict:
    names = [
        "paddle",
        "paddleocr",
        "paddlex",
        "cv2",
        "fitz",
        "PIL",
        "lxml",
        "openpyxl",
    ]
    result = {}
    for name in names:
        try:
            importlib.import_module(name)
            result[name] = "ok"
        except Exception as exc:
            result[name] = repr(exc)
    return result


def make_ppstructure_v3():
    from paddleocr import PPStructureV3

    attempts = [
        {
            "use_doc_orientation_classify": False,
            "use_doc_unwarping": False,
            "use_textline_orientation": False,
            "use_formula_recognition": False,
            "use_seal_recognition": False,
            "use_table_recognition": True,
            "use_wired_table_cells_trans_to_html": True,
            "use_ocr_results_with_table_cells": True,
            "device": "gpu",
        },
        {
            "use_doc_orientation_classify": False,
            "use_doc_unwarping": False,
            "use_textline_orientation": False,
            "use_formula_recognition": False,
            "use_seal_recognition": False,
            "use_table_recognition": True,
            "device": "gpu",
        },
        {"device": "gpu"},
        {},
    ]
    errors = []
    for kwargs in attempts:
        started = time.time()
        try:
            return PPStructureV3(**kwargs), kwargs, errors, round(time.time() - started, 3)
        except Exception as exc:
            errors.append({"kwargs": kwargs, "error": repr(exc), "traceback": traceback.format_exc()})
    raise RuntimeError(json.dumps(errors, ensure_ascii=False, indent=2))


def make_table_cells_detection():
    from paddleocr import TableCellsDetection

    attempts = [
        {"model_name": "RT-DETR-L_wired_table_cell_det", "device": "gpu"},
        {"model_name": "RT-DETR-L_wired_table_cell_det"},
        {},
    ]
    errors = []
    for kwargs in attempts:
        started = time.time()
        try:
            return TableCellsDetection(**kwargs), kwargs, errors, round(time.time() - started, 3)
        except Exception as exc:
            errors.append({"kwargs": kwargs, "error": repr(exc), "traceback": traceback.format_exc()})
    raise RuntimeError(json.dumps(errors, ensure_ascii=False, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="tests/fixtures/test_page2.pdf")
    parser.add_argument("--dpi", type=int, default=220)
    args = parser.parse_args()

    input_path = (ROOT / args.input).resolve() if not Path(args.input).is_absolute() else Path(args.input)
    out_dir = ROOT / "logs" / "structure_diag" / time.strftime("%Y%m%d_%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)

    report = {
        "input": str(input_path),
        "output_dir": str(out_dir),
        "versions": {
            "python": sys.version,
            "paddlepaddle": version_of("paddlepaddle"),
            "paddleocr": version_of("paddleocr"),
            "paddlex": version_of("paddlex"),
        },
        "imports": try_imports(),
        "steps": [],
    }

    try:
        image_path = render_first_page(input_path, args.dpi, out_dir)
        report["page_image"] = str(image_path)
        report["steps"].append({"step": "render_first_page", "status": "ok"})

        structure, structure_kwargs, structure_errors, load_seconds = make_ppstructure_v3()
        report["ppstructure_v3"] = {
            "status": "created",
            "kwargs": structure_kwargs,
            "previous_errors": structure_errors,
            "load_seconds": load_seconds,
        }
        started = time.time()
        structure_results = [jsonable(item) for item in structure.predict(str(image_path))]
        report["ppstructure_v3"]["predict_seconds"] = round(time.time() - started, 3)
        (out_dir / "ppstructure_v3_raw.json").write_text(
            json.dumps(structure_results, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        report["ppstructure_v3"]["raw_json"] = str(out_dir / "ppstructure_v3_raw.json")
        report["steps"].append({"step": "ppstructure_v3_predict", "status": "ok"})

        cells, cells_kwargs, cells_errors, cells_load_seconds = make_table_cells_detection()
        report["table_cells_detection"] = {
            "status": "created",
            "kwargs": cells_kwargs,
            "previous_errors": cells_errors,
            "load_seconds": cells_load_seconds,
        }
        started = time.time()
        cells_results = [jsonable(item) for item in cells.predict(str(image_path), threshold=0.3, batch_size=1)]
        report["table_cells_detection"]["predict_seconds"] = round(time.time() - started, 3)
        (out_dir / "table_cells_detection_raw.json").write_text(
            json.dumps(cells_results, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        report["table_cells_detection"]["raw_json"] = str(out_dir / "table_cells_detection_raw.json")
        report["steps"].append({"step": "table_cells_detection_predict", "status": "ok"})
        report["status"] = "ok"
    except Exception as exc:
        report["status"] = "error"
        report["error"] = repr(exc)
        report["traceback"] = traceback.format_exc()
        print(report["traceback"])
    finally:
        report_path = out_dir / "diagnostic_report.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"diagnostic report: {report_path}")
        print(json.dumps({k: report.get(k) for k in ["status", "error", "versions", "imports"]}, ensure_ascii=False, indent=2))

    return 0 if report.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
