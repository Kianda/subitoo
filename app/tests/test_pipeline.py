"""The pipeline behavior matrix (SPEC.md section 8)."""

from subitoo.core import db, pipeline
from subitoo.core.models import Filters, QueryStatus
from tests._helpers import FakeSite, make_listing


def mkquery(conn, *, filters=None, cron="* * * * *", site="fake", name="q",
            run_delay_seconds=0):
    # Default 0 so seeded runs don't actually sleep in tests; the delay behavior
    # itself is covered by test_run_delay_applied_only_after_seed.
    return db.create_query(conn, name=name, site=site, search={},
                           filters=filters or Filters(), cron=cron,
                           run_delay_seconds=run_delay_seconds)


def seed(conn, qid, listings, recorder, ts=1000):
    FakeSite.to_return = listings
    pipeline.run_query(conn, qid, now_ts=ts, send=recorder)


def test_first_run_seeds_silently(conn, recorder):
    qid = mkquery(conn)
    FakeSite.to_return = [make_listing("A"), make_listing("B")]
    res = pipeline.run_query(conn, qid, now_ts=1000, send=recorder)
    assert recorder.sent == []            # no flood on first run
    assert res.n_new == 0
    assert db.get_query(conn, qid).seeded is True
    assert len(db.list_listings(conn, qid)) == 2


def test_new_listing_notifies(conn, recorder):
    qid = mkquery(conn)
    seed(conn, qid, [make_listing("A"), make_listing("B")], recorder)
    FakeSite.to_return = [make_listing("A"), make_listing("B"), make_listing("C", title="New one")]
    res = pipeline.run_query(conn, qid, now_ts=2000, send=recorder)
    assert len(recorder.sent) == 1
    assert recorder.sent[0].kind == "new"
    assert recorder.sent[0].title == "New one"
    assert res.n_new == 1
    assert db.get_listing(conn, qid, "C") is not None


def test_run_delay_applied_only_after_seed(conn, recorder):
    slept: list[float] = []
    qid = mkquery(conn, run_delay_seconds=5)
    FakeSite.to_return = [make_listing("A")]
    # First run = the seed: must NOT pause (bypass the delay).
    pipeline.run_query(conn, qid, now_ts=1000, send=recorder, sleep=slept.append)
    assert slept == []
    # Second run = seeded: must pause for the base delay plus 0–1000ms of jitter.
    pipeline.run_query(conn, qid, now_ts=2000, send=recorder, sleep=slept.append)
    assert len(slept) == 1 and 5.0 <= slept[0] <= 6.0


def test_price_change_notifies_with_old_price(conn, recorder):
    qid = mkquery(conn)
    seed(conn, qid, [make_listing("A", price=100.0)], recorder)
    FakeSite.to_return = [make_listing("A", price=120.0)]
    res = pipeline.run_query(conn, qid, now_ts=2000, send=recorder)
    assert len(recorder.sent) == 1
    n = recorder.sent[0]
    assert n.kind == "price_change" and n.old_price == 100.0 and n.price == 120.0
    assert res.n_price_changed == 1
    assert db.get_listing(conn, qid, "A")["last_price"] == 120.0


def test_unchanged_price_no_notify(conn, recorder):
    qid = mkquery(conn)
    seed(conn, qid, [make_listing("A", price=100.0)], recorder)
    FakeSite.to_return = [make_listing("A", price=100.0)]
    pipeline.run_query(conn, qid, now_ts=2000, send=recorder)
    assert recorder.sent == []


def test_price_change_out_of_range_no_notify(conn, recorder):
    qid = mkquery(conn, filters=Filters(price_max=200))
    seed(conn, qid, [make_listing("A", price=100.0)], recorder)
    FakeSite.to_return = [make_listing("A", price=250.0)]  # now above max -> filtered out
    pipeline.run_query(conn, qid, now_ts=2000, send=recorder)
    assert recorder.sent == []
    assert db.get_listing(conn, qid, "A")["last_price"] == 100.0  # untouched


def test_failed_send_not_persisted_then_retried(conn, recorder):
    qid = mkquery(conn)
    seed(conn, qid, [], recorder)  # seed empty -> seeded=True
    FakeSite.to_return = [make_listing("C")]

    recorder.fail = True
    pipeline.run_query(conn, qid, now_ts=2000, send=recorder)
    assert db.get_listing(conn, qid, "C") is None  # not persisted
    failed = conn.execute(
        "SELECT COUNT(*) FROM notifications WHERE status='failed'").fetchone()[0]
    assert failed == 1

    recorder.fail = False
    res = pipeline.run_query(conn, qid, now_ts=3000, send=recorder)  # retry
    assert res.n_new == 1
    assert db.get_listing(conn, qid, "C") is not None


def test_adapter_error_latches(conn, recorder):
    qid = mkquery(conn, site="boom")
    pipeline.run_query(conn, qid, now_ts=1000, send=recorder)
    assert db.get_query(conn, qid).status == QueryStatus.ERROR
    last = db.recent_runs(conn, qid)[0]
    assert last["status"] == "error" and "blew up" in last["error_message"]


def test_watchdog_reclaims_stale(conn, recorder):
    qid = mkquery(conn)
    db.set_status(conn, qid, QueryStatus.RUNNING, ts=1000)  # stuck since t=1000
    pipeline.run_due(conn, now_ts=1000 + 100_000, send=recorder)  # way past RUN_TIMEOUT
    assert db.get_query(conn, qid).status == QueryStatus.ERROR


def test_dispatcher_runs_due_pending_only(conn, recorder):
    FakeSite.to_return = [make_listing("A")]
    q1 = mkquery(conn, name="q1")
    q2 = mkquery(conn, name="q2")
    q3 = mkquery(conn, name="q3")
    db.set_status(conn, q3, QueryStatus.RUNNING)  # already running -> must be skipped
    ran = pipeline.run_due(conn, now_ts=5000, send=recorder)
    assert set(ran) == {q1, q2}
    assert db.get_query(conn, q1).seeded and db.get_query(conn, q2).seeded


def test_dispatcher_skips_parked_site(conn, recorder):
    """A site with the in-code kill switch off is skipped by the scheduler, but its
    query stays enabled+pending (untouched) so it resumes when the site is unparked."""
    FakeSite.to_return = [make_listing("A")]
    qid = mkquery(conn, name="parked")
    try:
        FakeSite.enabled = False
        ran = pipeline.run_due(conn, now_ts=5000, send=recorder)
        assert ran == []
        q = db.get_query(conn, qid)
        assert q.enabled and q.status is QueryStatus.PENDING and not q.seeded
    finally:
        FakeSite.enabled = True

    # Unparked: the same query now runs on the next heartbeat.
    ran = pipeline.run_due(conn, now_ts=6000, send=recorder)
    assert ran == [qid]
    assert db.get_query(conn, qid).seeded


def test_manual_run_ignores_parked_site(conn, recorder):
    """The kill switch gates only the scheduler — a manual run_query still works, so a
    fix can be tested before flipping the site back on."""
    FakeSite.to_return = [make_listing("A")]
    qid = mkquery(conn, name="parked")
    try:
        FakeSite.enabled = False
        pipeline.run_query(conn, qid, now_ts=5000, send=recorder)
    finally:
        FakeSite.enabled = True
    assert db.get_query(conn, qid).seeded


# --------------------------------------------------------- same-heartbeat dedup

def _two_seeded_queries(conn, recorder):
    """Two overlapping queries (same fake site, same baseline ad 'A'), both seeded."""
    q1 = mkquery(conn, name="veneto")
    q2 = mkquery(conn, name="italia")
    seed(conn, q1, [make_listing("A")], recorder)
    seed(conn, q2, [make_listing("A")], recorder)
    assert recorder.sent == []  # seeding is silent
    return q1, q2


def test_heartbeat_dedup_same_ad_sent_once(conn, recorder):
    """A new ad matched by two queries on the SAME heartbeat pings once, but both
    queries still record it in their own diary so neither re-fires it later."""
    q1, q2 = _two_seeded_queries(conn, recorder)
    FakeSite.to_return = [make_listing("A"), make_listing("C", title="New iPhone")]
    ran = pipeline.run_due(conn, now_ts=1000 + 3600, send=recorder)

    assert set(ran) == {q1, q2}
    assert len(recorder.sent) == 1              # one ping, not two
    assert recorder.sent[0].title == "New iPhone"
    # both queries persisted 'C' independently -> no cross-heartbeat re-fire
    assert db.get_listing(conn, q1, "C") is not None
    assert db.get_listing(conn, q2, "C") is not None
    # the suppressed query logged it as 'suppressed'
    rows = conn.execute(
        "SELECT status FROM notifications WHERE site_listing_id='C' ORDER BY status"
    ).fetchall()
    assert [r["status"] for r in rows] == ["sent", "suppressed"]


def test_heartbeat_dedup_failed_send_lets_next_query_try(conn, recorder):
    """If the first query's send FAILS the ad stays unclaimed, so the next query still
    gets to send it (at-least-once preserved). Only the query that actually sent
    persists the ad; the failed one retries on a later heartbeat."""
    q1, q2 = _two_seeded_queries(conn, recorder)
    FakeSite.to_return = [make_listing("A"), make_listing("C", title="New")]

    calls: list = []
    def flaky(n):
        calls.append(n)
        if len(calls) == 1:
            raise RuntimeError("boom")  # first query's send fails

    pipeline.run_due(conn, now_ts=1000 + 3600, send=flaky)

    assert len(calls) == 2  # second query was NOT suppressed by the failed first send
    recorded = [q for q in (q1, q2) if db.get_listing(conn, q, "C") is not None]
    assert len(recorded) == 1  # only the successful send persisted


def test_dupes_across_heartbeats_still_fire(conn, recorder):
    """Documents the accepted limitation: dedup is per-heartbeat only. Two separate
    heartbeats (separate scratchpads) still double-fire the same ad — by design."""
    q1, q2 = _two_seeded_queries(conn, recorder)
    FakeSite.to_return = [make_listing("A"), make_listing("C", title="New")]
    # Separate run_query calls = separate heartbeats = separate scratchpads.
    pipeline.run_query(conn, q1, now_ts=2000, send=recorder)
    pipeline.run_query(conn, q2, now_ts=2000, send=recorder)
    assert len(recorder.sent) == 2
