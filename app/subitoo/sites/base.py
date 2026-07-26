"""BaseSite — the blueprint every site adapter fills in.

An adapter's ONLY job: take a search + the toolbox, fetch, and return normalized
``Listing`` objects. It never filters, dedups, or notifies — the core does all of
that uniformly, so every new adapter gets those features for free.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable
from typing import Any

from subitoo.core.fetch import FetchContext
from subitoo.core.models import Listing, SearchField


class BaseSite(ABC):
    registry_kind = "site"

    #: set by @register("...")
    key: str = ""

    #: whether the core must guarantee (and health-check) the browser service.
    needs_browser: bool = False

    #: in-code kill switch for the WHOLE site. Flip to False and commit to park it
    #: (e.g. the site changed its markup/access and the adapter needs a fix). The scheduler then skips every
    #: query on this site — they stay enabled+pending in the DB and resume the moment
    #: it's flipped back. Manual runs (`query run/retry`) still work, so a fix can be
    #: tested before re-enabling. The switch lives in the adapter file itself, so
    #: parking a site is a one-line diff.
    enabled: bool = True

    #: fields the CLI wizard prompts for to build this site's search blob.
    search_schema: list[SearchField] = []

    def validate_search(self, search: dict[str, Any]) -> dict[str, Any]:
        """Optional: validate/normalize the user's search blob at CRUD time.

        Default enforces required fields from ``search_schema``.
        """
        for f in self.search_schema:
            if f.required and not search.get(f.name):
                raise ValueError(f"search field {f.name!r} is required for {self.key}")
        return search

    def resolve(self, search: dict[str, Any], ctx: FetchContext) -> dict[str, Any]:
        """Optional: enrich the search blob ONCE at add-time (e.g. resolve a pasted
        page URL into a stable API URL via the browser). Runs after validation when a
        query is created. Default is a no-op; adapters that need it override this and
        return the (mutated) blob. ``fetch()`` runs every scheduled run, ``resolve()``
        only when the query is created or explicitly re-resolved.
        """
        return search

    @abstractmethod
    def fetch(self, search: dict[str, Any], ctx: FetchContext) -> Iterable[Listing]:
        """Given the search blob and toolbox, yield normalized listings."""
        raise NotImplementedError
