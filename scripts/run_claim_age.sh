#!/bin/bash
#
# Compare Social Security claim ages via `roth_optimizer.cli claim-age` --
# a brute-force grid search over every scenario.social_security entry
# with pia_monthly configured (a flat monthly_benefit can't be recomputed
# for a different claim age -- see pt/social_security.py), reporting
# which combination wins:
#
#   scripts/run_claim_age.sh ~/.portfolio_tool/retirement_scenario_1.yaml
#
# writes, into the current directory (or the directory given as $3):
#
#   retirement_scenario_1_claimage_24.yaml
#   retirement_scenario_1_claimage_24_projection.txt
#
# (24 here is the heir tax rate -- an optional second argument, default
# 24; a fractional rate like 22.5 becomes "22p5" in the filename.)
#
# IMPORTANT: with no retirement.life_expectancy (or spouse_life_
# expectancy) configured, this search scores against an unbounded
# lifetime, so claiming LATER (more guaranteed dollars over a longer
# assumed lifetime) will almost always win -- a real property of an
# unbounded-lifetime comparison, not a bug. Set retirement.
# life_expectancy (and spouse_life_expectancy, for a couple) to the
# age(s) you actually want this scored against first. This is
# independent of scenario.mortality, which only affects `pt.cli
# project`/`report`/`monte-carlo`'s own detailed trajectory, not this
# search.
#
# --per-bucket-growth is ON by default for this script -- same convention
# as run_optimizer.sh -- set PER_BUCKET_GROWTH=0 to turn it off.
#
# Usage:  scripts/run_claim_age.sh <scenario.yaml> [heir_tax_rate] [output_dir]
# Env:
#   CLAIM_AGES         comma-separated ages to try per person (default: 62-70)
#   PER_BUCKET_GROWTH  1 (default, on) or 0 (off)
#   PT_REPO            path to this pt checkout -- `python3 -m
#                      roth_optimizer.cli` must run from here unless pt is
#                      pip-installed                        (default: this
#                                                             script's repo)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PT_REPO="${PT_REPO:-$(dirname "$SCRIPT_DIR")}"
PER_BUCKET_GROWTH="${PER_BUCKET_GROWTH:-1}"

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

ages_flag=""
ages_note="62-70 (default)"
if [ -n "${CLAIM_AGES:-}" ]; then
    ages_flag="--claim-ages $CLAIM_AGES"
    ages_note="$CLAIM_AGES"
fi

# Output name stem: scenario basename minus its .yaml/.yml extension, plus
# _claimage_<heir_tax_rate> (a "." in a fractional rate becomes "p" so
# the filename stays a single clean token, e.g. 22.5 -> 22p5).
stem="$(basename "$scenario")"
stem="${stem%.yaml}"
stem="${stem%.yml}"
rate_tag="${heir_tax_rate//./p}"
out_stem="${stem}_claimage_${rate_tag}"
prefix="$out_dir/$out_stem"

echo "Scenario      : $scenario"
echo "Heir tax rate : ${heir_tax_rate}%"
echo "Claim ages    : $ages_note"
echo "Per-bucket    : $bucket_note"
echo "Outputs       : ${prefix}.yaml, ${prefix}_projection.txt"
echo

cd "$PT_REPO"

python3 -m roth_optimizer.cli claim-age \
    --scenario "$scenario" \
    --heir-tax-rate "$heir_tax_rate" \
    $ages_flag $bucket_flag \
    --out-scenario "${prefix}.yaml" \
    --out "${prefix}_projection.txt"

echo
echo "Done -- files written to $out_dir/  [${out_stem}.yaml, ${out_stem}_projection.txt]"
