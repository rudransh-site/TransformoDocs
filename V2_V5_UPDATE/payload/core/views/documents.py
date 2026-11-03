import io
import logging
import os

import pymupdf
from django.contrib import messages
from django.core.paginator import Paginator
from django.http import Http404, HttpResponse, JsonResponse, FileResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST
from PIL import Image, ImageOps

from core import constants as C
from core.models import AuditLog, Document, DocumentType, ReviewTask, SystemSetting
from core.services import exporters, extensions, pipeline, presenters
from core.services.files import UploadError, kind_of
from core.services.permissions import forbidden_response, role_of, role_required, visible_documents
from core.services.search import search_documents, snippet
from core.services.stats import system_stats
from core.services.uploads import DuplicateUpload, handle_upload

logger = logging.getLogger("transformodocs.views")
USER_OR_ADMIN = (C.Role.USER, C.Role.ADMIN)
ALL_ROLES = (C.Role.USER, C.Role.REVIEWER, C.Role.ADMIN)


def get_doc_or_404(user, pk):
    try:
        return visible_documents(user).get(pk=pk)
    except Document.DoesNotExist:
        raise Http404("Document not found") from None


def can_modify(user, doc):
    return doc.owner_id == user.id or role_of(user) == C.Role.ADMIN


# ---------------------------------------------------------------- dashboard
@role_required()
def dashboard(request):
    role = role_of(request.user)
    ctx = {}
    if role == C.Role.ADMIN:
        from core.models import Notification
        ctx["stats"] = system_stats()
        ctx["alerts"] = Notification.objects.filter(is_read=False)[:5]
        ctx["recent_audit"] = AuditLog.objects.select_related("user")[:8]
        ctx["recent_docs"] = visible_documents(request.user)[:6]
    elif role == C.Role.REVIEWER:
        from core.services.reviews import pending_queryset
        pending = pending_queryset()
        ctx["pending_count"] = pending.count()
        ctx["oldest"] = pending.order_by("created_at").first()
        ctx["my_recent"] = (ReviewTask.objects.filter(reviewed_by=request.user)
                            .select_related("version__document").order_by("-decided_at")[:5])
    else:
        docs = Document.objects.filter(owner=request.user)
        counts = {s: 0 for s in ("total", "processing", "review", "verified", "problem")}
        for d in docs.values("status"):
            counts["total"] += 1
            st = d["status"]
            if st in C.PROCESSING_STATES:
                counts["processing"] += 1
            elif st == "PENDING_VERIFICATION":
                counts["review"] += 1
            elif st in ("VERIFIED", "APPROVED"):
                counts["verified"] += 1
            elif st in ("REJECTED", "FAILED", "BLOCKED"):
                counts["problem"] += 1
        ctx["counts"] = counts
        ctx["recent_docs"] = docs.select_related("doc_type", "current_version")[:5]
    return render(request, "dashboard.html", ctx)


# ------------------------------------------------------------------- upload
def _dup_context(exc):
    return {"existing": exc.existing, "can_force": exc.can_force, "can_retry": exc.can_retry}


@role_required(*USER_OR_ADMIN)
def upload_page(request):
    from django.conf import settings
    ctx = {"max_mb": settings.MAX_UPLOAD_BYTES // (1024 * 1024), "max_pages": settings.MAX_PAGES,
           "extensions": ", ".join(e.upper().lstrip(".") for e in settings.ALLOWED_EXTENSIONS),
           "accept": ",".join(settings.ALLOWED_EXTENSIONS),
           "upload_cfg": {"apiUrl": "/api/documents/", "loginUrl": reverse("login"),
                          "maxBytes": settings.MAX_UPLOAD_BYTES, "maxMb": settings.MAX_UPLOAD_BYTES // (1024 * 1024),
                          "extensions": list(settings.ALLOWED_EXTENSIONS)}}
    ctx.update(extensions.upload_context())
    if request.method == "POST":  # no-JavaScript fallback
        f = request.FILES.get("file")
        if not f:
            messages.error(request, "Please choose a file to upload.")
            return render(request, "upload.html", ctx)
        try:
            doc = handle_upload(request.user, f, request=request, force=request.POST.get("force") == "1")
            extensions.save_upload_options(doc, request.POST)  # V5: OCR language / handwriting
            return redirect("document_status", pk=doc.pk)
        except UploadError as exc:
            messages.error(request, exc.message)
        except DuplicateUpload as exc:
            ctx["duplicate"] = _dup_context(exc)
    return render(request, "upload.html", ctx)


# ------------------------------------------------------------ list / detail
@role_required()
def document_list(request):
    qs = visible_documents(request.user).exclude(status="ARCHIVED") if request.GET.get("archived") != "1" \
        else visible_documents(request.user)
    status, type_code = request.GET.get("status", ""), request.GET.get("type", "")
    if status:
        qs = qs.filter(status=status)
    if type_code:
        qs = qs.filter(doc_type__code=type_code)
    page = Paginator(qs.order_by("-created_at"), 15).get_page(request.GET.get("page"))
    return render(request, "documents/list.html", {
        "page": page, "statuses": C.DocStatus.choices, "types": DocumentType.objects.filter(is_active=True),
        "f_status": status, "f_type": type_code, "f_archived": request.GET.get("archived") == "1"})


@role_required()
def document_detail(request, pk):
    doc = get_doc_or_404(request.user, pk)
    if doc.status in C.PROCESSING_STATES:
        return redirect("document_status", pk=doc.pk)
    v = doc.current_version
    task = ReviewTask.objects.filter(version=v).order_by("-id").first() if v else None
    reasons = presenters.friendly_reasons(task.reasons if task else [], doc.doc_type)
    pages = (v.layout_json or {}).get("pages", []) if v else []
    kind = kind_of(v.original_filename) if v else ""
    payload = exporters.build_payload(doc)
    payload["text"] = (payload["text"][:300] + " ...") if len(payload["text"]) > 300 else payload["text"]
    ctx = {
        "doc": doc, "v": v, "rows": presenters.field_rows(doc), "task": task, "reasons": reasons,
        "pages": pages, "page_numbers": range(1, (v.page_count if v else 0) + 1),
        "has_preview": kind in ("pdf", "image"), "can_modify": can_modify(request.user, doc),
        "json_preview": exporters.to_json(payload), "formats": exporters.FORMATS,
        "can_archive": doc.status in ("VERIFIED", "APPROVED", "REJECTED", "FAILED", "BLOCKED"),
        "threshold": SystemSetting.get("AUTO_VERIFY_MIN_CONFIDENCE"),
    }
    ctx.update(extensions.detail_context(request, doc))  # V2 tabs + chat panel
    return render(request, "documents/detail.html", ctx)


@role_required(*ALL_ROLES)
def document_status(request, pk):
    doc = get_doc_or_404(request.user, pk)
    if doc.status not in C.PROCESSING_STATES:
        return redirect("document_detail", pk=doc.pk)
    return render(request, "status.html", {"doc": doc, "can_process": can_modify(request.user, doc)})


@role_required()
@require_POST
def document_retry(request, pk):
    doc = get_doc_or_404(request.user, pk)
    if not can_modify(request.user, doc):
        return forbidden_response(request)
    if doc.status != C.DocStatus.FAILED:
        messages.warning(request, "Only failed documents can be retried.")
        return redirect("document_detail", pk=doc.pk)
    pipeline.retry_document(doc, actor=request.user)
    return redirect("document_status", pk=doc.pk)


@role_required()
@require_POST
def document_archive(request, pk):
    doc = get_doc_or_404(request.user, pk)
    if not can_modify(request.user, doc):
        return forbidden_response(request)
    from django.utils import timezone
    try:
        pipeline.transition(doc, C.DocStatus.ARCHIVED, actor=request.user, message="Archived", audit=False,
                            extra={"archived_at": timezone.now()})
    except pipeline.InvalidTransition:
        messages.warning(request, "This document cannot be archived in its current state.")
        return redirect("document_detail", pk=doc.pk)
    AuditLog.record(C.AuditAction.ARCHIVE, user=request.user, request=request, target=doc)
    messages.success(request, "Document archived.")
    return redirect("document_list")


# ---------------------------------------------------------------- downloads
@role_required()
def document_download(request, pk, fmt):
    doc = get_doc_or_404(request.user, pk)
    if fmt not in exporters.FORMATS:
        raise Http404("Unknown format")
    if doc.status in C.PROCESSING_STATES or doc.status == "FAILED":
        messages.warning(request, "There is no output yet for this document.")
        return redirect("document_detail", pk=doc.pk)
    content, ctype, ext = exporters.render(doc, fmt)
    AuditLog.record(C.AuditAction.DOWNLOAD, user=request.user, request=request, target=doc, details={"format": fmt})
    stem = os.path.splitext(doc.display_name)[0] or f"document_{doc.pk}"
    resp = HttpResponse(content, content_type=f"{ctype}; charset=utf-8")
    resp["Content-Disposition"] = f'attachment; filename="{stem}.{ext}"'
    resp["X-Content-Type-Options"] = "nosniff"
    return resp


@role_required()
def document_file(request, pk):
    doc = get_doc_or_404(request.user, pk)
    v = doc.current_version
    if not v or not v.file or not os.path.exists(v.file.path):
        raise Http404("File not found")
    AuditLog.record(C.AuditAction.DOWNLOAD, user=request.user, request=request, target=doc, details={"format": "original"})
    resp = FileResponse(open(v.file.path, "rb"), as_attachment=True, filename=v.original_filename,
                        content_type=v.mime_type or "application/octet-stream")
    resp["X-Content-Type-Options"] = "nosniff"
    return resp


@role_required()
def document_page_image(request, pk, n):
    doc = get_doc_or_404(request.user, pk)
    v = doc.current_version
    if not v or not os.path.exists(v.file.path):
        raise Http404("File not found")
    kind = kind_of(v.original_filename)
    try:
        if kind == "pdf":
            pdf = pymupdf.open(v.file.path)
            try:
                if n < 1 or n > pdf.page_count:
                    raise Http404("No such page")
                data = pdf.load_page(n - 1).get_pixmap(dpi=110).tobytes("png")
            finally:
                pdf.close()
        elif kind == "image" and n == 1:
            img = ImageOps.exif_transpose(Image.open(v.file.path)).convert("RGB")
            img.thumbnail((1400, 1400))
            buf = io.BytesIO()
            img.save(buf, "PNG")
            data = buf.getvalue()
        else:
            raise Http404("No preview")
    except Http404:
        raise
    except Exception:  # noqa: BLE001
        logger.exception("Preview failed for document %s", pk)
        raise Http404("Preview unavailable") from None
    resp = HttpResponse(data, content_type="image/png")
    resp["Cache-Control"] = "private, max-age=300"
    return resp


# ------------------------------------------------------------------- search
@role_required()
def search_view(request):
    q = request.GET.get("q", "").strip()
    type_code, status = request.GET.get("type", ""), request.GET.get("status", "")
    archived = request.GET.get("archived") == "1"
    tag = request.GET.get("tag", "").strip()
    mode = request.GET.get("mode", "keyword")
    semantic_ok = extensions.enabled("intelligence")
    results, backend = None, ""
    if q or type_code or status or tag:
        if mode == "semantic" and semantic_ok and q:
            from intelligence.services.semantic import semantic_search
            docs, backend = semantic_search(request.user, q, type_code, status, archived)
            results = Paginator(docs, 10).get_page(request.GET.get("page"))
        else:
            mode = "keyword"
            qs = search_documents(request.user, q, type_code, status, archived)
            if tag and semantic_ok:
                qs = qs.filter(tags__tag__slug=tag)
            results = Paginator(qs, 10).get_page(request.GET.get("page"))
            for d in results:
                d.snip = snippet(d.current_version.extracted_text if d.current_version else "", q)
    return render(request, "search.html", {
        "q": q, "results": results, "types": DocumentType.objects.filter(is_active=True), "f_type": type_code,
        "f_status": status, "statuses": C.DocStatus.choices, "f_archived": archived, "mode": mode,
        "semantic_ok": semantic_ok, "backend": backend, "f_tag": tag})
