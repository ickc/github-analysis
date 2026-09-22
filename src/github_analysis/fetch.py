"""Stage 1 — fetch raw workflow-run and job data from GitHub, cached on disk.

This stage is deliberately *dumb*: it copies GitHub's JSON payloads to local
files and does no interpretation. Keeping the cache as verbatim API responses
means the typed parsing in :mod:`github_analysis.domain` is the single place
where raw data becomes structured, and a cache can be re-parsed by a newer
version of the code without re-hitting the network.

Cache layout::

    {cache_dir}/{org}/{repo}/repo.json        # repository metadata (visibility)
    {cache_dir}/{org}/{repo}/runs.json        # list of workflow-run payloads
    {cache_dir}/{org}/{repo}/jobs/{run_id}.json  # list of job payloads per run

``repo.json`` keeps only the few repository fields the analysis needs (see
:data:`REPO_METADATA_FIELDS`), not the full payload. It records the
repository's visibility *at fetch time* and is rewritten on every org fetch.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ._api import github_api, github_api_paginate
from .config import DateRange
from github.GithubException import GithubException

log = logging.getLogger(__name__)

__all__ = [
    "CachePaths",
    "REPO_METADATA_FIELDS",
    "fetch_org",
    "fetch_repo",
    "list_org_repos",
    "list_account_repos",
]

# Repository fields cached in ``repo.json``.
REPO_METADATA_FIELDS: tuple[str, ...] = ("name", "private", "visibility", "archived")


@dataclass(frozen=True, slots=True)
class CachePaths:
    """Resolves the on-disk cache locations for one org. Pure path algebra."""

    cache_dir: Path
    org: str

    @property
    def org_dir(self) -> Path:
        return self.cache_dir / self.org

    def repo_dir(self, repo: str) -> Path:
        return self.org_dir / repo

    def repo_metadata_file(self, repo: str) -> Path:
        return self.repo_dir(repo) / "repo.json"

    def runs_file(self, repo: str) -> Path:
        return self.repo_dir(repo) / "runs.json"

    def jobs_dir(self, repo: str) -> Path:
        return self.repo_dir(repo) / "jobs"

    def job_file(self, repo: str, run_id: int) -> Path:
        return self.jobs_dir(repo) / f"{run_id}.json"


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text())


# ---------------------------------------------------------------------------
# API calls (network)
# ---------------------------------------------------------------------------


def list_account_repos(account: str) -> list[dict[str, Any]]:
    """All repositories for an organization *or* a user account.

    ``account`` may name either; the org endpoint is tried first and, on a 404
    (i.e. it is a user, not an org), the user endpoint is used. Which repos are
    returned depends on the token's visibility into ``account``.
    """
    try:
        return github_api_paginate(f"/orgs/{account}/repos", type="all")
    except GithubException as exc:
        if exc.status == 404:
            return github_api_paginate(f"/users/{account}/repos", type="all")
        raise


# Backwards-compatible alias.
list_org_repos = list_account_repos


def get_repo(org: str, repo: str) -> dict[str, Any]:
    """The repository payload for ``org/repo``."""
    return github_api(f"/repos/{org}/{repo}")


def list_workflow_runs(
    org: str, repo: str, date_range: DateRange | None = None
) -> list[dict[str, Any]]:
    """All workflow-run payloads for a repo, optionally filtered by creation date."""
    params: dict[str, str] = {}
    if date_range is not None:
        since = date_range.since.strftime("%Y-%m-%dT%H:%M:%SZ")
        until = date_range.until.strftime("%Y-%m-%dT%H:%M:%SZ")
        params["created"] = f"{since}..{until}"
    return github_api_paginate(
        f"/repos/{org}/{repo}/actions/runs",
        response_key="workflow_runs",
        **params,
    )


def list_run_jobs(org: str, repo: str, run_id: int) -> list[dict[str, Any]]:
    """All job payloads for a single workflow run."""
    return github_api_paginate(
        f"/repos/{org}/{repo}/actions/runs/{run_id}/jobs",
        response_key="jobs",
        filter="all",
    )


# ---------------------------------------------------------------------------
# Fetch-and-cache
# ---------------------------------------------------------------------------


def _write_repo_metadata(paths: CachePaths, repo: str, payload: Mapping[str, Any]) -> None:
    trimmed = {key: payload[key] for key in REPO_METADATA_FIELDS if key in payload}
    _write_json(paths.repo_metadata_file(repo), trimmed)


def _cache_repo_metadata(org: str, repo: str, paths: CachePaths, *, force: bool) -> None:
    """Cache ``repo.json`` for one repo if missing; failures are only logged."""
    if not force and paths.repo_metadata_file(repo).exists():
        return
    try:
        _write_repo_metadata(paths, repo, get_repo(org, repo))
    except Exception as exc:  # noqa: BLE001 - metadata is optional
        log.warning("No repository metadata for %s/%s: %s", org, repo, exc)


def fetch_repo(
    org: str,
    repo: str,
    cache_dir: Path,
    *,
    date_range: DateRange | None = None,
    force: bool = False,
    metadata: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Fetch and cache runs (and their jobs) for one repo.

    Cached files are reused unless ``force`` is set. ``metadata`` is the repo's
    payload from an org listing; without it the repo is looked up (once) for
    its visibility. Returns the run payloads.
    """
    paths = CachePaths(cache_dir, org)
    runs_file = paths.runs_file(repo)

    if metadata is not None:
        _write_repo_metadata(paths, repo, metadata)
    else:
        _cache_repo_metadata(org, repo, paths, force=force)

    if force or not runs_file.exists():
        log.info("Fetching runs for %s/%s", org, repo)
        runs = list_workflow_runs(org, repo, date_range)
        _write_json(runs_file, runs)
    else:
        runs = _read_json(runs_file)

    for run in runs:
        run_id = int(run["id"])
        job_file = paths.job_file(repo, run_id)
        if not force and job_file.exists():
            continue
        log.debug("Fetching jobs for run %s (%s/%s)", run_id, org, repo)
        _write_json(job_file, list_run_jobs(org, repo, run_id))

    return runs


def fetch_org(
    org: str,
    cache_dir: Path,
    *,
    date_range: DateRange | None = None,
    force: bool = False,
    repos: Iterable[str] | None = None,
) -> list[str]:
    """Fetch and cache every repo in the org (or an explicit ``repos`` subset).

    Repos that error (e.g. no Actions access) are logged and skipped. Returns
    the list of repo names successfully fetched.
    """
    metadata: dict[str, Mapping[str, Any]] = {}
    if repos is None:
        metadata = {r["name"]: r for r in list_account_repos(org)}
        repos = list(metadata)

    fetched: list[str] = []
    for repo in repos:
        try:
            fetch_repo(
                org,
                repo,
                cache_dir,
                date_range=date_range,
                force=force,
                metadata=metadata.get(repo),
            )
            fetched.append(repo)
        except Exception as exc:  # noqa: BLE001 - resilience over strictness
            log.warning("Skipping %s/%s: %s", org, repo, exc)
    return fetched
