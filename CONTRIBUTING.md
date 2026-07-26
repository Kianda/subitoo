# Contributing to subitoo

Thanks for helping! The two things contributors add most are **new sites** (Vinted,
Wallapop, …) and **new notification channels** — each is a single Python file. The core
(filters, dedup, first-run seeding, scheduling, notifications) is done and site-agnostic;
you only write the adapter. Design rationale lives in [`SPEC.md`](./SPEC.md).

## Add a site

Create `app/subitoo/sites/<name>.py`:

```python
from subitoo.sites.base import BaseSite
from subitoo.registry import register
from subitoo.core.models import Listing, SearchField

@register("mysite")
class MySite(BaseSite):
    needs_browser = False                      # True if it needs the Camoufox browser
    search_schema = [SearchField("url", "Search URL")]

    def fetch(self, search, ctx):
        resp = ctx.http.get(search["url"])     # ctx.http = Firefox-impersonated client
        return [Listing(site_listing_id=..., title=..., price=..., url=...) for ... in ...]
```

That's it — filtering, dedup, first-run seeding, and notifications are handled by the
core; adding a site changes **zero** core lines. Use `app/subitoo/sites/subito.py` (a
browser-based adapter) and `sites/base.py` as references.

To take a whole site offline (e.g. it changed its markup or access rules and the adapter
needs a fix), set `enabled = False` on the class and commit — the scheduler skips it while
manual runs still work, so you can test a fix. See `BaseSite` and SPEC.md §6.

## Add a notification channel

Create `app/subitoo/notifiers/<name>.py` subclassing `BaseNotifier`, implement
`send(notification)`, read secrets from `self.conf` (namespaced `MYCHANNEL_*` env vars),
and `@register("mychannel")`. Model it on `notifiers/pushover.py`.

## The bar for a new site

A site PR must ship:

1. The adapter file — `@register("<key>")`, subclassing `BaseSite`.
2. A saved response fixture under `app/tests/fixtures/`.
3. A parse/contract test that replays that fixture and asserts `fetch()` yields
   well-formed `Listing`s. Model it on `app/tests/test_subito.py`.

Tests must be **network-free** (fixtures only) so CI stays deterministic. Live scraping is
verified manually with `pytest --live`, never in CI.

## Dev setup

Fast, network-free loop:

```bash
cd app
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e ".[dev]"
.venv/bin/python -m pytest
```

Full container loop (builds from source): `./start.sh --dev`.

## Submitting a PR

1. Fork, branch, commit.
2. Open the PR. CI runs the test suite — **it must be green** before review.
3. A maintainer reviews and merges.
4. The maintainer cuts a release (tags a version); CI builds and publishes the images and
   your contribution goes live. See [`SPEC.md`](./SPEC.md) §12.

## Style

Match the surrounding code — same naming, comment density, and idioms. Keep it simple, and
prefer the dependencies already in the project over pulling in new ones.
