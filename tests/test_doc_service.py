# -*- coding: utf-8 -*-
"""Test doc_service 8768 → DOCX with v3_server_det"""
import sys, io, fitz, requests, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

PDF = r"D:\hongmengProjects\doc-structure-mcp\tests\fixtures\test_scanned_doc.pdf"
DOCX_OUT = r"D:\hongmengProjects\doc-structure-mcp\tests\output\docx_v3det.docx"

# 仅第1页 → DOCX
doc = fitz.open(PDF)
mat = fitz.Matrix(200/72, 200/72)
pix = doc[0].get_pixmap(matrix=mat)
img_bytes = pix.tobytes("png")
doc.close()

print("[1] 发送 /to-docx (仅第1页)...")
t0 = time.time()
r = requests.post("http://127.0.0.1:8768/to-docx",
                  files={"file": ("page1.png", img_bytes, "image/png")},
                  data={"page_limit": 1}, timeout=600)

if "octet-stream" in r.headers.get("content-type", ""):
    with open(DOCX_OUT, "wb") as f:
        f.write(r.content)
    print(f"  OK: {time.time()-t0:.1f}s, {len(r.content)} bytes")
    print(f"  {DOCX_OUT}")
else:
    print(f"  ERROR: {r.text[:500]}")
