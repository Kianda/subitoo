from subitoo.core.filters import apply_filters
from subitoo.core.models import Filters, Listing


def L(title="Item", price=100.0, shipping=None):
    return Listing(site_listing_id="x", title=title, price=price, shipping_available=shipping)


def test_price_range():
    items = [L(price=50), L(price=150), L(price=None)]
    out = apply_filters(items, Filters(price_min=100, price_max=200))
    assert [x.price for x in out] == [150]  # 50 too low, None fails a range check


def test_shipping():
    items = [L(shipping=True), L(shipping=False), L(shipping=None)]
    assert len(apply_filters(items, Filters(shipping="yes"))) == 1
    assert len(apply_filters(items, Filters(shipping="no"))) == 1
    assert len(apply_filters(items, Filters(shipping="any"))) == 3


def test_title_regex_case_insensitive():
    items = [L(title="Nikon 50mm"), L(title="Canon lens"), L(title="nikon body rotto")]
    inc = apply_filters(items, Filters(title_include="nikon"))
    assert {x.title for x in inc} == {"Nikon 50mm", "nikon body rotto"}
    exc = apply_filters(items, Filters(title_include="nikon", title_exclude="rotto"))
    assert {x.title for x in exc} == {"Nikon 50mm"}


def test_bad_regex_rejected_at_construction():
    import pytest
    with pytest.raises(Exception):
        Filters(title_include="[unclosed")
