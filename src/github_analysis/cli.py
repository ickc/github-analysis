"""CLI entry point for github-analysis.

Commands:
  fetch      Fetch raw workflow runs and jobs from the GitHub API (cached).
  report     Compute and save usage + performance metric CSVs from cache.
  dashboard  Build a static HTML dashboard from generated CSVs.
  recreate   Fetch data, generate CSVs, and optionally build the dashboard.
  show       Pretty-print a metrics table to the terminal.

Example workflow:
  github-analysis fetch --org example-org --period last-year --cache-dir ./cache
  github-analysis report --org example-org --cache-dir ./cache --output-dir ./reports
  github-analysis dashboard --config ./analysis.toml
  github-analysis show usage workflows --org example-org --cache-dir ./cache
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from .compute import PERFORMANCE_TABLES, USAGE_TABLES
from .dashboard import DashboardConfig, build_dashboard, load_dashboard_config
from .raw import fetch_org, fetch_repo

app = typer.Typer(help="GitHub Actions metrics analysis CLI.", add_completion=False)
console = Console()


def _resolve_cli_path(root: Path, path: Path) -> Path:
    return (root / path).resolve() if not path.is_absolute() else path

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

def _write_reports(
    org: str,
    cache_dir: Path,
    output_dir: Path,
    metric: str = "both",
) -> None:
    tables_to_run: dict[str, dict] = {}
    if metric in ("usage", "both"):
        tables_to_run["actions-usage-metrics"] = USAGE_TABLES
    if metric in ("performance", "both"):
        tables_to_run["actions-performance-metrics"] = PERFORMANCE_TABLES
    if not tables_to_run:
        raise typer.BadParameter("metric must be one of: usage, performance, both")

    for subdir, table_funcs in tables_to_run.items():
        out = output_dir / subdir
        out.mkdir(parents=True, exist_ok=True)
        for name, func in table_funcs.items():
            df = func(org, cache_dir)
            path = out / f"{name}.csv"
            df.to_csv(path, index=False)
            console.print(f"[green]Wrote[/green] {path} ({len(df)} rows)")


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
    _write_reports(org, cache_dir, output_dir, metric)


# ---------------------------------------------------------------------------
# dashboard
# ---------------------------------------------------------------------------

@app.command()
def dashboard(
    config: Optional[Path] = typer.Option(
        None,
        "--config",
        "-c",
        help="TOML analysis config. Relative paths inside it resolve from the config file.",
    ),
    org: Optional[str] = typer.Option(None, help="GitHub organization name."),
    data_dir: Optional[Path] = typer.Option(None, help="Directory containing generated CSVs."),
    cache_dir: Optional[Path] = typer.Option(None, help="Directory with cached JSON."),
    output: Optional[Path] = typer.Option(None, help="HTML dashboard output path."),
    summary: Optional[Path] = typer.Option(None, help="JSON summary output path."),
    title: Optional[str] = typer.Option(None, help="Dashboard title."),
    period_label: Optional[str] = typer.Option(None, help="Human-readable period label."),
    plan_minutes: Optional[float] = typer.Option(
        None,
        help="Optional monthly included-minutes cap for cap simulation.",
    ),
) -> None:
    """Build a static HTML dashboard from generated metric CSVs."""
    if config is not None:
        dashboard_config = load_dashboard_config(config)
        overrides = {}
        if data_dir is not None:
            overrides["data_dir"] = _resolve_cli_path(dashboard_config.root, data_dir)
        if cache_dir is not None:
            overrides["cache_dir"] = _resolve_cli_path(dashboard_config.root, cache_dir)
        if output is not None:
            overrides["output_html"] = _resolve_cli_path(dashboard_config.root, output)
        if summary is not None:
            overrides["summary_json"] = _resolve_cli_path(dashboard_config.root, summary)
        if title is not None:
            overrides["title"] = title
        if period_label is not None:
            overrides["period_label"] = period_label
        if plan_minutes is not None:
            overrides["plan_minutes"] = plan_minutes
        if overrides:
            dashboard_config = replace(dashboard_config, **overrides)
    else:
        if org is None:
            raise typer.BadParameter("--org is required when --config is not supplied")
        root = Path.cwd().resolve()
        resolved_data_dir = _resolve_cli_path(root, data_dir or Path("reports"))
        resolved_cache_dir = _resolve_cli_path(root, cache_dir or Path("cache"))
        dashboard_config = DashboardConfig(
            org=org,
            root=root,
            cache_dir=resolved_cache_dir,
            reports_dir=resolved_data_dir,
            data_dir=resolved_data_dir,
            output_html=_resolve_cli_path(root, output or Path("docs/index.html")),
            summary_json=_resolve_cli_path(root, summary or Path("docs/summary.json")),
            title=title or "GitHub Actions Usage Analysis",
            period_label=period_label or "selected period",
            plan_minutes=plan_minutes,
        )

    metadata = build_dashboard(dashboard_config)
    console.print(
        f"[green]Wrote[/green] {dashboard_config.output_html} "
        f"and {dashboard_config.summary_json}"
    )
    console.print(
        "[green]Summary[/green]: "
        f"{metadata['billed_equivalent_minutes_period']:,.0f} billed-equivalent minutes"
    )


# ---------------------------------------------------------------------------
# recreate
# ---------------------------------------------------------------------------

@app.command()
def recreate(
    config: Path = typer.Option(
        ...,
        "--config",
        "-c",
        help="TOML analysis config.",
    ),
    force: bool = typer.Option(False, "--force", help="Re-fetch even if cached data exists."),
    skip_fetch: bool = typer.Option(False, "--skip-fetch", help="Reuse cache and only regenerate outputs."),
    skip_dashboard: bool = typer.Option(False, "--skip-dashboard", help="Do not build the HTML dashboard."),
    metric: str = typer.Option("both", help="Which CSV metrics to generate: usage, performance, or both."),
) -> None:
    """Run the configured end-to-end analysis workflow."""
    dashboard_config = load_dashboard_config(config)
    since, until = _parse_period(dashboard_config.period)

    if not skip_fetch:
        console.print(
            f"[bold]Fetching[/bold] {dashboard_config.org} | "
            f"period: {since.date()} to {until.date()}"
        )
        repos = fetch_org(
            dashboard_config.org,
            dashboard_config.cache_dir,
            since=since,
            until=until,
            force=force,
        )
        console.print(f"[green]Done[/green]: fetched {len(repos)} repos")

        for related in dashboard_config.related_repositories:
            if not related.include_in_cache:
                continue
            console.print(f"[bold]Fetching related repo[/bold] {related.owner}/{related.repo}")
            fetch_repo(
                related.owner,
                related.repo,
                dashboard_config.cache_dir,
                since=since,
                until=until,
                force=force,
            )

    _write_reports(
        dashboard_config.org,
        dashboard_config.cache_dir,
        dashboard_config.reports_dir,
        metric,
    )

    if not skip_dashboard:
        build_dashboard(dashboard_config)
        console.print(
            f"[green]Wrote[/green] {dashboard_config.output_html} "
            f"and {dashboard_config.summary_json}"
        )


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
