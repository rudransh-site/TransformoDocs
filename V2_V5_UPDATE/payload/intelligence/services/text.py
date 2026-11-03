"""Small shared text helpers (stop words, tokens, language detection)."""
import re

_FALLBACK_STOP = set("""a an the and or but if then else of to in on at by for with from as is are was were be been being this that these
those it its i you he she we they them his her our your their not no yes do does did have has had will would can could should may might
about into over under after before between than so such there here which who whom what when where why how also more most other some any
each per via etc""".split())

_stop = None


def stop_words():
    global _stop
    if _stop is None:
        try:
            from sumy.utils import get_stop_words

            _stop = set(get_stop_words("english")) | _FALLBACK_STOP
        except Exception:  # noqa: BLE001
            _stop = set(_FALLBACK_STOP)
    return _stop


WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)


def words(text):
    return [w.lower() for w in WORD_RE.findall(text or "")]


def content_words(text, min_len=3):
    stops = stop_words()
    return [w for w in words(text) if len(w) >= min_len and w not in stops and not w.isdigit()]


def detect_language(text):
    """'hi' if a good part of the letters are Devanagari, else 'en'."""
    letters = [c for c in (text or "") if c.isalpha()]
    if not letters:
        return "en"
    dev = sum(1 for c in letters if "\u0900" <= c <= "\u097f")
    return "hi" if dev / len(letters) > 0.3 else "en"


def pretty_date(iso):
    import datetime

    try:
        d = datetime.date.fromisoformat(str(iso))
        return f"{d.day} {d.strftime('%b %Y')}"
    except ValueError:
        return str(iso)


def split_sentences(text):
    text = re.sub(r"[ \t]+", " ", text or "")
    parts = []
    for block in re.split(r"\n+", text):
        block = block.strip()
        if not block:
            continue
        parts.extend(p.strip() for p in re.split(r"(?<=[.!?।])\s+(?=[A-Z\u0900-\u097f0-9\"'(])", block) if p.strip())
    return parts
