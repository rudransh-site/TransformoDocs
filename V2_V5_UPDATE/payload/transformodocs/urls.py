"""Root URL config."""
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("intelligence.urls")),  # V2
    path("", include("advanced.urls")),  # V5
    path("", include("core.urls")),
]

handler404 = "core.views.errors.not_found"
handler403 = "core.views.errors.permission_denied"
handler500 = "core.views.errors.server_error"
