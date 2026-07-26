-- Initial schema (Decision 17). Schema is core-owned; plugins never migrate.

CREATE TABLE queries (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    name              TEXT NOT NULL UNIQUE,
    site              TEXT NOT NULL,
    search_json       TEXT NOT NULL,
    filters_json      TEXT NOT NULL,
    cron              TEXT NOT NULL,
    enabled           INTEGER NOT NULL DEFAULT 1,
    seeded            INTEGER NOT NULL DEFAULT 0,
    status            TEXT NOT NULL DEFAULT 'pending',      -- pending | running | error
    status_changed_at INTEGER NOT NULL,
    last_run_at       INTEGER,
    next_run_at       INTEGER,
    created_at        INTEGER NOT NULL,
    updated_at        INTEGER NOT NULL
);

CREATE TABLE listings (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    query_id           INTEGER NOT NULL REFERENCES queries(id) ON DELETE CASCADE,
    site_listing_id    TEXT NOT NULL,
    title              TEXT NOT NULL,
    last_price         REAL,
    currency           TEXT NOT NULL DEFAULT 'EUR',
    url                TEXT,
    shipping_available INTEGER,                              -- NULL = unknown
    location           TEXT,
    image_url          TEXT,
    posted_at          INTEGER,
    raw_json           TEXT,
    first_seen_at      INTEGER NOT NULL,
    last_seen_at       INTEGER NOT NULL,
    UNIQUE (query_id, site_listing_id)
);

CREATE INDEX idx_listings_query ON listings(query_id);

CREATE TABLE runs (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    query_id         INTEGER NOT NULL REFERENCES queries(id) ON DELETE CASCADE,
    started_at       INTEGER NOT NULL,
    finished_at      INTEGER,
    status           TEXT NOT NULL,                          -- running | ok | error
    n_found          INTEGER NOT NULL DEFAULT 0,
    n_new            INTEGER NOT NULL DEFAULT 0,
    n_price_changed  INTEGER NOT NULL DEFAULT 0,
    n_notified       INTEGER NOT NULL DEFAULT 0,
    error_message    TEXT
);

CREATE INDEX idx_runs_query ON runs(query_id, started_at DESC);

CREATE TABLE notifications (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    query_id         INTEGER NOT NULL REFERENCES queries(id) ON DELETE CASCADE,
    site_listing_id  TEXT NOT NULL,
    channel          TEXT NOT NULL,
    kind             TEXT NOT NULL,                          -- new | price_change
    status           TEXT NOT NULL,                          -- sent | failed | suppressed
    error            TEXT,
    sent_at          INTEGER NOT NULL
);

CREATE INDEX idx_notifications_query ON notifications(query_id, site_listing_id);
