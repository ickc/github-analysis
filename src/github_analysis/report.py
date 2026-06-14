"""Stage 3c — assemble the static HTML dashboard and JSON summary.

This module is a thin *assembler*: it loads the dataset, asks
:mod:`github_analysis.analysis` for the numbers, asks
:mod:`github_analysis.charts` for the figures, and stitches them into HTML. It
deliberately contains no metric maths of its own, so the report's content is
auditable in the analysis layer and only its *presentation* lives here.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from html import escape
from pathlib import Path
from typing import Any

import pandas as pd
from plotly.graph_objects import Figure
from plotly.io import to_html

from . import charts
from .analysis import (
    UsageSummary,
    billed_equivalent_by_os,
    monthly_usage,
    summarize_usage,
)
from .config import AnalysisConfig
from .dataset import ActionsDataset
from .metrics import performance_table, usage_table

__all__ = ["build_dashboard", "render_html", "summary_metadata"]

_TOP_REPOS = 12
_HEAVY_JOBS = 25


def _fig_html(fig: Figure, *, include_js: bool = False) -> str:
    return to_html(fig, include_plotlyjs="cdn" if include_js else False, full_html=False)


def _html_list(items: Sequence[str]) -> str:
    return "".join(f"<li>{item}</li>" for item in items)


def _relative(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


# ---------------------------------------------------------------------------
# Derived frames for the report
# ---------------------------------------------------------------------------


def _repo_share(usage_repos: pd.DataFrame, raw_total: float) -> pd.DataFrame:
    share = usage_repos.sort_values("Total minutes", ascending=False).copy()
    share["Share %"] = share["Total minutes"] / raw_total * 100 if raw_total else 0.0
    return share


def _workflow_billed(
    usage_workflows: pd.DataFrame, multipliers: Mapping[str, float]
) -> pd.DataFrame:
    cost = usage_workflows.groupby(
        ["Source repository", "Workflow", "Runtime OS"], as_index=False
    )["Total minutes"].sum()
    cost["Multiplier"] = (
        cost["Runtime OS"].astype(str).str.lower().map(multipliers).fillna(1.0)
    )
    cost["Billed equivalent minutes"] = cost["Total minutes"] * cost["Multiplier"]
    return (
        cost.groupby(["Source repository", "Workflow"], as_index=False)[
            "Billed equivalent minutes"
        ]
        .sum()
        .sort_values(
            ["Source repository", "Billed equivalent minutes"], ascending=[True, False]
        )
    )


def _monthly_aggregate(monthly: pd.DataFrame) -> pd.DataFrame:
    agg = (
        monthly.groupby("month")
        .agg(
            adj_billed=("adj_billed", "sum"),
            raw_billed=("billed_minutes", "sum"),
            runs=("is_failure", "count"),
            failures=("is_failure", "sum"),
        )
        .reset_index()
    )
    agg["failure_rate"] = agg["failures"] / agg["runs"] * 100
    return agg


# ---------------------------------------------------------------------------
# Text blocks
# ---------------------------------------------------------------------------


def _insights(summary: UsageSummary, config: AnalysisConfig) -> list[str]:
    items = [
        f"Raw exported usage over {escape(config.period_label)} is "
        f"<b>{summary.raw_total_minutes:,.0f} minutes</b>.",
        "After the configured OS billing multipliers, billed-equivalent usage is "
        f"<b>{summary.billed_equivalent_minutes:,.0f} minutes</b> for the same period.",
        "Estimated monthly billed demand is "
        f"<b>{summary.estimated_monthly_billed:,.0f} minutes/month</b> when the "
        "selected period is treated as an annual window.",
    ]
    if config.plan_minutes:
        items.append(
            f"Under the configured {config.plan_minutes:,.0f}-minute monthly cap: "
            f"{summary.cap_status_text}."
        )
    items.extend(config.extra_insights_html)
    return items


def _limitations(config: AnalysisConfig) -> list[str]:
    items = [
        "Headline KPI cards treat the selected period as an annual window to "
        "estimate monthly demand; a shorter or longer period scales accordingly.",
        "Monthly charts use job start timestamps from the local cache and may "
        "differ from GitHub UI exports due to per-job minute rounding and "
        "skipped or cancelled jobs.",
    ]
    if config.plan_minutes:
        items.append(
            "The monthly cap simulation uses annual-average demand; actual "
            "month-by-month billing may differ."
        )
    items.extend(config.extra_limitations_html)
    return items


def _cards(summary: UsageSummary, config: AnalysisConfig) -> list[tuple[str, str]]:
    cards = [
        ("Raw minutes", f"{summary.raw_total_minutes:,.0f}"),
        ("Billed-equivalent minutes", f"{summary.billed_equivalent_minutes:,.0f}"),
        ("Estimated monthly billed demand", f"{summary.estimated_monthly_billed:,.0f}"),
    ]
    if config.plan_minutes and summary.plan_pct is not None:
        cards.append(
            (f"Plan cap ({config.plan_minutes:,.0f} min/month)", f"{summary.plan_pct:.0f}% used avg")
        )
    return cards


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


def render_html(dataset: ActionsDataset, config: AnalysisConfig) -> tuple[str, dict[str, Any]]:
    """Render the dashboard HTML and return ``(html, summary_metadata)``."""
    multipliers = config.os_multipliers
    summary = summarize_usage(dataset, config)

    usage_repos = usage_table(dataset, "repositories")
    usage_workflows = usage_table(dataset, "workflows")
    usage_jobs = usage_table(dataset, "jobs")
    by_os = billed_equivalent_by_os(dataset, multipliers)
    perf_repos = performance_table(dataset, "repositories")
    perf_os = performance_table(dataset, "runtime-os")

    repo_share = _repo_share(usage_repos, summary.raw_total_minutes)
    top_repos = repo_share.head(_TOP_REPOS)
    heavy_jobs = usage_jobs.sort_values("Total minutes", ascending=False).head(_HEAVY_JOBS)
    workflow_billed = _workflow_billed(usage_workflows, multipliers)

    # -- period-level figures ----------------------------------------------
    fig_repo = charts.repo_minutes_bar(top_repos, config.period_label)
    fig_os = charts.os_raw_vs_billed_bar(by_os)
    fig_workflow = charts.workflow_explorer(workflow_billed)
    fig_perf = charts.reliability_scatter(perf_repos, config.period_label)
    fig_queue = charts.queue_by_os_bar(perf_os, config.period_label)

    cap_html = ""
    if config.plan_minutes:
        fig_cap = charts.cap_simulation(summary, config.days_per_month)
        cap_html = (
            "\n  <h2>Monthly cap simulation (annual-average demand)</h2>\n"
            '  <p class="muted">Based on annual-average demand, not individual months.</p>\n'
            f"  {_fig_html(fig_cap)}\n"
        )

    # -- monthly figures (derived from the tidy IR) ------------------------
    monthly = monthly_usage(dataset, multipliers)
    monthly_html = ""
    monthly_metadata: dict[str, Any] = {}
    if not monthly.empty:
        monthly_agg = _monthly_aggregate(monthly)
        months_sorted = sorted(monthly["month"].unique())
        fig_m_billed = charts.monthly_billed_by_os(monthly, multipliers, config.plan_minutes)
        fig_m_failure = charts.monthly_failure_rate(monthly_agg)
        fig_m_runs = charts.monthly_runs(monthly_agg)
        fig_m_last = charts.last_success_day(monthly)
        fig_m_repos = charts.monthly_repository_explorer(monthly, months_sorted)
        monthly_html = f"""
  <h2>Monthly trends (from job cache)</h2>
  <p class="muted">Charts below use raw job timestamps from the local cache.
  Billed-equivalent minutes apply the configured OS multipliers.</p>
  {_fig_html(fig_m_billed)}
  {_fig_html(fig_m_failure)}
  {_fig_html(fig_m_runs)}
  {_fig_html(fig_m_last)}

  <h2>Monthly repository breakdown</h2>
  <p class="muted">Select a month from the dropdown to see which repositories
  consumed the most billed-equivalent minutes in that period.</p>
  {_fig_html(fig_m_repos)}
"""
        monthly_metadata = {
            "months": months_sorted,
            "monthly_billed_equivalent_minutes": {
                row["month"]: float(row["adj_billed"]) for _, row in monthly_agg.iterrows()
            },
        }

    # -- tables ------------------------------------------------------------
    summary_table = top_repos[
        ["Source repository", "Total minutes", "Workflow runs", "Workflows", "Share %"]
    ].to_html(index=False, float_format=lambda v: f"{v:,.1f}")
    os_table = by_os[
        ["Runtime OS", "Total minutes", "Multiplier", "Billed equivalent minutes"]
    ].to_html(index=False)
    heavy_jobs_table = heavy_jobs[
        ["Source repository", "Workflow", "Job", "Total minutes", "Job runs", "Runner labels"]
    ].to_html(index=False)

    cards_html = "".join(
        f'<div class="card"><div class="muted">{escape(label)}</div>'
        f"<div><b>{value}</b></div></div>"
        for label, value in _cards(summary, config)
    )
    provenance = _relative(config.cache_dir / config.org, config.root)

    html = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{escape(config.title)}</title>
  <style>
    body {{ font-family: Inter, Arial, sans-serif; margin: 24px; max-width: 1200px; }}
    h1, h2 {{ margin-bottom: 8px; }}
    .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 12px; margin: 16px 0 24px; }}
    .card {{ border: 1px solid #ddd; border-radius: 8px; padding: 12px; background: #fafafa; }}
    .muted {{ color: #555; font-size: 0.9rem; }}
    table {{ border-collapse: collapse; width: 100%; margin: 8px 0 20px; font-size: 0.9rem; }}
    th, td {{ border: 1px solid #ddd; padding: 6px; text-align: left; }}
    th {{ background: #f2f2f2; }}
  </style>
</head>
<body>
  <h1>{escape(config.title)}</h1>
  <p class="muted">Generated from cached job data in <code>{escape(provenance)}</code>.</p>

  <div class="cards">
    {cards_html}
  </div>

  <h2>Key insights</h2>
  <ul>{_html_list(_insights(summary, config))}</ul>

  <h2>Important limitations</h2>
  <ul>{_html_list(_limitations(config))}</ul>

  <h2>Repository usage - {escape(config.period_label)}</h2>
  {_fig_html(fig_repo, include_js=True)}

  <h2>OS multiplier impact - {escape(config.period_label)}</h2>
  <p class="muted">Billed-equivalent minutes apply the configured OS multipliers.</p>
  {_fig_html(fig_os)}

  <h2>Workflow explorer - {escape(config.period_label)}</h2>
  {_fig_html(fig_workflow)}
{cap_html}{monthly_html}
  <h2>Reliability and performance - {escape(config.period_label)}</h2>
  {_fig_html(fig_perf)}
  {_fig_html(fig_queue)}

  <h2>Top repositories table - {escape(config.period_label)}</h2>
  {summary_table}

  <h2>OS multiplier table - {escape(config.period_label)}</h2>
  {os_table}

  <h2>Highest-cost jobs - {escape(config.period_label)}</h2>
  {heavy_jobs_table}
</body>
</html>
"""
    metadata = summary_metadata(summary, config, monthly_metadata)
    return html, metadata


def summary_metadata(
    summary: UsageSummary,
    config: AnalysisConfig,
    monthly_metadata: Mapping[str, Any],
) -> dict[str, Any]:
    """Machine-readable summary written alongside the HTML report."""
    return {
        "org": config.org,
        "period": config.period,
        "period_label": config.period_label,
        "raw_total_minutes_period": summary.raw_total_minutes,
        "billed_equivalent_minutes_period": summary.billed_equivalent_minutes,
        "plan_minutes": config.plan_minutes,
        "estimated_monthly_billed_minutes": summary.estimated_monthly_billed,
        "estimated_monthly_pct_of_cap": (
            round(summary.plan_pct, 1) if summary.plan_pct is not None else None
        ),
        "cap_reached_on_average_month": summary.cap_reached,
        "estimated_unserved_billed_minutes_month": summary.estimated_unserved_billed,
        "os_multipliers": dict(config.os_multipliers),
        "limitations": _limitations(config),
        **monthly_metadata,
    }


def build_dashboard(config: AnalysisConfig) -> dict[str, Any]:
    """Load the dataset, render the dashboard, and write HTML + JSON to disk.

    Returns the summary metadata.
    """
    dataset = ActionsDataset.from_cache(config.org, config.cache_dir)
    if dataset.is_empty:
        raise FileNotFoundError(
            f"No cached job data found under {config.cache_dir / config.org}. "
            "Run 'github-analysis fetch' first."
        )

    html, metadata = render_html(dataset, config)

    config.output_html.parent.mkdir(parents=True, exist_ok=True)
    config.output_html.write_text(html, encoding="utf-8")
    config.summary_json.parent.mkdir(parents=True, exist_ok=True)
    config.summary_json.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return metadata
