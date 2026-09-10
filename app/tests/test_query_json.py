"""`query list --json` / `query show` serialization.

The promise is that --json is the *whole* row (the table is the summary), so the key
assertion here is the invariant one: every field of `Query` must appear. Add a column to
the dataclass and forget the serializer, and this fails instead of quietly shipping a
--json that's missing data.
"""

import json
from dataclasses import fields
from datetime import datetime

from subitoo.cli import _query_json
from subitoo.core.models import Filters, Query, QueryStatus


def _query(**kw):
    base = dict(
        id=7, name="synology nas", site="vinted",
        search={"url": "https://www.vinted.it/catalog?search_text=nas", "max_pages": "1"},
        filters=Filters(price_min=75, price_max=150,
                        title_include=r"DS ?124|DS ?220", title_exclude="DS218j"),
        cron="0 */4 * * *", enabled=True, seeded=True, run_delay_seconds=5,
        status=QueryStatus.PENDING, status_changed_at=1788268783,
        last_run_at=1788268783, next_run_at=1788283183,
        created_at=1788200000, updated_at=1788268783,
    )
    return Query(**{**base, **kw})


def test_every_query_field_is_serialized():
    """The guard: --json must not drift behind the dataclass."""
    assert set(_query_json(_query())) == {f.name for f in fields(Query)}


def test_regexes_and_search_are_included():
    out = _query_json(_query())
    assert out["filters"]["title_include"] == r"DS ?124|DS ?220"
    assert out["filters"]["title_exclude"] == "DS218j"
    assert out["filters"]["price_min"] == 75
    assert out["filters"]["shipping"] == "any"
    assert out["search"]["url"].startswith("https://www.vinted.it/catalog")
    assert out["search"]["max_pages"] == "1"


def test_status_is_the_plain_value():
    assert _query_json(_query())["status"] == "pending"


def test_timestamps_are_iso_and_round_trip_to_the_epoch():
    out = _query_json(_query())
    for key in ("status_changed_at", "last_run_at", "next_run_at", "created_at",
                "updated_at"):
        assert int(datetime.fromisoformat(out[key]).timestamp()) == \
            getattr(_query(), key), key


def test_missing_timestamps_are_null_not_a_dash():
    """`-` is for the table; JSON says null so a consumer can test for it."""
    out = _query_json(_query(last_run_at=None, next_run_at=None))
    assert out["last_run_at"] is None
    assert out["next_run_at"] is None


def test_output_is_json_serializable():
    # what the CLI actually does — an enum or a stray object here would raise
    assert json.loads(json.dumps(_query_json(_query())))["id"] == 7
