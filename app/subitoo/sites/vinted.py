"""Vinted site adapter.

Strategy: Vinted needs **no browser** (`needs_browser = False`). We ask for the very
catalog page the user pasted and read the JSON the page was rendered from:

    page  https://www.vinted.it/catalog?search_text=…&price_from=100&catalog[]=…
    call  the same URL + header `RSC: 1`  ->  text/x-component (the flight payload)

Vinted's catalog is a Next.js App Router page. Asking for it normally returns ~7MB of
HTML; asking with `RSC: 1` returns the React Server Components *flight payload* the
client would hydrate from, and the listings sit in it as plain JSON:

    "items":{"items":[{"id":…,"productItem":{"title":…,"price":{"amount":"320.00",
                       "currencyCode":"EUR"},"isPromoted":false,…}}], …},
    "pagination":{"current_page":1,"per_page":96,"total_entries":592,"total_pages":7}

Three facts drive the design:

  no API    the REST endpoint this adapter used to call, `/api/v2/catalog/items`, now
            answers 404 — on every marketplace, with and without params, and from
            inside a real browser on a warm session, while sibling endpoints
            (`/api/v2/banners`) still answer 200. It is gone, not blocked, so there is
            nothing to rebuild the call against. The page is the source now.
  the URL   is replayed as the user pasted it. We are asking for *their* page, so
            every filter is applied by Vinted exactly as their browser saw it, and no
            param has to be translated or whitelisted — the old REST endpoint needed
            both; this does not.
  noise     Vinted's search is fuzzy: a narrow query pads its results with loosely
            related items (a Bottega Veneta boot in a "meta quest" search). Those are
            real results carrying `isPromoted: false` — the user's own browser shows
            them the same page — so they are NOT filtered here. `title_include` is the
            tool for that, which is why it stays in ``universal_filters``.
            `isPromoted: true` is a seller paying to boost an otherwise ordinary
            listing (often squarely on-topic), so promoted items are kept too: dropping
            them would hide real matches.

The flight payload is a Next.js internal format, not a contract: it can change shape on
any Vinted deploy. So parsing fails **loudly** (``extract_payload``) rather than
returning zero listings, which the core would read as "everything sold".

Parsing is kept in module-level pure functions so it can be unit-tested against a
recorded fixture with no network (the adapter contract harness).
"""

from __future__ import annotations

import json
import random
import re
import time
from collections.abc import Iterable
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from subitoo.core.fetch import FetchContext
from subitoo.core.models import Listing, SearchField
from subitoo.sites.base import BaseSite
from subitoo.registry import register

PAGE_SIZE = 96  # what the catalog page returns per page; not ours to set.

#: Vinted stops paginating here (10 x 96). `total_pages` never exceeds it, so a search
#: wider than ~960 results is trimmed by the site, not by us.
MAX_SITE_PAGES = 10

#: The header that turns a page request into a flight-payload request.
RSC_HEADER = {"RSC": "1"}

#: Params we set ourselves and therefore strip from the pasted URL, plus Vinted's empty
#: knobs. `time` pins a result set (see ``fetch``); `page` is our cursor.
IGNORED = {"page", "time", "search_by_image_uuid", "search_by_image_id",
           "catalog_from", "disabled_personalization"}

#: Where the listings live in the flight payload, and the object that follows them.
ITEMS_KEY = '"items":{"items":['
PAGINATION_RE = re.compile(r'"pagination":(\{[^}]*\})')


def _params(page_url: str) -> list[tuple[str, str]]:
    """The pasted URL's query, minus what we set ourselves and minus blanks.

    Blanks go because Vinted leaves empty knobs in the URL (`search_by_image_uuid=`)
    and an empty value filters nothing. Everything else rides along untouched: this is
    the user's own page, so its param names are by definition the ones Vinted reads.
    """
    return [
        (k, v)
        for k, v in parse_qsl(urlsplit(page_url).query, keep_blank_values=False)
        if k not in IGNORED
    ]


def page_url(url: str, page: int = 1, time_pin: int | None = None) -> str:
    """Rebuild the pasted catalog URL for one page of results.

    Keeps the user's host (vinted.it / .fr / .de …) so the search stays on their
    marketplace. ``time_pin`` is the `time` value the first page reported: Vinted uses
    it to freeze the result set, so pages 2..n describe the same snapshot instead of
    shifting under us as new listings arrive mid-run.
    """
    parts = urlsplit(url)
    query = _params(url) + [("page", str(page))]
    if time_pin:
        query.append(("time", str(time_pin)))
    # safe="[]" keeps `catalog[]`/`status_ids[]` readable, matching what the site sends.
    return urlunsplit((parts.scheme, parts.netloc, parts.path,
                       urlencode(query, safe="[]"), ""))


def _json_after(text: str, start: int) -> Any:
    """Decode the one JSON value starting at ``text[start]`` (a `[` or `{`).

    The flight payload is a stream of numbered rows, not a single JSON document, so it
    can't be parsed whole — we brace-match the one value we need out of it.
    """
    depth, in_str, esc = 0, False, False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start:i + 1])
    raise ValueError("unterminated JSON value")


def extract_payload(text: str) -> dict[str, Any]:
    """Pull `{items, pagination}` out of a flight payload.

    Raises rather than returning nothing: an empty result is indistinguishable from
    "the search matched nothing", which the core would treat as every listing having
    disappeared. A shape change has to fail the run and latch the query to 'error'.
    """
    i = text.find(ITEMS_KEY)
    if i < 0:
        head = text[:200].replace("\n", " ")
        raise RuntimeError(
            f"vinted: no item list in the response — Vinted's page shape changed, or "
            f"this wasn't a flight payload (got {len(text)} bytes): {head!r}"
        )
    try:
        items = _json_after(text, i + len(ITEMS_KEY) - 1)
    except ValueError as e:
        raise RuntimeError(f"vinted: could not parse the item list: {e}") from e

    m = PAGINATION_RE.search(text)
    pagination = json.loads(m.group(1)) if m else {}
    return {"items": items, "pagination": pagination}


def _price(node: Any) -> tuple[float | None, str]:
    """Vinted nests money as {"amount": "320.00", "currencyCode": "EUR"}. We read the
    item price, not `totalItemPrice` (which adds buyer protection), because that is
    what the URL's own `price_from`/`price_to` filter acts on."""
    if not isinstance(node, dict):
        return None, "EUR"
    currency = node.get("currencyCode") or "EUR"
    try:
        return float(node["amount"]), currency
    except (KeyError, TypeError, ValueError):
        return None, currency


def parse_item(product: dict[str, Any], root: str) -> Listing:
    price, currency = _price(product.get("price"))
    url = product.get("url") or ""
    return Listing(
        site_listing_id=str(product.get("id") or ""),
        title=product.get("title") or "",
        price=price,
        currency=currency,
        # the payload gives a site-relative path; the core wants a link that opens.
        url=urljoin(root, url) if url else "",
        # Every Vinted item ships — there is no "pickup only" listing and no field for
        # it — so the universal shipping filter is a no-op here rather than unknown.
        shipping_available=True,
        # No seller location in the catalog payload, and no listing timestamp either:
        # nothing in it tracks `order=newest_first`, so posting time stays unknown
        # rather than wrong.
        location=None,
        image_url=product.get("thumbnailUrl") or None,
        posted_at=None,
        raw=product,
    )


def parse_items(payload: dict[str, Any], root: str = "https://www.vinted.it/") -> list[Listing]:
    """Listings from an ``extract_payload`` result — every item the page returned.

    Nothing is dropped, including `isPromoted` ones: a promoted item is a seller paying
    to boost a normal listing, not an off-search ad, so it is a real result the user's
    own browser would show them. The flag stays available in ``Listing.raw``.
    """
    out: list[Listing] = []
    for wrapper in payload.get("items") or []:
        if not isinstance(wrapper, dict):
            continue
        product = wrapper.get("productItem")
        if not isinstance(product, dict):
            continue
        out.append(parse_item(product, root))
    return out


@register("vinted")
class VintedSite(BaseSite):
    needs_browser = False

    #: The user builds the whole search on Vinted — keywords, category, brand, size,
    #: condition, price — and pastes the resulting URL; every filter rides along in its
    #: query string, and we ask for that same page back.
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

    #: Price rides along in the pasted URL and Vinted answers it server-side, and every
    #: item ships (see ``shipping_available`` above), so the wizard asks for neither —
    #: only the title regex, the one thing Vinted cannot express. The core still applies
    #: whatever a query stores; this just stops the wizard asking twice.
    universal_filters = ("title_include", "title_exclude")

    def validate_search(self, search: dict[str, Any]) -> dict[str, Any]:
        super().validate_search(search)  # required fields + the vinted URL check
        mp = search.get("max_pages") or "1"
        try:
            if int(mp) < 1:
                raise ValueError
        except (TypeError, ValueError):
            raise ValueError("vinted: 'max_pages' must be a positive integer") from None
        search["max_pages"] = str(int(mp))
        return search

    def _warm(self, ctx: FetchContext, root: str) -> None:
        """One GET of the site root, so the run carries the cookie set a browser would.

        Not an auth step: the flight payload comes back 200 on a cold session too. It
        is here because arriving at a deep search URL with no cookies at all is the
        least browser-like way to show up.
        """
        ctx.http.get(root)

    def _get_page(self, ctx: FetchContext, root: str, url: str) -> dict[str, Any]:
        resp = ctx.http.get(url, headers=RSC_HEADER)
        if resp.status_code in (401, 403):
            self._warm(ctx, root)  # challenged mid-run — take the cookies and retry once
            resp = ctx.http.get(url, headers=RSC_HEADER)
        if resp.status_code != 200:
            snippet = resp.text[:200].replace("\n", " ")
            raise RuntimeError(
                f"vinted: {url} -> HTTP {resp.status_code} "
                f"(a block/challenge, or a filter Vinted rejected?): {snippet!r}"
            )
        if resp.headers.get("content-type", "").startswith("text/html"):
            raise RuntimeError(
                f"vinted: {url} answered HTML, not a flight payload — the RSC header "
                f"was ignored, which means Vinted changed how the catalog renders."
            )
        return extract_payload(resp.text)

    def fetch(self, search: dict[str, Any], ctx: FetchContext) -> Iterable[Listing]:
        url = search["url"]
        parts = urlsplit(url)
        root = f"{parts.scheme}://{parts.netloc}/"
        max_pages = max(1, int(search.get("max_pages") or 1))

        self._warm(ctx, root)
        out: list[Listing] = []
        time_pin: int | None = None
        for page_no in range(1, max_pages + 1):
            payload = self._get_page(ctx, root, page_url(url, page_no, time_pin))
            out.extend(parse_items(payload, root))

            pagination = payload.get("pagination") or {}
            # Page 1 hands us the snapshot id every later page must quote, or the
            # result set shifts under us as new listings arrive mid-run.
            time_pin = time_pin or pagination.get("time")
            total_pages = min(pagination.get("total_pages") or 1, MAX_SITE_PAGES)
            if page_no >= total_pages:
                break  # last page — don't ask for empty ones
            if page_no < max_pages:
                time.sleep(random.uniform(2.5, 5.0))  # stay low: human pacing
        return out
