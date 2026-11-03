"""Machine-readable output: JSON, XML, CSV."""
import csv
import io
import json
import xml.etree.ElementTree as ET

from django.utils import timezone

FORMATS = ("json", "xml", "csv")


def build_payload(doc):
    v = doc.current_version
    return {
        "document": {
            "id": doc.pk,
            "filename": v.original_filename if v else "",
            "type": doc.doc_type.code if doc.doc_type else "UNKNOWN",
            "status": doc.status,
            "verified": doc.status in ("VERIFIED", "APPROVED"),
            "verification_method": doc.verification_method or None,
            "machine_readable_original": v.is_machine_readable if v else None,
            "extraction_method": v.detection_method if v else None,
            "overall_confidence": v.overall_confidence if v else None,
            "classification_confidence": v.classification_confidence if v else None,
            "pages": v.page_count if v else None,
            "language": v.language if v else None,
            "version": v.version_number if v else None,
            "exported_at": timezone.now().isoformat(timespec="seconds"),
        },
        "fields": (v.structured_data if v else {}) or {},
        "field_confidences": (v.field_confidences if v else {}) or {},
        "text": v.extracted_text if v else "",
    }


def to_json(payload):
    return json.dumps(payload, indent=2, ensure_ascii=False)


def _add(parent, key, value):
    node = ET.SubElement(parent, key)
    if isinstance(value, dict):
        for k, v in value.items():
            _add(node, k if (k and (k[0].isalpha() or k[0] == "_")) else f"f_{k}", v)
    elif isinstance(value, list):
        for item in value:
            _add(node, "item", item)
    elif value is not None:
        node.text = str(value)
    return node


def to_xml(payload):
    root = ET.Element("transformodocs")
    for key, value in payload.items():
        _add(root, key, value)
    ET.indent(root)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode")


def to_csv(payload):
    out = io.StringIO()
    meta = payload["document"]
    fields = payload["fields"]
    row = {f"doc_{k}": v for k, v in meta.items()}
    for k, v in fields.items():
        row[k] = "; ".join(map(str, v)) if isinstance(v, list) else v
    writer = csv.DictWriter(out, fieldnames=list(row.keys()))
    writer.writeheader()
    writer.writerow(row)
    return out.getvalue()


def render(doc, fmt):
    """Return (content, content_type, extension)."""
    payload = build_payload(doc)
    if fmt == "json":
        return to_json(payload), "application/json", "json"
    if fmt == "xml":
        return to_xml(payload), "application/xml", "xml"
    if fmt == "csv":
        return to_csv(payload), "text/csv", "csv"
    raise ValueError("Unsupported format")
