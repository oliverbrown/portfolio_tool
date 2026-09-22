"""401(k) employee elective-deferral contribution limits: the regular
deferral limit, the standard 50+ catch-up, and SECURE 2.0's enhanced
catch-up for ages 60-63 (effective 2025) -- plus SECURE 2.0's mandatory
Roth catch-up rule (a high earner's catch-up contributions must be
designated Roth, i.e. after-tax, starting the year they become subject to
it -- see pt/projection.py's build_retirement_contributions()).

2025 published IRS figures, inflated forward using the scenario's general
inflation rate for later years -- a documented simplification, since the
real annual adjustment follows a wage-index formula rounded to $500
increments, not pure CPI (the same simplification this tool already makes
for tax brackets and Medicare/IRMAA thresholds).

Employee elective deferral limits only -- employer match is a separate,
simpler flat-amount-or-percent-of-salary figure the household supplies
directly in scenario.retirement_contributions[].employer_match (see
pt/projection.py's build_retirement_contributions()/module docstring),
not derived from any limit table here.
"""

BASE_CONTRIBUTION_YEAR = 2025

ELECTIVE_DEFERRAL_LIMIT_2025 = 23_500
STANDARD_CATCHUP_2025 = 7_500  # ages 50-59, and 64+
ENHANCED_CATCHUP_2025 = 11_250  # ages 60-63 (SECURE 2.0, effective 2025)

CATCHUP_MIN_AGE = 50
ENHANCED_CATCHUP_AGES = (60, 61, 62, 63)


def _inflate(value: float, inflation_rate: float, years: int) -> float:
    return value * (1 + inflation_rate) ** years


def elective_deferral_limit(inflation_rate: float, year: int) -> float:
    """This year's regular employee elective-deferral limit (before any
    catch-up), inflated from the 2025 published figure."""
    return _inflate(ELECTIVE_DEFERRAL_LIMIT_2025, inflation_rate, year - BASE_CONTRIBUTION_YEAR)


def catchup_limit(age: int, inflation_rate: float, year: int) -> float:
    """This year's additional catch-up limit for someone `age` years old --
    0.0 under age 50, the SECURE 2.0 enhanced amount for ages 60-63, the
    standard amount otherwise (50-59 and 64+)."""
    if age < CATCHUP_MIN_AGE:
        return 0.0
    years = year - BASE_CONTRIBUTION_YEAR
    base = ENHANCED_CATCHUP_2025 if age in ENHANCED_CATCHUP_AGES else STANDARD_CATCHUP_2025
    return _inflate(base, inflation_rate, years)


def max_contribution(age: int, inflation_rate: float, year: int) -> float:
    """The most this person could contribute this year: elective deferral
    limit plus whatever catch-up applies at their age."""
    return elective_deferral_limit(inflation_rate, year) + catchup_limit(age, inflation_rate, year)
