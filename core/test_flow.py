"""End-to-end tests: real files through the real pipeline (needs Tesseract)."""
import csv
import io
import json
import shutil
import tempfile
import xml.etree.ElementTree as ET
from datetime import timedelta
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.db import OperationalError
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from . import constants as C
from .models import AuditLog, Document, Notification, ReviewTask, SystemSetting
from .services import pipeline, reviews
from .services.uploads import DuplicateUpload

User = get_user_model()
SAMPLES = Path(tempfile.mkdtemp(prefix="td_samples_"))
MEDIA = tempfile.mkdtemp(prefix="td_media_")


def setUpModule():
    call_command("make_sample_docs", out=str(SAMPLES), verbosity=0)


def tearDownModule():
    shutil.rmtree(SAMPLES, ignore_errors=True)
    shutil.rmtree(MEDIA, ignore_errors=True)


def sample(name):
    return SimpleUploadedFile(name, (SAMPLES / name).read_bytes())


@override_settings(MEDIA_ROOT=MEDIA)
class FlowBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_initial_data", verbosity=0)

        def mk(name, role):
            u = User.objects.create_user(name, f"{name}@example.com", "Pass#12345")
            u.profile.role = role
            u.profile.save()
            return u

        cls.user = mk("alice", C.Role.USER)
        cls.other = mk("bob", C.Role.USER)
        cls.rev1 = mk("rev1", C.Role.REVIEWER)
        cls.rev2 = mk("rev2", C.Role.REVIEWER)
        cls.admin = mk("boss", C.Role.ADMIN)

    def client_for(self, user, **kw):
        c = Client(**kw)
        c.force_login(user)
        return c

    def upload(self, user, name, **extra):
        c = self.client_for(user)
        resp = c.post("/api/documents/", {"file": sample(name), **extra})
        return c, resp

    def upload_and_process(self, user, name):
        c, resp = self.upload(user, name)
        self.assertEqual(resp.status_code, 201, resp.content)
        doc_id = resp.json()["id"]
        r = c.post(f"/api/documents/{doc_id}/process/")
        self.assertEqual(r.status_code, 200, r.content)
        return c, Document.objects.get(pk=doc_id)


class AuthAndAccessTests(FlowBase):
    def test_anonymous_redirected_or_401(self):
        c = Client()
        for url in ("/dashboard/", "/upload/", "/documents/", "/review/", "/manage/users/"):
            r = c.get(url)
            self.assertEqual(r.status_code, 302, url)
            self.assertIn("/accounts/login/?next=", r["Location"])
        r = c.get("/api/documents/")
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.json()["code"], "AUTH_REQUIRED")
        r = c.post("/api/documents/", {"file": sample("invoice_native.pdf")})
        self.assertEqual(r.status_code, 401)

    def test_register_login_logout(self):
        c = Client()
        r = c.post("/accounts/register/", {"username": "newbie", "email": "n@example.com",
                                            "password1": "Str0ng#Pass99", "password2": "Str0ng#Pass99"})
        self.assertEqual(r.status_code, 302)
        u = User.objects.get(username="newbie")
        self.assertEqual(u.profile.role, C.Role.USER)
        self.assertEqual(c.get("/dashboard/").status_code, 200)
        self.assertEqual(c.post("/accounts/logout/").status_code, 302)
        self.assertEqual(c.get("/dashboard/").status_code, 302)
        self.assertTrue(AuditLog.objects.filter(action="REGISTER", username="newbie").exists())
        self.assertTrue(AuditLog.objects.filter(action="LOGOUT", username="newbie").exists())
        # duplicate email rejected
        r = Client().post("/accounts/register/", {"username": "n2", "email": "N@example.com",
                                                   "password1": "Str0ng#Pass99", "password2": "Str0ng#Pass99"})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(User.objects.filter(username="n2").exists())

    def test_login_failure_is_audited(self):
        r = Client().post("/accounts/login/", {"username": "alice", "password": "wrong"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(AuditLog.objects.filter(action="LOGIN_FAILED").exists())
        r = Client().post("/accounts/login/", {"username": "alice", "password": "Pass#12345"})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(AuditLog.objects.filter(action="LOGIN", username="alice").exists())

    def test_role_dashboards_render(self):
        for u in (self.user, self.rev1, self.admin):
            self.assertEqual(self.client_for(u).get("/dashboard/").status_code, 200)

    def test_role_restrictions(self):
        user_c, rev_c, admin_c = self.client_for(self.user), self.client_for(self.rev1), self.client_for(self.admin)
        self.assertEqual(user_c.get("/review/").status_code, 403)
        self.assertEqual(user_c.get("/manage/users/").status_code, 403)
        self.assertEqual(user_c.get("/api/admin/stats/").status_code, 403)
        self.assertEqual(rev_c.get("/manage/users/").status_code, 403)
        self.assertEqual(rev_c.get("/upload/").status_code, 403)
        r = rev_c.post("/api/documents/", {"file": sample("invoice_native.pdf")})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(admin_c.get("/manage/users/").status_code, 200)
        self.assertEqual(admin_c.get("/review/").status_code, 200)

    def test_csrf_enforced_on_api(self):
        c = self.client_for(self.user, enforce_csrf_checks=True)
        r = c.post("/api/documents/", {"file": sample("invoice_native.pdf")})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["code"], "CSRF_FAILED")

    def test_users_cannot_see_each_others_documents(self):
        c, doc = self.upload_and_process(self.user, "invoice_native.pdf")
        other = self.client_for(self.other)
        self.assertEqual(other.get(f"/documents/{doc.pk}/").status_code, 404)
        self.assertEqual(other.get(f"/api/documents/{doc.pk}/").status_code, 404)
        self.assertEqual(other.post(f"/api/documents/{doc.pk}/process/").status_code, 404)
        self.assertEqual(other.get(f"/documents/{doc.pk}/download/json/").status_code, 404)
        self.assertEqual(other.get(f"/documents/{doc.pk}/file/").status_code, 404)
        self.assertEqual(self.client_for(self.admin).get(f"/documents/{doc.pk}/").status_code, 200)


class UploadAndPipelineTests(FlowBase):
    def test_native_pdf_auto_verified_and_downloads(self):
        c, doc = self.upload_and_process(self.user, "invoice_native.pdf")
        self.assertEqual(doc.status, "VERIFIED")
        self.assertEqual(doc.verification_method, "AUTO")
        self.assertEqual(doc.doc_type.code, "INVOICE")
        v = doc.current_version
        self.assertTrue(v.is_machine_readable)
        self.assertEqual(v.detection_method, "NATIVE")
        self.assertEqual(v.structured_data["invoice_number"], "INV-2026-0142")
        self.assertEqual(v.structured_data["invoice_date"], "2026-10-14")
        self.assertEqual(v.structured_data["total_amount"], "4130.00")
        self.assertEqual(c.get(f"/documents/{doc.pk}/").status_code, 200)
        self.assertContains(c.get(f"/documents/{doc.pk}/"), "INV-2026-0142")
        data = c.get(f"/documents/{doc.pk}/download/json/").json()
        self.assertTrue(data["document"]["verified"])
        self.assertEqual(data["fields"]["gstin"], "23ABCDE1234F1Z5")
        root = ET.fromstring(c.get(f"/documents/{doc.pk}/download/xml/").content)
        self.assertEqual(root.find("fields/invoice_number").text, "INV-2026-0142")
        rows = list(csv.DictReader(io.StringIO(c.get(f"/documents/{doc.pk}/download/csv/").content.decode())))
        self.assertEqual(rows[0]["total_amount"], "4130.00")
        self.assertEqual(c.get(f"/documents/{doc.pk}/download/pdf/").status_code, 404)
        self.assertEqual(c.get(f"/documents/{doc.pk}/file/").status_code, 200)
        api = c.get(f"/api/documents/{doc.pk}/output/?format=xml")
        self.assertEqual(api.status_code, 200)
        self.assertIn("application/xml", api["Content-Type"])
        self.assertEqual(c.get(f"/api/documents/{doc.pk}/output/?format=bad").status_code, 400)
        self.assertTrue(AuditLog.objects.filter(action="DOWNLOAD", target_id=doc.pk).exists())

    def test_scanned_files_use_ocr(self):
        for name, dtype in [("invoice_scanned.png", "INVOICE"), ("invoice_scanned.pdf", "INVOICE"),
                            ("invoice_scanned_skewed.png", "INVOICE"), ("certificate_scanned.pdf", "CERTIFICATE"),
                            ("id_card_scanned.png", "ID_CARD")]:
            with self.subTest(name):
                _c, doc = self.upload_and_process(self.user, name)
                v = doc.current_version
                self.assertEqual(doc.status, "VERIFIED", (doc.status, v.structured_data))
                self.assertEqual(doc.doc_type.code, dtype)
                self.assertEqual(v.detection_method, "OCR")
                self.assertFalse(v.is_machine_readable)
                self.assertGreater(v.overall_confidence, 85)
        self.assertEqual(Document.objects.filter(doc_type__code="ID_CARD").first()
                         .current_version.structured_data["id_number"], "23BT0412")

    def test_docx_and_resume_fields(self):
        _c, doc = self.upload_and_process(self.user, "resume_native.docx")
        self.assertEqual(doc.status, "VERIFIED")
        d = doc.current_version.structured_data
        self.assertEqual(d["email"], "rahul.verma@example.com")
        self.assertIn("Django", d["skills"])
        _c, doc2 = self.upload_and_process(self.user, "resume_native.pdf")
        self.assertEqual(doc2.current_version.structured_data["full_name"], "RAHUL VERMA")

    def test_status_endpoint_and_pages(self):
        c, resp = self.upload(self.user, "invoice_scanned.png")
        doc_id = resp.json()["id"]
        st = c.get(f"/api/documents/{doc_id}/status/").json()
        self.assertFalse(st["is_terminal"])
        self.assertEqual(st["status"], "UPLOADED")
        self.assertEqual(c.get(f"/documents/{doc_id}/status/").status_code, 200)
        self.assertEqual(c.get(f"/documents/{doc_id}/").status_code, 302)  # still processing -> status page
        c.post(f"/api/documents/{doc_id}/process/")
        st = c.get(f"/api/documents/{doc_id}/status/").json()
        self.assertTrue(st["is_terminal"])
        self.assertTrue(any(s["key"] == "OCR_PROCESSING" for s in st["steps"]))
        self.assertEqual(c.get(f"/documents/{doc_id}/status/").status_code, 302)  # finished -> detail

    def test_process_is_idempotent(self):
        c, resp = self.upload(self.user, "invoice_native.pdf")
        doc_id = resp.json()["id"]
        first = pipeline.run_pipeline(doc_id)
        self.assertEqual(first.status, "VERIFIED")
        again = pipeline.run_pipeline(doc_id)
        self.assertEqual(again.status, "VERIFIED")
        self.assertEqual(AuditLog.objects.filter(action="STATUS_CHANGE", target_id=doc_id).count(), 4)

    def test_bad_uploads_rejected_with_messages(self):
        cases = [("bad_unsupported.txt", 400, "UNSUPPORTED_TYPE"), ("bad_fake.pdf", 400, "UNSUPPORTED_TYPE"),
                 ("bad_corrupted.pdf", 400, "CORRUPTED"), ("bad_too_large.pdf", 413, "FILE_TOO_LARGE"),
                 ("bad_too_many_pages.pdf", 400, "TOO_MANY_PAGES")]
        for name, status, code in cases:
            with self.subTest(name):
                _c, r = self.upload(self.user, name)
                self.assertEqual(r.status_code, status, r.content)
                self.assertEqual(r.json()["code"], code)
                self.assertTrue(r.json()["error"])
        self.assertEqual(Document.objects.count(), 0)
        self.assertEqual(AuditLog.objects.filter(action="UPLOAD_REJECTED").count(), len(cases))
        _c, r = self.upload(self.user, "bad_too_large.pdf")
        self.assertIn("Maximum is 5 MB", r.json()["error"])

    def test_empty_file_and_missing_file(self):
        c = self.client_for(self.user)
        r = c.post("/api/documents/", {"file": SimpleUploadedFile("a.pdf", b"")})
        self.assertEqual(r.json()["code"], "EMPTY_FILE")
        r = c.post("/api/documents/", {})
        self.assertEqual(r.json()["code"], "NO_FILE")

    def test_blank_image_fails_gracefully_and_can_retry(self):
        c, doc = self.upload_and_process(self.user, "bad_blank.png")
        self.assertEqual(doc.status, "FAILED")
        self.assertIn("No readable text", doc.status_detail)
        self.assertTrue(doc.error_ref)
        self.assertTrue(AuditLog.objects.filter(action="PROCESS_FAILED", target_id=doc.pk).exists())
        page = c.get(f"/documents/{doc.pk}/")
        self.assertContains(page, "No readable text")
        self.assertContains(page, "Retry")
        r = c.post(f"/documents/{doc.pk}/retry/")
        self.assertEqual(r.status_code, 302)
        doc.refresh_from_db()
        self.assertEqual(doc.status, "DETECTING")
        r = c.post(f"/api/documents/{doc.pk}/process/")
        self.assertEqual(r.json()["status"], "FAILED")
        r = c.post(f"/documents/{doc.pk}/archive/")
        doc.refresh_from_db()
        self.assertEqual(doc.status, "ARCHIVED")

    def test_duplicate_handling(self):
        c, doc = self.upload_and_process(self.user, "invoice_native.pdf")
        r = c.post("/api/documents/", {"file": sample("invoice_native.pdf")})
        self.assertEqual(r.status_code, 409)
        body = r.json()
        self.assertEqual(body["code"], "DUPLICATE")
        self.assertEqual(body["existing"]["id"], doc.pk)
        self.assertFalse(body["can_force"])
        self.assertEqual(Document.objects.count(), 1)
        # a different user may upload the same file
        _c2, r2 = self.upload(self.other, "invoice_native.pdf")
        self.assertEqual(r2.status_code, 201)
        # rejected originals can be re-uploaded as a new copy
        Document.objects.filter(pk=doc.pk).update(status="REJECTED")
        r = c.post("/api/documents/", {"file": sample("invoice_native.pdf")})
        self.assertEqual(r.status_code, 409)
        self.assertTrue(r.json()["can_force"])
        r = c.post("/api/documents/", {"file": sample("invoice_native.pdf"), "force": "1"})
        self.assertEqual(r.status_code, 201)
        # archived originals never block
        Document.objects.filter(pk=doc.pk).update(status="ARCHIVED")
        Document.objects.filter(owner=self.user).exclude(pk=doc.pk).update(status="ARCHIVED")
        r = c.post("/api/documents/", {"file": sample("invoice_native.pdf")})
        self.assertEqual(r.status_code, 201)

    def test_html_form_upload_fallback(self):
        c = self.client_for(self.user)
        self.assertContains(c.get("/upload/"), "Drag and drop")
        r = c.post("/upload/", {"file": sample("invoice_native.pdf")})
        self.assertEqual(r.status_code, 302)
        self.assertIn("/status/", r["Location"])
        r = c.post("/upload/", {"file": sample("bad_unsupported.txt")})
        self.assertContains(r, "Unsupported file type")
        r = c.post("/upload/", {"file": sample("invoice_native.pdf")})
        self.assertContains(r, "already uploaded")

    def test_page_preview_images(self):
        c, doc = self.upload_and_process(self.user, "invoice_native.pdf")
        r = c.get(f"/documents/{doc.pk}/page/1.png")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.content.startswith(b"\x89PNG"))
        self.assertEqual(c.get(f"/documents/{doc.pk}/page/2.png").status_code, 404)
        _c, img = self.upload_and_process(self.user, "invoice_scanned.png")
        self.assertEqual(c.get(f"/documents/{img.pk}/page/1.png").status_code, 200)
        _c, dx = self.upload_and_process(self.user, "resume_native.docx")
        self.assertEqual(c.get(f"/documents/{dx.pk}/page/1.png").status_code, 404)

    def test_threshold_setting_changes_routing(self):
        SystemSetting.set("AUTO_VERIFY_MIN_CONFIDENCE", 99)
        _c, doc = self.upload_and_process(self.user, "invoice_scanned.png")
        self.assertEqual(doc.status, "PENDING_VERIFICATION")
        task = ReviewTask.objects.get(version=doc.current_version)
        self.assertEqual(task.reasons, ["LOW_CONFIDENCE"])

    def test_tesseract_missing_is_handled(self):
        with mock.patch("pytesseract.image_to_data", side_effect=__import__("pytesseract").TesseractNotFoundError()):
            _c, doc = self.upload_and_process(self.user, "invoice_scanned.png")
        self.assertEqual(doc.status, "FAILED")
        self.assertIn("unavailable", doc.status_detail)
        self.assertTrue(Notification.objects.filter(code="OCR_UNAVAILABLE", severity="CRITICAL").exists())
        # native files still work while OCR is down
        with mock.patch("pytesseract.image_to_data", side_effect=__import__("pytesseract").TesseractNotFoundError()):
            _c, ok = self.upload_and_process(self.user, "invoice_native.pdf")
        self.assertEqual(ok.status, "VERIFIED")

    def test_unexpected_error_becomes_failed_with_reference(self):
        c, resp = self.upload(self.user, "invoice_native.pdf")
        with mock.patch("core.services.pipeline.classify_mod.classify", side_effect=RuntimeError("boom")):
            doc = pipeline.run_pipeline(resp.json()["id"])
        self.assertEqual(doc.status, "FAILED")
        self.assertRegex(doc.status_detail, r"ref #[0-9A-F]{6}")
        self.assertNotIn("boom", doc.status_detail)

    def test_repeated_errors_alert_admin(self):
        for name in ("invoice_native.pdf", "resume_native.pdf", "certificate_native.pdf"):
            _c, resp = self.upload(self.user, name)
            with mock.patch("core.services.pipeline.classify_mod.classify", side_effect=RuntimeError("boom")):
                pipeline.run_pipeline(resp.json()["id"])
        self.assertTrue(Notification.objects.filter(code="PIPELINE_ERRORS_REPEATED").exists())


class ReviewTests(FlowBase):
    def pending_missing_total(self):
        _c, doc = self.upload_and_process(self.user, "review_invoice_missing_total.pdf")
        self.assertEqual(doc.status, "PENDING_VERIFICATION")
        return doc, ReviewTask.objects.get(version=doc.current_version)

    def test_queue_claim_lock_and_approve(self):
        doc, task = self.pending_missing_total()
        self.assertEqual(task.reasons, ["MISSING:total_amount"])
        r1, r2 = self.client_for(self.rev1), self.client_for(self.rev2)
        self.assertContains(r1.get("/review/"), "review_invoice_missing_total.pdf")
        page = r1.get(f"/review/{task.pk}/")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Required field missing: Total Amount")
        # second reviewer sees it read-only while the lock is live
        page2 = r2.get(f"/review/{task.pk}/")
        self.assertContains(page2, "currently being reviewed by rev1")
        self.assertNotContains(page2, 'value="approve"')
        r = r2.post(f"/review/{task.pk}/", {"action": "approve", "f_total_amount": "4130"})
        self.assertEqual(r.status_code, 302)  # conflict -> redirected, nothing changed
        doc.refresh_from_db()
        self.assertEqual(doc.status, "PENDING_VERIFICATION")
        # approve without the required field: validation error, nothing saved
        r = r1.post(f"/review/{task.pk}/", {"action": "approve", "f_invoice_number": "INV-2026-0142",
                                             "f_invoice_date": "14/10/2026", "f_total_amount": ""})
        self.assertEqual(r.status_code, 400)
        self.assertContains(r, "This field is required", status_code=400)
        r = r1.post(f"/review/{task.pk}/", {"action": "approve", "f_invoice_number": "INV-2026-0142",
                                             "f_invoice_date": "14/10/2026", "f_total_amount": "abc"}, )
        self.assertContains(r, "Enter an amount", status_code=400)
        # valid approval with an edit
        r = r1.post(f"/review/{task.pk}/", {"action": "approve", "f_invoice_number": "INV-2026-0142",
                                             "f_invoice_date": "14/10/2026", "f_total_amount": "4,130.00",
                                             "f_vendor_name": "Acme Traders Pvt Ltd"})
        self.assertEqual(r.status_code, 302)
        doc.refresh_from_db()
        task.refresh_from_db()
        self.assertEqual(doc.status, "VERIFIED")
        self.assertEqual(doc.verification_method, "MANUAL")
        self.assertEqual(doc.verified_by, self.rev1)
        self.assertEqual(task.decision, "EDITED")
        self.assertEqual(doc.current_version.structured_data["total_amount"], "4130.00")
        self.assertEqual(doc.current_version.field_confidences["total_amount"], 100.0)
        self.assertIn("total_amount", task.changes)
        self.assertTrue(AuditLog.objects.filter(action="REVIEW_EDIT", target_id=doc.pk).exists())
        # the owner sees who verified it
        self.assertContains(self.client_for(self.user).get(f"/documents/{doc.pk}/"), "Verified by rev1")
        self.assertContains(r1.get("/review/history/"), "review_invoice_missing_total.pdf")

    def test_race_second_reviewer_gets_already_verified_message(self):
        doc, task = self.pending_missing_total()
        form = {"action": "approve", "f_invoice_number": "INV-2026-0142", "f_invoice_date": "14/10/2026",
                "f_total_amount": "4130"}
        r1, r2 = self.client_for(self.rev1), self.client_for(self.rev2)
        r1.get(f"/review/{task.pk}/")
        Document.objects.filter(pk=doc.pk).update(updated_at=timezone.now())
        ReviewTask.objects.filter(pk=task.pk).update(locked_at=timezone.now() - timedelta(minutes=30))  # lock expired
        r2.get(f"/review/{task.pk}/")  # rev2 now holds the lock, rev1 has a stale page
        self.assertEqual(r2.post(f"/review/{task.pk}/", form).status_code, 302)
        resp = r1.post(f"/review/{task.pk}/", form, follow=True)
        self.assertContains(resp, "was already verified by rev2")
        self.assertTrue(AuditLog.objects.filter(action="REVIEW_CONFLICT", username="rev1").exists())
        with self.assertRaises(reviews.ReviewConflict) as cm:
            reviews.approve(task.pk, self.rev1, {"total_amount": "1"})
        self.assertTrue(cm.exception.already_decided)
        doc.refresh_from_db()
        self.assertEqual(doc.verified_by, self.rev2)

    def test_reject_needs_reason_and_owner_sees_it(self):
        doc, task = self.pending_missing_total()
        c = self.client_for(self.rev1)
        r = c.post(f"/review/{task.pk}/", {"action": "reject", "comment": ""})
        self.assertContains(r, "give a reason", status_code=400)
        r = c.post(f"/review/{task.pk}/", {"action": "reject", "comment": "Total is cut off in the file"})
        self.assertEqual(r.status_code, 302)
        doc.refresh_from_db()
        self.assertEqual(doc.status, "REJECTED")
        self.assertContains(self.client_for(self.user).get(f"/documents/{doc.pk}/"), "Total is cut off")
        self.assertEqual(c.get(f"/review/{task.pk}/").status_code, 302)  # already decided

    def test_unknown_type_then_change_type_reextracts(self):
        _c, doc = self.upload_and_process(self.user, "review_unknown_letter.pdf")
        self.assertEqual(doc.status, "PENDING_VERIFICATION")
        self.assertIsNone(doc.doc_type)
        task = ReviewTask.objects.get(version=doc.current_version)
        self.assertEqual(task.reasons, ["UNKNOWN_TYPE"])
        c = self.client_for(self.rev1)
        c.get(f"/review/{task.pk}/")
        r = c.post(f"/review/{task.pk}/", {"action": "approve"})
        self.assertContains(r, "Choose a document type first", status_code=400)
        invoice = __import__("core.models", fromlist=["DocumentType"]).DocumentType.objects.get(code="INVOICE")
        r = c.post(f"/review/{task.pk}/", {"action": "change_type", "doc_type": invoice.pk})
        self.assertEqual(r.status_code, 302)
        doc.refresh_from_db()
        self.assertEqual(doc.doc_type, invoice)
        self.assertEqual(doc.status, "PENDING_VERIFICATION")  # stays in review
        task.refresh_from_db()
        self.assertIn("MISSING:invoice_number", task.reasons)
        self.assertNotIn("UNKNOWN_TYPE", task.reasons)

    def test_poor_scan_goes_to_review(self):
        _c, doc = self.upload_and_process(self.user, "review_invoice_poor_scan.png")
        self.assertEqual(doc.status, "PENDING_VERIFICATION")
        task = ReviewTask.objects.get(version=doc.current_version)
        self.assertIn("LOW_CONFIDENCE", task.reasons)

    def test_review_api(self):
        doc, task = self.pending_missing_total()
        api = self.client_for(self.rev1)
        r = api.post(f"/api/review/{task.pk}/approve/", data=json.dumps({"fields": {"total_amount": "x"}}),
                     content_type="application/json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("total_amount", r.json()["fields"])
        r = api.post(f"/api/review/{task.pk}/approve/", content_type="application/json", data=json.dumps(
            {"fields": {"invoice_number": "INV-2026-0142", "invoice_date": "2026-10-14", "total_amount": "4130"}}))
        self.assertEqual(r.status_code, 200)
        r = api.post(f"/api/review/{task.pk}/approve/", content_type="application/json", data=json.dumps({"fields": {}}))
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["code"], "REVIEW_CONFLICT")
        self.assertEqual(self.client_for(self.user).post(f"/api/review/{task.pk}/reject/").status_code, 403)

    def test_reviewer_can_open_documents_in_the_queue_only(self):
        doc, task = self.pending_missing_total()
        _c, other = self.upload_and_process(self.user, "invoice_native.pdf")
        rev = self.client_for(self.rev1)
        self.assertEqual(rev.get(f"/documents/{doc.pk}/").status_code, 200)
        self.assertEqual(rev.get(f"/documents/{other.pk}/").status_code, 404)


class AdminTests(FlowBase):
    def test_user_management_rules(self):
        c = self.client_for(self.admin)
        r = c.post("/manage/users/new/", {"username": "newrev", "email": "nr@example.com",
                                           "password": "Str0ng#Pass99", "role": "REVIEWER"})
        self.assertEqual(r.status_code, 302)
        new = User.objects.get(username="newrev")
        self.assertEqual(new.profile.role, "REVIEWER")
        r = c.post("/manage/users/new/", {"username": "weak", "email": "w@example.com", "password": "123",
                                           "role": "USER"})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(User.objects.filter(username="weak").exists())
        c.post(f"/manage/users/{new.pk}/role/", {"role": "ADMIN"})
        new.profile.refresh_from_db()
        self.assertEqual(new.profile.role, "ADMIN")
        c.post(f"/manage/users/{self.admin.pk}/role/", {"role": "USER"})  # cannot change own role
        self.admin.profile.refresh_from_db()
        self.assertEqual(self.admin.profile.role, "ADMIN")
        c.post(f"/manage/users/{new.pk}/toggle/")
        new.refresh_from_db()
        self.assertFalse(new.is_active)
        self.assertEqual(Client().post("/accounts/login/", {"username": "newrev", "password": "Str0ng#Pass99"}).status_code, 200)
        c.post(f"/manage/users/{new.pk}/toggle/")
        c.post(f"/manage/users/{self.admin.pk}/toggle/")  # cannot deactivate self
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)
        # last-admin protection
        new.profile.role = "USER"
        new.profile.save()
        other_admin = self.client_for(User.objects.create_user("a2", "a2@example.com", "x"))
        other_admin_user = User.objects.get(username="a2")
        other_admin_user.profile.role = "ADMIN"
        other_admin_user.profile.save()
        c2 = self.client_for(other_admin_user)
        c2.post(f"/manage/users/{self.admin.pk}/role/", {"role": "USER"})  # fine: a2 stays admin
        self.admin.profile.refresh_from_db()
        self.assertEqual(self.admin.profile.role, "USER")
        for action in ("ROLE_CHANGED", "USER_CREATED", "USER_DEACTIVATED", "USER_REACTIVATED"):
            self.assertTrue(AuditLog.objects.filter(action=action).exists(), action)

    def test_cannot_remove_last_admin(self):
        c = self.client_for(self.admin)
        other = self.client_for(self.user)
        # make 'alice' an admin, demote boss via alice, then try to demote alice via boss (no longer admin)
        self.user.profile.role = "ADMIN"
        self.user.profile.save()
        c.post(f"/manage/users/{self.user.pk}/toggle/")  # deactivate alice (boss stays the only active admin)
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_active)
        r = c.post(f"/manage/users/{self.user.pk}/role/", {"role": "USER"})  # alice inactive: allowed
        self.assertEqual(r.status_code, 302)

    def test_settings_validation_and_audit(self):
        c = self.client_for(self.admin)
        page = c.get("/manage/settings/")
        self.assertContains(page, "AUTO_VERIFY_MIN_CONFIDENCE")
        base = {f"s_{k}": v["default"] for k, v in C.SETTING_DEFINITIONS.items()}
        r = c.post("/manage/settings/", {**base, "s_AUTO_VERIFY_MIN_CONFIDENCE": "150"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(SystemSetting.get("AUTO_VERIFY_MIN_CONFIDENCE"), 85.0)
        r = c.post("/manage/settings/", {**base, "s_AUTO_VERIFY_MIN_CONFIDENCE": "90"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(SystemSetting.get("AUTO_VERIFY_MIN_CONFIDENCE"), 90.0)
        entry = AuditLog.objects.filter(action="SETTINGS_CHANGED").first()
        self.assertEqual(entry.details["changes"]["AUTO_VERIFY_MIN_CONFIDENCE"]["to"], 90.0)

    def test_audit_stats_health_pages(self):
        self.upload_and_process(self.user, "invoice_native.pdf")
        self.upload_and_process(self.user, "invoice_scanned.png")
        c = self.client_for(self.admin)
        self.assertContains(c.get("/manage/audit/?action=UPLOAD"), "Upload")
        self.assertContains(c.get(f"/manage/audit/?user=alice&document=1"), "alice")
        self.assertEqual(c.get("/manage/audit/?date_from=2020-01-01&date_to=2099-01-01").status_code, 200)
        stats = c.get("/manage/stats/")
        self.assertContains(stats, "Native text vs OCR")
        api = c.get("/api/admin/stats/").json()
        self.assertEqual(api["total_documents"], 2)
        self.assertEqual(api["by_status"]["VERIFIED"], 2)
        self.assertEqual(api["by_method"]["OCR"], 1)
        self.assertIsNotNone(api["avg_processing_seconds"])
        audit = c.get("/api/admin/audit/?action=UPLOAD").json()
        self.assertEqual(audit["count"], 2)
        health = c.get("/manage/health/")
        self.assertContains(health, "Tesseract OCR")
        self.assertContains(health, "All checks passed")

    def test_health_page_reports_missing_tesseract_and_alerts_admin(self):
        c = self.client_for(self.admin)
        with mock.patch("pytesseract.get_tesseract_version", side_effect=OSError("tesseract is not installed")):
            page = c.get("/manage/health/")
        self.assertContains(page, "Attention needed")
        self.assertContains(page, "UB-Mannheim.TesseractOCR")
        self.assertTrue(Notification.objects.filter(code="HEALTH_TESSERACT_OCR", severity="CRITICAL").exists())
        self.assertContains(c.get("/dashboard/"), "Unread alerts")
        c.post("/manage/alerts/read/")
        self.assertFalse(Notification.objects.filter(is_read=False).exists())

    def test_recover_stuck_documents(self):
        c, resp = self.upload(self.user, "invoice_native.pdf")
        doc = Document.objects.get(pk=resp.json()["id"])
        Document.objects.filter(pk=doc.pk).update(status="OCR_PROCESSING",
                                                  updated_at=timezone.now() - timedelta(minutes=10))
        doc.refresh_from_db()
        self.assertTrue(pipeline.is_stuck(doc))
        self.assertTrue(self.client_for(self.user).get(f"/api/documents/{doc.pk}/status/").json()["is_stuck"])
        admin = self.client_for(self.admin)
        admin.post("/manage/recover/")
        doc.refresh_from_db()
        self.assertEqual(doc.status, "FAILED")
        self.assertIn("interrupted", doc.status_detail)
        self.assertTrue(AuditLog.objects.filter(action="STUCK_RECOVERED").exists())
        c.post(f"/documents/{doc.pk}/retry/")
        self.assertEqual(c.post(f"/api/documents/{doc.pk}/process/").json()["status"], "VERIFIED")


class SearchAndResilienceTests(FlowBase):
    def test_search_scoped_and_highlighted(self):
        self.upload_and_process(self.user, "invoice_native.pdf")
        self.upload_and_process(self.other, "resume_native.pdf")
        c = self.client_for(self.user)
        r = c.get("/search/?q=sharma")
        self.assertContains(r, "invoice_native.pdf")
        self.assertContains(r, "<mark>")
        self.assertNotContains(r, "resume_native.pdf")
        self.assertContains(c.get("/search/?q=zzzznotfound"), "No documents matched")
        self.assertContains(c.get("/search/?type=INVOICE"), "invoice_native.pdf")
        api = c.get("/api/search/?q=INV-2026").json()
        self.assertEqual(api["count"], 1)
        self.assertIn("INV-2026", api["results"][0]["snippet"])
        self.assertEqual(self.client_for(self.admin).get("/api/search/?q=a").json()["count"], 2)
        self.assertEqual(c.get("/search/?q=%3Cscript%3E").status_code, 200)

    def test_document_list_filters_and_api_list(self):
        self.upload_and_process(self.user, "invoice_native.pdf")
        self.upload_and_process(self.user, "bad_blank.png")
        c = self.client_for(self.user)
        self.assertContains(c.get("/documents/?status=FAILED"), "bad_blank.png")
        self.assertNotContains(c.get("/documents/?status=FAILED"), "invoice_native.pdf")
        data = c.get("/api/documents/?page_size=1").json()
        self.assertEqual(data["count"], 2)
        self.assertEqual(len(data["results"]), 1)

    def test_database_outage_shows_friendly_503(self):
        c = self.client_for(self.user)
        with mock.patch("django.db.backends.base.base.BaseDatabaseWrapper.ensure_connection",
                        side_effect=OperationalError("connection refused")):
            r = c.get("/dashboard/")
            self.assertEqual(r.status_code, 503)
            self.assertContains(r, "temporarily unavailable", status_code=503)
            self.assertNotContains(r, "Traceback", status_code=503)
            j = c.get("/api/documents/")
            self.assertEqual(j.status_code, 503)
            self.assertEqual(j.json()["code"], "DB_UNAVAILABLE")
        self.assertEqual(c.get("/dashboard/").status_code, 200)  # recovered
        self.assertTrue(Notification.objects.filter(code="DB_RECOVERED").exists())

    def test_database_error_inside_view_is_503(self):
        c = self.client_for(self.user)
        with mock.patch("core.views.documents.system_stats", side_effect=OperationalError("lost connection")), \
                mock.patch("core.views.documents.visible_documents", side_effect=OperationalError("lost connection")):
            r = c.get("/documents/")
        self.assertEqual(r.status_code, 503)

    def test_session_expiry_messages(self):
        c = Client()
        c.cookies["sessionid"] = "expired-or-bogus"
        r = c.get("/documents/")
        self.assertEqual(r.status_code, 302)
        r = c.post("/api/documents/", {"file": sample("invoice_native.pdf")})
        self.assertEqual(r.status_code, 401)
        self.assertIn("expired", r.json()["error"])
        self.assertTrue(AuditLog.objects.filter(action="LOGIN_REQUIRED").exists())

    def test_404_and_invalid_transition(self):
        c = self.client_for(self.user)
        self.assertEqual(c.get("/documents/99999/").status_code, 404)
        self.assertEqual(c.get("/nope/").status_code, 404)
        _c, doc = self.upload_and_process(self.user, "invoice_native.pdf")
        with self.assertRaises(pipeline.InvalidTransition):
            pipeline.transition(doc, "UPLOADED")
        with self.assertRaises(pipeline.InvalidTransition):
            pipeline.transition(doc, "REJECTED")

    def test_preflight_command_passes(self):
        out = io.StringIO()
        call_command("preflight", stdout=out)
        text = out.getvalue()
        self.assertIn("PASS  Tesseract OCR", text)
        self.assertIn("Ready to demo", text)
        with mock.patch("pytesseract.get_tesseract_version", side_effect=OSError("missing")):
            with self.assertRaises(SystemExit):
                call_command("preflight", stdout=io.StringIO())

    def test_duplicate_upload_exception_shape(self):
        _c, doc = self.upload_and_process(self.user, "invoice_native.pdf")
        from .services.uploads import handle_upload
        with self.assertRaises(DuplicateUpload) as cm:
            handle_upload(self.user, sample("invoice_native.pdf"))
        self.assertEqual(cm.exception.existing.pk, doc.pk)
