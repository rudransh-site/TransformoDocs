"""OCR language and handwriting options (V5)."""
import logging
import time

from django.conf import settings

from core.services.errors import PipelineError

logger = logging.getLogger("transformodocs.v5")

LANGUAGES = [("eng", "English"), ("hin", "Hindi"), ("eng+hin", "English + Hindi")]
LABELS = dict(LANGUAGES)
_cache = (0.0, None)


def installed_languages(force=False):
    """Tesseract language codes available to this project (system + project tessdata)."""
    global _cache
    ts, cached = _cache
    if cached is not None and not force and time.time() - ts < 60:
        return cached
    langs = set()
    try:
        import pytesseract

        from core.services import ocr

        ocr.configure()  # may point TESSDATA_PREFIX at <project>/tessdata
        langs = set(pytesseract.get_languages(config=""))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not list Tesseract languages: %s", exc)
    _cache = (time.time(), langs)
    return langs


def language_ready(code):
    return all(part in installed_languages() for part in code.split("+"))


def ensure_language(code):
    """Raise a friendly PipelineError if the requested OCR language is not installed."""
    if language_ready(code):
        return
    missing = [p for p in code.split("+") if p not in installed_languages()]
    raise PipelineError(
        "LANGUAGE_UNAVAILABLE",
        f"Text recognition for {', '.join(LABELS.get(m, m) for m in missing)} is not installed on this computer. "
        "An administrator has been notified.",
        retryable=True, notify_admin=True, severity="CRITICAL")


def upload_context():
    ready = {code: language_ready(code) for code, _ in LANGUAGES}
    return {"ocr_languages": [(c, l, ready[c]) for c, l in LANGUAGES], "hindi_missing": not ready["hin"]}


def save_options(doc, data):
    from advanced.models import DocumentOCRProfile

    lang = (data.get("ocr_language") or "eng").strip()
    if lang not in LABELS:
        lang = "eng"
    handwriting = str(data.get("handwriting", "")).lower() in ("1", "true", "on", "yes")
    DocumentOCRProfile.objects.update_or_create(document=doc, defaults={"language": lang, "handwriting": handwriting})


def _profile(doc):
    from advanced.models import DocumentOCRProfile

    return DocumentOCRProfile.objects.filter(document=doc).first()


def options_for(doc):
    p = _profile(doc)
    if p is None:
        return {"lang": None, "handwriting": False}
    return {"lang": None if p.language == "eng" else p.language, "handwriting": p.handwriting}


def review_reasons(doc, version):
    p = _profile(doc)
    return ["HANDWRITTEN"] if p and p.handwriting else []


def detail_context(doc):
    p = _profile(doc)
    if p is None:
        return {}
    return {"ocr_profile": {"language_label": LABELS.get(p.language, p.language), "handwriting": p.handwriting}}
