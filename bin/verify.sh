#!/usr/bin/env bash
# Verify generated reports match the reference data snapshot.
#
# Usage:
#   bin/verify.sh [<reference_dir> [<generated_dir>]]
#
# Defaults:
#   reference_dir = ./data
#   generated_dir = ./reports
#
# Run recreate.sh first to generate the reports.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUBMODULE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

REF_DIR="${1:-${SUBMODULE_DIR}/data}"
GEN_DIR="${2:-${SUBMODULE_DIR}/reports}"

PIXI="pixi run --manifest-path ${SUBMODULE_DIR}/pyproject.toml"

echo "=== github-analysis: verify ==="
echo "  reference: ${REF_DIR}"
echo "  generated: ${GEN_DIR}"
echo ""

${PIXI} python "${SCRIPT_DIR}/compare.py" "${REF_DIR}" "${GEN_DIR}"
