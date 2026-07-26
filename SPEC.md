# subitoo-v2 — Design Specification

> A self-hosted, modular multi-site marketplace scraper: saved search queries run on
> per-query cron schedules, results are stored in SQLite, and a notification fires when a
> listing newly matches — or its price changes while still matching — a query's filters.
> Ships with **Subito.it**; **Vinted / Wallapop** etc. added by contributors.

**This file is the design rationale — the *why*.** For the *how*, the source of truth is
the code, `app/subitoo/migrations/*.sql`, `.env.example`, `subitoo --help`, and
`README.md`; this doc deliberately does not restate them (so it can't drift out of sync).

---

## 1. Understanding Summary

- **What:** a CLI-only, Docker-hosted scraper. Saved queries run on per-query cron
  schedules; matching listings are stored and can trigger push notifications.
- **Why:** automate watching marketplace sites for matching deals and get pushed alerts,
  instead of manually re-searching.
- **Who:** the owner, self-hosting via Docker. Other developers contribute **site
  adapters** and **notifier channels** via PRs.
- **How it runs:** ofelia fires one heartbeat job (`subitoo run-due`) on a short interval →
  the Typer CLI (over a shared `core` lib) reads the `queries` table, runs whichever are
  due per their own cron, fetches (HTTP-first, browser on demand), normalizes, filters,
  dedups, and notifies.
- **Modularity:** `BaseSite` / `BaseNotifier` blueprints; subclasses in `sites/` and
  `notifiers/`, auto-registered via `@register`. Universal filters (price, shipping, title
  regex) are guaranteed by the core; adapters may push filters server-side as an optimization.
- **Non-goals (current scope):** no web UI / FastAPI, no per-query notification routing,
  no multi-user / auth, no cloud/remote, no rate-limiting *system* (optional hook only), no
  price history.

---

## 2. Assumptions (non-functional)

1. **Scale:** single user; low-dozens of queries; tens–low-hundreds of listings per run;
   heartbeat ~every 1 min. SQLite (WAL) is sufficient.
2. **Concurrency:** `run-due` processes due queries **sequentially**; a query is skipped
   if its previous run is still in progress, enforced via an atomic status claim.
3. **Security/secrets:** CLI is local, **no auth**. Secrets come from env / `.env`, never
   committed. Only public listing data is stored — no PII.
4. **Reliability:** one query failing never aborts the others; errors latch the query to
   `error` until manually retried; dedup makes runs idempotent; a **watchdog** reclaims
   crashed (stuck `running`) queries.
5. **Politeness / pacing:** *nice-to-have.* Optional per-adapter delay + jitter; keep
   human-like pacing — space requests out rather than hammering a site.
6. **Data retention:** seen listings kept indefinitely (prune command may come later). **No
   price-history table** — only last price is retained.
7. **Timezone:** `.env` `TZ` (default `UTC`), applied to the container and cron.
8. **Maintenance:** single maintainer + PR contributors; each adapter/notifier ships with
   a fixture + must pass the contract test.

---

## 3. Decision Log

| # | Decision | Alternatives considered | Why |
|---|---|---|---|
| 1 | **Ofelia heartbeat + DB dispatcher.** One static ofelia job runs `subitoo run-due`; the CLI reads the queries table and fires whichever cron is due. | (a) App-internal APScheduler service; (b) Ofelia job-per-query with generated config. | Schedules stay 100% DB-driven; CRUD never reconfigures ofelia; no always-on Python scheduler to maintain. |
| 2 | **Notify on new matches + price changes (either direction) that still pass filters.** | (a) New only; (b) new + price *drops* only; (c) every match every run. | Matches the deal-hunting goal without spam; only "last price" needed to detect change. |
| 3 | **HTTP-first fetching; browser on demand.** Core provides both an HTTP client and a lazy browser client; adapter uses whichever it needs (refined by Decision 12). | (a) Browser-always; (b) HTTP-only. | Fast/cheap for API sites, a real browser available for sites that only serve results to one; no wasted browser. |
| 4 | **In-repo subclass + auto-registry** for sites & notifiers (`@register`, loaded from dirs). | (a) Python entry-point plugins; (b) runtime drop-in folder. | Simplest for contributors, easiest to review, shipped in one image; fits a self-hosted tool. |
| 5 | **Universal core filters, optional server-side pushdown.** Core guarantees all filters on a normalized `Listing`; adapters *may* pre-apply supported ones for efficiency; core always re-applies the full set. | (a) Per-adapter filtering; (b) core-only. | Every filter works on every site, with efficiency where the site supports it. |
| 6 | **Adapter-defined search spec.** Each adapter declares/validates its own search input (Subito: a pasted search-page URL resolved once to the API URL). Query stores a site-specific `search` blob + universal `filters`. | (a) Structured params per site; (b) raw URL everywhere. | Paste-the-URL lets the site's own UI build the query (region, category, sort) — no per-site param modelling. |
| 7 | **Typer CLI + shared `core` lib, no web server.** Ofelia execs the same CLI. | (a) FastAPI service + Typer HTTP client; (b) logic inline. | Matches "CLI-only"; simplest topology; a web API can be added later without rewriting logic. |
| 8 | **First run silently seeds** all current matches as "seen" (0 notifications); alerts start from the 2nd run. | (a) Notify everything on first run; (b) per-query opt-in flag. | Avoids an initial flood; standard alerter behavior. |
| 9 | **Single global notification channel (for now)** = Pushover; notifier design modular; per-query routing deferred. | (a) Global default + per-query override; (b) always per-query. | Simplest for the common case; routing can be added later without redesign. |
| 10 | **Timezone via `.env`** (`TZ`, default `UTC`). | Hardcode; local zone default. | Configurable per deployment; UTC default is portable and unambiguous for everyone. |
| 11 | **Per-adapter rate-limiting = nice-to-have.** Optional delay/jitter hook, not a full system yet. | Build a rate-limiter now. | YAGNI; can tighten later without rework. |
| 12 | **Pluggable browser backend.** `BrowserClient` interface, backend swappable via `.env`; default **camoufox** (Decision 19). Adapters get both HTTP + lazy browser clients. | Hard `HTTP` xor `BROWSER` flag; commit to one tool now. | Can't pick the winner without live testing; keeps the choice non-load-bearing. |
| 13 | **One browser container.** Per-site strategy lives in adapters; a 2nd engine/container is deferred until a site needs it. | Multiple browser containers from day one. | Sequential runs need no parallelism; a second browser adds nothing. |
| 19 | **Browser = Camoufox (a hardened Firefox) as an always-warm Playwright WS server** in `subitoo_browser`; the app `connect()`s and opens/closes only a context per run. | (a) Hand-rolled headed Chromium over CDP — *tried, rejected*; (b) Chrome via nodriver in-app; (c) browserless. | Chromium crash-looped in Docker and modern Chrome restricts remote CDP to localhost. Camoufox is Firefox (runs clean in Docker) and its server gives a genuinely warm browser reused across per-heartbeat CLI runs, all in Python. |
| 14 | **No `price_history` table.** `listings.last_price` is the single source for change detection. | Append-only price history. | Only last price matters for the compare; simpler. |
| 15 | **One notification per listing** (not batched). | Batched summary per run. | Each alert has its own link/image, easy to act on. |
| 16 | **ofelia uses `job-exec`** into the app container via the Docker socket (refined by Decision 24 for names). | `job-run` (fresh container per heartbeat). | The app already holds the DB volume, `.env`, and browser access; exec is cheaper. |
| 17 | **Raw `sqlite3` + WAL**, thin data-access layer, **versioned-SQL migrations** via `PRAGMA user_version`; schema core-owned, plugins never migrate. | SQLAlchemy + Alembic; peewee. | Overkill avoided for a 4-table schema; transparent and dependency-light. |
| 18 | **Testing:** unit-test core with injected fakes + in-memory SQLite; ship a **fixture-replay contract harness** as the contributor bar; live tests opt-in, excluded from CI. | Live-only tests; no contract harness. | Deterministic, network-free CI; scraping is flaky so live tests must be opt-in. |
| 20 | **Ship pre-built images; users pull, never build.** CI builds both images once and pushes; `docker-compose.yml` uses `image:`, not `build:`. | (a) Every user builds locally; (b) source-only distribution. | Everyone runs the *same bytes* — same base digest, same baked-in deps, same Firefox build. A frozen image is the smallest bug surface. |
| 21 | **Registry = Docker Hub** (`kianda/subitoo`, `kianda/subitoo-browser`). | (a) GHCR; (b) self-hosted registry. | Maintainer's choice + ubiquity. GHCR was runner-up (no anonymous-pull rate limits); the workflow is near-identical, so switching later is cheap. |
| 22 | **Release on a git semver tag → build → push** `:X.Y.Z` + `:X.Y` + `:X` + `:latest` (stable only). Both images versioned **in lockstep** from one tag. | (a) Build on every push to `main`; (b) version each image independently. | Deliberate, versioned releases; one version pins a **matched** app+browser pair (their Playwright coupling — Decision 19 — must never skew). |
| 23 | **Two separate compose files.** `docker-compose.yml` pulls `image:` (pinned via `${SUBITOO_VERSION:-latest}`); a **standalone** `docker-compose.dev.yml` builds from source with concrete values. | (a) Single file with `build:`; (b) an override merged onto the base. | A self-contained dev file is the simplest to *use* — one `-f`, no registry vars while hacking. Cost = a little duplicated service config, kept in sync by hand. |
| 24 | **Service = container names:** `subitoo`, `subitoo_scheduler`, `subitoo_browser` (app service is `subitoo`, no `_app` — refines Decision 16). | Short names (`app`/`browser`). | One scheme everywhere; matches the `.env` DNS name `subitoo_browser`. |
| 25 | **PR CI gate = network-free pytest** on every PR; green is the bar before a contributed site is merged and baked into the next release. | Manual review only. | The distribution flow makes a merge *ship to everyone*; the gate makes "merge → tag → build → live" safe. Live tests stay opt-in (Decision 18). |

---

## 4. Architecture & Containers

Three containers, one `docker-compose.yml`:

```
subitoo_scheduler ──job-exec: subitoo run-due (@every 1m)──▶ subitoo
   (mcuadros/ofelia)   via /var/run/docker.sock              core lib + Typer CLI,
                                                             owns SQLite (WAL),
                                                             idle (tail -f /dev/null)
                                                                     │ Playwright WS,
                                                                     │ on demand
                                                                     ▼
                                                             subitoo_browser
                                                             Camoufox (Firefox)
                                                             Playwright WS :1234
```

- **`subitoo`** — the app image (`core/`, `sites/`, `notifiers/`, `subitoo` entrypoint).
  **Not a server**: it idles so ofelia can `job-exec` into it. Mounts `./data` (SQLite) + `.env`.
- **`subitoo_scheduler`** — ofelia with one static job, configured via **Docker labels** on
  the app container (`daemon --docker`). Never reconfigured; real schedules live in the DB.
- **`subitoo_browser`** — always-warm Camoufox as a Playwright WS server; only
  browser-using adapters touch it.

The heartbeat is defined as compose **labels** (not an INI file): `ofelia.job-exec.run-due`
with schedule `${OFELIA_SCHEDULE:-@every 1m}` and command `subitoo run-due`. `TZ` flows into
both `subitoo` and `subitoo_scheduler` so schedules agree.

### Browser: Camoufox ↔ Playwright version pin (read before upgrading)

Camoufox declares a bare `playwright` dependency with **no upper bound**, but depends on
Playwright internals that change between releases — so `pip install camoufox` grabs a
too-new Playwright that breaks it. Playwright is therefore pinned in **two places that must
stay identical**: `browser/Dockerfile` (the Camoufox server) and `app/pyproject.toml`
`[browser]` extra (the client that `connect()`s to it).

**Current pin: `playwright==1.53.0`, validated against Camoufox 0.4.11** — the ceiling for
released 0.4.11: 1.54 fails (`proxy: expected object, got null` — 0.4.11 always passes
`proxy=None`; fixed only in Camoufox git `main`), 1.60 fails (Playwright removed
`browserServerImpl.js`). 1.47–1.53 work (1.48 & 1.53 live-verified vs Subito).

**Headed vs headless:** `launch_server` doesn't implement Camoufox's `headless="virtual"` and
only takes a boolean `headless`, so the container runs the server under `xvfb-run` and
`CAMOUFOX_HEADLESS` is a plain boolean (`false` = headed inside the Xvfb virtual display;
`true` = headless).

**To bump:** wait for a Camoufox release > 0.4.11 (or install Camoufox from git `main`), set
both Playwright pins to what it targets, rebuild, and confirm with `subitoo doctor`.

---

## 5. Data Model

Four SQLite tables — **schema is authoritative in `app/subitoo/migrations/*.sql`:**
`queries` (saved searches / CRUD target), `listings` (seen items + current state), `runs`
(execution history), `notifications` (send audit). Two design points worth stating here:

**`queries` state machine** (engine state, distinct from the user `enabled` pause):

```
create → pending ──(due & enabled)──▶ running ──success──▶ pending
                                         │
                                      failure
                                         ▼
                                       error ──(`query retry <id>`)──▶ pending
```

- `pending → running` is an **atomic conditional UPDATE** (`WHERE status='pending'`) — this
  doubles as the concurrency lock.
- `error` is **latched** — excluded from scheduling until `query retry`. `enabled` (user
  intent) is orthogonal to `status` (engine state).
- **Watchdog:** each heartbeat, any `running` older than `RUN_TIMEOUT` is reclaimed → `error`.

**Keys:**
- `listings` UNIQUE `(query_id, site_listing_id)` — the **per-query** dedup key (each query
  keeps its own view; price-change = incoming price vs `last_price`).
- `notifications` is keyed by `site_listing_id` (not a `listings` FK) **on purpose:** a
  `new` listing whose send fails is *not* persisted to `listings` (so it retries), yet we
  still want to log the failed attempt — a FK would forbid that. Status is
  `sent` | `failed` | `suppressed`.

---

## 6. Site Adapters (design)

An adapter is a **translator for one website**: *take a search → fetch → return listings in
one standard shape* (`Listing`). It never filters, dedups, or notifies — the core does all of
that uniformly, so every new adapter gets filters, dedup, seeding, and notifications for free.
Blueprint is `sites/base.py` (`BaseSite`); adding a site = one file in `sites/` (see
`CONTRIBUTING.md`), zero core changes.

**Site kill switch (`BaseSite.enabled`).** A maintainer parks a whole adapter by setting
`enabled = False` and committing — intended for a site that changes its markup or access
rules and needs an adapter fix.
`run-due` skips every query on a parked site; those queries stay `enabled` + `pending`
(untouched) and resume when the flag flips back. **Manual** runs (`query run/retry`)
bypass the switch, so a fix can be validated before re-enabling. Orthogonal to the
per-query `enabled` column: that's a *user* pause; this is a *maintainer* switch, kept in
code so parking is a one-line diff.

**Browser layering** (how per-site browser logic stays modular):

```
Layer 1 — ENGINE (shared, one container): Camoufox Firefox + optional proxy hook
Layer 2 — REUSABLE TECHNIQUES (BrowserClient): session(warm_url=…) → a site-agnostic
          page handle (goto / wait / click_first / text / eval_js / .requests).
          The core knows NO site's banners, toggles, or API shapes.
Layer 3 — PER-SITE CHOREOGRAPHY (inside each adapter): which handle calls, in what
          order, for THIS site — consent wall, search trigger, API capture.
```

Sites that need a browser fetch **entirely through Camoufox** (Decision 19) — no
browser→HTTP cookie handoff, so the whole chain is one consistent real browser. `curl_cffi`
(Firefox impersonation) is reserved for sites that need **no** browser.

| Site | Access | Strategy | `needs_browser` |
|---|---|---|---|
| **Subito** | browser-only | Add-time `resolve()`: load the pasted page, dismiss the cookie wall, toggle a filter so Subito fires its own `/search/items`, capture that URL. Run-time `fetch()`: one warmed session replays it per page (`start += 30`, human-paced), parses the JSON. All in-browser. | `True` |
| **Vinted** | open API | direct `curl_cffi` to the prefiltered URL's API | `False` |

*Escape hatch (Decision 12):* if a future site can't be fetched with Camoufox, the
`BrowserClient` backend is swappable — the only case that justifies a second browser
container. A site's flow may need occasional iteration (e.g. proxies via the Layer-1 hook);
the layering lets us adjust the adapter or swap the engine without re-architecting.

---

## 7. Notifiers (design)

A notifier is a **sender for one channel** (`notifiers/base.py`, `BaseNotifier.send`). The
**core** decides *what* to notify (`new` / `price_change`) and builds the `Notification`; the
notifier only decides *how* to render/send. For now it sends everything to one global channel
(`DEFAULT_CHANNEL`, Pushover). Each send is logged to `notifications`; a failed send does
**not** fail the run and does **not** mark the listing notified → it retries next heartbeat
(at-least-once, self-healing). Adding a channel = one file (see `CONTRIBUTING.md`).

---

## 8. Run Pipeline (behavior contract)

Two levels. Exact logic is in `core/pipeline.py`; the tests in `app/tests/test_pipeline.py`
pin this contract.

- **Heartbeat** (`run-due`, one ofelia tick): watchdog stale runs → find `enabled` +
  `pending` + cron-due queries → for each, sequentially: skip if its site is parked
  (kill switch); atomic-claim `pending → running`; skip if already running; else `run_query`.
- **Run** (`run_query`, one query): open a `runs` row; fetch via the adapter; apply universal
  filters. **First run seeds silently** (store matches as baseline, notify nothing, set
  `seeded`). **Seeded runs** classify each match: not seen → `new`; same price → unchanged
  (touch `last_seen_at`); different price → `price_change`. **Send-then-persist:** send
  first; on success persist + log `sent`; on failure log `failed` and **do not persist**, so
  it re-classifies and retries next run (at-least-once). Failure of the whole fetch latches
  the query to `error`. A per-query `run_delay_seconds` + fresh jitter paces seeded runs
  (the seed run bypasses it).

**Same-heartbeat cross-query dedup.** Two overlapping queries (e.g. "iPhone in Veneto" and
"iPhone in all Italy") can match the *same* ad, and since each query keeps its own state,
each would notify → double ping. `run-due` carries a `heartbeat_seen` set of
`(site, site_listing_id)` shared across every query in the one heartbeat: the **first** query
to *successfully* notify an ad claims it; later queries in the same heartbeat **suppress the
send** but still persist their own state (diaries stay honest, no re-fire next heartbeat).
Claimed **only on send success**, so a failed send leaves the ad open for the next query —
at-least-once preserved. Deliberately **per-heartbeat only**: overlapping queries on
*different* crons still double-notify (accepted; a cross-heartbeat global seen-set would
entangle the queries and break their independent filters/seeding).

---

## 9. Configuration & Secrets

Full list in `.env.example`. Design conventions:

- A typed `Settings` (`pydantic-settings`) loads env + `.env` once and is injected as
  `ctx.config`. DB holds per-query data; `.env` holds secrets + global knobs; nothing
  sensitive touches the DB or git.
- **Plugin secrets are namespaced by key:** Pushover → `PUSHOVER_*`, future Telegram →
  `TELEGRAM_*`, a login-gated site → `VINTED_*`. `ctx.config.for_plugin("telegram")` exposes
  them; the core never hard-codes plugin vars.
- **Fail-fast:** at startup (and `notify test`) the core checks the active channel's required
  secrets are present.

---

## 10. Persistence & Migrations

Stdlib `sqlite3`, thin data-access layer, short-lived connection per CLI invocation. On
connect: `journal_mode=WAL`, `foreign_keys=ON`, `busy_timeout=5000`.
`search`/`filters`/`raw` stored as JSON in TEXT columns. Migrations are ordered SQL files
shipped inside the package (`app/subitoo/migrations/`); `PRAGMA user_version` tracks the
applied version; `db init` / startup applies newer files in a transaction. Schema is
**core-owned** — plugins never migrate (extra site fields live in `raw`).

---

## 11. Testing Strategy

Enabled by dependency injection (toolbox + notifier are injected → fakeable).

1. **Core unit tests** (deterministic, in-memory SQLite + fakes): filters; the pipeline
   contract (new/unchanged/price-change, silent seeding, send-then-persist, failed-send
   retry, same-heartbeat dedup, kill switch); state machine, atomic claim, watchdog, cron.
2. **Adapter contract harness** (the contributor bar): fixture-replay — a new site PR ships a
   saved response fixture + a test asserting `fetch()` yields well-formed `Listing`s.
3. **Notifier contract test:** `send()` with a sample `Notification`, HTTP mocked.
4. **Live smoke tests:** `@pytest.mark.live`, skipped unless `--live`; excluded from CI.

Layers 1–3 are the network-free CI gate (Decision 25); Layer 4 is manual.

---

## 12. Distribution & Release

Design decisions are in the log (20–25); mechanics and the exact tag table are in README
"Releases" and `.github/workflows/`. In brief:

- **Pre-built images, users pull never build** — everyone runs the same bytes.
- **Release = push a git semver tag** `vX.Y.Z` → CI builds + pushes `kianda/subitoo` and
  `kianda/subitoo-browser` tagged `:X.Y.Z`, `:X.Y`, `:X`, `:latest` (stable only; a
  pre-release tag builds just its own version and never moves the pointers).
- **Lockstep versioning** (one `SUBITOO_VERSION` pins both) because the two images share the
  Playwright pin (Decision 19) and must never skew. An unchanged `browser/` is a **cache-hit
  re-tag**, not a rebuild, so lockstep is cheap.
- **arm64** ships for the app; the **browser image is amd64 only** until Camoufox arm64 is
  live-verified (it fetches a Firefox build at build time — don't silently claim arm64).
- **Two compose files:** `docker-compose.yml` (pulls, for users) and standalone
  `docker-compose.dev.yml` (`./start.sh --dev`, builds from source).
- **CI gate:** every PR runs the network-free suite before a site can be merged and shipped.

> **Dependencies float by design (no lockfile).** `pyproject.toml` uses `>=` ranges and the
> `app` image resolves them fresh at build time (`pip install`), so each release picks up the
> newest compatible deps. The pushed image is still the shared artifact — every user of a
> given tag runs identical bytes. **CI installs the same fresh-resolved way** (not a frozen
> set), so tests always run against the versions a build would actually pull. Trade-off
> accepted: re-building an old tag won't necessarily reproduce it bit-for-bit. (A `uv.lock`
> was considered and dropped — it only pays off if the Dockerfile installs *from* it, which
> adds complexity we don't want.)

---

## 13. Open / Deferred (future, intentionally out of scope for now)

- Web UI / FastAPI layer (core is structured so it can be added without rewrite).
- Per-query notification routing + multiple simultaneous channels.
- Per-adapter rate-limiting system (only an optional hook for now).
- Residential-proxy pool / multiple proxy-pinned browser containers.
- Auto-retry-before-latch on transient errors.
- Listing/price pruning command.
- Parallel query execution (currently sequential by design).
