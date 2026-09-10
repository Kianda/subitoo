"""Vinted site adapter.

Strategy: Vinted needs **no browser** (`needs_browser = False`). Its catalog page is
server-rendered — the page never fires the results API, so there is nothing to capture
the way Subito's `resolve()` does. Instead the REST endpoint the mobile app uses takes
the *same* query string as the page URL, so we rebuild the call ourselves:

    page  https://www.vinted.it/catalog?search_text=…&price_from=100&brand_ids[]=…
    api   https://www.vinted.it/api/v2/catalog/items?search_text=…&price_from=100&…

Two facts drive the design:

  auth      the API rejects a cold session (401 `invalid_authentication_token`). One GET
            of the site root mints an anonymous `access_token_web` cookie (a ~24h JWT)
            that `ctx.http` then carries. A non-impersonated client gets a Cloudflare
            403 instead — the Firefox TLS profile is what gets us in, so `ctx.http`
            (curl_cffi) is required; plain requests/httpx will not do.
  params    the API **silently ignores** a param name it doesn't know: it answers 200
            with the filter simply not applied. A mistyped/renamed filter would quietly
            widen the search instead of failing, so `validate_search()` refuses any name
            not known-good (`ALLOWED`) rather than passing it through and hoping.

Since the user sets every filter on Vinted itself, the search URL *is* the filter spec —
Subitoo's universal price/shipping filters are redundant here (leave them blank) and
`title_include`/`title_exclude` are what Vinted can't express.

Parsing is kept in module-level pure functions so it can be unit-tested against a
recorded fixture with no network (the adapter contract harness).
"""

from __future__ import annotations

import random
import time
from collections.abc import Iterable
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from subitoo.core.fetch import FetchContext
from subitoo.core.models import Listing, SearchField
from subitoo.sites.base import BaseSite
from subitoo.registry import register

API_PATH = "/api/v2/catalog/items"
PAGE_SIZE = 96  # Vinted's ceiling; asking for more silently returns 96.

#: Web param name -> API param name. The two mostly agree; this is the exception —
#: the API ignores `catalog[]` (200, unfiltered) and wants `catalog_ids[]`.
RENAME = {"catalog[]": "catalog_ids[]"}

#: Web-only params the API has no use for — dropped rather than refused. `page` and
#: `time` are dropped because we set our own (see ``api_url``).
IGNORED = {"page", "time", "search_by_image_uuid", "search_by_image_id",
           "catalog_from", "disabled_personalization"}

#: Params the API is known to act on, checked *after* RENAME. Anything else is refused
#: at `query add` time — see the module docstring on silent ignoring. Each name here was
#: verified live by watching the result count move; add to the set the same way.
ALLOWED = {
    "search_text", "price_from", "price_to", "currency", "order",
    "status_ids[]", "brand_ids[]", "catalog_ids[]", "color_ids[]",
    "size_ids[]", "material_ids[]", "video_game_rating_ids[]",
}


def _params(page_url: str) -> list[tuple[str, str]]:
    """The page URL's query as (api_name, value) pairs: renamed, with the web-only and
    blank ones dropped. Blanks go because Vinted leaves empty knobs in the URL
    (`search_by_image_uuid=`) and an empty value filters nothing."""
    return [
        (RENAME.get(k, k), v)
        for k, v in parse_qsl(urlsplit(page_url).query, keep_blank_values=False)
        if k not in IGNORED
    ]


def api_url(page_url: str, page: int = 1, per_page: int = PAGE_SIZE) -> str:
    """Rebuild the pasted catalog page URL as the REST call that returns its listings.

    Keeps the user's host (vinted.it / .fr / .de …) so the search stays on their
    marketplace, and sets our own pagination.
    """
    parts = urlsplit(page_url)
    query = _params(page_url) + [("page", str(page)), ("per_page", str(per_page))]
    # safe="[]" keeps `status_ids[]` readable and matches what the site itself sends;
    # the API accepts the bracket form and the comma form alike.
    return urlunsplit((parts.scheme, parts.netloc, API_PATH,
                       urlencode(query, safe="[]"), ""))


def _price(node: Any) -> tuple[float | None, str]:
    """Vinted nests money as {"amount": "170.0", "currency_code": "EUR"}. We read the
    item price, not `total_item_price` (which adds buyer protection), because that is
    what the URL's own `price_from`/`price_to` filter acts on."""
    if not isinstance(node, dict):
        return None, "EUR"
    currency = node.get("currency_code") or "EUR"
    try:
        return float(node["amount"]), currency
    except (KeyError, TypeError, ValueError):
        return None, currency


def parse_item(item: dict[str, Any]) -> Listing:
    price, currency = _price(item.get("price"))
    photo = item.get("photo") or {}
    return Listing(
        site_listing_id=str(item.get("id") or ""),
        title=item.get("title") or "",
        price=price,
        currency=currency,
        url=item.get("url") or "",
        # Every Vinted item ships — there is no "pickup only" listing and no field for
        # it — so the universal shipping filter is a no-op here rather than unknown.
        shipping_available=True,
        # The catalog payload carries no seller location, and no listing timestamp:
        # `photo.high_resolution.timestamp` is when the *photo* was uploaded and does
        # not track `order=newest_first`, so posting time stays unknown rather than wrong.
        location=None,
        image_url=photo.get("url"),
        posted_at=None,
        raw=item,
    )


def parse_items(payload: dict[str, Any]) -> list[Listing]:
    items = payload.get("items") or []
    return [parse_item(it) for it in items if isinstance(it, dict)]


@register("vinted")
class VintedSite(BaseSite):
    needs_browser = False

    #: The user builds the whole search on Vinted — keywords, category, brand, size,
    #: condition, price — and pastes the resulting URL; every filter rides along in its
    #: query string. Subitoo's own price/shipping filters are redundant here; the title
    #: regex is the one thing Vinted can't express.
    #: The base class uses this both to claim a pasted URL (so `query add` doesn't have
    #: to ask which site) and to reject a URL from anywhere else. Vinted runs one
    #: marketplace per country, so the TLD stays open — .it, .fr, .de, .com and
    #: two-label ones like .co.uk — but bounded, or `vinted.it.example.com` would
    #: claim to be Vinted.
    url_host_pattern = r"(^|\.)vinted\.[a-z]{2,3}(\.[a-z]{2,3})?$"

    search_schema = [
        SearchField("url", "Vinted search URL (paste from your browser's address bar)"),
        SearchField("max_pages", f"Max pages to fetch ({PAGE_SIZE} listings each)",
                    required=False, default="1"),
    ]

    def validate_search(self, search: dict[str, Any]) -> dict[str, Any]:
        super().validate_search(search)  # required fields + the vinted URL check
        url = search["url"]
        unknown = sorted({k for k, _ in _params(url)} - ALLOWED)
        if unknown:
            # Refused rather than passed through: the API would answer 200 and drop the
            # filter, silently widening the search. See the module docstring.
            raise ValueError(
                f"vinted: unsupported search parameter(s) {', '.join(unknown)} — the API "
                f"ignores names it doesn't know, which would silently drop that filter. "
                f"Remove them from the URL, or add them to ALLOWED in sites/vinted.py "
                f"once you've confirmed the API acts on them."
            )
        mp = search.get("max_pages") or "1"
        try:
            if int(mp) < 1:
                raise ValueError
        except (TypeError, ValueError):
            raise ValueError("vinted: 'max_pages' must be a positive integer") from None
        search["max_pages"] = str(int(mp))
        return search

    def _warm(self, ctx: FetchContext, root: str) -> None:
        """Mint the anonymous session cookie the API requires (see module docstring)."""
        ctx.http.get(root)

    def _get_page(self, ctx: FetchContext, root: str, url: str) -> dict[str, Any]:
        resp = ctx.http.get(url)
        if resp.status_code == 401:
            self._warm(ctx, root)  # the anon token aged out mid-run — mint a new one
            resp = ctx.http.get(url)
        if resp.status_code != 200:
            snippet = resp.text[:200].replace("\n", " ")
            raise RuntimeError(
                f"vinted: {url} -> HTTP {resp.status_code} "
                f"(a block/challenge, or a filter Vinted rejected?): {snippet!r}"
            )
        try:
            return resp.json()
        except ValueError as e:
            snippet = resp.text[:200].replace("\n", " ")
            raise RuntimeError(
                f"vinted: expected JSON but got non-JSON: {snippet!r}"
            ) from e

    def fetch(self, search: dict[str, Any], ctx: FetchContext) -> Iterable[Listing]:
        page_url = search["url"]
        parts = urlsplit(page_url)
        root = f"{parts.scheme}://{parts.netloc}/"
        max_pages = max(1, int(search.get("max_pages") or 1))

        self._warm(ctx, root)
        out: list[Listing] = []
        for page_no in range(1, max_pages + 1):
            payload = self._get_page(ctx, root, api_url(page_url, page_no))
            out.extend(parse_items(payload))
            total_pages = (payload.get("pagination") or {}).get("total_pages") or 1
            if page_no >= total_pages:
                break  # last page — don't ask for empty ones
            if page_no < max_pages:
                time.sleep(random.uniform(2.5, 5.0))  # stay low: human pacing
        return out
