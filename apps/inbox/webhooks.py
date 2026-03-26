"""Inbound webhook receivers for Facebook/Instagram and YouTube."""

import hashlib
import hmac
import json
import logging
import xml.etree.ElementTree as ET

from django.conf import settings
from django.http import HttpResponse, HttpResponseForbidden
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from apps.social_accounts.models import SocialAccount

from .models import InboxMessage
from .sentiment import analyze_sentiment

logger = logging.getLogger(__name__)


@csrf_exempt
@require_http_methods(["GET", "POST"])
def facebook_webhook(request):
    """Facebook & Instagram webhook endpoint.

    GET:  Verification handshake (hub.mode, hub.verify_token, hub.challenge).
    POST: Incoming events with HMAC-SHA256 signature in X-Hub-Signature-256.
    """
    if request.method == "GET":
        return _facebook_verify(request)
    return _facebook_receive(request)


def _facebook_verify(request):
    """Handle Facebook webhook verification handshake."""
    mode = request.GET.get("hub.mode")
    token = request.GET.get("hub.verify_token")
    challenge = request.GET.get("hub.challenge", "")

    if mode == "subscribe" and token == settings.FACEBOOK_WEBHOOK_VERIFY_TOKEN:
        return HttpResponse(challenge, content_type="text/plain")
    return HttpResponseForbidden("Verification failed.")


def _facebook_receive(request):
    """Handle incoming Facebook/Instagram webhook events."""
    signature = request.headers.get("X-Hub-Signature-256", "")
    if not _verify_facebook_signature(request.body, signature):
        logger.warning("Invalid Facebook webhook signature.")
        return HttpResponseForbidden("Invalid signature.")

    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        logger.warning("Invalid JSON in Facebook webhook payload.")
        return HttpResponse("Bad request", status=400)

    _process_facebook_events(payload)
    return HttpResponse("OK", status=200)


def _verify_facebook_signature(body: bytes, signature_header: str) -> bool:
    """Verify HMAC-SHA256 signature from Facebook."""
    app_secret = settings.PLATFORM_CREDENTIALS_FROM_ENV.get("facebook", {}).get("app_secret", "")
    if not app_secret:
        logger.error("Facebook app_secret not configured. Cannot verify webhook.")
        return False

    expected = (
        "sha256="
        + hmac.new(
            app_secret.encode(),
            body,
            hashlib.sha256,
        ).hexdigest()
    )
    return hmac.compare_digest(expected, signature_header)


def _process_facebook_events(payload: dict):
    """Process Facebook/Instagram webhook events and upsert InboxMessages."""
    for entry in payload.get("entry", []):
        page_id = entry.get("id")
        if not page_id:
            continue

        accounts = SocialAccount.objects.filter(
            account_platform_id=page_id,
            connection_status=SocialAccount.ConnectionStatus.CONNECTED,
        ).select_related("workspace")

        for account in accounts:
            # Process changes (Facebook Page events)
            for change in entry.get("changes", []):
                _handle_facebook_change(account, change)

            # Process messaging events (Instagram/Facebook DMs)
            for messaging in entry.get("messaging", []):
                _handle_facebook_messaging(account, messaging)


def _handle_facebook_change(account, change: dict):
    """Handle a single Facebook change event (comment, mention, etc.)."""
    field = change.get("field", "")
    value = change.get("value", {})

    if field == "feed":
        _upsert_facebook_comment(account, value)
    elif field == "mention":
        _upsert_facebook_mention(account, value)


def _upsert_facebook_comment(account, value: dict):
    """Upsert a Facebook comment from a webhook event."""
    comment_id = value.get("comment_id") or value.get("id")
    if not comment_id:
        return

    from_data = value.get("from", {})
    text = value.get("message", "")

    _create_if_new(
        account=account,
        platform_message_id=str(comment_id),
        message_type=InboxMessage.MessageType.COMMENT,
        sender_name=from_data.get("name", "Unknown"),
        sender_id=from_data.get("id", ""),
        body=text,
        extra=value,
    )


def _upsert_facebook_mention(account, value: dict):
    """Upsert a Facebook mention from a webhook event."""
    mention_id = value.get("post_id") or value.get("id")
    if not mention_id:
        return

    from_data = value.get("from", {})
    text = value.get("message", "")

    _create_if_new(
        account=account,
        platform_message_id=str(mention_id),
        message_type=InboxMessage.MessageType.MENTION,
        sender_name=from_data.get("name", "Unknown"),
        sender_id=from_data.get("id", ""),
        body=text,
        extra=value,
    )


def _handle_facebook_messaging(account, messaging: dict):
    """Handle a Facebook/Instagram messaging event (DM)."""
    message_data = messaging.get("message", {})
    mid = message_data.get("mid")
    if not mid:
        return

    sender = messaging.get("sender", {})
    text = message_data.get("text", "")

    _create_if_new(
        account=account,
        platform_message_id=str(mid),
        message_type=InboxMessage.MessageType.DM,
        sender_name=sender.get("name", sender.get("id", "Unknown")),
        sender_id=sender.get("id", ""),
        body=text,
        extra=messaging,
    )


def _create_if_new(
    account,
    platform_message_id: str,
    message_type: str,
    sender_name: str,
    sender_id: str,
    body: str,
    extra: dict,
):
    """Create InboxMessage if it doesn't already exist (deduplication)."""
    from django.utils import timezone

    obj, created = InboxMessage.objects.get_or_create(
        social_account=account,
        platform_message_id=platform_message_id,
        defaults={
            "workspace": account.workspace,
            "message_type": message_type,
            "sender_name": sender_name,
            "sender_handle": sender_id,  # Platform user ID as fallback handle
            "body": body,
            "sentiment": analyze_sentiment(body),
            "extra": extra,
            "received_at": timezone.now(),
        },
    )
    if created:
        from .tasks import InboxSyncEngine

        InboxSyncEngine()._notify_new_message(obj)


# --- YouTube PubSubHubbub ---


@csrf_exempt
@require_http_methods(["GET", "POST"])
def youtube_webhook(request):
    """YouTube PubSubHubbub webhook endpoint.

    GET:  Subscription verification (echo hub.challenge).
    POST: Atom XML notification for new comments/activity.
    """
    if request.method == "GET":
        challenge = request.GET.get("hub.challenge", "")
        return HttpResponse(challenge, content_type="text/plain")

    # Verify HMAC if configured
    webhook_secret = settings.YOUTUBE_WEBHOOK_SECRET
    if webhook_secret:
        signature = request.headers.get("X-Hub-Signature", "")
        expected = (
            "sha1="
            + hmac.new(
                webhook_secret.encode(),
                request.body,
                hashlib.sha1,
            ).hexdigest()
        )
        if not hmac.compare_digest(expected, signature):
            logger.warning("Invalid YouTube webhook signature.")
            return HttpResponseForbidden("Invalid signature.")

    try:
        _process_youtube_notification(request.body)
    except Exception:
        logger.exception("Error processing YouTube webhook.")

    return HttpResponse("OK", status=200)


def _process_youtube_notification(body: bytes):
    """Parse Atom XML notification from YouTube and upsert messages."""
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        logger.warning("Invalid XML in YouTube webhook payload.")
        return

    ns = {
        "atom": "http://www.w3.org/2005/Atom",
        "yt": "http://www.youtube.com/xml/schemas/2015",
    }

    for entry in root.findall("atom:entry", ns):
        video_id = entry.findtext("yt:videoId", default="", namespaces=ns)
        channel_id = entry.findtext("yt:channelId", default="", namespaces=ns)
        title = entry.findtext("atom:title", default="", namespaces=ns)
        entry_id = entry.findtext("atom:id", default="", namespaces=ns)

        if not channel_id or not entry_id:
            continue

        accounts = SocialAccount.objects.filter(
            account_platform_id=channel_id,
            platform="youtube",
            connection_status=SocialAccount.ConnectionStatus.CONNECTED,
        ).select_related("workspace")

        for account in accounts:
            _create_if_new(
                account=account,
                platform_message_id=entry_id,
                message_type=InboxMessage.MessageType.COMMENT,
                sender_name="YouTube",
                sender_id=channel_id,
                body=title,
                extra={"video_id": video_id, "entry_id": entry_id},
            )
