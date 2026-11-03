"""Text embeddings for semantic search, chat and AI classification.

Backend 'minilm': sentence-transformers all-MiniLM-L6-v2 (local, CPU). Used when it is
installed and its files are already on disk (python manage.py download_models).
Backend 'hash': a small built-in embedder (hashed words + a document-domain synonym
map, numpy only). It needs no download, so semantic features keep working on a
machine where the model is missing. The Admin health page shows which one is active.
"""
import logging
import re
import threading
import zlib

import numpy as np
from django.conf import settings

from . import text as T

logger = logging.getLogger("transformodocs.v2")

MODEL_NAME = getattr(settings, "EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
PREFERRED = getattr(settings, "EMBEDDING_BACKEND", "auto")  # auto | minilm | hash
HASH_DIM = 2048
MIN_SCORE = {"minilm": 0.28, "hash": 0.16}

_lock = threading.Lock()
_model = None
_model_failed = False

SYNONYM_GROUPS = [
    "invoice bill receipt statement", "total amount sum payable balance outstanding due grand price cost charge",
    "payment pay paid settlement remittance", "customer client buyer purchaser billed",
    "vendor seller supplier merchant company firm", "date dated day issued issue",
    "name applicant holder candidate recipient student person", "address location residence",
    "phone mobile contact telephone cell number", "email mail", "certificate certification diploma credential award",
    "course training program programme bootcamp class", "skill skills expertise proficiency technologies tools technology",
    "experience internship employment work job career", "education degree university college school qualification",
    "tax gst vat levy", "id identity identification enrollment enrolment registration roll",
    "birth dob born", "valid expiry expires validity until", "resume cv biodata profile",
]
_SYN = {}
for i, group in enumerate(SYNONYM_GROUPS):
    for w in group.split():
        _SYN.setdefault(w, []).append(f"syn{i}")


def _stem(w):
    for suf in ("ing", "ed", "es", "s"):
        if len(w) > len(suf) + 3 and w.endswith(suf):
            return w[: -len(suf)]
    return w


def _tokens(text):
    stops = T.stop_words()
    out = []
    for w in T.words(text):
        if len(w) < 2 or w in stops:
            continue
        out.append(_stem(w))
        for g in _SYN.get(w, []) + _SYN.get(_stem(w), []):
            out.append(g)
    return out


def _hash_embed(texts):
    mat = np.zeros((len(texts), HASH_DIM), dtype=np.float32)
    for r, text in enumerate(texts):
        toks = _tokens(text)
        counts = {}
        for t in toks:
            counts[t] = counts.get(t, 0) + 1
        for a, b in zip(toks, toks[1:]):  # bigrams add a little word-order signal
            if not a.startswith("syn") and not b.startswith("syn"):
                key = a + "_" + b
                counts[key] = counts.get(key, 0) + 0.5
        for tok, c in counts.items():
            h = zlib.crc32(tok.encode("utf-8"))
            mat[r, h % HASH_DIM] += (1.0 if (h >> 16) & 1 else -1.0) * (1.0 + np.log(c))
        norm = np.linalg.norm(mat[r])
        if norm:
            mat[r] /= norm
    return mat


def _load_model():
    global _model, _model_failed
    if _model is not None or _model_failed:
        return _model
    with _lock:
        if _model is not None or _model_failed:
            return _model
        try:
            from sentence_transformers import SentenceTransformer

            _model = SentenceTransformer(MODEL_NAME, device="cpu")
        except Exception as exc:  # noqa: BLE001 - not installed / model files missing / offline
            _model_failed = True
            logger.warning("MiniLM embeddings unavailable (%s); using the built-in hash embedder", exc)
    return _model


def active_backend():
    if PREFERRED == "hash":
        return "hash"
    return "minilm" if _load_model() is not None else "hash"


def embed(texts, backend=None):
    """Return a normalised float32 matrix (len(texts), dim)."""
    texts = [t or "" for t in texts]
    backend = backend or active_backend()
    if backend == "minilm":
        model = _load_model()
        if model is not None:
            vecs = model.encode(texts, normalize_embeddings=True, show_progress_bar=False, batch_size=32)
            return np.asarray(vecs, dtype=np.float32)
    return _hash_embed(texts)


def to_bytes(vec):
    return np.asarray(vec, dtype=np.float32).tobytes()


def from_bytes(blob):
    return np.frombuffer(bytes(blob), dtype=np.float32)


def chunk_text(text, max_chars=420):
    """Split text into overlapping chunks along line/sentence boundaries."""
    units = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        units.extend([line] if len(line) <= max_chars else T.split_sentences(line))
    chunks, cur = [], []
    size = 0
    for u in units:
        if cur and size + len(u) > max_chars:
            chunks.append("\n".join(cur))
            cur = cur[-1:]  # one-line overlap keeps context
            size = len(cur[0]) if cur else 0
        cur.append(u)
        size += len(u) + 1
    if cur:
        chunks.append("\n".join(cur))
    return chunks[:200]


def ensure_chunks(version, backend=None):
    """Create (or rebuild for another backend) the embedded chunks of a version."""
    from intelligence.models import DocumentChunk

    backend = backend or active_backend()
    existing = list(DocumentChunk.objects.filter(version=version).values_list("backend", flat=True)[:1])
    if existing and existing[0] == backend:
        return backend
    DocumentChunk.objects.filter(version=version).delete()
    chunks = chunk_text(version.extracted_text)
    if not chunks:
        return backend
    vecs = embed(chunks, backend)
    DocumentChunk.objects.bulk_create([
        DocumentChunk(version=version, index=i, text=c, embedding=to_bytes(v), backend=backend)
        for i, (c, v) in enumerate(zip(chunks, vecs))])
    return backend


def highlight(text, query):
    from django.utils.html import escape
    from django.utils.safestring import mark_safe

    out = escape(text)
    toks = sorted({w for w in T.words(query) if len(w) > 2 and w not in T.stop_words()}, key=len, reverse=True)
    for tok in toks:
        out = re.sub("(" + re.escape(escape(tok)) + ")", r"<mark>\1</mark>", out, flags=re.I)
    return mark_safe(out.replace("\n", "<br>"))
