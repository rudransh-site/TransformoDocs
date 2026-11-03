"""Tesseract OCR with OpenCV preprocessing."""
import logging
import os
import unicodedata
from dataclasses import dataclass, field
from statistics import mean

import cv2
import numpy as np
import pytesseract
from django.conf import settings
from PIL import Image, ImageOps
from pytesseract import Output

from .errors import PipelineError

logger = logging.getLogger("transformodocs.ocr")
OCR_TIMEOUT_SECONDS = 90
RETRY_TIMEOUT_SECONDS = 12  # the second-chance pass must never stall a demo


_tessdata_checked = False


def configure():
    """Point pytesseract at the right executable and, if Hindi data was downloaded
    into <project>/tessdata (python manage.py download_models) but the system
    Tesseract lacks it, use that folder."""
    global _tessdata_checked
    if getattr(settings, "TESSERACT_CMD", ""):
        pytesseract.pytesseract.tesseract_cmd = settings.TESSERACT_CMD
    if _tessdata_checked:
        return
    _tessdata_checked = True
    try:
        local = settings.BASE_DIR / "tessdata"
        if (local / "eng.traineddata").exists() and (local / "hin.traineddata").exists() \
                and not os.environ.get("TESSDATA_PREFIX"):
            if "hin" not in pytesseract.get_languages(config=""):
                os.environ["TESSDATA_PREFIX"] = str(local)
    except Exception:  # noqa: BLE001 - never break OCR because of optional language data
        logger.exception("Could not check Tesseract language data")


@dataclass
class OCRPage:
    lines: list = field(default_factory=list)  # {"text","conf","bbox":[x0,y0,x1,y1],"par":int}
    words: int = 0
    mean_conf: float = 0.0
    garbage: bool = False
    width: int = 0
    height: int = 0


# ----------------------------------------------------------- preprocessing
def deskew(gray):
    """Straighten a page by searching small rotation angles for the sharpest
    text-row profile. Convention-free, so it behaves the same on every OpenCV."""
    h, w = gray.shape
    scale = 800.0 / max(h, w)
    small = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else gray
    _, bw = cv2.threshold(cv2.bitwise_not(small), 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    if cv2.countNonZero(bw) < 200:
        return gray
    sh, sw = bw.shape
    center = (sw / 2, sh / 2)

    def score(angle):
        m = cv2.getRotationMatrix2D(center, angle, 1.0)
        rot = cv2.warpAffine(bw, m, (sw, sh), flags=cv2.INTER_NEAREST)
        return float(np.var(rot.sum(axis=1)))

    best = max(range(-10, 11), key=score)
    fine = [best + d / 4 for d in range(-4, 5)]
    best = max(fine, key=score)
    if abs(best) < 0.3:
        return gray
    m = cv2.getRotationMatrix2D((w / 2, h / 2), best, 1.0)
    return cv2.warpAffine(gray, m, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)


def preprocess(gray, alt=False):
    img = deskew(gray)
    if alt:  # second chance for poor scans: upscale, denoise, adaptive threshold
        img = cv2.resize(img, None, fx=1.5, fy=1.5, interpolation=cv2.INTER_CUBIC)
        img = cv2.medianBlur(img, 5)  # fast denoise (non-local-means was too slow for a live demo)
        img = cv2.adaptiveThreshold(img, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 41, 12)
        img = cv2.morphologyEx(img, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))  # drop speckles
    return img


# --------------------------------------------------------------- recognition
def is_texty(c):
    """Letters, digits and combining marks (Hindi vowel signs) all count as text."""
    return c.isalnum() or unicodedata.category(c).startswith("M")


def _alnum_ratio(text):
    chars = [c for c in text if not c.isspace()]
    return (sum(is_texty(c) for c in chars) / len(chars)) if chars else 0.0


def ocr_gray(gray, alt=False, timeout=OCR_TIMEOUT_SECONDS, lang=None):
    configure()
    img = preprocess(gray, alt)
    config = "--oem 1 --psm 6" if alt else "--oem 1 --psm 3"
    try:
        data = pytesseract.image_to_data(
            img, lang=lang or settings.OCR_LANG, config=config, output_type=Output.DICT, timeout=timeout
        )
    except pytesseract.TesseractNotFoundError:
        raise PipelineError(
            "OCR_UNAVAILABLE",
            "Text recognition is unavailable right now. An administrator has been notified.",
            retryable=True, notify_admin=True, severity="CRITICAL",
        ) from None
    except RuntimeError as exc:  # timeout
        raise PipelineError("OCR_TIMEOUT", "Reading this page took too long. Please try a smaller or clearer file.",
                            retryable=True) from exc
    except pytesseract.TesseractError as exc:
        msg = str(exc).lower()
        if "failed loading language" in msg or "data path" in msg:
            raise PipelineError(
                "OCR_UNAVAILABLE",
                "Text recognition is unavailable right now. An administrator has been notified.",
                retryable=True, notify_admin=True, severity="CRITICAL",
            ) from exc
        raise

    scale = 1.5 if alt else 1.0
    groups, order = {}, []
    for i, raw in enumerate(data["text"]):
        text = (raw or "").strip()
        try:
            conf = float(data["conf"][i])
        except (TypeError, ValueError):
            continue
        if not text or conf < 0:
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append((data["left"][i], data["top"][i], data["width"][i], data["height"][i], conf, text))

    lines, all_conf, par_ids = [], [], {}
    for key in order:
        words = sorted(groups[key], key=lambda w: w[0])
        text = " ".join(w[5] for w in words)
        x0 = min(w[0] for w in words) / scale
        y0 = min(w[1] for w in words) / scale
        x1 = max(w[0] + w[2] for w in words) / scale
        y1 = max(w[1] + w[3] for w in words) / scale
        confs = [w[4] for w in words]
        all_conf.extend(confs)
        par = par_ids.setdefault(key[:2], len(par_ids))
        lines.append({"text": text, "conf": round(mean(confs), 1),
                      "bbox": [round(x0), round(y0), round(x1), round(y1)], "par": par})
    joined = " ".join(l["text"] for l in lines)
    page = OCRPage(
        lines=lines, words=len(all_conf), mean_conf=round(mean(all_conf), 1) if all_conf else 0.0,
        garbage=bool(lines) and _alnum_ratio(joined) < 0.5, height=gray.shape[0], width=gray.shape[1],
    )
    return page


def ocr_page_with_retry(gray, conf_floor, lang=None):
    """OCR once; if the result looks poor retry once with alternate preprocessing
    and keep the better one."""
    first = ocr_gray(gray, alt=False, lang=lang)
    ink = float((gray < 128).mean())
    poor = (first.words > 0 and (first.mean_conf < conf_floor or first.garbage)) or (
        first.words == 0 and ink > 0.003
    )
    if not poor:
        return first
    try:
        second = ocr_gray(gray, alt=True, timeout=RETRY_TIMEOUT_SECONDS, lang=lang)
    except PipelineError as exc:
        if exc.code == "OCR_TIMEOUT":
            logger.info("Retry pass timed out; keeping the first result")
            return first
        raise
    if second.words and (second.mean_conf > first.mean_conf or first.words == 0) and not second.garbage:
        return second
    return first


# ------------------------------------------------------------ image loading
def pixmap_to_gray(pix):
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.stride)
    return np.ascontiguousarray(arr[:, : pix.width])


def load_image_gray(source):
    """Read an image path or bytes into a grayscale numpy array (unicode-path safe)."""
    import io

    img = Image.open(io.BytesIO(source) if isinstance(source, (bytes, bytearray)) else source)
    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
        bg.alpha_composite(img)
        img = bg
    gray = np.array(img.convert("L"))
    h, w = gray.shape
    longest = max(h, w)
    if longest > 3500:
        f = 3500.0 / longest
        gray = cv2.resize(gray, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
    elif longest < 1200:
        gray = cv2.resize(gray, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    return gray
