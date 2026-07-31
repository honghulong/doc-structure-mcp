# -*- coding: utf-8 -*-
"""横切法：顶部区域按行切割，每行单独OCR"""
import sys, io, cv2, numpy as np, time, fitz
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
from paddleocr import PaddleOCR

doc = fitz.open(r"D:\hongmengProjects\doc-structure-mcp\tests\fixtures\test_scanned_doc.pdf")
mat = fitz.Matrix(300/72, 300/72)  # 高分辨率便于小字识别
pix = doc[0].get_pixmap(matrix=mat)
img = cv2.imdecode(np.frombuffer(pix.tobytes("png"), np.uint8), cv2.IMREAD_COLOR)
H, W = img.shape[:2]
doc.close()

# 先用默认参数检测竖排框，获取Y坐标切分点
ocr = PaddleOCR(text_detection_model_name="PP-OCRv5_mobile_det", text_recognition_model_name="PP-OCRv5_mobile_rec",
                use_textline_orientation=True, lang="ch", text_det_thresh=0.3, text_det_box_thresh=0.5)
res = list(ocr.predict(img))

# 从竖排框获取Y坐标
y_cuts = set()
for r in res:
    if r is None: continue
    texts = r.get("rec_texts",[]) if isinstance(r,dict) else getattr(r,"rec_texts",[])
    polys = r.get("dt_polys",[]) if isinstance(r,dict) else getattr(r,"dt_polys",[])
    for i in range(len(texts)):
        t = texts[i].strip()
        poly = polys[i] if i < len(polys) else None
        if not t or len(t) < 3: continue
        if poly is not None and len(poly) >= 4:
            ys = [float(p[1]) for p in poly if len(p) >= 2]
            if not ys: continue
            y1, y2 = min(ys), max(ys)
            w = max([float(p[0]) for p in poly if len(p) >= 2]) - min([float(p[0]) for p in poly if len(p) >= 2])
            h = y2 - y1
            # 竖排框：h > w*2 且在顶部
            if h > w * 2 and y1 < H * 0.35:
                n_chars = len(t)  # 字数
                slice_h = h / n_chars
                for idx in range(n_chars):
                    cut_y = int(y1 + idx * slice_h)
                    y_cuts.add(cut_y)

y_cuts = sorted(y_cuts)
print(f"检测到 {len(y_cuts)} 行切分点: {y_cuts}")

# 按Y切分行，每行单独OCR
print(f"\n=== 横切结果 ===")
ocr2 = PaddleOCR(text_detection_model_name="PP-OCRv5_mobile_det", text_recognition_model_name="PP-OCRv5_mobile_rec",
                 use_textline_orientation=True, lang="ch", text_det_thresh=0.3, text_det_box_thresh=0.5)

for idx in range(len(y_cuts)):
    y_start = max(0, y_cuts[idx] - 15)  # 上下扩展15px
    y_end = min(H, y_cuts[idx] + 85) if idx + 1 < len(y_cuts) else min(H, y_cuts[idx] + 100)
    
    # 相邻切点太近就跳过
    if idx > 0 and y_start - y_cuts[idx-1] < 20:
        continue
    
    slice_img = img[y_start:y_end, :]
    if slice_img.shape[0] < 15:
        continue
    
    sr = list(ocr2.predict(slice_img))
    texts = []
    for s in sr:
        if s is None: continue
        st = s.get("rec_texts",[]) if isinstance(s,dict) else getattr(s,"rec_texts",[])
        ss = s.get("rec_scores",[]) if isinstance(s,dict) else getattr(s,"rec_scores",[])
        for j in range(len(st)):
            if float(ss[j]) if j < len(ss) else 0 > 0.5:
                texts.append(st[j].strip())
    
    line = " ".join(texts) if texts else "(空)"
    print(f"  切片 y={y_start}~{y_end}: {line}")
