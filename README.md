# subitoo

A self-hosted, **modular** marketplace scraper.

> 🛠️ Add a site or channel: [`CONTRIBUTING.md`](./CONTRIBUTING.md) · 📐 Design & rationale:
> [`SPEC.md`](./SPEC.md)

## Requirements

- [Docker](https://docs.docker.com/get-docker/)
- For phone notifications — optional, and pick one: [ntfy](https://ntfy.sh) (no account, just install the app) or a [Pushover](https://pushover.net) account

## Features

- Watch multiple marketplace sites — **Subito.it** and **Vinted** (more coming soon...)
- Per-query schedules (each saved search has its own cron)
- Alerts on **new** matches and **price changes**
- Universal filters: price range, shipping present/absent, title include/exclude (regex)
- One notification per listing, with image + link
- Overlapping searches are de-duplicated so you're pinged **once**, not twice

## Quick start

```bash
cp .env.example .env      # optional: set up notifications (see below)
./start.sh                # pull the images + start everything, wait until healthy
./subitoo query add       # create a search (interactive wizard)
./subitoo query list      # see your searches
./stop.sh                 # stop (your data in ./data is always preserved)
```

`./start.sh` pulls pre-built images (`kianda/*` on Docker Hub), so every install runs the
same build. Pin a version with `SUBITOO_VERSION` in `.env` (default `latest`). Once up,
the scheduler runs due searches automatically.

| Script | What it does |
|---|---|
| `./start.sh` | Pull images + start, wait until healthy. Add `--dev` to build from source. |
| `./stop.sh` | Stop the stack; your data in `./data` is kept. |
| `./subitoo …` | Run any CLI command inside the container (`./subitoo query list`, etc.). |

**Tip:** symlink the wrapper onto your PATH — `ln -s "$PWD/subitoo" ~/.local/bin/subitoo` —
then just `subitoo query list` from anywhere.

## Create a search

`./subitoo query add` walks you through it:

1. **Pick a site** — `subito` or `vinted`.
2. **Paste the search URL.** Set up the keywords, category, region and sorting on the site
   itself, then copy the URL from your browser's address bar. (Result pages to scan defaults
   to 1 — that's 30 listings on Subito, 96 on Vinted.)
3. **Add filters** — price range, shipping, title include/exclude (regex). Which of these
   you actually need depends on the site — see below.
4. **Set the schedule** — a cron expression (default: every 4 hours).

The search runs once immediately to seed a silent baseline (this also confirms the URL
works); you start getting alerts from the next run.

### Which filters go where

The two sites draw the line in different places, because Subito's own price filter only
appears once you pick a category while Vinted lets you set everything up front:

| | Subito.it | Vinted |
|---|---|---|
| **Price** | Subitoo's `price_min` / `price_max` | on Vinted — it rides along in the URL |
| **Shipping** | Subitoo's `shipping` filter | n/a, every Vinted item ships |
| **Category, brand, size, condition** | in the pasted URL | in the pasted URL |
| **Title regex** | Subitoo's `title_include` / `title_exclude` | same — the one thing neither site can express |

So on Vinted you leave `price_min`, `price_max` and `shipping` blank and use only the title
regex. If a Vinted URL carries a filter Subitoo can't pass through to Vinted's API, it's
refused when you create the search rather than silently dropped, so your results always
match what the URL says.

### Title filters (regex)

`title_include` / `title_exclude` are Python regexes matched against the listing **title**:

- **Case-insensitive**, and matched **anywhere in the title** — `iphone` matches
  "Apple iPhone 13". Anchor with `^` / `$` if you need the whole title.
- `title_include` keeps only matching listings; `title_exclude` drops matching ones.
  If both match, **exclude wins**.
- Blank = filter off. An invalid pattern is rejected when you create the search.

Example (on Subito, where the price filter is Subitoo's) — Synology NAS between €75 and
€150, only the models you want, minus the cut-down one:

```
  price_min             75
  price_max             150
  title_include (regex) DS ?124|DS ?220|DS ?223|DS ?218
  title_exclude (regex) DS218j
```

`|` is "or", `DS ?124` allows an optional space ("DS124" and "DS 124" both match). Note
that `DS ?218` also matches "DS218j" — the exclude is what filters it back out.

**Tip:** test a pattern on [regex101.com](https://regex101.com/) (flavor: Python, flag
`i`) before saving the search.

## CLI

Run via the wrapper (`./subitoo <cmd>`):

```
query add [--from-json f]    # create a search (wizard or JSON)
query list | show <id>       # inspect
query enable|disable <id>    # pause switch
query retry <id>             # clear an 'error' latch -> pending
query rm <id>                # delete
query test <id>              # dry run: fetch + filter, no notify/persist
run <id> [--dry-run]         # run one search now
runs <id> | listings <id>    # history / seen items
sites | notifiers            # list installed plugins
doctor                       # health check (DB, plugins, browser, channel)
notify test                  # send a test notification
```

`subitoo --help` documents everything.

## Notifications

**Pushover** and **ntfy** ship in-box. One channel is active at a time — pick it with
`DEFAULT_CHANNEL` in `.env` and verify with `./subitoo notify test`. Adding another is a
single file (see [`CONTRIBUTING.md`](./CONTRIBUTING.md)).

Either way an alert carries the listing's image, and tapping it opens the listing.

### Pushover

Grab your **USER_KEY** from the [Pushover homepage](https://pushover.net) and an
**APPLICATION_TOKEN** from a [new Pushover app](https://pushover.net/apps/build), then put
them in `.env`:

```ini
DEFAULT_CHANNEL=pushover
PUSHOVER_USER=your_user_key
PUSHOVER_TOKEN=your_app_token
```

### ntfy

Install [ntfy](https://ntfy.sh) on your phone, subscribe to a topic, and name that topic
in `.env`. No account needed:

```ini
DEFAULT_CHANNEL=ntfy
NTFY_TOPIC=subitoo-3f9c1ab74e2d
```

⚠️ **The topic name is the password.** On the public ntfy.sh server anyone who knows it
can read your alerts and post to them, so use a long random one — `NTFY_TOPIC=nas` is
effectively public. Self-hosting instead? Set `NTFY_URL` to your server, and `NTFY_TOKEN`
if the topic is access-controlled.

## FAQ

**Why no notifications on the first run?** By design — the first run just records the
current matches as a baseline. You get alerts from the next run onward, when something is
new or changes price.

**Will overlapping searches double-notify me?** No, as long as they run on the same
schedule. If "iPhone in Veneto" and "iPhone in all Italy" both match an ad on the same
run, only the first pings you; the other still records it silently. (Searches on
*different* schedules can each ping once — put overlapping searches on the same cron.)

**What if the scheduler fires while a previous run is still going?** It's skipped — a
built-in lock means a search never overlaps itself.

**Where is my data?** In `./data` (SQLite DB). Back it up to avoid losing your history.

## Contributing & design

- **Add a site or notification channel:** [`CONTRIBUTING.md`](./CONTRIBUTING.md).
- **Architecture, decisions, and the browser/Playwright version pin:** [`SPEC.md`](./SPEC.md).
- **Cutting a release (maintainer):** push a semver tag (`git tag v2.0.0 && git push origin
  v2.0.0`); CI builds and publishes the images. Details in [`SPEC.md`](./SPEC.md) §12; the
  workflow needs the `DOCKERHUB_USERNAME` / `DOCKERHUB_TOKEN` repo secrets.

## License

subitoo is licensed under the GNU GPLv3 — see [`LICENSE`](./LICENSE).
