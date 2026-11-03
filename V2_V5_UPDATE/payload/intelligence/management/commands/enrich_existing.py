from django.core.management.base import BaseCommand

from core.models import DocumentVersion
from intelligence.models import DocumentInsight
from intelligence.services.enrich import enrich_version


class Command(BaseCommand):
    help = "Create V2 insights (summary, entities, tags, embeddings) for documents processed before V2 was installed."

    def add_arguments(self, parser):
        parser.add_argument("--all", action="store_true", help="Rebuild insights even for documents that have them.")

    def handle(self, *args, **options):
        done = DocumentInsight.objects.values_list("version_id", flat=True)
        qs = DocumentVersion.objects.exclude(extracted_text="").select_related("document__doc_type")
        if not options["all"]:
            qs = qs.exclude(pk__in=list(done))
        n = 0
        for v in qs:
            if enrich_version(v, doc=v.document):
                n += 1
        self.stdout.write(self.style.SUCCESS(f"Insights created for {n} document(s)."))
