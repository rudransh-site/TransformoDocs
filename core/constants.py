"""Shared enums and rules for TransformoDocs.

Statuses are stored as plain strings (not DB enums), so V3/V4 can use the
reserved values without any schema change.
"""
from django.db import models


class Role(models.TextChoices):
    USER = "USER", "User"
    REVIEWER = "REVIEWER", "Reviewer"
    ADMIN = "ADMIN", "Admin"


class DocStatus(models.TextChoices):
    UPLOADED = "UPLOADED", "Uploaded"
    DETECTING = "DETECTING", "Detecting"
    OCR_PROCESSING = "OCR_PROCESSING", "OCR processing"
    CLASSIFYING = "CLASSIFYING", "Classifying"
    EXTRACTING = "EXTRACTING", "Extracting"
    PENDING_VERIFICATION = "PENDING_VERIFICATION", "Pending verification"
    VERIFIED = "VERIFIED", "Verified"
    REJECTED = "REJECTED", "Rejected"
    FAILED = "FAILED", "Failed"
    ARCHIVED = "ARCHIVED", "Archived"
    # Reserved for later versions (valid today, unused until then)
    IN_APPROVAL = "IN_APPROVAL", "In approval"  # V3
    APPROVED = "APPROVED", "Approved"  # V3
    BLOCKED = "BLOCKED", "Blocked by gatekeeper"  # V4


# Pipeline states: a document stuck here for too long is "stuck".
PROCESSING_STATES = frozenset(
    {
        DocStatus.UPLOADED,
        DocStatus.DETECTING,
        DocStatus.OCR_PROCESSING,
        DocStatus.CLASSIFYING,
        DocStatus.EXTRACTING,
    }
)

# The ONLY allowed status changes. Part 2's transition() enforces this table.
ALLOWED_TRANSITIONS = {
    DocStatus.UPLOADED: {DocStatus.DETECTING, DocStatus.FAILED},
    DocStatus.DETECTING: {
        DocStatus.OCR_PROCESSING,
        DocStatus.CLASSIFYING,
        DocStatus.FAILED,
        DocStatus.BLOCKED,  # V4 gatekeeper
    },
    DocStatus.OCR_PROCESSING: {
        DocStatus.CLASSIFYING,
        DocStatus.PENDING_VERIFICATION,  # low-quality scan flagged for review
        DocStatus.FAILED,
    },
    DocStatus.CLASSIFYING: {DocStatus.EXTRACTING, DocStatus.FAILED},
    DocStatus.EXTRACTING: {DocStatus.VERIFIED, DocStatus.PENDING_VERIFICATION, DocStatus.FAILED},
    DocStatus.PENDING_VERIFICATION: {
        DocStatus.VERIFIED,
        DocStatus.REJECTED,
        DocStatus.EXTRACTING,  # reviewer changed the type -> re-extract
    },
    DocStatus.VERIFIED: {DocStatus.ARCHIVED, DocStatus.IN_APPROVAL},
    DocStatus.IN_APPROVAL: {DocStatus.APPROVED, DocStatus.REJECTED},
    DocStatus.APPROVED: {DocStatus.ARCHIVED},
    DocStatus.REJECTED: {DocStatus.ARCHIVED},
    DocStatus.FAILED: {DocStatus.DETECTING, DocStatus.ARCHIVED},  # DETECTING = Retry
    DocStatus.BLOCKED: {DocStatus.ARCHIVED},
    DocStatus.ARCHIVED: set(),
}


class DetectionMethod(models.TextChoices):
    NATIVE = "NATIVE", "Native text"
    OCR = "OCR", "OCR"
    MIXED = "MIXED", "Mixed"


class QualityFlag(models.TextChoices):
    NONE = "", "None"
    LOW_QUALITY = "LOW_QUALITY", "Low quality scan"


class VerificationMethod(models.TextChoices):
    NONE = "", "Not verified"
    AUTO = "AUTO", "Auto-verified"
    MANUAL = "MANUAL", "Verified by reviewer"


class ReviewDecision(models.TextChoices):
    PENDING = "PENDING", "Pending"
    APPROVED = "APPROVED", "Approved"
    EDITED = "EDITED", "Edited and approved"
    REJECTED = "REJECTED", "Rejected"


class Severity(models.TextChoices):
    INFO = "INFO", "Info"
    WARNING = "WARNING", "Warning"
    CRITICAL = "CRITICAL", "Critical"


class AuditAction(models.TextChoices):
    LOGIN = "LOGIN", "Login"
    LOGOUT = "LOGOUT", "Logout"
    LOGIN_FAILED = "LOGIN_FAILED", "Login failed"
    LOGIN_REQUIRED = "LOGIN_REQUIRED", "Login required / session expired"
    REGISTER = "REGISTER", "Register"
    UPLOAD = "UPLOAD", "Upload"
    UPLOAD_REJECTED = "UPLOAD_REJECTED", "Upload rejected"
    DUPLICATE_BLOCKED = "DUPLICATE_BLOCKED", "Duplicate blocked"
    STATUS_CHANGE = "STATUS_CHANGE", "Status change"
    PROCESS_FAILED = "PROCESS_FAILED", "Processing failed"
    REVIEW_CLAIM = "REVIEW_CLAIM", "Review claimed"
    REVIEW_APPROVE = "REVIEW_APPROVE", "Review approved"
    REVIEW_EDIT = "REVIEW_EDIT", "Review edited"
    REVIEW_REJECT = "REVIEW_REJECT", "Review rejected"
    REVIEW_CONFLICT = "REVIEW_CONFLICT", "Review conflict"
    DOWNLOAD = "DOWNLOAD", "Download"
    ARCHIVE = "ARCHIVE", "Archive"
    USER_CREATED = "USER_CREATED", "User created"
    ROLE_CHANGED = "ROLE_CHANGED", "Role changed"
    USER_DEACTIVATED = "USER_DEACTIVATED", "User deactivated"
    USER_REACTIVATED = "USER_REACTIVATED", "User reactivated"
    SETTINGS_CHANGED = "SETTINGS_CHANGED", "Settings changed"
    STUCK_RECOVERED = "STUCK_RECOVERED", "Stuck documents recovered"


# ---------------------------------------------------------------------------
# Admin-editable settings (stored in SystemSetting; these are the defaults).
# ---------------------------------------------------------------------------
GATEKEEPER_MODES = ("AUTO_TRANSFORM", "BLOCK", "OFF")  # V4 uses BLOCK/OFF

SETTING_DEFINITIONS = {
    "AUTO_VERIFY_MIN_CONFIDENCE": {
        "default": 85.0, "type": "float", "min": 0, "max": 100,
        "description": "Minimum overall OCR confidence (0-100) for auto-verification.",
    },
    "MIN_CLASSIFICATION_CONFIDENCE": {
        "default": 0.6, "type": "float", "min": 0, "max": 1,
        "description": "Minimum classification confidence (0-1) to accept a document type.",
    },
    "LOW_QUALITY_CONFIDENCE_FLOOR": {
        "default": 40.0, "type": "float", "min": 0, "max": 100,
        "description": "Below this OCR confidence a scan is treated as low quality.",
    },
    "MIN_OCR_WORDS": {
        "default": 5, "type": "int", "min": 0, "max": 1000,
        "description": "Fewer recognised words than this counts as empty/garbage OCR.",
    },
    "NATIVE_PAGE_MIN_CHARS": {
        "default": 30, "type": "int", "min": 1, "max": 1000,
        "description": "A PDF page with fewer text characters is treated as scanned.",
    },
    "REVIEW_LOCK_MINUTES": {
        "default": 10, "type": "int", "min": 1, "max": 120,
        "description": "How long a reviewer's claim on a document is held.",
    },
    "STUCK_AFTER_SECONDS": {
        "default": 120, "type": "int", "min": 30, "max": 3600,
        "description": "A document in a processing state longer than this is 'stuck'.",
    },
    "ERROR_ALERT_REPEAT_COUNT": {
        "default": 3, "type": "int", "min": 1, "max": 100,
        "description": "Same pipeline error this many times within the window alerts Admin.",
    },
    "ERROR_ALERT_WINDOW_MINUTES": {
        "default": 10, "type": "int", "min": 1, "max": 1440,
        "description": "Window used by ERROR_ALERT_REPEAT_COUNT.",
    },
    "AI_CLASSIFY_MIN_CONFIDENCE": {
        "default": 0.5, "type": "float", "min": 0, "max": 1,
        "description": "V2: minimum confidence for the AI second-opinion classifier to name a type.",
    },
    "TRANSLATION_MAX_CHARS": {
        "default": 3000, "type": "int", "min": 200, "max": 20000,
        "description": "V2: how many characters of a document are translated (keeps the demo fast).",
    },
    "GATEKEEPER_MODE": {
        "default": "AUTO_TRANSFORM", "type": "choice", "choices": GATEKEEPER_MODES,
        "description": "V4: what to do with non-machine-readable uploads.",
    },
}


def validate_setting(key, raw):
    """Return the cleaned value for a setting or raise ValueError."""
    spec = SETTING_DEFINITIONS.get(key)
    if spec is None:
        raise ValueError(f"Unknown setting: {key}")
    kind = spec["type"]
    try:
        if kind == "float":
            value = float(raw)
        elif kind == "int":
            if isinstance(raw, float) and not raw.is_integer():
                raise ValueError
            value = int(raw)
        else:
            value = str(raw)
    except (TypeError, ValueError):
        raise ValueError(f"{key} must be a {kind}.") from None
    if kind == "choice" and value not in spec["choices"]:
        raise ValueError(f"{key} must be one of: {', '.join(spec['choices'])}.")
    if "min" in spec and value < spec["min"]:
        raise ValueError(f"{key} must be at least {spec['min']}.")
    if "max" in spec and value > spec["max"]:
        raise ValueError(f"{key} must be at most {spec['max']}.")
    return value
