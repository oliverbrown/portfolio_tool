"""Regression tests for pt/social_security.py's FRA/claim-age actuarial
adjustment -- checked against SSA's own well-known published checkpoint
percentages, not just internal consistency."""
import unittest

from pt import social_security as ss


class FullRetirementAgeTests(unittest.TestCase):
    def test_66_for_1943_through_1954(self):
        for year in (1943, 1950, 1954):
            self.assertEqual(ss.full_retirement_age_months(year), 66 * 12)

    def test_rises_two_months_per_year_from_1955_to_1959(self):
        self.assertEqual(ss.full_retirement_age_months(1955), 66 * 12 + 2)
        self.assertEqual(ss.full_retirement_age_months(1956), 66 * 12 + 4)
        self.assertEqual(ss.full_retirement_age_months(1957), 66 * 12 + 6)
        self.assertEqual(ss.full_retirement_age_months(1958), 66 * 12 + 8)
        self.assertEqual(ss.full_retirement_age_months(1959), 66 * 12 + 10)

    def test_67_for_1960_and_later(self):
        for year in (1960, 1975, 2000):
            self.assertEqual(ss.full_retirement_age_months(year), 67 * 12)

    def test_before_1938_raises(self):
        with self.assertRaises(ValueError):
            ss.full_retirement_age_months(1937)


class BenefitAtClaimAgeTests(unittest.TestCase):
    """Checked against SSA's own well-known published percentages (e.g.
    ssa.gov's benefit-reduction chart), not just internal consistency."""

    def test_claiming_exactly_at_fra_pays_full_pia(self):
        self.assertAlmostEqual(ss.benefit_at_claim_age(2000.0, 1960, 67), 2000.0)
        self.assertAlmostEqual(ss.benefit_at_claim_age(2000.0, 1950, 66), 2000.0)

    def test_fra_67_claiming_at_62_pays_70_percent(self):
        # 60 months early: 36 * 5/9% + 24 * 5/12% = 20% + 10% = 30% reduction.
        self.assertAlmostEqual(ss.benefit_at_claim_age(1000.0, 1960, 62), 700.0, places=2)

    def test_fra_66_claiming_at_62_pays_75_percent(self):
        # 48 months early: 36 * 5/9% + 12 * 5/12% = 20% + 5% = 25% reduction.
        self.assertAlmostEqual(ss.benefit_at_claim_age(1000.0, 1950, 62), 750.0, places=2)

    def test_fra_67_claiming_at_70_pays_124_percent(self):
        # 36 months delayed * 2/3%/month = 24% increase.
        self.assertAlmostEqual(ss.benefit_at_claim_age(1000.0, 1960, 70), 1240.0, places=2)

    def test_fra_66_claiming_at_70_pays_132_percent(self):
        # 48 months delayed * 2/3%/month = 32% increase.
        self.assertAlmostEqual(ss.benefit_at_claim_age(1000.0, 1950, 70), 1320.0, places=2)

    def test_claim_age_below_62_raises(self):
        with self.assertRaises(ValueError):
            ss.benefit_at_claim_age(1000.0, 1960, 61)

    def test_claim_age_above_70_raises(self):
        with self.assertRaises(ValueError):
            ss.benefit_at_claim_age(1000.0, 1960, 71)

    def test_benefit_increases_monotonically_with_claim_age(self):
        amounts = [ss.benefit_at_claim_age(1000.0, 1962, age) for age in range(62, 71)]
        self.assertEqual(amounts, sorted(amounts))


if __name__ == "__main__":
    unittest.main()
