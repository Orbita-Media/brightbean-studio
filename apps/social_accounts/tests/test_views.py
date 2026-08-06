"""Tests for social_accounts views."""

from unittest.mock import MagicMock, patch

import pytest
from django.core import signing
from django.urls import reverse

from apps.social_accounts.models import SocialAccount
from apps.social_accounts.views import OAUTH_SESSION_KEY, _sign_state, _unsign_state
from providers.types import AccountProfile, OAuthTokens


@pytest.fixture
def workspace(db, organization):
    from apps.workspaces.models import Workspace

    return Workspace.objects.create(name="Test WS", organization=organization)


@pytest.fixture
def manager_setup(db, user, organization, workspace):
    """Set up user as org owner + workspace manager."""
    from apps.members.models import OrgMembership, WorkspaceMembership

    OrgMembership.objects.create(user=user, organization=organization, org_role="owner")
    WorkspaceMembership.objects.create(user=user, workspace=workspace, workspace_role="manager")
    return user


@pytest.fixture
def authenticated_client(client, user, manager_setup):
    client.force_login(user)
    return client


class TestOAuthState:
    """Test OAuth state parameter signing and validation."""

    def test_sign_and_unsign_state(self):
        state = _sign_state("ws-123", "facebook", "user-456", "nonce-789")
        data = _unsign_state(state)
        assert data["workspace_id"] == "ws-123"
        assert data["platform"] == "facebook"
        assert data["user_id"] == "user-456"
        assert data["nonce"] == "nonce-789"

    def test_expired_state_raises(self):
        state = _sign_state("ws-123", "facebook", "user-456", "nonce")
        with pytest.raises(signing.BadSignature):
            signing.loads(state, salt="social-oauth-state", max_age=0)

    def test_tampered_state_raises(self):
        state = _sign_state("ws-123", "facebook", "user-456", "nonce")
        with pytest.raises(signing.BadSignature):
            _unsign_state(state + "tampered")


@pytest.mark.django_db
class TestAccountListView:
    def test_requires_authentication(self, client, workspace):
        url = reverse("social_accounts:list", kwargs={"workspace_id": workspace.id})
        response = client.get(url)
        assert response.status_code == 302
        assert "/accounts/" in response.url

    def test_returns_200_for_authenticated_user(self, authenticated_client, workspace):
        url = reverse("social_accounts:list", kwargs={"workspace_id": workspace.id})
        response = authenticated_client.get(url)
        assert response.status_code == 200

    def test_shows_connected_accounts(self, authenticated_client, workspace):
        SocialAccount.objects.create(
            workspace=workspace,
            platform="facebook",
            account_platform_id="123",
            account_name="My Facebook Page",
        )
        url = reverse("social_accounts:list", kwargs={"workspace_id": workspace.id})
        response = authenticated_client.get(url)
        assert b"My Facebook Page" in response.content

    def test_shows_empty_state(self, authenticated_client, workspace):
        url = reverse("social_accounts:list", kwargs={"workspace_id": workspace.id})
        response = authenticated_client.get(url)
        assert b"No accounts connected yet" in response.content


@pytest.mark.django_db
class TestConnectPlatformView:
    def test_get_shows_platform_grid(self, authenticated_client, workspace):
        url = reverse("social_accounts:connect", kwargs={"workspace_id": workspace.id})
        response = authenticated_client.get(url)
        assert response.status_code == 200
        assert b"Connect a Platform" in response.content

    def test_post_invalid_platform(self, authenticated_client, workspace):
        url = reverse("social_accounts:connect", kwargs={"workspace_id": workspace.id})
        response = authenticated_client.post(url, {"platform": "twitter"})
        assert response.status_code == 302

    def test_post_bluesky_redirects_to_form(self, authenticated_client, workspace):
        from apps.credentials.models import PlatformCredential

        PlatformCredential.objects.create(
            organization=workspace.organization,
            platform="bluesky",
            credentials={"handle": "test"},
            is_configured=True,
        )
        url = reverse("social_accounts:connect", kwargs={"workspace_id": workspace.id})
        response = authenticated_client.post(url, {"platform": "bluesky"})
        assert response.status_code == 302
        assert "bluesky" in response.url

    def test_pkce_connect_generates_and_forwards_verifier(self, authenticated_client, workspace):
        """A PKCE provider (TikTok) gets a code_verifier stashed in the session
        and forwarded to get_auth_url so it can derive the code_challenge."""
        from apps.credentials.models import PlatformCredential

        PlatformCredential.objects.create(
            organization=workspace.organization,
            platform="tiktok",
            credentials={"client_key": "k", "client_secret": "s"},
            is_configured=True,
        )

        mock_provider = MagicMock()
        mock_provider.uses_pkce = True
        mock_provider.get_auth_url.return_value = "https://www.tiktok.com/v2/auth/authorize/?ok=1"

        url = reverse("social_accounts:connect", kwargs={"workspace_id": workspace.id})
        with patch("apps.social_accounts.views._get_provider_for_platform", return_value=mock_provider):
            response = authenticated_client.post(url, {"platform": "tiktok"})

        assert response.status_code == 302
        assert response.url == "https://www.tiktok.com/v2/auth/authorize/?ok=1"

        verifier = authenticated_client.session[OAUTH_SESSION_KEY]["code_verifier"]
        assert verifier  # non-empty
        _, kwargs = mock_provider.get_auth_url.call_args
        assert kwargs["code_verifier"] == verifier

    def test_non_pkce_connect_omits_verifier(self, authenticated_client, workspace):
        """A non-PKCE provider stores code_verifier=None and is called without it."""
        from apps.credentials.models import PlatformCredential

        PlatformCredential.objects.create(
            organization=workspace.organization,
            platform="facebook",
            credentials={"client_id": "i", "client_secret": "s"},
            is_configured=True,
        )

        mock_provider = MagicMock()
        mock_provider.uses_pkce = False
        mock_provider.get_auth_url.return_value = "https://facebook.example/auth"

        url = reverse("social_accounts:connect", kwargs={"workspace_id": workspace.id})
        with patch("apps.social_accounts.views._get_provider_for_platform", return_value=mock_provider):
            response = authenticated_client.post(url, {"platform": "facebook"})

        assert response.status_code == 302
        assert authenticated_client.session[OAUTH_SESSION_KEY]["code_verifier"] is None
        _, kwargs = mock_provider.get_auth_url.call_args
        assert "code_verifier" not in kwargs


@pytest.mark.django_db
class TestReconnectView:
    def test_pkce_reconnect_generates_and_forwards_verifier(self, authenticated_client, workspace):
        """Reconnecting a TikTok account must regenerate + forward a PKCE verifier;
        reconnect previously sent no code_challenge -> TikTok errCode 10007."""
        account = SocialAccount.objects.create(
            workspace=workspace,
            platform="tiktok",
            account_platform_id="open-1",
            account_name="My TikTok",
        )

        mock_provider = MagicMock()
        mock_provider.uses_pkce = True
        mock_provider.get_auth_url.return_value = "https://www.tiktok.com/v2/auth/authorize/?ok=1"

        url = reverse(
            "social_accounts:reconnect",
            kwargs={"workspace_id": workspace.id, "account_id": account.id},
        )
        with patch("apps.social_accounts.views._get_provider_for_platform", return_value=mock_provider):
            response = authenticated_client.post(url)

        assert response.status_code == 302
        verifier = authenticated_client.session[OAUTH_SESSION_KEY]["code_verifier"]
        assert verifier  # non-empty
        _, kwargs = mock_provider.get_auth_url.call_args
        assert kwargs["code_verifier"] == verifier


@pytest.mark.django_db
class TestOAuthCallbackView:
    def test_error_parameter_shows_message(self, authenticated_client):
        url = reverse("social_accounts:oauth_callback", kwargs={"platform": "facebook"})
        response = authenticated_client.get(url, {"error": "access_denied", "error_description": "User denied"})
        assert response.status_code == 302

    def test_missing_code_shows_error(self, authenticated_client):
        url = reverse("social_accounts:oauth_callback", kwargs={"platform": "facebook"})
        response = authenticated_client.get(url, {"state": "somestate"})
        assert response.status_code == 302

    def test_invalid_state_shows_error(self, authenticated_client):
        url = reverse("social_accounts:oauth_callback", kwargs={"platform": "facebook"})
        response = authenticated_client.get(url, {"code": "abc123", "state": "invalid_state"})
        assert response.status_code == 302

    def test_instagram_redirects_to_account_selection(self, authenticated_client, workspace, user):
        nonce = "nonce-123"
        state = _sign_state(workspace.id, "instagram", user.id, nonce)
        session = authenticated_client.session
        session[OAUTH_SESSION_KEY] = {"nonce": nonce}
        session.save()

        mock_provider = MagicMock()
        mock_provider.exchange_code.return_value = OAuthTokens(access_token="user-token", refresh_token="refresh")
        mock_provider.get_user_pages.return_value = [
            {
                "id": "17841400000000000",
                "name": "Brightbean",
                "handle": "brightbean",
                "access_token": "page-token",
            }
        ]
        url = reverse("social_accounts:oauth_callback", kwargs={"platform": "instagram"})

        with patch("apps.social_accounts.views._get_provider_for_platform", return_value=mock_provider):
            response = authenticated_client.get(url, {"code": "abc123", "state": state})

        assert response.status_code == 302
        assert response.url == reverse("social_accounts:select_account")
        mock_provider.get_profile.assert_not_called()
        page_data = authenticated_client.session["oauth_page_select"]
        assert page_data["platform"] == "instagram"
        assert page_data["pages"][0]["id"] == "17841400000000000"

    def test_instagram_without_accounts_writes_the_finding_into_the_log(
        self, authenticated_client, workspace, user, caplog
    ):
        """Der eigentliche Zweck der Erhebung: Der Befund muss im Protokoll landen.

        Und zwar vollstaendig genug, um den Fall zu entscheiden - und ohne
        Zugangstoken, auch nicht den des Nutzers, der hier durchgereicht wird.
        """
        import logging

        nonce = "nonce-diag"
        state = _sign_state(workspace.id, "instagram", user.id, nonce)
        session = authenticated_client.session
        session[OAUTH_SESSION_KEY] = {"nonce": nonce}
        session.save()

        mock_provider = MagicMock()
        mock_provider.exchange_code.return_value = OAuthTokens(access_token="EAAgeheimestoken", refresh_token="r")
        mock_provider.get_user_pages.return_value = []
        mock_provider.diagnose_pages.return_value = {
            "verdict": "pages_without_instagram",
            "permissions": {"granted": ["pages_show_list"], "declined": []},
            "pages": {"count": 1, "items": [{"id": "page-1", "name": "Orbita Media Verlag"}]},
            "errors": [],
        }

        url = reverse("social_accounts:oauth_callback", kwargs={"platform": "instagram"})
        with (
            caplog.at_level(logging.WARNING, logger="apps.social_accounts.views"),
            patch("apps.social_accounts.views._get_provider_for_platform", return_value=mock_provider),
        ):
            response = authenticated_client.get(url, {"code": "abc123", "state": state})

        assert response.status_code == 302
        logged = "\n".join(record.getMessage() for record in caplog.records)
        assert "OAuth connect returned no accounts for instagram" in logged
        assert "pages_without_instagram" in logged
        assert "Orbita Media Verlag" in logged
        assert "pages_show_list" in logged
        assert "EAAgeheimestoken" not in logged

    def test_tiktok_callback_replays_pkce_verifier(self, authenticated_client, workspace, user):
        """The verifier stashed at connect is read from the session and replayed
        on the TikTok token exchange (callback arrives at the ``social1`` slug)."""
        nonce = "nonce-tiktok"
        verifier = "stored-code-verifier"
        state = _sign_state(workspace.id, "tiktok", user.id, nonce)
        session = authenticated_client.session
        session[OAUTH_SESSION_KEY] = {"nonce": nonce, "code_verifier": verifier}
        session.save()

        mock_provider = MagicMock()
        mock_provider.exchange_code.return_value = OAuthTokens(access_token="tok", refresh_token="r", expires_in=3600)
        mock_provider.get_profile.return_value = AccountProfile(platform_id="open-id-1", name="Test TikTok")

        url = reverse("social_accounts:oauth_callback", kwargs={"platform": "social1"})
        with patch("apps.social_accounts.views._get_provider_for_platform", return_value=mock_provider):
            response = authenticated_client.get(url, {"code": "auth-code", "state": state})

        assert response.status_code == 302
        mock_provider.exchange_code.assert_called_once()
        _, kwargs = mock_provider.exchange_code.call_args
        assert kwargs["code_verifier"] == verifier


@pytest.mark.django_db
class TestSelectAccountView:
    def test_blank_page_access_token_falls_back_to_user_token(self, authenticated_client, workspace):
        session = authenticated_client.session
        session["oauth_page_select"] = {
            "workspace_id": str(workspace.id),
            "platform": "instagram",
            "user_tokens": {
                "access_token": "user-token",
                "refresh_token": "refresh-token",
            },
            "pages": [
                {
                    "id": "17841400000000000",
                    "name": "Brightbean",
                    "handle": "brightbean",
                    "access_token": "",
                }
            ],
        }
        session.save()

        url = reverse("social_accounts:select_account")
        response = authenticated_client.post(url, {"selected_pages": ["17841400000000000"]})

        assert response.status_code == 302
        account = SocialAccount.objects.get(
            workspace=workspace,
            platform="instagram",
            account_platform_id="17841400000000000",
        )
        assert account.oauth_access_token == "user-token"
        assert account.oauth_refresh_token == "refresh-token"

    def test_facebook_page_without_access_token_is_not_connected(self, authenticated_client, workspace):
        session = authenticated_client.session
        session["oauth_page_select"] = {
            "workspace_id": str(workspace.id),
            "platform": "facebook",
            "user_tokens": {
                "access_token": "user-token",
                "refresh_token": "refresh-token",
            },
            "pages": [
                {
                    "id": "page-1",
                    "name": "Brightbean Page",
                    "access_token": "",
                }
            ],
        }
        session.save()

        url = reverse("social_accounts:select_account")
        response = authenticated_client.post(url, {"selected_pages": ["page-1"]})

        assert response.status_code == 302
        assert not SocialAccount.objects.filter(
            workspace=workspace,
            platform="facebook",
            account_platform_id="page-1",
        ).exists()


@pytest.mark.django_db
class TestDisconnectView:
    def test_disconnect_removes_account(self, authenticated_client, workspace):
        account = SocialAccount.objects.create(
            workspace=workspace,
            platform="facebook",
            account_platform_id="123",
            account_name="Test Page",
            oauth_access_token="token123",
        )
        url = reverse(
            "social_accounts:disconnect",
            kwargs={"workspace_id": workspace.id, "account_id": account.id},
        )
        with patch("apps.social_accounts.views._get_provider_for_platform") as mock:
            mock_provider = MagicMock()
            mock_provider.revoke_token.return_value = True
            mock.return_value = mock_provider
            response = authenticated_client.post(url)

        assert response.status_code == 302
        assert SocialAccount.objects.filter(pk=account.pk).count() == 0

    def test_disconnect_requires_post(self, authenticated_client, workspace):
        account = SocialAccount.objects.create(
            workspace=workspace,
            platform="facebook",
            account_platform_id="123",
            account_name="Test Page",
        )
        url = reverse(
            "social_accounts:disconnect",
            kwargs={"workspace_id": workspace.id, "account_id": account.id},
        )
        response = authenticated_client.get(url)
        assert response.status_code == 405


@pytest.mark.django_db
class TestBlueskyConnectView:
    def test_get_shows_form(self, authenticated_client, workspace):
        url = reverse(
            "social_accounts:connect_bluesky",
            kwargs={"workspace_id": workspace.id},
        )
        response = authenticated_client.get(url)
        assert response.status_code == 200
        assert b"Connect Bluesky" in response.content

    def test_post_requires_handle_and_password(self, authenticated_client, workspace):
        url = reverse(
            "social_accounts:connect_bluesky",
            kwargs={"workspace_id": workspace.id},
        )
        response = authenticated_client.post(url, {"handle": "", "app_password": ""})
        assert response.status_code == 200


@pytest.mark.django_db
class TestMastodonConnectView:
    def test_get_shows_form(self, authenticated_client, workspace):
        url = reverse(
            "social_accounts:connect_mastodon",
            kwargs={"workspace_id": workspace.id},
        )
        response = authenticated_client.get(url)
        assert response.status_code == 200
        assert b"Connect Mastodon" in response.content


class TestNoAccountsWarning:
    """Die Meldung nach einer Anbindung ohne Treffer muss den Fall benennen."""

    def _provider(self, diagnostics):
        provider = MagicMock()
        provider.diagnose_pages.return_value = diagnostics
        return provider

    def test_instagram_pages_without_instagram_names_the_pages(self):
        from apps.social_accounts.views import _no_accounts_warning

        warning = _no_accounts_warning(
            self._provider(
                {
                    "verdict": "pages_without_instagram",
                    "pages": {"count": 1, "items": [{"id": "page-1", "name": "Orbita Media Verlag"}]},
                }
            ),
            "instagram",
            "user-token",
        )

        assert "Orbita Media Verlag" in warning
        assert "Linked accounts" in warning
        assert "no Page at all" not in warning

    def test_instagram_without_any_page_says_so(self):
        from apps.social_accounts.views import _no_accounts_warning

        warning = _no_accounts_warning(
            self._provider({"verdict": "no_pages", "pages": {"count": 0, "items": []}}),
            "instagram",
            "user-token",
        )

        assert "no Page at all" in warning
        assert "Linked accounts" not in warning

    def test_instagram_with_a_linked_account_points_at_the_account_type(self):
        """Verknüpfung da, Konto trotzdem unbrauchbar: dann liegt es am Konto."""
        from apps.social_accounts.views import _no_accounts_warning

        warning = _no_accounts_warning(
            self._provider(
                {
                    "verdict": "pages_with_instagram",
                    "pages": {
                        "count": 1,
                        "items": [
                            {
                                "id": "page-1",
                                "name": "Orbita Media Verlag",
                                "connected_instagram_account": "17841466348000992",
                            }
                        ],
                    },
                }
            ),
            "instagram",
            "user-token",
        )

        assert "17841466348000992" in warning
        assert "Switch to professional account" in warning
        assert "Linked accounts" not in warning

    def test_instagram_without_a_diagnosis_stays_generic(self):
        from apps.social_accounts.views import _no_accounts_warning

        provider = MagicMock()
        provider.diagnose_pages.side_effect = RuntimeError("Graph nicht erreichbar")

        warning = _no_accounts_warning(provider, "instagram", "user-token")

        assert "did not say why" in warning

    def test_linkedin_company_keeps_its_own_wording(self):
        from apps.social_accounts.views import _no_accounts_warning

        provider = MagicMock()
        warning = _no_accounts_warning(provider, "linkedin_company", "user-token")

        assert "Company Pages" in warning
        provider.diagnose_pages.assert_not_called()


@pytest.mark.django_db
class TestThreadsNeedsItsOwnAppId:
    def test_connect_without_threads_credentials_names_the_missing_app_id(self, authenticated_client, workspace):
        url = reverse("social_accounts:connect", kwargs={"workspace_id": workspace.id})
        response = authenticated_client.post(url, {"platform": "threads"}, follow=True)

        text = " ".join(str(m) for m in response.context["messages"])
        assert "Threads App ID" in text
        assert "Facebook App ID does not work" in text

    def test_authorization_url_failure_does_not_send_the_user_to_the_platform(self, authenticated_client, workspace):
        """Scheitert der Aufbau der Adresse, bleibt der Nutzer im Verteiler."""
        from apps.credentials.models import PlatformCredential
        from providers.exceptions import OAuthError

        PlatformCredential.objects.create(
            organization=workspace.organization,
            platform="threads",
            credentials={"app_id": "", "app_secret": ""},
            is_configured=True,
        )

        mock_provider = MagicMock()
        mock_provider.uses_pkce = False
        mock_provider.get_auth_url.side_effect = OAuthError("Threads needs its own Threads App ID")

        url = reverse("social_accounts:connect", kwargs={"workspace_id": workspace.id})
        with patch("apps.social_accounts.views._get_provider_for_platform", return_value=mock_provider):
            response = authenticated_client.post(url, {"platform": "threads"}, follow=True)

        assert response.status_code == 200
        text = " ".join(str(m) for m in response.context["messages"])
        assert "Threads App ID" in text
