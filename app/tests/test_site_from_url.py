"""The site is inferred from the pasted search URL, not asked for.

Adapters opt in with `url_host_pattern`; the base class then both claims the URL for
`query add` and rejects a URL from the wrong site, so the domain is declared once per
adapter instead of hand-rolled in each `validate_search`.
"""

import pytest

from subitoo import registry
from subitoo.core.models import SearchField
from subitoo.sites.base import BaseSite

SUBITO_URL = "https://www.subito.it/annunci-italia/vendita/usato/?q=nas"
VINTED_URL = "https://www.vinted.it/catalog?search_text=nas"


@pytest.mark.parametrize("url, expected", [
    (SUBITO_URL, "subito"),
    ("https://subito.it/annunci-italia/vendita/usato/?q=nas", "subito"),
    (VINTED_URL, "vinted"),
    ("https://www.vinted.fr/catalog?search_text=nas", "vinted"),   # any country domain
    ("https://www.vinted.co.uk/catalog?search_text=nas", "vinted"),
])
def test_site_for_url_infers_the_adapter(url, expected):
    assert registry.site_for_url(url) == expected


@pytest.mark.parametrize("url", [
    "https://www.wallapop.com/search?keywords=nas",     # no adapter installed
    "https://www.subito.it.evil.example/?q=nas",        # lookalike host
    "https://www.subito.it/?ref=vinted.it",             # site named in a query param
    "not a url at all",
    "",
])
def test_site_for_url_refuses_what_it_doesnt_own(url):
    assert registry.site_for_url(url) != "vinted"


def test_lookalike_and_param_hosts_are_not_claimed():
    # the two that matter: matching the whole URL instead of the host would get these
    # wrong, silently sending the search to the wrong adapter.
    assert registry.site_for_url("https://www.subito.it/?ref=vinted.it") == "subito"
    assert registry.site_for_url("https://www.vinted.it.evil.example/c") is None


def test_a_non_url_adapter_claims_nothing_and_still_works():
    """An adapter that isn't URL-based leaves url_host_pattern empty: it never claims a
    URL, and the wizard falls back to asking. The core makes no URL assumption."""

    class KeywordSite(BaseSite):
        key = "kw"
        search_schema = [SearchField("keyword", "Keyword")]

        def fetch(self, search, ctx):
            return []

    assert KeywordSite.claims_url(VINTED_URL) is False
    assert KeywordSite().validate_search({"keyword": "nas"}) == {"keyword": "nas"}


@pytest.mark.parametrize("site_key, good, bad", [
    ("subito", SUBITO_URL, VINTED_URL),
    ("vinted", VINTED_URL, SUBITO_URL),
])
def test_validate_search_rejects_another_sites_url(site_key, good, bad):
    site = registry.get_site(site_key)
    assert site.validate_search({"url": good})["url"] == good
    with pytest.raises(ValueError, match="must be a .* search URL"):
        site.validate_search({"url": bad})


def test_validate_search_trims_the_url():
    assert registry.get_site("vinted").validate_search(
        {"url": f"  {VINTED_URL}  "})["url"] == VINTED_URL
