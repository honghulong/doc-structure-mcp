# -*- coding: utf-8 -*-
"""
Colab 对比实验：PP-Structure recovery → DOCX

在 Google Colab 上运行此脚本，对比：
  方案 A: 本项目 docx_builder 的排版结果
  方案 B: PP-Structure recovery=True 的排版结果

使用方法：
  1. 上传 test_page1.png 到 Colab 当前目录
  2. 逐块运行下面的代码
"""
# %% 安装依赖
"""
!pip install paddlepaddle-gpu -q
!pip install "paddleocr<3.0" -q
!pip install python-docx PyMuPDF -q
"""

# %% 导入
"""
import cv2, time, sys, os, io, numpy as np
from paddleocr import PaddleOCR, PPStructure, save_structure_res
from paddleocr.ppstructure.recovery.recovery_to_doc import sorted_layout_boxes, convert_info_docx
import matplotlib.pyplot as plt
"""

# %% 加载图片
"""
IMG = "test_page1.png"
img = cv2.imread(IMG)
H, W = img.shape[:2]
print(f"图片: {W}x{H}")
"""

# %% 方案A: PaddleOCR + docx_builder
"""
# 需要把 docx_builder.py 上传到 Colab
from docx_builder import build_docx

ocr = PaddleOCR(
    text_detection_model_name="PP-OCRv5_mobile_det",
    text_recognition_model_name="PP-OCRv5_mobile_rec",
    use_textline_orientation=True, lang="ch",
    text_det_thresh=0.3, text_det_box_thresh=0.5,
)
t0 = time.time()
res = list(ocr.predict(img))
print(f"OCR: {len(res)} boxes, {time.time()-t0:.1f}s")

lines = []
for r in res:
    if r is None: continue
    texts = r.get("rec_texts",[]) if isinstance(r,dict) else getattr(r,"rec_texts",[])
    scores = r.get("rec_scores",[]) if isinstance(r,dict) else getattr(r,"rec_scores",[])
    polys = r.get("dt_polys",[]) if isinstance(r,dict) else getattr(r,"dt_polys",[])
    for i in range(len(texts)):
        t = texts[i].strip(); conf = float(scores[i]) if i < len(scores) else 0.0
        poly = polys[i] if i < len(polys) else None
        if not t or conf < 0.3: continue
        if poly is not None and len(poly) >= 4:
            bbox = []
            for pt in poly:
                if len(pt) >= 2: bbox.extend([float(pt[0]), float(pt[1])])
            if bbox:
                lines.append({"text": t, "confidence": conf, "bbox": bbox})

pages = [{"page":1, "ocr_lines":lines, "ocr_text":"\\n".join(l["text"] for l in lines)}]
docx_bytes = build_docx(pages)
with open("/content/方案A_docx_builder.docx", "wb") as f:
    f.write(docx_bytes)
print(f"方案A输出: {len(docx_bytes)} bytes")
"""

# %% 方案B: PP-Structure recovery
"""
table_engine = PPStructure(recovery=True, lang='ch')
t0 = time.time()
result = table_engine(img)
print(f"PPStructure: {time.time()-t0:.1f}s, {len(result)} regions")

save_folder = "/content/output_b"
os.makedirs(save_folder, exist_ok=True)
for region in result:
    print(f"  type={region['type']}, bbox={region['bbox'][:4]}")
    if region['type'] == 'table':
        print(f"    table html: {region['res']['html'][:100]}")

# 恢复为 DOCX
convert_info_docx(img, result, save_folder, "方案B_ppstructure")
print(f"方案B输出: /content/output_b/")
"""

# %% 对比
"""
print("=== 对比 ===")
print("方案A: docx_builder.docx - 本项目排版方法")
print("方案B: output_b/ - PP-Structure 官方排版方法")
print("人工比较两份 DOCX 的排版效果（红头、标题、正文对齐）")
"""
