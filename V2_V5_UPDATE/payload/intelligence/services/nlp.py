"""Entity extraction: spaCy (en_core_web_sm) with a regex fallback so the feature
never disappears if the model is not installed."""
import logging
import re
from collections import Counter

from django.conf import settings

logger = logging.getLogger("transformodocs.v2")

SPACY_MODEL = getattr(settings, "SPACY_MODEL", "en_core_web_sm")
KEEP = {"PERSON": "Person", "ORG": "Organization", "GPE": "Place", "LOC": "Place", "DATE": "Date", "MONEY": "Amount"}
ORDER = ["Person", "Organization", "Place", "Date", "Amount", "Email", "Phone", "Link"]
MAX_CHARS = 60000

_nlp = None
_failed = False

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?<!\d)(?:\+?91[\s\-]?)?[6-9]\d{4}[\s\-]?\d{5}(?!\d)")
URL_RE = re.compile(r"\b(?:https?://|www\.)[^\s,;]+|\b(?:linkedin|github)\.com/[^\s,;]+", re.I)
DATE_RE = re.compile(r"\b\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4}\b|\b\d{4}-\d{2}-\d{2}\b|"
                     r"\b\d{1,2}(?:st|nd|rd|th)?\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?,?\s+\d{4}\b|"
                     r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}\b", re.I)
MONEY_RE = re.compile(r"(?:₹|Rs\.?|INR|\$|USD|€)\s?\d[\d,]*(?:\.\d{1,2})?|\b\d[\d,]*(?:\.\d{2})\b")


def get_nlp():
    global _nlp, _failed
    if _nlp is not None or _failed:
        return _nlp
    try:
        import spacy

        _nlp = spacy.load(SPACY_MODEL, exclude=["parser", "lemmatizer"])
    except Exception as exc:  # noqa: BLE001 - missing package or model
        _failed = True
        logger.warning("spaCy model '%s' unavailable (%s); using regex entities", SPACY_MODEL, exc)
    return _nlp


def spacy_available():
    return get_nlp() is not None


def _clean(label, value):
    value = " ".join(value.split()).strip(" ,.;:()-")
    if len(value) < 2 or len(value) > 80:
        return None
    if label == "Amount" and not re.search(r"\d", value):
        return None
    if label in ("Person", "Organization", "Place") and not re.search(r"[^\W\d_]{2}", value):
        return None
    return value


LABEL_WORDS = {"phone", "date", "group", "upto", "no", "id", "name", "number", "address", "email", "total", "amount",
               "invoice", "bill", "valid", "blood", "issue", "due", "gst", "qty", "price", "department", "mobile"}


def _ner_units(text):
    """Lines for spaCy. For 'Label: value' lines only the value is analysed, so
    labels ('Invoice Date', 'Blood Group') are not mistaken for names or organisations."""
    units = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = re.match(r"^([^:]{1,30}):\s*(\S.*)$", line)
        if m and not re.match(r"^https?$", m.group(1).strip(), re.I):
            line = m.group(2)
        units.append(line[:300])
    return units


def _person_ok(value):
    words = [w.lower() for w in re.findall(r"[^\W\d_]+", value)]
    return bool(words) and not (set(words) & LABEL_WORDS) and not re.search(r"\d", value)


def extract_entities(text, max_items=60):
    """Return (entities, backend). entities = [{"text","label","count"}] grouped in ORDER.
    People / organisations / places come from spaCy (when available); dates, amounts, emails,
    phone numbers and links come from patterns, which are more precise on forms and invoices."""
    text = (text or "")[:MAX_CHARS]
    counter = Counter()
    nlp = get_nlp()
    backend = "regex"
    if nlp is not None:
        backend = "spacy"
        try:
            for doc in nlp.pipe(_ner_units(text), batch_size=64):
                for ent in doc.ents:
                    label = KEEP.get(ent.label_)
                    if label in (None, "Date", "Amount"):
                        continue
                    value = _clean(label, ent.text)
                    if not value:
                        continue
                    if label == "Person" and not _person_ok(value):
                        continue
                    if label == "Organization" and (set(w.lower() for w in value.split()) & LABEL_WORDS
                                                    or re.search(r"\d", value)):
                        continue
                    counter[(label, value.lower(), value)] += 1
        except Exception:  # noqa: BLE001
            logger.exception("spaCy failed on a document; using patterns only")
            backend, counter = "regex", Counter()
    for rx, label in ((DATE_RE, "Date"), (MONEY_RE, "Amount"), (EMAIL_RE, "Email"), (PHONE_RE, "Phone"), (URL_RE, "Link")):
        for m in rx.finditer(text):
            value = _clean(label, m.group(0))
            if value:
                counter[(label, value.lower(), value)] += 1
    merged = {}
    for (label, low, value), n in counter.items():
        key = (label, low)
        if key in merged:
            merged[key]["count"] += n
        else:
            merged[key] = {"text": value, "label": label, "count": n}
    items = sorted(merged.values(), key=lambda e: (ORDER.index(e["label"]), -e["count"], e["text"].lower()))
    return items[:max_items], backend
