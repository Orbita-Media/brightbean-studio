"""Threads-Token verlängern und Meta-Drossel (24.11.2026).

1. Threads-Konten haben kein Refresh-Token: verlängert wird mit dem Access-Token
   selbst. Die Zustandsprüfung prüfte das leere oauth_refresh_token und
   verlängerte nie; das Verlagskonto lief nach 60 Tagen ab.
2. Meta antwortet bei Drosselung mit 403 und Code 4 ("Application request limit
   reached"). Das galt als Anmeldefehler, Konten wurden als getrennt markiert und
   Beiträge verbrauchten ihre drei Versuche in 36 Minuten.
"""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import pytest
from django.utils import timezone

import providers.base as base
from apps.analytics.tasks import _post_cadence_due
from apps.publisher import drossel
from apps.publisher.engine import PublishEngine
from apps.publisher.models import Drossel
from apps.social_accounts.models import SocialAccount
from providers.exceptions import RateLimitError
from providers.types import OAuthTokens


@pytest.fixture
def workspace(db, organization):
    from apps.workspaces.models import Workspace

    return Workspace.objects.create(name="Drossel WS", organization=organization)


@pytest.fixture
def threads_account(workspace):
    return SocialAccount.objects.create(
        workspace=workspace,
        platform="threads",
        account_platform_id="th-1",
        account_name="Orbita Threads",
        oauth_access_token="long-lived-alt",
        oauth_refresh_token="",
        token_expires_at=timezone.now() + timedelta(days=3),
        connection_status=SocialAccount.ConnectionStatus.CONNECTED,
    )


# --- 1. Threads-Verlängerung ---------------------------------------------


@pytest.mark.django_db
def test_refresh_credential_ist_access_token_bei_threads(threads_account):
    assert threads_account.refresh_credential == "long-lived-alt"


@pytest.mark.django_db
def test_refresh_credential_bleibt_refresh_token_bei_anderen(workspace):
    yt = SocialAccount.objects.create(
        workspace=workspace,
        platform="youtube",
        account_platform_id="yt",
        account_name="YT",
        oauth_access_token="a",
        oauth_refresh_token="r",
    )
    assert yt.refresh_credential == "r"


@pytest.mark.django_db
def test_zustandspruefung_verlaengert_threads_ohne_refresh_token(threads_account):
    from apps.social_accounts.tasks import check_social_account_health

    provider = MagicMock()
    provider.refresh_token.return_value = OAuthTokens(access_token="long-lived-neu", expires_in=60 * 86400)
    provider.get_profile.return_value = SimpleNamespace(follower_count=5, avatar_url="", name="", handle="")
    with patch("providers.get_provider", return_value=provider):
        check_social_account_health.now(str(threads_account.id))

    provider.refresh_token.assert_called_once_with("long-lived-alt")
    threads_account.refresh_from_db()
    assert threads_account.oauth_access_token == "long-lived-neu"
    assert threads_account.token_expires_at > timezone.now() + timedelta(days=59)
    assert threads_account.connection_status == SocialAccount.ConnectionStatus.CONNECTED


# --- 2. Meta-Drossel -----------------------------------------------------


def _antwort(status, body, headers=None):
    return httpx.Response(status, json=body, headers=headers or {}, request=httpx.Request("GET", "https://x"))


def _provider():
    # _request braucht nur platform_name und _safe_json.
    return SimpleNamespace(platform_name="Instagram", _safe_json=base.SocialProvider._safe_json)


def _request(p, url):
    return base.SocialProvider._request(p, "GET", url, access_token="t")


@pytest.mark.django_db
def test_code_4_wird_rate_limit_und_setzt_harte_drossel():
    body = {"error": {"message": "(#4) Application request limit reached", "code": 4, "is_transient": True}}
    with (
        patch.object(base, "DROSSEL_HOOK", drossel.hook),
        patch("httpx.Client.request", return_value=_antwort(403, body)),
        pytest.raises(RateLimitError),
    ):
        _request(_provider(), "https://graph.facebook.com/v25.0/123/insights")
    assert drossel.hart_bis("instagram") is not None
    assert drossel.aktiv("facebook")
    assert not drossel.aktiv("threads")


@pytest.mark.django_db
def test_hohe_auslastung_setzt_weiche_drossel():
    kopf = {
        "x-business-use-case-usage": '{"1":[{"type":"instagram","call_count":92,"total_cputime":3,'
        '"total_time":4,"estimated_time_to_regain_access":0}]}'
    }
    with (
        patch.object(base, "DROSSEL_HOOK", drossel.hook),
        patch("httpx.Client.request", return_value=_antwort(200, {"id": "1"}, kopf)),
    ):
        _request(_provider(), "https://graph.facebook.com/v25.0/1")
    assert drossel.aktiv("instagram")
    assert drossel.hart_bis("instagram") is None


@pytest.mark.django_db
def test_drossel_wird_nicht_verkuerzt():
    drossel.setzen("graph.facebook.com", 3600, hart=True, grund="a")
    bis = Drossel.objects.get(host="graph.facebook.com").bis
    drossel.setzen("graph.facebook.com", 300, hart=False, grund="b")
    d = Drossel.objects.get(host="graph.facebook.com")
    assert d.bis == bis and d.hart


def test_beitrag_wartet_bei_drossel_ohne_versuch_zu_verbrauchen():
    pp = MagicMock()
    pp.retry_count = 0
    pp.scheduled_at = timezone.now() - timedelta(minutes=1)
    PublishEngine._hold_for_rate_limit(MagicMock(), pp, "(#4) Application request limit reached", 3600)
    assert pp.retry_count == 0
    assert pp.status == "scheduled"
    assert pp.next_retry_at > timezone.now() + timedelta(minutes=55)
    assert "zählt nicht als Fehlversuch" in pp.publish_error
    pp.save.assert_called_once()


def test_beitrag_scheitert_6_stunden_nach_termin():
    engine = MagicMock()
    pp = MagicMock()
    pp.scheduled_at = timezone.now() - timedelta(hours=7)
    PublishEngine._hold_for_rate_limit(engine, pp, "limit", 600)
    engine._fail_permanently.assert_called_once()


# --- 3. Auswertung: tote Storys und Fehlschläge ---------------------------


def _post(alter, story=False, fehler_vor=None, fehler_zahl=0):
    post = MagicMock()
    post.published_at = timezone.now() - alter
    post.platform_extra = {"post_type": "story"} if story else {}
    post.analytics_fehler_am = timezone.now() - fehler_vor if fehler_vor else None
    post.analytics_fehler_zahl = fehler_zahl
    return post


def test_story_nach_24_stunden_nicht_mehr_faellig():
    assert _post_cadence_due(_post(timedelta(days=3), story=True), platform="instagram") is False


def test_fehlschlag_wartet_mit_backoff():
    # 7–30 Tage alt: Takt 1 Tag; zwei Fehlschläge in Folge -> 2 Tage Pause.
    assert (
        _post_cadence_due(
            _post(timedelta(days=10), fehler_vor=timedelta(hours=30), fehler_zahl=2), platform="instagram"
        )
        is False
    )


def test_nur_beitragsbezogene_fehler_setzen_die_fehlermarke():
    from apps.analytics.tasks import _post_specific_failure
    from providers.exceptions import APIError

    weg = APIError(
        "x",
        status_code=400,
        raw_response={"error": {"code": 100, "message": "Unsupported get request. Object with ID '1' does not exist"}},
    )
    pin_weg = APIError("x", status_code=404, raw_response={"code": 50, "message": "Pin not found."})
    systemisch = APIError(
        "x", status_code=400, raw_response={"code": 1, "message": "'end_date' must be before 2038-01-19"}
    )
    assert _post_specific_failure(weg)
    assert _post_specific_failure(pin_weg)
    assert not _post_specific_failure(systemisch)
    assert not _post_specific_failure(RuntimeError("x"))
