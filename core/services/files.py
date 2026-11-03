"""Upload validation: type, signature, size, corruption, password, page count."""
import hashlib
import io
import math
import os
import re
import zipfile
from dataclasses import dataclass

import pymupdf
from django.conf import settings
from PIL import Image

MB = 1024 * 1024
MIME = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
KIND = {".pdf": "pdf", ".docx": "docx", ".png": "image", ".jpg": "image", ".jpeg": "image"}


class UploadError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class ValidatedUpload:
    name: str
    ext: str
    kind: str
    mime: str
    size: int
    sha256: str
    page_count: int
    data: bytes


def kind_of(filename):
    return KIND.get(os.path.splitext(filename or "")[1].lower(), "")


def safe_name(name):
    name = os.path.basename((name or "").replace("\\", "/"))
    name = re.sub(r"[\x00-\x1f]", "", name).strip()
    return name[:255] or "upload"


def validate_upload(uploaded):
    name = safe_name(getattr(uploaded, "name", ""))
    ext = os.path.splitext(name)[1].lower()
    if ext not in settings.ALLOWED_EXTENSIONS:
        raise UploadError("UNSUPPORTED_TYPE", "Unsupported file type. Please upload PDF, DOCX, PNG or JPG.")
    size = getattr(uploaded, "size", 0) or 0
    if size == 0:
        raise UploadError("EMPTY_FILE", "This file is empty.")
    if size > settings.MAX_UPLOAD_BYTES:
        raise UploadError(
            "FILE_TOO_LARGE",
            f"File is {size / MB:.1f} MB. Maximum is {settings.MAX_UPLOAD_BYTES // MB} MB.",
        )
    uploaded.seek(0)
    data = uploaded.read()
    kind = KIND[ext]
    pages = 1

    if kind == "pdf":
        if b"%PDF" not in data[:1024]:
            raise UploadError("UNSUPPORTED_TYPE", "Unsupported file type. This file is not a real PDF.")
        pages = _check_pdf(data)
    elif kind == "docx":
        if data[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":  # OLE container = encrypted/legacy
            raise UploadError("PASSWORD_PROTECTED",
                              "This file is password-protected or in an unsupported Word format.")
        pages = _check_docx(data)
    else:
        sig_ok = data.startswith(b"\x89PNG\r\n\x1a\n") if ext == ".png" else data.startswith(b"\xff\xd8\xff")
        if not sig_ok:
            raise UploadError("UNSUPPORTED_TYPE", "Unsupported file type. The file content does not match its extension.")
        _check_image(data)

    return ValidatedUpload(name=name, ext=ext, kind=kind, mime=MIME[ext], size=size,
                           sha256=hashlib.sha256(data).hexdigest(), page_count=pages, data=data)


def _corrupt():
    return UploadError("CORRUPTED", "We couldn't open this file. It may be damaged.")


def _check_pdf(data):
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception:  # noqa: BLE001
        raise _corrupt() from None
    try:
        if doc.needs_pass or doc.is_encrypted:
            raise UploadError("PASSWORD_PROTECTED",
                              "This file is password-protected. Please remove the password and upload again.")
        n = doc.page_count
        if n < 1:
            raise _corrupt()
        if n > settings.MAX_PAGES:
            raise UploadError("TOO_MANY_PAGES", f"This file has {n} pages. Maximum is {settings.MAX_PAGES}.")
        try:
            doc.load_page(0).get_text("text")
        except Exception:  # noqa: BLE001
            raise _corrupt() from None
        return n
    finally:
        doc.close()


def _check_docx(data):
    if not zipfile.is_zipfile(io.BytesIO(data)):
        raise _corrupt()
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = z.namelist()
            if "word/document.xml" not in names:
                raise UploadError("UNSUPPORTED_TYPE", "Unsupported file type. This is not a Word (.docx) document.")
            import docx

            docx.Document(io.BytesIO(data))
            pages = 0
            if "docProps/app.xml" in names:
                m = re.search(rb"<Pages>(\d+)</Pages>", z.read("docProps/app.xml"))
                pages = int(m.group(1)) if m else 0
    except UploadError:
        raise
    except Exception:  # noqa: BLE001
        raise _corrupt() from None
    if pages > settings.MAX_PAGES:
        raise UploadError("TOO_MANY_PAGES", f"This file has {pages} pages. Maximum is {settings.MAX_PAGES}.")
    return pages or 1


def _check_image(data):
    try:
        img = Image.open(io.BytesIO(data))
        img.verify()
        img = Image.open(io.BytesIO(data))
        w, h = img.size
    except Exception:  # noqa: BLE001
        raise _corrupt() from None
    if w * h > 40_000_000:
        raise UploadError("IMAGE_TOO_LARGE", "This image has too many pixels. Please use a smaller scan.")
