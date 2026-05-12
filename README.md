# github-analysis

Reusable tools for analyzing GitHub Actions usage and performance across any
GitHub organization.

The package fetches workflow run and job data from the GitHub REST API, caches
the raw JSON locally, computes CSV tables similar to GitHub's Actions metrics
exports, and can build a static HTML dashboard from those CSVs. It is
organization-agnostic: organization names, output paths, and billing assumptions
belong in your config file.

## Install

After the package is published to PyPI:

```bash
uv tool install github-analysis
github-analysis --help
```

For local development from a checkout:

```bash
uv sync
uv run github-analysis --help
```

## Authentication

The CLI needs a GitHub token that can read Actions metadata for the target
organization. Use either:

```bash
export GITHUB_TOKEN=...
```

or login with the GitHub CLI:

```bash
gh auth login
```

If `GITHUB_TOKEN` and `GH_TOKEN` are unset, `github-analysis` reads the token
from `~/.config/gh/hosts.yml`.

## Tutorial

Create a project directory for your analysis:

```bash
mkdir github-actions-report
cd github-actions-report
mkdir -p docs
```

Create `analysis.toml`:

```toml
[analysis]
org = "example-org"
period = "last-year"
cache_dir = "cache"
reports_dir = "reports"
data_dir = "reports"
output_html = "docs/index.html"
summary_json = "docs/summary.json"

[dashboard]
title = "GitHub Actions Usage Analysis"
period_label = "last year"
# Optional monthly included-minutes cap for the dashboard simulation.
# plan_minutes = 3000

[os_multipliers]
linux = 1
windows = 2
macos = 10
```

Fetch and cache raw GitHub API data:

```bash
github-analysis fetch \
  --org example-org \
  --period last-year \
  --cache-dir cache
```

Generate CSV reports from the cache:

```bash
github-analysis report \
  --org example-org \
  --cache-dir cache \
  --output-dir reports
```

Build the static dashboard:

```bash
github-analysis dashboard --config analysis.toml
```

The dashboard is written to `docs/index.html`, and machine-readable summary
metadata is written to `docs/summary.json`.

You can run the same workflow in one command:

```bash
github-analysis recreate --config analysis.toml
```

## Other Commands

Show a table in the terminal:

```bash
github-analysis show usage workflows --org example-org --cache-dir cache
```

Compare generated CSV reports against a reference snapshot:

```bash
github-analysis compare reference-data reports
```

## Config Reference

See [examples/analysis.toml](examples/analysis.toml) for a minimal complete
configuration.

`[analysis]`:

- `org`: GitHub organization name.
- `period`: one of `last-year`, `last-6-months`, `last-3-months`,
  `last-month`, or `YYYY-MM-DD..YYYY-MM-DD`.
- `cache_dir`: directory for cached raw JSON.
- `reports_dir`: directory where CSV reports are written by `recreate`.
- `data_dir`: directory read by `dashboard`; usually the same as `reports_dir`.
- `output_html`: static dashboard output path.
- `summary_json`: summary metadata output path.

`[dashboard]`:

- `title`: dashboard title.
- `period_label`: label used in chart headings.
- `plan_minutes`: optional monthly included-minutes cap for cap simulation.
- `extra_insights_html`: optional list of HTML snippets appended to insights.
- `extra_limitations_html`: optional list of HTML snippets appended to limitations.

`[os_multipliers]`:

- Runtime OS billing multipliers used for billed-equivalent minute estimates.

## Development

This project uses `uv`:

```bash
uv sync
uv run python -m compileall src bin
uv build
```

The package exposes the `github-analysis` console script via `pyproject.toml`.
