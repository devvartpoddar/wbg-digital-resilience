#!/usr/bin/env bash
# Fetch, clean, audit, load into Postgres and write the review sheets.
# Every stage is resumable and skips work already done, so re-running is cheap.
#
#   ./run.sh                 everything
#   ./run.sh --limit 3       a smoke run on the first three projects
#
# Data lands in the MAIN checkout's data/ (see src/paths.py), even when this is
# run from a board card's worktree. Override with WBG_DATA, the database with WBG_PG.
set -euo pipefail
cd "$(dirname "$0")"
PY="${WBG_PY:-/opt/yggdrasil/venvs/work/bin/python}"
[ -x "$PY" ] || PY=python3
LIMIT=()
if [ "${1:-}" = "--limit" ]; then LIMIT=(--limit "$2"); fi

step() { echo; echo "== $*"; "$PY" "$@"; }
step src/fetch.py "${LIMIT[@]}"
step src/clean.py
step src/audit.py | tail -3
step src/fetch_procurement.py "${LIMIT[@]}"
step src/clean_procurement.py
step src/audit_procurement.py | tail -3
step src/load_pg.py
step src/review_sheets.py
step src/run_report.py --topic "Pipeline run"
