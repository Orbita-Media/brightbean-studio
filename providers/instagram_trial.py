"""Instagram trial reels (in der App: "Test-Reel").

A trial reel is shown to non-followers only. Followers do not see it in feed
or Reels and it does not appear on the profile until it "graduates" – either
by hand in the Instagram app or automatically when it performs well.

Graph API, ``POST /{ig-user-id}/media`` (and ``POST /me/media`` on the
Instagram-Login path), field table of the IG User Media reference:

    trial_params – "An optional parameter for publishing trial reels. The
    ``media_type`` must be ``REELS`` if this parameter is included in the
    request. Each object should have the following information:
    ``graduation_strategy`` – Required. […] The value should be either
    ``MANUAL`` or ``SS_PERFORMANCE``. When ``MANUAL``, the trial reel can be
    manually graduated in the native app. When ``SS_PERFORMANCE``, the trial
    reel will be automatically graduated if the trial reel performs well."

https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-user/media
https://developers.facebook.com/docs/instagram-platform/content-publishing/ ("Trial Reels posts")

Both connection types use this module, so the rules cannot drift apart
between the two ways to reach the same account.

Why a trial on anything but a reel FAILS instead of being dropped (unlike a
platform sound, which is dropped with a warning): a trial is a promise about
the audience. Dropping it would publish a post meant for strangers to every
follower – the exact opposite of what was asked for, and not reversible.

Like ``audio_configuration`` and ``collaborators`` it goes over the wire as a
JSON *string*. Graph does not reliably unpack nested values out of a JSON
body; a native list for ``collaborators`` was accepted with a 200 and
silently ignored. For ``trial_params`` a silent drop would be the worst
possible outcome (see above), so the string form is the one that was checked
against the live API (docs/INSTAGRAM-TEST-REELS.md).
"""

from __future__ import annotations

import json

from .exceptions import PublishError
from .types import PostType

#: Graduates automatically when the trial performs well – the default,
#: because it needs nobody to remember to open the app.
GRADUATION_SS_PERFORMANCE = "SS_PERFORMANCE"
#: Stays with non-followers until someone graduates it in the Instagram app.
GRADUATION_MANUAL = "MANUAL"
GRADUATION_STRATEGIES = (GRADUATION_SS_PERFORMANCE, GRADUATION_MANUAL)
DEFAULT_GRADUATION = GRADUATION_SS_PERFORMANCE

#: The two keys in ``PlatformPost.platform_extra``.
EXTRA_TRIAL = "trial"
EXTRA_GRADUATION = "trial_graduation"

#: Post types that go out as a REELS container. PostType.VIDEO is the
#: engine's fallback for a lone video, which Instagram publishes as a reel.
REEL_POST_TYPES = (PostType.REEL, PostType.VIDEO)

_TRUE_STRINGS = {"true", "1", "on", "yes", "ja"}


def is_trial(extra: dict | None) -> bool:
    """True when the per-platform settings ask for a trial reel.

    Accepts a real boolean and the string forms a form post or an imported
    plan produce. Anything else – including a missing key – is "no trial".
    """
    if not extra:
        return False
    value = extra.get(EXTRA_TRIAL)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value == 1
    if isinstance(value, str):
        return value.strip().lower() in _TRUE_STRINGS
    return False


def normalize_graduation(value) -> str | None:
    """Map a user-supplied strategy onto Meta's spelling, or ``None`` if unknown.

    Empty means "not chosen" and yields the default.
    """
    text = str(value or "").strip().upper()
    if not text:
        return DEFAULT_GRADUATION
    return text if text in GRADUATION_STRATEGIES else None


def build_trial_params(extra: dict | None, post_type: PostType, *, platform: str = "Instagram") -> dict | None:
    """Return the ``trial_params`` object for a reel container, or ``None``.

    Raises ``PublishError`` when a trial is asked for on something that is not
    a reel, or with a graduation strategy Meta does not know. Both are raised
    BEFORE any container is created, so nothing is half-published, and both
    are permanent (``retryable=False``): retrying cannot fix a setting.
    """
    if not is_trial(extra):
        return None
    if post_type not in REEL_POST_TYPES:
        raise PublishError(
            "Test-Reel geht nur bei einem Reel (ein einzelnes Video). Dieser Beitrag ist "
            f"'{post_type.value}'. Test-Reel ausschalten oder den Beitrag als Reel anlegen. "
            "Stillschweigend ohne Test zu veröffentlichen hieße, ihn allen Followern zu zeigen – "
            "das Gegenteil eines Tests.",
            platform=platform,
            retryable=False,
        )
    strategy = normalize_graduation((extra or {}).get(EXTRA_GRADUATION))
    if strategy is None:
        raise PublishError(
            f"Unbekannte Freigabe für das Test-Reel: {(extra or {}).get(EXTRA_GRADUATION)!r}. "
            f"Erlaubt sind {', '.join(GRADUATION_STRATEGIES)}.",
            platform=platform,
            retryable=False,
        )
    return {"graduation_strategy": strategy}


def trial_params_field(trial_params: dict) -> str:
    """Serialise for the container payload (JSON string, see module docstring)."""
    return json.dumps(trial_params)
