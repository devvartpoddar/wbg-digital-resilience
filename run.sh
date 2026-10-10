#!/usr/bin/env bash
# Everything: put data/ in its layout, fetch any cohort project not fetched
# before, rebuild every stage whose code or inputs changed, load Postgres,
# write review sheets and the run report. Unchanged work is skipped.
#
#   ./run.sh                 everything
#   ./run.sh --update        fetch every project again first
#
# For a selection (a project, country, region or approval year) use ./wbg:
#   ./wbg prepare --project P171528        ./wbg summary --country KE
#
# Data lands in the MAIN checkout's data/ (see src/paths.py), even when this is
# run from a board card's worktree. Override with WBG_DATA, the database with WBG_PG.
set -euo pipefail
cd "$(dirname "$0")"
exec ./wbg pipeline "$@"
