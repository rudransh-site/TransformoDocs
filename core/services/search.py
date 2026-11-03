"""Keyword search over the documents a user may see."""
import re

from django.db.models import Q
from django.utils.html import escape
from django.utils.safestring import mark_safe

from .permissions import visible_documents


def tokens(q):
    return [t for t in re.split(r"\s+", (q or "").strip()) if t][:6]


def search_documents(user, q, type_code="", status="", include_archived=False):
    qs = visible_documents(user)
    for tok in tokens(q):
        qs = qs.filter(
            Q(current_version__original_filename__icontains=tok)
            | Q(current_version__extracted_text__icontains=tok)
            | Q(doc_type__name__icontains=tok)
        )
    if type_code:
        qs = qs.filter(doc_type__code=type_code)
    if status:
        qs = qs.filter(status=status)
    elif not include_archived:
        qs = qs.exclude(status="ARCHIVED")
    return qs.order_by("-created_at")


def snippet(text, q, width=160):
    toks = tokens(q)
    text = re.sub(r"\s+", " ", text or "")
    if not text:
        return ""
    start = 0
    for tok in toks:
        m = re.search(re.escape(tok), text, re.I)
        if m:
            start = max(0, m.start() - width // 3)
            break
    piece = text[start:start + width]
    out = escape(("… " if start else "") + piece + (" …" if start + width < len(text) else ""))
    for tok in toks:
        out = re.sub("(" + re.escape(escape(tok)) + ")", r"<mark>\1</mark>", out, flags=re.I)
    return mark_safe(out)


def snippet_plain(text, q, width=160):
    """Plain-text snippet for the API (no HTML)."""
    text = re.sub(r"\s+", " ", text or "")
    start = 0
    for tok in tokens(q):
        m = re.search(re.escape(tok), text, re.I)
        if m:
            start = max(0, m.start() - width // 3)
            break
    return text[start:start + width]
