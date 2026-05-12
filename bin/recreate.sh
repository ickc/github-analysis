#!/usr/bin/env bash
# Recreate GitHub Actions metrics CSVs from the live API.
#
# Config-driven usage:
#   bin/recreate.sh --config ../analysis.toml [--force]
#
# Direct usage:
#   bin/recreate.sh --org ORG [--period PERIOD] [--cache-dir DIR] [--reports-dir DIR] [--force]
#
# Environment overrides for direct usage:
#   ORG, PERIOD, CACHE_DIR, REPORTS_DIR, EXTRA_REPOS, FORCE
#
# EXTRA_REPOS is an optional space-separated list of "Owner/repo" pairs to
# fetch into the cache before generating the main organization's reports.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUBMODULE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

ORG="${ORG:-}"
PERIOD="${PERIOD:-last-year}"
CACHE_DIR="${CACHE_DIR:-${SUBMODULE_DIR}/cache}"
REPORTS_DIR="${REPORTS_DIR:-${SUBMODULE_DIR}/reports}"
EXTRA_REPOS="${EXTRA_REPOS:-}"
CONFIG=""
FORCE="${FORCE:-}"

usage() {
    awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$0"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --config)
            CONFIG="$2"
            shift 2
            ;;
        --org)
            ORG="$2"
            shift 2
            ;;
        --period)
            PERIOD="$2"
            shift 2
            ;;
        --cache-dir)
            CACHE_DIR="$2"
            shift 2
            ;;
        --reports-dir|--output-dir)
            REPORTS_DIR="$2"
            shift 2
            ;;
        --extra-repos)
            EXTRA_REPOS="$2"
            shift 2
            ;;
        --force)
            FORCE="1"
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown argument: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

PIXI="pixi run --manifest-path ${SUBMODULE_DIR}/pyproject.toml"

if [[ -n "${CONFIG}" ]]; then
    ${PIXI} github-analysis recreate \
        --config "${CONFIG}" \
        ${FORCE:+--force}
    exit 0
fi

if [[ -z "${ORG}" ]]; then
    echo "Error: --org or ORG is required unless --config is used." >&2
    usage >&2
    exit 2
fi

echo "=== github-analysis: recreate metrics ==="
echo "  org:          ${ORG}"
echo "  period:       ${PERIOD}"
echo "  cache:        ${CACHE_DIR}"
echo "  reports:      ${REPORTS_DIR}"
echo "  extra repos:  ${EXTRA_REPOS:-<none>}"
echo ""

echo "--- Step 1: fetch raw data for ${ORG} ---"
${PIXI} github-analysis fetch \
    --org "${ORG}" \
    --period "${PERIOD}" \
    --cache-dir "${CACHE_DIR}" \
    ${FORCE:+--force}

if [[ -n "${EXTRA_REPOS}" ]]; then
    echo ""
    echo "--- Step 1b: fetch related repositories ---"
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
fi

echo ""
echo "--- Step 2: generate reports ---"
${PIXI} github-analysis report \
    --org "${ORG}" \
    --cache-dir "${CACHE_DIR}" \
    --output-dir "${REPORTS_DIR}"

echo ""
echo "=== Done. Reports in ${REPORTS_DIR} ==="
