"""Deterministic synthetic GitHub Actions cache for offline tests and docs.

This writes the same on-disk cache layout that ``github-analysis fetch`` would
produce, so the entire stage 2/3 pipeline can be exercised without a network or
a token. The numbers are small and hand-chosen so tests can assert exact values.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

__all__ = ["write_synthetic_cache", "ORG"]

ORG = "demo-org"

# (repo, workflow_path, runner_group, labels, conclusion, run_minutes, month_day)
# run_minutes is the exact job execution time; billed = ceil(minutes) per job.
_JOBS: list[tuple[str, str, str, list[str], str, float, tuple[int, int, int]]] = [
    ("api", ".github/workflows/ci.yml", "GitHub Actions", ["ubuntu-latest"], "success", 4.0, (2024, 1, 5)),
    ("api", ".github/workflows/ci.yml", "GitHub Actions", ["ubuntu-latest"], "failure", 6.0, (2024, 1, 20)),
    ("api", ".github/workflows/ci.yml", "GitHub Actions", ["windows-latest"], "success", 3.0, (2024, 2, 8)),
    ("api", ".github/workflows/release.yml", "GitHub Actions", ["macos-latest"], "success", 5.0, (2024, 2, 18)),
    ("web", ".github/workflows/ci.yml", "GitHub Actions", ["ubuntu-latest"], "success", 2.0, (2024, 1, 9)),
    ("web", ".github/workflows/ci.yml", "GitHub Actions", ["ubuntu-latest"], "success", 7.0, (2024, 2, 27)),
    # A self-hosted job: present in the cache but excluded from hosted metrics.
    ("web", ".github/workflows/ci.yml", "self-hosted-group", ["self-hosted", "linux"], "success", 9.0, (2024, 2, 14)),
]


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def write_synthetic_cache(cache_dir: Path, org: str = ORG) -> Path:
    """Create a synthetic cache under ``cache_dir/org`` and return ``cache_dir``."""
    runs_by_repo: dict[str, list[dict]] = {}
    jobs_by_run: dict[tuple[str, int], list[dict]] = {}

    for index, (repo, path, group, labels, conclusion, minutes, ymd) in enumerate(_JOBS):
        run_id = 1000 + index
        job_id = 2000 + index
        created = datetime(*ymd, 9, 0, 0, tzinfo=timezone.utc)
        started = created + timedelta(seconds=30)  # 30s queue time
        completed = started + timedelta(minutes=minutes)

        runs_by_repo.setdefault(repo, []).append(
            {
                "id": run_id,
                "path": path,
                "conclusion": conclusion,
                "created_at": _iso(created),
                "run_started_at": _iso(started),
                "updated_at": _iso(completed),
            }
        )
        jobs_by_run[(repo, run_id)] = [
            {
                "id": job_id,
                "run_id": run_id,
                "name": "build",
                "workflow_name": Path(path).stem,
                "status": "completed",
                "conclusion": conclusion,
                "runner_group_name": group,
                "labels": labels,
                "created_at": _iso(created),
                "started_at": _iso(started),
                "completed_at": _iso(completed),
            }
        ]

    for repo, runs in runs_by_repo.items():
        repo_dir = cache_dir / org / repo
        (repo_dir / "jobs").mkdir(parents=True, exist_ok=True)
        (repo_dir / "runs.json").write_text(json.dumps(runs), encoding="utf-8")
        for run in runs:
            job_file = repo_dir / "jobs" / f"{run['id']}.json"
            job_file.write_text(json.dumps(jobs_by_run[(repo, run["id"])]), encoding="utf-8")

    return cache_dir
