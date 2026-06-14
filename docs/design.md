# Design

`github-analysis` is built as a **three-stage pipeline** with a strong
functional bias: immutable data, pure transformations, and function signatures
that state their contracts through type hints. This page explains the stages,
the central design decision (the intermediate representation), and the
conventions that keep the code auditable.

## The three stages

```text
        GitHub REST API
              │  (fetch, network)
              ▼
   ┌─────────────────────┐
   │  Stage 1: raw cache  │   verbatim JSON on disk
   │  github_analysis.fetch│   {cache}/{org}/{repo}/runs.json, jobs/{id}.json
   └─────────────────────┘
              │  (parse, pure)
              ▼
   ┌──────────────────────────────┐
   │  Stage 2: the canonical IR     │   ActionsDataset
   │  github_analysis.dataset       │   tidy per-job + per-run tables
   └──────────────────────────────┘
              │  (project, pure)
              ▼
   ┌───────────────────────────────────────────────┐
   │  Stage 3: analysis                              │
   │  metrics  → GitHub-style usage/performance tables│
   │  analysis → KPIs + monthly time series          │
   │  report   → HTML dashboard + JSON summary       │
   │  csv_export → GitHub-compatible CSV projection  │
   └───────────────────────────────────────────────┘
```

### Stage 1 — fetch (`github_analysis.fetch`)

The only stage that touches the network. It is deliberately *dumb*: it copies
GitHub's JSON payloads to local files and interprets nothing. Keeping the cache
as verbatim API responses means:

- parsing happens in exactly one place (stage 2), so there is a single
  definition of every derived quantity; and
- an existing cache can be re-parsed by newer code without re-fetching.

### Stage 2 — the dataset (`github_analysis.dataset`)

Raw payloads are parsed once, at the boundary, into frozen
{py:class}`~github_analysis.domain.Job` and {py:class}`~github_analysis.domain.Run`
records (see {py:mod}`github_analysis.domain`). Every derived measure —
durations, billed minutes, OS classification, failure flags — is a pure
`property` on the record, so the rule "a billed minute is `ceil(run_ms / 60000)`"
lives next to the data, not scattered across the analysis.

These records are assembled into the **canonical intermediate representation**,
{py:class}`~github_analysis.dataset.ActionsDataset`: an immutable object exposing
*tidy* DataFrames (one row per job, one row per run) via `cached_property`.

### Stage 3 — analysis

Everything here is a **pure projection** of the dataset:

- {py:mod}`github_analysis.metrics` reproduces GitHub's *Usage* and
  *Performance* tables, described declaratively by `TableSpec`s.
- {py:mod}`github_analysis.analysis` derives billed-equivalent minutes, the
  plan-cap projection, and monthly time-series, returning plain DataFrames and
  an immutable {py:class}`~github_analysis.analysis.UsageSummary`.
- {py:mod}`github_analysis.charts` turns those frames into Plotly figures
  (pure `data -> Figure`).
- {py:mod}`github_analysis.report` assembles the HTML/JSON; it contains no
  metric maths of its own.

## The intermediate representation: why it changed

The original implementation used **GitHub's UI-export CSVs** (already grouped by
workflow / repo / OS) as its intermediate representation. That choice had a
concrete, load-bearing limitation:

> The aggregate CSVs drop per-job and per-run **timestamps**.

Because of that, no time-based view — monthly trends, "last green day" — could
be derived from the IR. The original dashboard worked around this by *secretly
re-reading the raw cache* to build its monthly charts, which broke the stated
goal that the report be built **entirely from the intermediate representation**.

The refactor promotes a **tidy, per-job long-form table** to be the canonical
IR. One row per job carries every dimension (repo, workflow, OS, runner type,
labels, month) and every measure (run/queue milliseconds, billed minutes,
failure flag) at full resolution. Consequently:

- **Every** downstream view — the GitHub-style aggregate tables *and* the
  time-series — is a pure projection of one object. Nothing reaches back to the
  cache.
- The GitHub-compatible CSVs still exist, but as an **export**
  ({py:mod}`github_analysis.csv_export`), not the source of truth. They remain so
  that generated output can be diffed against GitHub's own exports with
  {py:mod}`github_analysis.compare`.

This is the standard "tidy data" trade-off: store data in its most granular,
long form and aggregate on demand, rather than committing early to a lossy
aggregate.

## Functional conventions

- **Immutability.** Records and {py:class}`~github_analysis.config.AnalysisConfig`
  are `frozen` dataclasses; overrides are explicit
  {py:func}`dataclasses.replace`. Records also use `slots` for compactness; the
  dataset omits `slots` only because `cached_property` needs an instance dict.
- **Purity.** Stage 2 and 3 functions take data and return data. The single
  side-effecting boundaries are `fetch` (network) and `build_dashboard` /
  `write_reports` (disk writes).
- **Typed contracts.** Signatures speak in domain types
  (`Sequence[Job]`, `ActionsDataset`, `DateRange`) rather than bare dicts, so a
  signature documents what a function consumes and produces even where types are
  not enforced at runtime.
- **Declarative over imperative.** The ten metric tables are data
  (`TableSpec`/`Measure`) interpreted by one small function, instead of ten
  near-identical groupby blocks.

## Known limitations

- **Billing approximation.** Billed minutes use `ceil(run_ms / 60000)` per job.
  GitHub's exact billing may differ for very short, cancelled, or skipped jobs.
- **OS multipliers are assumptions.** Billed-equivalent minutes apply
  configurable OS multipliers (default linux ×1, windows ×2, macos ×10); these
  model standard hosted-runner pricing and should be set to match your plan.
- **Monthly estimate window.** Headline "monthly demand" treats the selected
  period as an annual window (total ÷ 12); choose the period accordingly.
- **Hosted-only metrics.** Self-hosted jobs are present in the cache but excluded
  from the metric tables, matching GitHub's exported metrics.
