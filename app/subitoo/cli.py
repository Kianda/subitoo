"""Typer CLI — the entire user interface (`subitoo ...`).

Groups: `query` (CRUD + test/retry), `notify` (test), `db` (init), plus top-level
run/run-due/sites/notifiers/runs/listings. See SPEC.md section 9.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import typer
from croniter import croniter
from rich.console import Console
from rich.table import Table

from subitoo.config import get_settings
from subitoo.core import db, pipeline
from subitoo.core.fetch import build_context
from subitoo.core.models import Filters, QueryStatus
from subitoo.sites.base import BaseSite
from subitoo import registry

app = typer.Typer(no_args_is_help=True, help="subitoo — modular marketplace scraper")
query_app = typer.Typer(no_args_is_help=True, help="Create and manage saved queries")
notify_app = typer.Typer(no_args_is_help=True, help="Notification utilities")
db_app = typer.Typer(no_args_is_help=True, help="Database maintenance")
app.add_typer(query_app, name="query")
app.add_typer(notify_app, name="notify")
app.add_typer(db_app, name="db")

console = Console()


@app.callback()
def _main() -> None:
    logging.basicConfig(
        level=get_settings().log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _conn():
    return db.init_db()


def _fmt_ts(ts: int | None) -> str:
    if not ts:
        return "-"
    tz = ZoneInfo(get_settings().tz)
    return datetime.fromtimestamp(ts, tz).strftime("%Y-%m-%d %H:%M")


def _validate_cron_or_exit(expr: str) -> None:
    if not croniter.is_valid(expr):
        console.print(f"[red]Invalid cron expression:[/red] {expr!r}")
        raise typer.Exit(1)


def _parse_run_delay(raw: str | int) -> int:
    """Validate a per-run delay: a non-negative integer number of seconds."""
    try:
        val = int(raw)
        if val < 0:
            raise ValueError
    except (TypeError, ValueError):
        raise ValueError("run_delay_seconds must be a non-negative integer") from None
    return val


def _prompt_run_delay() -> int:
    raw = typer.prompt("delay before each run, seconds (human pacing; seeding skips it)",
                       default="5")
    try:
        return _parse_run_delay(raw)
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1) from None


def _validate_search_or_exit(adapter: BaseSite, search: dict) -> dict:
    """Adapter-side validation is a user-input check, not a crash: a bad URL or an
    unsupported filter should read as one red line, the way a bad cron does."""
    try:
        return adapter.validate_search(search)
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1) from None


def _preview_cron(expr: str, n: int = 3) -> None:
    tz = ZoneInfo(get_settings().tz)
    itr = croniter(expr, datetime.now(tz))
    console.print("[dim]Next fire times:[/dim]")
    for _ in range(n):
        console.print(f"  • {itr.get_next(datetime):%Y-%m-%d %H:%M} ({get_settings().tz})")


# --------------------------------------------------------------------------- db

@db_app.command("init")
def db_init() -> None:
    """Create or upgrade the database schema."""
    conn = _conn()
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    console.print(f"[green]DB ready[/green] at {get_settings().db_path} (schema v{version})")


# ---------------------------------------------------------------- introspection

@app.command("doctor")
def cmd_doctor() -> None:
    """Health check: DB, registered plugins, browser reachability, channel config.

    Exits non-zero if a critical check (DB or browser) fails. A missing notification
    channel config is a warning, not a failure.
    """
    critical_ok = True

    # 1. DB + migrations
    try:
        conn = _conn()
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        console.print(f"[green]✓[/green] database ready (schema v{version}) at {get_settings().db_path}")
    except Exception as e:  # noqa: BLE001
        console.print(f"[red]✗[/red] database: {e}")
        critical_ok = False

    # 2. plugins
    registry.load_all()
    console.print(f"[green]✓[/green] sites: {', '.join(registry.site_keys()) or '(none)'}")
    console.print(f"[green]✓[/green] notifiers: {', '.join(registry.notifier_keys()) or '(none)'}")

    # 3. browser service (WS reachability)
    from subitoo.core.fetch import BrowserClient
    try:
        BrowserClient(get_settings()).health_check()
        console.print(f"[green]✓[/green] browser reachable at {get_settings().browser_ws_url}")
    except Exception as e:  # noqa: BLE001
        console.print(f"[red]✗[/red] browser: {e}")
        critical_ok = False

    # 4. active notification channel (warning only)
    from subitoo.core import notify
    try:
        notify.active_notifier().validate_config()
        console.print(f"[green]✓[/green] channel '{get_settings().default_channel}' configured")
    except Exception as e:  # noqa: BLE001
        console.print(f"[yellow]![/yellow] channel not configured: {e}")

    if not critical_ok:
        raise typer.Exit(1)
    console.print("[bold green]all critical checks passed[/bold green]")


@app.command("sites")
def cmd_sites() -> None:
    """List registered site adapters."""
    registry.load_all()
    t = Table("key", "enabled", "needs_browser", "class")
    for key in registry.site_keys():
        cls = registry.SITES[key]
        enabled = getattr(cls, "enabled", True)
        t.add_row(
            key,
            "✓" if enabled else "[red]✗ parked[/red]",
            "yes" if getattr(cls, "needs_browser", False) else "no",
            cls.__name__,
        )
    console.print(t)


@app.command("notifiers")
def cmd_notifiers() -> None:
    """List registered notification channels."""
    registry.load_all()
    t = Table("key", "class")
    for key in registry.notifier_keys():
        t.add_row(key, registry.NOTIFIERS[key].__name__)
    console.print(t)


# --------------------------------------------------------------------- query CRUD

def _pick_site(available: list[str]) -> tuple[str, dict]:
    """Work out which site the query is for, and any search field that fell out of it.

    URL-based adapters declare ``url_host_pattern``, so the URL the user has to paste
    anyway already answers "which site?" — we ask for it first and infer. That also
    makes a site/URL mismatch impossible instead of an error. Only if nothing claims
    the URL (or no installed adapter is URL-based) do we fall back to asking.
    """
    if any(registry.SITES[k].url_host_pattern for k in available):
        url = typer.prompt("Paste the search URL").strip()
        site = registry.site_for_url(url)
        if site:
            console.print(f"Site: [bold]{site}[/bold] (from the URL)")
            return site, {"url": url}
        console.print(f"[yellow]No installed site handles "
                      f"{urlsplit(url).netloc or url!r}.[/yellow]")
    if len(available) == 1:
        console.print(f"Site: [bold]{available[0]}[/bold] (only one available)")
        return available[0], {}
    console.print(f"Available sites: {', '.join(available)}")
    return typer.prompt("Site"), {}


def _needs_resolve(adapter: BaseSite) -> bool:
    """Whether this adapter does add-time work — i.e. overrides ``resolve``. The base
    implementation is a no-op, so calling it for (say) Vinted only prints a status
    message about work that never happens."""
    return type(adapter).resolve is not BaseSite.resolve


def _run_wizard(site: str, prefill: dict | None = None) -> tuple[dict, Filters, str, str, int]:
    adapter = registry.get_site(site)
    search: dict = dict(prefill or {})  # e.g. the URL we inferred the site from
    console.print(f"[bold]Search fields for {site}[/bold]")
    for f in adapter.search_schema:
        if f.name in search:
            console.print(f"  {f.prompt}: [dim]{search[f.name]}[/dim]")
            continue
        val = typer.prompt(f"  {f.prompt}", default=f.default or "", show_default=bool(f.default))
        if val:
            search[f.name] = val
    _validate_search_or_exit(adapter, search)

    console.print("[bold]Universal filters[/bold] (blank = skip)")
    pmin = typer.prompt("  price_min", default="", show_default=False)
    pmax = typer.prompt("  price_max", default="", show_default=False)
    shipping = typer.prompt("  shipping (any/yes/no)", default="any")
    t_inc = typer.prompt("  title_include (regex)", default="", show_default=False)
    t_exc = typer.prompt("  title_exclude (regex)", default="", show_default=False)
    filters = Filters(
        price_min=float(pmin) if pmin else None,
        price_max=float(pmax) if pmax else None,
        shipping=shipping or "any",
        title_include=t_inc or None,
        title_exclude=t_exc or None,
    )

    cron = typer.prompt("cron schedule", default="0 */4 * * *")  # default: every 4 hours
    _validate_cron_or_exit(cron)
    _preview_cron(cron)
    delay = _prompt_run_delay()
    name = typer.prompt("Query name")
    return search, filters, cron, name, delay


def _resolve_search(site: str, search: dict) -> dict:
    """Enrich the search blob at add-time (adapter.resolve) — e.g. Subito captures the
    fast-replay api_url so runtime doesn't have to re-derive it. Pure definition-time
    work — no DB, no fetching listings. Interactive `add` then seeds via the real
    pipeline; the confirmation that the URL works falls out of that seed run.

    A no-op for adapters that don't override ``resolve`` — callers skip it entirely
    via ``_needs_resolve`` rather than printing a status for work that never happens.
    """
    adapter = registry.get_site(site)
    ctx = build_context(get_settings(), needs_browser=adapter.needs_browser)
    with console.status(f"Resolving the search for {site}…"):
        search = adapter.resolve(search, ctx)
    return search


@query_app.command("add")
def query_add(
    from_json: Optional[Path] = typer.Option(None, "--from-json", help="Load the query from a JSON file"),
) -> None:
    """Create a query (interactive wizard, or --from-json for scripting)."""
    conn = _conn()
    if from_json:
        spec = json.loads(from_json.read_text())
        filters = Filters.model_validate(spec.get("filters", {}))
        _validate_cron_or_exit(spec["cron"])
        adapter = registry.get_site(spec["site"])
        search = _validate_search_or_exit(adapter, spec["search"])
        if _needs_resolve(adapter):
            search = _resolve_search(spec["site"], search)
        delay = _parse_run_delay(spec.get("run_delay_seconds", 5))
        qid = db.create_query(conn, name=spec["name"], site=spec["site"],
                              search=search, filters=filters, cron=spec["cron"],
                              enabled=spec.get("enabled", True), run_delay_seconds=delay)
        # Stays lazy on purpose: bulk imports shouldn't fire one browser session per
        # query. The scheduler seeds each on its first run (see `seeded` in pipeline).
        console.print(f"[green]Created query {qid}[/green] ({spec['name']})")
        return

    registry.load_all()
    available = registry.site_keys()
    site, prefill = _pick_site(available)
    if site not in available:  # only reachable via the typed fallback in _pick_site
        console.print(f"[red]Unknown site {site!r}[/red] (have: {', '.join(available)})")
        raise typer.Exit(1)

    search, filters, cron, name, delay = _run_wizard(site, prefill)
    if _needs_resolve(registry.get_site(site)):
        search = _resolve_search(site, search)
    qid = db.create_query(conn, name=name, site=site, search=search, filters=filters,
                          cron=cron, run_delay_seconds=delay)
    # Seed now via the real pipeline: the first run records the current listings as the
    # baseline (notifies nothing) and flips `seeded` — and doubles as the confirmation
    # that the URL actually works. If the fetch fails it latches to 'error' like any run.
    db.set_status(conn, qid, QueryStatus.RUNNING)
    with console.status("Seeding the baseline…"):
        res = pipeline.run_query(conn, qid)
    if db.get_query(conn, qid).status == QueryStatus.ERROR:
        console.print(f"[yellow]Created query {qid}[/yellow] ({name}) — but the seed fetch "
                      f"failed; it's latched to 'error'. Fix the URL and run "
                      f"[bold]query retry {qid}[/bold] to try again.")
    else:
        console.print(f"[green]Created query {qid}[/green] ({name}) — "
                      f"seeded {res.n_found} listing(s)")


@query_app.command("list")
def query_list(as_json: bool = typer.Option(False, "--json")) -> None:
    """List all queries."""
    conn = _conn()
    queries = db.list_queries(conn)
    if as_json:
        console.print_json(json.dumps([
            {"id": q.id, "name": q.name, "site": q.site, "enabled": q.enabled,
             "status": q.status.value, "cron": q.cron, "last_run_at": q.last_run_at}
            for q in queries
        ]))
        return
    registry.load_all()
    t = Table("id", "name", "site", "enabled", "status", "cron", "last run")
    for q in queries:
        status_color = {"pending": "green", "running": "yellow", "error": "red"}[q.status.value]
        # A parked site (in-code kill switch) means the scheduler skips this query
        # even though it's enabled — flag it so that's not a mystery.
        site_cls = registry.SITES.get(q.site)
        site_cell = q.site if getattr(site_cls, "enabled", True) else f"{q.site} [red](parked)[/red]"
        t.add_row(str(q.id), q.name, site_cell, "✓" if q.enabled else "✗",
                  f"[{status_color}]{q.status.value}[/{status_color}]", q.cron,
                  _fmt_ts(q.last_run_at))
    console.print(t)


@query_app.command("show")
def query_show(query_id: int) -> None:
    """Show one query in full."""
    conn = _conn()
    q = db.get_query(conn, query_id)
    if not q:
        console.print(f"[red]No query {query_id}[/red]")
        raise typer.Exit(1)
    console.print_json(json.dumps({
        "id": q.id, "name": q.name, "site": q.site, "search": q.search,
        "filters": q.filters.model_dump(), "cron": q.cron,
        "run_delay_seconds": q.run_delay_seconds, "enabled": q.enabled,
        "seeded": q.seeded, "status": q.status.value,
        "last_run_at": _fmt_ts(q.last_run_at),
    }))


@query_app.command("rm")
def query_rm(query_id: int, yes: bool = typer.Option(False, "--yes", "-y")) -> None:
    """Delete a query (and its listings/runs)."""
    conn = _conn()
    q = db.get_query(conn, query_id)
    if not q:
        console.print(f"[red]No query {query_id}[/red]")
        raise typer.Exit(1)
    if not yes and not typer.confirm(f"Delete query {query_id} ({q.name})?"):
        raise typer.Abort()
    db.delete_query(conn, query_id)
    console.print(f"[green]Deleted query {query_id}[/green]")


@query_app.command("enable")
def query_enable(query_id: int) -> None:
    """Enable a query."""
    db.update_query(_conn(), query_id, enabled=True)
    console.print(f"[green]Enabled query {query_id}[/green]")


@query_app.command("disable")
def query_disable(query_id: int) -> None:
    """Disable (pause) a query without deleting it."""
    db.update_query(_conn(), query_id, enabled=False)
    console.print(f"[yellow]Disabled query {query_id}[/yellow]")


@query_app.command("retry")
def query_retry(query_id: int) -> None:
    """Clear an 'error' latch: error -> pending."""
    conn = _conn()
    q = db.get_query(conn, query_id)
    if not q:
        console.print(f"[red]No query {query_id}[/red]")
        raise typer.Exit(1)
    if q.status != QueryStatus.ERROR:
        console.print(f"[yellow]Query {query_id} is '{q.status.value}', not 'error' — nothing to do[/yellow]")
        return
    db.set_status(conn, query_id, QueryStatus.PENDING)
    console.print(f"[green]Query {query_id} reset to pending[/green]")


@query_app.command("test")
def query_test(query_id: int) -> None:
    """Dry run: fetch + filter, print matches. No notify, no persist, no seeding."""
    conn = _conn()
    try:
        matches = pipeline.dry_run(conn, query_id)
    except Exception as e:  # noqa: BLE001
        console.print(f"[red]Dry run failed:[/red] {e}")
        raise typer.Exit(1)
    console.print(f"[green]{len(matches)} matching listing(s)[/green]")
    t = Table("id", "title", "price", "shipping", "location")
    for m in matches[:50]:
        t.add_row(m.site_listing_id, m.title[:60],
                  "-" if m.price is None else f"{m.price:g} {m.currency}",
                  {True: "yes", False: "no", None: "?"}[m.shipping_available],
                  m.location or "-")
    console.print(t)


# --------------------------------------------------------------------- running

@app.command("run-due")
def cmd_run_due() -> None:
    """Dispatcher: run every query whose schedule is due now. (ofelia entrypoint)"""
    conn = _conn()
    ran = pipeline.run_due(conn)
    console.print(f"ran {len(ran)} quer{'y' if len(ran)==1 else 'ies'}: {ran}")


@app.command("run")
def cmd_run(query_id: int, dry_run: bool = typer.Option(False, "--dry-run")) -> None:
    """Run one query now. --dry-run = fetch+filter only (no notify/persist)."""
    conn = _conn()
    q = db.get_query(conn, query_id)
    if not q:
        console.print(f"[red]No query {query_id}[/red]")
        raise typer.Exit(1)
    if dry_run:
        return query_test(query_id)
    if q.status == QueryStatus.RUNNING:
        console.print(f"[yellow]Query {query_id} is already running[/yellow]")
        raise typer.Exit(1)
    db.set_status(conn, query_id, QueryStatus.RUNNING)
    res = pipeline.run_query(conn, query_id)
    console.print(f"found={res.n_found} new={res.n_new} "
                  f"price_changed={res.n_price_changed} notified={res.n_notified}")


@app.command("runs")
def cmd_runs(query_id: int) -> None:
    """Show recent run history for a query."""
    conn = _conn()
    t = Table("run", "started", "status", "found", "new", "changed", "notified", "error")
    for r in db.recent_runs(conn, query_id):
        t.add_row(str(r["id"]), _fmt_ts(r["started_at"]), r["status"],
                  str(r["n_found"]), str(r["n_new"]), str(r["n_price_changed"]),
                  str(r["n_notified"]), (r["error_message"] or "")[:40])
    console.print(t)


@app.command("listings")
def cmd_listings(query_id: int) -> None:
    """Show seen listings for a query."""
    conn = _conn()
    t = Table("listing id", "title", "last price", "last seen")
    for r in db.list_listings(conn, query_id):
        price = "-" if r["last_price"] is None else f"{r['last_price']:g} {r['currency']}"
        t.add_row(r["site_listing_id"], (r["title"] or "")[:50], price, _fmt_ts(r["last_seen_at"]))
    console.print(t)


# --------------------------------------------------------------------- notify

@notify_app.command("test")
def notify_test() -> None:
    """Send a test notification through the active channel."""
    from subitoo.core import notify
    from subitoo.core.models import Notification
    try:
        notify.active_notifier().validate_config()
        notify.send(Notification(kind="new", title="subitoo test notification",
                                 url="https://example.com", query_name="test",
                                 price=1.0, currency="EUR"))
    except Exception as e:  # noqa: BLE001
        console.print(f"[red]Notification failed:[/red] {e}")
        raise typer.Exit(1)
    console.print("[green]Test notification sent[/green]")


if __name__ == "__main__":
    app()
