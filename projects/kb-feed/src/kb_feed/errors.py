from __future__ import annotations


class KbFeedError(ValueError):
    """Base error safe to surface to an operator."""


class UrlRejected(KbFeedError):
    """A submitted URL is unsupported or unsafe."""


class KbWriteRejected(KbFeedError):
    """A KB write would violate project rules."""
