# Quick Start

Five minutes to a rebalancing report and a retirement projection, using the
synthetic data in [`test_data/`](test_data/) so nothing here touches your
real accounts. See [`README.md`](README.md) for the full reference once
you're past this.

## 1. Install

Requires Python 3.9+.

```bash
git clone <this-repo-url> portfolio_tool
cd portfolio_tool
pip3 install -r requirements.txt
```

## 2. Load starter data and import a portfolio

Every command below points `--db` at a scratch database in `/tmp`, so your
real `~/.portfolio_tool/portfolio.db` (used when you drop `--db`) is never
touched.

```bash
# Seed ~40 common index funds/ETFs so most holdings classify automatically
python3 -m pt.cli --db /tmp/pt_demo.db seed-classifications

# Import the synthetic two-person household from test_data/
python3 -m pt.cli --db /tmp/pt_demo.db import \
  test_data/Portfolio_Positions_Aug-15-2026-jordan.csv "Jordan" \
  test_data/Portfolio_Positions_Aug-15-2026-casey.csv "Casey" \
  --debug
```

`--debug` prints the account list it found and how it inferred each
account's type — worth a look. Notice `690012659` ("JOINT WROS - TOD")
appeared in both files and got automatically tagged `Joint`.

## 3. Set your allocation targets (IPS)

These are household-level fractions and should sum to ~1.0.

```bash
python3 -m pt.cli --db /tmp/pt_demo.db targets set "Growth" 0.60
python3 -m pt.cli --db /tmp/pt_demo.db targets set "Income" 0.30
python3 -m pt.cli --db /tmp/pt_demo.db targets set "Liquidity" 0.05
python3 -m pt.cli --db /tmp/pt_demo.db targets set "Alternatives" 0.05
```

## 4. Run the rebalancing report

```bash
python3 -m pt.cli --db /tmp/pt_demo.db report \
  --out /tmp/pt_demo_report.xlsx --text-out /tmp/pt_demo_report.txt
```

This prints the report to the screen and writes both a text copy and an
Excel workbook (one tab per view: allocation, by-account, drift vs.
targets, holdings, investment expenses).

## 5. Run a retirement growth projection

```bash
python3 -m pt.cli --db /tmp/pt_demo.db project \
  --profile test_data/retirement_profile.yaml \
  --scenario test_data/retirement_scenario.yaml
```

This simulates the synthetic household year by year to age 100 — Social
Security, RMDs, an inherited-IRA 10-year drawdown, Medicare premiums, and
a 0.75%/year advisory fee are all already configured in
`test_data/retirement_scenario.yaml`. Open that file to see how each
piece is set up; it's meant to be read alongside the "Retirement scenario"
and "Growth projection" sections of `README.md`.

## Next steps, with your own data

1. Download your own export: on Fidelity.com, **Accounts & Trade →
   Positions → Download**. See "Getting your Fidelity export" in
   `README.md`.
2. Repeat steps 2-5 above against that file, dropping `--db` (and
   `--profile`/`--scenario`, once you've created your own
   `~/.portfolio_tool/retirement_profile.yaml` and
   `retirement_scenario.yaml` — see "Retirement planning profile" and
   "Retirement scenario" in `README.md`).
3. For sweeping many scenarios at once or searching for an optimal set of
   parameters, see `scenario_engine/README.md`.

**Only want to try `project`/`monte-carlo`, not the rebalancing report?**
You can skip the CSV/import steps above entirely — describe a made-up
household directly in a scenario file's `hypothetical_accounts` section
instead of importing real holdings. See "Hypothetical accounts" in
`README.md`.
