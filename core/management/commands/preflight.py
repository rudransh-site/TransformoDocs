import sys

from django.core.management.base import BaseCommand

from core.models import Notification
from core.services import health


class Command(BaseCommand):
    help = "Check that the machine is ready (database, Tesseract, libraries, folders). Run before every demo."

    def handle(self, *args, **options):
        checks = health.run_checks()
        failed = 0
        self.stdout.write("")
        for c in checks:
            if c["ok"]:
                self.stdout.write(self.style.SUCCESS(f"  PASS  {c['name']:<16} {c['detail']}"))
            else:
                tag = "FAIL" if c["critical"] else "WARN"
                style = self.style.ERROR if c["critical"] else self.style.WARNING
                self.stdout.write(style(f"  {tag}  {c['name']:<16} {c['detail']}"))
                self.stdout.write(f"        fix: {c['fix']}")
                if c["critical"]:
                    failed += 1
                    try:
                        Notification.raise_alert(f"PREFLIGHT_{c['name'].upper().replace(' ', '_')}",
                                                 f"Preflight: {c['name']} failed. {c['detail']}", severity="CRITICAL")
                    except Exception:  # noqa: BLE001 - e.g. the database itself is down
                        pass
        self.stdout.write("")
        if failed:
            self.stdout.write(self.style.ERROR(f"{failed} critical check(s) failed. Fix them before the demo."))
            sys.exit(1)
        self.stdout.write(self.style.SUCCESS("All critical checks passed. Ready to demo."))
