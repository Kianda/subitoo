"""Typed application configuration, loaded once from environment + .env.

Secrets and global knobs live here (Decision 10, 12, 17 in SPEC.md). Per-query
data lives in the DB, never here. Plugin-specific secrets are read via
``for_plugin()`` so contributors never edit core config.
"""

from __future__ import annotations

import os

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- core ---
    tz: str = "Europe/Rome"
    db_path: str = "/data/subitoo.db"
    log_level: str = "INFO"
    run_timeout: int = 900  # watchdog: stale 'running' -> 'error' (seconds)

    # --- fetching ---
    # HTTP client TLS/JA3 profile. Firefox to match the Camoufox browser, so a session
    # cookie earned by Camoufox is reused by an HTTP client with a consistent identity.
    impersonate: str = "firefox"
    proxy_url: str = ""

    # --- browser service ---
    browser_backend: str = "camoufox"
    browser_ws_url: str = "ws://subitoo_browser:1234/hello"

    # --- notifications ---
    default_channel: str = "pushover"

    def for_plugin(self, key: str) -> dict[str, str]:
        """Return env vars namespaced by a plugin key, e.g. PUSHOVER_* -> {token, user}.

        A notifier ``pushover`` gets {"token": PUSHOVER_TOKEN, "user": PUSHOVER_USER};
        a site ``vinted`` gets any VINTED_* vars. Core never hard-codes these.
        """
        prefix = f"{key.upper()}_"
        out: dict[str, str] = {}
        for env_key, value in os.environ.items():
            if env_key.startswith(prefix):
                out[env_key[len(prefix):].lower()] = value
        return out


_settings: Settings | None = None


def get_settings() -> Settings:
    """Process-wide singleton so the whole run shares one Settings instance."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
