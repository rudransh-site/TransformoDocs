"""Document chat: retrieval + rules, no LLM.

1. summary questions -> the stored summary
2. "list the dates / people / organisations / amounts ..." -> extracted entities
3. questions that name a field ("what is the total?") -> the extracted value
4. anything else -> the best matching passages, highlighted
"""
import re

import numpy as np

from . import embeddings as E
from . import text as T

ENTITY_WORDS = {
    "dates": "Date", "people": "Person", "persons": "Person", "names": "Person", "organizations": "Organization",
    "organisations": "Organization", "companies": "Organization", "amounts": "Amount", "prices": "Amount",
    "places": "Place", "locations": "Place", "emails": "Email", "phones": "Phone", "links": "Link",
}
SUMMARY_WORDS = {"summary", "summarize", "summarise", "overview", "gist", "tldr"}
NOT_FOUND = "I could not find that in this document."


def _tokens(q):
    return {w for w in T.words(q) if w not in T.stop_words() or w in ("who", "when")}


def _syn_expand(tokens):
    out = set(tokens)
    for t in list(tokens):
        for g in E._SYN.get(t, []):
            out.update(w for w in E.SYNONYM_GROUPS[int(g[3:])].split())
    return out


def _find_field(q, doc_type, data):
    """Best schema field named in the question that has a value."""
    if doc_type is None or not data:
        return None
    qt = _tokens(q)
    qx = _syn_expand(qt)
    best, best_score = None, 0.0
    for spec in doc_type.field_specs():
        key = spec["key"]
        if data.get(key) in (None, "", []):
            continue
        ft = set(re.split(r"[_\s/]+", key.lower())) | set(T.words(spec.get("label", "")))
        ft = {t for t in ft if t not in ("no", "of")} or ft
        direct = len(ft & qt) / len(ft)
        loose = len(ft & qx) / len(ft)
        score = direct + 0.3 * loose
        if direct > 0 and score > best_score:
            best, best_score = spec, score
    return best if best_score >= 0.5 else None


def _format_value(v):
    return ", ".join(map(str, v)) if isinstance(v, list) else str(v)


def _passages(q, version, k=3):
    E.ensure_chunks(version)
    from intelligence.models import DocumentChunk

    backend = E.active_backend()
    rows = list(DocumentChunk.objects.filter(version=version, backend=backend).values_list("text", "embedding"))
    if not rows:
        return []
    qvec = E.embed([q], backend)[0]
    scores = np.vstack([E.from_bytes(r[1]) for r in rows]) @ qvec
    terms = {w for w in T.words(q) if len(w) > 2 and w not in T.stop_words()}
    ranked = []
    for (text, _b), s in zip(rows, scores):
        s = float(s)
        if terms and any(re.search(r"(?<!\w)" + re.escape(t), text, re.I) for t in terms):
            s += 0.12
        ranked.append((s, text))
    ranked.sort(key=lambda x: -x[0])
    floor = E.MIN_SCORE[backend]
    return [(s, t) for s, t in ranked[:k] if s >= floor]


def _source(text, query, score=None):
    return {"html": str(E.highlight(text, query)), "score": round(float(score), 2) if score is not None else None}


def _passage_with(version, needle, q):
    for line_block in E.chunk_text(version.extracted_text):
        if needle.lower() in line_block.lower():
            return [_source(line_block, needle)]
    return []


def answer(question, doc, version):
    """Return {"answer", "mode", "sources"}."""
    q = (question or "").strip()
    qt = _tokens(q)
    if not q:
        return {"answer": "Please type a question about this document.", "mode": "none", "sources": []}

    if qt & SUMMARY_WORDS or q.lower() in ("what is this document about?", "what is this about?"):
        text = version.summary or "No summary is available for this document yet."
        return {"answer": text, "mode": "summary", "sources": []}

    ent_label = next((ENTITY_WORDS[w] for w in qt if w in ENTITY_WORDS), None)
    if ent_label:
        items = [e for e in (version.entities or []) if e["label"] == ent_label]
        if items:
            shown = "; ".join(e["text"] for e in items[:15])
            return {"answer": f"{ent_label} mentioned in this document: {shown}.", "mode": "entities", "sources": []}

    spec = _find_field(q, doc.doc_type, version.structured_data or {})
    if spec:
        value = _format_value(version.structured_data[spec["key"]])
        return {"answer": f"The {spec['label']} is {value}.", "mode": "field",
                "sources": _passage_with(version, value.split(",")[0].split("\n")[0], q)}

    hits = _passages(q, version)
    if hits:
        return {"answer": "Here are the most relevant parts of the document:", "mode": "passages",
                "sources": [_source(t, q, s) for s, t in hits]}
    return {"answer": NOT_FOUND + " Try asking about a specific field, such as the total, a date or a name.",
            "mode": "none", "sources": []}
