from django.contrib import admin

from .models import DocumentOCRProfile, GeneratedDocument


@admin.register(DocumentOCRProfile)
class OCRProfileAdmin(admin.ModelAdmin):
    list_display = ("document", "language", "handwriting", "created_at")


@admin.register(GeneratedDocument)
class GeneratedAdmin(admin.ModelAdmin):
    list_display = ("title", "owner", "template", "created_at")
