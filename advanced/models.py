"""V5 tables (they only add to V1)."""
from django.conf import settings
from django.db import models
from django.utils import timezone

from core.models import Document


class DocumentOCRProfile(models.Model):
    """The language / handwriting choice a user made when uploading a document."""

    LANGUAGES = [("eng", "English"), ("hin", "Hindi"), ("eng+hin", "English + Hindi")]
    document = models.OneToOneField(Document, on_delete=models.CASCADE, related_name="ocr_profile")
    language = models.CharField(max_length=10, choices=LANGUAGES, default="eng")
    handwriting = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f"{self.document_id}: {self.language}{' + handwriting' if self.handwriting else ''}"


class GeneratedDocument(models.Model):
    TEMPLATES = [("invoice", "Invoice"), ("certificate", "Certificate")]
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="generated_documents")
    template = models.CharField(max_length=20, choices=TEMPLATES)
    title = models.CharField(max_length=150)
    data = models.JSONField(default=dict)
    saved_document = models.ForeignKey(Document, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return self.title
