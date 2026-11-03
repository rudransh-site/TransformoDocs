"""Upload handling shared by the web form and the REST API."""
import logging

from django.core.files.base import ContentFile
from django.db import transaction

from core import constants as C
from core.models import AuditLog, Document, DocumentVersion, Notification

from .files import UploadError, safe_name, validate_upload

logger = logging.getLogger("transformodocs.uploads")


class DuplicateUpload(Exception):
    def __init__(self, existing, can_force, can_retry):
        super().__init__("duplicate")
        self.existing = existing
        self.can_force = can_force  # upload as a fresh copy (existing one was rejected)
        self.can_retry = can_retry  # existing one failed: retry it instead


def find_duplicates(user, sha256):
    return (DocumentVersion.objects.filter(document__owner=user, file_hash=sha256)
            .exclude(document__status=C.DocStatus.ARCHIVED).select_related("document").order_by("-created_at"))


def handle_upload(user, uploaded, request=None, force=False):
    """Validate, de-duplicate and store an upload. Returns the new Document
    (status UPLOADED). Raises UploadError or DuplicateUpload."""
    try:
        v = validate_upload(uploaded)
    except UploadError as exc:
        AuditLog.record(C.AuditAction.UPLOAD_REJECTED, user=user, request=request,
                        details={"filename": safe_name(getattr(uploaded, "name", "")), "code": exc.code})
        raise

    dups = list(find_duplicates(user, v.sha256))
    if dups:
        existing = dups[0].document
        blocking = [d for d in dups if d.document.status != C.DocStatus.REJECTED]
        if blocking or not force:
            existing = (blocking or dups)[0].document
            AuditLog.record(C.AuditAction.DUPLICATE_BLOCKED, user=user, request=request, target=existing,
                            details={"filename": v.name})
            raise DuplicateUpload(existing, can_force=not blocking, can_retry=existing.status == C.DocStatus.FAILED)

    saved_name = None
    try:
        with transaction.atomic():
            doc = Document.objects.create(owner=user, progress_message="File received", progress_percent=2)
            version = DocumentVersion(document=doc, version_number=1, created_by=user, original_filename=v.name,
                                      file_size=v.size, mime_type=v.mime, file_hash=v.sha256, page_count=v.page_count)
            version.file.save(v.name, ContentFile(v.data), save=False)
            saved_name = version.file.name
            version.save()
            doc.current_version = version
            doc.save(update_fields=["current_version"])
    except OSError as exc:
        logger.error("Could not store upload: %s", exc)
        if saved_name:
            try:
                version.file.storage.delete(saved_name)
            except Exception:  # noqa: BLE001
                pass
        Notification.raise_alert("STORAGE_ERROR", "Uploads cannot be saved (disk full or media folder not writable).",
                                 severity=C.Severity.CRITICAL, details={"error": str(exc)})
        raise UploadError("STORAGE_ERROR", "Upload failed, please try again. An administrator has been notified.") from exc

    AuditLog.record(C.AuditAction.UPLOAD, user=user, request=request, target=doc,
                    details={"filename": v.name, "size": v.size, "pages": v.page_count})
    return doc
