"""V2 environment checks (all non-critical: V2 degrades gracefully)."""
import importlib

from . import embeddings, nlp, translate


def _row(name, ok, detail, fix=""):
    return {"name": name, "ok": ok, "detail": detail, "fix": fix, "critical": False}


def checks():
    out = []
    if nlp.spacy_available():
        out.append(_row("V2 entities (spaCy)", True, f"model '{nlp.SPACY_MODEL}' loaded"))
    else:
        out.append(_row("V2 entities (spaCy)", False, "model not available: using regex entities",
                        "pip install spacy ; python -m spacy download en_core_web_sm"))
    try:
        importlib.import_module("sumy")
        out.append(_row("V2 summaries (sumy)", True, "sumy installed"))
    except ImportError:
        out.append(_row("V2 summaries (sumy)", False, "sumy missing: using the simple summariser", "pip install sumy"))
    backend = embeddings.active_backend()
    if backend == "minilm":
        out.append(_row("V2 semantic search", True, "MiniLM embeddings active"))
    else:
        out.append(_row("V2 semantic search", False,
                        "MiniLM not available: using the built-in embedder (works, less accurate)",
                        "pip install sentence-transformers ; python manage.py download_models"))
    st = translate.status()
    if {"en-hi", "hi-en"} <= set(st["pairs"]):
        out.append(_row("V2 translation (Argos)", True, "English <-> Hindi packs installed"))
    else:
        out.append(_row("V2 translation (Argos)", False,
                        f"packs missing (installed: {', '.join(st['pairs']) or 'none'})",
                        "pip install argostranslate ; python manage.py download_models"))
    return out
