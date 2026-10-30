"""IPS target consistency checks and the subclass comparison's handling of
macros with no subclass split (see pt/ips.py's module docstring)."""
import unittest

from pt import db as db_mod
from pt import ips

from . import _helpers as h


class CheckTargetsTests(unittest.TestCase):
    def test_consistent_targets_have_no_warnings(self):
        macro = {"Growth": 0.60, "Income": 0.40}
        sub = {"US Equity": 0.45, "International Equity": 0.15, "US Bonds": 0.40}
        self.assertEqual(ips.check_targets(macro, sub), [])

    def test_macro_total_and_subclass_mismatches_are_reported(self):
        macro = {"Growth": 0.70, "Income": 0.20, "Liquidity": 0.10, "Alternatives": 0.05}
        sub = {"US Equity": 0.50, "International Equity": 0.15, "US Bonds": 0.18, "International Bonds": 0.06}
        warnings = ips.check_targets(macro, sub)
        self.assertEqual(len(warnings), 3)
        self.assertIn("Macro targets sum to 105.0%", warnings[0])
        self.assertIn("Growth's subclass targets sum to 65.0%, 5.0% short of", warnings[1])
        self.assertIn("Income's subclass targets sum to 24.0%, 4.0% over", warnings[2])

    def test_subclass_targets_without_their_macro_target_are_flagged(self):
        warnings = ips.check_targets({"Growth": 1.0}, {"US Equity": 0.9, "CDs": 0.1})
        self.assertTrue(any("Liquidity has subclass targets" in w for w in warnings))

    def test_subclass_only_targets_must_sum_to_100(self):
        self.assertEqual(ips.check_targets({}, {"US Equity": 0.7, "US Bonds": 0.3}), [])
        warnings = ips.check_targets({}, {"US Equity": 0.7, "US Bonds": 0.2})
        self.assertEqual(len(warnings), 1)
        self.assertIn("90.0%", warnings[0])

    def test_target_tree_follows_hierarchy_order_with_subtotals(self):
        tree = ips.target_tree({"Income": 0.4, "Growth": 0.6}, {"US Bonds": 0.4})
        self.assertEqual([t[0] for t in tree], ["Growth", "Income"])
        self.assertEqual(tree[0], ("Growth", 0.6, [], None))
        self.assertEqual(tree[1], ("Income", 0.4, [("US Bonds", 0.4)], 0.4))


class CompareSkipsUnsplitMacrosTests(unittest.TestCase):
    def test_subclasses_of_a_macro_with_no_split_are_left_out(self):
        with h.TempDB() as db_path:
            conn = db_mod.get_connection(db_path)
            ips.set_target(conn, "Growth", 0.8)
            ips.set_target(conn, "Liquidity", 0.2)
            ips.set_target(conn, "US Equity", 0.8)
            rows = [
                {"asset_class": "US Equity", "pct": 0.7},
                {"asset_class": "REITs", "pct": 0.05},
                {"asset_class": "CDs", "pct": 0.2},
                {"asset_class": "Unclassified", "pct": 0.05},
            ]
            classes = {c["asset_class"]: c for c in ips.compare_to_targets(conn, rows, 1000.0)}
            conn.close()
        # Growth has a split, so REITs' missing target really is 0%; Liquidity
        # has none, so CDs isn't flagged; Unclassified sits outside the hierarchy.
        self.assertEqual(set(classes), {"US Equity", "REITs", "Unclassified"})
        self.assertEqual(classes["REITs"]["target_pct"], 0.0)

    def test_macro_names_stay_out_of_the_subclass_comparison(self):
        """A hypothetical account's asset_classes may name a macro directly
        ("Income"); that belongs in the macro comparison, not as a
        subclass with a 0% target."""
        with h.TempDB() as db_path:
            conn = db_mod.get_connection(db_path)
            ips.set_target(conn, "US Equity", 0.6)
            rows = [{"asset_class": "US Equity", "pct": 0.6}, {"asset_class": "Income", "pct": 0.4}]
            classes = {c["asset_class"] for c in ips.compare_to_targets(conn, rows, 1000.0)}
            conn.close()
        self.assertEqual(classes, {"US Equity"})


if __name__ == "__main__":
    unittest.main()
