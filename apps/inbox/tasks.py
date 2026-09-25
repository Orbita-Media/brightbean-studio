"""Inbox sync engine - polls connected accounts for new messages."""

import logging
from datetime import timedelta

from background_task import background
from django.utils import timezone

from apps.members.models import WorkspaceMembership
from apps.notifications.engine import notify
from apps.notifications.models import EventType
from apps.social_accounts.models import SocialAccount
from apps.social_accounts.tokens import call_with_fresh_token
from providers import get_provider

from .models import InboxMessage, InboxSLAConfig
from .sentiment import analyze_sentiment

logger = logging.getLogger(__name__)

# On an account's first-ever sync we may pull a historical backlog; notifications
# are suppressed for it, EXCEPT messages newer than this window — so a long-quiet
# account's genuinely-new first message still alerts instead of being swallowed.
INBOX_BACKLOG_NOTIFY_WINDOW = timedelta(hours=1)


# How long a part of the inbox the platform refused (missing app capability)
# is skipped before it is probed again. Once a day keeps the log quiet and
# still picks up a granted App Review without a redeploy.
INBOX_UNAVAILABLE_RECHECK = timedelta(hours=24)
INBOX_UNAVAILABLE_KEY = "inbox_unavailable"


def inbox_parts_to_skip(account) -> set[str]:
    """Inbox parts marked unavailable within the last ``INBOX_UNAVAILABLE_RECHECK``."""
    from django.utils.dateparse import parse_datetime

    marked = (account.platform_settings or {}).get(INBOX_UNAVAILABLE_KEY) or {}
    skip = set()
    for part, info in marked.items():
        checked = parse_datetime((info or {}).get("checked_at") or "")
        if checked and checked > timezone.now() - INBOX_UNAVAILABLE_RECHECK:
            skip.add(part)
    return skip


def record_inbox_availability(account, skipped: set[str], unavailable) -> None:
    """Persist which inbox parts the platform refused, and clear recovered ones.

    ``unavailable`` maps part name → reason, as reported by the provider for
    this cycle. Parts that were probed (not skipped) and not reported again
    have recovered and are removed.
    """
    if not isinstance(unavailable, dict):
        return
    settings_ = dict(account.platform_settings or {})
    marked = dict(settings_.get(INBOX_UNAVAILABLE_KEY) or {})
    before = dict(marked)
    now = timezone.now().isoformat()
    for part, reason in unavailable.items():
        if part not in before:
            logger.warning(
                "Inbox part %r of account %s (%s) is not available, skipping it for %s: %s",
                part,
                account.id,
                account.platform,
                INBOX_UNAVAILABLE_RECHECK,
                reason,
            )
        marked[part] = {"reason": str(reason)[:500], "checked_at": now}
    for part in list(marked):
        if part not in unavailable and part not in skipped:
            logger.info("Inbox part %r of account %s (%s) is available again", part, account.id, account.platform)
            del marked[part]
    if marked == before:
        return
    if marked:
        settings_[INBOX_UNAVAILABLE_KEY] = marked
    else:
        settings_.pop(INBOX_UNAVAILABLE_KEY, None)
    account.platform_settings = settings_
    account.save(update_fields=["platform_settings", "updated_at"])


def _is_recent(ts):
    """True if a provider message timestamp falls within the backlog-notify window."""
    if ts is None:
        return False
    if timezone.is_naive(ts):
        ts = timezone.make_aware(ts, timezone.get_default_timezone())
    return ts >= timezone.now() - INBOX_BACKLOG_NOTIFY_WINDOW


class InboxSyncEngine:
    """Syncs inbox messages from all connected social accounts."""

    def run_cycle(self):
        """Run one full inbox cycle: poll every connected account, then check SLAs.

        Single shared entry point for the ``run_inbox_sync`` management command
        and the recurring ``run_inbox_sync_cycle`` background task, so the two
        never diverge.
        """
        self.sync_all()
        self.check_sla()

    def sync_all(self):
        """Poll each connected account for new messages."""
        accounts = SocialAccount.objects.filter(
            connection_status=SocialAccount.ConnectionStatus.CONNECTED,
        ).select_related("workspace")

        for account in accounts:
            try:
                self._sync_account(account)
            except Exception:
                logger.exception("Inbox sync failed for account %s", account.id)

    def _sync_account(self, account):
        """Sync messages for a single social account."""
        from apps.publisher.engine import _resolve_publish_credentials

        try:
            provider = get_provider(account.platform, _resolve_publish_credentials(account))
        except ValueError:
            logger.warning("No provider for platform %s", account.platform)
            return

        last_msg = (
            InboxMessage.objects.filter(social_account=account)
            .order_by("-received_at")
            .values_list("received_at", flat=True)
            .first()
        )
        is_first_sync = last_msg is None

        # Parts of the inbox the platform refused for lack of an app capability
        # (e.g. Instagram DMs without instagram_manage_messages). They are
        # re-probed once a day instead of failing every five minutes.
        skip_parts = inbox_parts_to_skip(account)
        provider.inbox_skip_parts = skip_parts
        provider.inbox_unavailable_parts = {}

        try:
            # Refresh before expiry and once more on 401: YouTube/Google tokens
            # live one hour, the inbox polls every five minutes.
            messages = call_with_fresh_token(
                account,
                provider,
                lambda token: provider.get_messages(access_token=token, since=last_msg),
            )
        except NotImplementedError:
            return
        except Exception:
            logger.exception(
                "get_messages() failed for account %s (%s)",
                account.id,
                account.platform,
            )
            return

        record_inbox_availability(account, skip_parts, provider.inbox_unavailable_parts)

        for msg in messages:
            # Suppress notifications for the historical backlog pulled on an
            # account's first sync, but still alert for genuinely recent messages:
            # a long-quiet account's first real message also has last_msg=None, so
            # a blanket first-sync mute would silently swallow it. backfill_inbox
            # seeds explicit history silently (notify=False).
            notify_new = not is_first_sync or _is_recent(msg.timestamp)
            self._upsert_message(account, msg, notify=notify_new)

    def _upsert_message(self, account, msg, notify=True):
        """Create or update an inbox message, deduplicating by platform_message_id."""
        obj, created = InboxMessage.objects.update_or_create(
            social_account=account,
            platform_message_id=msg.platform_message_id,
            defaults={
                "workspace": account.workspace,
                "sender_name": msg.sender_name,
                "sender_handle": msg.extra.get("sender_handle", msg.sender_id),
                "sender_avatar_url": msg.extra.get("sender_avatar_url", ""),
                "body": msg.text,
                "message_type": msg.message_type,
                "received_at": msg.timestamp,
                "extra": msg.extra,
            },
        )
        if created:
            obj.sentiment = analyze_sentiment(obj.body)
            obj.save(update_fields=["sentiment"])
            if notify:
                self._notify_new_message(obj)

    def _notify_new_message(self, message):
        """Send notification for a new inbox message."""
        if message.assigned_to:
            users = [message.assigned_to]
        else:
            memberships = WorkspaceMembership.objects.filter(
                workspace=message.workspace,
                workspace_role__in=["owner", "manager"],
            ).select_related("user")
            users = [m.user for m in memberships]

        for user in users:
            notify(
                user=user,
                event_type=EventType.NEW_INBOX_MESSAGE,
                title=f"New {message.get_message_type_display()} from {message.sender_name}",
                body=message.body[:200],
                data={
                    "message_id": str(message.id),
                    "workspace_id": str(message.workspace_id),
                },
            )

    def check_sla(self):
        """Check for SLA-overdue messages and send notifications."""
        from datetime import timedelta

        configs = InboxSLAConfig.objects.filter(is_active=True).select_related("workspace")

        for config in configs:
            threshold = timezone.now() - timedelta(minutes=config.target_response_minutes)
            overdue_messages = InboxMessage.objects.filter(
                workspace=config.workspace,
                status__in=[InboxMessage.Status.UNREAD, InboxMessage.Status.OPEN],
                received_at__lte=threshold,
            ).exclude(extra__has_key="sla_notified")

            for message in overdue_messages:
                self._notify_sla_overdue(message, config)
                message.extra["sla_notified"] = True
                message.save(update_fields=["extra"])

    def _notify_sla_overdue(self, message, config):
        """Notify about an SLA-overdue message."""
        if message.assigned_to:
            users = [message.assigned_to]
        else:
            memberships = WorkspaceMembership.objects.filter(
                workspace=message.workspace,
                workspace_role__in=["owner", "manager"],
            ).select_related("user")
            users = [m.user for m in memberships]

        for user in users:
            notify(
                user=user,
                event_type=EventType.INBOX_SLA_OVERDUE,
                title=f"SLA overdue: {message.get_message_type_display()} from {message.sender_name}",
                body=f"Response target of {config.target_response_minutes} minutes exceeded.",
                data={
                    "message_id": str(message.id),
                    "workspace_id": str(message.workspace_id),
                },
            )


# How often the recurring inbox-sync cycle runs; registered on a repeating
# schedule by apps.inbox.apps.InboxConfig. Polling is the always-on baseline
# documented in architecture.md (webhooks, where configured, supplement it).
INBOX_SYNC_INTERVAL_SECONDS = 5 * 60  # every 5 minutes


@background(schedule=0)
def run_inbox_sync_cycle():
    """Run one inbox cycle on the shared ``process_tasks`` worker (every deploy target).

    Delegates to ``InboxSyncEngine.run_cycle`` — the same entry point the
    ``run_inbox_sync`` management command uses — so the two never diverge.
    """
    InboxSyncEngine().run_cycle()
