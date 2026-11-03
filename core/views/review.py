from django.contrib import messages
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from core import constants as C
from core.models import AuditLog, DocumentType, ReviewTask
from core.services import presenters, reviews
from core.services.files import kind_of
from core.services.permissions import role_required

REVIEWERS = (C.Role.REVIEWER, C.Role.ADMIN)


@role_required(*REVIEWERS)
def queue(request):
    page = Paginator(reviews.pending_queryset(), 15).get_page(request.GET.get("page"))
    for t in page:
        t.friendly = presenters.friendly_reasons(t.reasons, t.version.document.doc_type)
        t.locked_now = t.is_lock_active()
    return render(request, "review/queue.html", {"page": page})


@role_required(*REVIEWERS)
def history(request):
    qs = (ReviewTask.objects.filter(reviewed_by=request.user).select_related("version__document")
          .order_by("-decided_at"))
    return render(request, "review/history.html", {"page": Paginator(qs, 20).get_page(request.GET.get("page"))})


def _context(task, doc, can_edit, errors=None, posted=None):
    v = task.version
    rows = presenters.field_rows(doc, reviews.low_conf_threshold())
    posted = posted or {}
    for r in rows:
        if r["key"] in posted:
            r["display"] = posted[r["key"]]
        r["error"] = (errors or {}).get(r["key"])
    kind = kind_of(v.original_filename)
    return {"task": task, "doc": doc, "v": v, "rows": rows, "can_edit": can_edit,
            "errors": errors or {}, "general_error": (errors or {}).get("__all__"),
            "reasons": presenters.friendly_reasons(task.reasons, doc.doc_type),
            "types": DocumentType.objects.filter(is_active=True), "has_preview": kind in ("pdf", "image"),
            "page_numbers": range(1, (v.page_count or 1) + 1), "comment": posted.get("comment", "")}


@role_required(*REVIEWERS)
@require_http_methods(["GET", "POST"])
def review(request, task_id):
    task_obj = get_object_or_404(ReviewTask, pk=task_id)
    doc = task_obj.version.document

    def conflict(exc):
        AuditLog.record(C.AuditAction.REVIEW_CONFLICT, user=request.user, request=request, target=doc,
                        details={"task": task_id, "message": exc.message})
        messages.warning(request, exc.message)
        return redirect("document_detail", pk=doc.pk) if exc.already_decided else redirect("review_queue")

    if request.method == "GET":
        try:
            task, can_edit = reviews.claim(task_id, request.user)
        except reviews.ReviewConflict as exc:
            return conflict(exc)
        if not can_edit:
            messages.warning(request, f"This document is currently being reviewed by {task.locked_by.get_username()}. "
                                      "You can look, but not edit.")
        doc = task.version.document
        return render(request, "review/review.html", _context(task, doc, can_edit))

    action = request.POST.get("action", "")
    posted = {k[2:]: v for k, v in request.POST.items() if k.startswith("f_")}
    posted["comment"] = request.POST.get("comment", "")
    try:
        if action == "approve":
            reviews.approve(task_id, request.user, posted, comment=posted["comment"])
            messages.success(request, "Document verified.")
            return redirect("review_queue")
        if action == "reject":
            reviews.reject(task_id, request.user, posted["comment"])
            messages.success(request, "Document rejected.")
            return redirect("review_queue")
        if action == "change_type":
            reviews.change_type(task_id, request.user, request.POST.get("doc_type"))
            messages.success(request, "Fields were re-extracted for the selected type.")
            return redirect("review", task_id=task_id)
        messages.error(request, "Unknown action.")
        return redirect("review", task_id=task_id)
    except reviews.ReviewConflict as exc:
        return conflict(exc)
    except reviews.ReviewValidationError as exc:
        task = ReviewTask.objects.select_related("version__document__doc_type", "locked_by").get(pk=task_id)
        return render(request, "review/review.html",
                      _context(task, task.version.document, True, errors=exc.errors, posted=posted), status=400)
