"""The toolbox the core injects into every adapter's ``fetch()``.

An adapter receives a :class:`FetchContext` and uses whatever it needs:

- ``ctx.http``    — curl_cffi client impersonating Firefox (TLS/JA3 + HTTP/2), matching
  the Camoufox browser's TLS profile; used only by browserless sites.
- ``ctx.browser`` — a *lazy* browser client (only started if the adapter touches it).
- ``ctx.config``  — the typed Settings.
- ``ctx.log``     — a logger.

The per-site browser choreography (Layer 3 in SPEC.md) lives in the adapter; the
generic building blocks (Layer 2) live on :class:`BrowserClient`.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from subitoo.config import Settings

log = logging.getLogger("subitoo.fetch")


class HttpClient:
    """Thin wrapper over curl_cffi with browser TLS impersonation + optional proxy.

    The impersonation target is configurable (``IMPERSONATE``, default ``firefox`` to
    match the Camoufox browser). Impersonation matters even for JSON APIs: plain
    httpx/requests have a non-browser TLS/JA3 signature that a site can distinguish from
    a real browser regardless of headers.
    """

    def __init__(self, settings: Settings):
        self._settings = settings
        self._session = None  # created lazily so unit tests need not import curl_cffi

    @property
    def session(self):
        if self._session is None:
            from curl_cffi import requests as cffi_requests

            proxies = (
                {"http": self._settings.proxy_url, "https": self._settings.proxy_url}
                if self._settings.proxy_url
                else None
            )
            self._session = cffi_requests.Session(
                impersonate=self._settings.impersonate,
                proxies=proxies,
            )
        return self._session

    def get(self, url: str, **kwargs):
        return self.session.get(url, **kwargs)

    def post(self, url: str, **kwargs):
        return self.session.post(url, **kwargs)

    def set_header(self, name: str, value: str) -> None:
        self.session.headers[name] = value


class BrowserClient:
    """Access to the always-warm Camoufox browser (the subitoo_browser container
    running a Playwright WebSocket server).

    Sites that need a browser fetch *entirely* through it — no cookie/HTTP handoff
    — so the whole request chain is one consistent real browser. The one reusable
    Layer-2 primitive is:
      - ``session(warm_url=...)`` -> a context manager yielding a site-agnostic page
        handle (:class:`_CamoufoxPage`) the adapter drives itself.

    The core deliberately knows *nothing* about any site's consent banners, toggles,
    or API shapes — that Layer-3 choreography lives in each adapter. We connect to the
    running server per run (the browser stays warm) and open/close only a CONTEXT —
    never the shared browser. ``health_check`` runs before every browser use.
    """

    def __init__(self, settings: Settings):
        self._settings = settings

    def _host_port(self) -> tuple[str, int]:
        from urllib.parse import urlparse

        u = urlparse(self._settings.browser_ws_url)
        return u.hostname or "subitoo_browser", u.port or 1234

    def health_check(self) -> None:
        """Raise if the browser server's WS port is not accepting connections."""
        import socket

        host, port = self._host_port()
        try:
            with socket.create_connection((host, port), timeout=5):
                return
        except OSError as e:
            raise RuntimeError(
                f"browser service unavailable at {self._settings.browser_ws_url}: {e}"
            ) from e

    @contextmanager
    def session(self, *, warm_url: str | None = None, wait_seconds: float = 6.0):
        """Open ONE browser session and drive it through a site-agnostic page handle.

        If ``warm_url`` is given it is loaded first (to establish a real browser session
        and cookies in the context). Yields a :class:`_CamoufoxPage` the adapter
        drives itself — navigate, click, read text, inspect the URLs the page requested
        — reused across pagination so we make one human-like browsing session instead
        of re-warming per page. Closes only the context, leaving the browser warm.
        """
        backend = self._settings.browser_backend
        if backend != "camoufox":
            raise NotImplementedError(f"browser backend {backend!r} not implemented yet")
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.firefox.connect(self._settings.browser_ws_url)
            context = browser.new_context(viewport={"width": 1440, "height": 900})
            try:
                page = _CamoufoxPage(context.new_page(), wait_seconds=wait_seconds)
                if warm_url:
                    page.goto(warm_url)
                yield page
            finally:
                context.close()  # keep the shared browser warm; do NOT browser.close()


class _CamoufoxPage:
    """A thin, site-agnostic handle over one live Camoufox page.

    Adapters drive the browser through these generic primitives — the core stays
    ignorant of any site's consent banners, toggles, or API shapes. Every request the
    page issues is recorded in ``requests`` so an adapter can capture an API call the
    site fires client-side (e.g. after a filter/sort interaction).
    """

    def __init__(self, page, *, wait_seconds: float):
        self._page = page
        self._wait_seconds = wait_seconds
        self.requests: list[str] = []
        page.on("request", lambda r: self.requests.append(r.url))

    def goto(self, url: str, *, wait: float | None = None) -> None:
        """Navigate to ``url`` and settle for ``wait`` seconds (default ``wait_seconds``)."""
        self._page.goto(url, wait_until="load", timeout=60_000)
        self.wait(self._wait_seconds if wait is None else wait)

    def wait(self, seconds: float) -> None:
        self._page.wait_for_timeout(int(seconds * 1000))

    def click_first(self, selectors: tuple[str, ...], *, scroll: bool = False) -> bool:
        """Click the first of ``selectors`` that exists; return whether one matched."""
        for sel in selectors:
            try:
                loc = self._page.locator(sel).first
                if loc.count():
                    if scroll:
                        loc.scroll_into_view_if_needed(timeout=3000)
                        self.wait(0.5)
                    loc.click(timeout=4000)
                    return True
            except Exception:
                continue
        return False

    def text(self) -> str:
        """The current page's body text (raw JSON for API endpoints)."""
        return self._page.inner_text("body")

    def eval_js(self, expression: str) -> Any:
        """Evaluate a JS expression in the page and return the result."""
        return self._page.evaluate(expression)


@dataclass
class FetchContext:
    """The toolbox handed to ``adapter.fetch(search, ctx)``."""

    http: HttpClient
    config: Settings
    log: logging.Logger
    _browser: BrowserClient | None = None

    @property
    def browser(self) -> BrowserClient:
        """Lazily created browser client (only if the adapter actually uses it)."""
        if self._browser is None:
            self._browser = BrowserClient(self.config)
        return self._browser


def build_context(settings: Settings, *, needs_browser: bool) -> FetchContext:
    """Assemble a FetchContext; health-check the browser up front when required."""
    ctx = FetchContext(
        http=HttpClient(settings),
        config=settings,
        log=log,
    )
    if needs_browser:
        ctx.browser.health_check()  # fail fast with a clear error if it's down
    return ctx
