"""Environment checks used by `manage.py preflight` and the Admin health page."""
import importlib
import tempfile
from pathlib import Path

from django.conf import settings
from django.db import connection

from core.models import DocumentType

from . import extensions


def _check(name, fn, fix, critical=True):
    try:
        detail = fn() or "OK"
        return {"name": name, "ok": True, "detail": detail, "fix": "", "critical": critical}
    except Exception as exc:  # noqa: BLE001
        return {"name": name, "ok": False, "detail": f"{type(exc).__name__}: {exc}"[:300], "fix": fix,
                "critical": critical}


def _db():
    connection.ensure_connection()
    with connection.cursor() as cur:
        cur.execute("SELECT 1")
    return f"{connection.vendor} connected"


def _types():
    n = DocumentType.objects.filter(is_active=True).count()
    if n < 4:
        raise RuntimeError(f"only {n} document types found")
    return f"{n} document types"


def _tesseract():
    import pytesseract

    from . import ocr

    ocr.configure()
    version = pytesseract.get_tesseract_version()
    langs = pytesseract.get_languages(config="")
    if settings.OCR_LANG not in langs:
        raise RuntimeError(f"language '{settings.OCR_LANG}' not installed (found: {', '.join(langs)})")
    return f"Tesseract {version}, language '{settings.OCR_LANG}' available"


def _module(name, label=None):
    def run():
        mod = importlib.import_module(name)
        return f"{label or name} {getattr(mod, '__version__', '') or getattr(mod, 'VersionBind', '')}".strip()
    return run


def _writable(raw_path):
    def run():
        path = Path(raw_path)
        path.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path, delete=True):
            pass
        return f"{path} is writable"
    return run


def run_checks():
    tess_fix = ("Windows: install Tesseract (winget install UB-Mannheim.TesseractOCR) and set TESSERACT_CMD in .env. "
                "Linux: sudo apt install tesseract-ocr")
    base = [
        _check("Database", _db, "Start MySQL and check DB_* values in .env"),
        _check("Document types", _types, "Run: python manage.py seed_initial_data"),
        _check("Tesseract OCR", _tesseract, tess_fix),
        _check("PyMuPDF", _module("pymupdf", "PyMuPDF"), "pip install PyMuPDF"),
        _check("python-docx", _module("docx", "python-docx"), "pip install python-docx"),
        _check("OpenCV", _module("cv2", "OpenCV"), "pip install opencv-python-headless"),
        _check("Pillow", _module("PIL", "Pillow"), "pip install pillow"),
        _check("Uploads folder", _writable(settings.MEDIA_ROOT), "Check permissions/disk space for the media folder"),
        _check("Logs folder", _writable(settings.LOG_DIR), "Check permissions for the logs folder", critical=False),
    ]
    return base + extensions.extra_health_checks()
