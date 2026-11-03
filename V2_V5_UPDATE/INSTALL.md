# TransformoDocs V2 + V5 update: install guide

This adds **V2** (summaries, entities, tags, AI classification, semantic search, document chat, translation)
and **V5** (Hindi OCR, handwriting recognition, document generator) on top of your working V1.
Your V1 files that need changes are backed up automatically, and you can undo everything.

## 1. Put the folder in place
Extract the zip **inside your V1 project folder** (the folder that contains `manage.py`), so you have:

    transformodocs/
        manage.py
        core/
        V2_V5_UPDATE/      <- this folder

## 2. Run the integration script
Open a terminal in your project folder, activate your virtual environment, then:

    python V2_V5_UPDATE/integrate.py

It copies the new files in and backs up the 23 V1 files it replaces into `_backup_before_v2_v5/`.
(Want to preview first? `python V2_V5_UPDATE/integrate.py --dry-run`)

## 3. Install the extra libraries
    pip install -r requirements.txt

Windows only, if you ever see "No module named cv2" afterwards:
    pip uninstall -y opencv-python opencv-python-headless
    pip install opencv-python-headless

## 4. Update the database and data
    python manage.py migrate
    python manage.py seed_initial_data
(`seed_initial_data` adds Hindi words to the document types. It is safe to run again.)

## 5. One-time model download (needs internet)
    python manage.py download_models

This downloads: the spaCy English model, Hindi OCR data, and (if you installed them, step 6) the MiniLM
and Hindi translation models. Each step reports OK or FAILED. A failed step never breaks the project:
that feature just falls back or shows a clear message. You can run the command again later.

## 6. Optional heavy extras: translation and MiniLM (about 1 GB)
    pip install -r requirements_optional_models.txt
    python manage.py download_models
- **Translation tab** (English <-> Hindi) works only after this step.
- **Semantic search / chat** work without it (a built-in matcher is used); MiniLM makes them more accurate.
- Linux: install the small CPU PyTorch first: `pip install torch --index-url https://download.pytorch.org/whl/cpu`

## 7. Check and run
    python manage.py preflight
    python manage.py warm_models          (optional: loads models so the first demo request is fast)
    python manage.py enrich_existing      (optional: adds summaries/tags to documents you uploaded before)
    python manage.py runserver --noreload

`preflight` must say "Ready to demo". Lines marked WARN are optional pieces that are not installed yet.
For the demo day: set `OFFLINE_MODE=1` in `.env`, restart, and try one document with Wi-Fi off.

## What you get (and where)
| Feature | Where to find it |
|---|---|
| Summary, Entities and tags, Translate | Open any document: three new tabs |
| AI chat assistant | Round blue button, bottom-right of a document page |
| Semantic search | Search page: "Keyword / Semantic" toggle. Tags on the document are clickable |
| AI classification | Automatic when the normal rules are unsure (shown on the Summary tab) |
| Hindi OCR | Upload page: "Document language" |
| Handwriting recognition | Upload page: "Handwritten document" switch (always goes to a reviewer) |
| Document generator | "Generate" in the top menu: Invoice and Certificate, PDF/Word, or save into the system |

## 5-minute demo for V2 + V5
1. Log in as `demo_user`. Upload `samples/invoice_native.pdf`. Open it: **Summary**, **Entities & tags**.
2. Click the blue chat button. Ask "What is the total?", "List the dates", "Summarize this document".
3. **Search** -> choose **Semantic** -> type "balance outstanding". The invoice is found by meaning.
4. Upload `samples/hindi_certificate.png` with language **English + Hindi**: read, classified, fields extracted.
5. Upload `samples/handwritten_receipt.png` with **Handwritten document** on: text is recognised and the
   document goes to a reviewer (log in as `demo_reviewer`) with a handwriting warning.
6. **Generate** -> Invoice. Fill it in, watch the live preview, press **Save to my documents**: the generated
   PDF goes through the normal pipeline and is verified automatically.
7. Translate tab (after step 6 of this guide): English -> Hindi.

## Honest notes
- **Handwriting:** uses RapidOCR (instead of EasyOCR: no PyTorch, models included, works offline at once).
  It reads neat handwriting reasonably and messy or cursive handwriting poorly, so results always go to review.
  Hindi handwriting falls back to standard OCR. The sample is made with a handwriting-style font, not a real pen.
- **Chat** answers from the extracted text and fields. It is retrieval, not a generative AI.
- **Document generator** is template-based with smart autofill (from your documents, auto totals, amount in words).
- **Translation** covers the first part of a document (limit in Admin -> Settings) so it stays fast.

## Troubleshooting
- Hindi option greyed out / "not installed": run `python manage.py download_models` (or install `tesseract-ocr-hin`).
- Translate tab says the pack is missing: do step 6.
- Chat/search feel slow on the first request: run `python manage.py warm_models` before the demo.
- Page unstyled: keep `DJANGO_DEBUG=1` in `.env`.
- Anything else: Admin -> Health and alerts shows exactly which piece is missing and the fix.

## Undo
    python V2_V5_UPDATE/integrate.py --undo
Restores your V1 files and removes the V2/V5 files. (The extra database tables stay; they are harmless.)
