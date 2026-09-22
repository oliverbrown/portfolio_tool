"""Federal income tax estimate for the growth projection: 2024
married-filing-jointly AND single brackets, standard deductions, long-term
capital gains brackets, and Social Security's partial-taxability formula.

Deliberately simplified, matching this household's own choices (full
federal brackets, no state tax -- see README "Growth projection"):
- Federal only, no deductions/credits beyond the standard deduction (no
  itemizing, no child tax credit, etc.). Filing status is MFJ by default,
  switching to Single for a surviving spouse the year after the other
  dies (see pt/projection.py's mortality handling) -- every function here
  takes an explicit filing_status ("mfj" or "single") for that reason, not
  because this tool supports Head of Household or any other status.
- Taxable-brokerage withdrawals are treated as 100% long-term capital gain
  -- cost basis isn't tracked/excluded, so this somewhat overstates tax on
  that bucket.
- Traditional-account RMDs/withdrawals are 100% ordinary income (correct --
  pre-tax contributions and all growth are taxable on the way out).
- Roth withdrawals are tax-free (not modeled here at all -- they never
  enter ordinary or capital-gains income).

Bracket thresholds and the standard deduction ARE inflated by the
scenario's general inflation rate each projection year, since real tax
brackets are inflation-indexed -- held flat, they'd overstate the tax
burden badly by year 30 of a 39-year projection. The Social Security
taxability thresholds ($32k/$44k MFJ, $25k/$34k Single) are NOT inflated --
that's not an omission, those thresholds are fixed by statute and have
never been adjusted since 1983/1993 under current law.
"""

BASE_TAX_YEAR = 2024

# 2024 married filing jointly. (threshold, rate) -- rate applies to income
# above threshold, up to the next bracket's threshold.
ORDINARY_BRACKETS_2024_MFJ = [
    (0, 0.10),
    (23_200, 0.12),
    (94_300, 0.22),
    (201_050, 0.24),
    (383_900, 0.32),
    (487_450, 0.35),
    (731_200, 0.37),
]
STANDARD_DEDUCTION_2024_MFJ = 29_200

LTCG_BRACKETS_2024_MFJ = [
    (0, 0.00),
    (94_050, 0.15),
    (583_750, 0.20),
]

# Social Security "combined income" thresholds, MFJ. Fixed by statute --
# see module docstring for why these aren't inflated.
SS_COMBINED_INCOME_BASE_1_MFJ = 32_000
SS_COMBINED_INCOME_BASE_2_MFJ = 44_000

# 2024 single filer -- used for a surviving spouse (see module docstring).
ORDINARY_BRACKETS_2024_SINGLE = [
    (0, 0.10),
    (11_600, 0.12),
    (47_150, 0.22),
    (100_525, 0.24),
    (191_950, 0.32),
    (243_725, 0.35),
    (609_350, 0.37),
]
STANDARD_DEDUCTION_2024_SINGLE = 14_600

LTCG_BRACKETS_2024_SINGLE = [
    (0, 0.00),
    (47_025, 0.15),
    (518_900, 0.20),
]

SS_COMBINED_INCOME_BASE_1_SINGLE = 25_000
SS_COMBINED_INCOME_BASE_2_SINGLE = 34_000


def _ordinary_brackets(filing_status: str) -> list:
    return ORDINARY_BRACKETS_2024_SINGLE if filing_status == "single" else ORDINARY_BRACKETS_2024_MFJ


def _ltcg_brackets(filing_status: str) -> list:
    return LTCG_BRACKETS_2024_SINGLE if filing_status == "single" else LTCG_BRACKETS_2024_MFJ


def _standard_deduction_base(filing_status: str) -> float:
    return STANDARD_DEDUCTION_2024_SINGLE if filing_status == "single" else STANDARD_DEDUCTION_2024_MFJ


def _ss_combined_income_bases(filing_status: str) -> tuple:
    if filing_status == "single":
        return SS_COMBINED_INCOME_BASE_1_SINGLE, SS_COMBINED_INCOME_BASE_2_SINGLE
    return SS_COMBINED_INCOME_BASE_1_MFJ, SS_COMBINED_INCOME_BASE_2_MFJ


def _inflate(value: float, inflation_rate: float, years: int) -> float:
    return value * (1 + inflation_rate) ** years


def _progressive_tax(income: float, brackets: list) -> float:
    if income <= 0:
        return 0.0
    tax = 0.0
    for i, (threshold, rate) in enumerate(brackets):
        if income <= threshold:
            break
        next_threshold = brackets[i + 1][0] if i + 1 < len(brackets) else float("inf")
        tax += (min(income, next_threshold) - threshold) * rate
    return tax


def social_security_taxable_amount(ss_income: float, other_income: float, filing_status: str = "mfj") -> float:
    """The taxable portion of Social Security benefits, per the IRS
    "combined income" formula (combined income = other income + half of SS
    benefits). Returns a dollar amount, at most 85% of ss_income."""
    if ss_income <= 0:
        return 0.0
    combined_income = other_income + ss_income / 2
    base1, base2 = _ss_combined_income_bases(filing_status)
    if combined_income <= base1:
        return 0.0
    if combined_income <= base2:
        taxable = 0.5 * (combined_income - base1)
    else:
        taxable = 0.85 * (combined_income - base2) + 0.5 * (base2 - base1)
    return min(taxable, 0.85 * ss_income)


def inflated_standard_deduction(inflation_rate: float, year: int, filing_status: str = "mfj") -> float:
    return _inflate(_standard_deduction_base(filing_status), inflation_rate, year - BASE_TAX_YEAR)


def taxable_income(ordinary_income: float, ltcg_income: float, inflation_rate: float, year: int,
                    filing_status: str = "mfj") -> float:
    """Form-1040-style taxable income: (ordinary_income - standard
    deduction, floored at 0) + ltcg_income. This is exactly what
    federal_tax() computes tax from -- exposed separately so a caller (the
    projection's "Taxable Income" column) can show the same figure the tax
    was actually based on, rather than a parallel estimate that could drift
    from it."""
    taxable_ordinary = max(0.0, ordinary_income - inflated_standard_deduction(inflation_rate, year, filing_status))
    return taxable_ordinary + ltcg_income


def marginal_ordinary_rate(ordinary_income: float, inflation_rate: float, year: int,
                            filing_status: str = "mfj") -> float:
    """The marginal federal rate that would apply to the NEXT dollar of
    ordinary income, given `ordinary_income` (pre-standard-deduction)
    already accrued this year -- used by the Roth conversion/IRA
    distribution preserve_cash_reserve constraint to translate a
    taxable-bucket dollar limit into a pre-tax dollar limit (see
    pt/projection.py)."""
    years = year - BASE_TAX_YEAR
    taxable_ordinary = max(0.0, ordinary_income - inflated_standard_deduction(inflation_rate, year, filing_status))
    brackets = [(_inflate(t, inflation_rate, years), r) for t, r in _ordinary_brackets(filing_status)]
    rate = brackets[0][1]
    for threshold, r in brackets:
        if taxable_ordinary >= threshold:
            rate = r
        else:
            break
    return rate


def bracket_ceiling(target_rate: float, inflation_rate: float, year: int, filing_status: str = "mfj") -> float:
    """The most TAXABLE ordinary income (post-standard-deduction, i.e. the
    same quantity taxable_income() computes) that still keeps the marginal
    rate at or below `target_rate` -- e.g. bracket_ceiling(0.24, ...)
    returns the top of the 24% bracket. Returns float('inf') if
    target_rate is the top bracket's own rate. Raises ValueError if
    target_rate isn't one of this filing status's bracket rates -- used by
    the Roth conversion/IRA distribution max_marginal_bracket constraint
    (see pt/projection.py)."""
    years = year - BASE_TAX_YEAR
    brackets = [(_inflate(t, inflation_rate, years), r) for t, r in _ordinary_brackets(filing_status)]
    for i, (threshold, rate) in enumerate(brackets):
        if abs(rate - target_rate) < 1e-9:
            return brackets[i + 1][0] if i + 1 < len(brackets) else float("inf")
    valid = ", ".join(f"{r*100:g}%" for _, r in _ordinary_brackets(filing_status))
    raise ValueError(f"{target_rate*100:g}% isn't one of this year's {filing_status.upper()} bracket rates ({valid}).")


def federal_tax(ordinary_income: float, ltcg_income: float, inflation_rate: float, year: int,
                 filing_status: str = "mfj") -> float:
    """ordinary_income: Traditional withdrawals/RMDs plus taxable Social
    Security (see social_security_taxable_amount()). ltcg_income: taxable
    brokerage withdrawals. Roth withdrawals aren't passed in at all --
    they're tax-free. LTCG stacks on top of (post-deduction) ordinary
    income for bracket purposes, matching actual IRS stacking rules.
    filing_status: "mfj" (default) or "single" -- see module docstring."""
    years = year - BASE_TAX_YEAR
    ordinary_brackets = [(_inflate(t, inflation_rate, years), r) for t, r in _ordinary_brackets(filing_status)]
    ltcg_brackets = [(_inflate(t, inflation_rate, years), r) for t, r in _ltcg_brackets(filing_status)]

    taxable_ordinary = max(0.0, ordinary_income - inflated_standard_deduction(inflation_rate, year, filing_status))
    ordinary_tax = _progressive_tax(taxable_ordinary, ordinary_brackets)
    ltcg_tax = (
        _progressive_tax(taxable_ordinary + ltcg_income, ltcg_brackets)
        - _progressive_tax(taxable_ordinary, ltcg_brackets)
    )
    return ordinary_tax + ltcg_tax
