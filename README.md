# github-analysis

Reusable tooling for GitHub Actions metrics analysis.

The package fetches workflow run and job data from the GitHub REST API, caches
the raw JSON locally, computes CSV tables similar to GitHub's Actions metrics
exports, and can build a static HTML dashboard from those CSVs.

## Install

This repository uses `pixi` for local development:

```bash
pixi run --manifest-path ./pyproject.toml github-analysis --help
```

Authentication:

- preferred: set `GITHUB_TOKEN` or `GH_TOKEN`
- fallback: if you are already logged into the `gh` CLI, the library reads
  `~/.config/gh/hosts.yml` and reuses that token

## CLI Workflow

Fetch raw cache data:

```bash
pixi run --manifest-path ./pyproject.toml github-analysis fetch \
  --org example-org \
  --period last-year \
  --cache-dir ./cache
```

Generate CSV reports:

```bash
pixi run --manifest-path ./pyproject.toml github-analysis report \
  --org example-org \
  --cache-dir ./cache \
  --output-dir ./reports
```

Build a dashboard:

```bash
pixi run --manifest-path ./pyproject.toml github-analysis dashboard \
  --org example-org \
  --data-dir ./reports \
  --cache-dir ./cache \
  --output ./docs/index.html
```

Show a table in the terminal:

```bash
pixi run --manifest-path ./pyproject.toml github-analysis show \
  usage workflows \
  --org example-org \
  --cache-dir ./cache
```

## Config-Driven Workflow

For repeatable organization-specific analyses, keep private organization names,
paths, billing caps, and related-repository quirks in a TOML config outside this
library:

```toml
[analysis]
org = "example-org"
period = "last-year"
cache_dir = "cache"
reports_dir = "reports"
data_dir = "reports"
reference_data_dir = "data"
output_html = "docs/index.html"
summary_json = "docs/summary.json"

[dashboard]
title = "GitHub Actions Usage Analysis"
period_label = "last year"
plan_minutes = 3000

[os_multipliers]
linux = 1
windows = 2
macos = 10

[[related_repositories]]
owner = "new-owner"
repo = "moved-repo"
label = "moved-repo"
transfer_date = "2026-01-20"
billing_owner_after = "new-owner"
note_html = "example note rendered in the dashboard"
```

Run the complete workflow from that config:

```bash
pixi run --manifest-path ./pyproject.toml github-analysis recreate \
  --config ../analysis.toml
```

`recreate` fetches the main organization, fetches configured related
repositories, writes CSV reports, and builds the static dashboard.

## Repository Layout

- `src/github_analysis/`: library and CLI
- `bin/recreate.sh`: shell wrapper around fetch/report or config-driven recreate
- `bin/verify.sh`: compare generated CSVs against a reference metrics snapshot
- `bin/compare.py`: normalization-aware CSV comparator

## Comparison Notes

`bin/compare.py` compares generated CSVs with a reference directory containing
GitHub UI metrics exports. Exact byte-for-byte equality is not expected because:

- a reference snapshot can contain rows for repositories that are no longer
  visible to the token used for a fresh API fetch
- new workflow runs may have happened after the reference snapshot was exported
- exported UI timings are stable at about whole-second precision, not exact
  milliseconds

The verifier checks structural compatibility and reports likely visibility or
snapshot drift separately from unexpected missing shared rows.
