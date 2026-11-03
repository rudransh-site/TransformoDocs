"""The single seam between V1 and the optional V2 (intelligence) / V5 (advanced) apps.

V1 code calls these functions. If an app is not installed, or something inside it
fails, the function returns a harmless default and logs the problem, so the core
pipeline never breaks because of an optional feature.
"""
import logging

from django.apps import apps

logger = logging.getLogger("transformodocs.extensions")


def enabled(label):
    return apps.is_installed(label)


def processing_options(doc):
    """OCR language / handwriting choice made at upload (V5)."""
    options = {"lang": None, "handwriting": False}
    if enabled("advanced"):
        try:
            from advanced.services.options import options_for

            options.update(options_for(doc))
        except Exception:  # noqa: BLE001
            logger.exception("Could not read processing options for document %s", getattr(doc, "pk", "?"))
    return options


def ocr_page(gray, conf_floor, options):
    """OCR one page image. Routes to the handwriting engine when requested (V5)."""
    from . import ocr

    if options.get("lang") and enabled("advanced"):
        from advanced.services.options import ensure_language

        ensure_language(options["lang"])  # raises a friendly PipelineError if the language is not installed
    if options.get("handwriting") and enabled("advanced"):
        try:
            from advanced.services import handwriting

            return handwriting.recognize(gray, conf_floor, lang=options.get("lang"))
        except Exception:  # noqa: BLE001
            logger.exception("Handwriting engine failed; falling back to standard OCR")
    return ocr.ocr_page_with_retry(gray, conf_floor, lang=options.get("lang"))


def refine_classification(text, doc_type, class_conf):
    """Second opinion when the rule-based classifier is unsure (V2 AI classification)."""
    if doc_type is None and enabled("intelligence"):
        try:
            from intelligence.services.ai_classify import refine

            return refine(text, doc_type, class_conf)
        except Exception:  # noqa: BLE001
            logger.exception("AI classification failed; keeping the rule-based result")
    return doc_type, class_conf


def extra_reasons(doc, version):
    """Extra review reasons, e.g. HANDWRITTEN (V5)."""
    if enabled("advanced"):
        try:
            from advanced.services.options import review_reasons

            return review_reasons(doc, version)
        except Exception:  # noqa: BLE001
            logger.exception("Could not compute extra review reasons")
    return []


def after_pipeline(doc, version):
    """Summary, entities, tags, embeddings (V2). Never raises."""
    if enabled("intelligence"):
        try:
            from intelligence.services.enrich import enrich_version

            enrich_version(version, doc=doc)
        except Exception:  # noqa: BLE001
            logger.exception("Enrichment failed for document %s", getattr(doc, "pk", "?"))


def save_upload_options(doc, data):
    """Store the OCR language / handwriting choice that came with an upload (V5)."""
    if enabled("advanced"):
        try:
            from advanced.services.options import save_options

            save_options(doc, data)
        except Exception:  # noqa: BLE001
            logger.exception("Could not save upload options for document %s", getattr(doc, "pk", "?"))


def detail_context(request, doc):
    """Extra template context for the document page (V2 tabs, chat panel)."""
    ctx = {"v2_enabled": enabled("intelligence"), "v5_enabled": enabled("advanced")}
    if enabled("intelligence"):
        try:
            from intelligence.services.context import detail_context as v2_ctx

            ctx.update(v2_ctx(request, doc))
        except Exception:  # noqa: BLE001
            logger.exception("Could not build V2 context for document %s", doc.pk)
            ctx["v2_enabled"] = False
    if enabled("advanced"):
        try:
            from advanced.services.options import detail_context as v5_ctx

            ctx.update(v5_ctx(doc))
        except Exception:  # noqa: BLE001
            logger.exception("Could not build V5 context for document %s", doc.pk)
    return ctx


def upload_context():
    """Extra context for the upload page (V5 language selector)."""
    ctx = {"v5_enabled": enabled("advanced")}
    if enabled("advanced"):
        try:
            from advanced.services.options import upload_context as v5_ctx

            ctx.update(v5_ctx())
        except Exception:  # noqa: BLE001
            logger.exception("Could not build V5 upload context")
            ctx["v5_enabled"] = False
    return ctx


def extra_health_checks():
    checks = []
    for label, path in (("intelligence", "intelligence.services.health"), ("advanced", "advanced.services.health")):
        if enabled(label):
            try:
                module = __import__(path, fromlist=["checks"])
                checks.extend(module.checks())
            except Exception:  # noqa: BLE001
                logger.exception("Health checks for %s failed to run", label)
    return checks


def after_review(doc, version):
    """Refresh summary/tags after a reviewer edits fields (V2)."""
    if enabled("intelligence"):
        try:
            from intelligence.services.enrich import refresh_version

            refresh_version(version, doc=doc)
        except Exception:  # noqa: BLE001
            logger.exception("Refresh after review failed for document %s", getattr(doc, "pk", "?"))
