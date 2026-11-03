"""V5 tests: multi-language OCR, handwriting recognition, document generator."""
import io
import json
import shutil
import tempfile
import zipfile
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest import mock

import pymupdf
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import Client, TestCase, override_settings

from advanced.models import DocumentOCRProfile, GeneratedDocument
from advanced.services import generator as G
from advanced.services import handwriting, options
from core import constants as C
from core.models import AuditLog, Document, Notification, ReviewTask

User = get_user_model()
SAMPLES = Path(tempfile.mkdtemp(prefix="td_v5_samples_"))
MEDIA = tempfile.mkdtemp(prefix="td_v5_media_")
HINDI_OK = None


def setUpModule():
    global HINDI_OK
    call_command("make_sample_docs", out=str(SAMPLES), verbosity=0)
    call_command("make_v5_samples", out=str(SAMPLES), verbosity=0)
    HINDI_OK = options.language_ready("hin")


def tearDownModule():
    shutil.rmtree(SAMPLES, ignore_errors=True)
    shutil.rmtree(MEDIA, ignore_errors=True)


def sample(name, extra=b""):
    return SimpleUploadedFile(name, (SAMPLES / name).read_bytes() + extra)


INVOICE_FORM = {
    "template": "invoice", "seller_name": "Acme Traders Pvt Ltd", "seller_address": "12 Market Road, Indore",
    "seller_gstin": "23ABCDE1234F1Z5", "invoice_number": "INV-2026-0501", "invoice_date": "2026-10-14",
    "due_date": "2026-10-28", "customer_name": "Sharma Electronics", "customer_address": "45 Palasia Square, Indore",
    "gst_rate": "18", "notes": "Thank you", "item_desc": ["USB Cable", "Wireless Mouse"],
    "item_qty": ["10", "4"], "item_price": ["150", "500.50"],
}
CERT_FORM = {
    "template": "certificate", "title_type": "Completion", "recipient_name": "Priya Sharma",
    "course": "Python Programming Bootcamp", "organization": "Indore Tech Academy", "issue_date": "2026-09-12",
    "certificate_id": "CERT-2026-0999", "signatory": "Dr. A. Mehta",
}


@override_settings(MEDIA_ROOT=MEDIA)
class V5Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_initial_data", verbosity=0)

        def mk(name, role):
            u = User.objects.create_user(name, f"{name}@example.com", "Pass#12345")
            u.profile.role = role
            u.profile.save()
            return u

        cls.user, cls.other = mk("alice", C.Role.USER), mk("bob", C.Role.USER)
        cls.rev, cls.admin = mk("rev1", C.Role.REVIEWER), mk("boss", C.Role.ADMIN)

    def client_for(self, user):
        c = Client()
        c.force_login(user)
        return c

    def upload_process(self, user, name, extra=b"", **fields):
        c = self.client_for(user)
        r = c.post("/api/documents/", {"file": sample(name, extra), **fields})
        self.assertEqual(r.status_code, 201, r.content)
        doc_id = r.json()["id"]
        self.assertEqual(c.post(f"/api/documents/{doc_id}/process/").status_code, 200)
        return c, Document.objects.get(pk=doc_id)


class OptionsTests(V5Base):
    def test_upload_options_are_saved(self):
        c = self.client_for(self.user)
        r = c.post("/api/documents/", {"file": sample("invoice_native.pdf"), "ocr_language": "eng+hin", "handwriting": "1"})
        self.assertEqual(r.status_code, 201)
        p = DocumentOCRProfile.objects.get(document_id=r.json()["id"])
        self.assertEqual((p.language, p.handwriting), ("eng+hin", True))
        r = c.post("/api/documents/", {"file": sample("resume_native.pdf"), "ocr_language": "xx-nonsense"})
        p = DocumentOCRProfile.objects.get(document_id=r.json()["id"])
        self.assertEqual((p.language, p.handwriting), ("eng", False))

    def test_html_form_upload_saves_options(self):
        c = self.client_for(self.user)
        r = c.post("/upload/", {"file": sample("invoice_native.pdf"), "ocr_language": "hin"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(DocumentOCRProfile.objects.get().language, "hin")

    def test_upload_page_shows_language_and_handwriting_controls(self):
        page = self.client_for(self.user).get("/upload/")
        for text in ("Document language", "English + Hindi", "Handwritten document"):
            self.assertContains(page, text)
        self.assertContains(self.client_for(self.user).get("/dashboard/"), ">Generate<")
        self.assertNotContains(self.client_for(self.rev).get("/dashboard/"), ">Generate<")

    def test_missing_language_gives_friendly_failure_and_alert(self):
        with mock.patch("advanced.services.options.installed_languages", return_value={"eng"}):
            _c, doc = self.upload_process(self.user, "invoice_scanned.png", ocr_language="hin")
        self.assertEqual(doc.status, "FAILED")
        self.assertIn("Hindi is not installed", doc.status_detail.replace("recognition for ", ""))
        self.assertTrue(Notification.objects.filter(code="LANGUAGE_UNAVAILABLE", severity="CRITICAL").exists())

    def test_native_documents_ignore_ocr_language(self):
        with mock.patch("advanced.services.options.installed_languages", return_value={"eng"}):
            _c, doc = self.upload_process(self.user, "invoice_native.pdf", ocr_language="hin")
        self.assertEqual(doc.status, "VERIFIED")


class HindiOcrTests(V5Base):
    def setUp(self):
        if not HINDI_OK:
            self.skipTest("Hindi tessdata not available in this environment")

    def test_hindi_certificate_is_read_classified_and_extracted(self):
        c, doc = self.upload_process(self.user, "hindi_certificate.png", ocr_language="eng+hin")
        v = doc.current_version
        self.assertEqual(doc.status, "VERIFIED", (doc.status, v.structured_data, v.overall_confidence))
        self.assertEqual(doc.doc_type.code, "CERTIFICATE")
        self.assertEqual(v.structured_data["recipient_name"], "प्रिया शर्मा")
        self.assertEqual(v.structured_data["issue_date"], "2026-09-12")
        self.assertEqual(v.structured_data["certificate_id"], "CERT-2026-0999".replace("0999", "0457"))
        self.assertEqual(v.language, "hi")
        page = c.get(f"/documents/{doc.pk}/")
        self.assertContains(page, "English + Hindi")
        self.assertContains(page, "प्रिया शर्मा")

    def test_hindi_notice_is_read_but_needs_review(self):
        _c, doc = self.upload_process(self.user, "hindi_notice.png", ocr_language="hin")
        self.assertEqual(doc.status, "PENDING_VERIFICATION")
        self.assertIsNone(doc.doc_type)
        self.assertIn("सोसाइटी", doc.current_version.extracted_text)
        self.assertEqual(doc.current_version.language, "hi")

    def test_english_scan_read_as_hindi_goes_to_review_not_crash(self):
        _c, doc = self.upload_process(self.user, "invoice_scanned.png", ocr_language="hin")
        self.assertIn(doc.status, ("PENDING_VERIFICATION", "VERIFIED", "FAILED"))
        self.assertNotEqual(doc.status, "FAILED")


class HandwritingTests(V5Base):
    def test_handwritten_documents_are_always_reviewed(self):
        c, doc = self.upload_process(self.user, "handwritten_receipt.png", handwriting="1")
        v = doc.current_version
        self.assertEqual(doc.status, "PENDING_VERIFICATION")
        self.assertIn("Rahul Verma", v.extracted_text)
        task = ReviewTask.objects.get(version=v)
        self.assertIn("HANDWRITTEN", task.reasons)
        page = c.get(f"/documents/{doc.pk}/")
        self.assertContains(page, "Handwritten document: please check every recognised field")
        self.assertContains(page, "Handwritten mode")

    def test_reviewer_sees_handwriting_warning_and_can_finish_review(self):
        _c, doc = self.upload_process(self.user, "handwritten_receipt.png", handwriting="1")
        task = ReviewTask.objects.get(version=doc.current_version)
        rev = self.client_for(self.rev)
        self.assertContains(rev.get(f"/review/{task.pk}/"), "Handwritten document")
        r = rev.post(f"/review/{task.pk}/", {"action": "reject", "comment": "Not one of our document types"})
        self.assertEqual(r.status_code, 302)

    def test_same_picture_without_the_toggle_has_no_handwritten_reason(self):
        _c, doc = self.upload_process(self.user, "handwritten_receipt.png")
        task = ReviewTask.objects.get(version=doc.current_version)
        self.assertNotIn("HANDWRITTEN", task.reasons)

    def test_falls_back_to_tesseract_when_engine_missing(self):
        with mock.patch.object(handwriting, "get_engine", return_value=None):
            _c, doc = self.upload_process(self.user, "handwritten_receipt.png", extra=b"\0", handwriting="1")
        self.assertEqual(doc.status, "PENDING_VERIFICATION")
        self.assertIn("HANDWRITTEN", ReviewTask.objects.get(version=doc.current_version).reasons)
        self.assertIn("Receipt", doc.current_version.extracted_text)

    def test_engine_crash_falls_back_instead_of_failing(self):
        with mock.patch.object(handwriting, "recognize", side_effect=RuntimeError("onnx exploded")):
            _c, doc = self.upload_process(self.user, "handwritten_receipt.png", extra=b"\0\0", handwriting="1")
        self.assertEqual(doc.status, "PENDING_VERIFICATION")

    def test_line_grouping(self):
        items = [(100, 10, 200, 40, "world", 90.0), (10, 12, 90, 42, "hello", 80.0), (10, 100, 120, 130, "next line", 70.0)]
        lines = handwriting._group_lines(items)
        self.assertEqual([l["text"] for l in lines], ["hello world", "next line"])
        self.assertEqual(lines[0]["conf"], 85.0)
        self.assertEqual(handwriting._group_lines([]), [])


class GeneratorUnitTests(TestCase):
    def test_amount_in_words(self):
        self.assertEqual(G.amount_in_words(Decimal("4130")), "Rupees Four Thousand One Hundred Thirty Only")
        self.assertEqual(G.amount_in_words(Decimal("105.50")), "Rupees One Hundred Five and Fifty Paise Only")
        self.assertEqual(G.amount_in_words(Decimal("1234567")),
                         "Rupees Twelve Lakh Thirty Four Thousand Five Hundred Sixty Seven Only")
        self.assertEqual(G.amount_in_words(Decimal("20000000")), "Rupees Two Crore Only")
        self.assertEqual(G.amount_in_words(Decimal("0")), "Rupees Zero Only")

    def test_invoice_totals(self):
        form = dict(INVOICE_FORM)
        c = G.clean("invoice", {k: v for k, v in form.items()})
        self.assertEqual(c["subtotal"], Decimal("3502.00"))  # 10*150 + 4*500.50
        self.assertEqual(c["tax"], Decimal("630.36"))
        self.assertEqual(c["total"], Decimal("4132.36"))
        self.assertEqual(c["invoice_date"], date(2026, 10, 14))

    def test_invoice_validation(self):
        bad = dict(INVOICE_FORM, seller_name="", invoice_number="ABC", seller_gstin="123", gst_rate="500",
                   due_date="2026-01-01", item_desc=[""], item_qty=["1"], item_price=[""])
        with self.assertRaises(G.GenError) as cm:
            G.clean("invoice", bad)
        e = cm.exception.errors
        for key in ("seller_name", "invoice_number", "seller_gstin", "gst_rate", "due_date", "items"):
            self.assertIn(key, e)
        with self.assertRaises(G.GenError):
            G.clean("invoice", dict(INVOICE_FORM, item_qty=["x", "1"]))
        with self.assertRaises(G.GenError):
            G.clean("nope", {})

    def test_certificate_validation(self):
        with self.assertRaises(G.GenError) as cm:
            G.clean("certificate", dict(CERT_FORM, recipient_name="", certificate_id="NODIGITS", issue_date="31/02/2026"))
        self.assertEqual({"recipient_name", "certificate_id", "issue_date"}, set(cm.exception.errors))
        self.assertEqual(G.clean("certificate", dict(CERT_FORM, title_type="Weird"))["title_type"], "Completion")

    def test_prefill_mapping(self):
        self.assertEqual(G.prefill_from("invoice", {"vendor_name": "Acme", "customer_name": "Sharma", "gstin": "X"}),
                         {"seller_name": "Acme", "customer_name": "Sharma", "seller_gstin": "X"})
        self.assertEqual(G.prefill_from("certificate", {"full_name": "Rahul Verma"})["recipient_name"], "Rahul Verma")


class GeneratorFlowTests(V5Base):
    def post(self, client, url, data):
        return client.post(url, data)

    def test_page_templates_and_forms_render(self):
        c = self.client_for(self.user)
        self.assertContains(c.get("/generator/"), "Invoice")
        self.assertContains(c.get("/generator/?template=invoice"), 'name="item_desc"')
        self.assertContains(c.get("/generator/?template=certificate"), 'name="recipient_name"')
        self.assertContains(c.get("/generator/?template=bogus"), "Recently generated")

    def test_live_preview(self):
        c = self.client_for(self.user)
        d = c.post("/generator/preview/", INVOICE_FORM).json()
        self.assertTrue(d["ok"])
        self.assertIn("Sharma Electronics", d["html"])
        self.assertEqual(d["totals"]["total"], "4,132.36")
        self.assertIn("Four Thousand One Hundred Thirty Two", d["totals"]["words"])
        bad = c.post("/generator/preview/", dict(INVOICE_FORM, customer_name="")).json()
        self.assertFalse(bad["ok"])
        self.assertIn("customer_name", bad["errors"])
        evil = c.post("/generator/preview/", dict(INVOICE_FORM, customer_name="<script>alert(1)</script>")).json()
        self.assertNotIn("<script>", evil["html"])  # always escaped

    def test_exports(self):
        c = self.client_for(self.user)
        r = c.post("/generator/export/pdf/", INVOICE_FORM)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.content.startswith(b"%PDF"))
        self.assertIn("INV-2026-0501.pdf", r["Content-Disposition"])
        doc = pymupdf.open(stream=r.content, filetype="pdf")
        text = doc[0].get_text()
        self.assertIn("Total Amount: 4,132.36", text)
        self.assertIn("Invoice No: INV-2026-0501", text)
        r = c.post("/generator/export/docx/", CERT_FORM)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(zipfile.is_zipfile(io.BytesIO(r.content)))
        self.assertEqual(c.post("/generator/export/exe/", CERT_FORM).status_code, 404)
        r = c.post("/generator/export/pdf/", dict(INVOICE_FORM, seller_name=""), follow=True)
        self.assertContains(r, "Please fix the highlighted fields")
        self.assertEqual(GeneratedDocument.objects.filter(owner=self.user).count(), 2)

    def test_generated_invoice_is_verified_automatically_when_saved(self):
        c = self.client_for(self.user)
        r = c.post("/generator/save/", INVOICE_FORM)
        self.assertEqual(r.status_code, 302)
        self.assertIn("/status/", r["Location"])
        doc = Document.objects.get(owner=self.user)
        self.assertEqual(c.post(f"/api/documents/{doc.pk}/process/").json()["status"], "VERIFIED")
        doc.refresh_from_db()
        v = doc.current_version
        self.assertEqual(doc.doc_type.code, "INVOICE")
        self.assertEqual(v.structured_data["invoice_number"], "INV-2026-0501")
        self.assertEqual(v.structured_data["total_amount"], "4132.36")
        self.assertEqual(v.structured_data["customer_name"], "Sharma Electronics")
        self.assertEqual(v.structured_data["invoice_date"], "2026-10-14")
        self.assertEqual(v.structured_data["gstin"], "23ABCDE1234F1Z5")
        self.assertEqual(GeneratedDocument.objects.get().saved_document, doc)
        again = c.post("/generator/save/", INVOICE_FORM, follow=True)
        self.assertContains(again, "identical document is already")
        self.assertEqual(Document.objects.filter(owner=self.user).count(), 1)

    def test_generated_certificate_round_trip(self):
        c = self.client_for(self.user)
        c.post("/generator/save/", CERT_FORM)
        doc = Document.objects.get(owner=self.user)
        c.post(f"/api/documents/{doc.pk}/process/")
        doc.refresh_from_db()
        v = doc.current_version
        self.assertEqual(doc.status, "VERIFIED", (doc.status, v.structured_data))
        self.assertEqual(doc.doc_type.code, "CERTIFICATE")
        self.assertEqual(v.structured_data["recipient_name"], "Priya Sharma")
        self.assertEqual(v.structured_data["issue_date"], "2026-09-12")
        self.assertEqual(v.structured_data["certificate_id"], "CERT-2026-0999")

    def test_prefill_history_and_permissions(self):
        c, doc = self.upload_process(self.user, "invoice_native.pdf")
        d = c.get(f"/generator/prefill/{doc.pk}/?template=invoice").json()["fields"]
        self.assertEqual(d["seller_name"], "Acme Traders Pvt Ltd")
        self.assertEqual(d["customer_name"], "Sharma Electronics")
        page = c.get("/generator/?template=invoice")
        self.assertContains(page, "invoice_native.pdf (Invoice)")
        self.assertEqual(self.client_for(self.other).get(f"/generator/prefill/{doc.pk}/?template=invoice").status_code, 404)
        self.assertEqual(c.get(f"/generator/prefill/{doc.pk}/?template=zzz").status_code, 400)
        c.post("/generator/export/pdf/", INVOICE_FORM)
        gid = GeneratedDocument.objects.get().pk
        self.assertContains(c.get(f"/generator/?template=invoice&from={gid}"), "INV-2026-0501")
        self.assertContains(c.get("/generator/"), "Use again")

    def test_access_rules(self):
        self.assertEqual(Client().get("/generator/").status_code, 302)
        self.assertEqual(self.client_for(self.rev).get("/generator/").status_code, 403)
        self.assertEqual(self.client_for(self.admin).get("/generator/").status_code, 200)
        self.assertEqual(Client().post("/generator/preview/", INVOICE_FORM).status_code, 302)
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.user)
        r = csrf.post("/generator/preview/", INVOICE_FORM)
        self.assertEqual(r.status_code, 302)  # V1 behaviour: an expired/invalid form goes back to login
        self.assertIn("/accounts/login/", r["Location"])
        r = csrf.post("/generator/preview/", INVOICE_FORM, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["code"], "AUTH_REQUIRED")


class HealthTests(V5Base):
    def test_health_page_lists_v2_and_v5_checks_without_failing_critical(self):
        page = self.client_for(self.admin).get("/manage/health/")
        for text in ("V5 Hindi OCR", "V5 handwriting (RapidOCR)", "V5 document generator (PDF)",
                     "V2 entities (spaCy)", "V2 semantic search", "V2 translation (Argos)"):
            self.assertContains(page, text)
        self.assertContains(page, "All checks passed")  # optional pieces are warnings, not failures
