"""Parsing and validation helpers shared by extraction and reviewer edits.

Dates are read day-first (31/01/2026), the Indian convention.
"""
import re
from datetime import date

# Word boundaries that understand Hindi (Devanagari) as well as English.
WB_L = r"(?<![\w\u0900-\u097F])"
WB_R = r"(?![\w\u0900-\u097F])"

MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
    "january": 1, "february": 2, "march": 3, "april": 4, "june": 6, "july": 7,
    "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}

_D_ISO = re.compile(r"(?<!\d)(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?!\d)")
_D_NUM = re.compile(r"(?<!\d)(\d{1,2})[-/.](\d{1,2})[-/.](\d{2}|\d{4})(?!\d)")
_D_DMY = re.compile(r"(?<![A-Za-z0-9])(\d{1,2})(?:st|nd|rd|th)?[\s\-]+([A-Za-z]{3,9})\.?,?[\s\-]+(\d{4})(?!\d)")
_D_MDY = re.compile(r"(?<![A-Za-z])([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})(?!\d)")

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
URL_RE = re.compile(r"(?:https?://)?(?:www\.)?[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}(?:/[^\s,;]*)?")
PHONE_RE = re.compile(r"\+?\d[\d\s\-()]{8,}\d")
NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?(?![\d%])")


def _mk_date(y, m, d):
    try:
        return date(y, m, d)
    except ValueError:
        return None


def parse_date(text):
    """Return an ISO date string (YYYY-MM-DD) for the earliest valid date in
    `text`, or None."""
    if not text:
        return None
    found = []
    for m in _D_ISO.finditer(text):
        d = _mk_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if d:
            found.append((m.start(), d))
    for m in _D_NUM.finditer(text):
        a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        y = y if len(m.group(3)) == 4 else (2000 + y if y < 70 else 1900 + y)
        d = _mk_date(y, b, a) or _mk_date(y, a, b)  # day-first, fall back to month-first
        if d:
            found.append((m.start(), d))
    for m in _D_DMY.finditer(text):
        mon = MONTHS.get(m.group(2).lower())
        if mon:
            d = _mk_date(int(m.group(3)), mon, int(m.group(1)))
            if d:
                found.append((m.start(), d))
    for m in _D_MDY.finditer(text):
        mon = MONTHS.get(m.group(1).lower())
        if mon:
            d = _mk_date(int(m.group(3)), mon, int(m.group(2)))
            if d:
                found.append((m.start(), d))
    if not found:
        return None
    found.sort(key=lambda x: x[0])
    return found[0][1].isoformat()


def parse_money(fragment):
    """Amount from text like 'Rs. 1,180.00' or '(18%) 180.00'. Returns a string
    with 2 decimals, or None if the fragment contains words (i.e. not an amount)."""
    if not fragment:
        return None
    cleaned = re.sub(r"(?i)\b(rs|inr|usd|eur|rupees|only)\b\.?", " ", fragment)
    if re.search(r"[A-Za-z]{2,}", cleaned):
        return None
    nums = NUMBER_RE.findall(cleaned)
    if not nums:
        return None
    try:
        return f"{float(nums[-1].replace(',', '')):.2f}"
    except ValueError:
        return None


def clean_text(s):
    return re.sub(r"\s+", " ", s or "").strip(" \t:-|")


def split_list(text):
    """Split 'Python, Django • SQL' / bullet lines into clean items."""
    items = []
    for line in re.split(r"[\r\n]+", text or ""):
        if ":" in line:
            left, right = line.split(":", 1)
            if len(left.strip()) <= 25 and right.strip():
                line = right
        for part in re.split(r"[,;•|●▪·]|(?:^|\s)[-*]\s", line):
            part = clean_text(part)
            if part and part not in items and len(part) <= 80:
                items.append(part)
    return items


def shape(ftype, fragment):
    """Pull a value of `ftype` out of a text fragment; None if it has the wrong
    shape. Used for both label and regex extraction."""
    frag = (fragment or "").strip()
    if not frag:
        return None
    if ftype == "date":
        return parse_date(frag[:60])
    if ftype == "money":
        return parse_money(frag[:60])
    if ftype == "number":
        m = NUMBER_RE.search(frag)
        return m.group(0).replace(",", "") if m else None
    if ftype == "email":
        m = EMAIL_RE.search(frag)
        return m.group(0) if m else None
    if ftype == "phone":
        for m in PHONE_RE.finditer(frag):
            digits = re.sub(r"\D", "", m.group(0))
            if 10 <= len(digits) <= 15:
                return clean_text(m.group(0))
        return None
    if ftype == "url":
        m = URL_RE.search(frag)
        return m.group(0).rstrip(".,;)") if m else None
    if ftype == "id_number":
        m = re.match(r"[\s:#\-.]*([A-Za-z0-9][A-Za-z0-9\-/]{2,})", frag)
        if m and re.search(r"\d", m.group(1)):
            return m.group(1)
        return None
    if ftype == "list":
        items = split_list(frag)
        return items or None
    value = clean_text(frag)
    if not re.search(r"\w", value):  # any letter/digit in any script (English, Hindi...)
        return None
    limit = 600 if ftype == "text" else 150
    return value[:limit]


def coerce_strict(ftype, raw):
    """Validate a reviewer-typed value. Returns (value, error)."""
    if ftype == "list":
        items = raw if isinstance(raw, list) else split_list(raw)
        return (items, None) if items else (None, "Enter at least one item.")
    raw = raw if isinstance(raw, str) else str(raw)
    if ftype == "text":  # keep line breaks (sections like Education)
        text = "\n".join(clean_text(l) for l in raw.splitlines() if clean_text(l))
        return (text[:2000], None) if text else (None, "This field is required.")
    text = clean_text(raw)
    if not text:
        return None, "This field is required."
    if ftype == "date":
        v = parse_date(text)
        return (v, None) if v else (None, "Enter a valid date, e.g. 2026-01-31 or 31/01/2026.")
    if ftype == "money":
        v = parse_money(text)
        return (v, None) if v else (None, "Enter an amount, e.g. 1180.00.")
    if ftype == "number":
        try:
            float(text.replace(",", ""))
            return text.replace(",", ""), None
        except ValueError:
            return None, "Enter a number."
    if ftype == "email":
        return (text, None) if EMAIL_RE.fullmatch(text) else (None, "Enter a valid email address.")
    if ftype == "phone":
        digits = re.sub(r"\D", "", text)
        return (text, None) if 10 <= len(digits) <= 15 else (None, "Enter a valid phone number (10-15 digits).")
    if ftype == "url":
        return (text, None) if URL_RE.fullmatch(text) else (None, "Enter a valid URL.")
    if ftype == "id_number":
        ok = re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9\-/ ._]{2,39}", text) and re.search(r"\d", text)
        return (text, None) if ok else (None, "Enter a valid ID (3-40 characters, must include a digit).")
    limit = 2000 if ftype == "text" else 150
    return (text[:limit], None)
