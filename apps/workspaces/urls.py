from django.urls import include, path

from . import views

app_name = "workspaces"

urlpatterns = [
    path("", views.workspace_list, name="list"),
    path("<uuid:workspace_id>/", views.detail, name="detail"),
    path("<uuid:workspace_id>/media/", include("apps.media_library.urls")),
]
