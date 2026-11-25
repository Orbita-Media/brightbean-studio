"""Meta-App-Limit: Restverbrauch nach der Drossel-Reparatur (25.11.2026).

Gegenprobe 25.11. 01:48 UTC: x-app-usage stieg in sechs Minuten von 11 auf
100 % und Meta antwortete "(#4) Application request limit reached". Auslöser war
die Zustandsprüfung alle 6 h: Sie speichert jedes Konto mit
``oauth_access_token`` in ``update_fields``, das Signal hielt das für einen
Reconnect und startete je Konto einen Backfill über alle Beiträge von 90 Tagen,
ohne Takt, Story-Fenster und Fehler-Backoff (23 Backfills, ~500 Meta-Aufrufe).
"""

import datetime as dt
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import pytest
from django.utils import timezone

import providers.base as base
from apps.analytics.tasks import backfill_account_analytics
from apps.publisher import drossel
from apps.social_accounts.models import AnalyticsPlatformConfig, SocialAccount


@pytest.fixture
def workspace(db, organization):
    from apps.workspaces.models import Workspace

    return Workspace.objects.create(name="Restverbrauch WS", organization=organization)


def _konto(workspace, platform="instagram", status=SocialAccount.ConnectionStatus.CONNECTED, **extra):
    return SocialAccount.objects.create(
        workspace=workspace,
        platform=platform,
        account_platform_id=f"{platform}-1",
        account_name=f"{platform} Konto",
        oauth_access_token="alt",
        oauth_refresh_token="r",
        connection_status=status,
        **extra,
    )


# --- 1. Backfill nur bei Connect/Reconnect, nicht bei Token-Erneuerung -------


@pytest.mark.django_db
def test_neues_konto_startet_backfill(workspace):
    with patch("apps.analytics.tasks.backfill_account_analytics") as bf:
        _konto(workspace)
    bf.assert_called_once()


@pytest.mark.django_db
def test_zustandsprüfung_startet_keinen_backfill(workspace):
    from apps.social_accounts.tasks import check_social_account_health

    konto = _konto(workspace)
    provider = MagicMock()
    provider.get_profile.return_value = SimpleNamespace(follower_count=5, avatar_url="", name="", handle="")
    with (
        patch("providers.get_provider", return_value=provider),
        patch("apps.analytics.tasks.backfill_account_analytics") as bf,
    ):
        check_social_account_health.now(str(konto.id))
    bf.assert_not_called()


@pytest.mark.django_db
def test_token_erneuerung_startet_keinen_backfill(workspace):
    konto = _konto(workspace, platform="youtube")
    provider = MagicMock()
    provider.refresh_token.return_value = SimpleNamespace(access_token="neu", refresh_token="", expires_in=3600)
    with patch("apps.analytics.tasks.backfill_account_analytics") as bf:
        konto.refresh_oauth_token(provider)
    konto.refresh_from_db()
    assert konto.oauth_access_token == "neu"
    bf.assert_not_called()


@pytest.mark.django_db
def test_oauth_reconnect_startet_backfill(workspace):
    from apps.social_accounts.views import _create_or_update_account

    konto = _konto(workspace, analytics_needs_reconnect=True)
    profil = SimpleNamespace(
        platform_id=konto.account_platform_id, name="n", handle="h", avatar_url="", follower_count=1
    )
    with patch("apps.analytics.tasks.backfill_account_analytics") as bf:
        _create_or_update_account(
            workspace_id=workspace.id, platform=konto.platform, profile=profil, access_token="frisch"
        )
    bf.assert_called_once_with(str(konto.id))


@pytest.mark.django_db
def test_neues_token_für_getrenntes_konto_startet_backfill(workspace):
    konto = _konto(workspace, status=SocialAccount.ConnectionStatus.ERROR)
    konto.oauth_access_token = "neu"
    konto.connection_status = SocialAccount.ConnectionStatus.CONNECTED
    with patch("apps.analytics.tasks.backfill_account_analytics") as bf:
        konto.save(update_fields=["oauth_access_token", "connection_status"])
    bf.assert_called_once()


# --- 2. Backfill hält denselben Takt wie der Stundenlauf --------------------


def _beitrag(konto, alter, *, story=False, fehler_vor=None, fehler_zahl=0, pid="m1"):
    from apps.composer.models import PlatformPost, Post

    post = Post.objects.create(workspace=konto.workspace, caption="x")
    return PlatformPost.objects.create(
        post=post,
        social_account=konto,
        status=PlatformPost.Status.PUBLISHED,
        published_at=timezone.now() - alter,
        platform_post_id=pid,
        platform_extra={"post_type": "story"} if story else {},
        analytics_fehler_am=(timezone.now() - fehler_vor) if fehler_vor else None,
        analytics_fehler_zahl=fehler_zahl,
    )


@pytest.mark.django_db
def test_backfill_überspringt_tote_storys_und_backoff(workspace):
    AnalyticsPlatformConfig.objects.update_or_create(platform="instagram", defaults={"is_enabled": True})
    with patch("apps.analytics.tasks.backfill_account_analytics"):
        konto = _konto(workspace)
    fällig = _beitrag(konto, timedelta(days=2), pid="fällig")
    _beitrag(konto, timedelta(days=5), story=True, pid="story")
    _beitrag(konto, timedelta(days=10), fehler_vor=timedelta(hours=2), fehler_zahl=3, pid="backoff")
    with (
        patch("apps.analytics.tasks._sync_account_metrics"),
        patch("apps.analytics.tasks._sync_post_metrics") as sync,
    ):
        backfill_account_analytics.now(str(konto.id))
    assert [c.args[0].platform_post_id for c in sync.call_args_list] == [fällig.platform_post_id]


@pytest.mark.django_db
def test_backfill_pausiert_bei_drossel(workspace):
    AnalyticsPlatformConfig.objects.update_or_create(platform="instagram", defaults={"is_enabled": True})
    with patch("apps.analytics.tasks.backfill_account_analytics"):
        konto = _konto(workspace)
    _beitrag(konto, timedelta(days=2))
    drossel.setzen("graph.facebook.com", 900, hart=False, grund="test")
    with (
        patch("apps.analytics.tasks._sync_account_metrics") as konto_sync,
        patch("apps.analytics.tasks._sync_post_metrics") as sync,
    ):
        backfill_account_analytics.now(str(konto.id))
    konto_sync.assert_not_called()
    sync.assert_not_called()


# --- 3. Weiche Drossel auch bei 4xx-Antworten -------------------------------


def _antwort(status, body, headers=None):
    return httpx.Response(status, json=body, headers=headers or {}, request=httpx.Request("GET", "https://x"))


@pytest.mark.django_db
def test_auslastung_aus_400_antwort_setzt_weiche_drossel():
    kopf = {"x-app-usage": '{"call_count":72,"total_cputime":0,"total_time":14}'}
    body = {"error": {"message": "Unsupported get request. Object with ID '1' does not exist", "code": 100}}
    p = SimpleNamespace(platform_name="Instagram", _safe_json=base.SocialProvider._safe_json)
    with (
        patch.object(base, "DROSSEL_HOOK", drossel.hook),
        patch("httpx.Client.request", return_value=_antwort(400, body, kopf)),
        pytest.raises(Exception, match="does not exist"),
    ):
        base.SocialProvider._request(p, "GET", "https://graph.facebook.com/v25.0/1/insights", access_token="t")
    assert drossel.aktiv("instagram")
    assert drossel.hart_bis("instagram") is None


@pytest.mark.django_db
def test_niedrige_auslastung_setzt_keine_drossel():
    kopf = {"x-app-usage": '{"call_count":14,"total_cputime":0,"total_time":14}'}
    p = SimpleNamespace(platform_name="Facebook", _safe_json=base.SocialProvider._safe_json)
    with (
        patch.object(base, "DROSSEL_HOOK", drossel.hook),
        patch("httpx.Client.request", return_value=_antwort(200, {"id": "1"}, kopf)),
    ):
        base.SocialProvider._request(p, "GET", "https://graph.facebook.com/v25.0/1", access_token="t")
    assert not drossel.aktiv("facebook")


# --- 4. Pinterest: gültiges Datumsfenster, Summen aus summary_metrics --------


def test_pinterest_metriken_nur_letzte_90_tage():
    from providers.pinterest import PinterestProvider

    antwort = MagicMock()
    antwort.json.return_value = {
        "all": {
            "summary_metrics": {"IMPRESSION": 120.0, "PIN_CLICK": 4.0, "SAVE": 2.0, "OUTBOUND_CLICK": 1.0},
            "daily_metrics": [],
            "lifetime_metrics": {},
        }
    }
    prov = PinterestProvider.__new__(PinterestProvider)
    with patch.object(PinterestProvider, "_request", return_value=antwort) as req:
        m = prov.get_post_metrics("t", "pin1")
    params = req.call_args.kwargs["params"]
    heute = dt.datetime.now(dt.UTC).date()
    assert params["end_date"] == heute.isoformat()
    assert (heute - dt.date.fromisoformat(params["start_date"])).days == 89
    assert (m.impressions, m.clicks, m.saves) == (120.0, 4.0, 2.0)
    assert m.extra["outbound_clicks"] == 1.0
