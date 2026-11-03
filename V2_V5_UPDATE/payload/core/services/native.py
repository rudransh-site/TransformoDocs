"""Native (text-layer) reading for PDF and DOCX, plus layout blocks."""
import re
import zipfile
from statistics import median

import pymupdf


_LIGATURES = {"\ufb00": "ff", "\ufb01": "fi", "\ufb02": "fl", "\ufb03": "ffi", "\ufb04": "ffl", "\ufb05": "st",
              "\ufb06": "st", "\u00a0": " ", "\u200b": "", "\u00ad": ""}


def normalize_text(text):
    """PDFs often store 'fi' as one ligature character, which would break label matching
    ('Certificate' -> 'Certi\ufb01cate'). Replace ligatures and invisible spaces."""
    for bad, good in _LIGATURES.items():
        if bad in text:
            text = text.replace(bad, good)
    return text


def _alnum_ratio(text):
    chars = [c for c in text if not c.isspace()]
    return (sum(c.isalnum() for c in chars) / len(chars)) if chars else 0.0


def pdf_page_is_native(page, min_chars):
    txt = page.get_text("text") or ""
    stripped = re.sub(r"\s", "", txt)
    return len(stripped) >= min_chars and _alnum_ratio(txt) >= 0.6


def pdf_native_page(page):
    """Return (lines, blocks, width, height) for a page with a text layer."""
    lines = []
    for raw in normalize_text(page.get_text("text", sort=True) or "").splitlines():
        t = raw.strip()
        if t:
            lines.append({"text": t, "conf": 100.0})
    blocks, sizes = [], []
    try:
        d = page.get_text("dict", sort=True)
    except Exception:  # noqa: BLE001
        d = {"blocks": []}
    raw_blocks = []
    for b in d.get("blocks", []):
        if b.get("type") != 0:
            continue
        texts, size = [], 0.0
        for ln in b.get("lines", []):
            spans = ln.get("spans", [])
            t = normalize_text("".join(s.get("text", "") for s in spans)).strip()
            if t:
                texts.append(t)
                for s in spans:
                    if s.get("text", "").strip():
                        sizes.append(s.get("size", 0))
                        size = max(size, s.get("size", 0))
        if texts:
            raw_blocks.append(("\n".join(texts), size, [round(x) for x in b["bbox"]], len(texts)))
    body = median(sizes) if sizes else 0
    for text, size, bbox, n in raw_blocks:
        if body and size >= body * 1.2 and len(text) <= 100 and n <= 2:
            btype, level = "heading", (1 if size >= body * 1.6 else 2)
        elif n == 1 and ":" in text[:40]:
            btype, level = "kv", 0
        else:
            btype, level = "paragraph", 0
        blocks.append({"type": btype, "level": level, "text": text, "bbox": bbox, "conf": 100.0})
    return lines, blocks, round(page.rect.width), round(page.rect.height)


# ----------------------------------------------------------------- DOCX
def docx_text_and_layout(path):
    """Return (lines, blocks) from a DOCX in document order (paragraphs + tables)."""
    import docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    document = docx.Document(path)
    lines, blocks = [], []

    def add_line(t):
        t = normalize_text(t).strip()
        if t:
            lines.append({"text": t, "conf": 100.0})

    try:
        items = list(document.iter_inner_content())
    except AttributeError:  # older python-docx
        items = list(document.paragraphs) + list(document.tables)
    for item in items:
        if isinstance(item, Paragraph):
            text = item.text.strip()
            if not text:
                continue
            style = (item.style.name or "") if item.style is not None else ""
            if style.lower().startswith("heading") or style.lower() == "title":
                m = re.search(r"(\d+)", style)
                blocks.append({"type": "heading", "level": int(m.group(1)) if m else 1, "text": text, "conf": 100.0})
            else:
                blocks.append({"type": "kv" if ":" in text[:40] and len(text) < 120 else "paragraph",
                               "level": 0, "text": text, "conf": 100.0})
            for part in text.splitlines():
                add_line(part)
        elif isinstance(item, Table):
            rows = []
            for row in item.rows:
                cells, prev = [], None
                for c in row.cells:
                    ct = " ".join(c.text.split())
                    if ct and ct != prev:
                        cells.append(ct)
                    prev = ct
                if cells:
                    rows.append(cells)
                    add_line(" | ".join(cells))
            if rows:
                blocks.append({"type": "table", "level": 0, "rows": rows,
                               "text": "\n".join(" | ".join(r) for r in rows), "conf": 100.0})
    return lines, blocks


def docx_images(path, limit=10):
    """Bytes of embedded images (for image-only DOCX files), in order."""
    out = []
    with zipfile.ZipFile(path) as z:
        names = sorted(n for n in z.namelist() if n.startswith("word/media/")
                       and n.lower().endswith((".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tif", ".tiff")))
        for n in names[:limit]:
            out.append(z.read(n))
    return out
