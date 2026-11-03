"""Semantic (meaning-based) search over embedded document chunks."""
import re

import numpy as np

from core.services.permissions import visible_documents

from . import embeddings as E
from . import text as T

MAX_DOCS = 80


def _query_terms(q):
    return {w for w in T.words(q) if len(w) > 2 and w not in T.stop_words()}


def semantic_search(user, q, type_code="", status="", include_archived=False, limit=30):
    """Return (documents, backend). Each document gets .score, .snip (HTML), .snip_plain."""
    backend = E.active_backend()
    qs = visible_documents(user).filter(current_version__isnull=False).exclude(
        status__in=["UPLOADED", "DETECTING", "OCR_PROCESSING", "CLASSIFYING", "EXTRACTING", "FAILED"])
    if type_code:
        qs = qs.filter(doc_type__code=type_code)
    if status:
        qs = qs.filter(status=status)
    elif not include_archived:
        qs = qs.exclude(status="ARCHIVED")
    docs = list(qs.order_by("-created_at")[:MAX_DOCS])
    if not docs:
        return [], backend

    from intelligence.models import DocumentChunk

    for d in docs:
        E.ensure_chunks(d.current_version, backend)
    qvec = E.embed([q], backend)[0]
    terms = _query_terms(q)
    version_to_doc = {d.current_version_id: d for d in docs}
    rows = list(DocumentChunk.objects.filter(version_id__in=version_to_doc, backend=backend)
                .values_list("version_id", "text", "embedding"))
    best = {}
    if rows:
        mat = np.vstack([E.from_bytes(r[2]) for r in rows])
        scores = mat @ qvec
        for (vid, text, _blob), score in zip(rows, scores):
            score = float(score)
            if terms and any(re.search(r"(?<!\w)" + re.escape(t), text, re.I) for t in terms):
                score += 0.12  # a literal match still counts
            if vid not in best or score > best[vid][0]:
                best[vid] = (score, text)
    floor = E.MIN_SCORE[backend]
    hits = []
    for vid, (score, text) in best.items():
        if score >= floor:
            d = version_to_doc[vid]
            d.score, d.snip, d.snip_plain = score, E.highlight(text, q), re.sub(r"\s+", " ", text)[:200]
            hits.append(d)
    hits.sort(key=lambda d: -d.score)
    return hits[:limit], backend
