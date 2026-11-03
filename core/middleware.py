"""Database outage handling: friendly 503 instead of a crash page."""
import logging
import threading
from datetime import datetime

from django.contrib.sessions.middleware import SessionMiddleware
from django.db import InterfaceError, OperationalError, connection
from django.http import JsonResponse
from django.template.loader import render_to_string
from django.http import HttpResponse

logger = logging.getLogger("transformodocs.db")
_lock = threading.Lock()
_down_since = None


def _unavailable(request):
    if (request.headers.get("x-requested-with") == "XMLHttpRequest" or request.path.startswith("/api/")
            or "application/json" in request.headers.get("accept", "")):
        return JsonResponse({"error": "The system is temporarily unavailable. Please try again in a minute.",
                             "code": "DB_UNAVAILABLE"}, status=503)
    return HttpResponse(render_to_string("errors/503.html"), status=503)


class DatabaseErrorMiddleware:
    """1) Tries the DB connection (with one quick retry) before the view runs.
    2) Turns database errors raised inside views into a 503 page.
    Outages are logged to logs/app.log (the DB itself is unavailable); a notice
    is written for the Admin as soon as the database is back."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        global _down_since
        if request.path.startswith(("/static/", "/favicon")):
            return self.get_response(request)
        try:
            connection.ensure_connection()
        except (OperationalError, InterfaceError):
            connection.close()
            try:
                connection.ensure_connection()
            except (OperationalError, InterfaceError) as exc:
                with _lock:
                    if _down_since is None:
                        _down_since = datetime.now()
                        logger.error("DATABASE UNAVAILABLE: %s", exc)
                return _unavailable(request)
        if _down_since is not None:
            with _lock:
                since, _down_since = _down_since, None
            try:
                from core.models import Notification

                Notification.raise_alert(
                    "DB_RECOVERED", f"The database was unavailable from {since:%H:%M:%S} to {datetime.now():%H:%M:%S}.")
            except Exception:  # noqa: BLE001
                pass
        return self.get_response(request)

    def process_exception(self, request, exception):
        if isinstance(exception, (OperationalError, InterfaceError)):
            logger.error("Database error during %s: %s", request.path, exception)
            return _unavailable(request)
        return None


class SafeSessionMiddleware(SessionMiddleware):
    """Django's session middleware writes the session when sending every response.
    If the database is down that write would turn our friendly 503 into a crash,
    so a database error here is logged and the response is returned unchanged
    (the user's session cookie is kept)."""

    def process_response(self, request, response):
        try:
            return super().process_response(request, response)
        except (OperationalError, InterfaceError) as exc:
            logger.error("Could not save session during a database outage: %s", exc)
            return response
