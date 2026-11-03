"""Validates a DocumentType.schema. Used by the seed command now and by the
V4 Schema Builder later, so the extraction engine can trust what it reads."""
import re

FIELD_TYPES = {"string", "text", "date", "money", "number", "email", "phone", "id_number", "url", "list"}
STRATEGY_KINDS = {"label", "regex", "first_line", "section"}


def validate_schema(schema):
    """Return a list of error strings (empty list = valid)."""
    errors = []
    fields = (schema or {}).get("fields")
    if not isinstance(fields, list) or not fields:
        return ["schema.fields must be a non-empty list"]
    seen = set()
    for i, f in enumerate(fields):
        where = f"fields[{i}]"
        key = f.get("key")
        if not key or not re.fullmatch(r"[a-z][a-z0-9_]*", key):
            errors.append(f"{where}: 'key' must be snake_case")
            continue
        where = f"field '{key}'"
        if key in seen:
            errors.append(f"{where}: duplicate key")
        seen.add(key)
        if not f.get("label"):
            errors.append(f"{where}: missing 'label'")
        if f.get("type") not in FIELD_TYPES:
            errors.append(f"{where}: type must be one of {sorted(FIELD_TYPES)}")
        strategies = f.get("strategies")
        if not isinstance(strategies, list) or not strategies:
            errors.append(f"{where}: needs at least one strategy")
            continue
        for s in strategies:
            kind = s.get("kind")
            if kind not in STRATEGY_KINDS:
                errors.append(f"{where}: unknown strategy kind '{kind}'")
            elif kind == "label" and not s.get("anchors"):
                errors.append(f"{where}: 'label' strategy needs 'anchors'")
            elif kind == "section" and not s.get("headings"):
                errors.append(f"{where}: 'section' strategy needs 'headings'")
            elif kind == "regex":
                try:
                    re.compile(s.get("pattern", ""))
                except re.error as exc:
                    errors.append(f"{where}: bad regex ({exc})")
                if not s.get("pattern"):
                    errors.append(f"{where}: 'regex' strategy needs 'pattern'")
    return errors
