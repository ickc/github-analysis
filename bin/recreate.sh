#!/usr/bin/env bash
# Recreate GitHub Actions metrics CSVs from the live API.
#
# Usage:
#   bin/recreate.sh [--org ORG] [--period PERIOD] [--force]
#
# Defaults:
#   ORG          = UniExeterRSE
#   PERIOD       = last-year
#   CACHE        = <workspace>/cache
#   REPORTS      = <workspace>/reports
#   EXTRA_REPOS  = space-separated list of "NewOrg/repo-name" pairs for repos
#                  that were transferred out of ORG but should still be tracked.
#                  Their job data is cached under cache/<NewOrg>/<repo-name>/.
#                  Example: EXTRA_REPOS="Uni-of-Exeter/brownian-spin-dynamics"
#
# Override via environment:
#   ORG=MyOrg PERIOD=last-month bin/recreate.sh
#   EXTRA_REPOS="Uni-of-Exeter/brownian-spin-dynamics" bin/recreate.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUBMODULE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
WORKSPACE_DIR="$(cd "${SUBMODULE_DIR}/.." && pwd)"

ORG="${ORG:-UniExeterRSE}"
PERIOD="${PERIOD:-last-year}"
CACHE_DIR="${CACHE_DIR:-${WORKSPACE_DIR}/cache}"
REPORTS_DIR="${REPORTS_DIR:-${WORKSPACE_DIR}/reports}"
FORCE="${FORCE:-}"

# Space-separated "OtherOrg/repo" pairs for repos transferred out of ORG.
# Their job data is cached under cache/<OtherOrg>/<repo>/ and included by
# build_dashboard.py when EXTRA_REPOS (or TRANSFERRED_REPOS) is configured.
EXTRA_REPOS="${EXTRA_REPOS:-Uni-of-Exeter/brownian-spin-dynamics}"

PIXI="pixi run --manifest-path ${SUBMODULE_DIR}/pyproject.toml"

echo "=== github-analysis: recreate metrics ==="
echo "  org:          ${ORG}"
echo "  period:       ${PERIOD}"
echo "  cache:        ${CACHE_DIR}"
echo "  reports:      ${REPORTS_DIR}"
echo "  extra repos:  ${EXTRA_REPOS}"
echo ""

echo "--- Step 1: fetch raw data for ${ORG} ---"
${PIXI} github-analysis fetch \
    --org "${ORG}" \
    --period "${PERIOD}" \
    --cache-dir "${CACHE_DIR}" \
    ${FORCE:+--force}

echo ""
echo "--- Step 1b: fetch transferred repos ---"
for entry in ${EXTRA_REPOS}; do
    extra_org="${entry%%/*}"
    extra_repo="${entry##*/}"
    echo "  fetching ${extra_org}/${extra_repo} ..."
    ${PIXI} github-analysis fetch \
        --org "${extra_org}" \
        --repo "${extra_repo}" \
        --period "${PERIOD}" \
        --cache-dir "${CACHE_DIR}" \
        ${FORCE:+--force}
done

echo ""
echo "--- Step 2: generate reports ---"
${PIXI} github-analysis report \
    --org "${ORG}" \
    --cache-dir "${CACHE_DIR}" \
    --output-dir "${REPORTS_DIR}"

echo ""
echo "=== Done. Reports in ${REPORTS_DIR} ==="
