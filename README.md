# subitoo

A self-hosted, **modular** marketplace scraper.

> 🛠️ Add a site or channel: [`CONTRIBUTING.md`](./CONTRIBUTING.md) · 📐 Design & rationale:
> [`SPEC.md`](./SPEC.md)

## Requirements

- [Docker](https://docs.docker.com/get-docker/)
- [Pushover](https://pushover.net) account — optional, for phone notifications

## Features

- Watch multiple marketplace sites (more coming soon...)
- Per-query schedules (each saved search has its own cron)
- Alerts on **new** matches and **price changes**
- Universal filters: price range, shipping present/absent, title include/exclude (regex)
- One notification per listing, with image + link
- Overlapping searches are de-duplicated so you're pinged **once**, not twice

## Quick start

```bash
cp .env.example .env      # optional: add PUSHOVER_TOKEN / PUSHOVER_USER
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

1. **Pick a site** (e.g. `subito`).
2. **Paste a Subito search URL.** Set up the region, category, keywords and sorting on
   Subito itself, then copy the URL from your browser. (Result pages to scan defaults to 1.)
3. **Add filters** — price range, shipping, title include/exclude (regex).
4. **Set the schedule** — a cron expression (default: every 4 hours).

The search runs once immediately to seed a silent baseline (this also confirms the URL
works); you start getting alerts from the next run.

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

## Notifications (Pushover)

Grab your **USER_KEY** from the [Pushover homepage](https://pushover.net) and an
**APPLICATION_TOKEN** from a [new Pushover app](https://pushover.net/apps/build), then put
them in `.env`:

```ini
PUSHOVER_USER=your_user_key
PUSHOVER_TOKEN=your_app_token
```

Verify with `./subitoo notify test`. Notifications are modular — other channels can be
added (see [`CONTRIBUTING.md`](./CONTRIBUTING.md)); set the active one with `DEFAULT_CHANNEL`.

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
