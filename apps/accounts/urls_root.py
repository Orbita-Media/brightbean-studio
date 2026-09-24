from django.urls import path

from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("legal/privacy/", views.legal_privacy, name="legal_privacy"),
    path("legal/terms/", views.legal_terms, name="legal_terms"),
]
