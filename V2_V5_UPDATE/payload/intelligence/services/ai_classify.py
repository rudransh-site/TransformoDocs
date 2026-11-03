"""V2 'AI document classification': a second opinion used only when the rule-based
classifier cannot name a type. It blends three signals:
  50% keyword evidence (the V1 rules)   30% similarity to the type's profile (embeddings)
  20% entity pattern (money, dates, people, organisations...)
"""
import threading

from core.models import DocumentType, SystemSetting
from core.services.classify import _norm, score_type

from . import embeddings as E
from . import nlp

_state = threading.local()


def pop_method():
    """Which classifier decided the last document ('AI' or 'RULES'); resets to RULES."""
    method = getattr(_state, "method", "RULES")
    _state.method = "RULES"
    return method


def _entity_signal(code, entities):
    n = {}
    for e in entities:
        n[e["label"]] = n.get(e["label"], 0) + 1
    has = lambda label, k=1: n.get(label, 0) >= k  # noqa: E731
    if code == "INVOICE":
        return 0.5 * min(1, n.get("Amount", 0) / 2) + 0.25 * has("Organization") + 0.25 * has("Date")
    if code == "RESUME":
        return 0.4 * has("Email") + 0.3 * has("Person") + 0.3 * has("Phone")
    if code == "CERTIFICATE":
        return 0.4 * has("Person") + 0.3 * has("Date") + 0.3 * has("Organization")
    if code == "ID_CARD":
        return 0.35 * has("Person") + 0.35 * has("Date") + 0.3 * has("Phone")
    return 0.0


def refine(text, doc_type, class_conf):
    _state.method = "RULES"
    types = list(DocumentType.objects.filter(is_active=True))
    if not types or not (text or "").strip():
        return doc_type, class_conf
    threshold = SystemSetting.get("AI_CLASSIFY_MIN_CONFIDENCE")
    entities, _backend = nlp.extract_entities(text)
    protos = []
    for dt in types:
        terms = " ".join(t["t"] for t in (dt.classification_rules or {}).get("terms", []))
        protos.append(f"{dt.name}. {dt.description}. {terms}")
    vecs = E.embed([text[:3000]] + protos)
    sims = vecs[1:] @ vecs[0]
    norm = _norm(text)
    best, best_conf = None, 0.0
    for dt, sim in zip(types, sims):
        rules = dt.classification_rules or {}
        kw = min(1.0, score_type(norm, rules) / float(rules.get("full_score") or 10))
        if kw <= 0:  # never name a type with zero keyword evidence
            continue
        conf = 0.5 * kw + 0.3 * max(0.0, float(sim)) + 0.2 * _entity_signal(dt.code, entities)
        if conf > best_conf:
            best, best_conf = dt, conf
    if best is not None and best_conf >= threshold:
        _state.method = "AI"
        return best, round(best_conf, 3)
    return doc_type, class_conf
