"""Adapter contract test for Subito — fixture replay, no network/browser.

This is the shape every new site adapter should follow (SPEC.md section 12): a saved
raw response + assertions that parsing yields well-formed Listings.
"""

import json
from pathlib import Path

from subitoo.sites.subito import parse_items

FIXTURE = Path(__file__).parent / "fixtures" / "subito_search.json"


def test_parse_items_well_formed():
    payload = json.loads(FIXTURE.read_text())
    listings = parse_items(payload)
    assert len(listings) == 2

    # every listing must have a non-empty dedup key and a title (the contract).
    for lst in listings:
        assert lst.site_listing_id
        assert lst.title

    a, b = listings
    assert a.site_listing_id == "urn:subito:12345"
    assert a.price == 120.0
    assert a.shipping_available is True
    assert a.location == "Milano (MI)"       # town + province short_name
    assert a.image_url == ("https://images.sbito.it/api/v1/sbt-ads-images-pro/images/"
                           "8c/8c4a6adb-d972-44be-97ec-a8aea29b13ae?rule=gallery-desktop-1x-auto")
    assert a.posted_at == 1783416384          # 2026-07-07T11:26:24+0200 -> epoch

    assert b.price == 30.0
    assert b.shipping_available is None       # not exposed -> unknown
    assert b.location == "Roma"
    assert b.image_url is None
    assert b.posted_at is None                # no dates node -> unknown


def test_parse_empty_payload():
    assert parse_items({}) == []
