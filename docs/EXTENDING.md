# How V2 - V5 plug into this V1

Nothing below requires changing a V1 table's existing columns.

## V2 - AI intelligence
- Where: tabs on `templates/documents/detail.html` (Summary | Entities and Tags | Chat | Translate).
- Fill the columns already reserved on `DocumentVersion`: `summary`, `entities`, `language`.
- New tables: `Tag` (M2M to Document), `DocumentChunk` (version, index, text, embedding),
  `ChatMessage`, `Translation`.
- Hook: at the end of `finish_extraction()` in `core/services/pipeline.py`, call an `enrichment.run(version)`
  function (summary, entities, embeddings). Keep translation and chat on-demand (buttons with a spinner).
- Semantic search: add a `semantic` flag in `core/services/search.py::search_documents` and `/api/search/`.
  Keyword search stays untouched.
- Add the libraries (spaCy, sentence-transformers CPU build, sumy, Argos Translate) to requirements and the
  matching checks to `core/services/health.py::run_checks()`. Set OFFLINE_MODE=1 for the demo.

## V3 - Enterprise
- Approval: the state machine already contains VERIFIED -> IN_APPROVAL -> APPROVED/REJECTED
  (`core/constants.py::ALLOWED_TRANSITIONS`). Add `Workflow` / `ApprovalStep` tables and an `APPROVER`
  value to `constants.Role` (a CharField, so no schema change). Start approval only when a workflow is
  enabled for the document type.
- Versioning: `Document` + `DocumentVersion` already exist. A re-upload = a new `DocumentVersion`
  (`version_number + 1`, same Document), run through the same `run_pipeline()`.
  Compare = `difflib` on `extracted_text` and `structured_data`.
- Bulk: wrap `pipeline.run_pipeline(document_id)` in a Huey task. It is idempotent and claims its work
  atomically, so it is safe to call from a worker. On Windows use thread workers, or Huey immediate mode.

## V4 - Differentiators
- Gatekeeper: implement `_gatekeeper_allows()` in `core/services/pipeline.py` (runs after the readability
  check and before OCR). Read `SystemSetting.get("GATEKEEPER_MODE")`: AUTO_TRANSFORM (current behaviour) |
  BLOCK (transition to `BLOCKED`) | OFF. The setting, the status and the Admin settings screen already exist.
- Schema builder / custom templates: a UI that writes `DocumentType.classification_rules` and
  `DocumentType.schema` (validate with `core/schema_spec.py::validate_schema`). The extraction engine
  already reads these rows, so new types work with no engine change.
- Dual output (PDF + JSON/XML): generate on demand from stored data (`exporters.build_payload`).
- Accessible export: build HTML from `DocumentVersion.layout_json` (headings, reading order).

## V5 - Future scope (not built)
Handwriting recognition, contract intelligence, email ingestion, scanner integration, multi-language OCR
(`settings.OCR_LANG` and `DocumentVersion.language` are ready), AI document generator, smart forms,
cloud deployment.
