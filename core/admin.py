from django.contrib import admin

from . import models as m


class VersionInline(admin.TabularInline):
    model = m.DocumentVersion
    extra = 0
    fields = ("version_number", "original_filename", "overall_confidence", "detection_method")
    readonly_fields = fields
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(m.Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "role", "created_at")
    list_filter = ("role",)


@admin.register(m.DocumentType)
class DocumentTypeAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "is_builtin", "is_active", "updated_at")


@admin.register(m.Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ("id", "display_name", "owner", "doc_type", "status", "verification_method", "created_at")
    list_filter = ("status", "doc_type")
    search_fields = ("versions__original_filename", "owner__username")
    inlines = [VersionInline]


@admin.register(m.DocumentVersion)
class DocumentVersionAdmin(admin.ModelAdmin):
    list_display = ("id", "original_filename", "version_number", "overall_confidence", "detection_method")
    search_fields = ("original_filename", "file_hash")


@admin.register(m.ReviewTask)
class ReviewTaskAdmin(admin.ModelAdmin):
    list_display = ("id", "version", "decision", "reviewed_by", "created_at", "decided_at")
    list_filter = ("decision",)


@admin.register(m.AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "username", "action", "target_type", "target_id", "ip_address")
    list_filter = ("action",)
    search_fields = ("username", "target_type")
    readonly_fields = [f.name for f in m.AuditLog._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(m.Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("last_seen_at", "severity", "code", "occurrences", "is_read")
    list_filter = ("severity", "is_read")


@admin.register(m.SystemSetting)
class SystemSettingAdmin(admin.ModelAdmin):
    list_display = ("key", "value", "updated_by", "updated_at")
