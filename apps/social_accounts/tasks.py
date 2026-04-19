"""Background tasks for social account health checks."""

import logging
from datetime import timedelta

from background_task import background
from django.utils import timezone

logger = logging.getLogger(__name__)


@background(schedule=0)
def check_social_account_health(account_id: str):
    """Check health of a single social account.

    Validates the OAuth token by calling get_profile(). If the token
    is expiring soon, attempts to refresh it first.
    """
    from providers import get_provider

    from .error_messages import friendly_health_check_error
    from .models import SocialAccount

    try:
        account = SocialAccount.objects.get(id=account_id)
    except SocialAccount.DoesNotExist:
        logger.warning("Health check: account %s not found, skipping", account_id)
        return

    # Load app credentials from the workspace's org or env fallback
    from django.conf import settings

    from apps.credentials.models import PlatformCredential

    credentials: dict = {}
    try:
        org_id = account.workspace.organization_id
        cred = PlatformCredential.objects.for_org(org_id).get(platform=account.platform, is_configured=True)
        credentials = cred.credentials
    except PlatformCredential.DoesNotExist:
        env_creds = getattr(settings, "PLATFORM_CREDENTIALS_FROM_ENV", {})
        credentials = env_creds.get(account.platform, {})

    # For Mastodon, inject instance-specific client credentials
    if account.platform == PlatformCredential.Platform.MASTODON and account.instance_url:
        from .models import MastodonAppRegistration

        try:
            reg = MastodonAppRegistration.objects.get(instance_url=account.instance_url)
            credentials = {
                **credentials,
                "instance_url": account.instance_url,
                "client_id": reg.client_id,
                "client_secret": reg.client_secret,
            }
        except MastodonAppRegistration.DoesNotExist:
            pass

    try:
        provider = get_provider(account.platform, credentials)
    except ValueError:
        logger.error("Health check: no provider for platform %s", account.platform)
        return

    # Attempt token refresh if expiring soon
    if account.is_token_expiring_soon and account.oauth_refresh_token:
        try:
            new_tokens = provider.refresh_token(account.oauth_refresh_token)
            account.oauth_access_token = new_tokens.access_token
            if new_tokens.refresh_token:
                account.oauth_refresh_token = new_tokens.refresh_token
            if new_tokens.expires_in:
                account.token_expires_at = timezone.now() + timedelta(seconds=new_tokens.expires_in)
            account.connection_status = SocialAccount.ConnectionStatus.CONNECTED
            account.last_error = ""
            logger.info("Health check: refreshed token for %s", account)
        except Exception as e:
            logger.warning("Health check: token refresh failed for %s: %s", account, e)
            account.connection_status = SocialAccount.ConnectionStatus.TOKEN_EXPIRING
            account.last_error = friendly_health_check_error(e)

    # Validate token by fetching profile
    try:
        profile = provider.get_profile(account.oauth_access_token)
        account.follower_count = profile.follower_count
        if account.connection_status != SocialAccount.ConnectionStatus.TOKEN_EXPIRING:
            account.connection_status = SocialAccount.ConnectionStatus.CONNECTED
        account.last_error = ""
    except Exception as e:
        logger.warning("Health check: profile fetch failed for %s: %s", account, e)
        account.connection_status = SocialAccount.ConnectionStatus.ERROR
        account.last_error = friendly_health_check_error(e)

    account.last_health_check_at = timezone.now()
    account.save(
        update_fields=[
            "oauth_access_token",
            "oauth_refresh_token",
            "token_expires_at",
            "follower_count",
            "connection_status",
            "last_error",
            "last_health_check_at",
            "updated_at",
        ]
    )


@background(schedule=0)
def schedule_all_health_checks():
    """Enqueue individual health checks for all active accounts."""
    from .models import SocialAccount

    accounts = SocialAccount.objects.filter(
        connection_status__in=[
            SocialAccount.ConnectionStatus.CONNECTED,
            SocialAccount.ConnectionStatus.TOKEN_EXPIRING,
        ]
    ).values_list("id", flat=True)

    count = 0
    for account_id in accounts:
        check_social_account_health(str(account_id))
        count += 1

    logger.info("Scheduled health checks for %d accounts", count)
