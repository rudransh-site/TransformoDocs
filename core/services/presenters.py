"""Turn models into dicts/rows for templates and the API."""
from django.urls import reverse

from core import constants as C
from core.models import SystemSetting

from . import pipeline

REASON_LABELS = {
    "LOW_QUALITY": "The scan quality is low.",
    "LOW_CONFIDENCE": "Text recognition confidence is below the auto-verify threshold.",
    "UNKNOWN_TYPE": "The document type could not be recognised.",
    "HANDWRITTEN": "Handwritten document: please check every recognised field against the page.",
}

STATUS_BADGE = {
    "UPLOADED": "secondary", "DETECTING": "info", "OCR_PROCESSING": "info", "CLASSIFYING": "info",
    "EXTRACTING": "info", "PENDING_VERIFICATION": "warning", "VERIFIED": "success", "APPROVED": "success",
    "IN_APPROVAL": "primary", "REJECTED": "danger", "FAILED": "danger", "BLOCKED": "dark", "ARCHIVED": "secondary",
}


def friendly_reasons(reasons, doc_type=None):
    labels = {f["key"]: f.get("label", f["key"]) for f in (doc_type.field_specs() if doc_type else [])}
    out = []
    for r in reasons or []:
        if r.startswith("MISSING:"):
            key = r.split(":", 1)[1]
            out.append(f"Required field missing: {labels.get(key, key)}.")
        else:
            out.append(REASON_LABELS.get(r, r))
    return out


def field_rows(doc, threshold=None):
    """Rows for the 'Extracted data' table, in schema order."""
    v = doc.current_version
    if not v or not doc.doc_type:
        return []
    threshold = SystemSetting.get("AUTO_VERIFY_MIN_CONFIDENCE") if threshold is None else threshold
    data, confs, rows = v.structured_data or {}, v.field_confidences or {}, []
    for spec in doc.doc_type.field_specs():
        key = spec["key"]
        value = data.get(key)
        conf = confs.get(key)
        rows.append({
            "key": key, "label": spec.get("label", key), "type": spec["type"], "required": bool(spec.get("required")),
            "value": value, "display": "\n".join(value) if isinstance(value, list) else (value or ""),
            "is_list": isinstance(value, list), "conf": conf, "missing": value in (None, "", []),
            "low": conf is not None and conf < threshold,
        })
    return rows


def _steps(doc):
    v = doc.current_version
    uses_ocr = (v and v.detection_method in ("OCR", "MIXED")) or doc.status == "OCR_PROCESSING"
    order = [("UPLOADED", "File received"), ("DETECTING", "Checking readability")]
    if uses_ocr:
        order.append(("OCR_PROCESSING", "Reading text (OCR)"))
    order += [("CLASSIFYING", "Identifying type"), ("EXTRACTING", "Extracting fields")]
    keys = [k for k, _ in order]
    terminal = doc.status not in C.PROCESSING_STATES
    current = keys.index(doc.status) if doc.status in keys else len(keys)
    steps = []
    for i, (key, label) in enumerate(order):
        if doc.status == "FAILED" and i == current:
            state = "failed"
        elif terminal and doc.status != "FAILED":
            state = "done"
        elif i < current:
            state = "done"
        elif i == current:
            state = "active"
        else:
            state = "pending"
        steps.append({"key": key, "label": label, "state": state})
    return steps


def status_payload(doc):
    terminal = doc.status not in C.PROCESSING_STATES
    return {
        "id": doc.pk, "status": doc.status, "status_label": doc.get_status_display(),
        "progress_percent": doc.progress_percent if not terminal else (100 if doc.status != "FAILED" else 0),
        "progress_message": doc.progress_message, "is_terminal": terminal,
        "is_stuck": pipeline.is_stuck(doc), "detail": doc.status_detail, "error_ref": doc.error_ref,
        "redirect_url": reverse("document_detail", args=[doc.pk]), "steps": _steps(doc),
    }


def document_to_dict(doc):
    v = doc.current_version
    return {
        "id": doc.pk, "filename": doc.display_name, "status": doc.status, "status_label": doc.get_status_display(),
        "type": doc.doc_type.code if doc.doc_type else None,
        "type_name": doc.doc_type.name if doc.doc_type else None,
        "overall_confidence": v.overall_confidence if v else None,
        "classification_confidence": v.classification_confidence if v else None,
        "detection_method": v.detection_method if v else None,
        "pages": v.page_count if v else None,
        "verification_method": doc.verification_method or None,
        "fields": (v.structured_data if v else {}) or {},
        "field_confidences": (v.field_confidences if v else {}) or {},
        "owner": doc.owner.get_username(), "created_at": doc.created_at.isoformat(),
        "status_url": reverse("document_status", args=[doc.pk]),
        "detail_url": reverse("document_detail", args=[doc.pk]),
    }
