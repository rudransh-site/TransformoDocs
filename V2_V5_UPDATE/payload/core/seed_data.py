"""Built-in document types (INVOICE, RESUME, CERTIFICATE, ID_CARD).

CLASSIFICATION RULES (DocumentType.classification_rules)
    {"terms":    [{"t": "invoice no", "w": 3}, ...],   # case-insensitive, whole phrase
     "patterns": [{"p": "<regex>", "w": 3}, ...],      # optional
     "full_score": 10}
    Every term/pattern counts ONCE however often it appears. Score = sum of
    matched weights. confidence = min(1, score / full_score). The type with the
    highest score wins; below MIN_CLASSIFICATION_CONFIDENCE -> UNKNOWN.

EXTRACTION SCHEMA (DocumentType.schema)
    {"fields": [{"key", "label", "type", "required", "strategies": [...]}]}
    Strategies are tried in order; the first that yields a value wins:
      {"kind": "label",  "anchors": [...], "next_line": bool, "max_lines": int}
          value after "<anchor>:" on the same line; if empty and next_line is
          true, take the following line(s).
      {"kind": "regex",  "pattern": "...(group 1 = value)..."}
      {"kind": "first_line", "skip_if_contains": [...]}
          first non-empty line that does not contain a skip word.
      {"kind": "section", "headings": [...], "stop_headings": [...]}
          text under a heading until the next heading.
    Types: string text date money number email phone id_number url list

To change a schema: edit it here and re-run `python manage.py seed_initial_data`.
"""

INVOICE = {
    "code": "INVOICE",
    "name": "Invoice",
    "description": "Commercial invoice or bill.",
    "classification_rules": {
        "terms": [
            {"t": "invoice", "w": 3}, {"t": "invoice no", "w": 3}, {"t": "invoice number", "w": 3},
            {"t": "bill to", "w": 2}, {"t": "billed to", "w": 2}, {"t": "ship to", "w": 1},
            {"t": "subtotal", "w": 2}, {"t": "sub total", "w": 2}, {"t": "grand total", "w": 2},
            {"t": "total", "w": 1}, {"t": "gst", "w": 2}, {"t": "gstin", "w": 2}, {"t": "tax", "w": 1},
            {"t": "due date", "w": 2}, {"t": "amount due", "w": 2}, {"t": "balance due", "w": 2},
            {"t": "quantity", "w": 1}, {"t": "qty", "w": 1}, {"t": "unit price", "w": 1},
            {"t": "payment terms", "w": 1},
        
            # Hindi (V5)
            {"t": "चालान", "w": 3}, {"t": "कर चालान", "w": 3}, {"t": "कुल राशि", "w": 2}, {"t": "जीएसटी", "w": 2}, {"t": "देय तिथि", "w": 2}, {"t": "ग्राहक", "w": 1}, {"t": "बिल", "w": 1},
        ],
        "patterns": [{"p": r"invoice\s*(?:no\.?|number|#)", "w": 3}],
        "full_score": 10,
    },
    "schema": {
        "fields": [
            {"key": "invoice_number", "label": "Invoice Number", "type": "id_number", "required": True,
             "strategies": [
                 {"kind": "label", "anchors": ["चालान संख्या", "चालान क्रमांक", "बिल संख्या", "invoice number", "invoice no", "invoice #", "inv no", "bill no"]},
                 {"kind": "regex", "pattern": r"(?i)invoice\s*(?:no\.?|number|#)\s*[:\-]?\s*([A-Z0-9][A-Z0-9\-/]{2,})"},
             ]},
            {"key": "invoice_date", "label": "Invoice Date", "type": "date", "required": True,
             "strategies": [
                 {"kind": "label", "anchors": ["चालान दिनांक", "दिनांक", "तिथि", "invoice date", "date of issue", "bill date", "date"]},
                 {"kind": "regex", "pattern": r"(?i)date\s*[:\-]?\s*(\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4}|\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4})"},
             ]},
            {"key": "due_date", "label": "Due Date", "type": "date", "required": False,
             "strategies": [{"kind": "label", "anchors": ["देय तिथि", "due date", "payment due", "pay by"]}]},
            {"key": "vendor_name", "label": "Vendor", "type": "string", "required": False,
             "strategies": [{"kind": "first_line", "skip_if_contains": ["invoice", "tax invoice"]}]},
            {"key": "customer_name", "label": "Customer", "type": "string", "required": False,
             "strategies": [{"kind": "label", "anchors": ["ग्राहक का नाम", "ग्राहक", "bill to", "billed to", "customer name", "customer", "buyer", "sold to"],
                             "next_line": True}]},
            {"key": "gstin", "label": "GSTIN", "type": "id_number", "required": False,
             "strategies": [{"kind": "regex", "pattern": r"\b(\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z])\b"}]},
            {"key": "subtotal", "label": "Subtotal", "type": "money", "required": False,
             "strategies": [{"kind": "label", "anchors": ["उप योग", "subtotal", "sub total", "taxable value"]}]},
            {"key": "tax_amount", "label": "Tax Amount", "type": "money", "required": False,
             "strategies": [{"kind": "label", "anchors": ["total tax", "igst", "cgst", "gst", "tax"]}]},
            {"key": "total_amount", "label": "Total Amount", "type": "money", "required": True,
             "strategies": [{"kind": "label", "anchors": ["कुल राशि", "कुल योग", "कुल देय", "grand total", "total amount", "amount due", "balance due",
                                                          "total payable", "total"]}]},
        ]
    },
}

RESUME = {
    "code": "RESUME",
    "name": "Resume",
    "description": "Resume / curriculum vitae.",
    "classification_rules": {
        "terms": [
            {"t": "resume", "w": 3}, {"t": "curriculum vitae", "w": 3}, {"t": "objective", "w": 2},
            {"t": "career objective", "w": 2}, {"t": "professional summary", "w": 2},
            {"t": "education", "w": 2}, {"t": "experience", "w": 2}, {"t": "work experience", "w": 2},
            {"t": "skills", "w": 2}, {"t": "technical skills", "w": 2}, {"t": "projects", "w": 2},
            {"t": "internship", "w": 1}, {"t": "linkedin", "w": 1}, {"t": "github", "w": 1},
            {"t": "references", "w": 1}, {"t": "achievements", "w": 1},
        
            # Hindi (V5)
            {"t": "बायोडाटा", "w": 3}, {"t": "शैक्षणिक योग्यता", "w": 2}, {"t": "कौशल", "w": 2}, {"t": "अनुभव", "w": 2}, {"t": "परियोजनाएं", "w": 2},
        ],
        "patterns": [],
        "full_score": 8,
    },
    "schema": {
        "fields": [
            {"key": "full_name", "label": "Full Name", "type": "string", "required": True,
             "strategies": [{"kind": "first_line", "skip_if_contains": ["resume", "curriculum vitae", "cv"]}]},
            {"key": "email", "label": "Email", "type": "email", "required": True,
             "strategies": [{"kind": "regex", "pattern": r"([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})"}]},
            {"key": "phone", "label": "Phone", "type": "phone", "required": False,
             "strategies": [
                 {"kind": "regex", "pattern": r"(?<!\d)((?:\+?91[\s\-]?)?[6-9]\d{9})(?!\d)"},
                 {"kind": "regex", "pattern": r"(\+?\d[\d\s\-()]{8,}\d)"},
             ]},
            {"key": "linkedin", "label": "LinkedIn", "type": "url", "required": False,
             "strategies": [{"kind": "regex", "pattern": r"((?:https?://)?(?:www\.)?linkedin\.com/[^\s]+)"}]},
            {"key": "github", "label": "GitHub", "type": "url", "required": False,
             "strategies": [{"kind": "regex", "pattern": r"((?:https?://)?(?:www\.)?github\.com/[^\s]+)"}]},
            {"key": "skills", "label": "Skills", "type": "list", "required": False,
             "strategies": [{"kind": "section",
                             "headings": ["skills", "technical skills", "key skills", "core competencies"],
                             "stop_headings": ["education", "experience", "work experience", "projects",
                                               "certifications", "achievements", "summary", "objective", "profile"]}]},
            {"key": "education", "label": "Education", "type": "text", "required": False,
             "strategies": [{"kind": "section",
                             "headings": ["education", "academic qualification", "academic qualifications", "academics"],
                             "stop_headings": ["skills", "experience", "work experience", "projects",
                                               "certifications", "achievements"]}]},
            {"key": "experience", "label": "Experience", "type": "text", "required": False,
             "strategies": [{"kind": "section",
                             "headings": ["experience", "work experience", "professional experience",
                                          "internship", "internships"],
                             "stop_headings": ["skills", "education", "projects", "certifications", "achievements"]}]},
            {"key": "projects", "label": "Projects", "type": "text", "required": False,
             "strategies": [{"kind": "section",
                             "headings": ["projects", "academic projects", "personal projects"],
                             "stop_headings": ["skills", "education", "experience", "work experience",
                                               "certifications", "achievements"]}]},
        ]
    },
}

CERTIFICATE = {
    "code": "CERTIFICATE",
    "name": "Certificate",
    "description": "Certificate of completion, achievement or participation.",
    "classification_rules": {
        "terms": [
            {"t": "certificate", "w": 3}, {"t": "this is to certify", "w": 4}, {"t": "certify that", "w": 3},
            {"t": "certificate of completion", "w": 4}, {"t": "certificate of achievement", "w": 4},
            {"t": "certificate of participation", "w": 4}, {"t": "has successfully completed", "w": 3},
            {"t": "successfully completed", "w": 2}, {"t": "awarded to", "w": 3}, {"t": "presented to", "w": 3},
            {"t": "in recognition of", "w": 2}, {"t": "date of issue", "w": 1}, {"t": "issued on", "w": 1},
            {"t": "certificate id", "w": 2}, {"t": "credential id", "w": 2}, {"t": "signature", "w": 1},
        
            # Hindi (V5)
            {"t": "प्रमाणपत्र", "w": 3}, {"t": "प्रमाणित किया जाता है", "w": 4}, {"t": "सफलतापूर्वक पूरा", "w": 3}, {"t": "प्रदान किया", "w": 2},
        ],
        "patterns": [{"p": r"certificate\s+of\s+\w+", "w": 3}],
        "full_score": 8,
    },
    "schema": {
        "fields": [
            {"key": "recipient_name", "label": "Recipient Name", "type": "string", "required": True,
             "strategies": [{"kind": "label",
                             "anchors": ["यह प्रमाणित किया जाता है कि", "प्रदान किया गया", "this is to certify that", "certify that", "awarded to", "presented to",
                                         "proudly presented to", "issued to", "name"],
                             "next_line": True}]},
            {"key": "certificate_title", "label": "Certificate Title", "type": "string", "required": False,
             "strategies": [{"kind": "regex", "pattern": r"(?i)(certificate\s+of\s+[A-Za-z]+)"}]},
            {"key": "course_or_achievement", "label": "Course / Achievement", "type": "text", "required": False,
             "strategies": [{"kind": "label",
                             "anchors": ["पाठ्यक्रम", "has successfully completed", "successfully completed", "for completing",
                                         "for successfully completing", "in recognition of", "for participating in",
                                         "course"],
                             "next_line": True, "max_lines": 2}]},
            {"key": "issuing_organization", "label": "Issuing Organization", "type": "string", "required": False,
             "strategies": [{"kind": "label",
                             "anchors": ["जारीकर्ता", "द्वारा जारी", "issued by", "organized by", "organised by", "awarded by", "conducted by"],
                             "next_line": True}]},
            {"key": "issue_date", "label": "Issue Date", "type": "date", "required": True,
             "strategies": [{"kind": "label", "anchors": ["जारी करने की तिथि", "दिनांक", "date of issue", "issued on", "issue date", "date"]}]},
            {"key": "certificate_id", "label": "Certificate ID", "type": "id_number", "required": False,
             "strategies": [
                 {"kind": "label", "anchors": ["प्रमाणपत्र संख्या", "प्रमाणपत्र क्रमांक", "certificate id", "certificate no", "certificate number",
                                               "credential id", "serial no", "reg no"]},
                 {"kind": "regex", "pattern": r"(?i)(?:certificate|credential)\s*(?:id|no\.?|number)\s*[:\-]?\s*([A-Z0-9\-/]{4,})"},
             ]},
        ]
    },
}

ID_CARD = {
    "code": "ID_CARD",
    "name": "ID Card",
    "description": "Student / employee identity card.",
    "classification_rules": {
        "terms": [
            {"t": "identity card", "w": 4}, {"t": "id card", "w": 4}, {"t": "student id", "w": 3},
            {"t": "employee id", "w": 3}, {"t": "date of birth", "w": 2}, {"t": "dob", "w": 2},
            {"t": "blood group", "w": 2}, {"t": "valid upto", "w": 2}, {"t": "valid till", "w": 2},
            {"t": "valid until", "w": 2}, {"t": "enrollment no", "w": 2}, {"t": "enrolment no", "w": 2},
            {"t": "roll no", "w": 2}, {"t": "issuing authority", "w": 2}, {"t": "authorized signatory", "w": 1},
            {"t": "emergency contact", "w": 1}, {"t": "department", "w": 1}, {"t": "address", "w": 1},
        
            # Hindi (V5)
            {"t": "पहचान पत्र", "w": 4}, {"t": "परिचय पत्र", "w": 4}, {"t": "जन्म तिथि", "w": 2}, {"t": "रक्त समूह", "w": 2}, {"t": "छात्र", "w": 1},
        ],
        "patterns": [],
        "full_score": 8,
    },
    "schema": {
        "fields": [
            {"key": "full_name", "label": "Full Name", "type": "string", "required": True,
             "strategies": [{"kind": "label",
                             "anchors": ["छात्र का नाम", "नाम", "student name", "employee name", "holder name", "full name", "name"]}]},
            {"key": "id_number", "label": "ID Number", "type": "id_number", "required": True,
             "strategies": [
                 {"kind": "label", "anchors": ["पहचान संख्या", "नामांकन संख्या", "रोल नंबर", "id no", "id number", "student id", "employee id", "enrollment no",
                                               "enrolment no", "roll no", "reg no", "registration no", "emp id"]},
                 {"kind": "regex", "pattern": r"(?i)(?:id|enrol?l?ment|roll|reg(?:istration)?|emp(?:loyee)?)\s*(?:no\.?|number|#|id)?\s*[:\-]?\s*([A-Z0-9][A-Z0-9\-/]{3,})"},
             ]},
            {"key": "date_of_birth", "label": "Date of Birth", "type": "date", "required": False,
             "strategies": [{"kind": "label", "anchors": ["जन्म तिथि", "date of birth", "dob", "d.o.b", "birth date"]}]},
            {"key": "organization", "label": "Organization", "type": "string", "required": False,
             "strategies": [{"kind": "first_line", "skip_if_contains": ["identity card", "id card"]}]},
            {"key": "department", "label": "Department / Course", "type": "string", "required": False,
             "strategies": [{"kind": "label", "anchors": ["विभाग", "department", "dept", "course", "branch", "programme", "program"]}]},
            {"key": "blood_group", "label": "Blood Group", "type": "string", "required": False,
             "strategies": [
                 {"kind": "label", "anchors": ["रक्त समूह", "blood group"]},
                 {"kind": "regex", "pattern": r"\b((?:AB|A|B|O)[+\-])(?=\s|$)"},
             ]},
            {"key": "phone", "label": "Phone", "type": "phone", "required": False,
             "strategies": [
                 {"kind": "label", "anchors": ["phone", "mobile", "contact"]},
                 {"kind": "regex", "pattern": r"(?<!\d)((?:\+?91[\s\-]?)?[6-9]\d{9})(?!\d)"},
             ]},
            {"key": "valid_until", "label": "Valid Until", "type": "date", "required": False,
             "strategies": [{"kind": "label", "anchors": ["वैधता तिथि", "valid upto", "valid up to", "valid till", "valid until",
                                                          "expiry date", "expiry"]}]},
            {"key": "address", "label": "Address", "type": "text", "required": False,
             "strategies": [{"kind": "label", "anchors": ["पता", "address"], "next_line": True, "max_lines": 3}]},
        ]
    },
}

DOCUMENT_TYPES = [INVOICE, RESUME, CERTIFICATE, ID_CARD]
