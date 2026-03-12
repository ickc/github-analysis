"""CLI entry point for github-analysis.

Commands:
  fetch      Fetch raw workflow runs and jobs from the GitHub API (cached).
  report     Compute and save usage + performance metric CSVs from cache.
  show       Pretty-print a metrics table to the terminal.

Example workflow:
  github-analysis fetch --org UniExeterRSE --period last-year --cache-dir ./cache
  github-analysis report --org UniExeterRSE --cache-dir ./cache --output-dir ./reports
  github-analysis show usage workflows --org UniExeterRSE --cache-dir ./cache
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from .compute import PERFORMANCE_TABLES, USAGE_TABLES
from .raw import fetch_org, fetch_repo

app = typer.Typer(help="GitHub Actions metrics analysis CLI.", add_completion=False)
console = Console()

# ---------------------------------------------------------------------------
# Period helper
# ---------------------------------------------------------------------------

_PERIODS = {
    "last-year": 365,
    "last-6-months": 183,
    "last-3-months": 91,
    "last-month": 30,
}


def _parse_period(period: str) -> tuple[datetime, datetime]:
    until = datetime.now(tz=timezone.utc)
    days = _PERIODS.get(period)
    if days is not None:
        since = until - timedelta(days=days)
    else:
        # Try parsing as YYYY-MM-DD..YYYY-MM-DD
        try:
            parts = period.split("..")
            since = datetime.fromisoformat(parts[0])
            until = datetime.fromisoformat(parts[1]) if len(parts) > 1 else until
        except ValueError:
            raise typer.BadParameter(
                f"Unknown period '{period}'. Use: {list(_PERIODS)} or 'YYYY-MM-DD..YYYY-MM-DD'."
            )
    return since, until


# ---------------------------------------------------------------------------
# fetch
# ---------------------------------------------------------------------------

@app.command()
def fetch(
    org: str = typer.Option(..., help="GitHub organization name."),
    repo: Optional[str] = typer.Option(None, help="Single repo to fetch (default: all repos)."),
    period: str = typer.Option("last-year", help="Time period: last-year, last-6-months, last-3-months, last-month, or YYYY-MM-DD..YYYY-MM-DD."),
    cache_dir: Path = typer.Option(Path("cache"), help="Directory to store cached JSON."),
    force: bool = typer.Option(False, "--force", help="Re-fetch even if cached data exists."),
) -> None:
    """Fetch raw workflow run and job data from GitHub and cache to disk."""
    since, until = _parse_period(period)
    console.print(f"[bold]Fetching[/bold] {org} | period: {since.date()} to {until.date()}")

    if repo:
        fetch_repo(org, repo, cache_dir, since=since, until=until, force=force)
        console.print(f"[green]Done[/green]: {org}/{repo}")
    else:
        repos = fetch_org(org, cache_dir, since=since, until=until, force=force)
        console.print(f"[green]Done[/green]: fetched {len(repos)} repos")


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------

@app.command()
def report(
    org: str = typer.Option(..., help="GitHub organization name."),
    cache_dir: Path = typer.Option(Path("cache"), help="Directory with cached JSON."),
    output_dir: Path = typer.Option(Path("reports"), help="Directory to write CSV reports."),
    metric: str = typer.Option("both", help="Which metrics to generate: usage, performance, or both."),
) -> None:
    """Compute usage and/or performance metrics from cache and write CSVs.

    Output structure mirrors the GitHub UI:
      {output_dir}/actions-usage-metrics/{workflows,jobs,repositories,runtime-os,runner-type}.csv
      {output_dir}/actions-performance-metrics/{workflows,jobs,repositories,runtime-os,runner-type}.csv
    """
    tables_to_run: dict[str, dict] = {}
    if metric in ("usage", "both"):
        tables_to_run["actions-usage-metrics"] = USAGE_TABLES
    if metric in ("performance", "both"):
        tables_to_run["actions-performance-metrics"] = PERFORMANCE_TABLES

    for subdir, table_funcs in tables_to_run.items():
        out = output_dir / subdir
        out.mkdir(parents=True, exist_ok=True)
        for name, func in table_funcs.items():
            df = func(org, cache_dir)
            path = out / f"{name}.csv"
            df.to_csv(path, index=False)
            console.print(f"[green]Wrote[/green] {path} ({len(df)} rows)")


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------

@app.command()
def show(
    metric: str = typer.Argument(..., help="Metric type: usage or performance."),
    table: str = typer.Argument(..., help="Table: workflows, jobs, repositories, runtime-os, runner-type."),
    org: str = typer.Option(..., help="GitHub organization name."),
    cache_dir: Path = typer.Option(Path("cache"), help="Directory with cached JSON."),
    top: int = typer.Option(20, help="Show top N rows."),
) -> None:
    """Pretty-print a metrics table from cached data."""
    catalog = {"usage": USAGE_TABLES, "performance": PERFORMANCE_TABLES}
    if metric not in catalog:
        raise typer.BadParameter(f"metric must be one of: {list(catalog)}")
    table_funcs = catalog[metric]
    if table not in table_funcs:
        raise typer.BadParameter(f"table must be one of: {list(table_funcs)}")

    df = table_funcs[table](org, cache_dir).head(top)
    if df.empty:
        console.print("[yellow]No data — have you run 'fetch' yet?[/yellow]")
        return

    rich_table = Table(title=f"{metric} / {table} — {org} (top {top})")
    for col in df.columns:
        rich_table.add_column(str(col), no_wrap=True)
    for _, row in df.iterrows():
        rich_table.add_row(*[str(v) for v in row])
    console.print(rich_table)
