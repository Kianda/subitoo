"""Dispatch a Notification to the active global channel (Decision 9).

For now it sends everything to Settings.default_channel. Per-query routing is deferred, but
because notifiers are modular, adding channels later is just new files.
"""

from __future__ import annotations

from subitoo.config import get_settings
from subitoo.core.models import Notification
from subitoo.registry import get_notifier


def active_notifier():
    return get_notifier(get_settings().default_channel)


def send(n: Notification) -> None:
    """Send via the active channel. Raises on failure (caller logs + retries)."""
    active_notifier().send(n)
