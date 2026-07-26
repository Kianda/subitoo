"""Subito.it site adapter.

Strategy (SPEC.md Decision 19): Subito only serves its results to a real browser, so we
fetch *entirely through Camoufox* (`needs_browser = True`) — no HTTP/cookie handoff. The user fine-tunes
the search on Subito itself and pastes the resulting page URL; we let Subito build its
own hades `/search/items` API call for us. Two phases, both site-specific and both living
in THIS file (the core only lends a generic page handle — see `core/fetch.py`):

  add-time  `resolve()` -> `_capture_api_url()`: load the pasted page, dismiss the cookie
            wall, toggle "Cerca solo nel titolo" to make Subito fire `/search/items`,
            capture that URL, normalize it, and store it in the search blob.
  run-time  `fetch()`: warm one session on the page URL (establishes the browser session), then
            replay the stored API URL per page (`start += 30`), parsing each JSON body.

A block or challenge shows up as a non-JSON body, which `fetch()` surfaces as a clear error;
the run then errors and retries on the next schedule (re-warming from scratch).

The API endpoint + JSON shape are the parts the SPEC marks as needing live iteration.
Parsing is kept in module-level pure functions so it can be unit-tested against recorded
fixtures with no network or browser (the adapter contract harness).
"""

from __future__ import annotations

import json
import random
import time
from collections.abc import Iterable
from datetime import datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from subitoo.core.fetch import FetchContext
from subitoo.core.models import Listing, SearchField
from subitoo.sites.base import BaseSite
from subitoo.registry import register

PAGE_SIZE = 30  # Subito serves 30 listings/page; we mirror it exactly (stay low).

#: Didomi cookie wall — real users must clear it before the UI is interactive.
CONSENT_SELECTORS = (
    "text=Continua senza accettare",
    "#didomi-notice-disagree-button",
    "button:has-text('Continua senza accettare')",
)
#: Toggling "Cerca solo nel titolo" forces the client-side search we capture.
TRIGGER_SELECTORS = (
    "text=Cerca solo nel titolo",
    "label:has-text('Cerca solo nel titolo')",
    "text=solo nel titolo",
)


def _with_start(api_url: str, start: int) -> str:
    """Return the resolved API URL with its pagination offset set to ``start``."""
    parts = urlsplit(api_url)
    q = dict(parse_qsl(parts.query, keep_blank_values=True))
    q["start"] = str(start)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(q), parts.fragment))


def _is_search_items(url: str) -> bool:
    """The real results endpoint — not the promoted `/gallerized` or `/geoboosted`."""
    return "hades.subito.it" in url and "/search/items?" in url


def _read_intended_qso(page) -> bool:
    """Read the page's own resolved `qso` before we toggle it to trigger the search."""
    try:
        txt = page.eval_js(
            "() => {const e=document.getElementById('__NEXT_DATA__'); return e ? e.textContent : null;}"
        )
        data = json.loads(txt)
        return bool(data["props"]["pageProps"]["initialState"]["search"]["qso"])
    except Exception:
        return False


def _normalize_api_url(url: str, *, qso: bool, lim: int = PAGE_SIZE) -> str:
    """Fix only the knobs we own on Subito's captured URL: restore the page's `qso`
    (the trigger toggle flips it) and reset pagination to the first page."""
    parts = urlsplit(url)
    q = dict(parse_qsl(parts.query, keep_blank_values=True))
    q["qso"] = "true" if qso else "false"
    q["start"] = "0"
    q["lim"] = str(lim)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(q), parts.fragment))


def _capture_api_url(page_url: str, ctx: FetchContext) -> str:
    """Let Subito build its own hades `/search/items` URL and capture it.

    Loads the pasted page, dismisses the cookie wall, toggles "Cerca solo nel titolo"
    to make Subito fire the client-side search, and grabs the request URL — normalized
    with the page's own `qso` and reset to the first page. All the Subito-specific
    browser choreography lives here; the core only lends a generic page handle.
    """
    with ctx.browser.session() as page:
        page.goto(page_url, wait=4)
        intended_qso = _read_intended_qso(page)
        page.click_first(CONSENT_SELECTORS)
        page.wait(2)
        page.click_first(TRIGGER_SELECTORS, scroll=True)
        page.wait(6)
        hits = [u for u in page.requests if _is_search_items(u)]
    if not hits:
        raise RuntimeError(
            "subito: could not capture the search API URL — the page layout or "
            "consent/toggle controls may have changed. Paste the page URL and retry."
        )
    return _normalize_api_url(hits[-1], qso=intended_qso)


def _extract_price(ad: dict[str, Any]) -> float | None:
    """Subito puts price inside the `features` list under a `/price` uri."""
    for feat in ad.get("features", []):
        if feat.get("uri", "").endswith("/price"):
            for val in feat.get("values", []):
                raw = val.get("key") or val.get("value")
                if raw is None:
                    continue
                try:
                    return float(str(raw).replace(",", "."))
                except ValueError:
                    return None
    return None


def _extract_shipping(ad: dict[str, Any]) -> bool | None:
    """`/item_shippable` is the one shipping flag Subito puts on every ad (1=Sì / 0=No).
    The other /item_shipping_* features are metadata (cost, carriers, type, size)."""
    for feat in ad.get("features", []):
        uri = feat.get("uri", "")
        if uri.endswith("/item_shippable") or uri.endswith("/shipping"):
            for val in feat.get("values", []):
                # `key` is the language-neutral code ("1"/"0"); `value` is the
                # localized label ("Sì"/"No"). Trust the key, not the label.
                v = str(val.get("key") or val.get("value") or "").strip().lower()
                if v in ("1", "true", "yes", "si", "sì"):
                    return True
                if v in ("0", "false", "no"):
                    return False
    return None


# Subito renders images on demand; the API gives us a ready path in `cdn_base_url`
# and we pick a render rule. `uri` is only an internal handle (imgid:<uuid>), not a URL.
_IMG_RULE = "gallery-desktop-1x-auto"


def _extract_image(ad: dict[str, Any]) -> str | None:
    images = ad.get("images") or []
    if not images or not isinstance(images[0], dict):
        return None
    base = images[0].get("cdn_base_url")
    return f"{base}?rule={_IMG_RULE}" if base else None


def _extract_location(ad: dict[str, Any]) -> str | None:
    """Most specific place name available (comune > provincia > regione), suffixed with
    the province abbreviation, e.g. "Primiero San Martino di Castrozza (TN)". The
    `short_name` ("TN") lives on the `city` node — which Subito labels "Provincia"."""
    geo = ad.get("geo") or {}
    name = None
    for key in ("town", "city", "region"):
        node = geo.get(key)
        if isinstance(node, dict) and node.get("value"):
            name = node["value"]
            break
    if not name:
        return None
    city = geo.get("city")
    short = city.get("short_name") if isinstance(city, dict) else None
    return f"{name} ({short})" if short else name


def _extract_posted_at(ad: dict[str, Any]) -> int | None:
    """Subito's publish time -> epoch seconds. We read `display_iso8601` (e.g.
    "2026-07-07T11:26:24+0200") because it carries the Europe/Rome offset; the plain
    `display` string is naive. Stored as epoch to match first/last_seen_at."""
    iso = (ad.get("dates") or {}).get("display_iso8601")
    if not iso:
        return None
    try:
        return int(datetime.fromisoformat(iso).timestamp())
    except ValueError:
        return None


def parse_item(ad: dict[str, Any]) -> Listing:
    urls = ad.get("urls") or {}
    return Listing(
        site_listing_id=str(ad.get("urn") or ad.get("item_id") or ad.get("id") or ""),
        title=ad.get("subject") or ad.get("title") or "",
        price=_extract_price(ad),
        currency="EUR",
        url=urls.get("default") or ad.get("url") or "",
        shipping_available=_extract_shipping(ad),
        location=_extract_location(ad),
        image_url=_extract_image(ad),
        posted_at=_extract_posted_at(ad),
        raw=ad,
    )


def parse_items(payload: dict[str, Any]) -> list[Listing]:
    ads = payload.get("ads") or payload.get("results") or []
    return [parse_item(ad) for ad in ads if isinstance(ad, dict)]


@register("subito")
class SubitoSite(BaseSite):
    needs_browser = True

    #: The user fine-tunes the search on Subito itself and pastes the resulting page
    #: URL; we let Subito build the API call (see ``resolve``). Everything Subito can
    #: express (region, nearby, category, sort, keyword) lives in that URL; the
    #: universal filters (price/shipping/title regex) are what Subito *can't* do.
    search_schema = [
        SearchField("url", "Subito search URL (paste from your browser's address bar)"),
        SearchField("max_pages", "Max pages to fetch (30 listings each)",
                    required=False, default="1"),
    ]

    def validate_search(self, search: dict[str, Any]) -> dict[str, Any]:
        super().validate_search(search)
        url = (search.get("url") or "").strip()
        if "subito.it/" not in url:
            raise ValueError("subito: 'url' must be a subito.it search page URL")
        search["url"] = url
        mp = search.get("max_pages") or "1"
        try:
            if int(mp) < 1:
                raise ValueError
        except (TypeError, ValueError):
            raise ValueError("subito: 'max_pages' must be a positive integer") from None
        search["max_pages"] = str(int(mp))
        return search

    def resolve(self, search: dict[str, Any], ctx: FetchContext) -> dict[str, Any]:
        # Let Subito build the hades URL: load the pasted page, capture the
        # /search/items call it fires, and store it for fast runtime replay.
        search["api_url"] = _capture_api_url(search["url"], ctx)
        return search

    def fetch(self, search: dict[str, Any], ctx: FetchContext) -> Iterable[Listing]:
        page_url = search["url"]
        # Re-resolve on the fly if the query was created without a resolve step
        # (e.g. --from-json), or if we ever need to refresh a stale URL.
        api_url = search.get("api_url") or _capture_api_url(page_url, ctx)
        max_pages = max(1, int(search.get("max_pages") or 1))

        out: list[Listing] = []
        # One warmed browsing session, paged like a human — warm once, not per page.
        with ctx.browser.session(warm_url=page_url) as page:
            for page_no in range(max_pages):
                page.goto(_with_start(api_url, page_no * PAGE_SIZE))
                body = page.text()
                try:
                    payload = json.loads(body)
                except json.JSONDecodeError as e:
                    snippet = body[:200].replace("\n", " ")
                    raise RuntimeError(
                        f"subito: expected JSON but got non-JSON (a block/challenge or the "
                        f"resolved URL went stale?): {snippet!r}"
                    ) from e
                items = parse_items(payload)
                out.extend(items)
                if len(items) < PAGE_SIZE:
                    break  # short page => last page, stop early
                if page_no < max_pages - 1:
                    time.sleep(random.uniform(2.5, 5.0))  # stay low: human pacing
        return out
