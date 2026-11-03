"""Intelligent tagging: document type, year, top entities and key phrases."""
import re
from collections import Counter

from . import text as T


GENERIC = {"amount", "date", "invoice", "total", "number", "name", "address", "phone", "email", "page", "dated",
           "valid", "issue", "issued", "payment", "terms", "price", "item", "unit", "mobile", "from", "link", "with"}


def key_phrases(text, n=8):
    counts = Counter(w for w in T.content_words(text, min_len=4) if w not in GENERIC)
    return [w for w, c in counts.most_common(n * 2) if c >= 2][:n] or [w for w, _ in counts.most_common(n)]


def suggest_tags(doc_type_name, entities, phrases, data, max_tags=9):
    """Return a list of tag names, most useful first."""
    tags = []

    def add(name):
        name = " ".join((name or "").split())
        if name and name.lower() not in {t.lower() for t in tags} and 2 <= len(name) <= 40:
            tags.append(name)

    if doc_type_name:
        add(doc_type_name)
    years = Counter()
    for e in entities:
        if e["label"] == "Date":
            for y in re.findall(r"(?<!\d)(20\d{2}|19\d{2})(?!\d)", e["text"]):
                years[y] += e["count"]
    for v in (data or {}).values():
        if isinstance(v, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
            years[v[:4]] += 1
    if years:
        add(years.most_common(1)[0][0])
    for label, limit in (("Organization", 2), ("Place", 2), ("Person", 1)):
        for e in [e for e in entities if e["label"] == label][:limit]:
            add(e["text"].title() if e["text"].isupper() else e["text"])
    seen_text = " ".join(e["text"].lower() for e in entities if e["label"] in ("Person", "Email", "Link"))
    for p in phrases:
        if len(tags) >= max_tags:
            break
        if p in seen_text:  # already covered by a name / email / link
            continue
        add(p.capitalize())
    return tags[:max_tags]
