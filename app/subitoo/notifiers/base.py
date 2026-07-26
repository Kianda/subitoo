"""BaseNotifier — the blueprint every notification channel fills in.

A notifier's ONLY job: render + send one ``Notification``. The core decides *what*
to notify (new / price_change); the notifier decides *how* to deliver it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from subitoo.config import Settings
from subitoo.core.models import Notification


class BaseNotifier(ABC):
    registry_kind = "notifier"

    #: set by @register("...")
    key: str = ""

    def __init__(self, settings: Settings | None = None):
        from subitoo.config import get_settings

        self.settings = settings or get_settings()
        #: this channel's namespaced env vars, e.g. PUSHOVER_* (Decision 7/10).
        self.conf = self.settings.for_plugin(self.key)

    def required_keys(self) -> list[str]:
        """Config keys that must be present for this channel to work (fail-fast)."""
        return []

    def validate_config(self) -> None:
        missing = [k for k in self.required_keys() if not self.conf.get(k)]
        if missing:
            env_names = ", ".join(f"{self.key.upper()}_{k.upper()}" for k in missing)
            raise RuntimeError(f"notifier {self.key!r} missing config: {env_names}")

    @abstractmethod
    def send(self, n: Notification) -> None:
        """Deliver one notification. Raise on failure (core logs + retries next heartbeat)."""
        raise NotImplementedError
