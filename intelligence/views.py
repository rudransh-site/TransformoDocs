"""V2 API endpoints (session auth, same rules and error format as V1)."""
import logging

from django.http import Http404
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core import constants as C
from core.models import SystemSetting
from core.services.permissions import role_of, visible_documents

from .models import ChatMessage, DocumentTag, Tag
from .services import chat, enrich, translate

logger = logging.getLogger("transformodocs.v2")


def _doc(request, pk):
    try:
        return visible_documents(request.user).select_related("current_version", "doc_type").get(pk=pk)
    except Exception:  # noqa: BLE001 - DoesNotExist -> 404 (never reveal other users' documents)
        raise Http404 from None


def _can_modify(user, doc):
    return doc.owner_id == user.id or role_of(user) == C.Role.ADMIN


def _msg_dict(m):
    return {"id": m.id, "question": m.question, "answer": m.answer, "mode": m.mode, "sources": m.sources,
            "created_at": m.created_at.isoformat()}


class ChatView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        doc = _doc(request, pk)
        return Response({"messages": [_msg_dict(m) for m in doc.chat_messages.order_by("created_at", "id")[:100]]})

    def post(self, request, pk):
        doc = _doc(request, pk)
        v = doc.current_version
        question = str((request.data or {}).get("question", "")).strip()
        if not question:
            return Response({"error": "Please type a question.", "code": "EMPTY_QUESTION"}, status=400)
        if len(question) > 500:
            return Response({"error": "Please keep the question under 500 characters.", "code": "TOO_LONG"}, status=400)
        if v is None or not (v.extracted_text or "").strip():
            return Response({"error": "This document has no text to chat about yet.", "code": "NO_TEXT"}, status=409)
        result = chat.answer(question, doc, v)
        msg = ChatMessage.objects.create(document=doc, user=request.user, question=question,
                                         answer=result["answer"], mode=result["mode"], sources=result["sources"])
        return Response(_msg_dict(msg))

    def delete(self, request, pk):
        doc = _doc(request, pk)
        if not _can_modify(request.user, doc):
            raise PermissionDenied("Only the owner can clear the conversation.")
        doc.chat_messages.all().delete()
        return Response({"ok": True})


class TranslateView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        doc = _doc(request, pk)
        v = doc.current_version
        target = str((request.data or {}).get("target", "hi"))
        if target not in translate.LANG_NAMES:
            return Response({"error": "Unsupported language.", "code": "BAD_LANGUAGE"}, status=400)
        try:
            obj, cached = translate.translate_version(
                v, target, SystemSetting.get("TRANSLATION_MAX_CHARS"), force=bool((request.data or {}).get("force")))
        except translate.TranslationUnavailable as exc:
            return Response({"error": str(exc), "code": "TRANSLATION_UNAVAILABLE"}, status=503)
        return Response({"text": obj.text, "source_text": obj.source_text, "source": obj.source_lang,
                         "target": obj.target_lang, "cached": cached,
                         "truncated": len(v.extracted_text or "") > len(obj.source_text)})


class RegenerateView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        doc = _doc(request, pk)
        if not _can_modify(request.user, doc):
            raise PermissionDenied("Only the owner can regenerate insights.")
        insight = enrich.enrich_version(doc.current_version, doc=doc)
        if insight is None:
            return Response({"error": "This document has no text yet.", "code": "NO_TEXT"}, status=409)
        return Response({"ok": True, "errors": insight.error})


class TagsView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        doc = _doc(request, pk)
        if not _can_modify(request.user, doc):
            raise PermissionDenied("Only the owner can add tags.")
        tag = Tag.get_or_make(str((request.data or {}).get("name", "")))
        if tag is None:
            return Response({"error": "Please enter a tag name.", "code": "EMPTY_TAG"}, status=400)
        DocumentTag.objects.update_or_create(document=doc, tag=tag, defaults={"source": "MANUAL"})
        return Response({"ok": True, "name": tag.name, "slug": tag.slug})


class TagDeleteView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request, pk, tag_id):
        doc = _doc(request, pk)
        if not _can_modify(request.user, doc):
            raise PermissionDenied("Only the owner can remove tags.")
        DocumentTag.objects.filter(document=doc, tag_id=tag_id).delete()
        return Response({"ok": True})
