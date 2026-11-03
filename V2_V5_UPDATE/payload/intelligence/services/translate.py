"""Offline translation (English <-> Hindi) with Argos Translate language packs."""
import logging
import time

from django.utils import timezone

logger = logging.getLogger("transformodocs.v2")
LANG_NAMES = {"en": "English", "hi": "Hindi"}
_pairs_cache = (0.0, None)


class TranslationUnavailable(Exception):
    pass


def installed_pairs(force=False):
    """Set of (from, to) pairs that are installed. Empty if Argos is missing."""
    global _pairs_cache
    ts, cached = _pairs_cache
    if cached is not None and not force and time.time() - ts < 60:
        return cached
    pairs = set()
    try:
        import argostranslate.translate as at

        langs = at.get_installed_languages()
        for a in langs:
            for b in langs:
                if a.code != b.code and a.get_translation(b) is not None:
                    pairs.add((a.code, b.code))
    except Exception as exc:  # noqa: BLE001
        logger.info("Argos Translate not available: %s", exc)
    _pairs_cache = (time.time(), pairs)
    return pairs


def status():
    pairs = installed_pairs()
    return {"available": bool(pairs), "pairs": sorted(f"{a}-{b}" for a, b in pairs)}


def _chunks(text, size=600):
    cur, out = [], []
    n = 0
    for line in text.splitlines():
        if not line.strip():
            continue
        if cur and n + len(line) > size:
            out.append("\n".join(cur))
            cur, n = [], 0
        cur.append(line)
        n += len(line) + 1
    if cur:
        out.append("\n".join(cur))
    return out


def translate_text(text, src, tgt):
    if (src, tgt) not in installed_pairs():
        raise TranslationUnavailable(
            f"The {LANG_NAMES.get(src, src)} to {LANG_NAMES.get(tgt, tgt)} translation pack is not installed. "
            "Run: python manage.py download_models")
    import argostranslate.translate as at

    langs = {l.code: l for l in at.get_installed_languages()}
    translation = langs[src].get_translation(langs[tgt])
    return "\n".join(translation.translate(chunk) for chunk in _chunks(text))


def translate_version(version, target, max_chars, force=False):
    """Translate the start of a document (cached per language). Returns a Translation."""
    from intelligence.models import Translation

    src = version.language if version.language in LANG_NAMES else "en"
    if src == target:
        raise TranslationUnavailable("The document is already in that language.")
    cached = Translation.objects.filter(version=version, target_lang=target).first()
    if cached and not force:
        return cached, True
    source_text = (version.extracted_text or "")[:max_chars]
    if not source_text.strip():
        raise TranslationUnavailable("This document has no text to translate.")
    out = translate_text(source_text, src, target)
    obj, _ = Translation.objects.update_or_create(
        version=version, target_lang=target,
        defaults={"source_lang": src, "source_text": source_text, "text": out, "created_at": timezone.now()})
    return obj, False
