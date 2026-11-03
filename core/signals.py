from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver

from .constants import Role
from .models import Profile


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_profile(sender, instance, created, **kwargs):
    """Every new account gets a Profile. Self-registered = USER; a superuser
    (createsuperuser) starts as ADMIN. Admins create Reviewers/Admins later."""
    if created:
        role = Role.ADMIN if instance.is_superuser else Role.USER
        Profile.objects.get_or_create(user=instance, defaults={"role": role})


# ---- audit: login / logout / failed login
from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed  # noqa: E402

from .constants import AuditAction  # noqa: E402
from .models import AuditLog  # noqa: E402


@receiver(user_logged_in)
def audit_login(sender, request, user, **kwargs):
    AuditLog.record(AuditAction.LOGIN, user=user, request=request)


@receiver(user_logged_out)
def audit_logout(sender, request, user, **kwargs):
    if user is not None:
        AuditLog.record(AuditAction.LOGOUT, user=user, request=request)


@receiver(user_login_failed)
def audit_login_failed(sender, credentials, request=None, **kwargs):
    AuditLog.record(AuditAction.LOGIN_FAILED, request=request,
                    details={"username": (credentials or {}).get("username", "")})
