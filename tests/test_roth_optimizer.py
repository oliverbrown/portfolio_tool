"""Regression tests for roth_optimizer against a small self-contained
scenario (scenario.hypothetical_accounts -- see pt/projection.py's
build_hypothetical_accounts()) rather than test_data/'s real snapshot,
since test_data/retirement_scenario.yaml doesn't configure any
roth_conversions windows (nothing to optimize) and this suite shouldn't
require editing that shared fixture just to give the optimizer something
to chew on.
"""
import unittest
from datetime import date

from pt import projection as proj
from pt import social_security as social_security_mod

from roth_optimizer import optimizer as opt
from roth_optimizer import scatter_chart as scatter_chart_mod


def _hypothetical_scenario(**roth_conversion_overrides):
    # Date fields need real date objects here -- a scenario loaded from
    # YAML (scenario.load_scenario()) gets these from PyYAML's implicit
    # date resolver automatically; built by hand in Python, plain strings
    # would stay strings and break _conversion_windows()'s .year access.
    scenario = {
        "people": {"primary": {"name": "alex", "birthdate": date(1962, 1, 1)}},
        "retirement": {"retirement_age": 65},
        "income": {"target_spending": 60000},
        "inflation": {"general": 2.5},
        "returns": {"growth": 8.0, "fixed_income": 4.0},
        "hypothetical_accounts": [
            {"account_number": "trad1", "account_type": "IRA", "owner": "alex",
             "value": 500000, "pct_growth": 60},
            {"account_number": "roth1", "account_type": "Roth IRA", "owner": "alex",
             "value": 50000, "pct_growth": 100},
        ],
        "roth_conversions": [{
            "owner": "alex", "start_date": date(2027, 1, 1), "end_date": date(2033, 12, 31),
            "constraints": {"max_marginal_bracket": 24, "max_conversion_per_year": 60000},
        }],
    }
    scenario["roth_conversions"][0].update(roth_conversion_overrides)
    return scenario


class RothOptimizerTests(unittest.TestCase):
    def setUp(self):
        self.scenario = _hypothetical_scenario()
        self.profile = {"people": self.scenario["people"]}
        self.account_rows, self.allocation_rows, self.total = proj.build_hypothetical_accounts(
            self.scenario, self.profile
        )
        self.blended_rate = proj.compute_blended_growth_rate(self.allocation_rows, self.total, self.scenario)

    def test_optimize_never_does_worse_than_no_conversions(self):
        """all-zero is always one of optimize()'s own starting points --
        the winning schedule should never score below it."""
        result = opt.optimize(
            self.profile, self.scenario, self.account_rows, self.blended_rate,
            heir_tax_rate=0.24, restarts=2, seed=1,
        )
        self.assertGreaterEqual(result["value"], result["zero_conversions_value"])
        self.assertGreater(result["evaluations"], 0)

    def test_schedule_shape_matches_conversion_windows(self):
        result = opt.optimize(
            self.profile, self.scenario, self.account_rows, self.blended_rate,
            heir_tax_rate=0.24, restarts=1, seed=1,
        )
        windows = result["windows"]
        self.assertEqual(len(windows), 1)
        self.assertEqual(windows[0]["owner"], "alex")
        expected_years = windows[0]["end_year"] - windows[0]["start_year"] + 1
        self.assertEqual(len(result["schedule"][0]), expected_years)
        self.assertEqual(len(result["actual_schedule"][0]), expected_years)

    def test_schedule_respects_max_conversion_per_year_cap(self):
        result = opt.optimize(
            self.profile, self.scenario, self.account_rows, self.blended_rate,
            heir_tax_rate=0.24, restarts=2, seed=1,
        )
        cap = self.scenario["roth_conversions"][0]["constraints"]["max_conversion_per_year"]
        for amount in result["actual_schedule"][0]:
            self.assertLessEqual(amount, cap + 0.01)  # small float tolerance

    def test_no_conversion_windows_raises_optimizer_error(self):
        scenario = dict(self.scenario)
        scenario["roth_conversions"] = []
        with self.assertRaises(opt.OptimizerError):
            opt.optimize(self.profile, scenario, self.account_rows, self.blended_rate, heir_tax_rate=0.24)

    def test_seeded_search_is_reproducible(self):
        kwargs = dict(profile=self.profile, scenario=self.scenario, account_rows=self.account_rows,
                      blended_rate=self.blended_rate, heir_tax_rate=0.24, restarts=2, seed=3)
        result1 = opt.optimize(**kwargs)
        result2 = opt.optimize(**kwargs)
        self.assertEqual(result1["schedule"], result2["schedule"])
        self.assertEqual(result1["value"], result2["value"])


class PerBucketGrowthOptimizerTests(unittest.TestCase):
    """optimize()'s bucket_rates param -- grows the Traditional IRA and the
    Roth IRA at their own distinct blended rates during the search (and in
    zero_conversions_value/original_value/actual_schedule), instead of one
    household-wide blended_rate applied to both -- see optimize()'s
    bucket_rates docstring. Uses asset_classes-based hypothetical_accounts
    (unlike RothOptimizerTests' legacy pct_growth fixture above) since
    per-bucket growth is derived from each account's own asset-class mix."""

    def _bucketed_scenario(self, **roth_conversion_overrides):
        # birthdate/retirement_age/target_spending chosen so nothing forces
        # a withdrawal before the projection's final (age-100) row -- see
        # test_projection.py's PerBucketGrowthTests._hypothetical_scenario()
        # for why (a spending household would fully deplete its modest
        # $550k over ~35-65 years regardless of growth rate, making
        # zero_conversions_value 0.0 either way and hiding the very
        # difference this test class exists to check for).
        scenario = {
            "people": {"primary": {"name": "alex", "birthdate": date(1990, 1, 1)}},
            "retirement": {"retirement_age": 95},
            "income": {"target_spending": 0},
            "inflation": {"general": 2.5},
            "returns": {"asset_classes": {
                "US Equity": {"expected_return": 8.0, "volatility": 18},
                "US Bonds": {"expected_return": 3.0, "volatility": 7},
            }},
            "hypothetical_accounts": [
                {"account_number": "trad1", "account_type": "IRA", "owner": "alex", "value": 500000,
                 "asset_classes": {"US Bonds": 100}},
                {"account_number": "roth1", "account_type": "Roth IRA", "owner": "alex", "value": 50000,
                 "asset_classes": {"US Equity": 100}},
            ],
            "roth_conversions": [{
                "owner": "alex", "start_date": date(2027, 1, 1), "end_date": date(2033, 12, 31),
                "constraints": {"max_marginal_bracket": 24, "max_conversion_per_year": 60000},
            }],
        }
        scenario["roth_conversions"][0].update(roth_conversion_overrides)
        return scenario

    def setUp(self):
        self.scenario = self._bucketed_scenario()
        self.profile = {"people": self.scenario["people"]}
        self.account_rows, self.allocation_rows, self.total = proj.build_hypothetical_accounts(
            self.scenario, self.profile
        )
        self.blended_rate = proj.compute_blended_growth_rate(self.allocation_rows, self.total, self.scenario)
        class_rows = proj.build_hypothetical_account_class_rows(self.scenario, self.profile)
        inherited_schedules = proj.build_inherited_schedules(self.scenario, self.profile)
        bucket_allocs = proj.bucket_allocation_rows(class_rows, inherited_schedules, self.profile)
        self.bucket_rates = {
            key: proj.compute_blended_growth_rate(rows, tot, self.scenario)
            for key, (rows, tot) in bucket_allocs.items()
        }

    def test_bucket_rates_differ_from_the_household_blend(self):
        """Sanity check on the fixture itself -- if these matched, the rest
        of this test class wouldn't be testing anything."""
        self.assertEqual(set(self.bucket_rates), {"traditional:alex:IRA", "roth:alex"})
        self.assertAlmostEqual(self.bucket_rates["traditional:alex:IRA"], 0.03, places=6)
        self.assertAlmostEqual(self.bucket_rates["roth:alex"], 0.08, places=6)
        self.assertNotAlmostEqual(self.blended_rate, self.bucket_rates["traditional:alex:IRA"], places=3)

    def test_optimize_with_bucket_rates_runs_cleanly(self):
        result = opt.optimize(
            self.profile, self.scenario, self.account_rows, self.blended_rate,
            heir_tax_rate=0.24, restarts=2, seed=1, bucket_rates=self.bucket_rates,
        )
        self.assertGreaterEqual(result["value"], result["zero_conversions_value"])
        windows = result["windows"]
        expected_years = windows[0]["end_year"] - windows[0]["start_year"] + 1
        self.assertEqual(len(result["schedule"][0]), expected_years)
        self.assertEqual(len(result["actual_schedule"][0]), expected_years)

    def test_bucket_rates_change_the_result_vs_household_rate(self):
        """With bucket_rates, the Traditional IRA being converted FROM
        grows at 3% (not the household's blended rate) and the Roth being
        converted INTO grows at 8% -- a materially different trade-off
        than one household-wide rate, so zero_conversions_value (which
        doesn't depend on the search, just on how each bucket grows)
        should come out different between the two modes."""
        kwargs = dict(profile=self.profile, scenario=self.scenario, account_rows=self.account_rows,
                      blended_rate=self.blended_rate, heir_tax_rate=0.24, restarts=1, seed=1)
        without_buckets = opt.optimize(**kwargs)
        with_buckets = opt.optimize(**kwargs, bucket_rates=self.bucket_rates)
        self.assertNotAlmostEqual(
            without_buckets["zero_conversions_value"], with_buckets["zero_conversions_value"], places=2
        )

    def test_seeded_search_with_bucket_rates_is_reproducible(self):
        kwargs = dict(profile=self.profile, scenario=self.scenario, account_rows=self.account_rows,
                      blended_rate=self.blended_rate, heir_tax_rate=0.24, restarts=2, seed=3,
                      bucket_rates=self.bucket_rates)
        result1 = opt.optimize(**kwargs)
        result2 = opt.optimize(**kwargs)
        self.assertEqual(result1["schedule"], result2["schedule"])
        self.assertEqual(result1["value"], result2["value"])


class ConversionOwnersAndReorderTests(unittest.TestCase):
    """conversion_owners()/reorder_by_owner_priority() -- pure structural
    checks on the reordering itself (no project() involved)."""

    def _two_owner_scenario(self):
        return {
            "roth_conversions": [
                {"owner": "alex", "start_date": date(2027, 1, 1), "end_date": date(2027, 12, 31)},
                {"owner": "sam", "start_date": date(2028, 1, 1), "end_date": date(2028, 12, 31)},
                {"owner": "alex", "start_date": date(2029, 1, 1), "end_date": date(2029, 12, 31)},
            ]
        }

    def test_conversion_owners_lists_distinct_owners_in_first_appearance_order(self):
        self.assertEqual(opt.conversion_owners(self._two_owner_scenario()), ["alex", "sam"])

    def test_reorder_puts_named_owner_first_preserving_relative_order_within_each_owner(self):
        scenario = self._two_owner_scenario()
        reordered = opt.reorder_by_owner_priority(scenario, "sam")
        owners_in_order = [e["owner"] for e in reordered["roth_conversions"]]
        # sam's (single) entry moves to the front; alex's two entries keep
        # their own original relative order (2027 window before 2029's).
        self.assertEqual(owners_in_order, ["sam", "alex", "alex"])
        self.assertEqual(reordered["roth_conversions"][1]["start_date"], date(2027, 1, 1))
        self.assertEqual(reordered["roth_conversions"][2]["start_date"], date(2029, 1, 1))

    def test_reorder_does_not_mutate_the_input_scenario(self):
        scenario = self._two_owner_scenario()
        original_order = [e["owner"] for e in scenario["roth_conversions"]]
        opt.reorder_by_owner_priority(scenario, "sam")
        self.assertEqual([e["owner"] for e in scenario["roth_conversions"]], original_order)

    def test_reorder_unknown_owner_raises_optimizer_error(self):
        with self.assertRaises(opt.OptimizerError):
            opt.reorder_by_owner_priority(self._two_owner_scenario(), "nobody")


class ConversionPriorityTests(unittest.TestCase):
    """Proves reorder_by_owner_priority() has a REAL effect on project()'s
    output, not just a cosmetic list reorder -- see project()'s
    roth_conversions loop (pt/projection.py), which accumulates
    ordinary_income_so_far strictly in scenario.roth_conversions' own list
    order. Two owners share the same household max_marginal_bracket
    ceiling; alex requests a moderate amount while sam requests far more
    than the shared ceiling allows -- whoever goes FIRST gets first claim
    on that shared room, so alex's actual conversion should be
    substantially larger when alex is prioritized than when sam is.

    (As of the fix in _constrained_distribution_amount() -- see
    pt/projection.py and ConstrainedDistributionAmountTests in
    tests/test_projection.py -- the household's standard deduction is
    spent at most once per year no matter how many entries share a
    ceiling, so whoever's evaluated SECOND when sam goes first is fully
    crowded out: alex's share is exactly 0.)"""

    def _priority_scenario(self):
        return {
            "people": {
                "primary": {"name": "alex", "birthdate": date(1962, 1, 1)},
                "spouse": {"name": "sam", "birthdate": date(1962, 1, 1)},
            },
            "retirement": {"retirement_age": 90, "spouse_retirement_age": 90},
            "income": {"target_spending": 0},
            "inflation": {"general": 2.5},
            "returns": {"growth": 8.0, "fixed_income": 4.0},
            "hypothetical_accounts": [
                {"account_number": "alex_trad", "account_type": "IRA", "owner": "alex",
                 "value": 2000000, "pct_growth": 0},
                {"account_number": "sam_trad", "account_type": "IRA", "owner": "sam",
                 "value": 2000000, "pct_growth": 0},
            ],
            "roth_conversions": [
                {"owner": "alex", "start_date": date(2026, 1, 1), "end_date": date(2026, 12, 31),
                 "annual_amount": 50000, "constraints": {"max_marginal_bracket": 24}},
                {"owner": "sam", "start_date": date(2026, 1, 1), "end_date": date(2026, 12, 31),
                 "annual_amount": 2000000, "constraints": {"max_marginal_bracket": 24}},
            ],
        }

    def _year_2026_conversions(self, scenario, profile, account_rows, blended_rate):
        rows = proj.project(profile, scenario, account_rows, blended_rate, start_year=2026)
        self.assertEqual(rows[0]["year"], 2026)
        return rows[0]["roth_conversion_by_owner"]

    def test_prioritized_owner_gets_first_claim_on_shared_bracket_room(self):
        scenario = self._priority_scenario()
        profile = {"people": scenario["people"]}
        account_rows, allocation_rows, total = proj.build_hypothetical_accounts(scenario, profile)
        blended_rate = proj.compute_blended_growth_rate(allocation_rows, total, scenario)

        alex_first = opt.reorder_by_owner_priority(scenario, "alex")
        by_owner_alex_first = self._year_2026_conversions(alex_first, profile, account_rows, blended_rate)
        # alex went first -- gets (essentially) their full $50k request.
        self.assertGreater(by_owner_alex_first.get("alex", 0.0), 49000)

        sam_first = opt.reorder_by_owner_priority(scenario, "sam")
        by_owner_sam_first = self._year_2026_conversions(sam_first, profile, account_rows, blended_rate)
        # sam's uncapped request consumes the entire shared bracket ceiling
        # first, leaving alex fully crowded out.
        self.assertAlmostEqual(by_owner_sam_first.get("alex", 0.0), 0.0)
        self.assertGreater(
            by_owner_alex_first["alex"] - by_owner_sam_first.get("alex", 0.0), 9000
        )
        # Same total household room used up either way -- reordering only
        # changes WHO gets it, not how much room the shared ceiling allows.
        self.assertAlmostEqual(
            sum(by_owner_alex_first.values()), sum(by_owner_sam_first.values()), delta=1.0
        )


class AcceleratedIraDrawdownTests(unittest.TestCase):
    """optimize()'s include_ira_distributions param -- jointly searches
    scenario.ira_distributions windows alongside roth_conversions in the
    SAME pattern-search pass, instead of only ever tuning roth_conversions
    (the default, unchanged behavior)."""

    def _joint_scenario(self):
        return {
            "people": {"primary": {"name": "alex", "birthdate": date(1962, 1, 1)}},
            "retirement": {"retirement_age": 90},
            "income": {"target_spending": 0},
            "inflation": {"general": 2.5},
            "returns": {"growth": 8.0, "fixed_income": 4.0},
            "hypothetical_accounts": [
                {"account_number": "trad1", "account_type": "IRA", "owner": "alex",
                 "value": 1000000, "pct_growth": 0},
            ],
            "roth_conversions": [{
                "owner": "alex", "start_date": date(2026, 1, 1), "end_date": date(2030, 12, 31),
                "constraints": {"max_marginal_bracket": 24, "max_conversion_per_year": 2000000},
            }],
            "ira_distributions": [{
                "owner": "alex", "start_date": date(2026, 1, 1), "end_date": date(2030, 12, 31),
                "constraints": {"max_marginal_bracket": 24, "max_distribution_per_year": 40000},
            }],
        }

    def setUp(self):
        self.scenario = self._joint_scenario()
        self.profile = {"people": self.scenario["people"]}
        self.account_rows, self.allocation_rows, self.total = proj.build_hypothetical_accounts(
            self.scenario, self.profile
        )
        self.blended_rate = proj.compute_blended_growth_rate(self.allocation_rows, self.total, self.scenario)

    def test_default_still_searches_roth_conversions_only(self):
        """Backward compatibility -- omitting include_ira_distributions
        must behave exactly as before this parameter existed."""
        result = opt.optimize(
            self.profile, self.scenario, self.account_rows, self.blended_rate,
            heir_tax_rate=0.24, restarts=1, seed=1,
        )
        self.assertEqual(len(result["windows"]), 1)
        self.assertEqual(result["windows"][0]["kind"], "roth_conversion")

    def test_joint_mode_includes_both_kinds_of_window(self):
        result = opt.optimize(
            self.profile, self.scenario, self.account_rows, self.blended_rate,
            heir_tax_rate=0.24, restarts=1, seed=1, include_ira_distributions=True,
        )
        kinds = {w["kind"] for w in result["windows"]}
        self.assertEqual(kinds, {"roth_conversion", "ira_distribution"})
        self.assertEqual(len(result["schedule"]), 2)
        self.assertEqual(len(result["actual_schedule"]), 2)

    def test_ira_distribution_window_respects_its_own_max_distribution_per_year_cap(self):
        result = opt.optimize(
            self.profile, self.scenario, self.account_rows, self.blended_rate,
            heir_tax_rate=0.24, restarts=2, seed=1, include_ira_distributions=True,
        )
        ira_window_idx = next(i for i, w in enumerate(result["windows"]) if w["kind"] == "ira_distribution")
        cap = self.scenario["ira_distributions"][0]["constraints"]["max_distribution_per_year"]
        for amount in result["actual_schedule"][ira_window_idx]:
            self.assertLessEqual(amount, cap + 0.01)

    def test_zero_conversions_value_zeroes_both_sections_in_joint_mode(self):
        """zero_conversions_value with include_ira_distributions=True must
        match a scenario with BOTH sections removed entirely -- not just
        roth_conversions -- since the joint search's own "opt out
        entirely" baseline should mean no discretionary Traditional
        withdrawal of either kind."""
        result = opt.optimize(
            self.profile, self.scenario, self.account_rows, self.blended_rate,
            heir_tax_rate=0.24, restarts=1, seed=1, include_ira_distributions=True,
        )
        truly_empty_scenario = dict(self.scenario, roth_conversions=[], ira_distributions=[])
        rows = proj.project(self.profile, truly_empty_scenario, self.account_rows, self.blended_rate)
        expected = opt.after_tax_estate_value(rows[-1], 0.24)
        self.assertAlmostEqual(result["zero_conversions_value"], expected, places=2)

    def test_joint_search_never_does_worse_than_roth_conversions_alone(self):
        """More decision variables (a strict superset of what a
        roth_conversions-only search could already do) should never make
        the winning value worse -- the search could always just leave
        every ira_distribution variable at 0."""
        roth_only = opt.optimize(
            self.profile, self.scenario, self.account_rows, self.blended_rate,
            heir_tax_rate=0.24, restarts=2, seed=1,
        )
        joint = opt.optimize(
            self.profile, self.scenario, self.account_rows, self.blended_rate,
            heir_tax_rate=0.24, restarts=2, seed=1, include_ira_distributions=True,
        )
        self.assertGreaterEqual(joint["value"], roth_only["value"] - 1.0)


class InheritedWithdrawalOptimizerTests(unittest.TestCase):
    """optimize()'s include_inherited_withdrawals param -- searches each
    already-configured scenario.inherited_accounts planned_withdrawal
    window as a THIRD family of decision variables: how much EXTRA,
    above that account's own legally-required 10-year-rule minimum, to
    withdraw each year."""

    def _scenario(self, roth_conversions=None):
        scenario = {
            "people": {"primary": {"name": "alex", "birthdate": date(1962, 1, 1)}},
            "retirement": {"retirement_age": 90},
            "income": {"target_spending": 0},
            "inflation": {"general": 2.5},
            "returns": {"growth": 8.0, "fixed_income": 4.0},
            "hypothetical_accounts": [
                {"account_number": "inh1", "account_type": "Inherited IRA", "owner": "alex",
                 "value": 500000, "pct_growth": 0},
            ],
            "inherited_accounts": [{
                "account_number": "inh1", "type": "traditional",
                # Died past their own RBD (73) -- annual stretch RMDs
                # required in years 1-9, giving the search a nonzero
                # mandatory floor to search "extra" on top of.
                "decedent_birthdate": date(1940, 1, 1), "decedent_death_date": date(2025, 6, 1),
                "beneficiary": "alex",
                "planned_withdrawal": {"start_date": date(2026, 1, 1), "end_date": date(2034, 12, 31),
                                        "annual_amount": 0},
            }],
        }
        if roth_conversions is not None:
            scenario["roth_conversions"] = roth_conversions
        return scenario

    def setUp(self):
        self.scenario = self._scenario()
        self.profile = {"people": self.scenario["people"]}
        self.account_rows, self.allocation_rows, self.total = proj.build_hypothetical_accounts(
            self.scenario, self.profile
        )
        self.blended_rate = proj.compute_blended_growth_rate(self.allocation_rows, self.total, self.scenario)

    def test_default_does_not_search_inherited_withdrawals(self):
        """Backward compatibility -- with no roth_conversions section at
        all and the flag omitted, there's nothing to search."""
        with self.assertRaises(opt.OptimizerError):
            opt.optimize(self.profile, self.scenario, self.account_rows, self.blended_rate,
                         heir_tax_rate=0.24, restarts=1, seed=1)

    def test_flag_adds_an_inherited_withdrawal_window(self):
        result = opt.optimize(
            self.profile, self.scenario, self.account_rows, self.blended_rate,
            heir_tax_rate=0.24, restarts=2, seed=1, include_inherited_withdrawals=True,
        )
        self.assertEqual(len(result["windows"]), 1)
        w = result["windows"][0]
        self.assertEqual(w["kind"], "inherited_withdrawal")
        self.assertEqual(w["account_number"], "inh1")
        self.assertEqual(w["owner"], "alex")

    def test_search_never_does_worse_than_the_mandatory_minimum_alone(self):
        result = opt.optimize(
            self.profile, self.scenario, self.account_rows, self.blended_rate,
            heir_tax_rate=0.24, restarts=2, seed=1, include_inherited_withdrawals=True,
        )
        self.assertGreaterEqual(result["value"], result["zero_conversions_value"] - 1.0)

    def test_zero_conversions_value_keeps_the_mandatory_schedule_intact(self):
        """zero_conversions_value must NOT mean "no distribution at all"
        for an inherited account -- the legally-required 10-year-rule
        minimum still applies. Confirmed by checking the zeroed-floor
        scenario still distributes the full balance by the final year
        (the mandatory 10-year cliff), not zero."""
        result = opt.optimize(
            self.profile, self.scenario, self.account_rows, self.blended_rate,
            heir_tax_rate=0.24, restarts=1, seed=1, include_inherited_withdrawals=True,
        )
        self.assertGreater(result["zero_conversions_value"], 0.0)
        # The account is fully distributed (to Taxable) by the mandatory
        # 10-year cliff regardless -- confirmed directly against project().
        zeroed = dict(self.scenario)
        zeroed["inherited_accounts"] = [dict(self.scenario["inherited_accounts"][0])]
        zeroed["inherited_accounts"][0]["planned_withdrawal"] = dict(
            zeroed["inherited_accounts"][0]["planned_withdrawal"], annual_amount=0
        )
        rows = proj.project(self.profile, zeroed, self.account_rows, self.blended_rate, start_year=2026)
        self.assertAlmostEqual(rows[-1]["inherited_traditional_balance"], 0.0, places=2)

    def test_hypothetical_inherited_account_is_silently_skipped(self):
        """A hypothetical future inheritance (hypothetical_value, no
        account_number) can't be keyed in
        inherited_distribution_by_account -- _distribution_windows()
        documents this as skipped, not an error."""
        scenario = self._scenario(roth_conversions=[{
            "owner": "alex", "start_date": date(2027, 1, 1), "end_date": date(2027, 12, 31),
            "constraints": {"max_marginal_bracket": 24, "max_conversion_per_year": 10000},
        }])
        scenario["inherited_accounts"].append({
            "type": "traditional", "beneficiary": "alex",
            "decedent_birthdate": date(1940, 1, 1), "decedent_death_date": date(2030, 1, 1),
            "hypothetical_value": 200000,
            "planned_withdrawal": {"start_date": date(2031, 1, 1), "end_date": date(2039, 12, 31),
                                    "annual_amount": 0},
        })
        profile = {"people": scenario["people"]}
        account_rows, allocation_rows, total = proj.build_hypothetical_accounts(scenario, profile)
        blended_rate = proj.compute_blended_growth_rate(allocation_rows, total, scenario)
        result = opt.optimize(profile, scenario, account_rows, blended_rate, heir_tax_rate=0.24,
                              restarts=1, seed=1, include_inherited_withdrawals=True)
        kinds = [w["kind"] for w in result["windows"]]
        self.assertEqual(kinds.count("inherited_withdrawal"), 1)  # only the real account, not the hypothetical one


class ClaimAgeSearchTests(unittest.TestCase):
    """claim_age_candidates()/apply_claim_ages()/search_claim_ages() --
    the brute-force grid search over Social Security claim_age (see
    pt/social_security.py). No mortality configured -- by design, a
    projection running to age 100 for everyone should favor claiming as
    LATE as possible (more guaranteed dollars over a longer assumed
    lifetime); with mortality configured to an early death, it should
    flip to favoring claiming EARLY instead -- proving the search is
    genuinely mortality-sensitive, not just always picking one extreme."""

    def _scenario(self, mortality=None):
        scenario = {
            "people": {"primary": {"name": "alex", "birthdate": date(1962, 1, 1)}},
            "retirement": {"retirement_age": 62},
            "income": {"target_spending": 60000},
            "inflation": {"general": 2.5},
            "returns": {"growth": 8.0, "fixed_income": 4.0},
            "hypothetical_accounts": [
                {"account_number": "trad1", "account_type": "IRA", "owner": "alex",
                 "value": 800000, "pct_growth": 60},
            ],
            "social_security": {"alex": {"pia_monthly": 2800}},
        }
        if mortality:
            scenario["mortality"] = mortality
        return scenario

    def _setup(self, scenario):
        profile = {"people": scenario["people"]}
        account_rows, allocation_rows, total = proj.build_hypothetical_accounts(scenario, profile)
        blended_rate = proj.compute_blended_growth_rate(allocation_rows, total, scenario)
        return profile, account_rows, blended_rate

    def test_claim_age_candidates_only_lists_names_with_pia_monthly(self):
        scenario = self._scenario()
        scenario["social_security"]["someone_else"] = {"monthly_benefit": 1000, "claim_age": 67}
        self.assertEqual(opt.claim_age_candidates(scenario), ["alex"])

    def test_apply_claim_ages_only_touches_the_given_names(self):
        scenario = self._scenario()
        scenario["social_security"]["alex"]["claim_age"] = 62
        variant = opt.apply_claim_ages(scenario, {"alex": 70})
        self.assertEqual(variant["social_security"]["alex"]["claim_age"], 70)
        self.assertEqual(variant["social_security"]["alex"]["pia_monthly"], 2800)  # untouched

    def test_no_pia_monthly_configured_raises(self):
        scenario = self._scenario()
        scenario["social_security"]["alex"] = {"monthly_benefit": 2000, "claim_age": 67}
        profile, account_rows, blended_rate = self._setup(scenario)
        with self.assertRaises(opt.OptimizerError):
            opt.search_claim_ages(profile, scenario, account_rows, blended_rate, heir_tax_rate=0.24)

    def test_result_shape_covers_every_candidate_age_by_default(self):
        scenario = self._scenario()
        profile, account_rows, blended_rate = self._setup(scenario)
        result = opt.search_claim_ages(profile, scenario, account_rows, blended_rate, heir_tax_rate=0.24)
        self.assertEqual(result["names"], ["alex"])
        expected_ages = set(range(social_security_mod.MIN_CLAIM_AGE, social_security_mod.MAX_CLAIM_AGE + 1))
        self.assertEqual({r["claim_ages"]["alex"] for r in result["results"]}, expected_ages)
        self.assertEqual(result["best_value"], max(r["value"] for r in result["results"]))

    def test_candidate_ages_restricts_the_search(self):
        scenario = self._scenario()
        profile, account_rows, blended_rate = self._setup(scenario)
        result = opt.search_claim_ages(profile, scenario, account_rows, blended_rate, heir_tax_rate=0.24,
                                       candidate_ages=[62, 70])
        self.assertEqual(len(result["results"]), 2)
        self.assertEqual({r["claim_ages"]["alex"] for r in result["results"]}, {62, 70})

    def test_no_mortality_favors_claiming_as_late_as_possible(self):
        scenario = self._scenario()
        profile, account_rows, blended_rate = self._setup(scenario)
        result = opt.search_claim_ages(profile, scenario, account_rows, blended_rate, heir_tax_rate=0.24,
                                       candidate_ages=[62, 70])
        self.assertEqual(result["best_claim_ages"], {"alex": 70})

    def test_early_mortality_flips_the_optimum_toward_claiming_early(self):
        scenario = self._scenario(mortality={"primary_death_date": date(2032, 1, 1)})  # dies at 70
        profile, account_rows, blended_rate = self._setup(scenario)
        result = opt.search_claim_ages(profile, scenario, account_rows, blended_rate, heir_tax_rate=0.24,
                                       candidate_ages=[62, 70])
        self.assertEqual(result["best_claim_ages"], {"alex": 62})


class LifeExpectancyScoringTests(unittest.TestCase):
    """estate_valuation_row()/_life_expectancy_end_year() -- every
    function in this package scores against the household's OWN stated
    retirement.life_expectancy (the LATER of the two, for a couple), not
    necessarily project()'s own last row -- see optimizer.py's module
    docstring on why `pt.cli project`/`report`/`monte-carlo` are
    unaffected (they always use project()'s own last row, governed by
    scenario.mortality or age 100, exactly as before this existed).
    Falls back to the last row when life_expectancy isn't configured for
    anyone -- the default for every other test in this file, which is
    exactly why none of them needed updating when this was added."""

    def _scenario(self, life_expectancy=None, spouse_life_expectancy=None, with_spouse=False):
        people = {"primary": {"name": "alex", "birthdate": date(1962, 1, 1)}}
        if with_spouse:
            people["spouse"] = {"name": "sam", "birthdate": date(1965, 1, 1)}
        retirement = {"retirement_age": 62}
        if with_spouse:
            retirement["spouse_retirement_age"] = 62
        if life_expectancy is not None:
            retirement["life_expectancy"] = life_expectancy
        if spouse_life_expectancy is not None:
            retirement["spouse_life_expectancy"] = spouse_life_expectancy
        return {
            "people": people,
            "retirement": retirement,
            "income": {"target_spending": 0},
            "inflation": {"general": 2.5},
            "returns": {"growth": 4.0, "fixed_income": 4.0},
            "hypothetical_accounts": [
                {"account_number": "trad1", "account_type": "IRA", "owner": "alex",
                 "value": 500000, "pct_growth": 60},
            ],
        }

    def test_no_life_expectancy_configured_falls_back_to_the_last_row(self):
        scenario = self._scenario()
        profile = {"people": scenario["people"]}
        rows = [{"year": 2026}, {"year": 2027}, {"year": 2028}]
        self.assertIs(opt.estate_valuation_row(rows, scenario, profile), rows[-1])

    def test_picks_the_row_matching_birth_year_plus_life_expectancy(self):
        scenario = self._scenario(life_expectancy=80)  # alex born 1962 -> target year 2042
        profile = {"people": scenario["people"]}
        rows = [{"year": y} for y in range(2026, 2063)]
        row = opt.estate_valuation_row(rows, scenario, profile)
        self.assertEqual(row["year"], 2042)

    def test_uses_the_later_of_the_two_life_expectancies_for_a_couple(self):
        # alex (1962) + 70 = 2032; sam (1965) + 90 = 2055 -- sam's is later.
        scenario = self._scenario(life_expectancy=70, spouse_life_expectancy=90, with_spouse=True)
        profile = {"people": scenario["people"]}
        rows = [{"year": y} for y in range(2026, 2063)]
        row = opt.estate_valuation_row(rows, scenario, profile)
        self.assertEqual(row["year"], 2055)

    def test_target_year_past_the_last_row_falls_back_to_the_last_row(self):
        scenario = self._scenario(life_expectancy=150)  # absurdly far out
        profile = {"people": scenario["people"]}
        rows = [{"year": 2026}, {"year": 2027}, {"year": 2028}]
        self.assertIs(opt.estate_valuation_row(rows, scenario, profile), rows[-1])

    def test_shorter_life_expectancy_shifts_claim_age_optimum_earlier(self):
        """Integration check, through search_claim_ages(): with a
        realistic (non-extreme) growth rate, a short life expectancy
        should favor claiming EARLY and a long one should favor claiming
        LATE -- proving optimize()-family functions actually pick this up
        end to end, not just the unit-level row selection above."""
        def result_for(life_expectancy):
            scenario = self._scenario(life_expectancy=life_expectancy)
            scenario["social_security"] = {"alex": {"pia_monthly": 2800, "claim_age": 67}}
            profile = {"people": scenario["people"]}
            account_rows, allocation_rows, total = proj.build_hypothetical_accounts(scenario, profile)
            blended_rate = proj.compute_blended_growth_rate(allocation_rows, total, scenario)
            return opt.search_claim_ages(profile, scenario, account_rows, blended_rate, heir_tax_rate=0.24,
                                         candidate_ages=[62, 70])

        short = result_for(65)
        long = result_for(95)
        self.assertEqual(short["best_claim_ages"], {"alex": 62})
        self.assertEqual(long["best_claim_ages"], {"alex": 70})


class IterationLogTests(unittest.TestCase):
    """optimize()'s optional iteration_log parameter (see that function's
    docstring) and the lifetime_taxes/zero_conversions_lifetime_taxes/
    original_lifetime_taxes it pairs with value/zero_conversions_value/
    original_value in the returned dict -- see roth_optimizer/cli.py's
    `optimize --save-iterations` and roth_optimizer/scatter_chart.py,
    which both consume this."""

    def setUp(self):
        self.scenario = _hypothetical_scenario()
        self.profile = {"people": self.scenario["people"]}
        self.account_rows, self.allocation_rows, self.total = proj.build_hypothetical_accounts(
            self.scenario, self.profile
        )
        self.blended_rate = proj.compute_blended_growth_rate(self.allocation_rows, self.total, self.scenario)

    def test_iteration_log_gets_one_entry_per_evaluation(self):
        log = []
        result = opt.optimize(
            self.profile, self.scenario, self.account_rows, self.blended_rate,
            heir_tax_rate=0.24, restarts=2, seed=1, iteration_log=log,
        )
        self.assertEqual(len(log), result["evaluations"])
        for entry in log:
            self.assertIn("lifetime_taxes", entry)
            self.assertIn("after_tax_estate_value", entry)
            self.assertGreaterEqual(entry["lifetime_taxes"], 0)

    def test_omitting_iteration_log_changes_nothing_else(self):
        """Backward compatibility -- a plain optimize() call (no
        iteration_log) must return the exact same winning schedule/value
        as one that also happens to log every iteration."""
        kwargs = dict(profile=self.profile, scenario=self.scenario, account_rows=self.account_rows,
                      blended_rate=self.blended_rate, heir_tax_rate=0.24, restarts=2, seed=5)
        plain = opt.optimize(**kwargs)
        logged = opt.optimize(iteration_log=[], **kwargs)
        self.assertEqual(plain["schedule"], logged["schedule"])
        self.assertEqual(plain["value"], logged["value"])

    def test_result_carries_lifetime_taxes_paired_with_each_value(self):
        result = opt.optimize(
            self.profile, self.scenario, self.account_rows, self.blended_rate,
            heir_tax_rate=0.24, restarts=1, seed=1,
        )
        self.assertGreaterEqual(result["lifetime_taxes"], 0)
        self.assertGreaterEqual(result["zero_conversions_lifetime_taxes"], 0)
        # original_value/original_lifetime_taxes are a pair -- present
        # together, or None together (see optimize()'s Returns docstring).
        self.assertEqual(result["original_value"] is None, result["original_lifetime_taxes"] is None)

    def test_original_value_none_leaves_original_lifetime_taxes_none_too(self):
        # owner/start_date/end_date with no annual_amount and no
        # constraints.max_conversion_per_year -- nothing for project() to
        # run "as configured" -- original_value (a ProjectionError) must
        # be None, and original_lifetime_taxes right along with it.
        scenario = _hypothetical_scenario(constraints=None)
        profile = {"people": scenario["people"]}
        account_rows, allocation_rows, total = proj.build_hypothetical_accounts(scenario, profile)
        blended_rate = proj.compute_blended_growth_rate(allocation_rows, total, scenario)
        result = opt.optimize(profile, scenario, account_rows, blended_rate, heir_tax_rate=0.24,
                              restarts=1, seed=1)
        self.assertIsNone(result["original_value"])
        self.assertIsNone(result["original_lifetime_taxes"])

    def test_original_lifetime_taxes_present_when_original_value_is(self):
        scenario = _hypothetical_scenario(annual_amount=20000)
        profile = {"people": scenario["people"]}
        account_rows, allocation_rows, total = proj.build_hypothetical_accounts(scenario, profile)
        blended_rate = proj.compute_blended_growth_rate(allocation_rows, total, scenario)
        result = opt.optimize(profile, scenario, account_rows, blended_rate, heir_tax_rate=0.24,
                              restarts=1, seed=1)
        self.assertIsNotNone(result["original_value"])
        self.assertIsNotNone(result["original_lifetime_taxes"])
        self.assertGreaterEqual(result["original_lifetime_taxes"], 0)

    def test_lifetime_taxes_paid_sums_only_through_the_target_row(self):
        rows = [
            {"year": 2026, "tax": 1000},
            {"year": 2027, "tax": 2000},
            {"year": 2028, "tax": 3000},
            {"year": 2029, "tax": 4000},
        ]
        self.assertEqual(opt.lifetime_taxes_paid(rows, rows[2]), 6000)  # 1000+2000+3000, stop at 2028
        self.assertEqual(opt.lifetime_taxes_paid(rows, rows[-1]), 10000)  # every row


class ProjectionAgesTests(unittest.TestCase):
    """optimize()'s projection_ages/age_projections/life_expectancy_age
    (see that function's docstring) -- the configurable-age comparison
    trajectories and the household's own age at the life-expectancy
    point, both added for the scatter chart's optional hollow markers
    (see scatter_chart.py's age_projections/winner_age)."""

    def setUp(self):
        self.scenario = _hypothetical_scenario()
        self.profile = {"people": self.scenario["people"]}
        self.account_rows, self.allocation_rows, self.total = proj.build_hypothetical_accounts(
            self.scenario, self.profile
        )
        self.blended_rate = proj.compute_blended_growth_rate(self.allocation_rows, self.total, self.scenario)

    def test_omitting_projection_ages_leaves_age_projections_none(self):
        # Default (no projection_ages) -- must cost nothing extra and
        # both new keys must be None, not empty structures.
        result = opt.optimize(
            self.profile, self.scenario, self.account_rows, self.blended_rate,
            heir_tax_rate=0.24, restarts=1, seed=1,
        )
        self.assertIsNone(result["projection_ages"])
        self.assertIsNone(result["age_projections"])

    def test_projection_ages_echoed_back_sorted_and_deduplicated(self):
        result = opt.optimize(
            self.profile, self.scenario, self.account_rows, self.blended_rate,
            heir_tax_rate=0.24, restarts=1, seed=1, projection_ages=[95, 85, 95, 90],
        )
        self.assertEqual(result["projection_ages"], [85, 90, 95])

    def test_age_projections_shape_and_nonnegative_values(self):
        result = opt.optimize(
            self.profile, self.scenario, self.account_rows, self.blended_rate,
            heir_tax_rate=0.24, restarts=1, seed=1, projection_ages=[85, 95],
        )
        age_projections = result["age_projections"]
        self.assertEqual(set(age_projections), {"winner", "zero_conversions", "original"})
        for key in ("winner", "zero_conversions"):
            points = age_projections[key]
            self.assertEqual([p["age"] for p in points], [85, 95])
            for p in points:
                self.assertGreaterEqual(p["lifetime_taxes"], 0)
        # _hypothetical_scenario()'s roth_conversions window has
        # max_conversion_per_year configured, so "as configured" runs
        # cleanly here -- "original" should be populated right alongside
        # original_value, not None.
        self.assertIsNotNone(age_projections["original"])
        self.assertEqual([p["age"] for p in age_projections["original"]], [85, 95])

    def test_original_age_projections_none_when_original_value_is_none(self):
        # Same fixture as IterationLogTests'
        # test_original_value_none_leaves_original_lifetime_taxes_none_too
        # -- "original" must be None right along with original_value, for
        # the same reason (nothing "as configured" for project() to run).
        scenario = _hypothetical_scenario(constraints=None)
        profile = {"people": scenario["people"]}
        account_rows, allocation_rows, total = proj.build_hypothetical_accounts(scenario, profile)
        blended_rate = proj.compute_blended_growth_rate(allocation_rows, total, scenario)
        result = opt.optimize(profile, scenario, account_rows, blended_rate, heir_tax_rate=0.24,
                              restarts=1, seed=1, projection_ages=[90])
        self.assertIsNone(result["age_projections"]["original"])

    def test_life_expectancy_age_none_when_unconfigured(self):
        # _hypothetical_scenario() never sets retirement.life_expectancy.
        result = opt.optimize(
            self.profile, self.scenario, self.account_rows, self.blended_rate,
            heir_tax_rate=0.24, restarts=1, seed=1,
        )
        self.assertIsNone(result["life_expectancy_age"])

    def test_life_expectancy_age_matches_birth_year_and_target_year(self):
        # alex born 1962; life_expectancy=80 -> target year 2042 -> age 80
        # at that row (see LifeExpectancyScoringTests, same arithmetic).
        scenario = _hypothetical_scenario()
        scenario["retirement"]["life_expectancy"] = 80
        profile = {"people": scenario["people"]}
        account_rows, allocation_rows, total = proj.build_hypothetical_accounts(scenario, profile)
        blended_rate = proj.compute_blended_growth_rate(allocation_rows, total, scenario)
        result = opt.optimize(profile, scenario, account_rows, blended_rate, heir_tax_rate=0.24,
                              restarts=1, seed=1)
        self.assertEqual(result["life_expectancy_age"], 80)

    def test_project_without_mortality_ignores_configured_mortality(self):
        """_project_without_mortality() must reach the primary's own
        age-100 year even when scenario.mortality would otherwise cut
        the run short -- that's the whole point (a second, independent
        horizon from estate_valuation_row()'s life-expectancy row, which
        DOES respect scenario.mortality/life_expectancy)."""
        scenario = _hypothetical_scenario()
        scenario["mortality"] = {"alex": {"death_year": 2030}}  # otherwise cuts project() off at 2030
        rows = opt._project_without_mortality(
            self.profile, scenario, self.account_rows, self.blended_rate,
        )
        birth_year = self.scenario["people"]["primary"]["birthdate"].year
        self.assertEqual(rows[-1]["year"], birth_year + 100)

    def test_row_at_age_picks_the_row_matching_birth_year_plus_age(self):
        rows = [{"year": y} for y in range(2026, 2063)]
        # alex born 1962 -- age 80 -> year 2042.
        row = opt._row_at_age(rows, self.profile, 80)
        self.assertEqual(row["year"], 2042)

    def test_row_at_age_past_the_last_row_falls_back_to_the_last_row(self):
        rows = [{"year": 2026}, {"year": 2027}, {"year": 2028}]
        self.assertIs(opt._row_at_age(rows, self.profile, 150), rows[-1])

    def test_later_age_lifetime_taxes_at_least_earlier_age_lifetime_taxes(self):
        """lifetime_taxes_paid() sums a non-negative field, so a later
        age's total can only be >= an earlier age's, never less --
        across every schedule projection_ages produces a trajectory
        for."""
        result = opt.optimize(
            self.profile, self.scenario, self.account_rows, self.blended_rate,
            heir_tax_rate=0.24, restarts=1, seed=1, projection_ages=[80, 100],
        )
        for key in ("winner", "zero_conversions", "original"):
            points = result["age_projections"][key]
            self.assertGreaterEqual(points[1]["lifetime_taxes"], points[0]["lifetime_taxes"])

    def test_single_project_call_per_schedule_regardless_of_age_count(self):
        """The whole point of reading every requested age out of ONE
        _project_without_mortality() run (see that function's own
        docstring) is that project() isn't re-run per age -- evaluations
        (which only counts the search's own calls, not these) should be
        identical whether projection_ages has one age or five, and the
        two runs' own age_projections should agree at the ages they
        share."""
        kwargs = dict(profile=self.profile, scenario=self.scenario, account_rows=self.account_rows,
                      blended_rate=self.blended_rate, heir_tax_rate=0.24, restarts=1, seed=1)
        one_age = opt.optimize(projection_ages=[90], **kwargs)
        five_ages = opt.optimize(projection_ages=[70, 80, 90, 95, 100], **kwargs)
        self.assertEqual(one_age["evaluations"], five_ages["evaluations"])
        one_pt = one_age["age_projections"]["winner"][0]
        five_pt = next(p for p in five_ages["age_projections"]["winner"] if p["age"] == 90)
        self.assertEqual(one_pt["lifetime_taxes"], five_pt["lifetime_taxes"])
        self.assertEqual(one_pt["after_tax_estate_value"], five_pt["after_tax_estate_value"])


class ScatterChartTests(unittest.TestCase):
    """roth_optimizer/scatter_chart.py -- the self-contained HTML
    `optimize --save-iterations *.html` writes (see cli.py's
    _save_iteration_output()). Light-touch checks only (this isn't a
    browser test suite): the right data lands in the page, nothing more."""

    # A small age_projections fixture -- same shape optimize() returns
    # (see that function's docstring), reused across several tests below.
    _AGE_PROJECTIONS = {
        "winner": [{"age": 85, "lifetime_taxes": 210000.0, "after_tax_estate_value": 990000.0},
                   {"age": 100, "lifetime_taxes": 230000.0, "after_tax_estate_value": 1200000.0}],
        "zero_conversions": [{"age": 85, "lifetime_taxes": 130000.0, "after_tax_estate_value": 880000.0},
                              {"age": 100, "lifetime_taxes": 300000.0, "after_tax_estate_value": 1000000.0}],
        "original": [{"age": 85, "lifetime_taxes": 180000.0, "after_tax_estate_value": 930000.0},
                     {"age": 100, "lifetime_taxes": 250000.0, "after_tax_estate_value": 1100000.0}],
    }

    def test_render_embeds_every_point_and_the_reference_markers(self):
        log = [
            {"lifetime_taxes": 100000.0, "after_tax_estate_value": 900000.0},
            {"lifetime_taxes": 150000.0, "after_tax_estate_value": 950000.0},
        ]
        html = scatter_chart_mod.render_scatter_html(
            log, winner=(150000.0, 950000.0), zero_point=(80000.0, 850000.0),
            original_point=(120000.0, 910000.0), heir_tax_rate=0.24, scenario_label="test.yaml",
            seed=7, restarts=2, evaluations=len(log),
            winner_age=82, age_projections=self._AGE_PROJECTIONS,
        )
        self.assertIn("<!doctype html>", html.lower())
        self.assertIn("[[100000,900000],[150000,950000]]", html)
        self.assertIn("test.yaml", html)
        self.assertIn("No conversions", html)
        self.assertIn("Current plan", html)
        self.assertIn("const WINNER_AGE = 82;", html)
        # AGE_SERIES (compact separators) -- the zero_conversions age-85 point.
        self.assertIn('"age":85,"x":130000,"y":880000', html)
        # requested ages surfaced in the card-sub copy, not buried only in JSON.
        self.assertIn("age 85, 100", html)

    def test_parse_scatter_html_round_trips_what_render_embedded(self):
        log = [
            {"lifetime_taxes": 100000.0, "after_tax_estate_value": 900000.0},
            {"lifetime_taxes": 150000.0, "after_tax_estate_value": 950000.0},
        ]
        html = scatter_chart_mod.render_scatter_html(
            log, winner=(150000.0, 950000.0), zero_point=(80000.0, 850000.0),
            original_point=(120000.0, 910000.0), heir_tax_rate=0.24, scenario_label="test.yaml",
            seed=7, restarts=2, evaluations=len(log),
            winner_age=82, age_projections=self._AGE_PROJECTIONS,
        )
        data = scatter_chart_mod.parse_scatter_html(html)
        self.assertEqual(data["points"], [[100000, 900000], [150000, 950000]])
        self.assertEqual(data["winner"], [150000, 950000])
        self.assertEqual([r["label"] for r in data["refs"]], ["No conversions", "Current plan"])
        self.assertEqual(data["winner_age"], 82)
        self.assertIn(85, [pt["age"] for pt in data["age_series"]["zero"]])

    def test_parse_scatter_html_rejects_something_that_is_not_a_chart(self):
        with self.assertRaises(scatter_chart_mod.ScatterChartParseError):
            scatter_chart_mod.parse_scatter_html("<html><body>hello</body></html>")

    def test_render_html_escapes_the_scenario_label(self):
        html = scatter_chart_mod.render_scatter_html(
            [{"lifetime_taxes": 1.0, "after_tax_estate_value": 2.0}],
            winner=(1.0, 2.0), zero_point=(1.0, 2.0), original_point=None, heir_tax_rate=0.24,
            scenario_label="<script>alert(1)</script>.yaml", seed=1, restarts=1, evaluations=1,
            winner_age=None, age_projections=None,
        )
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;.yaml", html)

    def test_render_without_original_point_or_age_projections(self):
        """No original_point AND no age_projections -- the plain,
        pre-age-projections two-reference-point chart. Both omissions
        are independent (original_point=None with age_projections given
        is covered by test_render_embeds_every_point_and_the_reference_
        markers's "original" series; this test is the "neither" case)."""
        log = [{"lifetime_taxes": 100000.0, "after_tax_estate_value": 900000.0}]
        html = scatter_chart_mod.render_scatter_html(
            log, winner=(100000.0, 900000.0), zero_point=(80000.0, 850000.0),
            original_point=None, heir_tax_rate=0.24, scenario_label="test.yaml",
            seed=None, restarts=1, evaluations=len(log),
            winner_age=None,
        )
        self.assertNotIn("Current plan", html)
        self.assertIn("No conversions", html)
        # winner_age=None -- WINNER_AGE must serialize as JSON null (not
        # the Python "None"); the JS ternary that falls back to plain
        # "Optimized" (no age suffix) at render time is checked directly
        # in test_script_braces_and_parens_are_balanced.
        self.assertIn("const WINNER_AGE = null;", html)
        # age_projections omitted -- every AGE_SERIES key must be empty,
        # and the card-sub copy must NOT claim to trace any ages.
        self.assertIn('"winner":[],"zero":[],"original":[]', html)
        self.assertNotIn("Hollow markers trace", html)

    def test_original_series_empty_when_original_point_is_none(self):
        """age_projections given, but original_point (and, consistently,
        age_projections["original"]) is None -- the "original" AGE_SERIES
        entry must come out empty, independent of winner/zero_conversions
        still being populated."""
        log = [{"lifetime_taxes": 100000.0, "after_tax_estate_value": 900000.0}]
        age_projections = dict(self._AGE_PROJECTIONS, original=None)
        html = scatter_chart_mod.render_scatter_html(
            log, winner=(100000.0, 900000.0), zero_point=(80000.0, 850000.0),
            original_point=None, heir_tax_rate=0.24, scenario_label="test.yaml",
            seed=None, restarts=1, evaluations=len(log),
            winner_age=None, age_projections=age_projections,
        )
        self.assertIn('"original":[]', html)
        self.assertIn('"winner":[{"age":85', html)

    def test_script_braces_and_parens_are_balanced(self):
        """Not a real JS parser -- but a template edit that drops a
        closing brace (e.g. while removing/moving a block) silently
        renders a blank page with no error the string-content checks
        above would catch; this at least catches THAT class of mistake
        without requiring node/a JS engine in the test environment,
        consistent with the rest of this project staying dependency-free."""
        log = [{"lifetime_taxes": 100000.0, "after_tax_estate_value": 900000.0}]
        html = scatter_chart_mod.render_scatter_html(
            log, winner=(100000.0, 900000.0), zero_point=(80000.0, 850000.0),
            original_point=(90000.0, 880000.0), heir_tax_rate=0.24, scenario_label="test.yaml",
            seed=1, restarts=1, evaluations=len(log),
            winner_age=80, age_projections=self._AGE_PROJECTIONS,
        )
        script = html.split("<script>", 1)[1].split("</script>", 1)[0]
        self.assertEqual(script.count("{"), script.count("}"))
        self.assertEqual(script.count("("), script.count(")"))
        # Every drawing function this module defines must actually get
        # invoked -- a function left declared-but-uncalled (e.g. from a
        # dropped forEach line during an edit) is exactly the kind of
        # silent breakage a "still compiles" check wouldn't catch.
        for fn in ("refHLine", "refMarker", "polyline", "ageMarker", "drawAgeSeries"):
            self.assertIn(f"function {fn}(", script)
        self.assertIn("REFS.forEach(refHLine)", script)
        self.assertIn("REFS.forEach(refMarker)", script)
        self.assertIn("drawAgeSeries('zero', REFS[0], 'diamond', 'age-ref', REFS[0].label)", script)
        self.assertIn("drawAgeSeries('original', REFS[1], 'square', 'age-ref', REFS[1].label)", script)
        self.assertIn("drawAgeSeries('winner', { x: WINNER[0], y: WINNER[1] }, 'circle', 'age-winner', "
                       "'Optimized')", script)


if __name__ == "__main__":
    unittest.main()
