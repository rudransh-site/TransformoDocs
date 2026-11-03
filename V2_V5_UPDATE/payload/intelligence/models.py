"""V2 tables. They only ADD to V1 (foreign keys into core), nothing in core changes."""
from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.text import slugify

from core.models import Document, DocumentVersion


class Tag(models.Model):
    name = models.CharField(max_length=60)
    slug = models.SlugField(max_length=80, unique=True, allow_unicode=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @classmethod
    def get_or_make(cls, name):
        name = " ".join((name or "").split())[:60]
        slug = slugify(name, allow_unicode=True)[:80]
        if not slug:
            return None
        tag, _ = cls.objects.get_or_create(slug=slug, defaults={"name": name})
        return tag


class DocumentTag(models.Model):
    SOURCES = [("AUTO", "Automatic"), ("MANUAL", "Added by user")]
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="tags")
    tag = models.ForeignKey(Tag, on_delete=models.CASCADE, related_name="documents")
    source = models.CharField(max_length=6, choices=SOURCES, default="AUTO")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["document", "tag"], name="uniq_document_tag")]
        ordering = ["tag__name"]


class DocumentInsight(models.Model):
    """Bookkeeping for the V2 enrichment of one document version. The summary and
    entities themselves live in the columns V1 reserved on DocumentVersion."""

    version = models.OneToOneField(DocumentVersion, on_delete=models.CASCADE, related_name="insight")
    key_phrases = models.JSONField(default=list, blank=True)
    classification_method = models.CharField(max_length=10, default="RULES")  # RULES | AI | MANUAL
    entity_backend = models.CharField(max_length=20, blank=True)  # spacy | regex
    summary_backend = models.CharField(max_length=20, blank=True)  # sumy | simple
    embedding_backend = models.CharField(max_length=20, blank=True)  # minilm | hash
    error = models.TextField(blank=True)
    enriched_at = models.DateTimeField(default=timezone.now)


class DocumentChunk(models.Model):
    version = models.ForeignKey(DocumentVersion, on_delete=models.CASCADE, related_name="chunks")
    index = models.PositiveIntegerField()
    text = models.TextField()
    embedding = models.BinaryField()  # float32 vector
    backend = models.CharField(max_length=20)

    class Meta:
        ordering = ["version_id", "index"]
        constraints = [models.UniqueConstraint(fields=["version", "index"], name="uniq_chunk_index")]


class ChatMessage(models.Model):
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="chat_messages")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    question = models.TextField()
    answer = models.TextField()
    mode = models.CharField(max_length=12, default="passages")  # field | entities | summary | passages | none
    sources = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["created_at", "id"]


class Translation(models.Model):
    version = models.ForeignKey(DocumentVersion, on_delete=models.CASCADE, related_name="translations")
    source_lang = models.CharField(max_length=5)
    target_lang = models.CharField(max_length=5)
    source_text = models.TextField()
    text = models.TextField()
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["version", "target_lang"], name="uniq_translation_lang")]
