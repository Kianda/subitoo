"""Camoufox Playwright WebSocket server — the always-warm browser.

Runs one hardened-Firefox instance and exposes it over a Playwright-compatible
WebSocket. The app container connects with playwright.firefox.connect() and drives
the already-running browser, so it never pays a cold-start per run.

Config via env:
  CAMOUFOX_PORT     (default 1234)
  CAMOUFOX_WS_PATH  (default "hello")  -- fixed path so clients need no discovery
  CAMOUFOX_HEADLESS (default "false")  -- boolean mirroring Camoufox's own `headless`.
                                          The container always runs under Xvfb, so
                                          "false" = headed Firefox in the virtual
                                          display; "true" = patched headless mode.
"""

import os
from urllib.parse import urlparse

from camoufox.server import launch_server


def _headless() -> bool:
    # Mirror Camoufox/launch_server's own `headless` boolean directly — no invented
    # values. The container always runs under Xvfb (see Dockerfile CMD), so:
    #   false (default) -> headed Firefox rendering into the virtual display
    #   true            -> Camoufox's patched headless mode
    return os.environ.get("CAMOUFOX_HEADLESS", "false").strip().lower() in ("1", "true", "yes")


def _proxy():
    """Parse PROXY_URL (e.g. http://user:pass@host:port) into Camoufox's proxy dict.

    Passing the proxy to the browser means the browser and the HTTP client share the
    same egress IP — Camoufox also derives geoIP from it.
    """
    url = os.environ.get("PROXY_URL", "").strip()
    if not url:
        return None
    u = urlparse(url)
    if u.scheme not in ("http", "https", "socks5") or not u.hostname:
        # Not a real proxy URL (e.g. an accidental comment) -> ignore it.
        return None
    proxy = {"server": f"{u.scheme}://{u.hostname}:{u.port}"}
    if u.username:
        proxy["username"] = u.username
    if u.password:
        proxy["password"] = u.password
    return proxy


if __name__ == "__main__":
    proxy = _proxy()
    kwargs = dict(
        headless=_headless(),
        # Disable Firefox's JSON viewer so navigating to a JSON API returns the raw
        # JSON as the page body (readable via inner_text), not a rendered tree.
        firefox_user_prefs={"devtools.jsonview.enabled": False},
        # host is passed through to Playwright's launchServer; default binds to
        # localhost only, so other containers can't reach it. 0.0.0.0 = all interfaces.
        host="0.0.0.0",
        port=int(os.environ.get("CAMOUFOX_PORT", "1234")),
        ws_path=os.environ.get("CAMOUFOX_WS_PATH", "hello"),
    )
    if proxy:
        # Only include proxy when actually set: Camoufox 0.4.11 forwards proxy=None,
        # which Playwright >=1.54 rejects ("proxy: expected object, got null"). This
        # mirrors the fix in Camoufox's own repo. geoip aligns timezone/locale to the
        # egress IP and is derived from the proxy, so only enable it alongside a proxy.
        kwargs["proxy"] = proxy
        kwargs["geoip"] = True
    launch_server(**kwargs)
