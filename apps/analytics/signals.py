"""Signal handlers: enqueue analytics backfill when a SocialAccount is
created or reconnected.

Not on a plain token refresh (25.11.2026): the six-hourly health check and
``SocialAccount.refresh_oauth_token`` save ``oauth_access_token`` too. Every
such save used to start a full 90-day backfill for the account; the health
check wave (23 accounts) then fired some 500 Meta calls within ten minutes and
ran the app into "(#4) Application request limit reached" every six hours.
YouTube/Google Business refresh hourly, so their backfill ran every hour.
"""

from __future__ import annotations

from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from apps.social_accounts.models import SocialAccount


@receiver(pre_save, sender=SocialAccount)
def remember_connection_before_save(sender, instance: SocialAccount, update_fields=None, **kwargs):
    """Remember the stored connection status, to tell a reconnect from a refresh."""
    instance._analytics_vorher = None
    if instance._state.adding or not instance.pk:
        return
    if update_fields is None or "oauth_access_token" not in update_fields:
        return
    instance._analytics_vorher = SocialAccount.objects.filter(pk=instance.pk).values("connection_status").first()


def _is_reconnect(instance: SocialAccount, update_fields) -> bool:
    """A new token after the account was not connected or flagged for an analytics reconnect.

    A refresh of a connected account (health check, refresh before use) keeps
    the hourly sync going on its own and needs no backfill.
    """
    if "analytics_needs_reconnect" in update_fields:
        # Only the OAuth connect flow (views._create_or_update_account) writes this
        # flag together with the token; refresh paths never touch it.
        return True
    vorher = getattr(instance, "_analytics_vorher", None)
    if vorher is None:
        return False
    return vorher["connection_status"] != SocialAccount.ConnectionStatus.CONNECTED


@receiver(post_save, sender=SocialAccount)
def enqueue_analytics_backfill(sender, instance: SocialAccount, created: bool, update_fields=None, **kwargs):
    """Schedule a backfill when:
    - a new account is created (initial connection), or
    - an existing account is reconnected: the OAuth flow saves a new
      ``oauth_access_token`` with ``analytics_needs_reconnect``, or a new
      token revives an account that was not connected.
    """
    if not created:
        # Anything else (avatar refresh, follower count update, a full save)
        # would re-trigger the backfill noisily.
        if update_fields is None or "oauth_access_token" not in update_fields:
            return
        if not _is_reconnect(instance, update_fields):
            return
    if instance.connection_status != SocialAccount.ConnectionStatus.CONNECTED:
        return
    # Import inside the handler so AppConfig.ready() doesn't pull in
    # background_task before the apps registry is fully loaded.
    from .tasks import backfill_account_analytics

    backfill_account_analytics(str(instance.id))
