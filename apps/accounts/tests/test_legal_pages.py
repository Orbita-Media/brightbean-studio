"""Public legal pages (privacy policy, terms of service).

TikTok's app review (and Meta/Google) require reachable, app-specific legal
URLs. They must stay public: anonymous visitors and logged-in users who have
not accepted the ToS yet must both be able to read them.
"""

import pytest
from django.urls import reverse

from apps.accounts.models import User


@pytest.mark.django_db
@pytest.mark.parametrize("name", ["legal_privacy", "legal_terms"])
def test_legal_page_public(client, name):
    resp = client.get(reverse(name))
    assert resp.status_code == 200
    body = resp.content.decode()
    assert "Orbita Media GmbH" in body
    assert "Ericusspitze 4" in body
    assert 'id="en"' in body


@pytest.mark.django_db
def test_privacy_names_tiktok_scopes(client):
    body = client.get(reverse("legal_privacy")).content.decode()
    for scope in ("user.info.basic", "video.upload", "video.publish", "video.list"):
        assert scope in body


@pytest.mark.django_db
@pytest.mark.parametrize("name", ["legal_privacy", "legal_terms"])
def test_legal_page_not_blocked_by_tos_middleware(client, name):
    user = User.objects.create_user(email="tos@example.com", password="testpass123")
    assert user.tos_accepted_at is None
    client.force_login(user)
    resp = client.get(reverse(name))
    assert resp.status_code == 200


@pytest.mark.django_db
def test_login_page_links_legal_pages(client):
    body = client.get(reverse("account_login")).content.decode()
    assert reverse("legal_privacy") in body
    assert reverse("legal_terms") in body
    assert "orbita-media.de/agb" not in body
