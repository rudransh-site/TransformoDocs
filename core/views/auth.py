from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.views import LoginView
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from core import constants as C
from core.forms import RegisterForm
from core.models import AuditLog


def home(request):
    return redirect("dashboard" if request.user.is_authenticated else "login")


class AppLoginView(LoginView):
    template_name = "registration/login.html"
    redirect_authenticated_user = True


def register(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    form = RegisterForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save(commit=False)
        user.email = form.cleaned_data["email"]
        user.save()  # the signal creates a Profile with role USER
        AuditLog.record(C.AuditAction.REGISTER, user=user, request=request, details={"username": user.username})
        login(request, user)
        messages.success(request, "Welcome! Your account has been created.")
        return redirect("dashboard")
    return render(request, "registration/register.html", {"form": form})


@require_POST
def logout_view(request):
    logout(request)
    messages.info(request, "You have been logged out.")
    return redirect("login")
