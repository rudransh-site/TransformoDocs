from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.constants import Role
from core.models import DocumentType, Profile, SystemSetting
from core.schema_spec import validate_schema
from core.seed_data import DOCUMENT_TYPES


class Command(BaseCommand):
    help = "Idempotent: seeds the 4 document types, default settings, and missing profiles."

    @transaction.atomic
    def handle(self, *args, **options):
        for spec in DOCUMENT_TYPES:
            errors = validate_schema(spec["schema"])
            if errors:
                raise CommandError(f"Invalid schema for {spec['code']}: " + "; ".join(errors))
            obj, created = DocumentType.objects.update_or_create(
                code=spec["code"],
                defaults={
                    "name": spec["name"],
                    "description": spec["description"],
                    "classification_rules": spec["classification_rules"],
                    "schema": spec["schema"],
                    "is_builtin": True,
                    "is_active": True,
                },
            )
            self.stdout.write(f"  {'created' if created else 'updated'} document type {obj.code}")

        SystemSetting.ensure_defaults()
        self.stdout.write("  default settings ensured (existing Admin edits kept)")

        User = get_user_model()
        made = 0
        for user in User.objects.filter(profile__isnull=True):
            role = Role.ADMIN if user.is_superuser else Role.USER
            Profile.objects.create(user=user, role=role)
            made += 1
        if made:
            self.stdout.write(f"  created {made} missing profile(s)")
        self.stdout.write(self.style.SUCCESS("Seed complete."))
