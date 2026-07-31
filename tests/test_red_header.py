# -*- coding: utf-8 -*-
"""测试不同检测阈值对红头文字的识别效果"""
import sys, io, cv2, numpy as np, time, fitz
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from paddleocr import PaddleOCR

doc = fitz.open(r"D:\hongmengProjects\doc-structure-mcp\tests\fixtures\test_scanned_doc.pdf")
mat = fitz.Matrix(200/72, 200/72)
pix = doc[0].get_pixmap(matrix=mat)
nparr = np.frombuffer(pix.tobytes("png"), np.uint8)
img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
doc.close()

# 实验3组参数
params = [
    ("默认", 0.3, 0.5, 1.6),
    ("低阈值+扩大框", 0.1, 0.3, 2.5),
    ("更低阈值+更大框", 0.05, 0.2, 3.0),
]

for name, det_thresh, box_thresh, unclip in params:
    print(f"\n=== {name}: det={det_thresh} box={box_thresh} unclip={unclip} ===")
    ocr = PaddleOCR(
        text_detection_model_name="PP-OCRv5_mobile_det",
        text_recognition_model_name="PP-OCRv5_mobile_rec",
        use_textline_orientation=True, lang="ch",
        text_det_thresh=det_thresh,
        text_det_box_thresh=box_thresh,
        text_det_unclip_ratio=unclip,
    )
    t0 = time.time()
    res = list(ocr.predict(img))
    elapsed = time.time() - t0

    total = 0
    for r in res:
        if r is None: continue
        texts = r.get("rec_texts",[]) if isinstance(r,dict) else getattr(r,"rec_texts",[])
        scores = r.get("rec_scores",[]) if isinstance(r,dict) else getattr(r,"rec_scores",[])
        total += len(texts)
        for i in range(min(len(texts), 25)):
            t = texts[i]
            s = scores[i]
            if s > 0.5:
                print(f"  {s:.4f}  {t}")
    print(f"  总行数: {total}, 耗时: {elapsed:.1f}s")
