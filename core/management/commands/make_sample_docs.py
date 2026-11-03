"""Generate synthetic demo documents (all names/numbers are fictional)."""
import os
from pathlib import Path

import cv2
import numpy as np
import pymupdf
from django.conf import settings
from django.core.management.base import BaseCommand

INVOICE = [
    ("Acme Traders Pvt Ltd", 18, True), ("12 Market Road, Indore, Madhya Pradesh", 10, False),
    ("GSTIN: 23ABCDE1234F1Z5", 10, False), ("", 8, False), ("TAX INVOICE", 15, True),
    ("Invoice No: INV-2026-0142", 11, False), ("Invoice Date: 14/10/2026", 11, False),
    ("Due Date: 28/10/2026", 11, False), ("", 8, False), ("Bill To:", 11, True),
    ("Sharma Electronics", 11, False), ("45 Palasia Square, Indore", 10, False), ("", 8, False),
    ("Item                      Qty      Unit Price      Amount", 10, True),
    ("USB Cable                  10          150.00        1500.00", 10, False),
    ("Wireless Mouse              4          500.00        2000.00", 10, False), ("", 8, False),
    ("Subtotal: 3500.00", 11, False), ("GST (18%): 630.00", 11, False),
    ("Total Amount: 4130.00", 13, True), ("", 8, False), ("Payment terms: Net 14 days", 10, False),
]
RESUME = [
    ("RAHUL VERMA", 20, True), ("Email: rahul.verma@example.com | Phone: +91 98765 43210", 10, False),
    ("LinkedIn: linkedin.com/in/rahulverma | GitHub: github.com/rahulverma", 10, False), ("", 8, False),
    ("OBJECTIVE", 12, True), ("Entry-level software engineer seeking a role in backend development.", 10, False),
    ("", 8, False), ("EDUCATION", 12, True), ("B.Tech in Computer Science and Engineering, SVVV Indore", 10, False),
    ("2023 - 2027, CGPA 8.2", 10, False), ("", 8, False), ("SKILLS", 12, True),
    ("Programming: Python, SQL, Java", 10, False), ("Frameworks: Django, Django REST Framework", 10, False),
    ("Tools: Git, Linux, MySQL", 10, False), ("", 8, False), ("PROJECTS", 12, True),
    ("Online Certificate Issuance System - Django, MySQL", 10, False),
    ("Campus Placement Portal - Django, Bootstrap", 10, False), ("", 8, False),
    ("EXPERIENCE", 12, True), ("Software Intern, Nexora Labs, Indore (Jun 2026 - Aug 2026)", 10, False),
]
CERTIFICATE = [
    ("", 20, False), ("CERTIFICATE OF COMPLETION", 22, True), ("", 12, False),
    ("This is to certify that", 13, False), ("Priya Sharma", 20, True),
    ("has successfully completed", 13, False), ("Python Programming Bootcamp", 16, True), ("", 10, False),
    ("Issued by: Indore Tech Academy", 12, False), ("Date of Issue: 12 September 2026", 12, False),
    ("Certificate ID: CERT-2026-0457", 12, False),
]
ID_CARD = [
    ("SVVV Indore", 16, True), ("STUDENT IDENTITY CARD", 12, True), ("", 6, False),
    ("Name: Ankit Jain", 11, False), ("Enrollment No: 23BT0412", 11, False),
    ("Department: Computer Science and Engineering", 11, False), ("Date of Birth: 14/03/2005", 11, False),
    ("Blood Group: B+", 11, False), ("Phone: 9876501234", 11, False), ("Valid Upto: 30/06/2027", 11, False),
    ("Address: 12 Palasia, Indore, MP", 11, False),
]


LETTER = [
    ("Dear Neighbour,", 12, False), ("", 8, False),
    ("The society garden will be closed for maintenance next week.", 11, False),
    ("Please keep pets indoors and avoid parking near the gate.", 11, False),
    ("Thank you for your cooperation.", 11, False), ("", 8, False), ("Regards, Residents Committee", 11, False),
]


def text_pdf(lines, path, size=(595, 842), top=60, left=50):
    doc = pymupdf.open()
    page = doc.new_page(width=size[0], height=size[1])
    y = top
    for text, fs, bold in lines:
        if text:
            page.insert_text((left, y), text, fontsize=fs, fontname="hebo" if bold else "helv")
        y += fs * 1.75
    doc.save(path)
    doc.close()


def render_png(pdf_path, png_path, dpi=200):
    doc = pymupdf.open(pdf_path)
    doc[0].get_pixmap(dpi=dpi).save(png_path)
    doc.close()


def image_only_pdf(png_path, pdf_path):
    """A PDF whose page is just a (JPEG-compressed) picture: no text layer."""
    gray = cv2.imread(str(png_path), cv2.IMREAD_GRAYSCALE)
    ok, buf = cv2.imencode(".jpg", gray, [cv2.IMWRITE_JPEG_QUALITY, 85])
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_image(page.rect, stream=buf.tobytes())
    doc.save(pdf_path, deflate=True, garbage=3)
    doc.close()


def skew_and_noise(src, dst, angle=2.5, sigma=10):
    img = cv2.imread(str(src), cv2.IMREAD_GRAYSCALE)
    h, w = img.shape
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    img = cv2.warpAffine(img, m, (w, h), borderValue=255)
    rng = np.random.default_rng(7)
    img = np.clip(img.astype(np.int16) + rng.normal(0, sigma, img.shape), 0, 255).astype(np.uint8)
    cv2.imwrite(str(dst), img)


class Command(BaseCommand):
    help = "Create synthetic sample documents (good and bad) for testing and the demo."

    def add_arguments(self, parser):
        parser.add_argument("--out", default=str(settings.BASE_DIR / "samples"))

    def handle(self, *args, **options):
        out = Path(options["out"])
        out.mkdir(parents=True, exist_ok=True)
        p = lambda name: str(out / name)  # noqa: E731

        text_pdf(INVOICE, p("invoice_native.pdf"))
        text_pdf(INVOICE, p("_tmp_invoice.pdf"))
        render_png(p("_tmp_invoice.pdf"), p("invoice_scanned.png"))
        image_only_pdf(p("invoice_scanned.png"), p("invoice_scanned.pdf"))
        skew_and_noise(p("invoice_scanned.png"), p("invoice_scanned_skewed.png"))
        os.remove(p("_tmp_invoice.pdf"))

        # --- samples that deliberately need a human (review-queue demo)
        text_pdf([l for l in INVOICE if not l[0].startswith(("Total Amount", "Subtotal"))],
                 p("review_invoice_missing_total.pdf"))
        text_pdf(LETTER, p("review_unknown_letter.pdf"))
        text_pdf(INVOICE, p("_tmp_poor.pdf"))
        render_png(p("_tmp_poor.pdf"), p("_tmp_poor.png"), dpi=90)
        img = cv2.imread(p("_tmp_poor.png"), cv2.IMREAD_GRAYSCALE)
        img = cv2.GaussianBlur(img, (5, 5), 1.2)
        rng = np.random.default_rng(3)
        img = np.clip(img.astype(np.int16) + rng.normal(0, 18, img.shape), 0, 255).astype(np.uint8)
        cv2.imwrite(p("review_invoice_poor_scan.png"), img)
        os.remove(p("_tmp_poor.pdf")); os.remove(p("_tmp_poor.png"))

        text_pdf(RESUME, p("resume_native.pdf"))
        text_pdf(CERTIFICATE, p("certificate_native.pdf"), top=120, left=110)
        text_pdf(CERTIFICATE, p("_tmp_cert.pdf"), top=120, left=110)
        render_png(p("_tmp_cert.pdf"), p("_tmp_cert.png"))
        image_only_pdf(p("_tmp_cert.png"), p("certificate_scanned.pdf"))
        os.remove(p("_tmp_cert.pdf")); os.remove(p("_tmp_cert.png"))

        text_pdf(ID_CARD, p("_tmp_id.pdf"), size=(430, 290), top=34, left=30)
        render_png(p("_tmp_id.pdf"), p("id_card_scanned.png"), dpi=250)
        os.remove(p("_tmp_id.pdf"))

        import docx
        d = docx.Document()
        for text, fs, bold in RESUME:
            if not text:
                continue
            if fs >= 12:
                d.add_heading(text, level=1 if fs >= 20 else 2)
            else:
                d.add_paragraph(text)
        d.save(p("resume_native.docx"))

        # bad files for the error demo
        Path(p("bad_corrupted.pdf")).write_bytes(b"%PDF-1.4\n1 0 obj\n<< /Broken true >>\nthis is not a real pdf")
        Path(p("bad_fake.pdf")).write_bytes(b"MZ\x90\x00" + os.urandom(2000))
        Path(p("bad_unsupported.txt")).write_text("plain text is not an accepted type")
        cv2.imwrite(p("bad_blank.png"), np.full((1400, 1000), 255, np.uint8))
        doc = pymupdf.open()
        for i in range(12):
            doc.new_page().insert_text((60, 80), f"Page {i + 1} of a 12 page document", fontsize=14)
        doc.save(p("bad_too_many_pages.pdf")); doc.close()
        Path(p("bad_too_large.pdf")).write_bytes(b"%PDF-1.4\n" + b"0" * (6 * 1024 * 1024))

        Path(p("README.txt")).write_text(
            "TransformoDocs sample documents (synthetic, fictional data)\n\n"
            "GOOD FILES (one per type)\n"
            "  invoice_native.pdf        text PDF -> read directly, usually auto-verified\n"
            "  invoice_scanned.png       scanned image -> OCR\n"
            "  invoice_scanned.pdf       image-only PDF -> OCR\n"
            "  invoice_scanned_skewed.png  rotated + noisy scan -> OCR with deskew\n"
            "  resume_native.pdf / resume_native.docx   text resume\n"
            "  certificate_native.pdf / certificate_scanned.pdf\n"
            "  id_card_scanned.png       ID card image -> OCR\n\n"
            "REVIEW-QUEUE DEMO FILES (need a human)\n"
            "  review_invoice_missing_total.pdf   required fields missing -> review\n"
            "  review_unknown_letter.pdf          type not recognised -> review\n"
            "  review_invoice_poor_scan.png       low-quality scan -> review\n\n"
            "ERROR DEMO FILES\n"
            "  bad_corrupted.pdf         damaged PDF\n"
            "  bad_fake.pdf              not a real PDF (wrong content)\n"
            "  bad_unsupported.txt       unsupported type\n"
            "  bad_blank.png             blank page -> no readable text\n"
            "  bad_too_many_pages.pdf    12 pages, limit is 10\n"
            "  bad_too_large.pdf         6 MB, limit is 5 MB\n")
        self.stdout.write(self.style.SUCCESS(f"Sample documents written to {out}"))
