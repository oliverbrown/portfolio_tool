# Synthetic test data

Everything in this directory is **entirely fictional** — a made-up household
("Jordan" and "Casey", no real people), fake account numbers, fake dollar
amounts. It's here so you (or anyone trying `pt`) can see the whole tool
work end to end without touching real financial data, and so regressions
have something concrete to run against.

It mirrors the shape of a real household's data, sized similarly to what
the tool was originally built around:

- **13 accounts** across two owners plus one joint account, covering every
  account type `pt` recognizes: Brokerage (taxable), 401(k), Rollover IRA,
  Roth IRA, Inherited IRA, Inherited Roth IRA, HSA
- **≤6 securities per account** (real real-world portfolios are rarely
  more concentrated than that; the tool's own limit for this test data was
  10)
- Two Fidelity-format source CSVs (`...-jordan.csv`, `...-casey.csv`) with
  one account (`690012659`, "JOINT WROS - TOD") deliberately appearing in
  **both** files with the same account number, to exercise `pt`'s
  automatic "appears in >1 file → owned by Joint" detection
- One Schwab-format CSV (`Schwab_Positions_...`), demonstrating that
  format's multiple-accounts-in-one-file layout (an "Individual" and a
  "Roth IRA" for Jordan)
- One E*TRADE-format CSV (`ETRADE_Positions_...`), demonstrating that
  format's single-account-per-file layout (which needs `--account` to
  supply an account number, since the file itself never reveals one --
  see "Try it" below)
- A matching `retirement_profile.yaml` and `retirement_scenario.yaml`,
  including the two "inherited" accounts (one Traditional, one Roth) and
  an active `advisory_expense` setting, so `project` has something
  realistic to run against too

## Files

| File | What it's for |
|---|---|
| `Portfolio_Positions_Aug-15-2026-jordan.csv` | Jordan's synthetic Fidelity-format export |
| `Portfolio_Positions_Aug-15-2026-casey.csv` | Casey's synthetic Fidelity-format export |
| `Schwab_Positions_Aug-15-2026-jordan.csv` | Jordan's synthetic Schwab-format export (2 accounts in one file) |
| `ETRADE_Positions_Aug-15-2026-casey.csv` | Casey's synthetic E*TRADE-format export (1 account, no account number in the file) |
| `retirement_profile.yaml` | Fictional household identity (names/birthdates) |
| `retirement_scenario.yaml` | Fictional "what if" assumptions for `project` |

## Try it (uses a scratch database — never your real one)

```bash
python3 -m pt.cli --db /tmp/pt_demo.db seed-classifications
python3 -m pt.cli --db /tmp/pt_demo.db import \
  --account test_data/ETRADE_Positions_Aug-15-2026-casey.csv:X9999 \
  test_data/Portfolio_Positions_Aug-15-2026-jordan.csv "Jordan" \
  test_data/Portfolio_Positions_Aug-15-2026-casey.csv "Casey" \
  test_data/Schwab_Positions_Aug-15-2026-jordan.csv "Jordan" \
  test_data/ETRADE_Positions_Aug-15-2026-casey.csv "Casey" \
  --debug
# --account (and --broker, if you ever need it) must come BEFORE the file
# list -- an argparse quirk, not a pt-specific rule. Broker format itself
# (Fidelity/Schwab/E*TRADE) is auto-detected per file, no flag needed for
# that part.

python3 -m pt.cli --db /tmp/pt_demo.db targets set "Growth" 0.60
python3 -m pt.cli --db /tmp/pt_demo.db targets set "Income" 0.30
python3 -m pt.cli --db /tmp/pt_demo.db targets set "Liquidity" 0.05
python3 -m pt.cli --db /tmp/pt_demo.db targets set "Alternatives" 0.05

python3 -m pt.cli --db /tmp/pt_demo.db report \
  --out /tmp/pt_demo_report.xlsx --text-out /tmp/pt_demo_report.txt

python3 -m pt.cli --db /tmp/pt_demo.db project \
  --profile test_data/retirement_profile.yaml \
  --scenario test_data/retirement_scenario.yaml
```

See [`QUICKSTART.md`](../QUICKSTART.md) at the repo root for the same walkthrough
with explanations at each step.

## Regenerating

The Fidelity-format CSVs were produced by a small generator script (not
checked in -- it's just a one-off), so if you want to tweak the
household, hand-edit the CSVs directly. Column layout must match each
format's own parser:
- Fidelity: `pt/importer.py`'s `COLUMN_ALIASES` (`Account Number, Account
  Name, Symbol, Description, Quantity, Last Price, Current Value, Cost
  Basis Total, Percent Of Account`).
- Schwab: `pt/importer_schwab.py`'s `TITLE_RE`/`COLUMN_ALIASES` -- each
  account needs its own `"Positions for TYPE NUMBER as of TIME,
  DATE"` title line before its header+data rows.
- E*TRADE: `pt/importer_etrade.py`'s `COLUMN_ALIASES` -- one account per
  file, no account-identifying column at all (see `--account` above).
