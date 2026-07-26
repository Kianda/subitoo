"""The run pipeline: dispatcher (run_due) -> per-query run -> per-listing processing.

See SPEC.md section 8. Key properties:
- first run silently seeds (no notifications), then alerts on new + price changes;
- notify-then-persist gives at-least-once delivery that self-heals via retry;
- one failing query latches to 'error' and never blocks the others;
- a watchdog reclaims queries stuck in 'running' after a crash.
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable
from zoneinfo import ZoneInfo

from croniter import croniter

from subitoo.config import get_settings
from subitoo.core import db, notify
from subitoo.core.fetch import build_context
from subitoo.core.filters import apply_filters
from subitoo.core.models import Notification, Query, QueryStatus
from subitoo.registry import get_site

log = logging.getLogger("subitoo.pipeline")

# Injection seam so tests can substitute a fake sender.
SendFn = Callable[[Notification], None]
SleepFn = Callable[[float], None]


def cron_due(cron_expr: str, last_run_at: int | None, now_ts: int, tz: str) -> bool:
    """Is this cron due at now_ts, given the last run time?

    A query with no prior run is due immediately (its first run). Otherwise it is due
    if the next fire time after ``last_run_at`` is at or before ``now_ts``.
    """
    tzinfo = ZoneInfo(tz)
    if last_run_at is None:
        return True
    base = datetime.fromtimestamp(last_run_at, tzinfo)
    nxt = croniter(cron_expr, base).get_next(datetime)
    return nxt.timestamp() <= now_ts


@dataclass
class RunResult:
    n_found: int = 0
    n_new: int = 0
    n_price_changed: int = 0
    n_notified: int = 0
    n_suppressed: int = 0  # notifications skipped as same-heartbeat cross-query dupes


def run_due(conn, *, now_ts: int | None = None, send: SendFn = notify.send,
            sleep: SleepFn = time.sleep) -> list[int]:
    """Ofelia entrypoint (one heartbeat). Returns the ids of queries actually run."""
    settings = get_settings()
    now_ts = now_ts or db.now()

    stale = db.reclaim_stale(conn, settings.run_timeout, now_ts)
    for qid in stale:
        log.warning("reclaimed stale query %s -> error", qid)

    # Cross-query dedup scratchpad, scoped to THIS heartbeat: the set of
    # (site, site_listing_id) ads already notified during this run_due pass. When two
    # overlapping queries match the same ad on the same heartbeat, the first to notify
    # wins and the rest suppress their *send* (they still persist their own state).
    # It's born here and dies when the heartbeat ends — nothing spans heartbeats.
    heartbeat_seen: set[tuple[str, str]] = set()

    ran: list[int] = []
    for q in db.enabled_pending_queries(conn):
        if not get_site(q.site).enabled:
            continue  # site parked in code (kill switch) — leave the query untouched
        if not cron_due(q.cron, q.last_run_at, now_ts, settings.tz):
            continue
        if not db.claim_query(conn, q.id, now_ts):
            continue  # someone else grabbed it
        run_query(conn, q.id, now_ts=now_ts, send=send, sleep=sleep,
                  heartbeat_seen=heartbeat_seen)
        ran.append(q.id)
    return ran


def run_query(conn, query_id: int, *, now_ts: int | None = None,
              send: SendFn = notify.send, sleep: SleepFn = time.sleep,
              heartbeat_seen: set[tuple[str, str]] | None = None) -> RunResult:
    """Execute one query end to end. Assumes the query is already claimed (running).

    ``heartbeat_seen`` is the cross-query dedup scratchpad shared across all queries in
    one ``run_due`` heartbeat (see there). A standalone run (CLI ``run``/``retry``) is a
    single query, so it just gets a fresh empty set and dedups against nobody.
    """
    now_ts = now_ts or db.now()
    if heartbeat_seen is None:
        heartbeat_seen = set()
    q = db.get_query(conn, query_id)
    if q is None:
        raise KeyError(f"query {query_id} not found")

    run_id = db.start_run(conn, query_id, now_ts)
    res = RunResult()
    try:
        adapter = get_site(q.site)
        ctx = build_context(get_settings(), needs_browser=adapter.needs_browser)
        # Human pacing: pause before touching the site so a heartbeat with several due
        # queries doesn't fire them in a burst. The base is `run_delay_seconds`; we
        # add a fresh 0–1000ms of jitter each run so the cadence isn't a metronome.
        # Skipped on the seeding run — the interactive `add` seeds with the user
        # waiting, and a first-run baseline shouldn't stall (migration 0002).
        if q.seeded and q.run_delay_seconds > 0:
            sleep(q.run_delay_seconds + random.randint(0, 1000) / 1000)
        listings = list(adapter.fetch(q.search, ctx))
        matched = apply_filters(listings, q.filters)
        res.n_found = len(matched)

        if not q.seeded:
            # FIRST RUN: record everything as seen, notify nothing.
            for lst in matched:
                db.insert_listing(conn, query_id, lst, now_ts)
            db.update_query(conn, query_id, seeded=True)
        else:
            for lst in matched:
                _process_listing(conn, q, lst, res, now_ts, send, heartbeat_seen)

        db.finish_run(conn, run_id, status="ok", n_found=res.n_found, n_new=res.n_new,
                      n_price_changed=res.n_price_changed, n_notified=res.n_notified,
                      ts=now_ts)
        db.update_query(conn, query_id, last_run_at=now_ts)
        db.set_status(conn, query_id, QueryStatus.PENDING, now_ts)
    except Exception as e:  # noqa: BLE001 - latch to error, never crash the dispatcher
        log.exception("query %s failed", query_id)
        db.finish_run(conn, run_id, status="error", error_message=str(e), ts=now_ts)
        db.set_status(conn, query_id, QueryStatus.ERROR, now_ts)
    return res


def _process_listing(conn, q: Query, lst, res: RunResult, now_ts: int,
                     send: SendFn, heartbeat_seen: set[tuple[str, str]]) -> None:
    """Classify one already-filtered listing and, if warranted, notify-then-persist."""
    settings = get_settings()
    existing = db.get_listing(conn, q.id, lst.site_listing_id)

    if existing is None:
        _notify_and_persist(conn, q, lst, kind="new", old_price=None, res=res,
                            now_ts=now_ts, send=send, settings=settings,
                            heartbeat_seen=heartbeat_seen)
        return

    if lst.price == existing["last_price"]:
        db.touch_listing(conn, existing["id"], now_ts)  # unchanged -> just seen
        return

    # price changed and (since it passed filters) still matches -> notify.
    _notify_and_persist(conn, q, lst, kind="price_change",
                        old_price=existing["last_price"], res=res, now_ts=now_ts,
                        send=send, settings=settings, existing_id=existing["id"],
                        heartbeat_seen=heartbeat_seen)


def _notify_and_persist(conn, q: Query, lst, *, kind: str, old_price, res: RunResult,
                        now_ts: int, send: SendFn, settings,
                        heartbeat_seen: set[tuple[str, str]],
                        existing_id: int | None = None) -> None:
    def persist() -> None:
        """Record this query's own view of the listing. Runs whether we sent or
        suppressed — the dedup gates the *send*, never the per-query state, so the
        query's diary stays honest and it won't re-fire the ad on the next heartbeat."""
        if kind == "new":
            db.insert_listing(conn, q.id, lst, now_ts)
            res.n_new += 1
        else:
            db.update_listing_price(conn, existing_id, lst.price, now_ts)
            res.n_price_changed += 1

    def log_notif(status: str, error: str | None = None) -> None:
        db.log_notification(conn, query_id=q.id, site_listing_id=lst.site_listing_id,
                            channel=settings.default_channel, kind=kind,
                            status=status, error=error, ts=now_ts)

    # Cross-query dedup: another query on this heartbeat already notified this exact ad.
    # Skip the send (and its image download) but still persist our own state.
    key = (q.site, lst.site_listing_id)
    if key in heartbeat_seen:
        persist()
        log_notif("suppressed")
        res.n_suppressed += 1
        return

    n = Notification(
        kind=kind, title=lst.title, url=lst.url, query_name=q.name, site=q.site,
        price=lst.price, currency=lst.currency, image_url=lst.image_url,
        location=lst.location, shipping_available=lst.shipping_available,
        old_price=old_price,
    )
    try:
        send(n)
    except Exception as e:  # noqa: BLE001 - do NOT persist -> retried next heartbeat
        log.warning("notify failed for %s (%s): %s", lst.site_listing_id, kind, e)
        log_notif("failed", str(e))
        return

    # send succeeded -> persist the state change, log it, and claim this ad for the
    # rest of the heartbeat so overlapping queries suppress. Claiming ONLY on success
    # preserves at-least-once: a failed send leaves the ad unclaimed for the next query.
    persist()
    log_notif("sent")
    res.n_notified += 1
    heartbeat_seen.add(key)


def dry_run(conn, query_id: int):
    """Fetch + filter only. No notify, no persist, no seeding. For `query test`."""
    q = db.get_query(conn, query_id)
    if q is None:
        raise KeyError(f"query {query_id} not found")
    adapter = get_site(q.site)
    ctx = build_context(get_settings(), needs_browser=adapter.needs_browser)
    listings = list(adapter.fetch(q.search, ctx))
    return apply_filters(listings, q.filters)
