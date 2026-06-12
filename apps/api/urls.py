"""URL-Routing der externen Content-API (v1)."""

from django.urls import path

from . import views

app_name = "api"

urlpatterns = [
    path("accounts/", views.AccountListView.as_view(), name="accounts"),
    path("media/", views.MediaUploadView.as_view(), name="media_upload"),
    path("posts/", views.PostCreateView.as_view(), name="post_create"),
    path("posts/<uuid:post_id>/", views.PostDetailView.as_view(), name="post_detail"),
]
