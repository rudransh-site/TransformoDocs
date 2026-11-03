"""Handwriting recognition.

Engine: RapidOCR (PaddleOCR models running on ONNX Runtime, CPU). Chosen over EasyOCR
because it needs no PyTorch and its models ship inside the pip package, so it works
offline immediately. It reads neat handwriting reasonably and cursive/messy handwriting
poorly, which is why handwritten documents are ALWAYS sent to a reviewer.
If RapidOCR is missing, or Hindi is requested (the bundled models are English/Chinese),
the page is read with Tesseract using the preprocessing made for poor scans.
"""
import logging
import threading
from statistics import median

import cv2
import numpy as np

from core.services import ocr

logger = logging.getLogger("transformodocs.v5")
_engine = None
_failed = False
_lock = threading.Lock()


def get_engine():
    global _engine, _failed
    if _engine is not None or _failed:
        return _engine
    with _lock:
        if _engine is None and not _failed:
            try:
                from rapidocr_onnxruntime import RapidOCR

                _engine = RapidOCR()
            except Exception as exc:  # noqa: BLE001
                _failed = True
                logger.warning("RapidOCR unavailable (%s); handwriting will use Tesseract", exc)
    return _engine


def engine_available():
    return get_engine() is not None


def _group_lines(items):
    """items: [(x0, y0, x1, y1, text, conf)] -> OCR lines ordered top to bottom."""
    if not items:
        return []
    heights = [max(1, it[3] - it[1]) for it in items]
    tol = 0.6 * median(heights)
    items = sorted(items, key=lambda it: ((it[1] + it[3]) / 2, it[0]))
    rows, row_y = [], []
    for it in items:
        cy = (it[1] + it[3]) / 2
        if rows and abs(cy - row_y[-1]) <= tol:
            rows[-1].append(it)
            row_y[-1] = (row_y[-1] + cy) / 2
        else:
            rows.append([it])
            row_y.append(cy)
    lines, par, prev_bottom = [], 0, None
    for row in rows:
        row.sort(key=lambda it: it[0])
        top, bottom = min(i[1] for i in row), max(i[3] for i in row)
        if prev_bottom is not None and top - prev_bottom > 1.5 * median(heights):
            par += 1
        prev_bottom = bottom
        lines.append({"text": " ".join(i[4] for i in row), "conf": round(sum(i[5] for i in row) / len(row), 1),
                      "bbox": [round(min(i[0] for i in row)), round(top), round(max(i[2] for i in row)),
                               round(bottom)], "par": par})
    return lines


def recognize(gray, conf_floor, lang=None):
    """Return an ocr.OCRPage for one page image (grayscale numpy array)."""
    if lang and "hin" in lang:
        logger.info("Hindi handwriting requested: using Tesseract (RapidOCR models are English/Chinese)")
        return ocr.ocr_gray(gray, alt=True, timeout=60, lang=lang)
    engine = get_engine()
    if engine is None:
        return ocr.ocr_gray(gray, alt=True, timeout=60, lang=lang)
    img = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR) if gray.ndim == 2 else gray
    result, _elapse = engine(img)
    items = []
    for box, text, score in result or []:
        text = (text or "").strip()
        if not text:
            continue
        xs, ys = [p[0] for p in box], [p[1] for p in box]
        items.append((min(xs), min(ys), max(xs), max(ys), text, float(score) * 100.0))
    lines = _group_lines(items)
    words = sum(len(l["text"].split()) for l in lines)
    mean_conf = round(sum(i[5] for i in items) / len(items), 1) if items else 0.0
    return ocr.OCRPage(lines=lines, words=words, mean_conf=mean_conf, garbage=False,
                       width=int(gray.shape[1]), height=int(gray.shape[0]))
