TransformoDocs sample documents (synthetic, fictional data)

GOOD FILES (one per type)
  invoice_native.pdf        text PDF -> read directly, usually auto-verified
  invoice_scanned.png       scanned image -> OCR
  invoice_scanned.pdf       image-only PDF -> OCR
  invoice_scanned_skewed.png  rotated + noisy scan -> OCR with deskew
  resume_native.pdf / resume_native.docx   text resume
  certificate_native.pdf / certificate_scanned.pdf
  id_card_scanned.png       ID card image -> OCR

REVIEW-QUEUE DEMO FILES (need a human)
  review_invoice_missing_total.pdf   required fields missing -> review
  review_unknown_letter.pdf          type not recognised -> review
  review_invoice_poor_scan.png       low-quality scan -> review

ERROR DEMO FILES
  bad_corrupted.pdf         damaged PDF
  bad_fake.pdf              not a real PDF (wrong content)
  bad_unsupported.txt       unsupported type
  bad_blank.png             blank page -> no readable text
  bad_too_many_pages.pdf    12 pages, limit is 10
  bad_too_large.pdf         6 MB, limit is 5 MB
