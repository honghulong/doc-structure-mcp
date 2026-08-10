# -*- coding: utf-8 -*-
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from restore_structure_html import build_restore_html, output_path


def test_output_path_defaults_to_input_stem_with_html_suffix():
    class Args:
        out = ""

    assert output_path(Args(), Path(r"D:\sample\page.png")) == Path(r"D:\sample\page.html")


def test_build_restore_html_uses_region_restore_items():
    payload = {
        "pages": [{
            "page_no": 1,
            "width": 400,
            "height": 300,
            "restore": {
                "region_restore": {
                    "regions": [{
                        "index": 1,
                        "kind": "header",
                        "rect": [20, 30, 220, 120],
                        "items": [
                            {"text": "还原文字", "confidence": 0.98, "rect": [40, 60, 160, 84]},
                        ],
                    }],
                },
            },
        }],
    }

    output = build_restore_html(payload)

    assert "restore.region_restore" in output
    assert "R1 items=1" in output
    assert "还原文字" in output
    assert 'class="text-item' in output
