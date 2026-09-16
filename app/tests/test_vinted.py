"""Adapter contract test for Vinted — fixture replay, no network.

Mirrors tests/test_subito.py: a saved raw response + assertions that parsing yields
well-formed Listings. The fixture is a real flight payload (trimmed to four items,
photos pruned) wrapped in the numbered rows it really arrives in, so the extractor has
to find the item list inside noise rather than parse a tidy document.

Vinted needs no browser, so the URL rebuild is a pure function and is covered here too.
"""

import json
from pathlib import Path

import pytest

from subitoo.sites.vinted import (
    VintedSite, extract_payload, page_url, parse_items,
)

FIXTURE = Path(__file__).parent / "fixtures" / "vinted_catalog_rsc.txt"
ROOT = "https://www.vinted.it/"

PAGE_URL = (
    "https://www.vinted.it/catalog?search_text=synology&price_from=100&currency=EUR"
    "&page=1&price_to=500&status_ids[]=6&status_ids[]=1&search_by_image_uuid="
    "&search_by_image_id=&time=1788268707&brand_ids[]=7951746&order=newest_first"
)


@pytest.fixture
def payload():
    return extract_payload(FIXTURE.read_text())


def test_extract_payload_finds_items_and_pagination(payload):
    assert len(payload["items"]) == 4
    assert payload["pagination"]["total_pages"] == 7
    assert payload["pagination"]["time"]  # the snapshot id later pages quote


def test_parse_items_well_formed(payload):
    listings = parse_items(payload, ROOT)
    assert len(listings) == 4  # every item the page returned

    for lst in listings:
        assert lst.site_listing_id
        assert lst.title
        assert lst.shipping_available is True  # everything on Vinted ships
        assert lst.posted_at is None           # no listing timestamp in the payload
        assert lst.location is None            # no seller location either
        assert lst.url.startswith("https://www.vinted.it/items/")

    first = listings[0]
    assert first.title == "Meta Quest 3 128 go"
    assert first.price == 320.0        # item price, not totalItemPrice (336.70)
    assert first.currency == "EUR"
    assert first.image_url.startswith("https://images1.vinted.net/")


def test_promoted_items_are_kept(payload):
    """`isPromoted` marks a seller paying to boost an ordinary listing, not an ad for
    something else — checked live: promoted items in a "meta quest" search were meta
    quest headsets, while the off-search noise in the same payload was all
    `isPromoted: false`. Dropping them would hide real matches."""
    promoted = [w for w in payload["items"] if w["productItem"]["isPromoted"]]
    assert promoted, "fixture must contain a promoted item to exercise this"

    kept = {lst.site_listing_id for lst in parse_items(payload, ROOT)}
    assert str(promoted[0]["productItem"]["id"]) in kept


def test_a_listing_with_no_photo_parses_with_no_image(payload):
    no_image = [lst for lst in parse_items(payload, ROOT) if lst.image_url is None]
    assert len(no_image) == 1
    assert no_image[0].title  # still a usable listing, just no thumbnail


def test_relative_urls_are_made_absolute_on_the_users_marketplace(payload):
    """The payload gives `/items/…`; a notification has to carry a link that opens."""
    listings = parse_items(payload, "https://www.vinted.fr/")
    assert all(lst.url.startswith("https://www.vinted.fr/items/") for lst in listings)


def test_extract_payload_raises_when_the_shape_moves():
    """Returning nothing would read to the core as 'every listing sold' and fire a
    silent no-op run; a shape change must fail the run instead."""
    with pytest.raises(RuntimeError, match="no item list"):
        extract_payload('1:HL["/_next/static/css/a.css","style"]\n2:{"catalog":{}}')


def test_extract_payload_survives_json_that_looks_like_the_end():
    """Brace-matching has to ignore braces and brackets inside strings — Vinted titles
    contain both."""
    text = ('3:{"items":{"items":[{"id":1,"productItem":{"id":1,'
            '"title":"Lego [set] {rare}","url":"/items/1-lego",'
            '"price":{"amount":"10.00","currencyCode":"EUR"},"isPromoted":false}}]},'
            '"pagination":{"current_page":1,"total_pages":1}}')
    listings = parse_items(extract_payload(text), ROOT)
    assert len(listings) == 1
    assert listings[0].title == "Lego [set] {rare}"
    assert listings[0].price == 10.0


def test_page_url_replays_the_users_page_with_our_pagination():
    """The pasted URL is the URL we call — same path, same filters, our page cursor."""
    url = page_url(PAGE_URL)
    assert url.startswith("https://www.vinted.it/catalog?")
    for expected in ("search_text=synology", "price_from=100", "price_to=500",
                     "currency=EUR", "brand_ids[]=7951746", "order=newest_first",
                     "status_ids[]=6", "status_ids[]=1"):
        assert expected in url
    assert "page=1" in url
    # the pasted `time` is dropped — page 1 establishes its own snapshot...
    assert "time=" not in url
    # ...and Vinted's empty knobs are dropped rather than sent blank.
    assert "search_by_image" not in url


def test_page_url_pins_the_snapshot_on_later_pages():
    """Without the pin, new listings arriving mid-run shift items between pages."""
    url = page_url(PAGE_URL, page=3, time_pin=1789594892)
    assert "page=3" in url and "time=1789594892" in url


def test_page_url_keeps_the_users_marketplace():
    url = page_url(PAGE_URL.replace("vinted.it", "vinted.fr"), page=2)
    assert url.startswith("https://www.vinted.fr/catalog?")
    assert "page=2" in url


def test_page_url_passes_the_category_param_through_untouched():
    """We ask for the user's own page, so its param names are the ones Vinted reads —
    `catalog[]` needs no translation (the dead REST endpoint was why it once did)."""
    url = page_url("https://www.vinted.it/catalog?search_text=nas&catalog[]=2314")
    assert "catalog[]=2314" in url


def test_validate_search_accepts_a_real_search_url():
    search = VintedSite().validate_search({"url": PAGE_URL, "max_pages": "2"})
    assert search["max_pages"] == "2"


def test_validate_search_defaults_and_rejects_bad_input():
    site = VintedSite()
    with pytest.raises(ValueError, match="must be a vinted search URL"):
        site.validate_search({"url": "https://www.subito.it/annunci-italia/"})
    with pytest.raises(ValueError, match="max_pages"):
        site.validate_search({"url": PAGE_URL, "max_pages": "0"})


def test_an_unknown_search_param_is_no_longer_refused():
    """The old REST endpoint silently ignored param names it didn't know, so they had
    to be whitelisted. Replaying the user's own page removes that failure mode: if
    Vinted ignores a param, their browser showed them the same results."""
    search = VintedSite().validate_search(
        {"url": "https://www.vinted.it/catalog?search_text=nas&is_for_swap=1"}
    )
    assert "is_for_swap=1" in page_url(search["url"])
