"""Reviewer actions: claim, approve/edit, reject, change type. Every state
change re-checks the document under a row lock to handle two reviewers racing."""
from django.db import transaction
from django.utils import timezone

from core import constants as C
from core.models import AuditLog, Document, DocumentType, ReviewTask, SystemSetting

from . import extensions, pipeline
from .text_utils import coerce_strict


class ReviewConflict(Exception):
    """Someone else already decided (or is currently reviewing) this document."""

    def __init__(self, message, already_decided=False):
        super().__init__(message)
        self.message = message
        self.already_decided = already_decided


class ReviewValidationError(Exception):
    def __init__(self, errors):
        super().__init__("validation")
        self.errors = errors  # {field_key or "__all__": message}


def _decided_message(task, doc):
    who = task.reviewed_by or doc.verified_by
    when = task.decided_at or doc.verified_at or doc.updated_at
    verb = {"REJECTED": "rejected"}.get(doc.status, "verified")
    name = who.get_username() if who else "the system"
    return (f"This document was already {verb} by {name} at "
            f"{timezone.localtime(when):%H:%M}. Showing the final result.")


def claim(task_id, user):
    """Open a task for review. Returns (task, can_edit). If another reviewer holds
    a live lock, can_edit is False. Raises ReviewConflict if already decided."""
    with transaction.atomic():
        task = ReviewTask.objects.select_for_update().select_related("version__document", "locked_by").get(pk=task_id)
        doc = task.version.document
        if task.decision != C.ReviewDecision.PENDING or doc.status != C.DocStatus.PENDING_VERIFICATION:
            raise ReviewConflict(_decided_message(task, doc), already_decided=True)
        if task.is_lock_active() and task.locked_by_id != user.id:
            return task, False
        newly = task.locked_by_id != user.id or not task.is_lock_active()
        task.locked_by, task.locked_at = user, timezone.now()
        task.save(update_fields=["locked_by", "locked_at"])
    if newly:
        AuditLog.record(C.AuditAction.REVIEW_CLAIM, user=user, target=doc, details={"task": task.pk})
    return task, True


def clean_fields(doc_type, raw):
    """Validate reviewer input against the schema. Returns (cleaned, errors)."""
    cleaned, errors = {}, {}
    for spec in doc_type.field_specs():
        key, ftype = spec["key"], spec["type"]
        value = raw.get(key, "")
        blank = not (value or "").strip() if isinstance(value, str) else not value
        if blank:
            if spec.get("required"):
                errors[key] = "This field is required."
            continue
        val, err = coerce_strict(ftype, value)
        if err:
            errors[key] = err
        else:
            cleaned[key] = val
    return cleaned, errors


def _locked(task_id, user):
    task = ReviewTask.objects.select_for_update().select_related("version__document", "locked_by").get(pk=task_id)
    doc = Document.objects.select_for_update().select_related("doc_type").get(pk=task.version.document_id)
    if task.decision != C.ReviewDecision.PENDING or doc.status != C.DocStatus.PENDING_VERIFICATION:
        raise ReviewConflict(_decided_message(task, doc), already_decided=True)
    if task.is_lock_active() and task.locked_by_id != user.id:
        raise ReviewConflict(f"This document is currently being reviewed by {task.locked_by.get_username()}.")
    return task, doc


def approve(task_id, user, raw_fields, comment=""):
    with transaction.atomic():
        task, doc = _locked(task_id, user)
        if doc.doc_type is None:
            raise ReviewValidationError({"__all__": "Choose a document type first, then approve."})
        cleaned, errors = clean_fields(doc.doc_type, raw_fields)
        if errors:
            raise ReviewValidationError(errors)
        version = task.version
        before = version.structured_data or {}
        changes = {k: {"from": before.get(k), "to": cleaned.get(k)}
                   for k in set(before) | set(cleaned) if before.get(k) != cleaned.get(k)}
        confs = dict(version.field_confidences or {})
        for k in changes:
            confs[k] = 100.0
        version.structured_data, version.field_confidences = cleaned, confs
        version.save(update_fields=["structured_data", "field_confidences"])
        now = timezone.now()
        task.decision = C.ReviewDecision.EDITED if changes else C.ReviewDecision.APPROVED
        task.reviewed_by, task.comment, task.changes, task.decided_at = user, comment or "", changes, now
        task.save(update_fields=["decision", "reviewed_by", "comment", "changes", "decided_at"])
        pipeline.transition(doc, C.DocStatus.VERIFIED, actor=user, message="Verified by reviewer", percent=100,
                            detail="", extra={"verification_method": C.VerificationMethod.MANUAL,
                                              "verified_by": user, "verified_at": now})
    AuditLog.record(C.AuditAction.REVIEW_EDIT if changes else C.AuditAction.REVIEW_APPROVE, user=user, target=doc,
                    details={"task": task.pk, "changed_fields": sorted(changes)})
    extensions.after_review(doc, task.version)  # V2: refresh summary/tags from the corrected data
    return task


def reject(task_id, user, comment):
    comment = (comment or "").strip()
    if len(comment) < 3:
        raise ReviewValidationError({"__all__": "Please give a reason for rejecting this document."})
    with transaction.atomic():
        task, doc = _locked(task_id, user)
        now = timezone.now()
        task.decision, task.reviewed_by, task.comment, task.decided_at = C.ReviewDecision.REJECTED, user, comment, now
        task.save(update_fields=["decision", "reviewed_by", "comment", "decided_at"])
        pipeline.transition(doc, C.DocStatus.REJECTED, actor=user, message="Rejected", percent=100, detail=comment)
    AuditLog.record(C.AuditAction.REVIEW_REJECT, user=user, target=doc, details={"task": task.pk, "reason": comment})
    return task


def change_type(task_id, user, type_id):
    try:
        doc_type = DocumentType.objects.get(pk=type_id, is_active=True)
    except (DocumentType.DoesNotExist, ValueError, TypeError):
        raise ReviewValidationError({"__all__": "Choose a valid document type."}) from None
    with transaction.atomic():
        task, doc = _locked(task_id, user)
        pipeline.reextract(doc, task.version, doc_type, actor=user)
    return task


def pending_queryset():
    return (ReviewTask.objects.filter(decision=C.ReviewDecision.PENDING, version__document__status=C.DocStatus.PENDING_VERIFICATION)
            .select_related("version__document__owner", "version__document__doc_type", "locked_by")
            .order_by("version__overall_confidence", "created_at"))


def low_conf_threshold():
    return SystemSetting.get("AUTO_VERIFY_MIN_CONFIDENCE")
