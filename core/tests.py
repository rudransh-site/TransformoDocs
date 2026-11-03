from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from . import constants as C
from .models import (AuditLog, Document, DocumentType, DocumentVersion, Notification,
                     Profile, ReviewTask, SystemSetting)
from .schema_spec import validate_schema
from .seed_data import DOCUMENT_TYPES

User = get_user_model()


class ConstantsTests(TestCase):
    def test_transition_table_is_closed(self):
        valid = set(C.DocStatus)
        self.assertEqual(set(C.ALLOWED_TRANSITIONS), valid)  # every status has an entry
        for src, targets in C.ALLOWED_TRANSITIONS.items():
            self.assertTrue(targets <= valid, src)

    def test_setting_validation(self):
        self.assertEqual(C.validate_setting("AUTO_VERIFY_MIN_CONFIDENCE", "90"), 90.0)
        for key, bad in [("AUTO_VERIFY_MIN_CONFIDENCE", 150), ("MIN_OCR_WORDS", "x"),
                         ("GATEKEEPER_MODE", "NOPE"), ("NOT_A_KEY", 1)]:
            with self.assertRaises(ValueError):
                C.validate_setting(key, bad)


class SeedTests(TestCase):
    def test_schemas_valid_and_seed_idempotent(self):
        for spec in DOCUMENT_TYPES:
            self.assertEqual(validate_schema(spec["schema"]), [], spec["code"])
        call_command("seed_initial_data", verbosity=0)
        call_command("seed_initial_data", verbosity=0)
        self.assertEqual(set(DocumentType.objects.values_list("code", flat=True)),
                         {"INVOICE", "RESUME", "CERTIFICATE", "ID_CARD"})
        self.assertEqual(SystemSetting.get("AUTO_VERIFY_MIN_CONFIDENCE"), 85.0)

    def test_seed_keeps_admin_edits(self):
        call_command("seed_initial_data", verbosity=0)
        SystemSetting.set("AUTO_VERIFY_MIN_CONFIDENCE", 90)
        call_command("seed_initial_data", verbosity=0)
        self.assertEqual(SystemSetting.get("AUTO_VERIFY_MIN_CONFIDENCE"), 90.0)

    def test_required_keys(self):
        call_command("seed_initial_data", verbosity=0)
        self.assertEqual(DocumentType.objects.get(code="RESUME").required_keys(), ["full_name", "email"])


class ProfileTests(TestCase):
    def test_roles_on_create(self):
        u = User.objects.create_user("a", password="x")
        s = User.objects.create_superuser("b", "b@x.com", "x")
        self.assertEqual(u.profile.role, C.Role.USER)
        self.assertEqual(s.profile.role, C.Role.ADMIN)

    def test_demo_users_command(self):
        call_command("create_demo_users", verbosity=0)
        roles = dict(Profile.objects.values_list("user__username", "role"))
        self.assertEqual(roles, {"demo_user": "USER", "demo_reviewer": "REVIEWER", "demo_admin": "ADMIN"})


class DocumentModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("owner", password="x")

    def make_doc(self):
        doc = Document.objects.create(owner=self.user)
        v = DocumentVersion(document=doc, original_filename="inv.pdf", file_hash="a" * 64, created_by=self.user)
        v.file.save("inv.pdf", ContentFile(b"%PDF-1.4 test"), save=False)
        v.save()
        doc.current_version = v
        doc.save()
        return doc, v

    def test_version_and_storage_name(self):
        doc, v = self.make_doc()
        self.assertEqual(doc.display_name, "inv.pdf")
        self.assertIn(f"documents/user_{self.user.pk}/", v.file.name)
        self.assertNotIn("inv", v.file.name.split("/")[-1])  # random name, not the original
        self.assertTrue(doc.is_processing)  # default status UPLOADED
        v.file.delete(save=False)

    def test_review_lock(self):
        doc, v = self.make_doc()
        t = ReviewTask.objects.create(version=v, reasons=["LOW_CONFIDENCE"])
        self.assertFalse(t.is_lock_active())
        t.locked_by, t.locked_at = self.user, timezone.now()
        self.assertTrue(t.is_lock_active())
        t.locked_at = timezone.now() - timezone.timedelta(minutes=11)
        self.assertFalse(t.is_lock_active())
        v.file.delete(save=False)


class AuditAndAlertTests(TestCase):
    def test_audit_append_only(self):
        u = User.objects.create_user("x", password="x")
        entry = AuditLog.record(C.AuditAction.UPLOAD, user=u, details={"n": object().__class__.__name__})
        self.assertEqual(entry.username, "x")
        with self.assertRaises(PermissionError):
            entry.save()
        with self.assertRaises(PermissionError):
            entry.delete()

    def test_alert_dedupe(self):
        a = Notification.raise_alert("TESSERACT_MISSING", "missing", C.Severity.CRITICAL)
        b = Notification.raise_alert("TESSERACT_MISSING", "missing", C.Severity.CRITICAL)
        self.assertEqual(a.pk, b.pk)
        self.assertEqual(Notification.objects.get(pk=a.pk).occurrences, 2)
