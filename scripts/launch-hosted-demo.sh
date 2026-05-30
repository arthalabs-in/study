#!/usr/bin/env bash
set -euo pipefail

export STUDY_SKIP_AUTO_SETUP="${STUDY_SKIP_AUTO_SETUP:-1}"
export STUDY_DOCS_DIR="${STUDY_DOCS_DIR:-/app/demo}"

demo_file="${STUDY_TUI_DEMO_FILE:-/app/demo/leph101.pdf}"

exec study-tui "$demo_file"
