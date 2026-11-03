from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from core.constants import Role
from core.models import Profile

DEMO_ACCOUNTS = [
    ("demo_user", "user@example.com", Role.USER, False),
    ("demo_reviewer", "reviewer@example.com", Role.REVIEWER, False),
    ("demo_admin", "admin@example.com", Role.ADMIN, True),
]


class Command(BaseCommand):
    help = "Create/reset the 3 demo accounts (one per role). Re-running resets their passwords."

    def add_arguments(self, parser):
        parser.add_argument("--password", default="Demo@12345")

    def handle(self, *args, **options):
        User = get_user_model()
        for username, email, role, is_admin in DEMO_ACCOUNTS:
            user, _ = User.objects.get_or_create(username=username, defaults={"email": email})
            user.email = email
            user.is_staff = is_admin  # lets the demo admin open /admin for data checks
            user.is_superuser = is_admin
            user.is_active = True
            user.set_password(options["password"])
            user.save()
            Profile.objects.update_or_create(user=user, defaults={"role": role})
            self.stdout.write(f"  {username:15} role={role}")
        self.stdout.write(self.style.SUCCESS(f"Done. Password for all: {options['password']}"))
