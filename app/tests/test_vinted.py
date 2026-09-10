"""Adapter contract test for Vinted — fixture replay, no network.

Mirrors tests/test_subito.py: a saved raw response + assertions that parsing yields
well-formed Listings. Vinted needs no browser, so the URL rewrite is a pure function
and is covered here too — it's the part that can silently drop a filter.
"""

import json
from pathlib import Path

import pytest

from subitoo.sites.vinted import VintedSite, api_url, parse_items

FIXTURE = Path(__file__).parent / "fixtures" / "vinted_catalog.json"

PAGE_URL = (
    "https://www.vinted.it/catalog?search_text=synology&price_from=100&currency=EUR"
    "&page=1&price_to=500&status_ids[]=6&status_ids[]=1&search_by_image_uuid="
    "&search_by_image_id=&time=1788268707&brand_ids[]=7951746&order=newest_first"
)


def test_parse_items_well_formed():
    payload = json.loads(FIXTURE.read_text())
    listings = parse_items(payload)
    assert len(listings) == 3

    # every listing must have a non-empty dedup key and a title (the contract).
    for lst in listings:
        assert lst.site_listing_id
        assert lst.title
        assert lst.shipping_available is True  # everything on Vinted ships
        assert lst.posted_at is None           # no listing timestamp in the payload
        assert lst.location is None            # no seller location either

    a, b, c = listings
    assert a.site_listing_id == "9803216148"
    assert a.title == "NAS Synology DS223"
    assert a.price == 170.0                    # item price, not total_item_price (175.7)
    assert a.currency == "EUR"
    assert a.url == "https://www.vinted.it/items/9803216148-nas-synology-ds223"
    assert a.image_url and a.image_url.startswith("https://images1.vinted.net/")

    assert b.price == 500.0
    assert c.price == 89.99
    assert c.image_url is None                 # item with no photo -> unknown


def test_parse_empty_payload():
    assert parse_items({}) == []


def test_api_url_rewrites_page_url_to_the_rest_endpoint():
    """The pasted page URL is not the URL we call: same query, different path."""
    url = api_url(PAGE_URL)
    assert url.startswith("https://www.vinted.it/api/v2/catalog/items?")
    # the filters ride across untouched...
    for expected in ("search_text=synology", "price_from=100", "price_to=500",
                     "currency=EUR", "brand_ids[]=7951746", "order=newest_first",
                     "status_ids[]=6", "status_ids[]=1"):
        assert expected in url
    # ...pagination is ours, not the page's...
    assert "page=1" in url and f"per_page=96" in url
    assert "time=" not in url
    # ...and Vinted's empty knobs are dropped rather than sent blank.
    assert "search_by_image" not in url


def test_api_url_paginates_and_keeps_the_users_marketplace():
    url = api_url(PAGE_URL.replace("vinted.it", "vinted.fr"), page=3)
    assert url.startswith("https://www.vinted.fr/api/v2/catalog/items?")
    assert "page=3" in url


def test_api_url_renames_the_category_param():
    """`catalog[]` is the one web name the API doesn't know — it would answer 200 and
    silently return unfiltered results, so it must be rewritten, not passed through."""
    url = api_url("https://www.vinted.it/catalog?search_text=nas&catalog[]=2314")
    assert "catalog_ids[]=2314" in url
    assert "catalog[]=" not in url.replace("catalog_ids[]=", "")


def test_validate_search_accepts_a_real_search_url():
    search = VintedSite().validate_search({"url": PAGE_URL, "max_pages": "2"})
    assert search["max_pages"] == "2"


def test_validate_search_defaults_and_rejects_bad_input():
    site = VintedSite()
    with pytest.raises(ValueError, match="must be a vinted search URL"):
        site.validate_search({"url": "https://www.subito.it/annunci-italia/"})
    with pytest.raises(ValueError, match="max_pages"):
        site.validate_search({"url": PAGE_URL, "max_pages": "0"})


def test_validate_search_refuses_a_param_the_api_would_silently_ignore():
    """The failure this guards is invisible at runtime: the API returns 200 with the
    filter simply not applied, so the search quietly widens. Fail at `query add`."""
    with pytest.raises(ValueError, match="unsupported search parameter"):
        VintedSite().validate_search(
            {"url": "https://www.vinted.it/catalog?search_text=nas&is_for_swap=1"}
        )
