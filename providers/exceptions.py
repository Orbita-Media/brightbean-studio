"""Exception hierarchy for social platform providers."""


class ProviderError(Exception):
    """Base exception for all provider errors.

    ``retryable=False`` marks the error as permanent: the publish engine
    fails the post immediately instead of scheduling backoff retries.
    """

    def __init__(
        self,
        message: str,
        platform: str = "",
        raw_response: dict | None = None,
        retryable: bool = True,
    ):
        self.platform = platform
        self.raw_response = raw_response or {}
        self.retryable = retryable
        super().__init__(message)


class OAuthError(ProviderError):
    """OAuth flow failure (invalid code, denied access, etc.)."""


class TokenExpiredError(ProviderError):
    """Access token has expired and refresh failed or is unavailable."""


class RateLimitError(ProviderError):
    """Platform rate limit exceeded."""

    def __init__(
        self,
        message: str,
        retry_after: int | None = None,
        **kwargs,
    ):
        self.retry_after = retry_after
        super().__init__(message, **kwargs)


class PublishError(ProviderError):
    """Post publishing failed."""


class APIError(ProviderError):
    """Generic API error from the platform."""

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        **kwargs,
    ):
        self.status_code = status_code
        super().__init__(message, **kwargs)


class PublishOutcomeUnknownError(PublishError):
    """The request that makes a post public went out, its result is unknown.

    Raised by ``SocialProvider.publish_commit`` for a lost response (read
    timeout, dropped connection), a 5xx or an "unknown error" from the
    platform, or a success response the provider cannot read. The post may
    be live: Instagram answered 500 "An unexpected error has occurred" on
    22.10.2026 and Facebook timed out on 15.10.2026, both had published, and
    the automatic retry posted each of them a second time.

    The engine never retries such an attempt blindly. It looks the post up
    on the platform first (``SocialProvider.find_published_post``) and fails
    the row for a human to check when that is not possible.
    """

    def __init__(self, message: str, platform: str = "", raw_response: dict | None = None):
        super().__init__(message, platform=platform, raw_response=raw_response, retryable=False)
        # Filled in by the engine (apps/publisher/engine.py) while it looks the
        # post up: the start of the window searched, why the platform cannot
        # be checked at all, or why this particular lookup failed.
        self.outcome_since = None
        self.lookup_unavailable = ""
        self.lookup_failed = ""


class PublishLookupUnavailableError(ProviderError):
    """The platform cannot reliably tell whether a given post is already live."""

    def __init__(self, message: str, platform: str = ""):
        super().__init__(message, platform=platform, retryable=False)
