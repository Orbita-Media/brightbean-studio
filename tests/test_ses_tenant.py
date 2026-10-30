"""SES-Tenant nach Absenderdomain."""
from types import SimpleNamespace

from config.ses_tenant import ses_tenant, tenant_for_address


def test_tenant_nach_absenderdomain():
    assert tenant_for_address("noreply@orbita-media.de") == "orbita-media"
    assert tenant_for_address("Lisa <info@lucidpagemedia.com>") == "lucidpagemedia"
    assert tenant_for_address("x@mail.inyourcornerbook.com") == "iyc"
    assert tenant_for_address("x@example.org") is None
    assert tenant_for_address("") is None


def test_django_ses_funktion_nimmt_die_from_adresse():
    assert ses_tenant(SimpleNamespace(from_email="Orbita <noreply@orbita-media.de>"), dkim_domain=None) == "orbita-media"
