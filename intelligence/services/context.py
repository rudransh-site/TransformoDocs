from core import constants as C
from core.services.permissions import role_of

from . import translate
from .translate import LANG_NAMES


def detail_context(request, doc):
    from intelligence.models import DocumentInsight, DocumentTag, Translation

    v = doc.current_version
    if v is None:
        return {"v2_enabled": False}
    insight = DocumentInsight.objects.filter(version=v).first()
    groups = {}
    for e in v.entities or []:
        groups.setdefault(e["label"], []).append(e)
    entity_groups = [{"label": k, "items": groups[k]} for k in
                     ("Person", "Organization", "Place", "Date", "Amount", "Email", "Phone", "Link") if k in groups]
    history = list(doc.chat_messages.order_by("-created_at", "-id")[:30])[::-1]
    pairs = set(translate.status()["pairs"])
    src = v.language if v.language in LANG_NAMES else "en"
    target = "hi" if src == "en" else "en"
    cached = Translation.objects.filter(version=v, target_lang=target).first()
    suggestions = ["Summarize this document"]
    if doc.doc_type:
        suggestions += [f"What is the {s.get('label', s['key'])}?" for s in doc.doc_type.field_specs()
                        if s.get("required")][:2]
    suggestions.append("List the dates")
    can_modify = doc.owner_id == request.user.id or role_of(request.user) == C.Role.ADMIN
    return {
        "v2_enabled": True, "insight": insight, "entity_groups": entity_groups, "doc_tags": list(
            DocumentTag.objects.filter(document=doc).select_related("tag")),
        "chat_history": history, "chat_suggestions": suggestions,
        "show_chat": bool(v.extracted_text) and doc.status not in C.PROCESSING_STATES and doc.status != "FAILED",
        "tr": {"available": f"{src}-{target}" in pairs, "source": src, "target": target,
               "source_name": LANG_NAMES[src], "target_name": LANG_NAMES[target], "cached": cached},
        "v2_can_modify": can_modify,
    }
