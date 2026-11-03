"""Create the V5 demo documents: Hindi text and a handwritten note (all fictional)."""
from pathlib import Path

import numpy as np
from django.conf import settings
from django.core.management.base import BaseCommand
from PIL import Image, ImageDraw, ImageFont

HINDI_CERT = [
    "प्रमाणपत्र", "", "यह प्रमाणित किया जाता है कि", "प्रिया शर्मा", "ने पायथन प्रोग्रामिंग पाठ्यक्रम सफलतापूर्वक पूरा किया है",
    "", "जारीकर्ता: इंदौर टेक अकादमी", "दिनांक: 12/09/2026", "प्रमाणपत्र संख्या: CERT-2026-0457",
]
HINDI_NOTICE = [
    "सूचना", "", "सोसाइटी का बगीचा अगले सप्ताह रखरखाव के लिए बंद रहेगा।", "कृपया अपने पालतू जानवरों को घर के अंदर रखें।",
    "आपके सहयोग के लिए धन्यवाद।", "", "निवासी समिति",
]
HANDWRITTEN = [
    "Received from Rahul Verma", "the sum of Rupees 4500", "Date: 14/10/2026", "Receipt No: 2231",
    "Paid in cash, with thanks.", "Signed: Anil Mehta",
]


def page(lines, font_path, size, width=1600, margin=120, gap=1.9, angle=0.0, noise=0):
    font_kw = {}
    try:
        font_kw["layout_engine"] = ImageFont.Layout.RAQM  # proper Hindi shaping (needs libraqm)
    except AttributeError:
        pass
    try:
        font = ImageFont.truetype(font_path, size, **font_kw)
    except Exception:  # noqa: BLE001 - RAQM not available
        font = ImageFont.truetype(font_path, size)
    height = int(margin * 2 + len(lines) * size * gap)
    img = Image.new("L", (width, height), 255)
    d = ImageDraw.Draw(img)
    y = margin
    for line in lines:
        if line:
            d.text((margin, y), line, font=font, fill=20)
        y += size * gap
    if angle:
        img = img.rotate(angle, expand=False, fillcolor=255, resample=Image.BICUBIC)
    if noise:
        arr = np.asarray(img, dtype=np.int16) + np.random.default_rng(5).normal(0, noise, (img.height, img.width))
        img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
    return img


class Command(BaseCommand):
    help = "Create Hindi and handwriting sample documents in samples/."

    def add_arguments(self, parser):
        parser.add_argument("--out", default=str(settings.BASE_DIR / "samples"))

    def handle(self, *args, **options):
        out = Path(options["out"])
        out.mkdir(parents=True, exist_ok=True)
        fonts = settings.BASE_DIR / "static" / "fonts"
        page(HINDI_CERT, str(fonts / "Hind-Regular.ttf"), 56).save(out / "hindi_certificate.png")
        page(HINDI_NOTICE, str(fonts / "Hind-Regular.ttf"), 54).save(out / "hindi_notice.png")
        page(HANDWRITTEN, str(fonts / "PatrickHand-Regular.ttf"), 74, angle=1.2, noise=6).save(out / "handwritten_receipt.png")
        self.stdout.write(self.style.SUCCESS(f"V5 samples written to {out}"))
