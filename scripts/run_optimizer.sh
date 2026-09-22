#!/bin/bash
#
# Run roth_optimizer against one scenario and save the winning schedule as
# a new scenario file, ready to feed into `pt.cli project`/`monte-carlo`/
# `report` (or scripts/run_scenario.sh) -- plus its year-by-year text
# projection. Output name is based on the scenario file's own name and the
# heir tax rate used:
#
#   scripts/run_optimizer.sh ~/.portfolio_tool/retirement_scenario_1.yaml
#
# writes, into the current directory (or the directory given as $3):
#
#   retirement_scenario_1_optimized_24.yaml
#   retirement_scenario_1_optimized_24_projection.txt
#   retirement_scenario_1_optimized_24_search.csv     (every schedule the search tried)
#   retirement_scenario_1_optimized_24_search.html    (same data, as a scatter chart)
#
# (24 here is the heir tax rate -- an optional second argument, default 24;
# a fractional rate like 22.5 becomes "22p5" in the filename.) The last two
# come from `optimize --save-iterations` (repeatable -- see roth_optimizer/
# README.md) -- lifetime taxes paid vs. after-tax estate value for every
# candidate schedule the pattern search evaluated, not just the winner.
#
# SWEEP MODE: give a comma-separated LIST of rates instead of one --
#
#   scripts/run_optimizer.sh ~/.portfolio_tool/retirement_scenario_1.yaml 10,12,22,24,32,35,37
#
# and this loops the optimizer once per rate (same scenario/profile/
# accounts, loaded once), still writing each rate's *_optimized_<rate>.yaml/
# _projection.txt as above, plus a side-by-side comparison table at the
# end. See `roth_optimizer.cli sweep --help` (or its cmd_sweep() docstring)
# for why that table deliberately does NOT declare a single "best" rate --
# --heir-tax-rate is an assumption, not something the search optimizes.
#
# --per-bucket-growth is ON by default for this script -- unlike
# `roth_optimizer.cli optimize`/`sweep`'s own opt-in default, since that's
# what this script is for. Set PER_BUCKET_GROWTH=0 to turn it off and
# search/report against one household-wide blended rate instead.
#
# --optimize-ira-distributions is OFF by default (unlike --per-bucket-
# growth above) -- most scenarios have no scenario.ira_distributions
# section at all, so there'd be nothing to jointly search anyway. Set
# OPTIMIZE_IRA_DISTRIBUTIONS=1 to turn it on and let the search also
# decide, dollar for dollar, between a Roth conversion and a plain
# taxable IRA distribution -- see 'roth_optimizer.cli optimize --help'.
#
# --optimize-inherited-withdrawals is likewise OFF by default -- set
# OPTIMIZE_INHERITED_WITHDRAWALS=1 to also search each already-configured
# inherited_accounts planned_withdrawal window (how much EXTRA, above
# that account's own legally-required 10-year-rule minimum, to withdraw
# each year -- see 'roth_optimizer.cli optimize --help').
#
# --optimizer-projection-ages defaults to 87,90,93 for this script's single-
# rate *_search.html chart -- draws each schedule's own trajectory through
# those ages alongside its life-expectancy point (see 'roth_optimizer.cli
# optimize --help' and README.md). Set PROJECTION_AGES to a different
# comma-separated list, or to an empty string to turn this off and get the
# plain two-reference-point chart. Ignored in sweep mode (not a `sweep`
# option).
#
# Usage:  scripts/run_optimizer.sh <scenario.yaml> [heir_tax_rate_or_csv_list] [output_dir]
# Env:
#   RESTARTS                      search restart count (per rate)  (default 5)
#   SEED                          search seed, for reproducibility (default 1)
#   PER_BUCKET_GROWTH             1 (default, on) or 0 (off)
#   OPTIMIZE_IRA_DISTRIBUTIONS    1 (on) or 0 (default, off)
#   OPTIMIZE_INHERITED_WITHDRAWALS 1 (on) or 0 (default, off)
#   PROJECTION_AGES               comma-separated ages for the *_search.html
#                                 trajectory lines (default 87,90,93; empty
#                                 to turn off)
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
PROJECTION_AGES="${PROJECTION_AGES-87,90,93}"

if [ $# -lt 1 ]; then
    echo "Usage: $0 <scenario.yaml> [heir_tax_rate_or_csv_list] [output_dir]" >&2
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

projection_ages_flag=""
projection_ages_note="off"
if [ -n "$PROJECTION_AGES" ]; then
    projection_ages_flag="--optimizer-projection-ages $PROJECTION_AGES"
    projection_ages_note="$PROJECTION_AGES"
fi

cd "$PT_REPO"

# Sweep mode: a comma in $2 means "a list of rates", not one rate --
# delegate the whole thing to `roth_optimizer.cli sweep`, which already
# loads the scenario/profile/accounts once and loops the search itself
# (faster and simpler than this script looping its own single-rate case).
case "$heir_tax_rate" in
    *,*)
        echo "Scenario      : $scenario"
        echo "Heir tax rates: $heir_tax_rate (sweep)"
        echo "Per-bucket    : $bucket_note"
        echo "IRA dist.     : $ira_note"
        echo "Inherited wd. : $inherited_note"
        echo "Output dir    : $out_dir"
        echo "Settings      : restarts=$RESTARTS seed=$SEED"
        echo
        python3 -m roth_optimizer.cli sweep \
            --scenario "$scenario" \
            --heir-tax-rates "$heir_tax_rate" \
            --restarts "$RESTARTS" --seed "$SEED" \
            $bucket_flag $ira_flag $inherited_flag \
            --out-dir "$out_dir"
        exit 0
        ;;
esac

# Output name stem: scenario basename minus its .yaml/.yml extension, plus
# _optimized_<heir_tax_rate> (a "." in a fractional rate becomes "p" so
# the filename stays a single clean token, e.g. 22.5 -> 22p5).
stem="$(basename "$scenario")"
stem="${stem%.yaml}"
stem="${stem%.yml}"
rate_tag="${heir_tax_rate//./p}"
out_stem="${stem}_optimized_${rate_tag}"
prefix="$out_dir/$out_stem"

echo "Scenario      : $scenario"
echo "Heir tax rate : ${heir_tax_rate}%"
echo "Per-bucket    : $bucket_note"
echo "IRA dist.     : $ira_note"
echo "Inherited wd. : $inherited_note"
echo "Age projs.    : $projection_ages_note"
echo "Outputs       : ${prefix}.yaml, ${prefix}_projection.txt"
echo "Settings      : restarts=$RESTARTS seed=$SEED"
echo

python3 -m roth_optimizer.cli optimize \
    --scenario "$scenario" \
    --heir-tax-rate "$heir_tax_rate" \
    --restarts "$RESTARTS" --seed "$SEED" \
    $bucket_flag $ira_flag $inherited_flag \
    --out "${prefix}_projection.txt" \
    --out-scenario "${prefix}.yaml" \
    --save-iterations "${prefix}_search.csv" \
    --save-iterations "${prefix}_search.html" \
    $projection_ages_flag

echo
echo "Done -- files written to $out_dir/  [${out_stem}.yaml, ${out_stem}_projection.txt,"
echo "                                    ${out_stem}_search.csv, ${out_stem}_search.html]"
