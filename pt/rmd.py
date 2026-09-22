"""IRS Required Minimum Distribution rules and tables.

Covers two distinct situations:

1. An account owner's own RMDs during their lifetime, on their own
   Traditional accounts (401(k), IRA). Roth IRAs have no lifetime RMD for
   the original owner under SECURE 2.0, so they're never passed through
   uniform_lifetime_factor(). Uses the IRS Uniform Lifetime Table (Pub.
   590-B, Table III, effective 2022).

2. An inherited account under the SECURE Act's 10-year rule, for a
   non-eligible-designated-beneficiary (e.g. an adult child) -- which is
   what this module assumes; it does not model the alternative
   life-expectancy "stretch" available to a spouse or other eligible
   designated beneficiary. Under the 10-year rule:
   - The account must be fully distributed by December 31 of the year
     containing the 10th anniversary of the original owner's death.
   - If the original owner died on or after their own Required Beginning
     Date (RMD age), the beneficiary must ALSO take annual RMDs during
     years 1-9, using the IRS Single Life Expectancy Table (Pub. 590-B,
     Table I): the beneficiary's age in the year after death sets the
     initial divisor, which is then reduced by 1 for each subsequent year
     (not re-looked-up from the table) -- this is the method IRS
     regulations require for a 10-year-rule beneficiary.
   - If the original owner died before their RBD, no annual RMD is
     required during years 1-9 -- only the year-10 full distribution.
   - A Roth IRA's original owner never has an RBD (no lifetime RMDs), so an
     inherited Roth IRA never requires annual stretch RMDs regardless of
     the owner's age at death -- only the year-10 full distribution.

These tables/rules reflect IRS guidance as of the 2024 final regulations on
the 10-year rule's annual-RMD requirement (effective for RMD years starting
2025). Tax law can change; this isn't tax advice.
"""
from datetime import date

RMD_AGE_TRANSITION_BIRTH_YEAR = 1960  # SECURE 2.0: 73 if born 1951-1959, 75 if born 1960+


def rmd_age(birth_year: int) -> int:
    """The age at which an account owner's own lifetime RMDs begin."""
    return 75 if birth_year >= RMD_AGE_TRANSITION_BIRTH_YEAR else 73


# IRS Uniform Lifetime Table (Table III), effective 2022.
UNIFORM_LIFETIME_TABLE = {
    72: 27.4, 73: 26.5, 74: 25.5, 75: 24.6, 76: 23.7, 77: 22.9, 78: 22.0,
    79: 21.1, 80: 20.2, 81: 19.4, 82: 18.5, 83: 17.7, 84: 16.8, 85: 16.0,
    86: 15.2, 87: 14.4, 88: 13.7, 89: 12.9, 90: 12.2, 91: 11.5, 92: 10.8,
    93: 10.1, 94: 9.5, 95: 8.9, 96: 8.4, 97: 7.8, 98: 7.3, 99: 6.8,
    100: 6.4, 101: 6.0, 102: 5.6, 103: 5.2, 104: 4.9, 105: 4.6, 106: 4.3,
    107: 4.1, 108: 3.9, 109: 3.7, 110: 3.5, 111: 3.4, 112: 3.3, 113: 3.1,
    114: 3.0, 115: 2.9, 116: 2.8, 117: 2.7, 118: 2.5, 119: 2.3, 120: 2.0,
}


def uniform_lifetime_factor(age: int) -> float:
    if age < 72:
        raise ValueError(f"No lifetime RMD applies at age {age} (starts at 72 in this table).")
    return UNIFORM_LIFETIME_TABLE.get(age, UNIFORM_LIFETIME_TABLE[120])


def owner_rmd(balance: float, age: int, birth_year: int) -> float:
    """This year's RMD for an account owner's own Traditional account, or
    0.0 if they haven't reached their RMD age yet."""
    if balance <= 0 or age < rmd_age(birth_year):
        return 0.0
    return balance / uniform_lifetime_factor(age)


# IRS Single Life Expectancy Table (Table I), effective 2022. Covers the
# range plausibly needed for a beneficiary's initial stretch-RMD divisor;
# extend if a beneficiary falls outside it.
SINGLE_LIFE_TABLE = {
    40: 45.7, 41: 44.7, 42: 43.8, 43: 42.8, 44: 41.9,
    45: 41.0, 46: 40.0, 47: 39.1, 48: 38.1, 49: 37.2,
    50: 36.2, 51: 35.3, 52: 34.3, 53: 33.4, 54: 32.5,
    55: 31.6, 56: 30.6, 57: 29.8, 58: 28.9, 59: 28.0,
    60: 27.1, 61: 26.2, 62: 25.4, 63: 24.5, 64: 23.7,
    65: 22.9, 66: 22.0, 67: 21.2, 68: 20.4, 69: 19.6,
    70: 18.8, 71: 18.0, 72: 17.2, 73: 16.4, 74: 15.6,
    75: 14.8, 76: 14.1, 77: 13.3, 78: 12.6, 79: 11.9,
    80: 11.2,
}


def single_life_initial_factor(beneficiary_age: int) -> float:
    if beneficiary_age not in SINGLE_LIFE_TABLE:
        raise ValueError(
            f"No Single Life Table entry for age {beneficiary_age} -- extend "
            "pt/rmd.py's SINGLE_LIFE_TABLE for this beneficiary."
        )
    return SINGLE_LIFE_TABLE[beneficiary_age]


class InheritedAccountSchedule:
    """The 10-year-rule schedule for one inherited account. Computed once
    from the original owner's birth/death dates and the beneficiary's
    birthdate; then queried year by year as the projection runs.

    account_kind: "traditional" or "roth" -- a Roth's original owner never
    has an RBD, so an inherited Roth never requires annual stretch RMDs
    (see module docstring).
    """

    def __init__(self, account_kind: str, decedent_birthdate: date, decedent_death_date: date,
                 beneficiary_birthdate: date):
        if account_kind not in ("traditional", "roth"):
            raise ValueError(f"account_kind must be 'traditional' or 'roth', got {account_kind!r}")
        self.account_kind = account_kind
        self.decedent_birthdate = decedent_birthdate
        self.decedent_death_date = decedent_death_date
        self.beneficiary_birthdate = beneficiary_birthdate

        self.death_year = decedent_death_date.year
        self.final_distribution_year = self.death_year + 10  # must be $0 by Dec 31 of this year
        self.first_stretch_rmd_year = self.death_year + 1

        decedent_age_at_death = (
            decedent_death_date.year - decedent_birthdate.year
            - ((decedent_death_date.month, decedent_death_date.day) < (decedent_birthdate.month, decedent_birthdate.day))
        )
        decedent_rbd_age = rmd_age(decedent_birthdate.year)
        self.annual_rmd_required = account_kind == "traditional" and decedent_age_at_death >= decedent_rbd_age

        if self.annual_rmd_required:
            beneficiary_age_at_first_rmd = self.first_stretch_rmd_year - beneficiary_birthdate.year
            self._initial_factor = single_life_initial_factor(beneficiary_age_at_first_rmd)

    def required_distribution(self, year: int, balance: float) -> float:
        """The minimum required distribution for `year`, given the account's
        balance at the start of that year. 0.0 outside the 10-year window
        (before death or after full distribution) and in years 1-9 when no
        annual RMD is required; the full balance in the final year."""
        if balance <= 0 or year <= self.death_year:
            return 0.0
        if year >= self.final_distribution_year:
            return balance
        if not self.annual_rmd_required:
            return 0.0
        years_since_first = year - self.first_stretch_rmd_year
        factor = self._initial_factor - years_since_first
        if factor <= 0:
            return balance  # table exhausted -- distribute what's left
        return min(balance, balance / factor)
