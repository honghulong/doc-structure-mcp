# -*- coding: utf-8 -*-
"""红头文字后处理：按坐标合并间距大的单字为完整文本行"""
import sys, io, cv2, numpy as np, time, fitz
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from paddleocr import PaddleOCR

doc = fitz.open(r"D:\hongmengProjects\doc-structure-mcp\tests\fixtures\test_scanned_doc.pdf")
mat = fitz.Matrix(200/72, 200/72)
pix = doc[0].get_pixmap(matrix=mat)
nparr = np.frombuffer(pix.tobytes("png"), np.uint8)
img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
PAGE_H, PAGE_W = img.shape[:2]
doc.close()

ocr = PaddleOCR(
    text_detection_model_name="PP-OCRv5_mobile_det",
    text_recognition_model_name="PP-OCRv5_mobile_rec",
    use_textline_orientation=True, lang="ch",
    text_det_thresh=0.3, text_det_box_thresh=0.5,
)

t0 = time.time()
res = list(ocr.predict(img))
print(f"OCR: {time.time()-t0:.1f}s")

# 提取带坐标的原始行
raw_lines = []
for r in res:
    if r is None: continue
    texts = r.get("rec_texts",[]) if isinstance(r,dict) else getattr(r,"rec_texts",[])
    scores = r.get("rec_scores",[]) if isinstance(r,dict) else getattr(r,"rec_scores",[])
    polys = r.get("dt_polys",[]) if isinstance(r,dict) else getattr(r,"dt_polys",[])
    for i in range(len(texts)):
        t = texts[i]
        conf = float(scores[i]) if i < len(scores) else 0.0
        poly = polys[i] if i < len(polys) else None
        if t and conf > 0.5:
            bbox = []
            if poly is not None:
                for pt in poly:
                    if isinstance(pt, (list, tuple, np.ndarray)) and len(pt) >= 2:
                        bbox.extend([float(pt[0]), float(pt[1])])
            if bbox:
                cx = (bbox[0] + bbox[4]) / 2
                cy = (bbox[1] + bbox[5]) / 2
                raw_lines.append({"text": t, "conf": conf, "cx": cx, "cy": cy, "bbox": bbox})

print(f"\nOCR原始结果 ({len(raw_lines)}行):")
for l in raw_lines:
    print(f"  y={l['cy']:.0f} x={l['cx']:.0f}  {l['text']}")

# ─── 后处理：按Y坐标合并间距大的相邻字符 ───

def merge_wide_spaced(raw_lines, y_thresh=30):
    """合并同一Y水平线上间距大的相邻字符"""
    if not raw_lines:
        return raw_lines
    
    # 按Y排序
    sorted_lines = sorted(raw_lines, key=lambda l: (l["cy"], l["cx"]))
    
    # 分组：Y坐标相近的归为一行
    rows = []
    current_row = [sorted_lines[0]]
    for l in sorted_lines[1:]:
        if abs(l["cy"] - current_row[-1]["cy"]) < y_thresh:
            current_row.append(l)
        else:
            rows.append(current_row)
            current_row = [l]
    if current_row:
        rows.append(current_row)
    
    # 每行内按X排序，合并文本
    merged = []
    for row in rows:
        row.sort(key=lambda l: l["cx"])
        avg_y = sum(l["cy"] for l in row) / len(row)
        text = "".join(l["text"] for l in row)
        conf = sum(l["conf"] for l in row) / len(row)
        merged.append({"text": text, "conf": conf, "cy": avg_y})
    
    # 按Y排序输出
    merged.sort(key=lambda l: l["cy"])
    return merged

merged = merge_wide_spaced(raw_lines, y_thresh=25)

print(f"\n=== 合并后 ({len(merged)}行) ===")
for l in merged:
    print(f"  y={l['cy']:.0f} conf={l['conf']:.4f}  {l['text']}")

# 对比 merged vs 原始
print(f"\n=== 顶部区域对比 ===")
original_top = [l for l in raw_lines if l["cy"] < PAGE_H * 0.25]
merged_top = [l for l in merged if l["cy"] < PAGE_H * 0.25]

print("原始:")
for l in original_top:
    print(f"  y={l['cy']:.0f}  {l['text']}")

print("合并后:")
for l in merged_top:
    print(f"  y={l['cy']:.0f}  {l['text']}")
