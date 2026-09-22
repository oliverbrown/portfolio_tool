"""Regression tests for the import layer (pt/importer.py + its Schwab/
E*TRADE siblings) -- broker detection, per-broker parsing correctness,
multi-file merging (Joint-account detection), and that everything a real
import produces classifies cleanly against the starter seed data.

Run: python3 -m unittest tests.test_import -v (or see tests/README.md for
running the whole suite at once).
"""
import unittest

from pt import importer
from pt.importer_etrade import EtradeImportError
from pt.importer_schwab import SchwabImportError

from . import _helpers as h


class DetectBrokerTests(unittest.TestCase):
    def test_detects_fidelity(self):
        self.assertEqual(importer.detect_broker(h.FIDELITY_JORDAN), "fidelity")

    def test_detects_schwab(self):
        self.assertEqual(importer.detect_broker(h.SCHWAB_JORDAN), "schwab")

    def test_detects_etrade(self):
        self.assertEqual(importer.detect_broker(h.ETRADE_CASEY), "etrade")


class FidelityImportTests(unittest.TestCase):
    """Guards against a regression in the existing Fidelity path while
    generalizing importer.py for multi-broker support -- these numbers
    should never change unless test_data/'s own CSVs do."""

    def test_two_file_household_merges_with_joint_detection(self):
        as_of_date, accounts, holdings, skipped = importer.parse_multiple_csvs(
            [(h.FIDELITY_JORDAN, "Jordan"), (h.FIDELITY_CASEY, "Casey")]
        )
        self.assertEqual(len(accounts), 11)
        self.assertEqual(len(skipped), 1)
        joint_account = skipped[0]["account_number"]
        self.assertEqual(accounts[joint_account]["owner"], "Joint")
        # Every other account keeps whichever file's owner it came from.
        non_joint_owners = {info["owner"] for num, info in accounts.items() if num != joint_account}
        self.assertEqual(non_joint_owners, {"Jordan", "Casey"})

    def test_holdings_have_no_zero_or_missing_values(self):
        _as_of, _accounts, holdings, _skipped = importer.parse_multiple_csvs(
            [(h.FIDELITY_JORDAN, "Jordan"), (h.FIDELITY_CASEY, "Casey")]
        )
        self.assertTrue(holdings)
        for hold in holdings:
            self.assertTrue(hold["symbol"])
            self.assertIsNotNone(hold["current_value"])
            self.assertGreater(hold["current_value"], 0)


class SchwabImportTests(unittest.TestCase):
    def test_multi_account_file_splits_correctly(self):
        as_of_date, accounts, holdings = importer.parse_csv(h.SCHWAB_JORDAN, broker="schwab")[1:]
        self.assertEqual(as_of_date, "2026-08-15")
        self.assertEqual(len(accounts), 2)
        types = {info["account_type"] for info in accounts.values()}
        self.assertEqual(types, {"Brokerage", "Roth IRA"})
        by_account = {}
        for hold in holdings:
            by_account.setdefault(hold["account_number"], []).append(hold)
        self.assertEqual(len(by_account), 2)
        for acct_holdings in by_account.values():
            self.assertGreaterEqual(len(acct_holdings), 2)  # at least one real position + cash

    def test_account_total_footer_is_not_a_holding(self):
        _as_of, _accounts, holdings = importer.parse_csv(h.SCHWAB_JORDAN, broker="schwab")[1:]
        symbols = {hold["symbol"] for hold in holdings}
        self.assertNotIn("Account Total", symbols)

    def test_cash_row_normalized_to_cash_symbol(self):
        _as_of, _accounts, holdings = importer.parse_csv(h.SCHWAB_JORDAN, broker="schwab")[1:]
        cash_rows = [hold for hold in holdings if hold["symbol"] == "CASH"]
        self.assertEqual(len(cash_rows), 1)
        self.assertGreater(cash_rows[0]["current_value"], 0)

    def test_missing_required_column_raises_schwab_import_error(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d:
            bad = Path(d) / "bad.csv"
            bad.write_text(
                '"Positions for Individual XXXX1234 as of 08:00 AM ET, 08/15/2026"\n\n'
                "Symbol,Quantity\nAAPL,10\n"
            )
            with self.assertRaises(SchwabImportError):
                importer.parse_csv(bad, broker="schwab")


class EtradeImportTests(unittest.TestCase):
    def test_requires_account_number(self):
        with self.assertRaises(EtradeImportError):
            importer.parse_csv(h.ETRADE_CASEY, broker="etrade")  # no account_number given

    def test_parses_with_account_number(self):
        _broker, as_of_date, accounts, holdings = importer.parse_csv(
            h.ETRADE_CASEY, account_number=h.ETRADE_CASEY_ACCOUNT, broker="etrade",
        )
        self.assertIsNone(as_of_date)  # E*TRADE's export has no embedded date
        self.assertEqual(list(accounts.keys()), [h.ETRADE_CASEY_ACCOUNT])
        self.assertTrue(holdings)
        for hold in holdings:
            self.assertEqual(hold["account_number"], h.ETRADE_CASEY_ACCOUNT)

    def test_cash_row_normalized_and_cost_basis_computed(self):
        _broker, _as_of, _accounts, holdings = importer.parse_csv(
            h.ETRADE_CASEY, account_number=h.ETRADE_CASEY_ACCOUNT, broker="etrade",
        )
        by_symbol = {hold["symbol"]: hold for hold in holdings}
        self.assertIn("CASH", by_symbol)
        # A real position (not cash) should have cost_basis_total computed
        # from quantity * price_paid, since E*TRADE only reports a
        # per-share cost, not a total -- see pt/importer_etrade.py.
        real_positions = [hold for sym, hold in by_symbol.items() if sym != "CASH"]
        self.assertTrue(real_positions)
        for hold in real_positions:
            self.assertIsNotNone(hold["cost_basis_total"])
            self.assertGreater(hold["cost_basis_total"], 0)


class MixedBrokerImportTests(unittest.TestCase):
    """The scenario test_data/README.md's combined example demonstrates:
    all three broker formats, mixed owners, one account genuinely
    duplicated across two Fidelity files -- imported and classified as one
    household, same as a real multi-broker household would be."""

    def test_full_household_imports_and_classifies_cleanly(self):
        with h.TempDB() as db_path:
            conn = h.seeded_connection(db_path)
            snapshot_id, accounts, holdings, skipped = h.import_full_household(conn)

            self.assertEqual(len(accounts), 14)
            self.assertEqual(len(skipped), 1)  # the Joint brokerage account

            from pt import classifier as classifier_mod
            unclassified = classifier_mod.list_unclassified(conn, snapshot_id)
            self.assertEqual(unclassified, [],
                              "every symbol in test_data/'s fixtures should already be seeded -- "
                              "if this fails, either a new fixture uses an unseeded symbol, or "
                              "data/classifications_seed.csv lost a row")

            rows, total = h.allocation_rows_and_total(conn, snapshot_id)
            self.assertGreater(total, 0)
            self.assertAlmostEqual(sum(r["value"] for r in rows), total, delta=0.01)
            classes = {r["asset_class"] for r in rows}
            self.assertIn("Cash", classes)  # Schwab + E*TRADE cash rows both landed here


if __name__ == "__main__":
    unittest.main()
