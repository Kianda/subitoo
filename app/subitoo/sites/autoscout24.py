"""AutoScout24 site adapter.

Strategy: AutoScout24 needs **no browser** (`needs_browser = False`). We ask for the
very search page the user pasted and read the JSON the page was rendered from:

    page  https://www.autoscout24.it/lst/bmw/3-series?fregfrom=1998&sort=age&desc=1&…
    data  <script id="__NEXT_DATA__"> … "pageProps":{"listings":[…],"numberOfPages":23}

The search results are a Next.js Pages Router page, so the listings arrive fully formed
in `__NEXT_DATA__` — id, a numeric price (`price.priceRaw`), make/model/version,
mileage, first registration, seller type and city — with no second request.

Three facts drive the design:

  the URL   is replayed as the user pasted it, plus our `page` cursor. The user builds
            the search on the site (make, model, years, km, price, gearbox, radius…)
            and every filter rides along in the path and query string, so nothing has
            to be translated. AutoScout24 also rewrites what it doesn't like — a
            localized path (`serie-3`) is redirected to the canonical one (`3-series`)
            and an unknown param is dropped — which is why the user should paste the
            URL from the address bar rather than build one by hand.
  sponsored listings (`searchResultType: "Nfm"`) are injected at the top of each page.
            They do match the search, so they are kept, but the same car then shows up
            again as an organic result on another page. The core dedups by id, yet a
            run that returns the same id twice fails the first-run seeding, so ids are
            deduplicated here, within the run.
  no dates  the listing carries no publication timestamp, only the first registration
            of the car. Sorting the pasted search by `sort=age&desc=1` ("most recent
            first") is what keeps new listings on page 1; posting time stays unknown.

`__NEXT_DATA__` is a Next.js internal, not a contract: it can change shape on any
AutoScout24 deploy. So parsing fails **loudly** (``extract_page``) rather than
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

PAGE_SIZE = 20  # what the search page returns per page; not ours to set.

#: AutoScout24 stops paginating here (20 x 20). `numberOfPages` can report more, but
#: the site won't serve past it, so a search wider than ~400 results is trimmed by the
#: site, not by us.
MAX_SITE_PAGES = 20

#: Params we set ourselves and therefore strip from the pasted URL. `page` is our
#: cursor; `search_id`/`source` are per-visit tracking the site adds to its own links.
IGNORED = {"page", "search_id", "source"}

NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json"[^>]*>(.*?)</script>', re.S
)


def page_url(url: str, page: int = 1) -> str:
    """Rebuild the pasted search URL for one page of results.

    Keeps the user's host (autoscout24.it / .de / .fr …) and path (`/lst/bmw/3-series`)
    so the search stays exactly the one they built; only `page` is ours.
    """
    parts = urlsplit(url)
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=False)
        if k not in IGNORED
    ]
    query.append(("page", str(page)))
    return urlunsplit((parts.scheme, parts.netloc, parts.path,
                       urlencode(query, safe=","), ""))


def extract_page(html: str) -> dict[str, Any]:
    """Pull `{listings, numberOfPages}` out of a search page.

    Raises rather than returning nothing: an empty result is indistinguishable from
    "the search matched nothing", which the core would treat as every listing having
    disappeared. A shape change has to fail the run and latch the query to 'error'.
    A search that genuinely matches nothing still parses: `listings` is there, empty.
    """
    m = NEXT_DATA_RE.search(html)
    if not m:
        raise RuntimeError(
            f"autoscout24: no __NEXT_DATA__ in the response — the page shape changed, "
            f"or this wasn't a search page (got {len(html)} bytes)"
        )
    try:
        props = json.loads(m.group(1))["props"]["pageProps"]
    except (ValueError, KeyError, TypeError) as e:
        raise RuntimeError(f"autoscout24: could not parse __NEXT_DATA__: {e}") from e
    listings = props.get("listings")
    if not isinstance(listings, list):
        raise RuntimeError(
            "autoscout24: __NEXT_DATA__ has no 'listings' — not a search results page, "
            "or AutoScout24 moved them"
        )
    return {"listings": listings, "numberOfPages": props.get("numberOfPages") or 1}


def _title(vehicle: dict[str, Any]) -> str:
    """Make + model + the seller's free-text version line: "Mazda MX-5 1.8 Fire".

    Sellers often repeat the make or model in the version line ("330ci Manuale Coupè"
    under model "330", "MX-5 1.8 Black Record Series" under "MX-5"), so a part is only
    prepended when the text doesn't already start with it. The model has to survive:
    it is what a `title_include` regex is most likely to test.
    """
    make = (vehicle.get("make") or "").strip()
    model = (vehicle.get("model") or "").strip()
    title = (vehicle.get("modelVersionInput") or "").strip()
    if make and title.lower().startswith(make.lower()):
        title = title[len(make):].strip()
    if model and not title.lower().startswith(model.lower()):
        title = f"{model} {title}"
    return f"{make} {title}".strip()


def _price(node: Any) -> float | None:
    """`priceRaw` is the plain number behind the formatted "€ 12.800". A missing or
    zero price is price-on-request, which the core represents as None."""
    if not isinstance(node, dict):
        return None
    try:
        price = float(node["priceRaw"])
    except (KeyError, TypeError, ValueError):
        return None
    return price or None


def _location(node: Any) -> str | None:
    if not isinstance(node, dict):
        return None
    city = (node.get("city") or "").strip()
    zip_ = (node.get("zip") or "").strip()
    return " ".join(p for p in (zip_, city) if p) or None


def parse_item(item: dict[str, Any], root: str) -> Listing:
    vehicle = item.get("vehicle") or {}
    url = item.get("url") or ""
    images = item.get("images") or []
    return Listing(
        site_listing_id=str(item.get("id") or ""),
        title=_title(vehicle),
        price=_price(item.get("price")),
        currency="EUR",
        # the payload gives a site-relative path; the core wants a link that opens.
        url=urljoin(root, url) if url else "",
        # A car is collected or delivered by arrangement, never "shipped": there is no
        # field for it, so the universal shipping filter stays unknown, not guessed.
        shipping_available=None,
        location=_location(item.get("location")),
        image_url=images[0] if images else None,
        posted_at=None,
        raw=item,
    )


def parse_items(page: dict[str, Any], root: str = "https://www.autoscout24.it/") -> list[Listing]:
    """Listings from an ``extract_page`` result — every car the page returned,
    sponsored ones included (they match the search too)."""
    return [
        parse_item(item, root)
        for item in page.get("listings") or []
        if isinstance(item, dict) and item.get("id")
    ]


@register("autoscout24")
class AutoScout24Site(BaseSite):
    needs_browser = False

    #: The user builds the whole search on AutoScout24 and pastes the resulting URL.
    #: One marketplace per country (.it, .de, .fr, .com, .at, .be, .nl, .es …), so the
    #: TLD stays open but bounded, or `autoscout24.it.example.com` would claim it.
    url_host_pattern = r"(^|\.)autoscout24\.[a-z]{2,3}$"

    search_schema = [
        SearchField("url", "AutoScout24 search URL (paste from your browser's address "
                           "bar; sort by 'most recent' so new cars land on page 1)"),
        SearchField("max_pages", f"Max pages to fetch ({PAGE_SIZE} listings each)",
                    required=False, default="1"),
    ]

    #: Price rides along in the pasted URL and AutoScout24 answers it server-side, and
    #: a car is never "shipped" (see ``shipping_available`` above), so the wizard asks
    #: for neither — only the title regex, the one thing the site's filters cannot
    #: express (e.g. keeping only "coupé" versions). The core still applies whatever a
    #: query stores; this just stops the wizard asking twice.
    universal_filters = ("title_include", "title_exclude")

    def validate_search(self, search: dict[str, Any]) -> dict[str, Any]:
        super().validate_search(search)  # required fields + the autoscout24 URL check
        mp = search.get("max_pages") or "1"
        try:
            if int(mp) < 1:
                raise ValueError
        except (TypeError, ValueError):
            raise ValueError("autoscout24: 'max_pages' must be a positive integer") from None
        search["max_pages"] = str(int(mp))
        return search

    def _get_page(self, ctx: FetchContext, url: str) -> dict[str, Any]:
        resp = ctx.http.get(url)
        if resp.status_code != 200:
            snippet = resp.text[:200].replace("\n", " ")
            raise RuntimeError(
                f"autoscout24: {url} -> HTTP {resp.status_code} "
                f"(a block/challenge, or a search the site rejected?): {snippet!r}"
            )
        return extract_page(resp.text)

    def fetch(self, search: dict[str, Any], ctx: FetchContext) -> Iterable[Listing]:
        url = search["url"]
        parts = urlsplit(url)
        root = f"{parts.scheme}://{parts.netloc}/"
        max_pages = max(1, int(search.get("max_pages") or 1))

        out: list[Listing] = []
        seen: set[str] = set()
        for page_no in range(1, max_pages + 1):
            page = self._get_page(ctx, page_url(url, page_no))
            for lst in parse_items(page, root):
                # a sponsored car reappears as an organic one on another page
                if lst.site_listing_id not in seen:
                    seen.add(lst.site_listing_id)
                    out.append(lst)

            total_pages = min(page.get("numberOfPages") or 1, MAX_SITE_PAGES)
            if page_no >= total_pages:
                break  # last page — don't ask for empty ones
            if page_no < max_pages:
                time.sleep(random.uniform(2.5, 5.0))  # stay low: human pacing
        return out
