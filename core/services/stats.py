"""System-wide numbers for the Admin dashboard and /api/admin/stats/."""
from django.contrib.auth import get_user_model
from django.db.models import Avg, Count
from django.utils import timezone

from core import constants as C
from core.models import Document, DocumentVersion, Notification, Profile, ReviewTask


def system_stats():
    docs = Document.objects.all()
    by_status = {r["status"]: r["n"] for r in docs.values("status").annotate(n=Count("id"))}
    by_type = {(r["doc_type__name"] or "Unknown"): r["n"]
               for r in docs.values("doc_type__name").annotate(n=Count("id"))}
    versions = DocumentVersion.objects.all()
    by_method = {(r["detection_method"] or "Pending"): r["n"]
                 for r in versions.values("detection_method").annotate(n=Count("id"))}
    avg_conf = versions.exclude(overall_confidence__isnull=True).aggregate(a=Avg("overall_confidence"))["a"]
    times = [v.processing_seconds for v in
             versions.filter(processing_started_at__isnull=False, processing_finished_at__isnull=False)
             .order_by("-id")[:200] if v.processing_seconds is not None]
    reviewed = ReviewTask.objects.exclude(decision=C.ReviewDecision.PENDING).exclude(decided_at__isnull=True)
    turn = [(t.decided_at - t.created_at).total_seconds() / 60 for t in reviewed.order_by("-id")[:200]]
    today = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
    roles = {r["role"]: r["n"] for r in Profile.objects.values("role").annotate(n=Count("id"))}
    return {
        "total_documents": docs.count(),
        "documents_today": docs.filter(created_at__gte=today).count(),
        "by_status": by_status, "by_type": by_type, "by_method": by_method,
        "avg_confidence": round(avg_conf, 1) if avg_conf is not None else None,
        "avg_processing_seconds": round(sum(times) / len(times), 1) if times else None,
        "pending_reviews": ReviewTask.objects.filter(decision=C.ReviewDecision.PENDING).count(),
        "reviews_completed": reviewed.count(),
        "avg_review_minutes": round(sum(turn) / len(turn), 1) if turn else None,
        "users_by_role": roles, "total_users": get_user_model().objects.count(),
        "unread_alerts": Notification.objects.filter(is_read=False).count(),
    }
