from django.contrib import admin

from . import models as m

admin.site.register(m.Tag)
admin.site.register(m.DocumentTag)


@admin.register(m.DocumentInsight)
class InsightAdmin(admin.ModelAdmin):
    list_display = ("version", "classification_method", "entity_backend", "embedding_backend", "enriched_at")


@admin.register(m.ChatMessage)
class ChatAdmin(admin.ModelAdmin):
    list_display = ("document", "user", "mode", "created_at")


@admin.register(m.Translation)
class TranslationAdmin(admin.ModelAdmin):
    list_display = ("version", "source_lang", "target_lang", "created_at")
