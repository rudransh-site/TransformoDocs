"""Admin screens: users/roles, audit log, stats, health, settings."""
from datetime import datetime, time, timedelta

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.paginator import Paginator
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST

from core import constants as C
from core.forms import AdminUserForm
from core.models import AuditLog, Notification, Profile, SystemSetting
from core.services import health, pipeline
from core.services.permissions import role_required
from core.services.stats import system_stats

User = get_user_model()
ADMIN = (C.Role.ADMIN,)


def _active_admins():
    return User.objects.filter(is_active=True, profile__role=C.Role.ADMIN)


@role_required(*ADMIN)
def users(request):
    qs = User.objects.select_related("profile").order_by("username")
    return render(request, "manage/users.html", {"users": qs, "roles": C.Role.choices})


@role_required(*ADMIN)
def user_new(request):
    form = AdminUserForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        with transaction.atomic():
            user = User.objects.create_user(d["username"], d["email"], d["password"])
            Profile.objects.update_or_create(user=user, defaults={"role": d["role"]})
        AuditLog.record(C.AuditAction.USER_CREATED, user=request.user, request=request, target=user,
                        details={"username": user.username, "role": d["role"]})
        messages.success(request, f"Account '{user.username}' created as {d['role'].title()}.")
        return redirect("manage_users")
    return render(request, "manage/user_new.html", {"form": form})


@role_required(*ADMIN)
@require_POST
def user_role(request, user_id):
    target = get_object_or_404(User.objects.select_related("profile"), pk=user_id)
    new_role = request.POST.get("role")
    if new_role not in C.Role.values:
        messages.error(request, "Invalid role.")
    elif target.pk == request.user.pk:
        messages.error(request, "You cannot change your own role.")
    else:
        old = target.profile.role
        if old == C.Role.ADMIN and new_role != C.Role.ADMIN and target.is_active and _active_admins().count() <= 1:
            messages.error(request, "At least one active Admin must remain.")
        elif old != new_role:
            target.profile.role = new_role
            target.profile.save(update_fields=["role"])
            AuditLog.record(C.AuditAction.ROLE_CHANGED, user=request.user, request=request, target=target,
                            details={"username": target.username, "from": old, "to": new_role})
            messages.success(request, f"{target.username} is now {new_role.title()}.")
    return redirect("manage_users")


@role_required(*ADMIN)
@require_POST
def user_toggle(request, user_id):
    target = get_object_or_404(User.objects.select_related("profile"), pk=user_id)
    if target.pk == request.user.pk:
        messages.error(request, "You cannot deactivate your own account.")
    elif target.is_active and target.profile.role == C.Role.ADMIN and _active_admins().count() <= 1:
        messages.error(request, "At least one active Admin must remain.")
    else:
        target.is_active = not target.is_active
        target.save(update_fields=["is_active"])
        AuditLog.record(C.AuditAction.USER_DEACTIVATED if not target.is_active else C.AuditAction.USER_REACTIVATED,
                        user=request.user, request=request, target=target, details={"username": target.username})
        messages.success(request, f"{target.username} is now {'active' if target.is_active else 'deactivated'}.")
    return redirect("manage_users")


@role_required(*ADMIN)
def audit_log(request):
    qs = AuditLog.objects.select_related("user")
    f = {k: request.GET.get(k, "").strip() for k in ("user", "action", "date_from", "date_to", "document")}
    if f["user"]:
        qs = qs.filter(username__icontains=f["user"])
    if f["action"]:
        qs = qs.filter(action=f["action"])
    if f["document"].isdigit():
        qs = qs.filter(target_type="Document", target_id=int(f["document"]))
    d1, d2 = parse_date(f["date_from"]) if f["date_from"] else None, parse_date(f["date_to"]) if f["date_to"] else None
    # Plain datetime ranges (not __date): MySQL's __date lookup returns nothing
    # when its time-zone tables are not loaded, which is common on Windows.
    tz = timezone.get_current_timezone()
    if d1:
        qs = qs.filter(created_at__gte=timezone.make_aware(datetime.combine(d1, time.min), tz))
    if d2:
        qs = qs.filter(created_at__lt=timezone.make_aware(datetime.combine(d2 + timedelta(days=1), time.min), tz))
    page = Paginator(qs, 40).get_page(request.GET.get("page"))
    qd = request.GET.copy()
    qd.pop("page", None)
    return render(request, "manage/audit.html", {"page": page, "f": f, "actions": C.AuditAction.choices,
                                                  "qs": qd.urlencode()})


@role_required(*ADMIN)
def stats(request):
    return render(request, "manage/stats.html", {"s": system_stats()})


@role_required(*ADMIN)
def system_health(request):
    checks = health.run_checks()
    for c in checks:
        if not c["ok"] and c["critical"]:
            Notification.raise_alert(f"HEALTH_{c['name'].upper().replace(' ', '_')}",
                                     f"{c['name']} check failed: {c['detail']}", severity=C.Severity.CRITICAL,
                                     dedupe_minutes=240)
    return render(request, "manage/health.html", {
        "checks": checks, "alerts": Notification.objects.all()[:30],
        "all_ok": all(c["ok"] or not c["critical"] for c in checks)})


@role_required(*ADMIN)
@require_POST
def alert_read(request, alert_id=None):
    qs = Notification.objects.filter(is_read=False)
    if alert_id:
        qs = qs.filter(pk=alert_id)
    qs.update(is_read=True)
    return redirect("manage_health")


@role_required(*ADMIN)
@require_POST
def recover_stuck(request):
    n = pipeline.recover_stuck(actor=request.user)
    messages.success(request, f"{n} stuck document(s) marked as failed so they can be retried." if n
                     else "No stuck documents found.")
    return redirect("manage_health")


@role_required(*ADMIN)
def settings_view(request):
    rows, errors = [], {}
    if request.method == "POST":
        changes = {}
        for key in C.SETTING_DEFINITIONS:
            raw = request.POST.get(f"s_{key}", "")
            try:
                value = C.validate_setting(key, raw)
            except ValueError as exc:
                errors[key] = str(exc)
                continue
            if value != SystemSetting.get(key):
                changes[key] = value
        if not errors:
            old = {k: SystemSetting.get(k) for k in changes}
            for key, value in changes.items():
                SystemSetting.set(key, value, user=request.user)
            if changes:
                AuditLog.record(C.AuditAction.SETTINGS_CHANGED, user=request.user, request=request,
                                details={"changes": {k: {"from": old[k], "to": v} for k, v in changes.items()}})
                messages.success(request, "Settings saved.")
            else:
                messages.info(request, "No changes.")
            return redirect("manage_settings")
    for key, spec in C.SETTING_DEFINITIONS.items():
        value = request.POST.get(f"s_{key}") if request.method == "POST" else SystemSetting.get(key)
        rows.append({"key": key, "spec": spec, "value": value, "error": errors.get(key)})
    return render(request, "manage/settings.html", {"rows": rows}, status=400 if errors else 200)
