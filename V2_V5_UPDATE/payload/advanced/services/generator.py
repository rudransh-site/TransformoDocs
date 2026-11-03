"""Template-based document generator with smart autofill (no LLM).

Templates: invoice, certificate. Data -> validated -> HTML -> PDF (PyMuPDF Story) or DOCX.
The PDF layout is written so TransformoDocs' own extractor reads it back correctly,
so a generated document can be saved into the system and verified automatically.
"""
import io
import re
from datetime import date
from decimal import Decimal, InvalidOperation

from django.template.loader import render_to_string

from core.services.text_utils import parse_date

TEMPLATES = {
    "invoice": {"label": "Invoice", "blurb": "Seller, customer, line items, GST and totals."},
    "certificate": {"label": "Certificate", "blurb": "Completion, achievement or participation."},
}
CERT_TYPES = ["Completion", "Achievement", "Participation"]


class GenError(Exception):
    def __init__(self, errors):
        super().__init__("invalid")
        self.errors = errors  # {field: message}


# ---------------------------------------------------------------- numbers
_ONES = ("Zero One Two Three Four Five Six Seven Eight Nine Ten Eleven Twelve Thirteen Fourteen Fifteen Sixteen "
         "Seventeen Eighteen Nineteen").split()
_TENS = "_ _ Twenty Thirty Forty Fifty Sixty Seventy Eighty Ninety".split()


def _below_100(n):
    return _ONES[n] if n < 20 else _TENS[n // 10] + (" " + _ONES[n % 10] if n % 10 else "")


def _below_1000(n):
    out = []
    if n >= 100:
        out.append(_ONES[n // 100] + " Hundred")
        n %= 100
    if n:
        out.append(_below_100(n))
    return " ".join(out)


def amount_in_words(amount):
    """4130.50 -> 'Rupees Four Thousand One Hundred Thirty and Fifty Paise Only' (Indian grouping)."""
    amount = Decimal(amount).quantize(Decimal("0.01"))
    rupees, paise = int(amount), int((amount - int(amount)) * 100)
    if rupees == 0 and paise == 0:
        return "Rupees Zero Only"
    parts, n = [], rupees
    for unit, div in (("Crore", 10_000_000), ("Lakh", 100_000), ("Thousand", 1000)):
        if n >= div:
            parts.append(_below_1000(n // div) + " " + unit)
            n %= div
    if n:
        parts.append(_below_1000(n))
    words = " ".join(parts) if parts else "Zero"
    text = f"Rupees {words}"
    if paise:
        text += f" and {_below_100(paise)} Paise"
    return text + " Only"


def _money(v):
    return f"{v:,.2f}"


def _dec(raw, name, errors, minimum=None, maximum=None):
    try:
        v = Decimal(str(raw).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        errors[name] = "Enter a number."
        return None
    if minimum is not None and v < minimum:
        errors[name] = f"Must be at least {minimum}."
    if maximum is not None and v > maximum:
        errors[name] = f"Must be at most {maximum}."
    return v


def _date(raw, name, errors, required=True):
    raw = (raw or "").strip()
    if not raw:
        if required:
            errors[name] = "This field is required."
        return None
    iso = parse_date(raw)
    if not iso:
        errors[name] = "Enter a valid date."
        return None
    return date.fromisoformat(iso)


def _text(raw, name, errors, required=True, maxlen=150):
    v = " ".join((raw or "").split())
    if required and not v:
        errors[name] = "This field is required."
    if len(v) > maxlen:
        errors[name] = f"Keep this under {maxlen} characters."
    return v


# --------------------------------------------------------------- defaults
def defaults(template, today=None):
    today = today or date.today()
    if template == "invoice":
        return {"seller_name": "", "seller_address": "", "seller_gstin": "",
                "invoice_number": f"INV-{today.year}-0001", "invoice_date": today.isoformat(),
                "due_date": "", "customer_name": "", "customer_address": "", "gst_rate": "18", "notes": "",
                "items": [{"description": "", "qty": "1", "unit_price": ""}]}
    return {"title_type": "Completion", "recipient_name": "", "course": "", "organization": "",
            "issue_date": today.isoformat(), "certificate_id": f"CERT-{today.year}-0001", "signatory": ""}


# ------------------------------------------------------------- validation
def _items_from(data):
    """Accept either a list of dicts or parallel lists (from a form)."""
    if isinstance(data.get("items"), list):
        return data["items"]
    desc, qty, price = (data.get("item_desc") or [], data.get("item_qty") or [], data.get("item_price") or [])
    return [{"description": d, "qty": q, "unit_price": p} for d, q, p in zip(desc, qty, price)]


def clean_invoice(data):
    errors = {}
    c = {
        "seller_name": _text(data.get("seller_name"), "seller_name", errors),
        "seller_address": _text(data.get("seller_address"), "seller_address", errors, False, 250),
        "seller_gstin": _text(data.get("seller_gstin"), "seller_gstin", errors, False, 20).upper(),
        "invoice_number": _text(data.get("invoice_number"), "invoice_number", errors, True, 40),
        "customer_name": _text(data.get("customer_name"), "customer_name", errors),
        "customer_address": _text(data.get("customer_address"), "customer_address", errors, False, 250),
        "notes": _text(data.get("notes"), "notes", errors, False, 300),
    }
    if c["invoice_number"] and not re.search(r"\d", c["invoice_number"]):
        errors["invoice_number"] = "The invoice number must contain a digit."
    if c["seller_gstin"] and not re.fullmatch(r"\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]", c["seller_gstin"]):
        errors["seller_gstin"] = "Enter a valid 15-character GSTIN or leave it empty."
    c["invoice_date"] = _date(data.get("invoice_date"), "invoice_date", errors)
    c["due_date"] = _date(data.get("due_date"), "due_date", errors, required=False)
    if c["invoice_date"] and c["due_date"] and c["due_date"] < c["invoice_date"]:
        errors["due_date"] = "The due date cannot be before the invoice date."
    gst = _dec(data.get("gst_rate", "18") or "0", "gst_rate", errors, Decimal(0), Decimal(100))
    items = []
    for i, it in enumerate(_items_from(data)[:20]):
        desc = " ".join(str(it.get("description", "")).split())
        if not desc and not str(it.get("unit_price", "")).strip():
            continue
        if not desc:
            errors["items"] = f"Item {i + 1} needs a description."
            continue
        qty = _dec(it.get("qty", "1") or "1", "items", errors, Decimal("0.01"), Decimal(100000))
        price = _dec(it.get("unit_price", "") or "", "items", errors, Decimal(0), Decimal(100_000_000))
        if qty is None or price is None:
            if "items" not in errors:
                errors["items"] = f"Check the quantity and price of item {i + 1}."
            continue
        items.append({"description": desc[:120], "qty": qty, "unit_price": price,
                      "amount": (qty * price).quantize(Decimal("0.01"))})
    if not items and "items" not in errors:
        errors["items"] = "Add at least one item with a description, quantity and price."
    if errors:
        raise GenError(errors)
    subtotal = sum((i["amount"] for i in items), Decimal(0)).quantize(Decimal("0.01"))
    tax = (subtotal * gst / 100).quantize(Decimal("0.01"))
    total = subtotal + tax
    c.update(items=items, gst_rate=gst.normalize() if gst == gst.to_integral() else gst, subtotal=subtotal,
             tax=tax, total=total, words=amount_in_words(total))
    return c


def clean_certificate(data):
    errors = {}
    c = {
        "title_type": data.get("title_type") if data.get("title_type") in CERT_TYPES else "Completion",
        "recipient_name": _text(data.get("recipient_name"), "recipient_name", errors, True, 80),
        "course": _text(data.get("course"), "course", errors, True, 120),
        "organization": _text(data.get("organization"), "organization", errors, True, 100),
        "certificate_id": _text(data.get("certificate_id"), "certificate_id", errors, True, 40),
        "signatory": _text(data.get("signatory"), "signatory", errors, False, 80),
    }
    if c["certificate_id"] and not re.search(r"\d", c["certificate_id"]):
        errors["certificate_id"] = "The certificate ID must contain a digit."
    c["issue_date"] = _date(data.get("issue_date"), "issue_date", errors)
    if errors:
        raise GenError(errors)
    return c


def clean(template, data):
    if template not in TEMPLATES:
        raise GenError({"template": "Unknown template."})
    return clean_invoice(data) if template == "invoice" else clean_certificate(data)


def serialisable(template, cleaned):
    """JSON-safe copy of cleaned data (for the history table)."""
    out = {}
    for k, v in cleaned.items():
        if isinstance(v, date):
            out[k] = v.isoformat()
        elif isinstance(v, Decimal):
            out[k] = str(v)
        elif k == "items":
            out[k] = [{"description": i["description"], "qty": str(i["qty"]), "unit_price": str(i["unit_price"])}
                      for i in v]
        else:
            out[k] = v
    out.pop("words", None)
    return out


# --------------------------------------------------------------- rendering
def fmt_date(d):
    return f"{d.day} {d.strftime('%B %Y')}"


def render_html(template, cleaned):
    ctx = dict(cleaned)
    ctx["fmt_date"] = fmt_date
    if template == "invoice":
        ctx["rows"] = [{"n": i + 1, "description": it["description"], "qty": f"{it['qty'].normalize():f}",
                        "price": _money(it["unit_price"]), "amount": _money(it["amount"])}
                       for i, it in enumerate(cleaned["items"])]
        ctx.update(invoice_date_text=fmt_date(cleaned["invoice_date"]),
                   due_date_text=fmt_date(cleaned["due_date"]) if cleaned["due_date"] else "",
                   subtotal_text=_money(cleaned["subtotal"]), tax_text=_money(cleaned["tax"]),
                   total_text=_money(cleaned["total"]))
    else:
        ctx["issue_date_text"] = fmt_date(cleaned["issue_date"])
    return render_to_string(f"advanced/doc_{template}.html", ctx)


def html_to_pdf(html):
    import pymupdf

    buf = io.BytesIO()
    writer = pymupdf.DocumentWriter(buf)
    story = pymupdf.Story(html=html)
    mediabox = pymupdf.paper_rect("a4")
    where = mediabox + (40, 40, -40, -40)
    more = 1
    pages = 0
    while more and pages < 5:
        dev = writer.begin_page(mediabox)
        more, _filled = story.place(where)
        story.draw(dev)
        writer.end_page()
        pages += 1
    writer.close()
    return buf.getvalue()


def to_pdf(template, cleaned):
    return html_to_pdf(render_html(template, cleaned))


def to_docx(template, cleaned):
    import docx
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt

    d = docx.Document()

    def para(text, bold=False, size=11, align=None):
        p = d.add_paragraph()
        r = p.add_run(text)
        r.bold, r.font.size = bold, Pt(size)
        if align:
            p.alignment = align
        return p

    if template == "invoice":
        c = cleaned
        para(c["seller_name"], True, 18)
        if c["seller_address"]:
            para(c["seller_address"], size=10)
        if c["seller_gstin"]:
            para(f"GSTIN: {c['seller_gstin']}", size=10)
        para("TAX INVOICE", True, 14)
        para(f"Invoice No: {c['invoice_number']}")
        para(f"Invoice Date: {fmt_date(c['invoice_date'])}")
        if c["due_date"]:
            para(f"Due Date: {fmt_date(c['due_date'])}")
        para("Bill To:", True)
        para(c["customer_name"])
        if c["customer_address"]:
            para(c["customer_address"], size=10)
        table = d.add_table(rows=1, cols=5)
        table.style = "Light Grid Accent 1"
        for cell, label in zip(table.rows[0].cells, ["#", "Description", "Qty", "Unit price", "Amount"]):
            cell.text = label
        for i, it in enumerate(c["items"], 1):
            row = table.add_row().cells
            row[0].text, row[1].text = str(i), it["description"]
            row[2].text, row[3].text, row[4].text = f"{it['qty'].normalize():f}", _money(it["unit_price"]), _money(it["amount"])
        para("")
        para(f"Subtotal: {_money(c['subtotal'])}")
        para(f"GST ({c['gst_rate']}%): {_money(c['tax'])}")
        para(f"Total Amount: {_money(c['total'])}", True, 13)
        para(c["words"], size=10)
        if c["notes"]:
            para(f"Notes: {c['notes']}", size=10)
    else:
        c = cleaned
        center = WD_ALIGN_PARAGRAPH.CENTER
        para("")
        para(f"CERTIFICATE OF {c['title_type'].upper()}", True, 24, center)
        para("This is to certify that", size=14, align=center)
        para(c["recipient_name"], True, 22, center)
        para("has successfully completed" if c["title_type"] == "Completion" else "in recognition of", size=14, align=center)
        para(c["course"], True, 16, center)
        para("")
        para(f"Issued by: {c['organization']}", align=center)
        para(f"Date of Issue: {fmt_date(c['issue_date'])}", align=center)
        para(f"Certificate ID: {c['certificate_id']}", align=center)
        if c["signatory"]:
            para("")
            para(c["signatory"], True, 12, center)
    out = io.BytesIO()
    d.save(out)
    return out.getvalue()


def filename(template, cleaned, ext):
    base = cleaned["invoice_number"] if template == "invoice" else cleaned["certificate_id"]
    return re.sub(r"[^A-Za-z0-9_\-]+", "_", f"{template}_{base}") + f".{ext}"


# ---------------------------------------------------------------- autofill
PREFILL = {
    "invoice": {"seller_name": ["vendor_name"], "customer_name": ["customer_name"], "seller_gstin": ["gstin"]},
    "certificate": {"recipient_name": ["recipient_name", "full_name"], "course": ["course_or_achievement"],
                    "organization": ["issuing_organization", "organization"]},
}
PREFILL_SOURCES = {"invoice": ["INVOICE"], "certificate": ["CERTIFICATE", "RESUME", "ID_CARD"]}


def prefill_from(template, structured):
    out = {}
    for field, keys in PREFILL.get(template, {}).items():
        for k in keys:
            v = (structured or {}).get(k)
            if v:
                out[field] = v if isinstance(v, str) else ", ".join(v)
                break
    return out
