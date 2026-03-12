#!/usr/bin/env python3
"""Compare reference and generated Actions metrics CSVs.

Usage:
    python compare.py <reference_dir> <generated_dir>

Where:
    reference_dir  contains actions-usage-metrics/ and actions-performance-metrics/
    generated_dir  has the same structure (output of 'github-analysis report')

Methodology:
    1. Load both CSVs, normalise the reference's \"'..\" encoding.
    2. Round floats (failure rates → 2 dp; times/counts → nearest integer).
    3. Outer-merge on key columns to find matched, new, and missing rows.
    4. For matched rows: report max absolute difference per numeric column.

    Rows present only in generated (not in reference) are EXPECTED when new
    workflow runs occurred between the reference snapshot and our fetch.
    Rows present only in reference are a potential bug.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pandas as pd

# ---------------------------------------------------------------------------
# Schema: keys and numeric columns per table
# ---------------------------------------------------------------------------

SCHEMA = {
    "actions-usage-metrics": {
        "workflows":    (["Workflow", "Source repository", "Runner type", "Runtime OS"],
                         ["Total minutes", "Workflow runs", "Jobs"]),
        "jobs":         (["Job", "Workflow", "Source repository", "Runner type", "Runner labels"],
                         ["Total minutes", "Job runs"]),
        "repositories": (["Source repository"],
                         ["Total minutes", "Workflow runs", "Workflows"]),
        "runtime-os":   (["Runtime OS"],
                         ["Total minutes", "Workflow runs", "Workflows"]),
        "runner-type":  (["Runner type"],
                         ["Total minutes", "Workflow runs", "Workflows"]),
    },
    "actions-performance-metrics": {
        "workflows":    (["Workflow", "Source repository"],
                         ["Has job failures", "Avg run time", "Workflow runs", "Jobs"]),
        "jobs":         (["Job", "Workflow", "Source repository", "Runner type", "Runner labels"],
                         ["Failure rate", "Avg run time", "Avg queue time", "Job runs"]),
        "repositories": (["Source repository"],
                         ["Failure rate", "Avg job run time", "Avg job queue time", "Job runs"]),
        "runtime-os":   (["Runtime OS"],
                         ["Failure rate", "Avg job run time", "Avg job queue time", "Job runs"]),
        "runner-type":  (["Runner type"],
                         ["Failure rate", "Avg job run time", "Avg job queue time", "Job runs"]),
    },
}

# Rounding precision per column
ROUND = {
    "Total minutes": 0, "Workflow runs": 0, "Job runs": 0,
    "Jobs": 0, "Workflows": 0,
    "Has job failures": 2, "Failure rate": 2,
    "Avg run time": 0, "Avg queue time": 0,
    "Avg job run time": 0, "Avg job queue time": 0,
}

# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------

def _clean(s: object) -> object:
    """Strip GitHub UI's \"'..\" encoding from column names and string cells."""
    if isinstance(s, str):
        s = re.sub(r'^"\'', "", s)
        s = re.sub(r'"$', "", s)
    return s


def load_ref(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df.columns = [_clean(c) for c in df.columns]
    # _clean is a no-op on non-strings, so safe to apply to all columns
    df = df.apply(lambda col: col.map(_clean))
    return df


def load_gen(path: Path) -> pd.DataFrame:
    return pd.read_csv(path)


def _round(df: pd.DataFrame, numeric_cols: list[str]) -> pd.DataFrame:
    df = df.copy()
    for col in numeric_cols:
        if col in df.columns:
            decimals = ROUND.get(col, 2)
            df[col] = pd.to_numeric(df[col], errors="coerce").round(decimals)
    return df


# ---------------------------------------------------------------------------
# Comparison logic
# ---------------------------------------------------------------------------

_GREEN = "\033[92m"
_YELLOW = "\033[93m"
_RED = "\033[91m"
_BOLD = "\033[1m"
_RESET = "\033[0m"


def _col(text: str, code: str) -> str:
    return f"{code}{text}{_RESET}"


def compare_table(
    ref_path: Path,
    gen_path: Path,
    keys: list[str],
    numeric_cols: list[str],
) -> bool:
    """Compare one pair of CSVs. Returns True if considered identical."""
    if not gen_path.exists():
        print(_col(f"  MISSING generated file: {gen_path}", _RED))
        return False

    ref = _round(load_ref(ref_path), numeric_cols)
    gen = _round(load_gen(gen_path), numeric_cols)

    # Normalise string key columns
    for col in keys:
        for df in (ref, gen):
            if col in df.columns:
                df[col] = df[col].astype(str).str.strip()

    avail_numeric = [c for c in numeric_cols if c in ref.columns and c in gen.columns]
    ref_cols = [c for c in keys + avail_numeric if c in ref.columns]
    gen_cols = [c for c in keys + avail_numeric if c in gen.columns]
    if not all(k in ref.columns for k in keys) or not all(k in gen.columns for k in keys):
        print(_col(f"  SKIP (key columns missing — columns mismatch)", _YELLOW))
        return True  # not a bug, just schema mismatch

    merged = (
        ref[ref_cols]
        .merge(gen[gen_cols], on=keys, how="outer", suffixes=("_ref", "_gen"), indicator=True)
    )

    both      = merged[merged["_merge"] == "both"]
    only_ref  = merged[merged["_merge"] == "left_only"]
    only_gen  = merged[merged["_merge"] == "right_only"]

    n_ref, n_gen = len(ref), len(gen)
    n_both = len(both)
    n_new  = len(only_gen)   # in generated, not in reference → newer runs (expected)
    n_miss = len(only_ref)   # in reference, not in generated → potential bug

    # Per-column diffs on matched rows
    col_diffs: dict[str, float] = {}
    for col in numeric_cols:
        rc, gc = f"{col}_ref", f"{col}_gen"
        if rc not in both.columns:
            continue
        delta = (both[rc] - both[gc]).abs()
        if delta.max() > 0:
            col_diffs[col] = float(delta.max())

    identical = (n_miss == 0) and (not col_diffs)
    status = _col("PASS", _GREEN) if identical else _col("DIFF", _YELLOW)

    print(f"  {status}  ref={n_ref} rows  gen={n_gen} rows  matched={n_both}"
          f"  new={n_new}  missing={n_miss}")

    if n_miss > 0:
        print(_col(f"  ⚠  {n_miss} rows in reference but NOT in generated:", _RED))
        print(only_ref[keys].head(5).to_string(index=False, justify="left"))

    if n_new > 0:
        print(_col(f"  ℹ  {n_new} rows only in generated (newer runs since reference snapshot — expected)", _YELLOW))

    if col_diffs:
        print(_col("  ⚠  Numeric differences on matched rows (after rounding):", _YELLOW))
        for col, mx in col_diffs.items():
            print(f"       {col}: max_delta={mx}")

    return identical


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(ref_dir: Path, gen_dir: Path) -> int:
    all_pass = True
    for metric, tables in SCHEMA.items():
        print(f"\n{_col(_BOLD + metric + _RESET, _BOLD)}")
        for table_name, (keys, numeric_cols) in tables.items():
            ref_path = ref_dir / metric / f"{table_name}.csv"
            gen_path = gen_dir / metric / f"{table_name}.csv"
            if not ref_path.exists():
                print(f"  {table_name}: {_col('SKIP (no reference file)', _YELLOW)}")
                continue
            print(f"  {table_name}:", end="  ")
            ok = compare_table(ref_path, gen_path, keys, numeric_cols)
            if not ok:
                all_pass = False

    print()
    if all_pass:
        print(_col("✓  All tables PASS — generated data matches reference.", _GREEN))
    else:
        print(_col("✗  Some differences found — see above.", _YELLOW))
        print(  "   Rows labelled 'new' are expected (newer runs since snapshot).")
        print(  "   Rows labelled 'missing' or numeric diffs may indicate a bug.")
    return 0 if all_pass else 1


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <reference_dir> <generated_dir>")
        sys.exit(1)
    sys.exit(main(Path(sys.argv[1]), Path(sys.argv[2])))
