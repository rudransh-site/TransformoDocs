import json

from django import template

from core.services.presenters import STATUS_BADGE

register = template.Library()


@register.filter
def get_item(mapping, key):
    try:
        return mapping.get(key)
    except AttributeError:
        return None


@register.filter
def status_color(status):
    return STATUS_BADGE.get(status, "secondary")


@register.filter
def conf_color(value):
    if value is None:
        return "secondary"
    return "success" if value >= 85 else "warning" if value >= 60 else "danger"


@register.filter
def pretty_json(value):
    return json.dumps(value, indent=2, ensure_ascii=False)


@register.filter
def pct(value):
    return "-" if value is None else f"{value * 100:.0f}%"


@register.filter
def num1(value):
    return "-" if value is None else f"{value:.1f}"
