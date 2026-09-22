"""End-to-end smoke tests -- actually invoke each package's CLI as a
subprocess (`python3 -m pt.cli ...`, same as a real user would), against
test_data/'s synthetic household, and just check the exit code and a
couple of expected output fragments.

This layer exists because tests/test_import.py, tests/test_projection.py,
and tests/test_roth_optimizer.py all call internal functions directly --
precise and fast, but blind to a broken argparse wiring (a wrong flag
name, a positional/optional ordering bug) since they never go through
cli.py's actual argument parsing at all. The --account flag's "must come
before the file list" behavior (an argparse quirk with the existing
nargs="+" positional -- see pt/cli.py's import subcommand) is exactly the
kind of thing only a real CLI invocation catches; that's the gap this
file covers. Keep it to a handful of "does the command still basically
work" checks, not a copy of the detailed internal-function tests above.
"""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from . import _helpers as h

REPO_ROOT = h.REPO_ROOT


def run_cli(module: str, *args, cwd=REPO_ROOT, timeout=60):
    """Runs `python3 -m {module} {args}` as a real subprocess (the exact
    way a user invokes any of this project's tools), from the repo root
    (so relative test_data/ paths resolve the same way the examples in
    README.md/test_data/README.md do). Returns the completed process."""
    return subprocess.run(
        [sys.executable, "-m", module, *args],
        cwd=cwd, capture_output=True, text=True, timeout=timeout,
    )


class PtCliSmokeTests(unittest.TestCase):
    def setUp(self):
        self._db = h.TempDB()
        self.db_path = self._db.__enter__()
        conn = h.seeded_connection(self.db_path)
        h.import_fidelity_household(conn)
        conn.close()

    def tearDown(self):
        self._db.__exit__(None, None, None)

    def test_project_runs_cleanly(self):
        result = run_cli(
            "pt.cli", "--db", str(self.db_path), "project",
            "--scenario", str(h.TEST_SCENARIO), "--profile", str(h.TEST_PROFILE),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Blended growth rate", result.stdout)

    def test_monte_carlo_runs_cleanly(self):
        result = run_cli(
            "pt.cli", "--db", str(self.db_path), "monte-carlo",
            "--scenario", str(h.TEST_SCENARIO), "--profile", str(h.TEST_PROFILE),
            "--trials", "20", "--seed", "1",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Success rate", result.stdout)

    def test_project_per_bucket_growth_prints_a_breakdown(self):
        result = run_cli(
            "pt.cli", "--db", str(self.db_path), "project",
            "--scenario", str(h.TEST_SCENARIO), "--profile", str(h.TEST_PROFILE),
            "--per-bucket-growth",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("household summary", result.stdout)
        self.assertIn("Per-bucket growth rate", result.stdout)
        self.assertIn("Taxable", result.stdout)

    def test_monte_carlo_per_bucket_growth_runs_cleanly(self):
        result = run_cli(
            "pt.cli", "--db", str(self.db_path), "monte-carlo",
            "--scenario", str(h.TEST_SCENARIO), "--profile", str(h.TEST_PROFILE),
            "--trials", "20", "--seed", "1", "--per-bucket-growth",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Success rate", result.stdout)
        self.assertIn("Per-bucket mean blended return", result.stdout)

    def test_report_runs_cleanly_and_embeds_projection_tab(self):
        with tempfile.TemporaryDirectory() as out_dir:
            xlsx = Path(out_dir) / "r.xlsx"
            txt = Path(out_dir) / "r.txt"
            result = run_cli("pt.cli", "--db", str(self.db_path), "report",
                              "--out", str(xlsx), "--text-out", str(txt),
                              "--scenario", str(h.TEST_SCENARIO), "--profile", str(h.TEST_PROFILE))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(xlsx.exists())
            self.assertTrue(txt.exists())
            # The workbook's Projection tab is only added when the scenario
            # projects cleanly -- and report.py must feed compute_blended_
            # growth_rate() the subclass-level allocation rows, not the
            # macro-level ones, or a scenario using the newer
            # returns.asset_classes shape (test_data/'s does) silently
            # drops the tab.
            import openpyxl
            wb = openpyxl.load_workbook(xlsx)
            self.assertIn("Projection", wb.sheetnames)

    def test_report_monte_carlo_flag_adds_tab_right_after_projection(self):
        with tempfile.TemporaryDirectory() as out_dir:
            xlsx = Path(out_dir) / "r.xlsx"
            txt = Path(out_dir) / "r.txt"
            result = run_cli("pt.cli", "--db", str(self.db_path), "report",
                              "--out", str(xlsx), "--text-out", str(txt),
                              "--scenario", str(h.TEST_SCENARIO), "--profile", str(h.TEST_PROFILE),
                              "--monte-carlo", "--trials", "20", "--seed", "1")
            self.assertEqual(result.returncode, 0, result.stderr)
            import openpyxl
            names = openpyxl.load_workbook(xlsx).sheetnames
            self.assertIn("Monte Carlo", names)
            self.assertEqual(names.index("Monte Carlo"), names.index("Projection") + 1)

    def test_report_per_bucket_growth_adds_a_breakdown_to_both_tabs(self):
        import openpyxl
        with tempfile.TemporaryDirectory() as out_dir:
            xlsx = Path(out_dir) / "r.xlsx"
            txt = Path(out_dir) / "r.txt"
            result = run_cli("pt.cli", "--db", str(self.db_path), "report", "--private",
                              "--out", str(xlsx), "--text-out", str(txt),
                              "--scenario", str(h.TEST_SCENARIO), "--profile", str(h.TEST_PROFILE),
                              "--monte-carlo", "--trials", "20", "--seed", "1", "--per-bucket-growth")
            self.assertEqual(result.returncode, 0, result.stderr)
            wb = openpyxl.load_workbook(xlsx)
            for sheet_name in ("Projection", "Monte Carlo"):
                col_a = [r[0] for r in wb[sheet_name].iter_rows(min_col=1, max_col=1, values_only=True)]
                self.assertTrue(any(v and "Per-bucket growth rate" in str(v) for v in col_a), sheet_name)
                self.assertTrue(any(v and str(v).strip().startswith("Taxable:") for v in col_a), sheet_name)

    def test_report_hypothetical_scenario_uses_its_own_accounts_not_the_snapshot(self):
        """`report` always reads the DATABASE snapshot for its Holdings/
        Allocation/IPS tabs -- but if --scenario configures its own
        hypothetical_accounts, the Projection/Monte Carlo tabs' growth
        math (including --per-bucket-growth) must come ENTIRELY from that
        made-up household, not from self.db_path's real (and completely
        unrelated) test-fidelity snapshot -- see build_workbook()'s
        docstring. A single 100%-US-Equity hypothetical account should
        blend to exactly that class's own expected_return (7.5% here),
        regardless of what the snapshot holds."""
        import openpyxl
        with tempfile.TemporaryDirectory() as d:
            scenario_path = Path(d) / "hypothetical.yaml"
            scenario_path.write_text("""
people:
  primary: {name: alex, birthdate: 1962-01-01}
retirement: {retirement_age: 65}
income: {target_spending: 60000}
inflation: {general: 2.5}
returns:
  asset_classes:
    US Equity: {expected_return: 7.5, volatility: 18}
hypothetical_accounts:
  - account_number: taxable1
    account_type: Brokerage
    owner: alex
    value: 500000
    asset_classes: {US Equity: 100}
""")
            xlsx = Path(d) / "r.xlsx"
            txt = Path(d) / "r.txt"
            result = run_cli("pt.cli", "--db", str(self.db_path), "report", "--private",
                              "--out", str(xlsx), "--text-out", str(txt),
                              "--scenario", str(scenario_path), "--per-bucket-growth")
            self.assertEqual(result.returncode, 0, result.stderr)
            ws = openpyxl.load_workbook(xlsx)["Projection"]
            col_a = [v for v in ws.iter_rows(min_col=1, max_col=1, values_only=True) for v in v]
            self.assertTrue(any(v and "Blended growth rate" in str(v) and "7.50%" in str(v) for v in col_a),
                             col_a)
            self.assertTrue(any(v and str(v).strip() == "Taxable: 7.50%" for v in col_a), col_a)
            # Allocation tab is unaffected -- still the real snapshot's household.
            alloc_owners = {r[1] for r in openpyxl.load_workbook(xlsx)["Allocation"].iter_rows(
                min_row=4, values_only=True)}
            self.assertNotIn("Alex", alloc_owners)

    def test_report_public_is_the_default_and_trims_the_shareable_view(self):
        import openpyxl
        with tempfile.TemporaryDirectory() as out_dir:
            xlsx = Path(out_dir) / "r.xlsx"
            txt = Path(out_dir) / "r.txt"
            result = run_cli("pt.cli", "--db", str(self.db_path), "report",
                              "--out", str(xlsx), "--text-out", str(txt),
                              "--scenario", str(h.TEST_SCENARIO), "--profile", str(h.TEST_PROFILE))
            self.assertEqual(result.returncode, 0, result.stderr)
            names = openpyxl.load_workbook(xlsx).sheetnames
            for dropped in ("Holdings", "Investment Expenses", "IPS Comparison",
                            "IPS Comparison (Macro)", "IPS by Account", "Unclassified"):
                self.assertNotIn(dropped, names)
            # Account Number column gone; decedent dates present (test_data's
            # scenario has inherited_accounts tied to held accounts).
            acct_header = [c.value for c in openpyxl.load_workbook(xlsx)["Accounts"][1]]
            self.assertNotIn("Account Number", acct_header)
            self.assertIn("Decedent Date of Death", acct_header)
            body = txt.read_text()
            self.assertNotIn("#...", body)
            self.assertNotIn("IPS TARGET", body)
            self.assertNotIn("EXPENSE SUMMARY", body)
            # source_file ("test-fidelity", see _helpers.import_fidelity_household)
            # is redacted to a file count, not shown verbatim.
            self.assertNotIn("test-fidelity", body)
            self.assertIn("<redacted> (1 file)", body)
            summary_a2 = openpyxl.load_workbook(xlsx)["Summary"]["A2"].value
            self.assertNotIn("test-fidelity", summary_a2)
            self.assertIn("<redacted>", summary_a2)

    def test_report_private_keeps_every_tab_and_the_account_number_column(self):
        import openpyxl
        with tempfile.TemporaryDirectory() as out_dir:
            xlsx = Path(out_dir) / "r.xlsx"
            txt = Path(out_dir) / "r.txt"
            result = run_cli("pt.cli", "--db", str(self.db_path), "report", "--private",
                              "--out", str(xlsx), "--text-out", str(txt),
                              "--scenario", str(h.TEST_SCENARIO), "--profile", str(h.TEST_PROFILE))
            self.assertEqual(result.returncode, 0, result.stderr)
            wb = openpyxl.load_workbook(xlsx)
            for kept in ("Holdings", "Investment Expenses", "IPS Comparison", "IPS Comparison (Macro)", "Unclassified"):
                self.assertIn(kept, wb.sheetnames)
            acct_header = [c.value for c in wb["Accounts"][1]]
            self.assertEqual(acct_header[0], "Account Number")
            self.assertIn("Decedent Date of Death", acct_header)
            # scenario dump keeps real account numbers in the private view
            proj_col_a = [r[0] for r in wb["Projection"].iter_rows(min_col=1, max_col=1, values_only=True)]
            self.assertFalse(any("account_number: <redacted>" in str(v) for v in proj_col_a if v))
            self.assertIn("test-fidelity", wb["Summary"]["A2"].value)
            self.assertIn("test-fidelity", txt.read_text())

    def test_import_with_missing_etrade_account_fails_clearly(self):
        """The exact ordering/flag issue caught by hand this session --
        --account must be accepted and, when omitted for an E*TRADE file,
        must fail with a clear message rather than a traceback."""
        result = run_cli("pt.cli", "--db", str(self.db_path), "import", str(h.ETRADE_CASEY))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("account number", result.stderr.lower())

    def test_import_multi_broker_with_account_flag_succeeds(self):
        result = run_cli(
            "pt.cli", "--db", str(self.db_path), "import",
            "--account", f"{h.ETRADE_CASEY.name}:{h.ETRADE_CASEY_ACCOUNT}",
            str(h.SCHWAB_JORDAN), "Jordan", str(h.ETRADE_CASEY), "Casey",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Imported snapshot", result.stdout)


class RothOptimizerCliSmokeTests(unittest.TestCase):
    def test_optimize_runs_cleanly_against_hypothetical_scenario(self):
        with tempfile.TemporaryDirectory() as d:
            scenario_path = Path(d) / "scenario.yaml"
            scenario_path.write_text("""
people:
  primary: {name: alex, birthdate: 1962-01-01}
retirement: {retirement_age: 65}
income: {target_spending: 60000}
inflation: {general: 2.5}
returns: {growth: 8.0, fixed_income: 4.0}
hypothetical_accounts:
  - account_number: trad1
    account_type: IRA
    owner: alex
    value: 500000
    pct_growth: 60
roth_conversions:
  - owner: alex
    start_date: 2027-01-01
    end_date: 2033-12-31
    constraints: {max_marginal_bracket: 24, max_conversion_per_year: 60000}
""")
            result = run_cli(
                "roth_optimizer.cli", "optimize", "--scenario", str(scenario_path),
                "--heir-tax-rate", "24", "--seed", "1", "--restarts", "2",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Optimized schedule", result.stdout)

    _SCATTER_SCENARIO = """
people:
  primary: {name: alex, birthdate: 1962-01-01}
retirement: {retirement_age: 65}
income: {target_spending: 60000}
inflation: {general: 2.5}
returns: {growth: 8.0, fixed_income: 4.0}
hypothetical_accounts:
  - account_number: trad1
    account_type: IRA
    owner: alex
    value: 500000
    pct_growth: 60
roth_conversions:
  - owner: alex
    start_date: 2027-01-01
    end_date: 2033-12-31
    constraints: {max_marginal_bracket: 24, max_conversion_per_year: 60000}
"""

    def test_optimize_records_the_html_chart_on_the_saved_scenarios_roth_conversions(self):
        import yaml
        with tempfile.TemporaryDirectory() as d:
            scenario_path = Path(d) / "scenario.yaml"
            scenario_path.write_text(self._SCATTER_SCENARIO)
            out_scenario = Path(d) / "out" / "scenario_optimized_24.yaml"
            chart = Path(d) / "out" / "charts" / "search.html"
            result = run_cli(
                "roth_optimizer.cli", "optimize", "--scenario", str(scenario_path),
                "--heir-tax-rate", "24", "--seed", "1", "--restarts", "2",
                "--out-scenario", str(out_scenario),
                "--save-iterations", str(Path(d) / "out" / "search.csv"),
                "--save-iterations", str(chart),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            saved = yaml.safe_load(out_scenario.read_text())
            # Relative to the saved scenario file's own directory, so the
            # two can be moved together; only the .html counts (not the .csv).
            self.assertEqual(saved["roth_conversions"][0]["scatter_chart"], "charts/search.html")
            self.assertTrue(chart.exists())

    def test_optimize_without_an_html_chart_records_no_scatter_chart_field(self):
        import yaml
        with tempfile.TemporaryDirectory() as d:
            scenario_path = Path(d) / "scenario.yaml"
            scenario_path.write_text(self._SCATTER_SCENARIO)
            out_scenario = Path(d) / "scenario_optimized_24.yaml"
            result = run_cli(
                "roth_optimizer.cli", "optimize", "--scenario", str(scenario_path),
                "--heir-tax-rate", "24", "--seed", "1", "--restarts", "2",
                "--out-scenario", str(out_scenario),
                "--save-iterations", str(Path(d) / "search.csv"),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            saved = yaml.safe_load(out_scenario.read_text())
            self.assertNotIn("scatter_chart", saved["roth_conversions"][0])

    def test_optimize_per_bucket_growth_prints_a_breakdown(self):
        with tempfile.TemporaryDirectory() as d:
            scenario_path = Path(d) / "scenario.yaml"
            scenario_path.write_text("""
people:
  primary: {name: alex, birthdate: 1962-01-01}
retirement: {retirement_age: 65}
income: {target_spending: 60000}
inflation: {general: 2.5}
returns:
  asset_classes:
    US Equity: {expected_return: 8.0, volatility: 18}
    US Bonds: {expected_return: 3.0, volatility: 7}
hypothetical_accounts:
  - account_number: trad1
    account_type: IRA
    owner: alex
    value: 500000
    asset_classes: {US Bonds: 100}
  - account_number: roth1
    account_type: Roth IRA
    owner: alex
    value: 50000
    asset_classes: {US Equity: 100}
roth_conversions:
  - owner: alex
    start_date: 2027-01-01
    end_date: 2033-12-31
    constraints: {max_marginal_bracket: 24, max_conversion_per_year: 60000}
""")
            result = run_cli(
                "roth_optimizer.cli", "optimize", "--scenario", str(scenario_path),
                "--heir-tax-rate", "24", "--seed", "1", "--restarts", "2", "--per-bucket-growth",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Optimized schedule", result.stdout)
            self.assertIn("Per-bucket growth rates", result.stdout)
            self.assertIn("Traditional (Alex: IRA): 3.00%", result.stdout)
            self.assertIn("Roth (Alex): 8.00%", result.stdout)

    def test_sweep_prints_a_comparison_table_without_declaring_a_winner(self):
        with tempfile.TemporaryDirectory() as d:
            scenario_path = Path(d) / "scenario.yaml"
            scenario_path.write_text("""
people:
  primary: {name: alex, birthdate: 1962-01-01}
retirement: {retirement_age: 65}
income: {target_spending: 60000}
inflation: {general: 2.5}
returns: {growth: 8.0, fixed_income: 4.0}
hypothetical_accounts:
  - account_number: trad1
    account_type: IRA
    owner: alex
    value: 500000
    pct_growth: 60
roth_conversions:
  - owner: alex
    start_date: 2027-01-01
    end_date: 2033-12-31
    constraints: {max_marginal_bracket: 24, max_conversion_per_year: 60000}
""")
            result = run_cli(
                "roth_optimizer.cli", "sweep", "--scenario", str(scenario_path),
                "--heir-tax-rates", "12,24,35", "--seed", "1", "--restarts", "1",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Heir tax rate sweep", result.stdout)
            for rate in ("12%", "24%", "35%"):
                self.assertIn(rate, result.stdout)
            # Deliberately no single "winner" declared -- see cmd_sweep()'s
            # docstring for why (--heir-tax-rate is an assumption, not
            # something the search optimizes; the raw dollar totals aren't
            # comparable across different rate assumptions).
            self.assertNotIn("Best rate", result.stdout)
            self.assertNotIn("Optimal rate", result.stdout)

    def test_sweep_out_dir_saves_one_file_pair_per_rate(self):
        with tempfile.TemporaryDirectory() as d:
            scenario_path = Path(d) / "my_scenario.yaml"
            scenario_path.write_text("""
people:
  primary: {name: alex, birthdate: 1962-01-01}
retirement: {retirement_age: 65}
income: {target_spending: 60000}
inflation: {general: 2.5}
returns: {growth: 8.0, fixed_income: 4.0}
hypothetical_accounts:
  - account_number: trad1
    account_type: IRA
    owner: alex
    value: 500000
    pct_growth: 60
roth_conversions:
  - owner: alex
    start_date: 2027-01-01
    end_date: 2033-12-31
    constraints: {max_marginal_bracket: 24, max_conversion_per_year: 60000}
""")
            out_dir = Path(d) / "out"
            result = run_cli(
                "roth_optimizer.cli", "sweep", "--scenario", str(scenario_path),
                "--heir-tax-rates", "12,24", "--seed", "1", "--restarts", "1", "--out-dir", str(out_dir),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            for rate_tag in ("12", "24"):
                self.assertTrue((out_dir / f"my_scenario_optimized_{rate_tag}.yaml").exists())
                self.assertTrue((out_dir / f"my_scenario_optimized_{rate_tag}_projection.txt").exists())

    def test_priority_compares_both_orderings_and_saves_both_variants(self):
        with tempfile.TemporaryDirectory() as d:
            scenario_path = Path(d) / "my_scenario.yaml"
            scenario_path.write_text("""
people:
  primary: {name: alex, birthdate: 1962-01-01}
  spouse: {name: sam, birthdate: 1962-01-01}
retirement: {retirement_age: 90, spouse_retirement_age: 90}
income: {target_spending: 0}
inflation: {general: 2.5}
returns: {growth: 8.0, fixed_income: 4.0}
hypothetical_accounts:
  - account_number: alex_trad
    account_type: IRA
    owner: alex
    value: 2000000
    pct_growth: 0
  - account_number: sam_trad
    account_type: IRA
    owner: sam
    value: 2000000
    pct_growth: 0
roth_conversions:
  - owner: alex
    start_date: 2026-01-01
    end_date: 2026-12-31
    annual_amount: 50000
    constraints: {max_marginal_bracket: 24}
  - owner: sam
    start_date: 2026-01-01
    end_date: 2026-12-31
    annual_amount: 2000000
    constraints: {max_marginal_bracket: 24}
""")
            out_dir = Path(d) / "out"
            result = run_cli(
                "roth_optimizer.cli", "priority", "--scenario", str(scenario_path),
                "--heir-tax-rate", "24", "--seed", "1", "--restarts", "1", "--out-dir", str(out_dir),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Conversion priority comparison", result.stdout)
            self.assertIn("alex prioritized:", result.stdout)
            self.assertIn("sam prioritized:", result.stdout)
            for owner in ("alex", "sam"):
                self.assertTrue((out_dir / f"my_scenario_priority_{owner}first.yaml").exists())
                self.assertTrue((out_dir / f"my_scenario_priority_{owner}first_projection.txt").exists())

    def test_priority_requires_exactly_two_owners(self):
        with tempfile.TemporaryDirectory() as d:
            scenario_path = Path(d) / "scenario.yaml"
            scenario_path.write_text("""
people:
  primary: {name: alex, birthdate: 1962-01-01}
retirement: {retirement_age: 65}
income: {target_spending: 60000}
inflation: {general: 2.5}
returns: {growth: 8.0, fixed_income: 4.0}
hypothetical_accounts:
  - account_number: trad1
    account_type: IRA
    owner: alex
    value: 500000
    pct_growth: 60
roth_conversions:
  - owner: alex
    start_date: 2027-01-01
    end_date: 2033-12-31
    constraints: {max_marginal_bracket: 24}
""")
            result = run_cli(
                "roth_optimizer.cli", "priority", "--scenario", str(scenario_path),
                "--heir-tax-rate", "24",
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("exactly two", result.stderr)

    def test_optimize_ira_distributions_jointly_searches_both_kinds(self):
        with tempfile.TemporaryDirectory() as d:
            scenario_path = Path(d) / "scenario.yaml"
            scenario_path.write_text("""
people:
  primary: {name: alex, birthdate: 1962-01-01}
retirement: {retirement_age: 90}
income: {target_spending: 0}
inflation: {general: 2.5}
returns: {growth: 8.0, fixed_income: 4.0}
hypothetical_accounts:
  - account_number: trad1
    account_type: IRA
    owner: alex
    value: 1000000
    pct_growth: 0
roth_conversions:
  - owner: alex
    start_date: 2026-01-01
    end_date: 2030-12-31
    constraints: {max_marginal_bracket: 24, max_conversion_per_year: 2000000}
ira_distributions:
  - owner: alex
    start_date: 2026-01-01
    end_date: 2030-12-31
    constraints: {max_marginal_bracket: 24, max_distribution_per_year: 40000}
""")
            result = run_cli(
                "roth_optimizer.cli", "optimize", "--scenario", str(scenario_path),
                "--heir-tax-rate", "24", "--seed", "1", "--restarts", "1", "--optimize-ira-distributions",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("alex (Roth conversion,", result.stdout)
            self.assertIn("alex (IRA distribution,", result.stdout)
            self.assertIn("No Roth conversions or IRA distributions at all:", result.stdout)

    def test_optimize_without_the_flag_ignores_ira_distributions(self):
        with tempfile.TemporaryDirectory() as d:
            scenario_path = Path(d) / "scenario.yaml"
            scenario_path.write_text("""
people:
  primary: {name: alex, birthdate: 1962-01-01}
retirement: {retirement_age: 90}
income: {target_spending: 0}
inflation: {general: 2.5}
returns: {growth: 8.0, fixed_income: 4.0}
hypothetical_accounts:
  - account_number: trad1
    account_type: IRA
    owner: alex
    value: 1000000
    pct_growth: 0
roth_conversions:
  - owner: alex
    start_date: 2026-01-01
    end_date: 2030-12-31
    constraints: {max_marginal_bracket: 24, max_conversion_per_year: 2000000}
ira_distributions:
  - owner: alex
    start_date: 2026-01-01
    end_date: 2030-12-31
    constraints: {max_marginal_bracket: 24, max_distribution_per_year: 40000}
""")
            result = run_cli(
                "roth_optimizer.cli", "optimize", "--scenario", str(scenario_path),
                "--heir-tax-rate", "24", "--seed", "1", "--restarts", "1",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("alex (Roth conversion,", result.stdout)
            self.assertNotIn("IRA distribution", result.stdout)

    def test_optimize_inherited_withdrawals_searches_the_planned_withdrawal_window(self):
        with tempfile.TemporaryDirectory() as d:
            scenario_path = Path(d) / "scenario.yaml"
            scenario_path.write_text("""
people:
  primary: {name: alex, birthdate: 1962-01-01}
retirement: {retirement_age: 90}
income: {target_spending: 0}
inflation: {general: 2.5}
returns: {growth: 8.0, fixed_income: 4.0}
hypothetical_accounts:
  - account_number: inh1
    account_type: Inherited IRA
    owner: alex
    value: 500000
    pct_growth: 0
inherited_accounts:
  - account_number: inh1
    type: traditional
    decedent_birthdate: 1940-01-01
    decedent_death_date: 2025-06-01
    beneficiary: alex
    planned_withdrawal:
      start_date: 2026-01-01
      end_date: 2034-12-31
      annual_amount: 0
""")
            result = run_cli(
                "roth_optimizer.cli", "optimize", "--scenario", str(scenario_path),
                "--heir-tax-rate", "24", "--seed", "1", "--restarts", "1",
                "--optimize-inherited-withdrawals",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("alex (Inherited withdrawal, account inh1,", result.stdout)
            self.assertIn("No Roth conversions or extra inherited-account withdrawals at all:", result.stdout)

    def test_optimize_without_the_flag_ignores_inherited_accounts_planned_withdrawal(self):
        with tempfile.TemporaryDirectory() as d:
            scenario_path = Path(d) / "scenario.yaml"
            scenario_path.write_text("""
people:
  primary: {name: alex, birthdate: 1962-01-01}
retirement: {retirement_age: 65}
income: {target_spending: 60000}
inflation: {general: 2.5}
returns: {growth: 8.0, fixed_income: 4.0}
hypothetical_accounts:
  - account_number: trad1
    account_type: IRA
    owner: alex
    value: 500000
    pct_growth: 60
  - account_number: inh1
    account_type: Inherited IRA
    owner: alex
    value: 500000
    pct_growth: 0
roth_conversions:
  - owner: alex
    start_date: 2027-01-01
    end_date: 2033-12-31
    constraints: {max_marginal_bracket: 24}
inherited_accounts:
  - account_number: inh1
    type: traditional
    decedent_birthdate: 1940-01-01
    decedent_death_date: 2025-06-01
    beneficiary: alex
    planned_withdrawal:
      start_date: 2026-01-01
      end_date: 2034-12-31
      annual_amount: 0
""")
            result = run_cli(
                "roth_optimizer.cli", "optimize", "--scenario", str(scenario_path),
                "--heir-tax-rate", "24", "--seed", "1", "--restarts", "1",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("alex (Roth conversion,", result.stdout)
            self.assertNotIn("Inherited withdrawal", result.stdout)

    def test_claim_age_searches_pia_monthly_and_declares_a_winner(self):
        with tempfile.TemporaryDirectory() as d:
            scenario_path = Path(d) / "scenario.yaml"
            scenario_path.write_text("""
people:
  primary: {name: alex, birthdate: 1962-01-01}
retirement: {retirement_age: 62}
income: {target_spending: 60000}
inflation: {general: 2.5}
returns: {growth: 8.0, fixed_income: 4.0}
hypothetical_accounts:
  - account_number: trad1
    account_type: IRA
    owner: alex
    value: 800000
    pct_growth: 60
social_security:
  alex: {pia_monthly: 2800}
""")
            result = run_cli(
                "roth_optimizer.cli", "claim-age", "--scenario", str(scenario_path),
                "--heir-tax-rate", "24", "--claim-ages", "62,70",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Best:", result.stdout)
            self.assertIn("alex", result.stdout)
            self.assertIn("no retirement.life_expectancy", result.stdout)

    def test_claim_age_requires_pia_monthly(self):
        with tempfile.TemporaryDirectory() as d:
            scenario_path = Path(d) / "scenario.yaml"
            scenario_path.write_text("""
people:
  primary: {name: alex, birthdate: 1962-01-01}
retirement: {retirement_age: 62}
income: {target_spending: 60000}
inflation: {general: 2.5}
returns: {growth: 8.0, fixed_income: 4.0}
hypothetical_accounts:
  - account_number: trad1
    account_type: IRA
    owner: alex
    value: 800000
    pct_growth: 60
social_security:
  alex: {monthly_benefit: 2000, claim_age: 67}
""")
            result = run_cli(
                "roth_optimizer.cli", "claim-age", "--scenario", str(scenario_path), "--heir-tax-rate", "24",
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("pia_monthly", result.stderr)


class ScenarioEngineCliSmokeTests(unittest.TestCase):
    def test_generate_and_run_against_test_data_scenario(self):
        with tempfile.TemporaryDirectory() as d:
            sweep_path = Path(d) / "sweep.yaml"
            scenarios_dir = Path(d) / "scenarios"
            results_dir = Path(d) / "results"
            sweep_path.write_text(f"""
base_scenario: {h.TEST_SCENARIO}
parameters:
  retirement.retirement_age: {{min: 63, max: 65, step: 2}}
""")
            gen = run_cli("scenario_engine.cli", "generate", "--sweep", str(sweep_path),
                          "--out-dir", str(scenarios_dir))
            self.assertEqual(gen.returncode, 0, gen.stderr)
            self.assertTrue(list(scenarios_dir.glob("*.yaml")))

            db_path = self._import_household(d)
            run = run_cli("scenario_engine.cli", "run", "--db", str(db_path),
                          "--scenarios-dir", str(scenarios_dir), "--out-dir", str(results_dir),
                          "--profile", str(h.TEST_PROFILE))
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertTrue((results_dir / "summary.csv").exists())

    def _import_household(self, tmp_dir):
        db_path = Path(tmp_dir) / "scenario_engine_test.db"
        conn = h.seeded_connection(db_path)
        h.import_fidelity_household(conn)
        conn.close()
        return db_path


if __name__ == "__main__":
    unittest.main()
