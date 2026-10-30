# Sample results

Real output from this tool, run on [`test_data/`](../test_data/)'s
**entirely fictional** household (Jordan and Casey, fake account numbers,
fake balances), so you can see what you'll get before importing your own
accounts.

The household: about **$2.2M across 13 accounts** -- every account type
`pt` recognizes, including roughly $1M in Traditional IRA/401(k) money,
which is what makes Roth conversions worth searching. Both owners retire in
2031, leaving years of lower income before Social Security and RMDs to
convert in.

## Files

| File | What it is |
|---|---|
| [`demo_scenario.yaml`](demo_scenario.yaml) | The input: `test_data/retirement_scenario.yaml` plus a `roth_conversions` window for each owner (24% bracket cap, IRMAA tier 2) |
| [`demo_scenario_optimized_24.yaml`](demo_scenario_optimized_24.yaml) | The optimizer's output: the same scenario with the winning year-by-year conversion amounts filled in |
| [`demo_scenario_optimized_24_projection.txt`](demo_scenario_optimized_24_projection.txt) | Year-by-year projection of the winning schedule |
| [`demo_scenario_optimized_24_search.html`](demo_scenario_optimized_24_search.html) | Interactive chart of every schedule the search tried -- lifetime taxes vs. after-tax estate value. Download and open in a browser to hover |
| [`demo_scenario_optimized_24_search.csv`](demo_scenario_optimized_24_search.csv) | The same 3,028 search points as a CSV |
| [`demo_scenario_optimized_24_public_report.xlsx`](demo_scenario_optimized_24_public_report.xlsx) / [`.txt`](demo_scenario_optimized_24_public_report.txt) | `report --public` (the default): the shareable view -- no account numbers, holdings, or IPS targets |
| [`demo_scenario_optimized_24_private_report.xlsx`](demo_scenario_optimized_24_private_report.xlsx) / [`.txt`](demo_scenario_optimized_24_private_report.txt) | `report --private`: every tab, including Holdings, Holdings by Ticker, Investment Expenses and IPS comparisons (account numbers masked to their last 4 digits) |
| [`generate.sh`](generate.sh) | Rebuilds everything above |

Both workbooks include **Projection** and **Monte Carlo** tabs for the
optimized scenario (per-bucket growth, 1,000 trials) and an **Optimizer
Search** tab that redraws the search chart natively in Excel and links to
the HTML version next to it.

## What the optimizer found

At a 24% heir tax rate, with each tax bucket growing at its own mix's rate:

| | After-tax estate value |
|---|---|
| No Roth conversions | $10,739,445 |
| Optimized schedule | $11,522,183 |
| **Improvement** | **$782,738** |

The winning schedule converts most of Casey's Traditional money early --
about $571,000 over 2031-2035 -- and about $20,555 a year of Jordan's in
2036-2040. See the projection file for the year-by-year detail, including
where the 24% bracket and IRMAA caps trimmed a year's amount.

## Regenerating

```bash
samples/generate.sh
```

It imports `test_data/` into a throwaway database (never your own
`~/.portfolio_tool`), runs the same optimizer and report commands as
`scripts/run_optimizer.sh` and `scripts/run_scenario.sh`, and overwrites
the files here. The search and Monte Carlo use a fixed seed, so a rerun
changes only the import timestamps unless the code's numbers changed.
