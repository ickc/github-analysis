"""Static dashboard builder for GitHub Actions metrics reports."""

from __future__ import annotations

import calendar
import json
import math
import os
import tomllib
from dataclasses import dataclass, field
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.io import to_html

DEFAULT_OS_MULTIPLIERS = {"linux": 1.0, "windows": 2.0, "macos": 10.0}
DEFAULT_DAYS_PER_MONTH = 30.44

_OS_KEYWORDS = {
    "linux": ["ubuntu", "linux", "debian"],
    "macos": ["macos", "mac-os", "osx"],
    "windows": ["windows", "win"],
}


@dataclass(frozen=True)
class DashboardConfig:
    """Configuration for building a static dashboard."""

    org: str
    root: Path
    cache_dir: Path
    reports_dir: Path
    data_dir: Path
    output_html: Path
    summary_json: Path
    period: str = "last-year"
    period_label: str = "selected period"
    title: str = "GitHub Actions Usage Analysis"
    plan_minutes: float | None = None
    days_per_month: float = DEFAULT_DAYS_PER_MONTH
    os_multipliers: dict[str, float] = field(default_factory=lambda: DEFAULT_OS_MULTIPLIERS.copy())
    extra_insights_html: list[str] = field(default_factory=list)
    extra_limitations_html: list[str] = field(default_factory=list)


def _resolve_path(root: Path, value: str | os.PathLike[str] | None, default: str) -> Path:
    raw = Path(value or default).expanduser()
    if raw.is_absolute():
        return raw
    return (root / raw).resolve()


def _section(data: dict[str, Any], name: str) -> dict[str, Any]:
    value = data.get(name, {})
    if not isinstance(value, dict):
        raise ValueError(f"[{name}] must be a table")
    return value


def load_dashboard_config(path: Path) -> DashboardConfig:
    """Load dashboard configuration from a TOML file.

    Relative paths are resolved from the directory containing the config file.
    The preferred TOML shape is:

        [analysis]
        org = "example-org"
        cache_dir = "cache"
        reports_dir = "reports"
        output_html = "docs/index.html"
    """

    path = path.resolve()
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    analysis = _section(data, "analysis")
    dashboard = _section(data, "dashboard")
    root = _resolve_path(path.parent, analysis.get("root"), ".")

    org = analysis.get("org")
    if not org:
        raise ValueError("Config must set [analysis].org")

    reports_dir = _resolve_path(root, analysis.get("reports_dir"), "reports")
    data_dir = _resolve_path(root, analysis.get("data_dir"), str(reports_dir))

    os_multipliers = DEFAULT_OS_MULTIPLIERS.copy()
    for key, value in _section(data, "os_multipliers").items():
        os_multipliers[str(key).lower()] = float(value)

    return DashboardConfig(
        org=str(org),
        root=root,
        cache_dir=_resolve_path(root, analysis.get("cache_dir"), "cache"),
        reports_dir=reports_dir,
        data_dir=data_dir,
        output_html=_resolve_path(root, analysis.get("output_html"), "docs/index.html"),
        summary_json=_resolve_path(root, analysis.get("summary_json"), "docs/summary.json"),
        period=str(analysis.get("period", "last-year")),
        period_label=str(dashboard.get("period_label", analysis.get("period", "selected period"))),
        title=str(dashboard.get("title", "GitHub Actions Usage Analysis")),
        plan_minutes=(
            float(dashboard["plan_minutes"]) if dashboard.get("plan_minutes") is not None else None
        ),
        days_per_month=float(dashboard.get("days_per_month", DEFAULT_DAYS_PER_MONTH)),
        os_multipliers=os_multipliers,
        extra_insights_html=[str(item) for item in dashboard.get("extra_insights_html", [])],
        extra_limitations_html=[str(item) for item in dashboard.get("extra_limitations_html", [])],
    )


def _clean_csv_cell(value: object) -> object:
    if not isinstance(value, str):
        return value
    cleaned = value.strip()
    while cleaned and cleaned[0] in {"'", '"'}:
        cleaned = cleaned[1:].strip()
    while cleaned and cleaned[-1] in {"'", '"'}:
        cleaned = cleaned[:-1].strip()
    return cleaned


def clean_frame(path: Path) -> pd.DataFrame:
    """Read a GitHub metrics CSV and normalize UI-export quoting artifacts."""

    df = pd.read_csv(path)
    df.columns = [str(_clean_csv_cell(col)) for col in df.columns]
    for col in df.columns:
        if pd.api.types.is_string_dtype(df[col]):
            df[col] = df[col].map(_clean_csv_cell)
    return df


def _labels_to_os(labels: list[str]) -> str:
    flat = " ".join(labels).lower()
    for os_name, keywords in _OS_KEYWORDS.items():
        if any(keyword in flat for keyword in keywords):
            return os_name
    return "unknown"


def _runner_type(runner_group_name: str | None) -> str:
    if runner_group_name and runner_group_name.lower() in ("github actions", "default"):
        return "hosted"
    return "self-hosted"


def _relative(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _numeric_columns(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.copy()
    for col in frame.columns:
        if any(token in col.lower() for token in ["minutes", "runs", "rate", "time", "jobs"]):
            converted = pd.to_numeric(frame[col], errors="coerce")
            if converted.notna().any():
                frame[col] = converted.fillna(frame[col])
    return frame


def _job_files(org: str, cache_dir: Path) -> list[tuple[Path, str]]:
    return [
        (path, path.parent.parent.name)
        for path in (cache_dir / org).glob("*/jobs/*.json")
    ]


def load_hosted_jobs_from_cache(config: DashboardConfig) -> pd.DataFrame:
    """Load completed GitHub-hosted jobs from cache for monthly charts."""

    rows: list[dict[str, Any]] = []
    for jobs_file, repo in _job_files(config.org, config.cache_dir):
        try:
            jobs = json.loads(jobs_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(jobs, list):
            continue
        for job in jobs:
            if job.get("status") != "completed":
                continue
            started_at = job.get("started_at", "")
            completed_at = job.get("completed_at", "")
            created_at = job.get("created_at", "")
            if not started_at or not completed_at:
                continue
            if _runner_type(job.get("runner_group_name")) != "hosted":
                continue

            start = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
            end = datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
            run_ms = (end - start).total_seconds() * 1000
            billed = math.ceil(run_ms / 60_000) if run_ms > 0 else 0
            queue_ms = None
            if created_at:
                created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                queue_ms = max(0.0, (start - created).total_seconds() * 1000)

            runtime_os = _labels_to_os(job.get("labels") or [])
            multiplier = config.os_multipliers.get(runtime_os, 1.0)

            rows.append(
                {
                    "repo": repo,
                    "started_at": started_at,
                    "month": started_at[:7],
                    "os": runtime_os,
                    "billed_minutes": billed,
                    "adj_billed": billed * multiplier,
                    "billing_org": config.org,
                    "run_ms": run_ms,
                    "queue_ms": queue_ms,
                    "conclusion": job.get("conclusion", ""),
                    "workflow_name": job.get("workflow_name", ""),
                }
            )

    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["is_failure"] = (df["conclusion"] == "failure").astype(int)
    return df


def _read_metric_tables(data_dir: Path) -> dict[str, pd.DataFrame]:
    paths = {
        "usage_repos": data_dir / "actions-usage-metrics" / "repositories.csv",
        "usage_workflows": data_dir / "actions-usage-metrics" / "workflows.csv",
        "usage_jobs": data_dir / "actions-usage-metrics" / "jobs.csv",
        "usage_os": data_dir / "actions-usage-metrics" / "runtime-os.csv",
        "perf_repos": data_dir / "actions-performance-metrics" / "repositories.csv",
        "perf_os": data_dir / "actions-performance-metrics" / "runtime-os.csv",
    }
    missing = [path for path in paths.values() if not path.exists()]
    if missing:
        formatted = "\n".join(f"  - {path}" for path in missing)
        raise FileNotFoundError(f"Required metrics CSVs are missing:\n{formatted}")
    return {name: _numeric_columns(clean_frame(path)) for name, path in paths.items()}


def _month_length(ym: str) -> int:
    year, month = int(ym[:4]), int(ym[5:7])
    return calendar.monthrange(year, month)[1]


def _build_monthly_charts(
    cache_jobs: pd.DataFrame,
    os_multipliers: dict[str, float],
    plan_minutes: float | None,
) -> tuple[str, dict[str, Any]]:
    months_sorted = sorted(cache_jobs["month"].unique())
    monthly_agg = (
        cache_jobs.groupby("month")
        .agg(
            adj_billed=("adj_billed", "sum"),
            raw_billed=("billed_minutes", "sum"),
            runs=("is_failure", "count"),
            failures=("is_failure", "sum"),
        )
        .reset_index()
    )
    monthly_agg["failure_rate"] = monthly_agg["failures"] / monthly_agg["runs"] * 100
    monthly_os = cache_jobs.groupby(["month", "os"])["adj_billed"].sum().reset_index()
    monthly_repos = (
        cache_jobs.groupby(["month", "repo"])["adj_billed"]
        .sum()
        .reset_index()
        .sort_values(["month", "adj_billed"], ascending=[True, False])
    )

    os_order = ["linux", "windows", "macos", "unknown"]
    os_colors = {
        "linux": "#4C78A8",
        "windows": "#72B7B2",
        "macos": "#F58518",
        "unknown": "#BAB0AC",
    }
    fig_monthly_billed = go.Figure()
    for runtime_os in os_order:
        sub = monthly_os[monthly_os["os"] == runtime_os]
        if sub.empty or sub["adj_billed"].sum() == 0:
            continue
        multiplier = os_multipliers.get(runtime_os, 1.0)
        fig_monthly_billed.add_trace(
            go.Bar(
                x=sub["month"],
                y=sub["adj_billed"],
                name=f"{runtime_os} (x{multiplier:g})",
                marker_color=os_colors.get(runtime_os),
            )
        )
    if plan_minutes:
        fig_monthly_billed.add_hline(
            y=plan_minutes,
            line_dash="dash",
            line_color="red",
            annotation_text=f"{plan_minutes:,.0f}-minute plan cap",
            annotation_position="top left",
        )
    fig_monthly_billed.update_layout(
        barmode="stack",
        title="Monthly billed-equivalent minutes by OS",
        xaxis_title="Month",
        yaxis_title="Billed-equivalent minutes",
        legend_title="Runtime OS",
    )

    fig_monthly_failure = px.line(
        monthly_agg,
        x="month",
        y="failure_rate",
        markers=True,
        title="Monthly job failure rate (%)",
        labels={"failure_rate": "Failure rate (%)", "month": "Month"},
    )
    fig_monthly_failure.update_traces(line_color="#E45756")

    fig_monthly_runs = px.bar(
        monthly_agg,
        x="month",
        y="runs",
        title="Monthly job runs (completed, hosted runners)",
        labels={"runs": "Job runs", "month": "Month"},
        color_discrete_sequence=["#4C78A8"],
    )

    success_jobs = cache_jobs[cache_jobs["conclusion"] == "success"].copy()
    success_jobs["day"] = pd.to_datetime(success_jobs["started_at"]).dt.day
    last_success = (
        success_jobs.groupby("month")["day"]
        .max()
        .reset_index()
        .rename(columns={"day": "last_success_day"})
    )
    cache_jobs = cache_jobs.copy()
    cache_jobs["day"] = pd.to_datetime(cache_jobs["started_at"]).dt.day
    last_any = (
        cache_jobs.groupby("month")["day"]
        .max()
        .reset_index()
        .rename(columns={"day": "last_any_day"})
    )
    last_ci = last_success.merge(last_any, on="month", how="outer")
    last_ci["month_length"] = last_ci["month"].apply(_month_length)

    fig_last_success = go.Figure()
    fig_last_success.add_trace(
        go.Scatter(
            x=last_ci["month"],
            y=last_ci["last_any_day"],
            mode="markers",
            name="Last day any job ran",
            marker=dict(symbol="circle-open", size=10, color="#AECDE8"),
        )
    )
    fig_last_success.add_trace(
        go.Scatter(
            x=last_ci["month"],
            y=last_ci["last_success_day"],
            mode="markers+lines",
            name="Last day a job succeeded",
            marker=dict(size=10, color="#4C78A8"),
            line=dict(color="#4C78A8"),
        )
    )
    fig_last_success.add_trace(
        go.Scatter(
            x=last_ci["month"],
            y=last_ci["month_length"],
            mode="lines",
            name="Month length",
            line=dict(color="#BAB0AC", dash="dot"),
        )
    )
    fig_last_success.update_layout(
        title="Last day of month with a successful CI job",
        xaxis_title="Month",
        yaxis_title="Day of month",
        yaxis=dict(range=[0, 32]),
    )

    top_repo_names = (
        cache_jobs.groupby("repo")["adj_billed"]
        .sum()
        .sort_values(ascending=False)
        .head(10)
        .index.tolist()
    )
    monthly_repos_plot = monthly_repos.copy()
    monthly_repos_plot["repo_label"] = monthly_repos_plot["repo"].where(
        monthly_repos_plot["repo"].isin(top_repo_names), other="other"
    )
    monthly_repos_plot = (
        monthly_repos_plot.groupby(["month", "repo_label"])["adj_billed"]
        .sum()
        .reset_index()
        .sort_values(["month", "adj_billed"], ascending=[True, False])
    )

    fig_monthly_repos = go.Figure()
    repo_buttons_monthly = []
    for idx, month in enumerate(months_sorted):
        sub = monthly_repos_plot[monthly_repos_plot["month"] == month].sort_values(
            "adj_billed", ascending=False
        )
        fig_monthly_repos.add_trace(
            go.Bar(
                x=sub["repo_label"],
                y=sub["adj_billed"],
                name=month,
                visible=(idx == 0),
                marker_color=px.colors.qualitative.Plotly[: len(sub)],
            )
        )
        visible = [False] * len(months_sorted)
        visible[idx] = True
        repo_buttons_monthly.append(
            {
                "label": month,
                "method": "update",
                "args": [
                    {"visible": visible},
                    {"title": f"Top repositories by billed-equivalent minutes - {month}"},
                ],
            }
        )

    fig_monthly_repos.update_layout(
        title=f"Top repositories by billed-equivalent minutes - {months_sorted[0]}",
        updatemenus=[
            {
                "buttons": repo_buttons_monthly,
                "x": 1.02,
                "y": 1.0,
                "xanchor": "left",
                "type": "dropdown",
            }
        ],
        xaxis_title="Repository",
        yaxis_title="Billed-equivalent minutes",
        showlegend=False,
    )

    html = f"""
  <h2>Monthly trends (from job cache)</h2>
  <p class="muted">Charts below use raw job timestamps from the local cache.
  Billed-equivalent minutes apply the configured OS multipliers.</p>

  {to_html(fig_monthly_billed, include_plotlyjs=False, full_html=False)}
  {to_html(fig_monthly_failure, include_plotlyjs=False, full_html=False)}
  {to_html(fig_monthly_runs, include_plotlyjs=False, full_html=False)}
  {to_html(fig_last_success, include_plotlyjs=False, full_html=False)}

  <h2>Monthly repository breakdown</h2>
  <p class="muted">Select a month from the dropdown to see which repositories consumed
  the most billed-equivalent minutes in that period.</p>
  {to_html(fig_monthly_repos, include_plotlyjs=False, full_html=False)}
"""
    return html, {
        "months": months_sorted,
        "monthly_billed_equivalent_minutes": {
            row["month"]: float(row["adj_billed"]) for _, row in monthly_agg.iterrows()
        },
    }


def _html_list(items: list[str]) -> str:
    return "".join(f"<li>{item}</li>" for item in items)


def build_dashboard(config: DashboardConfig) -> dict[str, Any]:
    """Build the configured dashboard and return the summary metadata."""

    data_dir = config.data_dir
    if not (data_dir / "actions-usage-metrics").exists():
        raise FileNotFoundError(f"{data_dir / 'actions-usage-metrics'} not found")

    tables = _read_metric_tables(data_dir)
    usage_repos = tables["usage_repos"]
    usage_workflows = tables["usage_workflows"]
    usage_jobs = tables["usage_jobs"]
    usage_os = tables["usage_os"].copy()
    perf_repos = tables["perf_repos"]
    perf_os = tables["perf_os"]

    minutes_total_raw = float(usage_repos["Total minutes"].sum())
    usage_os["Multiplier"] = (
        usage_os["Runtime OS"].astype(str).str.lower().map(config.os_multipliers).fillna(1.0)
    )
    usage_os["Billed equivalent minutes"] = usage_os["Total minutes"] * usage_os["Multiplier"]
    minutes_total_billed = float(usage_os["Billed equivalent minutes"].sum())

    est_monthly_billed = minutes_total_billed / 12
    est_daily_billed = est_monthly_billed / config.days_per_month
    plan_pct = None
    est_unserved_billed = 0.0
    cap_status_text = ""
    if config.plan_minutes:
        plan_pct = est_monthly_billed / config.plan_minutes * 100
        if est_monthly_billed > config.plan_minutes:
            est_day_cap_hit = config.plan_minutes / est_daily_billed if est_daily_billed else 0
            est_last_day_actions = min(config.days_per_month, est_day_cap_hit)
            est_unserved_billed = est_monthly_billed - config.plan_minutes
            cap_status_text = (
                f"cap hit around day <b>{est_last_day_actions:.1f}</b> of an average month, "
                f"leaving <b>{est_unserved_billed:,.0f}</b> billed-equivalent minutes unmet"
            )
        else:
            cap_status_text = (
                f"cap <b>not reached</b> in an average month "
                f"(average usage is {plan_pct:.0f}% of the {config.plan_minutes:,.0f}-minute cap)"
            )

    cache_jobs = load_hosted_jobs_from_cache(config)
    has_cache = not cache_jobs.empty
    monthly_section_html = ""
    monthly_metadata: dict[str, Any] = {}
    if has_cache:
        monthly_section_html, monthly_metadata = _build_monthly_charts(
            cache_jobs, config.os_multipliers, config.plan_minutes
        )

    repo_share = usage_repos.sort_values("Total minutes", ascending=False).copy()
    repo_share["Share %"] = repo_share["Total minutes"] / minutes_total_raw * 100
    top_repos = repo_share.head(12)

    workflow_cost = (
        usage_workflows.groupby(
            ["Source repository", "Workflow", "Runtime OS"], as_index=False
        )["Total minutes"].sum()
    )
    workflow_cost["Multiplier"] = (
        workflow_cost["Runtime OS"].astype(str).str.lower().map(config.os_multipliers).fillna(1.0)
    )
    workflow_cost["Billed equivalent minutes"] = (
        workflow_cost["Total minutes"] * workflow_cost["Multiplier"]
    )
    heavy_jobs = usage_jobs.sort_values("Total minutes", ascending=False).head(25).copy()

    perf_plot_df = perf_repos.copy()
    perf_plot_df["Avg job run time (min)"] = perf_plot_df["Avg job run time"] / 60_000
    perf_plot_df["Avg job queue time (min)"] = perf_plot_df["Avg job queue time"] / 60_000

    fig_repo_minutes = px.bar(
        top_repos,
        x="Source repository",
        y="Total minutes",
        color="Total minutes",
        color_continuous_scale="Blues",
        title=f"Top repositories by raw Actions minutes ({config.period_label})",
        hover_data={"Share %": ":.1f"},
    )

    fig_os_raw_billed = go.Figure()
    fig_os_raw_billed.add_trace(
        go.Bar(name="Raw minutes", x=usage_os["Runtime OS"], y=usage_os["Total minutes"])
    )
    fig_os_raw_billed.add_trace(
        go.Bar(
            name="Billed-equivalent minutes",
            x=usage_os["Runtime OS"],
            y=usage_os["Billed equivalent minutes"],
        )
    )
    fig_os_raw_billed.update_layout(
        barmode="group",
        title="Raw vs multiplier-adjusted minutes by OS",
        xaxis_title="Runtime OS",
        yaxis_title="Minutes",
    )

    fig_perf = px.scatter(
        perf_plot_df,
        x="Failure rate",
        y="Avg job run time (min)",
        size="Job runs",
        color="Avg job queue time (min)",
        hover_name="Source repository",
        title=f"Reliability vs runtime by repository ({config.period_label})",
        labels={"Failure rate": "Failure rate (%)"},
    )

    fig_queue_os = px.bar(
        perf_os.assign(**{"Avg queue time (min)": perf_os["Avg job queue time"] / 60_000}),
        x="Runtime OS",
        y="Avg queue time (min)",
        color="Runtime OS",
        title=f"Average queue time by OS ({config.period_label})",
    )

    workflow_billed = (
        workflow_cost.groupby(["Source repository", "Workflow"], as_index=False)[
            "Billed equivalent minutes"
        ]
        .sum()
        .sort_values(["Source repository", "Billed equivalent minutes"], ascending=[True, False])
    )
    repos = sorted(workflow_billed["Source repository"].unique())
    fig_workflow = go.Figure()
    workflow_buttons = []
    for idx, repo in enumerate(repos):
        sub = workflow_billed[workflow_billed["Source repository"] == repo]
        fig_workflow.add_trace(
            go.Bar(
                x=sub["Workflow"],
                y=sub["Billed equivalent minutes"],
                name=repo,
                visible=(idx == 0),
            )
        )
        visible = [False] * len(repos)
        visible[idx] = True
        workflow_buttons.append(
            {
                "label": repo,
                "method": "update",
                "args": [
                    {"visible": visible},
                    {"title": f"Workflow billed-equivalent minutes - {repo}"},
                ],
            }
        )
    fig_workflow.update_layout(
        title=f"Workflow billed-equivalent minutes - {repos[0] if repos else 'N/A'}",
        updatemenus=[{"buttons": workflow_buttons, "x": 1.02, "y": 1.0, "xanchor": "left"}],
        xaxis_title="Workflow",
        yaxis_title="Billed-equivalent minutes",
        showlegend=False,
    )

    cap_chart_html = ""
    if config.plan_minutes:
        days = pd.DataFrame({"Day": range(1, 32)})
        days["Projected billed usage"] = days["Day"] * est_daily_billed
        days["Usable under plan cap"] = days["Projected billed usage"].clip(
            upper=config.plan_minutes
        )
        fig_cap = go.Figure()
        fig_cap.add_trace(
            go.Scatter(
                x=days["Day"],
                y=days["Projected billed usage"],
                mode="lines",
                name="Projected demand",
            )
        )
        fig_cap.add_trace(
            go.Scatter(
                x=days["Day"],
                y=days["Usable under plan cap"],
                mode="lines",
                name="Usable before cap",
            )
        )
        fig_cap.add_hline(
            y=config.plan_minutes,
            line_dash="dash",
            line_color="red",
            annotation_text=f"{config.plan_minutes:,.0f}-minute cap",
        )
        fig_cap.update_layout(
            title="Monthly cap simulation from annual average",
            xaxis_title="Day of month",
            yaxis_title="Billed-equivalent minutes",
        )
        cap_chart_html = f"""
  <h2>Monthly cap simulation (annual-average demand)</h2>
  <p class="muted">Based on annual-average demand, not individual months.</p>
  {to_html(fig_cap, include_plotlyjs=False, full_html=False)}
"""

    summary_table = top_repos[
        ["Source repository", "Total minutes", "Workflow runs", "Workflows", "Share %"]
    ].to_html(index=False, float_format=lambda value: f"{value:,.1f}")
    os_table = usage_os[
        ["Runtime OS", "Total minutes", "Multiplier", "Billed equivalent minutes"]
    ].to_html(index=False)
    heavy_jobs_table = heavy_jobs[
        ["Source repository", "Workflow", "Job", "Total minutes", "Job runs", "Runner labels"]
    ].to_html(index=False)

    insights = [
        f"Raw exported usage over {escape(config.period_label)} is "
        f"<b>{minutes_total_raw:,.0f} minutes</b>.",
        "After the configured OS billing multipliers, billed-equivalent usage is "
        f"<b>{minutes_total_billed:,.0f} minutes</b> for the same period.",
        "Estimated monthly billed demand is "
        f"<b>{est_monthly_billed:,.0f} minutes/month</b> when the selected period is "
        "treated as an annual window.",
    ]
    if config.plan_minutes:
        insights.append(
            f"Under the configured {config.plan_minutes:,.0f}-minute monthly cap: "
            f"{cap_status_text}."
        )

    insights.extend(config.extra_insights_html)

    limitations = [
        "Aggregate CSV exports do not contain per-run dates; KPI cards reflect the full selected period.",
        "Monthly charts use raw job cache timestamps and may differ from GitHub UI exports due to rounding and skipped or cancelled jobs.",
    ]
    if config.plan_minutes:
        limitations.append(
            "The monthly cap simulation uses annual-average demand; actual month-by-month billing may differ."
        )
    limitations.extend(config.extra_limitations_html)

    cards = [
        ("Raw minutes", f"{minutes_total_raw:,.0f}"),
        ("Billed-equivalent minutes", f"{minutes_total_billed:,.0f}"),
        ("Estimated monthly billed demand", f"{est_monthly_billed:,.0f}"),
    ]
    if config.plan_minutes and plan_pct is not None:
        cards.append((f"Plan cap ({config.plan_minutes:,.0f} min/month)", f"{plan_pct:.0f}% used avg"))
    cards_html = "".join(
        f'<div class="card"><div class="muted">{escape(label)}</div><div><b>{value}</b></div></div>'
        for label, value in cards
    )

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
  <p class="muted">Generated from <code>{escape(_relative(data_dir, config.root))}/actions-usage-metrics</code>
  plus <code>{escape(_relative(config.cache_dir / config.org, config.root))}</code> when cached job data is available.</p>

  <div class="cards">
    {cards_html}
  </div>

  <h2>Key insights</h2>
  <ul>{_html_list(insights)}</ul>

  <h2>Important limitations</h2>
  <ul>{_html_list(limitations)}</ul>

  <h2>Repository usage - {escape(config.period_label)}</h2>
  {to_html(fig_repo_minutes, include_plotlyjs="cdn", full_html=False)}

  <h2>OS multiplier impact - {escape(config.period_label)}</h2>
  <p class="muted">Billed-equivalent minutes apply the configured OS multipliers.</p>
  {to_html(fig_os_raw_billed, include_plotlyjs=False, full_html=False)}

  <h2>Workflow explorer - {escape(config.period_label)}</h2>
  {to_html(fig_workflow, include_plotlyjs=False, full_html=False)}

{cap_chart_html}
{monthly_section_html}

  <h2>Reliability and performance - {escape(config.period_label)}</h2>
  {to_html(fig_perf, include_plotlyjs=False, full_html=False)}
  {to_html(fig_queue_os, include_plotlyjs=False, full_html=False)}

  <h2>Top repositories table - {escape(config.period_label)}</h2>
  {summary_table}

  <h2>OS multiplier table - {escape(config.period_label)}</h2>
  {os_table}

  <h2>Highest-cost jobs - {escape(config.period_label)}</h2>
  {heavy_jobs_table}
</body>
</html>
"""

    config.output_html.parent.mkdir(parents=True, exist_ok=True)
    config.output_html.write_text(html, encoding="utf-8")

    metadata = {
        "org": config.org,
        "period": config.period,
        "period_label": config.period_label,
        "raw_total_minutes_period": minutes_total_raw,
        "billed_equivalent_minutes_period": minutes_total_billed,
        "plan_minutes": config.plan_minutes,
        "estimated_monthly_billed_minutes": est_monthly_billed,
        "estimated_monthly_pct_of_cap": round(plan_pct, 1) if plan_pct is not None else None,
        "cap_reached_on_average_month": (
            est_monthly_billed > config.plan_minutes if config.plan_minutes else None
        ),
        "estimated_unserved_billed_minutes_month": est_unserved_billed,
        "os_multipliers": config.os_multipliers,
        "limitations": limitations,
        **monthly_metadata,
    }
    config.summary_json.parent.mkdir(parents=True, exist_ok=True)
    config.summary_json.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return metadata
