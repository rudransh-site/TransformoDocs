"""REST API (session auth, used by our own frontend)."""
import logging
import uuid

from django.core.paginator import EmptyPage, Paginator
from django.http import Http404, HttpResponse
from rest_framework import status as http
from rest_framework.exceptions import NotAuthenticated, PermissionDenied
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView, exception_handler as drf_handler

from core import constants as C
from core.models import AuditLog, Document
from core.services import exporters, extensions, pipeline, presenters, reviews
from core.services.files import UploadError
from core.services.permissions import role_of, visible_documents
from core.services.search import search_documents, snippet_plain
from core.services.stats import system_stats
from core.services.uploads import DuplicateUpload, handle_upload

logger = logging.getLogger("transformodocs.api")


def api_exception_handler(exc, context):
    """Every API error looks like {"error", "code", ...}."""
    if isinstance(exc, NotAuthenticated):
        return Response({"error": "Your session has expired. Please log in again.", "code": "AUTH_REQUIRED"},
                        status=http.HTTP_401_UNAUTHORIZED)
    if isinstance(exc, PermissionDenied):
        text = str(exc.detail)
        code = "CSRF_FAILED" if "csrf" in text.lower() else "FORBIDDEN"
        msg = "Your session has expired. Please refresh and log in again." if code == "CSRF_FAILED" else text
        return Response({"error": msg, "code": code}, status=http.HTTP_403_FORBIDDEN)
    if isinstance(exc, Http404):
        return Response({"error": "Not found.", "code": "NOT_FOUND"}, status=http.HTTP_404_NOT_FOUND)
    resp = drf_handler(exc, context)
    if resp is not None:
        detail = resp.data.get("detail") if isinstance(resp.data, dict) else None
        resp.data = {"error": str(detail or "Request failed."), "code": "BAD_REQUEST"}
        return resp
    ref = uuid.uuid4().hex[:6].upper()
    logger.exception("Unhandled API error ref=%s", ref)
    return Response({"error": f"Something went wrong (ref #{ref}).", "code": "SERVER_ERROR", "ref": ref},
                    status=http.HTTP_500_INTERNAL_SERVER_ERROR)


class HasRole(BasePermission):
    roles = ()

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and role_of(request.user) in self.roles)


class UserOrAdmin(HasRole):
    roles = (C.Role.USER, C.Role.ADMIN)


class ReviewerOrAdmin(HasRole):
    roles = (C.Role.REVIEWER, C.Role.ADMIN)


class AdminOnly(HasRole):
    roles = (C.Role.ADMIN,)


def _doc(request, pk):
    try:
        return visible_documents(request.user).get(pk=pk)
    except Document.DoesNotExist:
        raise Http404 from None


def _can_modify(user, doc):
    return doc.owner_id == user.id or role_of(user) == C.Role.ADMIN


def _paginate(request, qs, default=20):
    try:
        size = max(1, min(int(request.GET.get("page_size", default)), 100))
        number = int(request.GET.get("page", 1))
    except ValueError:
        size, number = default, 1
    paginator = Paginator(qs, size)
    try:
        page = paginator.page(number)
    except EmptyPage:
        page = paginator.page(paginator.num_pages)
    return paginator, page


class DocumentList(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = visible_documents(request.user)
        if request.GET.get("status"):
            qs = qs.filter(status=request.GET["status"])
        if request.GET.get("type"):
            qs = qs.filter(doc_type__code=request.GET["type"])
        paginator, page = _paginate(request, qs.order_by("-created_at"))
        return Response({"count": paginator.count, "page": page.number, "pages": paginator.num_pages,
                         "results": [presenters.document_to_dict(d) for d in page]})

    def post(self, request):
        if role_of(request.user) not in (C.Role.USER, C.Role.ADMIN):
            raise PermissionDenied("Only users can upload documents.")
        f = request.FILES.get("file")
        if not f:
            return Response({"error": "No file was provided.", "code": "NO_FILE"}, status=400)
        try:
            doc = handle_upload(request.user, f, request=request, force=request.POST.get("force") == "1")
        except UploadError as exc:
            code = 413 if exc.code == "FILE_TOO_LARGE" else 500 if exc.code == "STORAGE_ERROR" else 400
            return Response({"error": exc.message, "code": exc.code}, status=code)
        except DuplicateUpload as exc:
            e = exc.existing
            return Response({
                "error": f"You already uploaded this file on {e.created_at:%d %b %Y} (status: {e.get_status_display()}).",
                "code": "DUPLICATE", "can_force": exc.can_force, "can_retry": exc.can_retry,
                "existing": presenters.document_to_dict(e)}, status=409)
        extensions.save_upload_options(doc, request.POST)  # V5: OCR language / handwriting
        return Response(presenters.document_to_dict(doc), status=201)


class DocumentDetail(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        return Response(presenters.document_to_dict(_doc(request, pk)))


class DocumentStatus(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        return Response(presenters.status_payload(_doc(request, pk)))


class DocumentProcess(APIView):
    """Runs the pipeline synchronously for this document, then returns its status."""
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        doc = _doc(request, pk)
        if not _can_modify(request.user, doc):
            raise PermissionDenied("You cannot process this document.")
        doc = pipeline.run_pipeline(doc.pk)
        return Response(presenters.status_payload(doc))


class DocumentOutput(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        doc = _doc(request, pk)
        fmt = request.GET.get("format", "json").lower()
        if fmt not in exporters.FORMATS:
            return Response({"error": "format must be json, xml or csv.", "code": "BAD_FORMAT"}, status=400)
        if doc.status in C.PROCESSING_STATES or doc.status == "FAILED":
            return Response({"error": "This document has no output yet.", "code": "NO_OUTPUT"}, status=409)
        content, ctype, _ext = exporters.render(doc, fmt)
        AuditLog.record(C.AuditAction.DOWNLOAD, user=request.user, request=request, target=doc,
                        details={"format": fmt, "via": "api"})
        return HttpResponse(content, content_type=f"{ctype}; charset=utf-8")


class Search(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        q = request.GET.get("q", "").strip()
        if request.GET.get("mode") == "semantic" and extensions.enabled("intelligence") and q:
            from intelligence.services.semantic import semantic_search
            docs, backend = semantic_search(request.user, q, request.GET.get("type", ""),
                                            request.GET.get("status", ""), request.GET.get("archived") == "1")
            paginator, page = _paginate(request, docs, default=10)
            results = []
            for d in page:
                row = presenters.document_to_dict(d)
                row["snippet"], row["score"] = d.snip_plain, round(d.score, 3)
                results.append(row)
            return Response({"count": paginator.count, "page": page.number, "pages": paginator.num_pages,
                             "mode": "semantic", "backend": backend, "results": results})
        qs = search_documents(request.user, q, request.GET.get("type", ""), request.GET.get("status", ""),
                              request.GET.get("archived") == "1")
        paginator, page = _paginate(request, qs, default=10)
        results = []
        for d in page:
            row = presenters.document_to_dict(d)
            row["snippet"] = snippet_plain(d.current_version.extracted_text if d.current_version else "", q)
            results.append(row)
        return Response({"count": paginator.count, "page": page.number, "pages": paginator.num_pages,
                         "results": results})


def _review_error(exc):
    if isinstance(exc, reviews.ReviewConflict):
        return Response({"error": exc.message, "code": "REVIEW_CONFLICT", "already_decided": exc.already_decided},
                        status=409)
    return Response({"error": "Please fix the highlighted fields.", "code": "VALIDATION", "fields": exc.errors},
                    status=400)


class ReviewApprove(APIView):
    permission_classes = [ReviewerOrAdmin]

    def post(self, request, task_id):
        data = request.data if isinstance(request.data, dict) else {}
        try:
            reviews.approve(task_id, request.user, data.get("fields") or {}, comment=data.get("comment", ""))
        except (reviews.ReviewConflict, reviews.ReviewValidationError) as exc:
            return _review_error(exc)
        return Response({"ok": True})


class ReviewReject(APIView):
    permission_classes = [ReviewerOrAdmin]

    def post(self, request, task_id):
        data = request.data if isinstance(request.data, dict) else {}
        try:
            reviews.reject(task_id, request.user, data.get("comment", ""))
        except (reviews.ReviewConflict, reviews.ReviewValidationError) as exc:
            return _review_error(exc)
        return Response({"ok": True})


class AdminStats(APIView):
    permission_classes = [AdminOnly]

    def get(self, request):
        return Response(system_stats())


class AdminAudit(APIView):
    permission_classes = [AdminOnly]

    def get(self, request):
        qs = AuditLog.objects.select_related("user")
        if request.GET.get("action"):
            qs = qs.filter(action=request.GET["action"])
        if request.GET.get("user"):
            qs = qs.filter(username__icontains=request.GET["user"])
        paginator, page = _paginate(request, qs, default=50)
        return Response({"count": paginator.count, "page": page.number, "pages": paginator.num_pages,
                         "results": [{"time": a.created_at.isoformat(), "user": a.username, "action": a.action,
                                      "target": f"{a.target_type}:{a.target_id}" if a.target_type else "",
                                      "details": a.details, "ip": a.ip_address} for a in page]})
