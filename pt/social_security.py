"""Social Security claim-age actuarial adjustment.

Full Retirement Age (FRA) -- the age at which a worker receives their
full Primary Insurance Amount (PIA) -- and the reduction (claiming
before it, as early as age 62) or Delayed Retirement Credit (claiming
after it, as late as age 70) that scales a claimed monthly benefit
relative to that PIA.

This tool otherwise works entirely in whole years -- see pt/projection.py's
module docstring ("only the YEAR matters to the tool") -- so claim_age
here is likewise a whole integer age (62-70) with an implicit January 1
claim date, not a specific birthday month. FRA itself, and the resulting
adjustment, still use SSA's own month-level formula internally for
accuracy (it would be wrong to round FRA to the nearest year first) --
only the PUBLIC claim_age input (and the caller's own use of the
resulting benefit) stays whole-year, consistent with the rest of this
project.

Source: SSA's own published early-retirement-reduction and Delayed
Retirement Credit rules (ssa.gov/oact/ProgData/nra.html, ssa.gov/benefits/
retirement/planner/1943-delay.html), current as of this writing. Benefit
law can change; this isn't tax or benefits advice -- see the top-level
README's "Disclaimer".
"""

MIN_CLAIM_AGE = 62
MAX_CLAIM_AGE = 70

# Rates as exact fractions, not floats, to avoid compounding rounding
# error across many months -- SSA's own formula:
#   - Early: 5/9 of 1% per month for the first 36 months before FRA,
#     then an additional 5/12 of 1% per month beyond that.
#   - Delayed: 2/3 of 1% per month (8%/year) -- the Delayed Retirement
#     Credit rate for anyone born 1943 or later (this tool doesn't model
#     the lower historical DRC rates that applied to earlier birth years).
_EARLY_RATE_FIRST_36 = 5 / 9 / 100
_EARLY_RATE_BEYOND_36 = 5 / 12 / 100
_DELAYED_RATE = 2 / 3 / 100


def full_retirement_age_months(birth_year: int) -> int:
    """Full Retirement Age, in whole months (e.g. 66 years 4 months ->
    796) -- SSA's own published schedule: 66 for anyone born 1943-1954,
    rising by 2 months per birth year from 1955 through 1959, then 67 for
    1960 and later. Anyone born before 1938 isn't covered (this tool has
    no retirees that old in practice) -- raises ValueError if so."""
    if birth_year < 1938:
        raise ValueError(
            f"full_retirement_age_months() doesn't cover birth years before 1938 (got {birth_year})."
        )
    if birth_year <= 1954:
        years, extra_months = 66, 0
    elif birth_year <= 1959:
        years, extra_months = 66, (birth_year - 1954) * 2
    else:
        years, extra_months = 67, 0
    return years * 12 + extra_months


def benefit_at_claim_age(pia_monthly: float, birth_year: int, claim_age: int) -> float:
    """The monthly benefit for claiming at `claim_age` (a whole integer
    age, MIN_CLAIM_AGE-MAX_CLAIM_AGE inclusive), given `pia_monthly` (the
    Primary Insurance Amount -- what claiming EXACTLY at FRA would pay)
    and the worker's own birth_year (determines FRA -- see
    full_retirement_age_months()).

    Some well-known checkpoints this reproduces exactly: FRA 66 (born
    1943-1954), claiming at 62 (48 months early) -> 75% of PIA; FRA 67
    (born 1960+), claiming at 62 (60 months early) -> 70% of PIA;
    claiming at 70 (36 months after a 67 FRA) -> 124%; claiming at 70 (48
    months after a 66 FRA) -> 132%.

    Raises ValueError if claim_age is outside [MIN_CLAIM_AGE, MAX_CLAIM_AGE]."""
    if not (MIN_CLAIM_AGE <= claim_age <= MAX_CLAIM_AGE):
        raise ValueError(f"claim_age must be {MIN_CLAIM_AGE}-{MAX_CLAIM_AGE} (got {claim_age}).")
    fra_months = full_retirement_age_months(birth_year)
    months_from_fra = claim_age * 12 - fra_months

    if months_from_fra == 0:
        return pia_monthly
    if months_from_fra > 0:
        return pia_monthly * (1 + _DELAYED_RATE * months_from_fra)

    months_early = -months_from_fra
    first_tier = min(months_early, 36)
    second_tier = months_early - first_tier
    reduction = first_tier * _EARLY_RATE_FIRST_36 + second_tier * _EARLY_RATE_BEYOND_36
    return pia_monthly * (1 - reduction)
