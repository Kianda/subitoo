"""Universal filters applied by the core to every site's listings (Decision 5).

This is the single place filtering happens. Adapters may pre-filter server-side for
efficiency, but the core always re-applies the full set here as the guarantee.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from subitoo.core.models import Filters, Listing


def _passes(listing: Listing, f: Filters,
            include_re: re.Pattern | None, exclude_re: re.Pattern | None) -> bool:
    # price range — listings with an unknown price (None) fail a range check.
    if f.price_min is not None or f.price_max is not None:
        if listing.price is None:
            return False
        if f.price_min is not None and listing.price < f.price_min:
            return False
        if f.price_max is not None and listing.price > f.price_max:
            return False

    # shipping — 'any' skips; otherwise require a known matching value.
    if f.shipping == "yes" and listing.shipping_available is not True:
        return False
    if f.shipping == "no" and listing.shipping_available is not False:
        return False

    # title regex (case-insensitive)
    if include_re is not None and not include_re.search(listing.title):
        return False
    if exclude_re is not None and exclude_re.search(listing.title):
        return False

    return True


def apply_filters(listings: Iterable[Listing], f: Filters) -> list[Listing]:
    include_re = re.compile(f.title_include, re.IGNORECASE) if f.title_include else None
    exclude_re = re.compile(f.title_exclude, re.IGNORECASE) if f.title_exclude else None
    return [x for x in listings if _passes(x, f, include_re, exclude_re)]
