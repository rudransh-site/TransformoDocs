"""One-time setup that needs internet: downloads the offline models for V2 and V5."""
import os
import subprocess
import sys
import urllib.request

from django.conf import settings
from django.core.management.base import BaseCommand

TESSDATA_URL = "https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/main/{}.traineddata"


class Command(BaseCommand):
    help = "Download the spaCy model, MiniLM embeddings, Argos English<->Hindi packs and Hindi OCR data."

    def add_arguments(self, parser):
        for name in ("spacy", "minilm", "argos", "tessdata"):
            parser.add_argument(f"--skip-{name}", action="store_true")

    def step(self, name, fn):
        self.stdout.write(f"\n== {name}")
        try:
            msg = fn()
            self.stdout.write(self.style.SUCCESS(f"   OK   {msg or ''}"))
            return True
        except Exception as exc:  # noqa: BLE001
            self.stdout.write(self.style.ERROR(f"   FAILED: {exc}"))
            return False

    def handle(self, *args, **o):
        # downloads need the network: undo OFFLINE_MODE for this command only
        for var in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE"):
            os.environ.pop(var, None)
        results = []
        if not o["skip_spacy"]:
            results.append(self.step("spaCy English model (en_core_web_sm, ~13 MB)", self.spacy))
        if not o["skip_minilm"]:
            results.append(self.step("MiniLM embeddings (~90 MB, needs sentence-transformers)", self.minilm))
        if not o["skip_argos"]:
            results.append(self.step("Argos Translate English<->Hindi packs (~200 MB, needs argostranslate)", self.argos))
        if not o["skip_tessdata"]:
            results.append(self.step("Tesseract English + Hindi data (~5 MB)", self.tessdata))
        self.stdout.write("")
        if all(results):
            self.stdout.write(self.style.SUCCESS("All models downloaded. Set OFFLINE_MODE=1 in .env for the demo."))
        else:
            self.stdout.write(self.style.WARNING(
                "Some steps failed. The project still runs: missing pieces fall back or show a clear message. "
                "Fix the connection or install the missing package, then run this command again."))

    def spacy(self):
        subprocess.run([sys.executable, "-m", "spacy", "download", "en_core_web_sm"], check=True)
        return "installed"

    def minilm(self):
        from sentence_transformers import SentenceTransformer

        SentenceTransformer(getattr(settings, "EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"))
        return "downloaded"

    def argos(self):
        import argostranslate.package as pkg

        pkg.update_package_index()
        available = pkg.get_available_packages()
        done = []
        for src, tgt in (("en", "hi"), ("hi", "en")):
            match = next((p for p in available if p.from_code == src and p.to_code == tgt), None)
            if match is None:
                raise RuntimeError(f"No {src}->{tgt} package found in the Argos index")
            pkg.install_from_path(match.download())
            done.append(f"{src}->{tgt}")
        return "installed " + ", ".join(done)

    def tessdata(self):
        target = settings.BASE_DIR / "tessdata"
        target.mkdir(exist_ok=True)
        for lang in ("eng", "hin"):
            dest = target / f"{lang}.traineddata"
            if not dest.exists():
                urllib.request.urlretrieve(TESSDATA_URL.format(lang), dest)
        return f"saved in {target}"
