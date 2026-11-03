from django.urls import path

from . import views

urlpatterns = [
    path("api/documents/<int:pk>/chat/", views.ChatView.as_view(), name="api_chat"),
    path("api/documents/<int:pk>/translate/", views.TranslateView.as_view(), name="api_translate"),
    path("api/documents/<int:pk>/insights/", views.RegenerateView.as_view(), name="api_insights"),
    path("api/documents/<int:pk>/tags/", views.TagsView.as_view(), name="api_tags"),
    path("api/documents/<int:pk>/tags/<int:tag_id>/", views.TagDeleteView.as_view(), name="api_tag_delete"),
]
