from django.urls import path

from . import views

urlpatterns = [
    path("generator/", views.generator, name="generator"),
    path("generator/preview/", views.preview, name="generator_preview"),
    path("generator/export/<str:fmt>/", views.export, name="generator_export"),
    path("generator/save/", views.save_to_system, name="generator_save"),
    path("generator/prefill/<int:doc_id>/", views.prefill, name="generator_prefill"),
]
