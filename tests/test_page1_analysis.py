# -*- coding: utf-8 -*-
"""
分析 test_page1.png 的 OCR 输出特征：
  图片尺寸、红头竖排框坐标、标题候选、正文行数。
  不生成 DOCX，只打印分析结果，方便调整 docx_builder 参数。
"""
import sys, io, cv2, numpy as np, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
from paddleocr import PaddleOCR

IMG_PATH = r"D:\hongmengProjects\doc-structure-mcp\tests\fixtures\test_page1.png"

print("=" * 60)
print("test_page1.png OCR 特征分析")
print("=" * 60)

# 读取图片
img = cv2.imdecode(np.fromfile(IMG_PATH, np.uint8), cv2.IMREAD_COLOR)
if img is None:
    print(f"[ERROR] 无法读取图片: {IMG_PATH}")
    sys.exit(1)
H, W = img.shape[:2]
print(f"\n[图片信息] 尺寸: {W}x{H}, 通道: {img.shape[2] if len(img.shape) > 2 else 1}")

# OCR
ocr = PaddleOCR(
    text_detection_model_name="PP-OCRv5_mobile_det",
    text_recognition_model_name="PP-OCRv5_mobile_rec",
    use_textline_orientation=True,
    lang="ch",
    text_det_thresh=0.3,
    text_det_box_thresh=0.5,
)
t0 = time.time()
res = list(ocr.predict(img))
elapsed = time.time() - t0
print(f"[OCR] {len(res)} 检测框, 耗时 {elapsed:.1f}s\n")

# 解析结果
blocks = []
for r in res:
    if r is None:
        continue
    texts = r.get("rec_texts", []) if isinstance(r, dict) else getattr(r, "rec_texts", [])
    scores = r.get("rec_scores", []) if isinstance(r, dict) else getattr(r, "rec_scores", [])
    polys = r.get("dt_polys", []) if isinstance(r, dict) else getattr(r, "dt_polys", [])
    for i in range(len(texts)):
        t = texts[i].strip()
        conf = float(scores[i]) if i < len(scores) else 0.0
        poly = polys[i] if i < len(polys) else None
        if not t or conf < 0.3:
            continue
        if poly is not None and len(poly) >= 4:
            pts = [(float(p[0]), float(p[1])) for p in poly if len(p) >= 2]
            if pts:
                xs = [p[0] for p in pts]
                ys = [p[1] for p in pts]
                blocks.append({
                    "text": t, "conf": conf,
                    "x1": min(xs), "y1": min(ys),
                    "x2": max(xs), "y2": max(ys),
                    "cx": (min(xs) + max(xs)) / 2,
                    "cy": (min(ys) + max(ys)) / 2,
                    "w": max(xs) - min(xs),
                    "h": max(ys) - min(ys),
                })

print(f"[解析] 有效检测框: {len(blocks)} 个\n")

# ─── 红头检测：Y 在顶部 1/4 + 竖排框（高 > 宽*2） ───
header_y_thresh = H // 4
vertical_blocks = [b for b in blocks if b["cy"] < header_y_thresh and b["h"] > b["w"] * 2]
horizontal_top = [b for b in blocks if b["cy"] < header_y_thresh and b["h"] <= b["w"] * 2]

print(f"[红头区域] Y 阈值: {header_y_thresh}")
print(f"  竖排框(高>宽*2): {len(vertical_blocks)} 个")
print(f"  横排框:          {len(horizontal_top)} 个")

if vertical_blocks:
    print(f"\n  ── 竖排框详情 ──")
    vertical_blocks.sort(key=lambda b: (b["cx"], b["cy"]))
    for i, b in enumerate(vertical_blocks):
        print(f"  [{i:2d}] y={b['cy']:.0f} x={b['cx']:.0f} "
              f"w={b['w']:.0f} h={b['h']:.0f} "
              f"conf={b['conf']:.2f} 文字='{b['text']}'")

# ─── 标题候选：Y 在顶部，居中，宽度 < 60% ───
title_candidates = []
for b in blocks:
    center_ratio = abs(b["cx"] - W / 2) / (W / 2)  # 0=正中间, 1=边缘
    width_ratio = b["w"] / W
    # 标题特征：居中 + 不太宽 + 位置靠上
    if center_ratio < 0.3 and width_ratio < 0.6 and b["cy"] < H * 0.5:
        title_candidates.append(b)

print(f"\n[标题候选] {len(title_candidates)} 个")
title_candidates.sort(key=lambda b: b["cy"])
for i, b in enumerate(title_candidates):
    center_offset = int(abs(b["cx"] - W / 2))
    print(f"  [{i:2d}] y={b['cy']:.0f} x={b['cx']:.0f} "
          f"w={b['w']:.0f} h={b['h']:.0f} "
          f"conf={b['conf']:.2f} 居中偏={center_offset}px "
          f"text='{b['text']}'")

# ─── 正文区域分析 ───
body_blocks = [b for b in blocks if b["cy"] >= header_y_thresh]
print(f"\n[正文区域] {len(body_blocks)} 个")

# XY-Cut 行分组
body_blocks.sort(key=lambda b: b["cy"])
rows = []
y_thresh = 30
if body_blocks:
    current = [body_blocks[0]]
    for b in body_blocks[1:]:
        if abs(b["cy"] - current[-1]["cy"]) < y_thresh:
            current.append(b)
        else:
            rows.append(sorted(current, key=lambda x: x["cx"]))
            current = [b]
    if current:
        rows.append(sorted(current, key=lambda x: x["cx"]))

print(f"  XY-Cut 行分组: {len(rows)} 行 (y_thresh={y_thresh})")
for ri, row in enumerate(rows):
    texts = [b["text"] for b in row]
    line = "".join(texts)
    y_pos = row[0]["cy"]
    conf_avg = sum(b["conf"] for b in row) / len(row)
    print(f"  [{ri:2d}] y={y_pos:.0f} conf={conf_avg:.2f} '{line[:80]}{'...' if len(line)>80 else ''}'")

# ─── 汇总 ───
print(f"\n{'=' * 60}")
print(f"汇总: 图片 {W}x{H}, {len(blocks)} 检测框, "
      f"{len(vertical_blocks)} 竖排(红头), "
      f"{len(title_candidates)} 标题候选, "
      f"{len(rows)} 正文行")
print(f"{'=' * 60}")
