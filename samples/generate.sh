#!/bin/bash
#
# Regenerate every sample output in this directory from test_data/'s
# synthetic Jordan/Casey household -- no real data, and never touches your
# own ~/.portfolio_tool database (it imports into a throwaway one).
#
#   samples/generate.sh
#
# Steps, the same ones you'd run on your own household:
#   1. import test_data/'s four brokerage exports and set IPS targets
#   2. roth_optimizer optimize on demo_scenario.yaml (24% heir tax rate,
#      per-bucket growth) -- what scripts/run_optimizer.sh runs, plus --db
#   3. report --public and report --private on the optimized scenario --
#      what scripts/run_scenario.sh runs, plus --db
#
# The search and the Monte Carlo tabs use a fixed seed, so a rerun only
# changes the import timestamps -- unless the code's numbers changed.

set -euo pipefail

SAMPLES="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(dirname "$SAMPLES")"
cd "$REPO"

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
db="$work/demo.db"

pt() { python3 -m pt.cli --db "$db" "$@"; }

echo "== Importing test_data/ into a scratch database"
pt seed-classifications >/dev/null
pt import --account test_data/ETRADE_Positions_Aug-15-2026-casey.csv:X9999 \
    test_data/Portfolio_Positions_Aug-15-2026-jordan.csv "Jordan" \
    test_data/Portfolio_Positions_Aug-15-2026-casey.csv "Casey" \
    test_data/Schwab_Positions_Aug-15-2026-jordan.csv "Jordan" \
    test_data/ETRADE_Positions_Aug-15-2026-casey.csv "Casey" >/dev/null
pt targets set "Growth" 0.60 >/dev/null
pt targets set "Income" 0.30 >/dev/null
pt targets set "Liquidity" 0.05 >/dev/null
pt targets set "Alternatives" 0.05 >/dev/null

profile=test_data/retirement_profile.yaml
stem=samples/demo_scenario_optimized_24

echo "== Optimizing Roth conversions"
python3 -m roth_optimizer.cli --db "$db" optimize \
    --scenario samples/demo_scenario.yaml --profile "$profile" \
    --heir-tax-rate 24 --restarts 5 --seed 1 --per-bucket-growth \
    --out "${stem}_projection.txt" \
    --out-scenario "${stem}.yaml" \
    --save-iterations "${stem}_search.csv" \
    --save-iterations "${stem}_search.html" \
    --optimizer-projection-ages 87,90,93

for view in public private; do
    echo "== Writing the $view report"
    pt report --"$view" --scenario "${stem}.yaml" --profile "$profile" \
        --per-bucket-growth --monte-carlo --trials 1000 --seed 1 \
        --out "${stem}_${view}_report.xlsx" \
        --text-out "${stem}_${view}_report.txt" >/dev/null
done

echo "Done -- outputs are in samples/"
