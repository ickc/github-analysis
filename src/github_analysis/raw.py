"""Fetch raw workflow run and job data from the GitHub API, with disk caching."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from ._api import gh_api, gh_api_paginate

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Fetching helpers
# ---------------------------------------------------------------------------

def list_org_repos(org: str) -> list[dict]:
    """Return all repos in the org (public + private, all types)."""
    return gh_api_paginate(f"/orgs/{org}/repos", type="all", per_page="100")


def list_workflow_runs(
    org: str,
    repo: str,
    since: datetime | None = None,
    until: datetime | None = None,
) -> list[dict]:
    """Return all workflow runs for a repo, optionally filtered by creation date."""
    params: dict[str, str] = {"per_page": "100"}
    if since or until:
        s = since.strftime("%Y-%m-%dT%H:%M:%SZ") if since else ""
        u = until.strftime("%Y-%m-%dT%H:%M:%SZ") if until else ""
        if s and u:
            params["created"] = f"{s}..{u}"
        elif s:
            params["created"] = f">={s}"
        elif u:
            params["created"] = f"<={u}"
    return gh_api_paginate(
        f"/repos/{org}/{repo}/actions/runs",
        response_key="workflow_runs",
        **params,
    )


def list_run_jobs(org: str, repo: str, run_id: int) -> list[dict]:
    """Return all jobs for a specific workflow run."""
    return gh_api_paginate(
        f"/repos/{org}/{repo}/actions/runs/{run_id}/jobs",
        response_key="jobs",
        filter="all",
        per_page="100",
    )


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------

def _cache_path(cache_dir: Path, org: str, repo: str) -> Path:
    return cache_dir / org / repo


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def _read_json(path: Path) -> object:
    return json.loads(path.read_text())


# ---------------------------------------------------------------------------
# High-level fetch-and-cache
# ---------------------------------------------------------------------------

def fetch_org(
    org: str,
    cache_dir: Path,
    since: datetime | None = None,
    until: datetime | None = None,
    force: bool = False,
) -> list[str]:
    """Fetch runs and jobs for all repos in the org, caching results.

    Returns list of repo names that were fetched (skips repos with no runs).
    Cached files:
      {cache_dir}/{org}/{repo}/runs.json   — list of run dicts
      {cache_dir}/{org}/{repo}/jobs/        — one {run_id}.json per run
    """
    repos = list_org_repos(org)
    fetched = []
    for repo_obj in repos:
        repo = repo_obj["name"]
        try:
            fetch_repo(org, repo, cache_dir, since=since, until=until, force=force)
            fetched.append(repo)
        except Exception as exc:
            log.warning("Skipping %s/%s: %s", org, repo, exc)
    return fetched


def fetch_repo(
    org: str,
    repo: str,
    cache_dir: Path,
    since: datetime | None = None,
    until: datetime | None = None,
    force: bool = False,
) -> list[dict]:
    """Fetch runs and jobs for a single repo, using cache where available.

    Returns the list of run dicts (loaded from cache or freshly fetched).
    """
    repo_dir = _cache_path(cache_dir, org, repo)
    runs_path = repo_dir / "runs.json"

    # --- runs ---
    if force or not runs_path.exists():
        log.info("Fetching runs for %s/%s", org, repo)
        runs = list_workflow_runs(org, repo, since=since, until=until)
        _write_json(runs_path, runs)
    else:
        runs = _read_json(runs_path)  # type: ignore[assignment]

    # --- jobs (one file per run, skip if already cached) ---
    jobs_dir = repo_dir / "jobs"
    for run in runs:
        run_id = run["id"]
        job_path = jobs_dir / f"{run_id}.json"
        if not force and job_path.exists():
            continue
        log.debug("Fetching jobs for run %s (%s/%s)", run_id, org, repo)
        jobs = list_run_jobs(org, repo, run_id)
        _write_json(job_path, jobs)

    return runs  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Loading cached data into flat structures
# ---------------------------------------------------------------------------

def load_runs(org: str, cache_dir: Path) -> list[dict]:
    """Load all cached runs for the org as a flat list."""
    runs = []
    org_dir = cache_dir / org
    if not org_dir.exists():
        return runs
    for runs_path in org_dir.glob("*/runs.json"):
        repo = runs_path.parent.name
        for run in json.loads(runs_path.read_text()):
            run["_repo"] = repo
            runs.append(run)
    return runs


def load_jobs(org: str, cache_dir: Path) -> list[dict]:
    """Load all cached jobs for the org as a flat list."""
    jobs = []
    org_dir = cache_dir / org
    if not org_dir.exists():
        return jobs
    for job_path in org_dir.glob("*/jobs/*.json"):
        repo = job_path.parent.parent.name
        for job in json.loads(job_path.read_text()):
            job["_repo"] = repo
            jobs.append(job)
    return jobs
