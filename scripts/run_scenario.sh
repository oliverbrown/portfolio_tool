#!/bin/bash
#
# Run project + monte-carlo + report (both private and public) against one
# scenario, in a single go. Every output file is named after the scenario
# file:
#
#   scripts/run_scenario.sh ~/.portfolio_tool/retirement_scenario_1.yaml
#
# writes, into the current directory (or the directory given as $2):
#
#   retirement_scenario_1_project.png          retirement_scenario_1_project.xlsx
#   retirement_scenario_1_mc.png                retirement_scenario_1_mc.xlsx
#   retirement_scenario_1_private_report.xlsx   retirement_scenario_1_private_report.txt
#   retirement_scenario_1_public_report.xlsx    retirement_scenario_1_public_report.txt
#
# `project` and `monte-carlo` use pt.cli's repeatable --out (one invocation
# each, not two). `report` reads the current database snapshot for its
# allocation/rebalancing sections and uses --scenario for its embedded
# Projection + Monte Carlo tabs (run with --monte-carlo, so both
# *_report.xlsx files also carry the fan chart) -- so each is skipped, with
# a warning, if there's no snapshot yet (e.g. a scenario using
# hypothetical_accounts on a machine with no import). TRIALS/SEED feed the
# standalone monte-carlo run and both reports' Monte Carlo tabs. `report`
# runs once with --private (the full workbook) and once with --public (the
# shareable one -- see `pt report --help` for what that drops/redacts).
#
# Usage:  scripts/run_scenario.sh <scenario.yaml> [output_dir]
# Env:
#   TRIALS   monte-carlo trial count               (default 1000)
#   SEED     monte-carlo seed, for reproducibility (default 1)
#   PT_REPO  path to this pt checkout -- `python3 -m pt.cli` must run from
#            here unless pt is pip-installed       (default: this script's repo)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PT_REPO="${PT_REPO:-$(dirname "$SCRIPT_DIR")}"
TRIALS="${TRIALS:-1000}"
SEED="${SEED:-1}"

if [ $# -lt 1 ]; then
    echo "Usage: $0 <scenario.yaml> [output_dir]" >&2
    exit 1
fi

# Resolve everything to absolute paths BEFORE cd-ing into the repo, so the
# outputs land where the user expects regardless of where pt.cli runs from.
scenario="$1"
[ -f "$scenario" ] || { echo "Scenario file not found: $scenario" >&2; exit 1; }
scenario="$(cd "$(dirname "$scenario")" && pwd)/$(basename "$scenario")"

out_dir="${2:-.}"
mkdir -p "$out_dir"
out_dir="$(cd "$out_dir" && pwd)"

[ -f "$PT_REPO/pt/cli.py" ] || {
    echo "pt checkout not found at: $PT_REPO (set \$PT_REPO, or run pt.cli --help to check)" >&2
    exit 1
}

# Output name stem: scenario basename minus its .yaml/.yml extension.
stem="$(basename "$scenario")"
stem="${stem%.yaml}"
stem="${stem%.yml}"
prefix="$out_dir/$stem"

echo "Scenario : $scenario"
echo "Outputs  : ${prefix}_{project,mc}.{png,xlsx}, ${prefix}_{private,public}_report.{xlsx,txt}"
echo "Settings : trials=$TRIALS seed=$SEED"
echo

cd "$PT_REPO"

python3 -m pt.cli project \
    --scenario "$scenario" \
    --out "${prefix}_project.png" \
    --out "${prefix}_project.xlsx"

python3 -m pt.cli monte-carlo \
    --scenario "$scenario" \
    --trials "$TRIALS" --seed "$SEED" \
    --out "${prefix}_mc.png" \
    --out "${prefix}_mc.xlsx"

echo
if python3 -m pt.cli report \
    --scenario "$scenario" \
    --private \
    --verbose \
    --per-bucket-growth \
    --monte-carlo --trials "$TRIALS" --seed "$SEED" \
    --out "${prefix}_private_report.xlsx" \
    --text-out "${prefix}_private_report.txt"; then
    private_report_note="private_report.{xlsx,txt}"
else
    echo "  (private report skipped -- needs a database snapshot; see 'pt.cli import')" >&2
    private_report_note="(private report skipped -- no snapshot)"
fi

echo
if python3 -m pt.cli report \
    --scenario "$scenario" \
    --public \
    --monte-carlo --trials "$TRIALS" --seed "$SEED" \
    --out "${prefix}_public_report.xlsx" \
    --text-out "${prefix}_public_report.txt"; then
    public_report_note="public_report.{xlsx,txt}"
else
    echo "  (public report skipped -- needs a database snapshot; see 'pt.cli import')" >&2
    public_report_note="(public report skipped -- no snapshot)"
fi

echo
echo "Done -- files written to $out_dir/  [project.{png,xlsx}, mc.{png,xlsx}, $private_report_note, $public_report_note]"
