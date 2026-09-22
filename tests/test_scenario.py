"""Regression tests for pt/scenario.py's load_scenario() -- specifically
_include (see its docstring for the merge rules), since plain loading
(no _include) is already exercised indirectly by every other test module
that loads a scenario. Everything here works against throwaway files in a
temp directory, never test_data/ or the user's real ~/.portfolio_tool
files.
"""
import tempfile
import unittest
from pathlib import Path

import yaml

from pt import scenario as scenario_mod


def _write(path: Path, data: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f)
    return path


class LoadScenarioTests(unittest.TestCase):
    """Plain load_scenario() behavior, unaffected by _include -- guards
    against a regression in the refactor that added it."""

    def test_missing_file_raises_scenario_error(self):
        with self.assertRaises(scenario_mod.ScenarioError):
            scenario_mod.load_scenario("/no/such/file.yaml")

    def test_invalid_yaml_raises_scenario_error(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "bad.yaml"
            path.write_text("retirement: [unterminated\n")
            with self.assertRaises(scenario_mod.ScenarioError):
                scenario_mod.load_scenario(path)

    def test_non_mapping_yaml_raises_scenario_error(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "list.yaml"
            path.write_text("- just\n- a\n- list\n")
            with self.assertRaises(scenario_mod.ScenarioError):
                scenario_mod.load_scenario(path)

    def test_plain_scenario_with_no_include_loads_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            path = _write(Path(d) / "plain.yaml", {"retirement": {"retirement_age": 65}})
            self.assertEqual(scenario_mod.load_scenario(path), {"retirement": {"retirement_age": 65}})


class IncludeTests(unittest.TestCase):
    def test_single_string_include_merges_under_the_including_file(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            _write(d / "shared_returns.yaml", {
                "returns": {"asset_classes": {
                    "US Equity": {"expected_return": 8.0, "volatility": 18},
                    "US Bonds": {"expected_return": 4.5, "volatility": 7},
                }},
            })
            main = _write(d / "main.yaml", {
                "_include": "shared_returns.yaml",
                "retirement": {"retirement_age": 65},
            })
            result = scenario_mod.load_scenario(main)
            self.assertEqual(result["retirement"], {"retirement_age": 65})
            self.assertEqual(result["returns"]["asset_classes"]["US Equity"]["expected_return"], 8.0)
            self.assertNotIn("_include", result)

    def test_including_file_overrides_one_nested_key_without_dropping_siblings(self):
        """The whole point of a deep merge -- overriding US Equity alone
        must not silently discard US Bonds, which only the include
        provides."""
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            _write(d / "shared.yaml", {
                "returns": {"asset_classes": {
                    "US Equity": {"expected_return": 8.0, "volatility": 18},
                    "US Bonds": {"expected_return": 4.5, "volatility": 7},
                }},
            })
            main = _write(d / "main.yaml", {
                "_include": "shared.yaml",
                "returns": {"asset_classes": {"US Equity": {"expected_return": 7.5, "volatility": 18}}},
            })
            result = scenario_mod.load_scenario(main)
            self.assertEqual(result["returns"]["asset_classes"]["US Equity"]["expected_return"], 7.5)
            self.assertEqual(result["returns"]["asset_classes"]["US Bonds"]["expected_return"], 4.5)

    def test_lists_are_replaced_wholesale_not_merged(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            _write(d / "shared.yaml", {"roth_conversions": [{"owner": "alex", "start_date": "2027-01-01"}]})
            main = _write(d / "main.yaml", {
                "_include": "shared.yaml",
                "roth_conversions": [{"owner": "sam", "start_date": "2028-01-01"}],
            })
            result = scenario_mod.load_scenario(main)
            self.assertEqual(len(result["roth_conversions"]), 1)
            self.assertEqual(result["roth_conversions"][0]["owner"], "sam")

    def test_list_of_includes_merges_left_to_right_then_the_file_itself_wins(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            _write(d / "a.yaml", {"income": {"target_spending": 1}, "inflation": {"general": 1.0}})
            _write(d / "b.yaml", {"income": {"target_spending": 2}})
            main = _write(d / "main.yaml", {"_include": ["a.yaml", "b.yaml"]})
            result = scenario_mod.load_scenario(main)
            # b.yaml (later in the list) wins over a.yaml for the shared key ...
            self.assertEqual(result["income"]["target_spending"], 2)
            # ... but a.yaml's own untouched key survives.
            self.assertEqual(result["inflation"]["general"], 1.0)

    def test_relative_include_path_resolves_against_the_including_files_directory(self):
        """Not the current working directory -- an included file can live
        anywhere regardless of where each scenario using it lives."""
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            _write(d / "shared" / "returns.yaml", {"returns": {"growth": 8.0}})
            main = _write(d / "scenarios" / "main.yaml", {
                "_include": "../shared/returns.yaml",
                "retirement": {"retirement_age": 65},
            })
            import os
            old_cwd = os.getcwd()
            os.chdir(tempfile.gettempdir())  # deliberately NOT d, to prove cwd is irrelevant
            try:
                result = scenario_mod.load_scenario(main)
            finally:
                os.chdir(old_cwd)
            self.assertEqual(result["returns"]["growth"], 8.0)

    def test_transitive_include_resolves_recursively(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            _write(d / "c.yaml", {"income": {"target_spending": 60000}})
            _write(d / "b.yaml", {"_include": "c.yaml", "inflation": {"general": 2.5}})
            main = _write(d / "a.yaml", {"_include": "b.yaml", "retirement": {"retirement_age": 65}})
            result = scenario_mod.load_scenario(main)
            self.assertEqual(result["income"]["target_spending"], 60000)
            self.assertEqual(result["inflation"]["general"], 2.5)
            self.assertEqual(result["retirement"]["retirement_age"], 65)

    def test_include_cycle_raises_scenario_error(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            _write(d / "a.yaml", {"_include": "b.yaml"})
            _write(d / "b.yaml", {"_include": "a.yaml"})
            with self.assertRaises(scenario_mod.ScenarioError):
                scenario_mod.load_scenario(d / "a.yaml")

    def test_diamond_include_is_not_a_false_cycle(self):
        """The same shared file included from two different (unrelated)
        branches is legitimate, not a cycle."""
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            _write(d / "shared.yaml", {"inflation": {"general": 2.5}})
            _write(d / "a.yaml", {"_include": "shared.yaml", "income": {"target_spending": 1}})
            _write(d / "b.yaml", {"_include": "shared.yaml", "income": {"target_spending": 2}})
            main = _write(d / "main.yaml", {"_include": ["a.yaml", "b.yaml"]})
            result = scenario_mod.load_scenario(main)  # should not raise
            self.assertEqual(result["inflation"]["general"], 2.5)
            self.assertEqual(result["income"]["target_spending"], 2)

    def test_missing_included_file_raises_scenario_error(self):
        with tempfile.TemporaryDirectory() as d:
            main = _write(Path(d) / "main.yaml", {"_include": "does_not_exist.yaml"})
            with self.assertRaises(scenario_mod.ScenarioError):
                scenario_mod.load_scenario(main)

    def test_include_of_wrong_type_raises_scenario_error(self):
        with tempfile.TemporaryDirectory() as d:
            main = _write(Path(d) / "main.yaml", {"_include": 42})
            with self.assertRaises(scenario_mod.ScenarioError):
                scenario_mod.load_scenario(main)

    def test_tilde_in_include_path_is_expanded(self):
        # Redirects HOME to a temp dir so this doesn't touch the real
        # user's home directory, then includes via "~/shared.yaml".
        import os
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as fake_home:
            _write(Path(fake_home) / "shared.yaml", {"inflation": {"general": 3.0}})
            main = _write(Path(d) / "main.yaml", {"_include": "~/shared.yaml"})
            old_home = os.environ.get("HOME")
            os.environ["HOME"] = fake_home
            try:
                result = scenario_mod.load_scenario(main)
            finally:
                if old_home is not None:
                    os.environ["HOME"] = old_home
                else:
                    del os.environ["HOME"]
            self.assertEqual(result["inflation"]["general"], 3.0)


if __name__ == "__main__":
    unittest.main()
