"""V5 document generator screens and endpoints."""
import json
import logging

from django.contrib import messages
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from core import constants as C
from core.models import AuditLog, Document
from core.services.files import UploadError
from core.services.permissions import role_required, visible_documents
from core.services.uploads import DuplicateUpload, handle_upload

from .models import GeneratedDocument
from .services import generator as G

logger = logging.getLogger("transformodocs.v5")
ALLOWED = (C.Role.USER, C.Role.ADMIN)


def _post_data(request):
    """Form POST -> dict (single values; list values for the repeated item rows)."""
    data = {k: v for k, v in request.POST.items() if k not in ("csrfmiddlewaretoken",)}
    for key in ("item_desc", "item_qty", "item_price"):
        data[key] = request.POST.getlist(key)
    return data


def _form_context(template, initial):
    base = G.defaults(template)
    base.update({k: v for k, v in (initial or {}).items() if v not in (None, "")})
    if template == "invoice" and not base.get("items"):
        base["items"] = G.defaults("invoice")["items"]
    return base


@role_required(*ALLOWED)
def generator(request):
    template = request.GET.get("template", "")
    ctx = {"templates": G.TEMPLATES, "template": template if template in G.TEMPLATES else "",
           "cert_types": G.CERT_TYPES}
    if ctx["template"]:
        initial = None
        from_id = request.GET.get("from", "")
        if from_id.isdigit():
            past = GeneratedDocument.objects.filter(owner=request.user, pk=int(from_id), template=template).first()
            initial = past.data if past else None
        ctx["form"] = _form_context(template, initial)
        sources = visible_documents(request.user).filter(
            owner=request.user, status__in=["VERIFIED", "APPROVED"], doc_type__code__in=G.PREFILL_SOURCES[template])
        ctx["sources"] = sources.select_related("current_version", "doc_type")[:30]
    ctx["history"] = GeneratedDocument.objects.filter(owner=request.user)[:8]
    return render(request, "advanced/generator.html", ctx)


@role_required(*ALLOWED)
@require_POST
def preview(request):
    template = request.POST.get("template", "")
    try:
        cleaned = G.clean(template, _post_data(request))
        html = G.render_html(template, cleaned)
    except G.GenError as exc:
        return JsonResponse({"ok": False, "errors": exc.errors})
    totals = {}
    if template == "invoice":
        totals = {"subtotal": f"{cleaned['subtotal']:,.2f}", "tax": f"{cleaned['tax']:,.2f}",
                  "total": f"{cleaned['total']:,.2f}", "words": cleaned["words"]}
    return JsonResponse({"ok": True, "html": html, "totals": totals})


def _record(request, template, cleaned, saved=None):
    title = (f"Invoice {cleaned['invoice_number']} for {cleaned['customer_name']}" if template == "invoice"
             else f"Certificate for {cleaned['recipient_name']}")
    return GeneratedDocument.objects.create(owner=request.user, template=template, title=title[:150],
                                            data=G.serialisable(template, cleaned), saved_document=saved)


@role_required(*ALLOWED)
@require_POST
def export(request, fmt):
    template = request.POST.get("template", "")
    if fmt not in ("pdf", "docx"):
        raise Http404("Unknown format")
    try:
        cleaned = G.clean(template, _post_data(request))
        content = G.to_pdf(template, cleaned) if fmt == "pdf" else G.to_docx(template, cleaned)
    except G.GenError:
        messages.error(request, "Please fix the highlighted fields before exporting.")
        return redirect(f"/generator/?template={template}")
    except Exception:  # noqa: BLE001
        logger.exception("Document generation failed")
        messages.error(request, "Could not generate the document. Please try again.")
        return redirect(f"/generator/?template={template}")
    _record(request, template, cleaned)
    ctype = "application/pdf" if fmt == "pdf" else \
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    resp = HttpResponse(content, content_type=ctype)
    resp["Content-Disposition"] = f'attachment; filename="{G.filename(template, cleaned, fmt)}"'
    resp["X-Content-Type-Options"] = "nosniff"
    return resp


@role_required(*ALLOWED)
@require_POST
def save_to_system(request):
    """Generate the PDF and run it through the normal upload + processing pipeline."""
    template = request.POST.get("template", "")
    try:
        cleaned = G.clean(template, _post_data(request))
        pdf = G.to_pdf(template, cleaned)
    except G.GenError:
        messages.error(request, "Please fix the highlighted fields before saving.")
        return redirect(f"/generator/?template={template}")
    upload = SimpleUploadedFile(G.filename(template, cleaned, "pdf"), pdf, content_type="application/pdf")
    try:
        doc = handle_upload(request.user, upload, request=request)
    except DuplicateUpload as exc:
        messages.warning(request, "An identical document is already in your library.")
        return redirect("document_detail", pk=exc.existing.pk)
    except UploadError as exc:
        messages.error(request, exc.message)
        return redirect(f"/generator/?template={template}")
    _record(request, template, cleaned, saved=doc)
    messages.success(request, "Saved. It is now being processed like any uploaded document.")
    return redirect("document_status", pk=doc.pk)


@role_required(*ALLOWED)
def prefill(request, doc_id):
    template = request.GET.get("template", "")
    if template not in G.TEMPLATES:
        return JsonResponse({"error": "Unknown template."}, status=400)
    try:
        doc = visible_documents(request.user).get(pk=doc_id, owner=request.user)
    except Document.DoesNotExist:
        raise Http404("Document not found") from None
    data = G.prefill_from(template, doc.current_version.structured_data if doc.current_version else {})
    return JsonResponse({"fields": data})
