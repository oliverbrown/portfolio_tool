#!/bin/bash
#
# Compare Roth conversion priority (you-first vs. spouse-first) via
# `roth_optimizer.cli priority` -- runs the search twice, once with each
# owner's roth_conversions entries given first claim on any shared
# household bracket ceiling, and reports which ordering wins:
#
#   scripts/run_priority.sh ~/.portfolio_tool/retirement_scenario_1.yaml
#
# writes, into the current directory (or the directory given as $3):
#
#   retirement_scenario_1_priority_alexfirst.yaml
#   retirement_scenario_1_priority_alexfirst_projection.txt
#   retirement_scenario_1_priority_samfirst.yaml
#   retirement_scenario_1_priority_samfirst_projection.txt
#
# (owner names come from the scenario's own scenario.roth_conversions --
# it must have entries from exactly two different owners to compare.)
#
# --per-bucket-growth is ON by default for this script -- same convention
# as run_optimizer.sh -- set PER_BUCKET_GROWTH=0 to turn it off.
#
# --optimize-ira-distributions is OFF by default -- set
# OPTIMIZE_IRA_DISTRIBUTIONS=1 to also jointly search scenario.
# ira_distributions windows for each ordering (NOTE: reordering only
# affects roth_conversions' priority; ira_distributions entries keep
# their own scenario order regardless of which owner is prioritized).
#
# --optimize-inherited-withdrawals is likewise OFF by default -- set
# OPTIMIZE_INHERITED_WITHDRAWALS=1 to also search each already-configured
# inherited_accounts planned_withdrawal window for each ordering (also
# unaffected by which owner is prioritized).
#
# Usage:  scripts/run_priority.sh <scenario.yaml> [heir_tax_rate] [output_dir]
# Env:
#   RESTARTS                      search restart count (per ordering) (default 5)
#   SEED                          search seed, reused for both orderings (default 1)
#   PER_BUCKET_GROWTH             1 (default, on) or 0 (off)
#   OPTIMIZE_IRA_DISTRIBUTIONS    1 (on) or 0 (default, off)
#   OPTIMIZE_INHERITED_WITHDRAWALS 1 (on) or 0 (default, off)
#   PT_REPO                       path to this pt checkout -- `python3 -m
#                                 roth_optimizer.cli` must run from here
#                                 unless pt is pip-installed  (default: this
#                                                               script's repo)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PT_REPO="${PT_REPO:-$(dirname "$SCRIPT_DIR")}"
RESTARTS="${RESTARTS:-5}"
SEED="${SEED:-1}"
PER_BUCKET_GROWTH="${PER_BUCKET_GROWTH:-1}"
OPTIMIZE_IRA_DISTRIBUTIONS="${OPTIMIZE_IRA_DISTRIBUTIONS:-0}"
OPTIMIZE_INHERITED_WITHDRAWALS="${OPTIMIZE_INHERITED_WITHDRAWALS:-0}"

if [ $# -lt 1 ]; then
    echo "Usage: $0 <scenario.yaml> [heir_tax_rate] [output_dir]" >&2
    exit 1
fi

# Resolve the scenario to an absolute path BEFORE cd-ing into the repo, so
# the outputs land where the user expects regardless of where
# roth_optimizer.cli runs from.
scenario="$1"
[ -f "$scenario" ] || { echo "Scenario file not found: $scenario" >&2; exit 1; }
scenario="$(cd "$(dirname "$scenario")" && pwd)/$(basename "$scenario")"

heir_tax_rate="${2:-24}"

out_dir="${3:-.}"
mkdir -p "$out_dir"
out_dir="$(cd "$out_dir" && pwd)"

[ -f "$PT_REPO/roth_optimizer/cli.py" ] || {
    echo "pt checkout not found at: $PT_REPO (set \$PT_REPO, or run roth_optimizer.cli --help to check)" >&2
    exit 1
}

# A plain string (not an array) -- macOS's default bash (3.2) treats an
# empty array as an unbound variable under `set -u` when expanded with
# "${arr[@]}", which would abort the script whenever this flag is off.
bucket_flag=""
bucket_note="off"
if [ "$PER_BUCKET_GROWTH" = "1" ]; then
    bucket_flag="--per-bucket-growth"
    bucket_note="on (default)"
fi

ira_flag=""
ira_note="off (default)"
if [ "$OPTIMIZE_IRA_DISTRIBUTIONS" = "1" ]; then
    ira_flag="--optimize-ira-distributions"
    ira_note="on"
fi

inherited_flag=""
inherited_note="off (default)"
if [ "$OPTIMIZE_INHERITED_WITHDRAWALS" = "1" ]; then
    inherited_flag="--optimize-inherited-withdrawals"
    inherited_note="on"
fi

echo "Scenario      : $scenario"
echo "Heir tax rate : ${heir_tax_rate}%"
echo "Per-bucket    : $bucket_note"
echo "IRA dist.     : $ira_note"
echo "Inherited wd. : $inherited_note"
echo "Output dir    : $out_dir"
echo "Settings      : restarts=$RESTARTS seed=$SEED"
echo

cd "$PT_REPO"

python3 -m roth_optimizer.cli priority \
    --scenario "$scenario" \
    --heir-tax-rate "$heir_tax_rate" \
    --restarts "$RESTARTS" --seed "$SEED" \
    $bucket_flag $ira_flag $inherited_flag \
    --out-dir "$out_dir"
