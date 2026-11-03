"""TransformoDocs data model (V1, designed so V2-V4 add tables, not rewrites).

Document         = identity + lifecycle status (what users see in lists)
DocumentVersion  = the file and everything extracted from it (V1 always has v1)
DocumentType     = classification rules + extraction schema (data, not code)
"""
import json
import logging
import os
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import DatabaseError, models
from django.utils import timezone

from . import constants as C

logger = logging.getLogger("transformodocs.models")
audit_logger = logging.getLogger("transformodocs.audit")


# ------------------------------------------------------------------ users
class Profile(models.Model):
    """Holds the RBAC role. One account = one role."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile")
    role = models.CharField(max_length=20, choices=C.Role.choices, default=C.Role.USER, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.get_username()} ({self.role})"


# -------------------------------------------------------- document types
class DocumentType(models.Model):
    """One class of document (INVOICE, RESUME, CERTIFICATE, ID_CARD).

    classification_rules and schema are JSON so the V4 Schema Builder only
    has to write rows here. Formats are documented in core/seed_data.py and
    validated by core/schema_spec.py.
    """

    code = models.CharField(max_length=40, unique=True)
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    classification_rules = models.JSONField(default=dict, blank=True)
    schema = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True)
    is_builtin = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def field_specs(self):
        return (self.schema or {}).get("fields", [])

    def required_keys(self):
        return [f["key"] for f in self.field_specs() if f.get("required")]


# ------------------------------------------------------------- documents
class Document(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="documents")
    doc_type = models.ForeignKey(
        DocumentType, null=True, blank=True, on_delete=models.PROTECT, related_name="documents"
    )  # null until classified / when UNKNOWN
    status = models.CharField(
        max_length=30, choices=C.DocStatus.choices, default=C.DocStatus.UPLOADED, db_index=True
    )
    # Live progress for the status page (polled every second)
    progress_message = models.CharField(max_length=200, blank=True)
    progress_percent = models.PositiveSmallIntegerField(default=0)
    # Friendly failure text shown to the user + reference id for support/logs
    status_detail = models.TextField(blank=True)
    error_ref = models.CharField(max_length=12, blank=True)
    # "Auto-verified" vs "Verified by X"
    verification_method = models.CharField(
        max_length=10, choices=C.VerificationMethod.choices, blank=True, default=""
    )
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="verified_documents"
    )
    verified_at = models.DateTimeField(null=True, blank=True)
    current_version = models.ForeignKey(
        "DocumentVersion", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    archived_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["owner", "status"])]

    def __str__(self):
        return f"#{self.pk} {self.display_name}"

    @property
    def display_name(self):
        v = self.current_version
        return v.original_filename if v else f"Document {self.pk}"

    @property
    def is_processing(self):
        return self.status in C.PROCESSING_STATES


def version_upload_path(instance, filename):
    """Store under a random name: safe on Windows (no odd characters) and
    never collides. The real filename lives in original_filename."""
    ext = os.path.splitext(filename)[1].lower()
    return f"documents/user_{instance.document.owner_id}/{uuid.uuid4().hex}{ext}"


class DocumentVersion(models.Model):
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="versions")
    version_number = models.PositiveIntegerField(default=1)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    # --- the file
    file = models.FileField(upload_to=version_upload_path, max_length=255)
    original_filename = models.CharField(max_length=255)
    file_size = models.PositiveIntegerField(default=0)
    mime_type = models.CharField(max_length=100, blank=True)
    file_hash = models.CharField(max_length=64, db_index=True)  # SHA-256 (duplicates, V3 compare)
    page_count = models.PositiveSmallIntegerField(default=0)

    # --- detection / extraction output
    is_machine_readable = models.BooleanField(null=True, blank=True)  # null = not checked yet
    detection_method = models.CharField(max_length=10, choices=C.DetectionMethod.choices, blank=True)
    extracted_text = models.TextField(blank=True)
    layout_json = models.JSONField(default=dict, blank=True)  # pages[] -> blocks[] (see plan)
    structured_data = models.JSONField(default=dict, blank=True)  # {field_key: value}
    field_confidences = models.JSONField(default=dict, blank=True)  # {field_key: 0-100}
    overall_confidence = models.FloatField(null=True, blank=True)  # 0-100
    classification_confidence = models.FloatField(null=True, blank=True)  # 0-1
    quality_flag = models.CharField(max_length=15, choices=C.QualityFlag.choices, blank=True, default="")

    # --- placeholders for V2 (empty in V1, so V2 needs no migration here)
    language = models.CharField(max_length=10, default="en")
    summary = models.TextField(blank=True)
    entities = models.JSONField(default=list, blank=True)

    # --- timing (admin stats: average processing time)
    processing_started_at = models.DateTimeField(null=True, blank=True)
    processing_finished_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-version_number"]
        constraints = [
            models.UniqueConstraint(fields=["document", "version_number"], name="uniq_document_version_number")
        ]

    def __str__(self):
        return f"{self.original_filename} v{self.version_number}"

    @property
    def processing_seconds(self):
        if self.processing_started_at and self.processing_finished_at:
            return round((self.processing_finished_at - self.processing_started_at).total_seconds(), 1)
        return None


# --------------------------------------------------------------- review
class ReviewTask(models.Model):
    version = models.ForeignKey(DocumentVersion, on_delete=models.CASCADE, related_name="review_tasks")
    reasons = models.JSONField(default=list, blank=True)  # e.g. ["LOW_CONFIDENCE", "MISSING:total_amount"]
    decision = models.CharField(
        max_length=10, choices=C.ReviewDecision.choices, default=C.ReviewDecision.PENDING, db_index=True
    )
    locked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    locked_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="reviews"
    )
    comment = models.TextField(blank=True)  # required when rejecting
    changes = models.JSONField(default=dict, blank=True)  # {field: {"from": x, "to": y}}
    created_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"Review of {self.version} [{self.decision}]"

    @property
    def document(self):
        return self.version.document

    def is_lock_active(self, minutes=None):
        if not self.locked_by_id or not self.locked_at:
            return False
        minutes = minutes or SystemSetting.get("REVIEW_LOCK_MINUTES")
        return timezone.now() < self.locked_at + timedelta(minutes=minutes)


# ---------------------------------------------------------------- audit
class AuditLog(models.Model):
    """Append-only record of who did what. Rows cannot be edited or deleted
    through the ORM instance API or the admin."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="audit_entries"
    )
    username = models.CharField(max_length=150, blank=True)  # snapshot, survives user changes
    action = models.CharField(max_length=40, choices=C.AuditAction.choices, db_index=True)
    target_type = models.CharField(max_length=40, blank=True)
    target_id = models.PositiveBigIntegerField(null=True, blank=True)
    details = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["target_type", "target_id"])]

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M:%S} {self.action} {self.username or '-'}"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise PermissionError("AuditLog entries are append-only.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("AuditLog entries cannot be deleted.")

    @classmethod
    def record(cls, action, user=None, target=None, details=None, request=None,
               target_type="", target_id=None):
        """Write one audit row. Never raises: if the DB is down the entry goes
        to logs/app.log instead (plan: DB failure = file-only logging)."""
        try:
            if target is not None:
                target_type, target_id = target.__class__.__name__, target.pk
            ip = None
            if request is not None:
                user = user or getattr(request, "user", None)
                ip = request.META.get("REMOTE_ADDR") or None
            if user is not None and not getattr(user, "is_authenticated", False):
                user = None
            clean = json.loads(json.dumps(details or {}, default=str))
            return cls.objects.create(
                user=user, username=user.get_username() if user else "", action=action,
                target_type=target_type, target_id=target_id, details=clean, ip_address=ip,
            )
        except Exception:  # noqa: BLE001 - auditing must never break a request
            audit_logger.exception("Could not write audit entry: %s %s %s", action, target_type, target_id)
            return None


# --------------------------------------------------------- notifications
class Notification(models.Model):
    """Admin alerts (no email: the demo is offline)."""

    severity = models.CharField(max_length=10, choices=C.Severity.choices, default=C.Severity.WARNING)
    code = models.CharField(max_length=60, db_index=True)  # e.g. TESSERACT_MISSING
    message = models.TextField()
    details = models.JSONField(default=dict, blank=True)
    is_read = models.BooleanField(default=False)
    occurrences = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-last_seen_at"]

    def __str__(self):
        return f"[{self.severity}] {self.code}"

    @classmethod
    def raise_alert(cls, code, message, severity=C.Severity.WARNING, details=None, dedupe_minutes=60):
        """Create an alert, or bump the counter on an unread one with the same
        code (so a repeated failure does not flood the Admin dashboard)."""
        try:
            now = timezone.now()
            existing = cls.objects.filter(
                code=code, is_read=False, last_seen_at__gte=now - timedelta(minutes=dedupe_minutes)
            ).first()
            if existing:
                existing.occurrences += 1
                existing.last_seen_at = now
                existing.save(update_fields=["occurrences", "last_seen_at"])
                return existing
            clean = json.loads(json.dumps(details or {}, default=str))
            return cls.objects.create(code=code, message=message, severity=severity, details=clean)
        except Exception:  # noqa: BLE001
            logger.exception("Could not raise admin alert %s", code)
            return None


# -------------------------------------------------------------- settings
class SystemSetting(models.Model):
    """Admin-editable key/value settings (thresholds now, gatekeeper in V4)."""

    key = models.CharField(max_length=60, unique=True)
    value = models.JSONField()
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["key"]

    def __str__(self):
        return f"{self.key} = {self.value}"

    @classmethod
    def get(cls, key):
        """Current value, falling back to the default if the row is missing or
        the DB is unreachable (so the pipeline never crashes on a setting)."""
        default = C.SETTING_DEFINITIONS[key]["default"]
        try:
            row = cls.objects.filter(key=key).first()
        except DatabaseError:
            logger.warning("Could not read setting %s; using default", key)
            return default
        return default if row is None else row.value

    @classmethod
    def set(cls, key, value, user=None):
        clean = C.validate_setting(key, value)  # raises ValueError if invalid
        obj, _ = cls.objects.update_or_create(key=key, defaults={"value": clean, "updated_by": user})
        return obj

    @classmethod
    def ensure_defaults(cls):
        """Create missing rows only; never overwrites an Admin's edits."""
        for key, spec in C.SETTING_DEFINITIONS.items():
            cls.objects.get_or_create(key=key, defaults={"value": spec["default"]})
