from subitoo.core.pipeline import cron_due

TZ = "Europe/Rome"


def test_never_run_is_due():
    assert cron_due("*/10 * * * *", None, 1_000_000, TZ) is True


def test_recent_run_not_due():
    # last run "now"; next 10-min tick is in the future -> not due yet.
    now = 1_000_000
    assert cron_due("*/10 * * * *", now, now + 60, TZ) is False


def test_old_run_is_due():
    now = 1_000_000
    assert cron_due("*/10 * * * *", now, now + 3600, TZ) is True
