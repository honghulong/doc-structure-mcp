# -*- coding: utf-8 -*-
"""Test OCR-8767 on scanned PDF, print full results."""
import sys, io, json, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

RESULT_FILE = r"D:\hongmengProjects\doc-structure-mcp\logs\pdf_test_result.json"
PDF_PATH = r"D:\hongmengProjects\doc-structure-mcp\tests\fixtures\test_scanned_doc.pdf"
OCR_URL = "http://127.0.0.1:8767/ocr"

print("=" * 60)
print(" 测试扫描 PDF → OCR (8767)")
print(f" 文件: {PDF_PATH}")
print("=" * 60)

# Check if service is up
import requests
try:
    r = requests.get(f"{OCR_URL.replace('/ocr','')}/health", timeout=3)
    print(f" 服务状态: {r.json().get('status')}")
except Exception as e:
    print(f" 服务未运行: {e}")
    sys.exit(1)

# Send OCR request
print("\n--- 发送 OCR 请求 ---")
t0 = time.time()
with open(PDF_PATH, "rb") as f:
    r = requests.post(OCR_URL, files={"file": f}, timeout=120)
elapsed = time.time() - t0

d = r.json()
status = d.get("status")
lines = d.get("ocr_raw", [])
text = d.get("ocr_text", "")
timing = d.get("timing", {})

print(f" HTTP状态: {r.status_code}")
print(f" 状态: {status}")
print(f" 识别行数: {len(lines)}")
print(f" 耗时: {elapsed:.2f}s (process={timing.get('process_seconds','?')}s)")

# Save result for inspection
with open(RESULT_FILE, "w", encoding="utf-8") as f:
    json.dump(d, f, ensure_ascii=False, indent=2)
print(f" 结果已保存: {RESULT_FILE}")

if status == "ok" and lines:
    print(f"\n{'='*60}")
    print(" 全文识别结果")
    print(f"{'='*60}")
    print(text)

    print(f"\n{'='*60}")
    print(f" 前20行详情 (conf, text)")
    print(f"{'='*60}")
    for i, l in enumerate(lines[:20]):
        print(f"  [{i:2d}] conf={l['confidence']:.4f}  {l['text']}")
    if len(lines) > 20:
        print(f"  ... 还有 {len(lines)-20} 行")
else:
    print(f"\n 错误: {d.get('error', 'unknown')}")
