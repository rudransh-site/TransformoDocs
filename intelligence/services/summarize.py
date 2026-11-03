"""Automatic summaries: a structured headline from the extracted fields plus
extractive sentences (sumy TextRank, with a simple fallback)."""
import logging
import re
from collections import Counter

from . import text as T

logger = logging.getLogger("transformodocs.v2")


# ------------------------------------------------------------------ headline
def _join(*parts):
    return " ".join(p for p in parts if p).strip()


def headline(doc_type_code, data):
    """One factual sentence built from the verified/extracted fields."""
    d = data or {}
    g = lambda k: d.get(k) if d.get(k) not in (None, "", []) else None  # noqa: E731
    if doc_type_code == "INVOICE":
        s = _join(f"Invoice {g('invoice_number')}" if g("invoice_number") else "Invoice",
                  f"dated {T.pretty_date(g('invoice_date'))}" if g("invoice_date") else "",
                  f"from {g('vendor_name')}" if g("vendor_name") else "",
                  f"to {g('customer_name')}" if g("customer_name") else "",
                  f"for a total of {g('total_amount')}" if g("total_amount") else "",
                  f"(due {T.pretty_date(g('due_date'))})" if g("due_date") else "")
        return s + "."
    if doc_type_code == "RESUME":
        skills = g("skills") or []
        return _join(f"Resume of {g('full_name')}" if g("full_name") else "Resume",
                     f"({g('email')})" if g("email") else "",
                     f"with skills in {', '.join(skills[:6])}" if skills else "") + "."
    if doc_type_code == "CERTIFICATE":
        return _join(g("certificate_title") or "Certificate",
                     f"awarded to {g('recipient_name')}" if g("recipient_name") else "",
                     f"for {g('course_or_achievement')}" if g("course_or_achievement") else "",
                     f"by {g('issuing_organization')}" if g("issuing_organization") else "",
                     f"on {T.pretty_date(g('issue_date'))}" if g("issue_date") else "") + "."
    if doc_type_code == "ID_CARD":
        return _join(f"ID card of {g('full_name')}" if g("full_name") else "ID card",
                     f"(ID {g('id_number')})" if g("id_number") else "",
                     f"from {g('organization')}" if g("organization") else "",
                     f"in {g('department')}" if g("department") else "",
                     f"valid until {T.pretty_date(g('valid_until'))}" if g("valid_until") else "") + "."
    return ""


# ----------------------------------------------------------------- extractive
def looks_like_prose(text):
    """True when the text has at least two real sentences (not just form lines)."""
    return len([x for x in T.split_sentences(text) if len(x.split()) >= 6]) >= 2


def _simple_rank(sentences, n):
    freq = Counter(T.content_words(" ".join(sentences)))
    if not freq:
        return sentences[:n]
    scored = []
    for i, s in enumerate(sentences):
        ws = T.content_words(s)
        if len(ws) < 4:
            continue
        scored.append((sum(freq[w] for w in ws) / (len(ws) ** 0.7), i, s))
    top = sorted(scored, reverse=True)[:n]
    return [s for _score, _i, s in sorted(top, key=lambda x: x[1])]


def _sumy_rank(sentences, n):
    from sumy.nlp.stemmers import Stemmer
    from sumy.nlp.tokenizers import Tokenizer
    from sumy.parsers.plaintext import PlaintextParser
    from sumy.summarizers.text_rank import TextRankSummarizer
    from sumy.utils import get_stop_words

    class OfflineTokenizer(Tokenizer):
        """sumy's default tokenizer needs NLTK data downloads; this one does not."""

        def __init__(self):  # noqa: D401 - skip the NLTK-loading parent __init__
            self._language = "english"

        def to_sentences(self, paragraph):
            return T.split_sentences(paragraph) or [paragraph]

        def to_words(self, sentence):
            return T.WORD_RE.findall(sentence)

    tokenizer = OfflineTokenizer()
    parser = PlaintextParser.from_string("\n".join(sentences), tokenizer)
    summarizer = TextRankSummarizer(Stemmer("english"))
    summarizer.stop_words = get_stop_words("english")
    picked = [str(s) for s in summarizer(parser.document, n)]
    order = {s: i for i, s in enumerate(sentences)}
    return sorted(picked, key=lambda s: order.get(s, 0))


def extractive(text, n=3):
    sentences = [s for s in T.split_sentences(text) if len(s.split()) >= 5]
    if len(sentences) <= n:
        return sentences, "simple"
    try:
        out = _sumy_rank(sentences, n)
        if out:
            return out, "sumy"
    except Exception as exc:  # noqa: BLE001
        logger.warning("sumy unavailable or failed (%s); using the simple summariser", exc)
    return _simple_rank(sentences, n), "simple"


def make_summary(doc_type_code, data, text):
    """Return (summary_text, backend)."""
    head = headline(doc_type_code, data)
    backend = "fields"
    body = ""
    if not head and looks_like_prose(text):  # known types: the factual headline is the summary
        sentences, backend = extractive(text, 3)
        body = " ".join(sentences)
    if not head and not body:
        first = next((l.strip() for l in (text or "").splitlines() if len(l.strip()) > 8), "")
        head, backend = first[:200], "first-line"
    return (head + ("\n\n" + body if body else "")).strip(), backend
