"""The synchronous document pipeline and the single status-transition function.

Flow: UPLOADED -> DETECTING -> [OCR_PROCESSING] -> CLASSIFYING -> EXTRACTING
      -> VERIFIED (auto) | PENDING_VERIFICATION (review)       any step -> FAILED
"""
import logging
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from statistics import median

import pymupdf
from django.conf import settings
from django.utils import timezone

from core import constants as C
from core.models import AuditLog, Document, DocumentType, Notification, ReviewTask, SystemSetting

from . import classify as classify_mod
from . import extensions, extractor, native, ocr
from .errors import PipelineError
from .files import kind_of

logger = logging.getLogger("transformodocs.pipeline")


class InvalidTransition(Exception):
    pass


# ------------------------------------------------------------ transitions
def transition(doc, new_status, actor=None, message=None, percent=None, detail=None, extra=None, audit=True):
    """The ONLY way a document's status changes. Checks the allowed table, does an
    atomic compare-and-swap in the DB, and writes the audit log."""
    new_status, cur = C.DocStatus(new_status), C.DocStatus(doc.status)
    if new_status not in C.ALLOWED_TRANSITIONS.get(cur, set()):
        raise InvalidTransition(f"{cur.value} -> {new_status.value} is not allowed")
    fields = {"status": new_status.value, "updated_at": timezone.now()}
    if message is not None:
        fields["progress_message"] = message[:200]
    if percent is not None:
        fields["progress_percent"] = percent
    if detail is not None:
        fields["status_detail"] = detail
    if extra:
        fields.update(extra)
    if not Document.objects.filter(pk=doc.pk, status=cur.value).update(**fields):
        raise InvalidTransition("The document status was changed by someone else.")
    for key, value in fields.items():
        setattr(doc, key, value)
    if audit:
        AuditLog.record(C.AuditAction.STATUS_CHANGE, user=actor, target=doc,
                        details={"from": cur.value, "to": new_status.value})


def set_progress(doc, message, percent):
    now = timezone.now()
    Document.objects.filter(pk=doc.pk).update(progress_message=message[:200], progress_percent=percent, updated_at=now)
    doc.progress_message, doc.progress_percent, doc.updated_at = message, percent, now


# ------------------------------------------------------------- detection
@dataclass
class Detection:
    kind: str
    method: str
    native: list = field(default_factory=list)  # PDF: per-page "has text layer"
    n_pages: int = 1
    docx_lines: list = field(default_factory=list)
    docx_blocks: list = field(default_factory=list)
    docx_images: list = field(default_factory=list)


def detect(path, kind, min_chars):
    if kind == "pdf":
        doc = pymupdf.open(path)
        try:
            flags = [native.pdf_page_is_native(p, min_chars) for p in doc]
        finally:
            doc.close()
        method = (C.DetectionMethod.NATIVE if all(flags) else
                  C.DetectionMethod.OCR if not any(flags) else C.DetectionMethod.MIXED)
        return Detection(kind, method.value, flags, len(flags))
    if kind == "docx":
        lines, blocks = native.docx_text_and_layout(path)
        if sum(len(l["text"]) for l in lines) >= min_chars:
            return Detection(kind, C.DetectionMethod.NATIVE.value, [True], 1, lines, blocks)
        images = native.docx_images(path)
        if not images:
            raise PipelineError("EMPTY_DOCUMENT", "No readable text found in this document. Please upload a clearer scan or file.")
        return Detection(kind, C.DetectionMethod.OCR.value, [False] * len(images), len(images), docx_images=images)
    return Detection("image", C.DetectionMethod.OCR.value, [False], 1)


def _blocks_from_ocr(lines):
    blocks, group = [], []

    def flush():
        if not group:
            return
        text = "\n".join(l["text"] for l in group)
        bbox = [min(l["bbox"][0] for l in group), min(l["bbox"][1] for l in group),
                max(l["bbox"][2] for l in group), max(l["bbox"][3] for l in group)]
        height = median([max(1, l["bbox"][3] - l["bbox"][1]) for l in group])
        conf = round(sum(l["conf"] for l in group) / len(group), 1)
        blocks.append({"text": text, "bbox": bbox, "conf": conf, "_h": height, "_n": len(group)})

    last = None
    for l in lines:
        if last is not None and l["par"] != last:
            flush()
            group = []
        group.append(l)
        last = l["par"]
    flush()
    heights = [b["_h"] for b in blocks]
    body = median(heights) if heights else 0
    for b in blocks:
        h, n = b.pop("_h"), b.pop("_n")
        if body and h >= body * 1.35 and n <= 2 and len(b["text"]) <= 100:
            b["type"], b["level"] = "heading", (1 if h >= body * 1.8 else 2)
        elif n == 1 and ":" in b["text"][:40]:
            b["type"], b["level"] = "kv", 0
        else:
            b["type"], b["level"] = "paragraph", 0
    return blocks


def read_pages(path, det, conf_floor, progress, options=None):
    """Run native extraction / OCR for every page. Returns a list of page dicts."""
    options = options or {}
    pages, total = [], det.n_pages
    pdf = pymupdf.open(path) if det.kind == "pdf" else None
    try:
        for i in range(total):
            progress(i, total, det.native[i])
            if det.kind == "pdf":
                page = pdf.load_page(i)
                if det.native[i]:
                    lines, blocks, w, h = native.pdf_native_page(page)
                    pages.append({"page": i + 1, "method": "NATIVE", "width": w, "height": h, "lines": lines,
                                  "blocks": blocks, "words": sum(len(l["text"].split()) for l in lines),
                                  "conf": 100.0, "garbage": False})
                    continue
                pix = page.get_pixmap(dpi=settings.OCR_RENDER_DPI, colorspace=pymupdf.csGRAY, alpha=False)
                gray = ocr.pixmap_to_gray(pix)
            elif det.kind == "docx":
                if det.native[i]:
                    pages.append({"page": 1, "method": "NATIVE", "width": 0, "height": 0, "lines": det.docx_lines,
                                  "blocks": det.docx_blocks, "words": sum(len(l["text"].split()) for l in det.docx_lines),
                                  "conf": 100.0, "garbage": False})
                    continue
                gray = ocr.load_image_gray(det.docx_images[i])
            else:
                gray = ocr.load_image_gray(path)
            res = extensions.ocr_page(gray, conf_floor, options)
            pages.append({"page": i + 1, "method": "OCR", "width": res.width, "height": res.height,
                          "lines": res.lines, "blocks": _blocks_from_ocr(res.lines), "words": res.words,
                          "conf": res.mean_conf, "garbage": res.garbage})
    finally:
        if pdf is not None:
            pdf.close()
    return pages


def lines_from_layout(layout):
    out = []
    for p in (layout or {}).get("pages", []):
        out.extend({"text": l["text"], "conf": l.get("conf", 100.0)} for l in p.get("lines", []))
    return out


# ------------------------------------------------------------ assessment
def compute_reasons(doc_type, data, overall_conf, quality_flag="", extra=None):
    reasons = list(extra or [])
    if quality_flag == C.QualityFlag.LOW_QUALITY:
        reasons.append("LOW_QUALITY")
    if (overall_conf or 0) < SystemSetting.get("AUTO_VERIFY_MIN_CONFIDENCE"):
        reasons.append("LOW_CONFIDENCE")
    if doc_type is None:
        reasons.append("UNKNOWN_TYPE")
    else:
        for key in doc_type.required_keys():
            if data.get(key) in (None, "", []):
                reasons.append(f"MISSING:{key}")
    return reasons


def ensure_review_task(version, reasons):
    task = ReviewTask.objects.filter(version=version, decision=C.ReviewDecision.PENDING).first()
    if task:
        task.reasons = reasons
        task.save(update_fields=["reasons"])
        return task
    return ReviewTask.objects.create(version=version, reasons=reasons)


def finish_extraction(doc, version, doc_type, class_conf, lines, overall_conf, auto_allowed=True, actor=None):
    """Extract fields for `doc_type`, then auto-verify or queue for review."""
    data, confs = extractor.extract_fields(doc_type.schema, lines) if doc_type else ({}, {})
    version.structured_data, version.field_confidences = data, confs
    version.classification_confidence = class_conf
    version.processing_finished_at = timezone.now()
    version.save(update_fields=["structured_data", "field_confidences", "classification_confidence",
                                "processing_finished_at"])
    reasons = compute_reasons(doc_type, data, overall_conf, version.quality_flag,
                              extra=extensions.extra_reasons(doc, version))
    if not reasons and auto_allowed:
        transition(doc, C.DocStatus.VERIFIED, actor=actor, message="Verified automatically", percent=100,
                   extra={"verification_method": C.VerificationMethod.AUTO, "verified_at": timezone.now()})
    else:
        ensure_review_task(version, reasons)
        transition(doc, C.DocStatus.PENDING_VERIFICATION, actor=actor, message="Waiting for review", percent=100)
    extensions.after_pipeline(doc, version)  # V2: summary, entities, tags, embeddings (never raises)
    return reasons


# ---------------------------------------------------------------- the run
def _gatekeeper_allows(doc, version):
    """V4 hook: the Machine-Readability Gatekeeper decides here (after detection,
    before OCR). V1 behaviour is always AUTO_TRANSFORM."""
    return True


def run_pipeline(document_id):
    """Process one document synchronously. Safe to call twice: only the first
    caller claims the work. Never raises; failures become FAILED documents."""
    doc = Document.objects.select_related("current_version", "owner").get(pk=document_id)
    version = doc.current_version
    if version is None or doc.status not in (C.DocStatus.UPLOADED, C.DocStatus.DETECTING):
        return doc
    from core.models import DocumentVersion

    claimed = DocumentVersion.objects.filter(pk=version.pk, processing_started_at__isnull=True).update(
        processing_started_at=timezone.now())
    if not claimed:
        return doc
    version.refresh_from_db()
    try:
        _run(doc, version)
    except PipelineError as exc:
        fail_document(doc, version, exc.user_message, exc.code, exc=None, notify=exc.notify_admin,
                      severity=exc.severity)
    except Exception as exc:  # noqa: BLE001
        fail_document(doc, version, None, "UNEXPECTED", exc=exc)
    doc.refresh_from_db()
    return doc


def _run(doc, version):
    if doc.status == C.DocStatus.UPLOADED:
        transition(doc, C.DocStatus.DETECTING, message="Checking whether the document is machine-readable", percent=5)
    min_chars = SystemSetting.get("NATIVE_PAGE_MIN_CHARS")
    floor = SystemSetting.get("LOW_QUALITY_CONFIDENCE_FLOOR")
    min_words = SystemSetting.get("MIN_OCR_WORDS")
    kind = kind_of(version.original_filename)
    path = version.file.path

    try:
        det = detect(path, kind, min_chars)
    except PipelineError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not open %s: %s", version.original_filename, exc)
        raise PipelineError("CORRUPTED", "We couldn't open this file. It may be damaged.") from exc

    version.is_machine_readable = det.method == C.DetectionMethod.NATIVE.value
    version.detection_method = det.method
    version.page_count = det.n_pages
    version.save(update_fields=["is_machine_readable", "detection_method", "page_count"])

    if not _gatekeeper_allows(doc, version):
        return

    options = extensions.processing_options(doc)
    uses_ocr = det.method != C.DetectionMethod.NATIVE.value
    if uses_ocr:
        transition(doc, C.DocStatus.OCR_PROCESSING, message="Preparing text recognition", percent=15)
    else:
        set_progress(doc, "Reading the text layer", 20)

    def progress(i, total, is_native):
        pct = 15 + int(55 * i / max(total, 1))
        label = "Reading page" if not is_native else "Extracting text from page"
        set_progress(doc, f"{label} {i + 1} of {total}", pct)

    pages = read_pages(path, det, floor, progress, options)

    total_words = sum(p["words"] for p in pages)
    if total_words == 0:
        raise PipelineError("EMPTY_DOCUMENT",
                            "No readable text found in this document. Please upload a clearer scan or file.")
    overall = round(sum(p["conf"] * p["words"] for p in pages) / total_words, 1)
    page_texts = ["\n".join(l["text"] for l in p["lines"]) for p in pages if p["lines"]]
    text = "\n\n".join(page_texts)
    alnum = sum(ocr.is_texty(c) for c in text)
    nonspace = sum(not c.isspace() for c in text) or 1
    low_quality = uses_ocr and (total_words < min_words or overall < floor or alnum / nonspace < 0.5)

    layout = {"version": 1, "pages": [
        {"page": p["page"], "method": p["method"], "width": p["width"], "height": p["height"],
         "lines": p["lines"], "blocks": p["blocks"]} for p in pages]}
    version.extracted_text, version.layout_json, version.overall_confidence = text, layout, overall
    version.quality_flag = C.QualityFlag.LOW_QUALITY.value if low_quality else ""
    version.save(update_fields=["extracted_text", "layout_json", "overall_confidence", "quality_flag"])

    if low_quality:
        logger.warning("Low quality scan: doc=%s words=%s conf=%s", doc.pk, total_words, overall)
        version.processing_finished_at = timezone.now()
        version.save(update_fields=["processing_finished_at"])
        ensure_review_task(version, ["LOW_QUALITY"])
        transition(doc, C.DocStatus.PENDING_VERIFICATION, message="Scan quality is low; sent to a reviewer", percent=100)
        return

    transition(doc, C.DocStatus.CLASSIFYING, message="Identifying document type", percent=75)
    doc_type, class_conf, _scores = classify_mod.classify(text, SystemSetting.get("MIN_CLASSIFICATION_CONFIDENCE"))
    doc_type, class_conf = extensions.refine_classification(text, doc_type, class_conf)  # V2 second opinion
    Document.objects.filter(pk=doc.pk).update(doc_type=doc_type)
    doc.doc_type = doc_type

    transition(doc, C.DocStatus.EXTRACTING, message="Extracting and validating fields", percent=88)
    finish_extraction(doc, version, doc_type, class_conf, lines_from_layout(layout), overall)


# ---------------------------------------------------------------- failures
def fail_document(doc, version, user_message, code, exc=None, notify=False, severity="WARNING"):
    ref = uuid.uuid4().hex[:6].upper()
    if user_message is None:
        user_message = f"Something went wrong while processing this document (ref #{ref}). Please try again."
    if exc is not None:
        logger.error("Pipeline failure doc=%s ref=%s: %s\n%s", doc.pk, ref, exc, traceback.format_exc())
    else:
        logger.warning("Pipeline failure doc=%s ref=%s code=%s: %s", doc.pk, ref, code, user_message)
    signature = f"{code}:{type(exc).__name__ if exc else ''}:{str(exc)[:60] if exc else ''}"
    try:
        transition(doc, C.DocStatus.FAILED, message="Failed", percent=0, detail=user_message,
                   extra={"error_ref": ref}, audit=False)
    except InvalidTransition:
        logger.error("Could not mark doc %s as failed (status=%s)", doc.pk, doc.status)
    if version is not None:
        version.processing_finished_at = timezone.now()
        version.save(update_fields=["processing_finished_at"])
    AuditLog.record(C.AuditAction.PROCESS_FAILED, target=doc,
                    details={"code": code, "ref": ref, "error": str(exc)[:300] if exc else user_message,
                             "signature": signature})
    if notify:
        Notification.raise_alert(code, user_message, severity=severity, details={"document": doc.pk, "ref": ref})
    elif exc is not None:
        window = SystemSetting.get("ERROR_ALERT_WINDOW_MINUTES")
        repeats = AuditLog.objects.filter(
            action=C.AuditAction.PROCESS_FAILED, details__signature=signature,
            created_at__gte=timezone.now() - timedelta(minutes=window)).count()
        if repeats >= SystemSetting.get("ERROR_ALERT_REPEAT_COUNT"):
            Notification.raise_alert("PIPELINE_ERRORS_REPEATED",
                                     f"The same processing error occurred {repeats} times: {signature}",
                                     severity=C.Severity.WARNING, details={"signature": signature})


def retry_document(doc, actor=None):
    """FAILED -> DETECTING; the status page then kicks the pipeline again."""
    version = doc.current_version
    transition(doc, C.DocStatus.DETECTING, actor=actor, message="Retrying", percent=5, detail="",
               extra={"error_ref": ""})
    version.processing_started_at = None
    version.processing_finished_at = None
    version.save(update_fields=["processing_started_at", "processing_finished_at"])


def is_stuck(doc):
    if doc.status not in C.PROCESSING_STATES:
        return False
    return timezone.now() - doc.updated_at > timedelta(seconds=SystemSetting.get("STUCK_AFTER_SECONDS"))


def recover_stuck(actor=None):
    cutoff = timezone.now() - timedelta(seconds=SystemSetting.get("STUCK_AFTER_SECONDS"))
    stuck = list(Document.objects.filter(status__in=[s.value for s in C.PROCESSING_STATES], updated_at__lt=cutoff))
    for doc in stuck:
        try:
            transition(doc, C.DocStatus.FAILED, actor=actor, message="Failed", percent=0,
                       detail="Processing was interrupted. Please retry.")
        except InvalidTransition:
            continue
    if stuck:
        AuditLog.record(C.AuditAction.STUCK_RECOVERED, user=actor, details={"count": len(stuck),
                                                                            "ids": [d.pk for d in stuck]})
    return len(stuck)


def reextract(doc, version, doc_type, actor=None):
    """Reviewer picked/changed the type: re-run extraction, stay in review."""
    transition(doc, C.DocStatus.EXTRACTING, actor=actor, message="Re-extracting fields", percent=88)
    Document.objects.filter(pk=doc.pk).update(doc_type=doc_type)
    doc.doc_type = doc_type
    lines = lines_from_layout(version.layout_json)
    return finish_extraction(doc, version, doc_type, version.classification_confidence or 0.0, lines,
                             version.overall_confidence or 0.0, auto_allowed=False, actor=actor)
