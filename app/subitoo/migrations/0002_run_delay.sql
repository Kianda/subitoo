-- Per-query pre-run delay (human pacing / anti-burst). Applied before each *seeded*
-- run; the first-run seed bypasses it (nobody's watching a burst there, and the
-- interactive `add` seed happens with the user waiting). Default 5s.

ALTER TABLE queries ADD COLUMN run_delay_seconds INTEGER NOT NULL DEFAULT 5;
