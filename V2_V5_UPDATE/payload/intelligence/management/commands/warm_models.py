import time

from django.core.management.base import BaseCommand

from intelligence.services import embeddings, nlp, translate


class Command(BaseCommand):
    help = "Load the V2 models once so the first demo request is fast. Run before the demo."

    def handle(self, *args, **options):
        for name, fn in (("spaCy entities", lambda: nlp.extract_entities("Acme Ltd paid 100 on 1 Jan 2026.")),
                         ("embeddings", lambda: embeddings.embed(["warm up"])),
                         ("translation", translate.status)):
            t = time.time()
            try:
                fn()
                self.stdout.write(self.style.SUCCESS(f"  {name:<16} ready ({time.time() - t:.1f}s)"))
            except Exception as exc:  # noqa: BLE001
                self.stdout.write(self.style.WARNING(f"  {name:<16} skipped: {exc}"))
        self.stdout.write(f"Embedding backend: {embeddings.active_backend()}")
