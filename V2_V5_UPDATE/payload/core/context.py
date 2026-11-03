from django.apps import apps
from django.db import DatabaseError

from . import constants as C
from .models import Notification, ReviewTask
from .services.permissions import role_of


def app_context(request):
    ctx = {"app_name": "TransformoDocs", "v5_enabled": apps.is_installed("advanced"), "role": None, "is_admin": False, "is_reviewer": False, "is_plain_user": False}
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        try:
            role = role_of(user)
            ctx.update(role=role, is_admin=role == C.Role.ADMIN, is_reviewer=role == C.Role.REVIEWER,
                       is_plain_user=role == C.Role.USER)
            if role == C.Role.ADMIN:
                ctx["unread_alerts"] = Notification.objects.filter(is_read=False).count()
            if role in (C.Role.ADMIN, C.Role.REVIEWER):
                ctx["pending_reviews"] = ReviewTask.objects.filter(
                    decision=C.ReviewDecision.PENDING, version__document__status=C.DocStatus.PENDING_VERIFICATION).count()
        except DatabaseError:
            pass
    return ctx
