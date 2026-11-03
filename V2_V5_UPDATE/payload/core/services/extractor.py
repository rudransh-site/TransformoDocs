"""Schema-driven field extraction.

Reads DocumentType.schema (see core/seed_data.py for the format) and runs each
field's strategies in order. `lines` is a list of {"text": str, "conf": float}
(conf = OCR confidence 0-100, or 100 for native text).

Rules worth knowing:
 * A value is only accepted if it has the right SHAPE for its type (a date must
   parse, a money value must be an amount...). Invalid candidates count as missing.
 * For shaped types (date, money, id_number, email, phone...) a label whose value
   is empty falls through to the next line automatically.
 * Two fields on one line ("Name: A   Roll No: 12") are separated by cutting at the
   next known label that ends with a colon.
"""
import bisect
import re
from statistics import mean

import unicodedata

from .text_utils import WB_L, WB_R, clean_text, shape, split_list

SHAPED = {"date", "money", "number", "email", "phone", "url", "id_number"}


def _anchor_rx(anchor):
    return re.compile(WB_L + re.escape(anchor.strip()).replace(r"\ ", r"\s+") + WB_R, re.I)


class _Ctx:
    def __init__(self, schema, lines):
        self.lines = lines
        self.texts = [l["text"] for l in lines]
        self.full = "\n".join(self.texts)
        self.offsets, pos = [], 0
        for t in self.texts:
            self.offsets.append(pos)
            pos += len(t) + 1
        anchors = set()
        for f in schema.get("fields", []):
            for s in f.get("strategies", []):
                if s.get("kind") == "label":
                    anchors.update(a.lower() for a in s.get("anchors", []))
        alts = "|".join(re.escape(a).replace(r"\ ", r"\s+") for a in sorted(anchors, key=len, reverse=True))
        self.cut_rx = re.compile(WB_L + r"(?:" + alts + r")\s*:", re.I) if alts else None
        self.label_start_rx = (
            re.compile(r"^\s*(?:" + alts + r")" + WB_R, re.I) if alts else None
        )

    def line_index(self, offset):
        return max(0, bisect.bisect_right(self.offsets, offset) - 1)

    def conf(self, idxs):
        vals = [self.lines[i]["conf"] for i in idxs if 0 <= i < len(self.lines)]
        return round(mean(vals), 1) if vals else 0.0

    def cut(self, fragment):
        if self.cut_rx:
            m = self.cut_rx.search(fragment)
            if m:
                return fragment[: m.start()]
        return fragment


def _label(field, strat, ctx):
    ftype = field["type"]
    allow_next = strat.get("next_line") or ftype in SHAPED
    max_lines = int(strat.get("max_lines", 1))
    for anchor in sorted(strat["anchors"], key=len, reverse=True):
        rx = _anchor_rx(anchor)
        for i, text in enumerate(ctx.texts):
            m = rx.search(text)
            if not m:
                continue
            frag = ctx.cut(text[m.end():].lstrip(" \t:-–—.#|"))
            value, idxs = shape(ftype, frag), [i]
            if value is None and allow_next:
                collected, idxs = [], [i]
                for j in range(i + 1, min(i + 1 + max_lines, len(ctx.texts))):
                    t = ctx.texts[j].strip()
                    if not t or (ctx.label_start_rx and ctx.label_start_rx.match(t) and ftype not in SHAPED):
                        break
                    collected.append(ctx.cut(t))
                    idxs.append(j)
                    if ftype in SHAPED:
                        value = shape(ftype, collected[-1])
                        if value is not None:
                            idxs = [i, j]
                            break
                if value is None and collected and ftype not in SHAPED:
                    value = shape(ftype, " ".join(collected))
            if value is not None:
                return value, ctx.conf(idxs)
    return None


def _regex(field, strat, ctx):
    try:
        rx = re.compile(strat["pattern"], re.M)
    except re.error:
        return None
    for m in rx.finditer(ctx.full):
        raw = m.group(1) if m.groups() else m.group(0)
        value = shape(field["type"], raw)
        if value is not None:
            return value, ctx.conf([ctx.line_index(m.start(1) if m.groups() else m.start())])
    return None


def _first_line(field, strat, ctx):
    skips = [re.compile(r"\b" + re.escape(w) + r"\b", re.I) for w in strat.get("skip_if_contains", [])]
    for i, text in enumerate(ctx.texts[:15]):
        s = text.strip()
        if len(re.findall(r"[^\W\d_]", s)) < 2 or len(s) > 80:  # letters in any script
            continue
        if "@" in s or re.search(r"\d{6,}", s) or any(r.search(s) for r in skips):
            continue
        value = shape(field["type"], s)
        if value is not None:
            return value, ctx.conf([i])
    return None


def _norm_heading(s):
    """Lower-case letters (any script) and spaces only, so headings compare reliably."""
    kept = "".join(ch for ch in (s or "").lower()
                   if ch.isalpha() or ch == " " or unicodedata.category(ch).startswith("M"))
    return re.sub(r"\s+", " ", kept).strip()


def _section(field, strat, ctx):
    heads = {_norm_heading(h) for h in strat["headings"]}
    stops = {_norm_heading(h) for h in strat.get("stop_headings", [])} - heads
    for i, text in enumerate(ctx.texts):
        t = text.strip()
        same_line = ""
        if len(t) <= 40 and _norm_heading(t.rstrip(":")) in heads:
            pass
        else:
            m = re.match(r"^([^\W\d_][^\W\d_ ]*(?: [^\W\d_ ]+){0,3})\s*:\s*(\S.*)$", t)
            if not (m and _norm_heading(m.group(1)) in heads):
                continue
            same_line = m.group(2)
        body, idxs = ([same_line] if same_line else []), [i]
        for j in range(i + 1, min(i + 41, len(ctx.texts))):
            nxt = ctx.texts[j].strip()
            if _norm_heading(nxt.rstrip(":")) in stops and len(nxt) <= 40:
                break
            body.append(nxt)
            idxs.append(j)
        body = [b for b in body if b]
        if not body:
            continue
        joined = "\n".join(body)
        value = split_list(joined) if field["type"] == "list" else joined[:2000]
        if value:
            return value, ctx.conf(idxs)
    return None


_RUNNERS = {"label": _label, "regex": _regex, "first_line": _first_line, "section": _section}


def extract_fields(schema, lines):
    """Return (structured_data, field_confidences). Missing fields are omitted."""
    ctx = _Ctx(schema, lines)
    data, confs = {}, {}
    for field in schema.get("fields", []):
        for strat in field.get("strategies", []):
            runner = _RUNNERS.get(strat.get("kind"))
            if runner is None:
                continue
            try:
                result = runner(field, strat, ctx)
            except Exception:  # noqa: BLE001 - one bad rule must not sink the document
                result = None
            if result is not None:
                data[field["key"]], confs[field["key"]] = result
                break
    return data, confs
