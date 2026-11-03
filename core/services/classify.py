"""Rule-based document classification (keyword + pattern scoring)."""
import re

from core.models import DocumentType

from .text_utils import WB_L, WB_R


def _norm(text):
    return re.sub(r"\s+", " ", (text or "").lower())


def score_type(norm_text, rules):
    score = 0.0
    for item in rules.get("terms", []):
        term = re.escape(item["t"].lower()).replace(r"\ ", r"\s+")
        if re.search(WB_L + term + WB_R, norm_text):
            score += item.get("w", 1)
    for item in rules.get("patterns", []):
        try:
            if re.search(item["p"], norm_text, re.I):
                score += item.get("w", 1)
        except re.error:
            continue
    return score


def classify(text, min_confidence, types=None):
    """Return (DocumentType or None, confidence 0-1, {code: score}).

    Every term counts once; confidence = min(1, score / full_score). If the best
    type is below `min_confidence` the document is UNKNOWN (None)."""
    norm = _norm(text)
    types = types if types is not None else list(DocumentType.objects.filter(is_active=True))
    scores, best, best_score, best_conf = {}, None, 0.0, 0.0
    for dt in types:
        rules = dt.classification_rules or {}
        score = score_type(norm, rules)
        scores[dt.code] = score
        full = float(rules.get("full_score") or 10)
        conf = min(1.0, score / full) if full else 0.0
        if score > best_score or (score == best_score and score > 0 and conf > best_conf):
            best, best_score, best_conf = dt, score, conf
    if best is None or best_conf < min_confidence:
        return None, best_conf, scores
    return best, best_conf, scores
