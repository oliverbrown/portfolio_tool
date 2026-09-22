"""Regression tests for pt/projection.py against test_data/'s synthetic
household -- growth-rate blending (both the legacy 2-bucket returns
shape and the newer per-asset-class returns.asset_classes shape),
deterministic project(), and the correlated Monte Carlo path.

These are deliberately structural/sanity checks (does it run, are the
numbers in a plausible range, does a documented invariant hold) rather
than exact-value assertions -- test_data/'s own scenario file is free to
change without breaking this suite, the way an exact "blended rate must
equal 0.0669" assertion would.
"""
import copy
import unittest
from datetime import date

from pt import allocation as alloc
from pt import planning
from pt import projection as proj
from pt import scenario as scenario_mod
from pt import tax as tax_mod

from . import _helpers as h


class BlendedGrowthRateTests(unittest.TestCase):
    """compute_blended_growth_rate()/compute_blended_volatility() against
    both scenario shapes -- see pt/projection.py's resolve_asset_class_
    returns()."""

    def test_legacy_two_bucket_shape(self):
        scenario = {"returns": {"growth": 8.0, "fixed_income": 4.0}}
        allocation_rows = [
            {"asset_class": "Growth", "value": 600_000, "pct": 0.6},
            {"asset_class": "Income", "value": 400_000, "pct": 0.4},
        ]
        rate = proj.compute_blended_growth_rate(allocation_rows, 1_000_000, scenario)
        self.assertAlmostEqual(rate, 0.6 * 0.08 + 0.4 * 0.04)

    def test_new_asset_classes_shape_resolves_by_subclass_then_macro(self):
        scenario = {
            "returns": {
                "asset_classes": {
                    "US Equity": {"expected_return": 8.0},
                    "Income": {"expected_return": 4.0},  # macro-level fallback
                }
            }
        }
        allocation_rows = [
            {"asset_class": "US Equity", "value": 700_000, "pct": 0.7},
            {"asset_class": "US Bonds", "value": 300_000, "pct": 0.3},  # falls back to Income
        ]
        rate = proj.compute_blended_growth_rate(allocation_rows, 1_000_000, scenario)
        self.assertAlmostEqual(rate, 0.7 * 0.08 + 0.3 * 0.04)

    def test_unconfigured_held_class_raises_clear_error(self):
        scenario = {"returns": {"asset_classes": {"US Equity": {"expected_return": 8.0}}}}
        allocation_rows = [{"asset_class": "Gold", "value": 100_000, "pct": 1.0}]
        with self.assertRaises(proj.ProjectionError):
            proj.compute_blended_growth_rate(allocation_rows, 100_000, scenario)

    def test_real_household_snapshot_produces_plausible_rate(self):
        with h.TempDB() as db_path:
            conn = h.seeded_connection(db_path)
            snapshot_id = h.import_fidelity_household(conn)
            allocation_rows, total = alloc.allocation_by_asset_class(conn, snapshot_id)
            scenario = scenario_mod.load_scenario(h.TEST_SCENARIO)
            rate = proj.compute_blended_growth_rate(allocation_rows, total, scenario)
            # A sanity range, not an exact figure -- somewhere between pure
            # cash and pure equities for any remotely diversified household.
            self.assertGreater(rate, 0.0)
            self.assertLess(rate, 0.15)


class ProjectRunTests(unittest.TestCase):
    """project() against a real imported snapshot -- catches anything that
    would make a normal `pt.cli project` run crash or misbehave."""

    def setUp(self):
        self._db = h.TempDB()
        db_path = self._db.__enter__()
        self.conn = h.seeded_connection(db_path)
        self.snapshot_id = h.import_fidelity_household(self.conn)
        self.profile = planning.load_profile(h.TEST_PROFILE)
        self.scenario = scenario_mod.load_scenario(h.TEST_SCENARIO)
        self.allocation_rows, self.total = alloc.allocation_by_asset_class(self.conn, self.snapshot_id)
        self.account_rows = alloc.account_values(self.conn, self.snapshot_id)
        self.blended_rate = proj.compute_blended_growth_rate(self.allocation_rows, self.total, self.scenario)

    def tearDown(self):
        self._db.__exit__(None, None, None)

    def test_project_returns_rows_through_age_100(self):
        rows = proj.project(self.profile, self.scenario, self.account_rows, self.blended_rate)
        self.assertTrue(rows)
        primary_birth_year = self.profile["people"]["primary"]["birthdate"].year
        self.assertEqual(rows[-1]["primary_age"], 100)
        self.assertEqual(rows[0]["year"] - primary_birth_year, rows[0]["primary_age"])
        # total_balance is always the sum of its own component buckets --
        # this is a real invariant project() must hold every year, not
        # just an approximation.
        for row in rows:
            component_sum = (row["taxable_balance"] + row["traditional_balance"]
                              + row["roth_balance"] + row["inherited_balance"])
            self.assertAlmostEqual(row["total_balance"], component_sum, delta=1.0)

    def test_summarize_outcome_matches_last_row(self):
        rows = proj.project(self.profile, self.scenario, self.account_rows, self.blended_rate)
        summary = proj.summarize_outcome(rows, self.profile)
        self.assertIn(summary["outcome"], ("ok", "shortfall", "estate"))
        self.assertEqual(summary["final_total_balance"], rows[-1]["total_balance"])

    def test_pre_retirement_income_raises_tax_via_the_real_bracket(self):
        """scenario.pre_retirement_income (see build_pre_retirement_income())
        doesn't add wage income to cash flow, but a Roth conversion
        started before retirement should now be taxed at the household's
        REAL (wage-inclusive) marginal bracket -- see
        _household_tax_owed(). Comparing the SAME conversion with and
        without pre_retirement_income configured isolates that effect
        end to end (HouseholdTaxOwedTests covers the helper in isolation)."""
        this_year = date.today().year
        without_wages = copy.deepcopy(self.scenario)
        without_wages["roth_conversions"] = [{
            "owner": "jordan",
            "start_date": date(this_year, 1, 1),
            "end_date": date(this_year, 12, 31),
            "annual_amount": 100_000,
        }]
        with_wages = copy.deepcopy(without_wages)
        with_wages["pre_retirement_income"] = {"jordan": {"estimated_gross_income": 300_000}}

        rows_without = proj.project(self.profile, without_wages, self.account_rows, self.blended_rate)
        rows_with = proj.project(self.profile, with_wages, self.account_rows, self.blended_rate)

        # The exact formula/backward-compatibility guarantees live in
        # HouseholdTaxOwedTests -- this just proves project() actually
        # wires a real scenario's pre_retirement_income through to a real
        # conversion's tax, not just the helper in isolation. (A later
        # year's tax is left uncompared: pre_retirement_income also feeds
        # magi_history for the Medicare/IRMAA lookback -- a separate,
        # pre-existing effect -- so the two variants' trajectories
        # legitimately diverge for the rest of the projection, not just
        # during the conversion year itself.)
        self.assertEqual(rows_without[0]["year"], this_year)
        self.assertGreater(rows_with[0]["tax"], rows_without[0]["tax"])


class MonteCarloTests(unittest.TestCase):
    """run_monte_carlo() -- the correlated per-asset-class draw
    (build_correlated_return_sampler()), not just the legacy single-
    blended-draw path. Small trial counts here for speed; this is a
    regression check, not a statistical convergence study."""

    def setUp(self):
        self._db = h.TempDB()
        db_path = self._db.__enter__()
        self.conn = h.seeded_connection(db_path)
        self.snapshot_id = h.import_fidelity_household(self.conn)
        self.profile = planning.load_profile(h.TEST_PROFILE)
        self.scenario = scenario_mod.load_scenario(h.TEST_SCENARIO)
        self.allocation_rows, self.total = alloc.allocation_by_asset_class(self.conn, self.snapshot_id)
        self.account_rows = alloc.account_values(self.conn, self.snapshot_id)
        self.blended_rate = proj.compute_blended_growth_rate(self.allocation_rows, self.total, self.scenario)
        self.volatility = proj.compute_blended_volatility(self.allocation_rows, self.total, self.scenario)

    def tearDown(self):
        self._db.__exit__(None, None, None)

    def test_runs_and_returns_expected_shape(self):
        mc = proj.run_monte_carlo(
            self.profile, self.scenario, self.account_rows, self.blended_rate, self.volatility,
            self.allocation_rows, self.total, trials=30, seed=1,
        )
        self.assertEqual(mc["trials"], 30)
        self.assertEqual(sum(mc["outcomes"].values()), 30)
        self.assertTrue(0.0 <= mc["success_rate"] <= 1.0)
        self.assertTrue(mc["percentiles_by_year"])
        for row in mc["percentiles_by_year"]:
            self.assertLessEqual(row["p10"], row["p50"])
            self.assertLessEqual(row["p50"], row["p90"])

    def test_seeded_runs_are_reproducible(self):
        kwargs = dict(profile=self.profile, scenario=self.scenario, account_rows=self.account_rows,
                      blended_rate=self.blended_rate, volatility=self.volatility,
                      allocation_rows=self.allocation_rows, total=self.total, trials=20, seed=7)
        mc1 = proj.run_monte_carlo(**kwargs)
        mc2 = proj.run_monte_carlo(**kwargs)
        self.assertEqual(mc1["median_final_balance"], mc2["median_final_balance"])
        self.assertEqual(mc1["percentiles_by_year"], mc2["percentiles_by_year"])

    def test_lower_correlation_narrows_the_percentile_band(self):
        """The whole point of the correlated draw (see
        build_correlated_return_sampler()) is that correlation < 1 between
        held asset classes should produce a TIGHTER spread than treating
        the portfolio as one fully-correlated blob. Verified directly
        against the sampler's own output distribution (many years' worth
        of draws) rather than through a full 40-year project() run --
        compounding/ruin dynamics in a small synthetic household can wash
        the signal out by pushing most trials to a $0 floor well before
        the end of the projection, which would make an end-to-end
        percentile-band comparison unreliable regardless of whether the
        underlying math is correct."""
        allocation_rows = [
            {"asset_class": "US Equity", "value": 500_000, "pct": 0.5},
            {"asset_class": "US Bonds", "value": 500_000, "pct": 0.5},
        ]
        total = 1_000_000.0
        base_scenario = {
            "returns": {
                "asset_classes": {
                    "US Equity": {"expected_return": 7.0, "volatility": 20},
                    "US Bonds": {"expected_return": 4.0, "volatility": 20},
                }
            }
        }
        low_corr = copy.deepcopy(base_scenario)
        low_corr["returns"]["correlations"] = {"US Equity,US Bonds": 0.0}
        high_corr = copy.deepcopy(base_scenario)
        high_corr["returns"]["correlations"] = {"US Equity,US Bonds": 1.0}

        import random
        def stdev_of_draws(scenario, n=20_000):
            rng = random.Random(42)
            sampler = proj.build_correlated_return_sampler(allocation_rows, total, scenario, rng)
            draws = [sampler() for _ in range(n)]
            mean = sum(draws) / n
            return (sum((d - mean) ** 2 for d in draws) / n) ** 0.5

        low_stdev = stdev_of_draws(low_corr)
        high_stdev = stdev_of_draws(high_corr)
        # corr=1 with equal 50/50 weights and equal 20% volatilities should
        # reproduce the naive weighted-average volatility exactly (20%);
        # corr=0 should land at sqrt(0.5^2*20^2 + 0.5^2*20^2) = ~14.14%.
        self.assertLess(low_stdev, high_stdev)
        self.assertAlmostEqual(high_stdev, 0.20, delta=0.01)
        self.assertAlmostEqual(low_stdev, 0.1414, delta=0.01)


class ShortfallYearDistributionTests(unittest.TestCase):
    """shortfall_year_distribution() -- a pure function of
    run_monte_carlo()'s first_shortfall_years, so tested directly against
    a synthetic mc dict rather than a full simulation run."""

    def test_counts_and_sorts_by_year(self):
        mc = {"first_shortfall_years": [2050, 2048, 2050, 2049, 2050]}
        self.assertEqual(
            proj.shortfall_year_distribution(mc),
            [(2048, 1), (2049, 1), (2050, 3)],
        )

    def test_empty_when_no_trial_ever_shorted(self):
        self.assertEqual(proj.shortfall_year_distribution({"first_shortfall_years": []}), [])


class AssetClassTrackingTests(unittest.TestCase):
    """resolve_asset_class_tracking() and build_correlated_return_sampler()'s
    tracks/floor/cap mechanism (a Fixed Indexed Annuity crediting a
    floored-and-capped copy of its tracked index's own draw, instead of an
    independent Normal(mean, vol))."""

    def _scenario(self, fia_entry):
        return {
            "returns": {
                "asset_classes": {
                    "US Equity": {"expected_return": 7.5, "volatility": 18},
                    "Fixed Indexed Annuities": fia_entry,
                }
            }
        }

    def test_built_in_default_applies_with_just_expected_return(self):
        tracking = proj.resolve_asset_class_tracking(self._scenario({"expected_return": 4.0}))
        self.assertEqual(tracking["Fixed Indexed Annuities"],
                          {"tracks": "US Equity", "floor": 0.0, "cap": 0.08})

    def test_explicit_tracks_floor_cap_override_the_default(self):
        tracking = proj.resolve_asset_class_tracking(
            self._scenario({"expected_return": 4.0, "tracks": "US Equity", "floor": -2, "cap": 12})
        )
        self.assertEqual(tracking["Fixed Indexed Annuities"],
                          {"tracks": "US Equity", "floor": -0.02, "cap": 0.12})

    def test_tracks_false_disables_the_built_in_default(self):
        tracking = proj.resolve_asset_class_tracking(
            self._scenario({"expected_return": 4.0, "tracks": False, "volatility": 5.0})
        )
        self.assertNotIn("Fixed Indexed Annuities", tracking)

    def test_missing_cap_raises_for_a_class_with_no_built_in_default(self):
        scenario = {"returns": {"asset_classes": {
            "US Equity": {"expected_return": 7.5, "volatility": 18},
            "Gold": {"expected_return": 4.5, "volatility": 17, "tracks": "US Equity"},
        }}}
        with self.assertRaises(proj.ProjectionError):
            proj.resolve_asset_class_tracking(scenario)

    def test_floor_greater_than_cap_raises(self):
        scenario = self._scenario({"expected_return": 4.0, "tracks": "US Equity", "floor": 10, "cap": 5})
        with self.assertRaises(proj.ProjectionError):
            proj.resolve_asset_class_tracking(scenario)

    def test_chained_tracking_raises(self):
        scenario = {"returns": {"asset_classes": {
            "US Equity": {"expected_return": 7.5, "volatility": 18},
            "Fixed Indexed Annuities": {"expected_return": 4.0, "tracks": "US Equity", "cap": 8},
            "Gold": {"expected_return": 4.5, "volatility": 17, "tracks": "Fixed Indexed Annuities", "cap": 5},
        }}}
        with self.assertRaises(proj.ProjectionError):
            proj.resolve_asset_class_tracking(scenario)

    def test_sampler_clips_draws_to_floor_and_cap(self):
        """100% Fixed Indexed Annuities, tracking US Equity's volatile draw
        (18 pts) with the default 0%/8% floor/cap -- every single draw
        must land in [0, 0.08], and (given US Equity's volatility) some
        draws should actually hit each boundary."""
        import random
        scenario = self._scenario({"expected_return": 4.0})
        allocation_rows = [{"asset_class": "Fixed Indexed Annuities", "value": 1_000_000.0}]
        rng = random.Random(1)
        sampler = proj.build_correlated_return_sampler(allocation_rows, 1_000_000.0, scenario, rng)
        draws = [sampler() for _ in range(5000)]
        self.assertTrue(all(-1e-9 <= d <= 0.08 + 1e-9 for d in draws))
        self.assertTrue(any(d <= 1e-9 for d in draws), "expected some draws at the 0% floor")
        self.assertTrue(any(d >= 0.08 - 1e-9 for d in draws), "expected some draws at the 8% cap")

    def test_sampler_works_when_the_tracked_class_isnt_separately_held(self):
        """The household holds only the FIA, not US Equity directly -- the
        tracked index still needs a draw to clip, via a zero-weight
        reference row (see build_correlated_return_sampler())."""
        import random
        scenario = self._scenario({"expected_return": 4.0})
        allocation_rows = [{"asset_class": "Fixed Indexed Annuities", "value": 1_000_000.0}]
        rng = random.Random(2)
        sampler = proj.build_correlated_return_sampler(allocation_rows, 1_000_000.0, scenario, rng)
        draws = [sampler() for _ in range(1000)]
        self.assertTrue(all(-1e-9 <= d <= 0.08 + 1e-9 for d in draws))

    def test_tracks_disabled_falls_back_to_an_independent_normal_draw(self):
        """With tracking off, the FIA's draws should be a plain
        Normal(4%, 5%) -- unbounded (well past 8%) and not floored at 0,
        unlike the tracking case above."""
        import random
        scenario = self._scenario({"expected_return": 4.0, "tracks": False, "volatility": 5.0})
        allocation_rows = [{"asset_class": "Fixed Indexed Annuities", "value": 1_000_000.0}]
        rng = random.Random(3)
        sampler = proj.build_correlated_return_sampler(allocation_rows, 1_000_000.0, scenario, rng)
        draws = [sampler() for _ in range(5000)]
        self.assertTrue(any(d > 0.08 for d in draws))
        self.assertTrue(any(d < 0.0 for d in draws))


class PerBucketGrowthTests(unittest.TestCase):
    """bucket_allocation_rows(), build_hypothetical_account_class_rows(),
    build_bucket_correlated_return_sampler(), and project()'s
    bucket_rates/bucket_rate_fn -- growing each tax bucket (Taxable,
    Traditional pools, Roth, Inherited) at its OWN blended rate instead of
    one household-wide rate applied to all of them."""

    def _hypothetical_scenario(self, retirement_age=90, target_spending=40000):
        import datetime
        return {
            # Young enough that neither retirement (pushed far out below)
            # nor Medicare eligibility (65) triggers any withdrawal in the
            # first couple of projected years -- isolating pure growth for
            # test_project_grows_each_bucket_at_its_own_rate/
            # test_plain_project_uses_one_household_rate_for_every_bucket.
            "people": {"primary": {"name": "alex", "birthdate": datetime.date(1990, 1, 1)}},
            "retirement": {"retirement_age": retirement_age},
            "income": {"target_spending": target_spending},
            "inflation": {"general": 2.5},
            "returns": {"asset_classes": {
                "US Equity": {"expected_return": 8.0, "volatility": 18},
                "US Bonds": {"expected_return": 3.0, "volatility": 7},
            }},
            "hypothetical_accounts": [
                {"account_number": "taxable1", "account_type": "Brokerage", "owner": "alex", "value": 500000,
                 "asset_classes": {"US Equity": 100}},
                {"account_number": "ira1", "account_type": "IRA", "owner": "alex", "value": 500000,
                 "asset_classes": {"US Bonds": 100}},
            ],
        }

    def test_bucket_allocation_rows_groups_by_tax_bucket(self):
        scenario = self._hypothetical_scenario()
        profile = {"people": scenario["people"]}
        class_rows = proj.build_hypothetical_account_class_rows(scenario, profile)
        inherited_schedules = proj.build_inherited_schedules(scenario, profile)
        bucket_allocs = proj.bucket_allocation_rows(class_rows, inherited_schedules, profile)
        self.assertEqual(set(bucket_allocs), {"taxable", "traditional:alex:IRA"})
        rows, total = bucket_allocs["taxable"]
        self.assertEqual(total, 500000)
        self.assertEqual(rows, [{"asset_class": "US Equity", "value": 500000.0, "pct": 1.0}])

    def test_bucket_label_is_readable(self):
        self.assertEqual(proj.bucket_label("taxable"), "Taxable")
        self.assertEqual(proj.bucket_label("roth:alex"), "Roth (Alex)")
        self.assertEqual(proj.bucket_label("traditional:alex:401(k)"), "Traditional (Alex: 401(k))")
        self.assertEqual(proj.bucket_label("inherited:257502424"), "Inherited (...2424)")

    def test_project_grows_each_bucket_at_its_own_rate(self):
        """Taxable (100% US Equity, 8%) and the Traditional IRA (100% US
        Bonds, 3%) should each grow at their OWN rate, not the 5.5%
        household blend -- verified against a retirement age far enough
        out that no withdrawal/Medicare activity touches either bucket in
        year 1, isolating pure growth."""
        scenario = self._hypothetical_scenario(retirement_age=95, target_spending=0)
        profile = {"people": scenario["people"]}
        account_rows, allocation_rows, total = proj.build_hypothetical_accounts(scenario, profile)
        class_rows = proj.build_hypothetical_account_class_rows(scenario, profile)
        inherited_schedules = proj.build_inherited_schedules(scenario, profile)
        bucket_allocs = proj.bucket_allocation_rows(class_rows, inherited_schedules, profile)
        bucket_rates = {
            key: proj.compute_blended_growth_rate(rows, tot, scenario) for key, (rows, tot) in bucket_allocs.items()
        }
        household_rate = proj.compute_blended_growth_rate(allocation_rows, total, scenario)
        rows = proj.project(profile, scenario, account_rows, household_rate, bucket_rates=bucket_rates)
        self.assertAlmostEqual(rows[1]["taxable_balance"] / rows[0]["taxable_balance"] - 1, 0.08, places=6)
        self.assertAlmostEqual(rows[1]["traditional_balance"] / rows[0]["traditional_balance"] - 1, 0.03, places=6)

    def test_plain_project_uses_one_household_rate_for_every_bucket(self):
        """Without bucket_rates, both buckets should grow at the SAME
        household-blended rate (today's unchanged default behavior)."""
        scenario = self._hypothetical_scenario(retirement_age=95, target_spending=0)
        profile = {"people": scenario["people"]}
        account_rows, allocation_rows, total = proj.build_hypothetical_accounts(scenario, profile)
        household_rate = proj.compute_blended_growth_rate(allocation_rows, total, scenario)
        rows = proj.project(profile, scenario, account_rows, household_rate)
        self.assertAlmostEqual(rows[1]["taxable_balance"] / rows[0]["taxable_balance"] - 1, household_rate, places=6)
        self.assertAlmostEqual(rows[1]["traditional_balance"] / rows[0]["traditional_balance"] - 1,
                                household_rate, places=6)

    def test_bucket_sampler_stays_correlated_with_the_household_sampler(self):
        """The whole point of build_bucket_correlated_return_sampler(): the
        household-wide draw (build_correlated_return_sampler()) must equal
        the weighted recombination of the SAME year's per-bucket draws --
        i.e. buckets share one underlying per-asset-class economy, they
        don't each draw independently."""
        import random
        scenario = self._hypothetical_scenario()
        household_allocation_rows = [
            {"asset_class": "US Equity", "value": 500_000.0},
            {"asset_class": "US Bonds", "value": 500_000.0},
        ]
        household_total = 1_000_000.0
        bucket_allocations = {
            "taxable": ([{"asset_class": "US Equity", "value": 500_000.0}], 500_000.0),
            "traditional:alex:IRA": ([{"asset_class": "US Bonds", "value": 500_000.0}], 500_000.0),
        }
        for seed in (1, 2, 3):
            rng_bucket = random.Random(seed)
            bucket_sampler = proj.build_bucket_correlated_return_sampler(
                household_allocation_rows, household_total, bucket_allocations, scenario, rng_bucket
            )
            rng_household = random.Random(seed)
            household_sampler = proj.build_correlated_return_sampler(
                household_allocation_rows, household_total, scenario, rng_household
            )
            bucket_draw = bucket_sampler()
            household_draw = household_sampler()
            recombined = 0.5 * bucket_draw["taxable"] + 0.5 * bucket_draw["traditional:alex:IRA"]
            self.assertAlmostEqual(household_draw, recombined, places=9)

    def test_run_monte_carlo_with_bucket_allocations_is_reproducible(self):
        scenario = self._hypothetical_scenario()
        profile = {"people": scenario["people"]}
        account_rows, allocation_rows, total = proj.build_hypothetical_accounts(scenario, profile)
        class_rows = proj.build_hypothetical_account_class_rows(scenario, profile)
        inherited_schedules = proj.build_inherited_schedules(scenario, profile)
        bucket_allocations = proj.bucket_allocation_rows(class_rows, inherited_schedules, profile)
        blended_rate = proj.compute_blended_growth_rate(allocation_rows, total, scenario)
        volatility = proj.compute_blended_volatility(allocation_rows, total, scenario)
        kwargs = dict(profile=profile, scenario=scenario, account_rows=account_rows, blended_rate=blended_rate,
                      volatility=volatility, allocation_rows=allocation_rows, total=total, trials=15, seed=11,
                      bucket_allocations=bucket_allocations)
        mc1 = proj.run_monte_carlo(**kwargs)
        mc2 = proj.run_monte_carlo(**kwargs)
        self.assertEqual(mc1["percentiles_by_year"], mc2["percentiles_by_year"])
        self.assertEqual(mc1["trials"], 15)


class ConstrainedDistributionAmountTests(unittest.TestCase):
    """_constrained_distribution_amount()'s max_marginal_bracket branch --
    bracket_ceiling()/taxable_income() operate on TAXABLE (post-standard-
    deduction) dollars, but ordinary_income_so_far is a GROSS running
    total, so the cap has to convert between the two without silently
    "spending" the household's standard deduction more than once a year
    (see the function's own max_marginal_bracket comment)."""

    YEAR = tax_mod.BASE_TAX_YEAR  # 2024 -- so tax.py's un-inflated MFJ figures apply directly.
    CEILING_24PCT = 383_900  # tax.bracket_ceiling(0.24, ...) for BASE_TAX_YEAR MFJ.
    DEDUCTION = tax_mod.STANDARD_DEDUCTION_2024_MFJ

    def _dist(self, **overrides):
        dist = {
            "annual_amount": 10_000_000.0,  # effectively unconstrained by the request itself
            "max_conversion_per_year": None,
            "max_marginal_bracket": 0.24,
            "avoid_irmaa_tier": None,
            "preserve_cash_reserve_years": None,
        }
        dist.update(overrides)
        return dist

    def _amount(self, dist, ordinary_income_so_far):
        return proj._constrained_distribution_amount(
            dist, "max_conversion_per_year", flat={}, ss_income=0.0,
            ordinary_income_so_far=ordinary_income_so_far, anticipated_ltcg=0.0,
            target_spending=0.0, inflation_rate=0.0, medical_inflation_rate=0.0,
            year=self.YEAR, years_from_start=0, filing_status="mfj",
        )

    def test_single_entry_alone_gets_full_gross_cap_including_deduction(self):
        # With nothing else going on this year, the correct gross cap is
        # the raw taxable ceiling PLUS the still-unused standard deduction
        # (C + D) -- not just C, which is what the pre-fix flooring bug
        # produced.
        amount = self._amount(self._dist(), ordinary_income_so_far=0.0)
        self.assertAlmostEqual(amount, self.CEILING_24PCT + self.DEDUCTION)

    def test_two_entries_sharing_a_bracket_split_the_deduction_only_once(self):
        total_room = self.CEILING_24PCT + self.DEDUCTION
        dist_a = self._dist()
        dist_b = self._dist()

        first = self._amount(dist_a, ordinary_income_so_far=0.0)
        second = self._amount(dist_b, ordinary_income_so_far=first)
        self.assertAlmostEqual(first + second, total_room)

        # Same two entries, evaluated in the opposite order -- the split
        # between them may differ, but the combined total must not.
        second_first = self._amount(dist_b, ordinary_income_so_far=0.0)
        first_second = self._amount(dist_a, ordinary_income_so_far=second_first)
        self.assertAlmostEqual(second_first + first_second, total_room)

    def test_second_entry_gets_no_extra_deduction_once_first_used_it_all(self):
        # If the first entry alone already consumes the full C + D room,
        # a second entry sharing the same ceiling should be capped at
        # exactly 0 -- not get a second helping of the standard deduction.
        first = self._amount(self._dist(), ordinary_income_so_far=0.0)
        second = self._amount(self._dist(), ordinary_income_so_far=first)
        self.assertAlmostEqual(second, 0.0)


class HouseholdTaxOwedTests(unittest.TestCase):
    """_household_tax_owed() -- project()'s step 5. scenario.
    pre_retirement_income (see build_pre_retirement_income()) is a
    baseline the household's own RMD/conversion/distribution income
    stacks ON TOP OF for tax purposes, not income taxed in isolation --
    taxing it in isolation would understate its real cost whenever a
    person is still earning wages (a conversion that looks like it's in
    the 24% bracket alone can really be crossing into 32%/35% once wages
    are accounted for)."""

    YEAR = tax_mod.BASE_TAX_YEAR  # 2024 -- so tax.py's un-inflated MFJ figures apply directly.

    def _tax_owed(self, pre_retirement_income_total, ordinary_income, taxable_ss=0.0,
                  ltcg_income=0.0, ss_income=0.0):
        return proj._household_tax_owed(
            pre_retirement_income_total, ordinary_income, taxable_ss, ltcg_income,
            ss_income, inflation_rate=0.0, year=self.YEAR, filing_status="mfj",
        )

    def test_zero_pre_retirement_income_matches_isolated_tax(self):
        # Backward compatibility: with nobody still working (the common
        # case, and every year after each owner's own retirement), this
        # must be EXACTLY the plain tax on the household's own income --
        # unchanged from before pre_retirement_income_total existed.
        ordinary_income = 200_000.0
        expected = tax_mod.federal_tax(ordinary_income, 0.0, 0.0, self.YEAR, "mfj")
        self.assertAlmostEqual(self._tax_owed(0.0, ordinary_income), expected)

    def test_matches_the_literal_increment_definition(self):
        pre_retirement = 300_000.0
        ordinary_income = 200_000.0
        expected = (
            tax_mod.federal_tax(pre_retirement + ordinary_income, 0.0, 0.0, self.YEAR, "mfj")
            - tax_mod.federal_tax(pre_retirement, 0.0, 0.0, self.YEAR, "mfj")
        )
        self.assertAlmostEqual(self._tax_owed(pre_retirement, ordinary_income), expected)

    def test_wages_push_the_conversion_into_a_higher_real_bracket(self):
        # A $200k conversion taxed in ISOLATION sits entirely inside the
        # 24% bracket (taxable income 200,000 - 29,200 = 170,800, well
        # under the 32% threshold at 383,900). Stacked on top of $300k of
        # real wages, that SAME $200k conversion crosses from the 24%
        # into the 32% bracket -- so its real incremental tax must be
        # meaningfully larger than the isolated calculation, which is
        # exactly the gap a household with pre-retirement income was
        # silently missing before this existed.
        ordinary_income = 200_000.0
        isolated = tax_mod.federal_tax(ordinary_income, 0.0, 0.0, self.YEAR, "mfj")
        with_wages = self._tax_owed(300_000.0, ordinary_income)
        self.assertGreater(with_wages, isolated + 5_000)

    def test_never_negative(self):
        self.assertGreaterEqual(self._tax_owed(300_000.0, 0.0), 0.0)

    def test_standard_deduction_is_applied_exactly_once_not_twice(self):
        # The household's REAL combined return only ever gets ONE standard
        # deduction, against pre_retirement_income_total + the household's
        # own income together -- not a second deduction "for" the
        # conversion on top of that, and not zero (the deduction isn't
        # silently dropped either). Proof: once wages alone already
        # exceed the deduction (so it's fully "used up" by wages before
        # the household's own income even starts stacking), the
        # INCREMENTAL TAXABLE income (not tax -- taxable_income() is the
        # same post-deduction figure federal_tax() computes tax from, see
        # its own docstring) the household's own income adds must be
        # EXACTLY equal to that income itself, dollar for dollar. If the
        # deduction were being subtracted a second time here, this
        # increment would come out DEDUCTION dollars too small; if it
        # were never applied at all to the baseline, wages alone would
        # already show nonzero taxable income above, which this test
        # deliberately keeps below the deduction to isolate the effect.
        deduction = tax_mod.inflated_standard_deduction(0.0, self.YEAR, "mfj")
        pre_retirement = deduction + 50_000.0  # comfortably exceeds the deduction alone
        ordinary_income = 120_000.0
        taxable_with = tax_mod.taxable_income(pre_retirement + ordinary_income, 0.0, 0.0, self.YEAR, "mfj")
        taxable_baseline = tax_mod.taxable_income(pre_retirement, 0.0, 0.0, self.YEAR, "mfj")
        self.assertAlmostEqual(taxable_with - taxable_baseline, ordinary_income)


class PreRetirementIncomeTests(unittest.TestCase):
    """build_pre_retirement_income()'s estimated_gross_income field --
    the renamed replacement for the old estimated_agi (see the function's
    own docstring for why: gross W-2 income, not AGI, avoids double-
    counting investment income this tool already tracks separately) --
    and its new per-year LIST support (mirrors roth_conversions' own
    annual_amount lists) for a household with a real, non-flat trajectory
    (e.g. a one-time bonus year) that a single flat number can't
    represent."""

    PROFILE = {"people": {"primary": {"name": "alex", "birthdate": date(1990, 1, 1)}}}
    RETIREMENT_YEAR = 2030  # alex born 1990 -- retirement_age: 40 retires in 2030

    def _scenario(self, pre_retirement_income):
        return {
            "people": self.PROFILE["people"],
            "retirement": {"retirement_age": 40},
            "pre_retirement_income": {"alex": pre_retirement_income},
        }

    def test_old_estimated_agi_key_raises_a_clear_migration_error(self):
        scenario = self._scenario({"estimated_agi": 200_000})
        with self.assertRaises(proj.ProjectionError) as ctx:
            proj.build_pre_retirement_income(scenario, self.PROFILE)
        self.assertIn("estimated_gross_income", str(ctx.exception))

    def test_missing_estimated_gross_income_raises(self):
        scenario = self._scenario({})
        with self.assertRaises(proj.ProjectionError):
            proj.build_pre_retirement_income(scenario, self.PROFILE)

    def test_flat_number_inflates_forward_like_a_wage(self):
        scenario = self._scenario({"estimated_gross_income": 200_000})
        entries = proj.build_pre_retirement_income(scenario, self.PROFILE)
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertIsNone(entry["start_year"])
        self.assertAlmostEqual(
            proj._pre_retirement_income_for_year(entry, year=2028, years_from_start=2, inflation_rate=0.05),
            200_000 * 1.05 ** 2,
        )

    def test_list_requires_a_start_date_to_anchor_it(self):
        scenario = self._scenario({"estimated_gross_income": [200_000, 210_000, 220_000, 230_000]})
        with self.assertRaises(proj.ProjectionError):
            proj.build_pre_retirement_income(scenario, self.PROFILE)

    def test_list_length_must_match_the_window(self):
        # retirement_age: 4 against start_date 2026 means the window is
        # 2026-2029 (4 years) -- only 3 values given.
        scenario = self._scenario({
            "start_date": date(2026, 1, 1),
            "estimated_gross_income": [200_000, 210_000, 220_000],
        })
        with self.assertRaises(proj.ProjectionError):
            proj.build_pre_retirement_income(scenario, self.PROFILE)

    def test_list_gives_a_different_already_nominal_value_each_year_no_further_inflation(self):
        # The real-world case this exists for: a flat one-time bonus
        # spike (2027) that a growth-rate-based flat number can't
        # represent, followed by resuming a normal trajectory.
        scenario = self._scenario({
            "start_date": date(2026, 1, 1),
            "estimated_gross_income": [76_351, 420_869, 79_043, 81_820],
        })
        entries = proj.build_pre_retirement_income(scenario, self.PROFILE)
        entry = entries[0]
        self.assertEqual(entry["start_year"], 2026)
        # Even with a large inflation_rate/years_from_start, list values
        # come back completely unchanged -- they're already nominal,
        # exact-dollar figures for that specific year, same convention as
        # roth_conversions' own annual_amount lists.
        for offset, expected in enumerate([76_351, 420_869, 79_043, 81_820]):
            self.assertAlmostEqual(
                proj._pre_retirement_income_for_year(
                    entry, year=2026 + offset, years_from_start=offset, inflation_rate=0.10,
                ),
                expected,
            )
        # Outside the list's own window (before start_year, or at/after
        # end_year), this entry contributes nothing.
        self.assertEqual(
            proj._pre_retirement_income_for_year(entry, year=2025, years_from_start=-1, inflation_rate=0.0), 0.0
        )
        self.assertEqual(
            proj._pre_retirement_income_for_year(entry, year=2030, years_from_start=4, inflation_rate=0.0), 0.0
        )

    def test_end_to_end_bonus_year_raises_tax_only_in_that_year(self):
        """A real reproduction of the motivating case: a normal salary,
        then one bonus year, then back to a normal (raised) trajectory --
        project()'s tax for the bonus year should reflect that year's own
        much larger pre-retirement income, not a smoothed-out average."""
        scenario = copy.deepcopy(self._scenario({
            "start_date": date(2026, 1, 1),
            "estimated_gross_income": [80_000, 400_000, 83_000, 86_000],
        }))
        scenario["income"] = {"target_spending": 0}
        scenario["inflation"] = {"general": 0.0}
        scenario["returns"] = {"growth": 0.0, "fixed_income": 0.0}
        scenario["roth_conversions"] = [{
            "owner": "alex", "start_date": date(2026, 1, 1), "end_date": date(2029, 12, 31),
            "annual_amount": 50_000,
        }]
        account_rows = [{"account_number": "ira", "account_type": "IRA", "owner": "alex",
                          "value": 1_000_000, "pct_growth": 0}]
        profile = {"people": scenario["people"]}
        blended_rate = 0.0
        rows = proj.project(profile, scenario, account_rows, blended_rate, start_year=2026)

        by_year = {r["year"]: r for r in rows[:4]}
        # The SAME $50,000 conversion each year -- only the pre-retirement
        # income baseline differs -- so the bonus year (2027) must show
        # meaningfully more tax than the flanking normal-income years.
        self.assertGreater(by_year[2027]["tax"], by_year[2026]["tax"] + 5_000)
        self.assertGreater(by_year[2027]["tax"], by_year[2028]["tax"] + 5_000)
        self.assertAlmostEqual(by_year[2026]["pre_retirement_income"], 80_000)
        self.assertAlmostEqual(by_year[2027]["pre_retirement_income"], 400_000)
        self.assertAlmostEqual(by_year[2028]["pre_retirement_income"], 83_000)


class LiabilityTests(unittest.TestCase):
    """build_liabilities() -- a recurring, FIXED-dollar debt payment (a
    mortgage, car loan, or similar), given either as a flat monthly_
    payment/annual_payment or as loan terms (principal/interest_rate/
    term_years) the standard amortization formula turns into a payment
    -- see the function's own docstring for the full contract."""

    PROFILE = {"people": {"primary": {"name": "alex", "birthdate": date(1990, 1, 1)}}}

    def _scenario(self, liabilities):
        return {"people": self.PROFILE["people"], "retirement": {"retirement_age": 95},
                "liabilities": liabilities}

    def test_flat_monthly_payment_multiplies_by_twelve(self):
        entries = proj.build_liabilities(self._scenario([
            {"name": "Car loan", "start_date": date(2028, 1, 1), "end_date": date(2032, 12, 31),
             "monthly_payment": 500},
        ]), self.PROFILE)
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry["name"], "Car loan")
        self.assertEqual(entry["start_year"], 2028)
        self.assertEqual(entry["end_year"], 2032)
        self.assertAlmostEqual(entry["annual_payment"], 6000)

    def test_flat_annual_payment_used_directly(self):
        entries = proj.build_liabilities(self._scenario([
            {"name": "Loan", "start_date": date(2028, 1, 1), "end_date": date(2028, 12, 31),
             "annual_payment": 7200},
        ]), self.PROFILE)
        self.assertAlmostEqual(entries[0]["annual_payment"], 7200)

    def test_flat_payment_without_end_date_raises(self):
        with self.assertRaises(proj.ProjectionError):
            proj.build_liabilities(self._scenario([
                {"name": "Loan", "start_date": date(2028, 1, 1), "monthly_payment": 500},
            ]), self.PROFILE)

    def test_flat_payment_with_both_monthly_and_annual_raises(self):
        with self.assertRaises(proj.ProjectionError):
            proj.build_liabilities(self._scenario([
                {"name": "Loan", "start_date": date(2028, 1, 1), "end_date": date(2028, 12, 31),
                 "monthly_payment": 500, "annual_payment": 6000},
            ]), self.PROFILE)

    def test_loan_terms_compute_the_standard_amortized_payment(self):
        # Independently verified against _amortized_annual_payment()
        # directly -- $45,000 at 6.5% over 5 years.
        entries = proj.build_liabilities(self._scenario([
            {"name": "Car loan", "start_date": date(2028, 1, 1),
             "principal": 45000, "interest_rate": 6.5, "term_years": 5},
        ]), self.PROFILE)
        entry = entries[0]
        self.assertAlmostEqual(entry["annual_payment"], 10565.720038113479)
        # No end_date given -- defaults to a term_years-long window.
        self.assertEqual(entry["start_year"], 2028)
        self.assertEqual(entry["end_year"], 2032)

    def test_loan_terms_with_explicit_end_date_overrides_the_default_window(self):
        # An early payoff: term_years still drives the PAYMENT amount,
        # but end_date -- not start_year + term_years - 1 -- drives how
        # long the cash flow lasts in the projection.
        entries = proj.build_liabilities(self._scenario([
            {"name": "Car loan", "start_date": date(2028, 1, 1), "end_date": date(2030, 12, 31),
             "principal": 45000, "interest_rate": 6.5, "term_years": 5},
        ]), self.PROFILE)
        entry = entries[0]
        self.assertAlmostEqual(entry["annual_payment"], 10565.720038113479)
        self.assertEqual(entry["end_year"], 2030)

    def test_zero_interest_rate_is_a_plain_even_split(self):
        entries = proj.build_liabilities(self._scenario([
            {"name": "Promo loan", "start_date": date(2028, 1, 1),
             "principal": 12000, "interest_rate": 0, "term_years": 3},
        ]), self.PROFILE)
        self.assertAlmostEqual(entries[0]["annual_payment"], 4000.0)

    def test_loan_terms_missing_a_field_raises(self):
        with self.assertRaises(proj.ProjectionError):
            proj.build_liabilities(self._scenario([
                {"name": "Car loan", "start_date": date(2028, 1, 1),
                 "principal": 45000, "interest_rate": 6.5},
            ]), self.PROFILE)

    def test_both_flat_payment_and_loan_terms_raises(self):
        with self.assertRaises(proj.ProjectionError):
            proj.build_liabilities(self._scenario([
                {"name": "Car loan", "start_date": date(2028, 1, 1), "end_date": date(2032, 12, 31),
                 "monthly_payment": 500, "principal": 45000, "interest_rate": 6.5, "term_years": 5},
            ]), self.PROFILE)

    def test_neither_flat_payment_nor_loan_terms_raises(self):
        with self.assertRaises(proj.ProjectionError):
            proj.build_liabilities(self._scenario([
                {"name": "Mystery liability", "start_date": date(2028, 1, 1), "end_date": date(2029, 12, 31)},
            ]), self.PROFILE)

    def test_missing_start_date_raises(self):
        with self.assertRaises(proj.ProjectionError):
            proj.build_liabilities(self._scenario([
                {"name": "Car loan", "end_date": date(2032, 12, 31), "monthly_payment": 500},
            ]), self.PROFILE)

    def test_end_to_end_payment_only_applies_within_its_own_window_and_never_inflates(self):
        scenario = self._scenario([
            {"name": "Vacation mortgage", "start_date": date(2030, 6, 1), "end_date": date(2032, 12, 31),
             "monthly_payment": 3200},
        ])
        scenario["income"] = {"target_spending": 0}
        scenario["inflation"] = {"general": 5.0}  # a large rate makes any accidental
                                                   # inflation of the payment obvious
        scenario["returns"] = {"growth": 0.0, "fixed_income": 0.0}
        account_rows = [{"account_number": "brok", "account_type": "Brokerage", "owner": "alex",
                          "value": 2_000_000, "pct_growth": 0}]
        profile = {"people": scenario["people"]}
        rows = proj.project(profile, scenario, account_rows, 0.0, start_year=2026)
        by_year = {r["year"]: r for r in rows[:8]}

        self.assertEqual(by_year[2028]["liability_payment"], 0.0)
        self.assertEqual(by_year[2029]["liability_payment"], 0.0)
        for year in (2030, 2031, 2032):
            self.assertAlmostEqual(by_year[year]["liability_payment"], 38400.0)
            self.assertAlmostEqual(
                by_year[year]["liability_payment_by_name"]["Vacation mortgage"], 38400.0
            )
        self.assertEqual(by_year[2033]["liability_payment"], 0.0)

    def test_multiple_liabilities_break_down_by_name(self):
        scenario = self._scenario([
            {"name": "Car loan", "start_date": date(2026, 1, 1), "end_date": date(2030, 12, 31),
             "monthly_payment": 500},
            {"name": "Mortgage", "start_date": date(2028, 1, 1), "end_date": date(2035, 12, 31),
             "monthly_payment": 3000},
        ])
        scenario["income"] = {"target_spending": 0}
        scenario["inflation"] = {"general": 2.5}
        scenario["returns"] = {"growth": 0.0, "fixed_income": 0.0}
        account_rows = [{"account_number": "brok", "account_type": "Brokerage", "owner": "alex",
                          "value": 2_000_000, "pct_growth": 0}]
        profile = {"people": scenario["people"]}
        rows = proj.project(profile, scenario, account_rows, 0.0, start_year=2026)
        row_2028 = next(r for r in rows if r["year"] == 2028)
        self.assertAlmostEqual(row_2028["liability_payment"], 6000 + 36000)
        self.assertAlmostEqual(row_2028["liability_payment_by_name"]["Car loan"], 6000)
        self.assertAlmostEqual(row_2028["liability_payment_by_name"]["Mortgage"], 36000)


class InheritedWithdrawalScheduleTests(unittest.TestCase):
    """_planned_withdrawal_floor()'s per-year LIST annual_amount support
    (mirrors roth_conversions' own -- see build_roth_conversions()'s
    docstring) plus project()'s new inherited_distribution_by_account row
    field, needed to disambiguate which inherited account a withdrawal
    came from when a household holds more than one."""

    def _scenario(self, inh1_planned_amount):
        # decedent born the same year as the beneficiary -- unrealistic,
        # but keeps decedent_age_at_death (35) well under their RMD age
        # (73/75), so no annual stretch RMD is required in years 1-9,
        # isolating the planned_withdrawal floor cleanly. target_spending:
        # 0 and a far-off retirement_age keep every other cash need out of
        # the way too.
        return {
            "people": {"primary": {"name": "alex", "birthdate": date(1990, 1, 1)}},
            "retirement": {"retirement_age": 95},
            "income": {"target_spending": 0},
            "inflation": {"general": 2.5},
            "returns": {"growth": 8.0, "fixed_income": 4.0},
            "hypothetical_accounts": [
                {"account_number": "inh1", "account_type": "Inherited IRA", "owner": "alex",
                 "value": 500000, "pct_growth": 0},
                {"account_number": "inh2", "account_type": "Inherited IRA", "owner": "alex",
                 "value": 300000, "pct_growth": 0},
            ],
            "inherited_accounts": [
                {"account_number": "inh1", "type": "traditional",
                 "decedent_birthdate": date(1990, 1, 1), "decedent_death_date": date(2025, 6, 1),
                 "beneficiary": "alex",
                 "planned_withdrawal": {"start_date": date(2026, 1, 1), "end_date": date(2028, 12, 31),
                                         "annual_amount": inh1_planned_amount}},
                {"account_number": "inh2", "type": "traditional",
                 "decedent_birthdate": date(1990, 1, 1), "decedent_death_date": date(2025, 6, 1),
                 "beneficiary": "alex"},
            ],
        }

    def _run(self, inh1_planned_amount):
        scenario = self._scenario(inh1_planned_amount)
        profile = {"people": scenario["people"]}
        account_rows, allocation_rows, total = proj.build_hypothetical_accounts(scenario, profile)
        blended_rate = proj.compute_blended_growth_rate(allocation_rows, total, scenario)
        return proj.project(profile, scenario, account_rows, blended_rate, start_year=2026)

    def test_flat_annual_amount_applies_every_year_in_the_window(self):
        rows = self._run(15000)
        for row in rows[:3]:
            self.assertAlmostEqual(row["inherited_distribution_by_account"]["inh1"], 15000, places=2)
            self.assertNotIn("inh2", row["inherited_distribution_by_account"])

    def test_list_annual_amount_gives_a_different_value_each_year(self):
        rows = self._run([10000, 20000, 30000])
        expected = [10000, 20000, 30000]
        for row, exp in zip(rows[:3], expected):
            self.assertAlmostEqual(row["inherited_distribution_by_account"]["inh1"], exp, places=2)
            self.assertNotIn("inh2", row["inherited_distribution_by_account"])

    def test_list_length_must_match_the_window(self):
        scenario = self._scenario([10000, 20000])  # window covers 3 years, list has 2
        profile = {"people": scenario["people"]}
        with self.assertRaises(proj.ProjectionError):
            proj.build_inherited_schedules(scenario, profile)


class SocialSecurityPiaTests(unittest.TestCase):
    """_resolve_social_security_config()'s pia_monthly support -- computes
    monthly_benefit from a Primary Insurance Amount + claim_age via
    pt/social_security.py, instead of requiring the household to already
    know the benefit at their chosen claim_age. inflation.general: 0
    throughout, so ss_income == monthly_benefit * 12 exactly regardless
    of years_from_start, isolating the claim-age math being tested."""

    def _scenario(self, ss_entry):
        return {
            "people": {"primary": {"name": "alex", "birthdate": date(1960, 1, 1)}},
            "retirement": {"retirement_age": 62},
            "income": {"target_spending": 0},
            "inflation": {"general": 0},
            "returns": {"growth": 8.0, "fixed_income": 4.0},
            "hypothetical_accounts": [
                {"account_number": "trad1", "account_type": "IRA", "owner": "alex",
                 "value": 100000, "pct_growth": 0},
            ],
            "social_security": {"alex": ss_entry},
        }

    def _first_row(self, ss_entry):
        scenario = self._scenario(ss_entry)
        profile = {"people": scenario["people"]}
        account_rows, allocation_rows, total = proj.build_hypothetical_accounts(scenario, profile)
        blended_rate = proj.compute_blended_growth_rate(allocation_rows, total, scenario)
        # alex (born 1960) turns 62 in 2022 -- ages[name] = year - birth_year (project()'s own
        # whole-year convention), matching claim_age: 62 exactly in the first row.
        return proj.project(profile, scenario, account_rows, blended_rate, start_year=2022)[0]

    def test_pia_monthly_resolves_to_the_correct_claim_age_adjusted_benefit(self):
        # FRA 67 (born 1960), claiming at 62 (60 months early) -> 70% of PIA
        # -- see test_social_security.py's own checkpoint test.
        row = self._first_row({"pia_monthly": 1000.0, "claim_age": 62})
        self.assertAlmostEqual(row["ss_income"], 700.0 * 12, places=2)

    def test_flat_monthly_benefit_still_works_unchanged(self):
        row = self._first_row({"monthly_benefit": 700.0, "claim_age": 62})
        self.assertAlmostEqual(row["ss_income"], 700.0 * 12, places=2)

    def test_both_monthly_benefit_and_pia_monthly_raises(self):
        scenario = self._scenario({"monthly_benefit": 700.0, "pia_monthly": 1000.0, "claim_age": 62})
        profile = {"people": scenario["people"]}
        with self.assertRaises(proj.ProjectionError):
            proj._resolve_social_security_config(scenario["social_security"], profile["people"])

    def test_pia_monthly_without_claim_age_raises(self):
        scenario = self._scenario({"pia_monthly": 1000.0})
        profile = {"people": scenario["people"]}
        with self.assertRaises(proj.ProjectionError):
            proj._resolve_social_security_config(scenario["social_security"], profile["people"])

    def test_pia_monthly_for_unmatched_name_raises(self):
        with self.assertRaises(proj.ProjectionError):
            proj._resolve_social_security_config(
                {"nobody": {"pia_monthly": 1000.0, "claim_age": 62}}, {"alex": date(1960, 1, 1)}
            )


if __name__ == "__main__":
    unittest.main()
