"""Shared test doubles. Kept out of conftest.py so both conftest and the test
modules import the SAME module object (avoids duplicate class identities)."""

from __future__ import annotations

from subitoo.core.models import Listing, SearchField
from subitoo.sites.base import BaseSite


class FakeSite(BaseSite):
    """A site whose results the tests control directly. No network, no browser."""

    needs_browser = False
    search_schema = [SearchField("q", "q", required=False)]
    key = "fake"
    to_return: list[Listing] = []

    def fetch(self, search, ctx):
        return list(type(self).to_return)


class BoomSite(BaseSite):
    """A site whose fetch always raises — for the error-latch test."""

    needs_browser = False
    key = "boom"

    def fetch(self, search, ctx):
        raise RuntimeError("adapter blew up")


class Recorder:
    """A fake notifier `send` that records calls and can be told to fail."""

    def __init__(self):
        self.sent = []
        self.fail = False

    def __call__(self, n):
        if self.fail:
            raise RuntimeError("send failed")
        self.sent.append(n)


def make_listing(id_: str, price: float | None = 100.0, title="Item", shipping=None):
    return Listing(site_listing_id=id_, title=title, price=price,
                   shipping_available=shipping)
