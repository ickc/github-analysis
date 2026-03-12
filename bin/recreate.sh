#!/usr/bin/env bash
# Recreate GitHub Actions metrics CSVs from the live API.
#
# Usage:
#   bin/recreate.sh [--org ORG] [--period PERIOD] [--force]
#
# Defaults:
#   ORG     = UniExeterRSE
#   PERIOD  = last-year
#   CACHE   = <workspace>/cache
#   REPORTS = <workspace>/reports
#
# Override via environment:
#   ORG=MyOrg PERIOD=last-month bin/recreate.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUBMODULE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
WORKSPACE_DIR="$(cd "${SUBMODULE_DIR}/.." && pwd)"

ORG="${ORG:-UniExeterRSE}"
PERIOD="${PERIOD:-last-year}"
CACHE_DIR="${CACHE_DIR:-${WORKSPACE_DIR}/cache}"
REPORTS_DIR="${REPORTS_DIR:-${WORKSPACE_DIR}/reports}"
FORCE="${FORCE:-}"

PIXI="pixi run --manifest-path ${SUBMODULE_DIR}/pyproject.toml"

echo "=== github-analysis: recreate metrics ==="
echo "  org:     ${ORG}"
echo "  period:  ${PERIOD}"
echo "  cache:   ${CACHE_DIR}"
echo "  reports: ${REPORTS_DIR}"
echo ""

echo "--- Step 1: fetch raw data ---"
${PIXI} github-analysis fetch \
    --org "${ORG}" \
    --period "${PERIOD}" \
    --cache-dir "${CACHE_DIR}" \
    ${FORCE:+--force}

echo ""
echo "--- Step 2: generate reports ---"
${PIXI} github-analysis report \
    --org "${ORG}" \
    --cache-dir "${CACHE_DIR}" \
    --output-dir "${REPORTS_DIR}"

echo ""
echo "=== Done. Reports in ${REPORTS_DIR} ==="
