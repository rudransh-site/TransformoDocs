import os
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from core.models import Profile
from core.constants import Role

class Command(BaseCommand):
    help = "Create or update the admin user"

    def handle(self, *args, **options):
        username = os.getenv("BOOTSTRAP_ADMIN_USERNAME")
        password = os.getenv("BOOTSTRAP_ADMIN_PASSWORD")
        email = os.getenv("BOOTSTRAP_ADMIN_EMAIL", "")

        if not username or not password:
            self.stdout.write("Admin bootstrap skipped.")
            return

        User = get_user_model()

        user, created = User.objects.get_or_create(
            **{User.USERNAME_FIELD: username},
            defaults={"email": email}
        )

        user.is_staff = True
        user.is_superuser = True
        user.set_password(password)
        user.save()

        Profile.objects.update_or_create(
            user=user,
            defaults={"role": Role.ADMIN}
        )

        self.stdout.write(
            self.style.SUCCESS("Admin account and ADMIN role configured.")
        )
