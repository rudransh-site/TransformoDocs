"""V5 environment checks (non-critical)."""
from . import handwriting, options


def _row(name, ok, detail, fix=""):
    return {"name": name, "ok": ok, "detail": detail, "fix": fix, "critical": False}


def checks():
    out = []
    if options.language_ready("hin"):
        out.append(_row("V5 Hindi OCR", True, "Hindi text recognition available"))
    else:
        out.append(_row("V5 Hindi OCR", False, "Hindi language data not found",
                        "python manage.py download_models  (or install tesseract-ocr-hin)"))
    if handwriting.engine_available():
        out.append(_row("V5 handwriting (RapidOCR)", True, "RapidOCR engine loaded"))
    else:
        out.append(_row("V5 handwriting (RapidOCR)", False,
                        "RapidOCR not installed: handwriting falls back to Tesseract",
                        "pip install rapidocr-onnxruntime"))
    try:
        import pymupdf

        ok = hasattr(pymupdf, "Story")
        out.append(_row("V5 document generator (PDF)", ok, "PyMuPDF Story available" if ok else "PyMuPDF too old",
                        "" if ok else "pip install -U PyMuPDF"))
    except ImportError:
        out.append(_row("V5 document generator (PDF)", False, "PyMuPDF missing", "pip install PyMuPDF"))
    return out
