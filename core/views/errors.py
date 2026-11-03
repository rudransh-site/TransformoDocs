import sys

from django.db import InterfaceError, OperationalError
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.contrib import messages

from core.services.permissions import is_ajax


def csrf_failure(request, reason=""):
    msg = "Your session has expired. Please refresh the page and log in again."
    if is_ajax(request):
        return JsonResponse({"error": msg, "code": "AUTH_REQUIRED"}, status=403)
    messages.warning(request, msg)
    return redirect("login")


def not_found(request, exception=None):
    return render(request, "errors/404.html", status=404)


def server_error(request):
    exc = sys.exc_info()[1]
    if isinstance(exc, (OperationalError, InterfaceError)):
        return HttpResponse(render_to_string("errors/503.html"), status=503)
    return HttpResponse(render_to_string("errors/500.html"), status=500)


def permission_denied(request, exception=None):
    return render(request, "errors/403.html", status=403)
