"""Run all V2 enrichment for one document version (summary, entities, tags, chunks)."""
import logging

from django.db import transaction
from django.utils import timezone

from . import ai_classify, embeddings, nlp, summarize, tags
from . import text as T

logger = logging.getLogger("transformodocs.v2")


def _save_tags(doc, names):
    from intelligence.models import DocumentTag, Tag

    with transaction.atomic():
        DocumentTag.objects.filter(document=doc, source="AUTO").delete()
        manual = set(DocumentTag.objects.filter(document=doc).values_list("tag__slug", flat=True))
        for name in names:
            tag = Tag.get_or_make(name)
            if tag and tag.slug not in manual:
                DocumentTag.objects.get_or_create(document=doc, tag=tag, defaults={"source": "AUTO"})


def enrich_version(version, doc=None):
    from intelligence.models import DocumentInsight

    doc = doc or version.document
    text = version.extracted_text or ""
    if not text.strip():
        return None
    method = ai_classify.pop_method()
    errors = []
    entities, ent_backend = nlp.extract_entities(text)
    phrases = tags.key_phrases(text)
    code = doc.doc_type.code if doc.doc_type else ""
    summary, sum_backend = summarize.make_summary(code, version.structured_data, text)
    version.entities, version.summary, version.language = entities, summary, T.detect_language(text)
    version.save(update_fields=["entities", "summary", "language"])
    try:
        _save_tags(doc, tags.suggest_tags(doc.doc_type.name if doc.doc_type else "", entities, phrases,
                                          version.structured_data))
    except Exception as exc:  # noqa: BLE001
        logger.exception("Tagging failed")
        errors.append(f"tags: {exc}")
    emb_backend = ""
    try:
        emb_backend = embeddings.ensure_chunks(version)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Embedding failed")
        errors.append(f"embeddings: {exc}")
    insight, _ = DocumentInsight.objects.update_or_create(
        version=version,
        defaults={"key_phrases": phrases, "entity_backend": ent_backend, "summary_backend": sum_backend,
                  "embedding_backend": emb_backend, "error": "; ".join(errors), "enriched_at": timezone.now(),
                  "classification_method": "AI" if method == "AI" else "RULES"})
    return insight


def refresh_version(version, doc=None):
    """Cheap refresh after a reviewer corrects fields: summary + tags only."""
    from intelligence.models import DocumentInsight

    doc = doc or version.document
    version.refresh_from_db()
    code = doc.doc_type.code if doc.doc_type else ""
    summary, backend = summarize.make_summary(code, version.structured_data, version.extracted_text)
    version.summary = summary
    version.save(update_fields=["summary"])
    insight = DocumentInsight.objects.filter(version=version).first()
    entities = version.entities or []
    _save_tags(doc, tags.suggest_tags(doc.doc_type.name if doc.doc_type else "", entities,
                                      (insight.key_phrases if insight else []), version.structured_data))
    if insight:
        insight.summary_backend = backend
        insight.save(update_fields=["summary_backend"])
