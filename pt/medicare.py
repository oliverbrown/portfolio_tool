"""Medicare Part B premium and IRMAA (income-related monthly adjustment
amount) modeling: Part B's own premium plus the Part D IRMAA surcharge
(the Part D PLAN premium itself isn't modeled -- it varies by plan and
isn't something this tool could estimate).

2024 published CMS figures, married-filing-jointly, based on a 2-year
income lookback: a given year's premium is set by MAGI from the tax
return filed 2 years earlier (e.g. 2026's premium is set by 2024's MAGI).
MAGI here means AGI (ordinary income + taxable Social Security + capital
gains -- see pt/projection.py) -- tax-exempt interest isn't tracked
separately anywhere in this tool, so it's simply omitted, a minor
understatement for a household with meaningful muni-bond income.

Tiers are numbered 0 (standard premium, no surcharge) through 5 (top
surcharge). avoid_irmaa_tier in a scenario's roth_conversions.constraints
refers to these same numbers.

Thresholds and premiums are inflated forward using the scenario's medical
inflation rate (scenario.inflation.medical, falling back to
inflation.general if not set) -- historically Medicare costs have run
faster than general CPI, hence the separate rate. Two different years get
inflated to, and it matters which: a tier is decided by comparing MAGI
EARNED in year Y-2 against thresholds inflated to Y-2 (the year that MAGI
was actually reported in); the PREMIUM for that tier is looked up in
Y-dollars (the year it's actually paid) -- see tier_for_magi() and
annual_part_b_premium()/annual_part_d_irmaa()."""

BASE_MEDICARE_YEAR = 2024
MEDICARE_ELIGIBILITY_AGE = 65
IRMAA_LOOKBACK_YEARS = 2

# 2024 MFJ IRMAA tiers: (magi_ceiling, monthly Part B premium, monthly Part
# D IRMAA surcharge). magi_ceiling is the top of that tier's MAGI range;
# the last tier's ceiling is unbounded.
IRMAA_TIERS_2024_MFJ = [
    (206_000, 174.70, 0.00),
    (258_000, 244.60, 12.90),
    (322_000, 349.40, 33.30),
    (386_000, 454.20, 53.80),
    (750_000, 559.00, 74.20),
    (float("inf"), 594.00, 81.00),
]


def _inflate(value: float, rate: float, years: int) -> float:
    return value * (1 + rate) ** years


def tier_for_magi(magi: float, medical_inflation_rate: float, income_year: int) -> int:
    """Which IRMAA tier (0-5) `magi` (earned in `income_year`) falls into,
    comparing against thresholds inflated to that same income_year."""
    years = income_year - BASE_MEDICARE_YEAR
    for i, (ceiling, _, _) in enumerate(IRMAA_TIERS_2024_MFJ):
        if magi <= _inflate(ceiling, medical_inflation_rate, years):
            return i
    return len(IRMAA_TIERS_2024_MFJ) - 1


def tier_magi_ceiling(tier: int, medical_inflation_rate: float, income_year: int) -> float:
    """The most MAGI, earned in `income_year`, that still falls within
    `tier` -- used by the avoid_irmaa_tier Roth-conversion constraint to
    cap a conversion so that year's MAGI doesn't spill into the next tier
    up."""
    years = income_year - BASE_MEDICARE_YEAR
    ceiling, _, _ = IRMAA_TIERS_2024_MFJ[tier]
    return _inflate(ceiling, medical_inflation_rate, years)


def annual_part_b_premium(tier: int, medical_inflation_rate: float, premium_year: int) -> float:
    """One Medicare-eligible person's annual Part B premium for `tier`, in
    `premium_year` dollars (the year it's actually paid, which is
    IRMAA_LOOKBACK_YEARS after the MAGI that set the tier was earned)."""
    years = premium_year - BASE_MEDICARE_YEAR
    _, part_b, _ = IRMAA_TIERS_2024_MFJ[tier]
    return _inflate(part_b, medical_inflation_rate, years) * 12


def annual_part_d_irmaa(tier: int, medical_inflation_rate: float, premium_year: int) -> float:
    """One Medicare-eligible person's annual Part D IRMAA surcharge for
    `tier`, in `premium_year` dollars. Not the Part D plan premium itself
    -- see module docstring."""
    years = premium_year - BASE_MEDICARE_YEAR
    _, _, part_d = IRMAA_TIERS_2024_MFJ[tier]
    return _inflate(part_d, medical_inflation_rate, years) * 12
