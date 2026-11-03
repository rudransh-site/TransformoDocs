"""Role checks and document visibility (RBAC)."""
from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse

from core import constants as C
from core.models import AuditLog, Document, Profile


def role_of(user):
    if not user.is_authenticated:
        return None
    try:
        return user.profile.role
    except Profile.DoesNotExist:
        role = C.Role.ADMIN if user.is_superuser else C.Role.USER
        return Profile.objects.get_or_create(user=user, defaults={"role": role})[0].role


def is_ajax(request):
    return (request.headers.get("x-requested-with") == "XMLHttpRequest"
            or "application/json" in request.headers.get("accept", "")
            or request.path.startswith("/api/"))


def auth_required_response(request):
    expired = settings.SESSION_COOKIE_NAME in request.COOKIES
    msg = "Your session has expired. Please log in again." if expired else "Please log in to continue."
    if expired:
        AuditLog.record(C.AuditAction.LOGIN_REQUIRED, request=request, details={"path": request.path})
    if is_ajax(request):
        return JsonResponse({"error": msg, "code": "AUTH_REQUIRED", "login_url": reverse("login")}, status=401)
    messages.warning(request, msg)
    return redirect(f"{reverse('login')}?next={request.get_full_path()}")


def forbidden_response(request, message="You do not have permission to view this page."):
    if is_ajax(request):
        return JsonResponse({"error": message, "code": "FORBIDDEN"}, status=403)
    return render(request, "errors/403.html", {"message": message}, status=403)


def role_required(*roles):
    """Login + role check for template views. No roles = any logged-in user."""

    def deco(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return auth_required_response(request)
            if roles and role_of(request.user) not in roles:
                return forbidden_response(request)
            return view(request, *args, **kwargs)

        return wrapper

    return deco


def visible_documents(user):
    role = role_of(user)
    qs = Document.objects.select_related("doc_type", "current_version", "owner")
    if role == C.Role.ADMIN:
        return qs
    if role == C.Role.REVIEWER:
        return qs.filter(versions__review_tasks__isnull=False).distinct()
    return qs.filter(owner=user)
