# github-analysis

Reusable tooling for recreating GitHub Actions metrics tables from the GitHub API.

This submodule is the public part of the workflow. It fetches raw workflow/job
data with the GitHub REST API through `PyGithub`, caches the JSON locally, and
computes CSV tables that mirror GitHub's Actions metrics exports.

## What lives here

- `src/github_analysis/`: the library and CLI
- `bin/recreate.sh`: end-to-end command sequence to fetch raw data and write CSVs
- `bin/verify.sh`: compare generated CSVs against the private snapshot in `../data/`
- `bin/compare.py`: normalization-aware verifier run inside the submodule's `pixi` environment

## Quick start

From the private workspace root, initialize the submodule first:

```bash
git submodule update --init --recursive
```

Then run the reproducible workflow from inside the submodule:

```bash
cd github-analysis
bin/recreate.sh
bin/verify.sh
```

The recreate script uses the `pixi` environment defined by this submodule and
writes its artifacts into the parent workspace:

- cache: `../cache/`
- generated reports: `../reports/`

Default settings:

- org: `UniExeterRSE`
- period: `last-year`

You can override them with environment variables:

```bash
cd github-analysis
ORG=UniExeterRSE PERIOD=last-year bin/recreate.sh
```

Authentication:

- preferred: set `GITHUB_TOKEN` or `GH_TOKEN`
- fallback: if you are already logged into the `gh` CLI, the library reads
  `~/.config/gh/hosts.yml` directly and reuses that token without invoking `gh`

## What the scripts do

`bin/recreate.sh` runs:

```bash
pixi run --manifest-path ./pyproject.toml github-analysis fetch \
  --org UniExeterRSE \
  --period last-year \
  --cache-dir ../cache

pixi run --manifest-path ./pyproject.toml github-analysis report \
  --org UniExeterRSE \
  --cache-dir ../cache \
  --output-dir ../reports
```

`bin/verify.sh` runs the Python comparator in the same `pixi` environment:

```bash
pixi run --manifest-path ./pyproject.toml python ./bin/compare.py ../data ../reports
```

## Notes on comparison

The private `../data/` directory is a manual GitHub UI export committed in the
private parent repository. Exact byte-for-byte equality is not expected because:

- the snapshot can contain rows for repos that are now private, deleted, or no
  longer visible in the org (`Repository not found`, removed repos, etc.)
- new workflow runs may have happened between the snapshot export and your API fetch
- the exported UI timings are stable at about whole-second precision, not exact milliseconds

The verifier therefore checks for structural compatibility and reports which
differences are explained by visibility/snapshot drift.
