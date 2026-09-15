"""End-to-end tests over the synthetic cache: dataset, metrics, analysis, report."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from github_analysis.analysis import billed_equivalent_by_os, summarize_usage
from github_analysis.compare import main as compare_main
from github_analysis.config import AnalysisConfig
from github_analysis.csv_export import write_reports
from github_analysis.dataset import ActionsDataset
from github_analysis.metrics import performance_table, usage_table
from github_analysis.report import build_dashboard

from .synthetic import ORG

MULTIPLIERS = {"linux": 1.0, "windows": 2.0, "macos": 10.0}


# ---------------------------------------------------------------------------
# dataset (stage 2)
# ---------------------------------------------------------------------------


def test_dataset_counts(dataset: ActionsDataset):
    assert len(dataset.jobs) == 7
    assert dataset.jobs_frame.shape[0] == 7  # all completed
    assert dataset.hosted_jobs_frame.shape[0] == 6  # one self-hosted excluded
    assert dataset.runs_frame.shape[0] == 7


def test_workflow_path_resolved_onto_jobs(dataset: ActionsDataset):
    paths = set(dataset.hosted_jobs_frame["workflow_path"])
    assert ".github/workflows/ci.yml" in paths
    assert ".github/workflows/release.yml" in paths


# ---------------------------------------------------------------------------
# metrics (stage 3a)
# ---------------------------------------------------------------------------


def test_usage_repositories(dataset: ActionsDataset):
    table = usage_table(dataset, "repositories").set_index("Source repository")
    assert table.loc["api", "Total minutes"] == 18
    assert table.loc["api", "Workflow runs"] == 4
    assert table.loc["api", "Workflows"] == 2
    assert table.loc["web", "Total minutes"] == 9  # self-hosted 9-min job excluded


def test_usage_runtime_os(dataset: ActionsDataset):
    table = usage_table(dataset, "runtime-os").set_index("Runtime OS")
    assert table.loc["linux", "Total minutes"] == 19
    assert table.loc["windows", "Total minutes"] == 3
    assert table.loc["macos", "Total minutes"] == 5


def test_performance_repositories_failure_rate(dataset: ActionsDataset):
    table = performance_table(dataset, "repositories").set_index("Source repository")
    assert table.loc["api", "Failure rate"] == 25.0  # 1 of 4 hosted jobs failed
    assert table.loc["web", "Failure rate"] == 0.0


def test_performance_workflows_is_run_level(dataset: ActionsDataset):
    table = performance_table(dataset, "workflows")
    assert set(["Workflow", "Source repository", "Avg run time", "Jobs"]).issubset(table.columns)
    assert len(table) >= 1


# ---------------------------------------------------------------------------
# analysis (stage 3b)
# ---------------------------------------------------------------------------


def test_billed_equivalent_by_os(dataset: ActionsDataset):
    by_os = billed_equivalent_by_os(dataset, MULTIPLIERS).set_index("Runtime OS")
    assert by_os.loc["linux", "Billed equivalent minutes"] == 19
    assert by_os.loc["windows", "Billed equivalent minutes"] == 6
    assert by_os.loc["macos", "Billed equivalent minutes"] == 50


def _config(tmp_path: Path, **extra) -> AnalysisConfig:
    return AnalysisConfig(
        org=ORG,
        root=tmp_path,
        cache_dir=tmp_path / "cache",
        reports_dir=tmp_path / "reports",
        data_dir=tmp_path / "reports",
        output_html=tmp_path / "docs" / "index.html",
        summary_json=tmp_path / "docs" / "summary.json",
        os_multipliers=MULTIPLIERS,
        **extra,
    )


def test_summarize_usage(dataset: ActionsDataset, tmp_path: Path):
    summary = summarize_usage(dataset, _config(tmp_path))
    assert summary.raw_total_minutes == 27
    assert summary.billed_equivalent_minutes == 75
    assert summary.estimated_monthly_billed == 75 / 12


def test_summarize_usage_with_plan_cap(dataset: ActionsDataset, tmp_path: Path):
    summary = summarize_usage(dataset, _config(tmp_path, plan_minutes=5.0))
    assert summary.cap_reached is True  # 6.25/month > 5
    assert summary.estimated_unserved_billed > 0


# ---------------------------------------------------------------------------
# csv export + compare (stage 3 projection)
# ---------------------------------------------------------------------------


def test_csv_export_and_self_compare(dataset: ActionsDataset, tmp_path: Path):
    out = tmp_path / "reports"
    paths = write_reports(dataset, out, "both")
    assert paths
    assert (out / "actions-usage-metrics" / "repositories.csv").exists()
    assert (out / "actions-performance-metrics" / "jobs.csv").exists()

    # A snapshot compared against itself must be a clean match (exit 0).
    assert compare_main(out, out) == 0


# ---------------------------------------------------------------------------
# report (stage 3c)
# ---------------------------------------------------------------------------


def test_build_dashboard_writes_html_and_summary(dataset: ActionsDataset, tmp_path: Path):
    config = _config(tmp_path, plan_minutes=5.0)
    metadata = build_dashboard(config)

    assert config.output_html.exists()
    assert config.summary_json.exists()
    html = config.output_html.read_text()
    assert "Key insights" in html
    assert "Monthly trends" in html  # time-series derived from the tidy IR

    assert metadata["raw_total_minutes_period"] == 27
    assert metadata["billed_equivalent_minutes_period"] == 75
    saved = json.loads(config.summary_json.read_text())
    assert saved["org"] == ORG
    assert "2024-01" in saved["monthly_billed_equivalent_minutes"]


def test_empty_dataset_is_safe():
    empty = ActionsDataset(org="none", jobs=(), runs=())
    assert empty.is_empty
    assert usage_table(empty, "repositories").empty
    assert performance_table(empty, "workflows").empty
