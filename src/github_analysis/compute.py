"""Compute usage and performance metrics from raw cached job/run data.

Usage metrics  (mirrors GitHub's Actions > Metrics > Usage page):
  - Total minutes consumed, grouped by workflow / job / repo / OS / runner type
  - "Total minutes" = sum of ceil(job_duration_ms / 60000) per job run

Performance metrics (mirrors GitHub's Actions > Metrics > Performance page):
  - Failure rates, avg run times, avg queue times
  - Failure = job conclusion == 'failure'
  - Avg run time in milliseconds (job: completed_at - started_at)
  - Avg queue time in milliseconds (job: started_at - created_at)
  - Workflow-level avg run time = wall-clock of the whole run
    (max(job.completed_at) - min(job.started_at)) per run, then averaged

Note: Computed values closely approximate GitHub's UI but may differ slightly
due to minimum-billing rounding per job (GitHub floors short jobs differently)
and how GitHub handles cancelled/skipped jobs.
"""

from __future__ import annotations

import math
from datetime import datetime
from pathlib import Path

import pandas as pd

from .raw import load_jobs, load_runs


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_OS_KEYWORDS = {
    "linux": ["ubuntu", "linux", "debian"],
    "macos": ["macos", "mac-os", "osx"],
    "windows": ["windows", "win"],
}


def _labels_to_os(labels: list[str]) -> str:
    flat = " ".join(labels).lower()
    for os_name, keywords in _OS_KEYWORDS.items():
        if any(kw in flat for kw in keywords):
            return os_name
    return "unknown"


def _runner_type(runner_group_name: str | None) -> str:
    if runner_group_name and runner_group_name.lower() in ("github actions", "default"):
        return "hosted"
    return "self-hosted"


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _duration_ms(start: str | None, end: str | None) -> float | None:
    s, e = _parse_dt(start), _parse_dt(end)
    if s is None or e is None:
        return None
    return (e - s).total_seconds() * 1000


# ---------------------------------------------------------------------------
# Build DataFrames from raw data
# ---------------------------------------------------------------------------

def _jobs_df(org: str, cache_dir: Path) -> pd.DataFrame:
    """Flat DataFrame of completed cached jobs with derived columns."""
    rows = [j for j in load_jobs(org, cache_dir) if j.get("status") == "completed"]
    if not rows:
        return pd.DataFrame()

    records = []
    for j in rows:
        run_ms = _duration_ms(j.get("started_at"), j.get("completed_at"))
        queue_ms = _duration_ms(j.get("created_at"), j.get("started_at"))
        records.append(
            {
                "job_id": j["id"],
                "run_id": j["run_id"],
                "repo": j["_repo"],
                "job_name": j["name"],
                "workflow_name": j.get("workflow_name", ""),
                "conclusion": j.get("conclusion") or "",
                "runner_type": _runner_type(j.get("runner_group_name")),
                "runtime_os": _labels_to_os(j.get("labels") or []),
                "runner_labels": ",".join(sorted(j.get("labels") or [])),
                "run_ms": run_ms,
                "queue_ms": queue_ms,
                # billing: round up to nearest minute (min 1 min per job)
                "billed_minutes": math.ceil(run_ms / 60_000) if run_ms and run_ms > 0 else 0,
                "is_failure": 1 if j.get("conclusion") == "failure" else 0,
                "started_at": j.get("started_at"),
                "completed_at": j.get("completed_at"),
            }
        )

    df = pd.DataFrame.from_records(records)
    # Attach workflow path from runs cache for the workflow column
    runs_df = _runs_df(org, cache_dir)[["run_id", "workflow_path"]].drop_duplicates()
    df = df.merge(runs_df, on="run_id", how="left")
    return df


def _runs_df(org: str, cache_dir: Path) -> pd.DataFrame:
    """Flat DataFrame of all cached workflow runs."""
    rows = load_runs(org, cache_dir)
    if not rows:
        return pd.DataFrame()

    records = [
        {
            "run_id": r["id"],
            "repo": r["_repo"],
            "workflow_path": r.get("path", ""),
            "conclusion": r.get("conclusion") or "",
            "created_at": r.get("created_at"),
            "run_started_at": r.get("run_started_at"),
            "updated_at": r.get("updated_at"),
        }
        for r in rows
    ]
    df = pd.DataFrame.from_records(records)
    # Workflow-level duration: updated_at - run_started_at
    df["run_duration_ms"] = df.apply(
        lambda row: _duration_ms(row["run_started_at"], row["updated_at"]), axis=1
    )
    return df


# ---------------------------------------------------------------------------
# Usage metrics
# ---------------------------------------------------------------------------

def usage_workflows(org: str, cache_dir: Path) -> pd.DataFrame:
    """Per-workflow usage, grouped by (workflow_path, repo, runner_type, runtime_os)."""
    df = _jobs_df(org, cache_dir)
    if df.empty:
        return pd.DataFrame()

    g = df.groupby(["workflow_path", "repo", "runner_type", "runtime_os"])
    result = pd.DataFrame(
        {
            "Total minutes": g["billed_minutes"].sum(),
            "Workflow runs": g["run_id"].nunique(),
            "Jobs": g["job_name"].nunique(),
        }
    ).reset_index()
    result.columns = ["Workflow", "Source repository", "Runner type", "Runtime OS",
                       "Total minutes", "Workflow runs", "Jobs"]
    return result.sort_values("Total minutes", ascending=False).reset_index(drop=True)


def usage_jobs(org: str, cache_dir: Path) -> pd.DataFrame:
    """Per-job usage, grouped by (job_name, workflow_path, repo, runner_type, runner_labels)."""
    df = _jobs_df(org, cache_dir)
    if df.empty:
        return pd.DataFrame()

    g = df.groupby(["job_name", "workflow_path", "repo", "runner_type", "runner_labels"])
    result = pd.DataFrame(
        {
            "Total minutes": g["billed_minutes"].sum(),
            "Job runs": g["job_id"].count(),
        }
    ).reset_index()
    result.columns = ["Job", "Workflow", "Source repository", "Runner type", "Runner labels",
                       "Total minutes", "Job runs"]
    return result.sort_values("Total minutes", ascending=False).reset_index(drop=True)


def usage_repositories(org: str, cache_dir: Path) -> pd.DataFrame:
    """Per-repository usage."""
    df = _jobs_df(org, cache_dir)
    if df.empty:
        return pd.DataFrame()

    g = df.groupby("repo")
    result = pd.DataFrame(
        {
            "Total minutes": g["billed_minutes"].sum(),
            "Workflow runs": g["run_id"].nunique(),
            "Workflows": g["workflow_path"].nunique(),
        }
    ).reset_index()
    result.columns = ["Source repository", "Total minutes", "Workflow runs", "Workflows"]
    return result.sort_values("Total minutes", ascending=False).reset_index(drop=True)


def usage_runtime_os(org: str, cache_dir: Path) -> pd.DataFrame:
    """Per-OS usage."""
    df = _jobs_df(org, cache_dir)
    if df.empty:
        return pd.DataFrame()

    g = df.groupby("runtime_os")
    result = pd.DataFrame(
        {
            "Total minutes": g["billed_minutes"].sum(),
            "Workflow runs": g["run_id"].nunique(),
            "Workflows": g["workflow_path"].nunique(),
        }
    ).reset_index()
    result.columns = ["Runtime OS", "Total minutes", "Workflow runs", "Workflows"]
    return result.sort_values("Total minutes", ascending=False).reset_index(drop=True)


def usage_runner_type(org: str, cache_dir: Path) -> pd.DataFrame:
    """Per-runner-type usage."""
    df = _jobs_df(org, cache_dir)
    if df.empty:
        return pd.DataFrame()

    g = df.groupby("runner_type")
    result = pd.DataFrame(
        {
            "Total minutes": g["billed_minutes"].sum(),
            "Workflow runs": g["run_id"].nunique(),
            "Workflows": g["workflow_path"].nunique(),
        }
    ).reset_index()
    result.columns = ["Runner type", "Total minutes", "Workflow runs", "Workflows"]
    return result.sort_values("Total minutes", ascending=False).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Performance metrics
# ---------------------------------------------------------------------------

def _workflow_run_durations(org: str, cache_dir: Path) -> pd.DataFrame:
    """Per-run wall-clock duration and failure flag, using run-level timestamps.

    Duration = updated_at - run_started_at (mirrors the GitHub UI calculation).
    has_failure = 1 if any job in the run concluded as 'failure'.
    Only completed runs are included.
    """
    runs = _runs_df(org, cache_dir)
    if runs.empty:
        return pd.DataFrame()

    jobs = _jobs_df(org, cache_dir)
    failed_runs = set()
    if not jobs.empty:
        failed_runs = set(jobs.loc[jobs["is_failure"] == 1, "run_id"])

    completed = runs[runs["conclusion"].notna() & (runs["conclusion"] != "")].copy()
    completed["has_failure"] = completed["run_id"].isin(failed_runs).astype(int)
    return completed[["run_id", "repo", "workflow_path", "run_duration_ms", "has_failure"]].rename(
        columns={"run_duration_ms": "wall_ms"}
    )


def perf_workflows(org: str, cache_dir: Path) -> pd.DataFrame:
    """Per-workflow performance metrics."""
    run_times = _workflow_run_durations(org, cache_dir)
    jobs = _jobs_df(org, cache_dir)
    if run_times.empty or jobs.empty:
        return pd.DataFrame()

    g = run_times.groupby(["workflow_path", "repo"])
    result = pd.DataFrame(
        {
            "Has job failures": g["has_failure"].mean() * 100,
            "Avg run time": g["wall_ms"].mean(),
            "Workflow runs": g["run_id"].count(),
        }
    ).reset_index()

    # Count distinct job names per workflow/repo
    job_counts = (
        jobs.groupby(["workflow_path", "repo"])["job_name"]
        .nunique()
        .reset_index()
        .rename(columns={"job_name": "Jobs"})
    )
    result = result.merge(job_counts, on=["workflow_path", "repo"], how="left")
    result.columns = ["Workflow", "Source repository", "Has job failures",
                       "Avg run time", "Workflow runs", "Jobs"]
    return result.sort_values("Avg run time", ascending=False).reset_index(drop=True)


def perf_jobs(org: str, cache_dir: Path) -> pd.DataFrame:
    """Per-job performance metrics."""
    df = _jobs_df(org, cache_dir)
    df = df.dropna(subset=["run_ms", "queue_ms"])
    if df.empty:
        return pd.DataFrame()

    g = df.groupby(["job_name", "workflow_path", "repo", "runner_type", "runner_labels"])
    result = pd.DataFrame(
        {
            "Failure rate": g["is_failure"].mean() * 100,
            "Avg run time": g["run_ms"].mean(),
            "Avg queue time": g["queue_ms"].mean(),
            "Job runs": g["job_id"].count(),
        }
    ).reset_index()
    result.columns = ["Job", "Workflow", "Source repository", "Runner type", "Runner labels",
                       "Failure rate", "Avg run time", "Avg queue time", "Job runs"]
    return result.sort_values("Avg run time", ascending=False).reset_index(drop=True)


def perf_repositories(org: str, cache_dir: Path) -> pd.DataFrame:
    """Per-repository performance metrics."""
    df = _jobs_df(org, cache_dir)
    df = df.dropna(subset=["run_ms", "queue_ms"])
    if df.empty:
        return pd.DataFrame()

    g = df.groupby("repo")
    result = pd.DataFrame(
        {
            "Failure rate": g["is_failure"].mean() * 100,
            "Avg job run time": g["run_ms"].mean(),
            "Avg job queue time": g["queue_ms"].mean(),
            "Job runs": g["job_id"].count(),
        }
    ).reset_index()
    result.columns = ["Source repository", "Failure rate",
                       "Avg job run time", "Avg job queue time", "Job runs"]
    return result.sort_values("Avg job run time", ascending=False).reset_index(drop=True)


def perf_runtime_os(org: str, cache_dir: Path) -> pd.DataFrame:
    """Per-OS performance metrics."""
    df = _jobs_df(org, cache_dir)
    df = df.dropna(subset=["run_ms", "queue_ms"])
    if df.empty:
        return pd.DataFrame()

    g = df.groupby("runtime_os")
    result = pd.DataFrame(
        {
            "Failure rate": g["is_failure"].mean() * 100,
            "Avg job run time": g["run_ms"].mean(),
            "Avg job queue time": g["queue_ms"].mean(),
            "Job runs": g["job_id"].count(),
        }
    ).reset_index()
    result.columns = ["Runtime OS", "Failure rate",
                       "Avg job run time", "Avg job queue time", "Job runs"]
    return result.sort_values("Avg job run time", ascending=False).reset_index(drop=True)


def perf_runner_type(org: str, cache_dir: Path) -> pd.DataFrame:
    """Per-runner-type performance metrics."""
    df = _jobs_df(org, cache_dir)
    df = df.dropna(subset=["run_ms", "queue_ms"])
    if df.empty:
        return pd.DataFrame()

    g = df.groupby("runner_type")
    result = pd.DataFrame(
        {
            "Failure rate": g["is_failure"].mean() * 100,
            "Avg job run time": g["run_ms"].mean(),
            "Avg job queue time": g["queue_ms"].mean(),
            "Job runs": g["job_id"].count(),
        }
    ).reset_index()
    result.columns = ["Runner type", "Failure rate",
                       "Avg job run time", "Avg job queue time", "Job runs"]
    return result.sort_values("Avg job run time", ascending=False).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Convenience: generate all tables at once
# ---------------------------------------------------------------------------

USAGE_TABLES = {
    "workflows": usage_workflows,
    "jobs": usage_jobs,
    "repositories": usage_repositories,
    "runtime-os": usage_runtime_os,
    "runner-type": usage_runner_type,
}

PERFORMANCE_TABLES = {
    "workflows": perf_workflows,
    "jobs": perf_jobs,
    "repositories": perf_repositories,
    "runtime-os": perf_runtime_os,
    "runner-type": perf_runner_type,
}
