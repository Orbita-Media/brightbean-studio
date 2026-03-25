from django.urls import path

from . import views

app_name = "media_library_org"

urlpatterns = [
    path("shared/", views.shared_library_index, name="shared_index"),
    path("shared/upload/", views.shared_upload, name="shared_upload"),
    path("shared/<uuid:asset_id>/", views.shared_asset_detail, name="shared_asset_detail"),
]
