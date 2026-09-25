"""Adapter contract test for AutoScout24 — fixture replay, no network.

Mirrors tests/test_vinted.py: a saved raw response + assertions that parsing yields
well-formed Listings. The fixture is a real search page (trimmed to five cars, seller
contacts and street addresses removed) wrapped in the HTML it really arrives in, so the
extractor has to find `__NEXT_DATA__` among other scripts rather than parse a tidy
document. It keeps the page's sponsored ("Nfm") slots, one of which is the same car as
an organic result further down — exactly as the live page served it. One car's price was
blanked to what a price-on-request listing sends.

AutoScout24 needs no browser, so the URL rebuild is a pure function and is covered here
too, and ``fetch`` is exercised against a fake HTTP client.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from subitoo.sites import autoscout24
from subitoo.sites.autoscout24 import AutoScout24Site, extract_page, page_url, parse_items

FIXTURE = Path(__file__).parent / "fixtures" / "autoscout24_search.html"
ROOT = "https://www.autoscout24.it/"

SEARCH_URL = (
    "https://www.autoscout24.it/lst/bmw/3-series?fregfrom=1998&fregto=2006&cy=I"
    "&damaged_listing=exclude&sort=age&desc=1&ustate=N%2CU&atype=C"
    "&search_id=2k4t9x&source=listpage_pagination&page=4"
)


@pytest.fixture
def page():
    return extract_page(FIXTURE.read_text())


def test_extract_page_finds_listings_and_page_count(page):
    assert len(page["listings"]) == 5
    assert page["numberOfPages"] == 23


def test_parse_items_well_formed(page):
    listings = parse_items(page, ROOT)
    assert len(listings) == 5  # every car the page returned, sponsored ones too

    for lst in listings:
        assert lst.site_listing_id
        assert lst.title.startswith("BMW ")
        assert lst.currency == "EUR"
        assert lst.shipping_available is None  # cars aren't shipped: unknown, not False
        assert lst.posted_at is None           # no publication date in the payload
        assert lst.location
        assert lst.url.startswith("https://www.autoscout24.it/annunci/")

    first = listings[0]
    assert first.title == "BMW 330ci Manuale Coupè"  # make + the seller's version line
    assert first.price == 12800.0                     # priceRaw, not "€ 12.800"
    assert first.location == "36060 Romano d'Ezzelino - Vicenza - VI"
    assert first.image_url.startswith("https://prod.pictures.autoscout24.net/")


@pytest.mark.parametrize("vehicle, title", [
    ({"make": "BMW", "model": "330", "modelVersionInput": "330ci Manuale Coupè"},
     "BMW 330ci Manuale Coupè"),                      # version already names the model
    ({"make": "Mazda", "model": "MX-5", "modelVersionInput": "1.8 Fire (high)"},
     "Mazda MX-5 1.8 Fire (high)"),                   # ...or doesn't: the model is added
    ({"make": "Mazda", "model": "MX-5", "modelVersionInput": "Mazda MX-5 2.0"},
     "Mazda MX-5 2.0"),                               # make not doubled either
    ({"make": "BMW", "model": "320", "modelVersionInput": None}, "BMW 320"),
])
def test_title_always_carries_make_and_model_once(vehicle, title):
    """Sellers write the version line freely; a `title_include` on the model must still
    match, and the notification shouldn't read "Mazda MX-5 Mazda MX-5"."""
    assert autoscout24._title(vehicle) == title


def test_price_on_request_is_none(page):
    """A missing number behind the price label must not become 0 EUR — that would pass
    every price_max filter and read as the bargain of the century."""
    on_request = [lst for lst in parse_items(page, ROOT) if lst.price is None]
    assert len(on_request) == 1
    assert on_request[0].title


def test_sponsored_slots_duplicate_an_organic_result(page):
    """The fixture's premise, and the reason ``fetch`` dedups: the page serves the same
    car twice, once as a sponsored slot and once organically."""
    ids = [lst.site_listing_id for lst in parse_items(page, ROOT)]
    assert len(ids) - len(set(ids)) == 1


def test_relative_urls_are_made_absolute_on_the_users_marketplace(page):
    listings = parse_items(page, "https://www.autoscout24.de/")
    assert all(lst.url.startswith("https://www.autoscout24.de/annunci/") for lst in listings)


def test_extract_page_raises_when_the_shape_moves():
    """Returning nothing would read to the core as 'every listing sold' and fire a
    silent no-op run; a shape change must fail the run instead."""
    with pytest.raises(RuntimeError, match="no __NEXT_DATA__"):
        extract_page("<html><body>Access denied</body></html>")
    with pytest.raises(RuntimeError, match="no 'listings'"):
        extract_page('<script id="__NEXT_DATA__" type="application/json">'
                     '{"props":{"pageProps":{"vehicle":{}}}}</script>')


def test_a_search_that_matches_nothing_is_not_an_error():
    page = extract_page('<script id="__NEXT_DATA__" type="application/json">'
                        '{"props":{"pageProps":{"listings":[],"numberOfPages":0}}}</script>')
    assert parse_items(page, ROOT) == []


def test_page_url_replays_the_users_search_with_our_pagination():
    """Same host, path and filters; the site's own tracking params and the pasted
    `page` are replaced by our cursor."""
    url = page_url(SEARCH_URL, page=2)
    assert url.startswith("https://www.autoscout24.it/lst/bmw/3-series?")
    for expected in ("fregfrom=1998", "fregto=2006", "cy=I", "sort=age", "desc=1",
                     "ustate=N,U", "damaged_listing=exclude", "atype=C"):
        assert expected in url
    assert url.endswith("page=2")
    assert url.count("page=") == 1
    assert "search_id" not in url and "source=" not in url


def test_validate_search_accepts_a_real_search_url_on_any_marketplace():
    site = AutoScout24Site()
    assert site.validate_search({"url": SEARCH_URL, "max_pages": "2"})["max_pages"] == "2"
    assert site.validate_search({"url": "https://www.autoscout24.de/lst/bmw"})["max_pages"] == "1"


def test_validate_search_rejects_bad_input():
    site = AutoScout24Site()
    with pytest.raises(ValueError, match="must be a autoscout24 search URL"):
        site.validate_search({"url": "https://www.subito.it/annunci-italia/"})
    with pytest.raises(ValueError, match="must be a autoscout24 search URL"):
        site.validate_search({"url": "https://www.autoscout24.it.example.com/lst"})
    with pytest.raises(ValueError, match="max_pages"):
        site.validate_search({"url": SEARCH_URL, "max_pages": "0"})


class _FakeHttp:
    def __init__(self, pages: dict[int, str]):
        self.pages = pages
        self.asked: list[str] = []

    def get(self, url, **kwargs):
        self.asked.append(url)
        page_no = int(url.rsplit("page=", 1)[1])
        return SimpleNamespace(status_code=200, text=self.pages[page_no])


def test_fetch_dedups_within_the_run_and_stops_at_max_pages(monkeypatch):
    """The core's first-run seeding inserts blindly, so an id returned twice fails the
    run on the UNIQUE constraint — and a sponsored car does reappear organically. The
    run must hand back each car once. Two identical pages → the five cars' four
    distinct ids, and no page 3."""
    monkeypatch.setattr(autoscout24.time, "sleep", lambda s: None)
    html = FIXTURE.read_text()
    http = _FakeHttp({1: html, 2: html})
    ctx = SimpleNamespace(http=http)

    listings = list(AutoScout24Site().fetch({"url": SEARCH_URL, "max_pages": "2"}, ctx))
    ids = [lst.site_listing_id for lst in listings]
    assert len(ids) == len(set(ids)) == 4
    assert len(http.asked) == 2


def test_fetch_raises_on_a_block():
    http = SimpleNamespace(get=lambda url, **kw: SimpleNamespace(status_code=403, text="no"))
    with pytest.raises(RuntimeError, match="HTTP 403"):
        list(AutoScout24Site().fetch({"url": SEARCH_URL}, SimpleNamespace(http=http)))
