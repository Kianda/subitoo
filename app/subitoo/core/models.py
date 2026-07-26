"""The data shapes the whole system agrees on.

- ``Listing``  — the normalized item every site adapter must return.
- ``Filters``  — the universal filters the core applies to every site.
- ``Notification`` — the bundle the core builds and a notifier renders.
- ``SearchField`` — how an adapter declares its search-input fields for the CLI wizard.
- ``Query`` / ``QueryStatus`` — a saved query row.
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, field_validator


@dataclass(slots=True)
class Listing:
    """One marketplace item, normalized. Adapters produce these; core consumes them."""

    site_listing_id: str  # the site's native id -> dedup key
    title: str
    price: float | None  # None = price-on-request / unparseable
    currency: str = "EUR"
    url: str = ""
    shipping_available: bool | None = None
    location: str | None = None
    image_url: str | None = None
    posted_at: int | None = None  # epoch seconds, if the site exposes it
    raw: dict[str, Any] = field(default_factory=dict)


class Filters(BaseModel):
    """Universal filters, guaranteed by the core on every site (Decision 5)."""

    price_min: float | None = None
    price_max: float | None = None
    shipping: Literal["any", "yes", "no"] = "any"
    title_include: str | None = None  # regex
    title_exclude: str | None = None  # regex

    @field_validator("title_include", "title_exclude")
    @classmethod
    def _valid_regex(cls, v: str | None) -> str | None:
        if v:
            re.compile(v)  # raises on bad pattern -> surfaced at CRUD time
        return v


@dataclass(slots=True)
class Notification:
    """What the core hands a notifier. The notifier decides how to render it."""

    kind: Literal["new", "price_change"]
    title: str
    url: str
    query_name: str
    site: str = ""  # source site key, e.g. "subito" — shown in the message footer
    price: float | None = None
    currency: str = "EUR"
    image_url: str | None = None
    location: str | None = None
    shipping_available: bool | None = None  # None = unknown
    old_price: float | None = None  # only set for price_change


@dataclass(slots=True)
class SearchField:
    """An adapter declares these so the CLI wizard knows what to prompt for."""

    name: str
    prompt: str
    required: bool = True
    default: str | None = None


class QueryStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    ERROR = "error"


@dataclass(slots=True)
class Query:
    """A saved query row (mirrors the ``queries`` table)."""

    id: int
    name: str
    site: str
    search: dict[str, Any]
    filters: Filters
    cron: str
    enabled: bool
    seeded: bool
    run_delay_seconds: int  # pause before each seeded run (human pacing); seed bypasses it
    status: QueryStatus
    status_changed_at: int
    last_run_at: int | None = None
    next_run_at: int | None = None
    created_at: int | None = None
    updated_at: int | None = None
