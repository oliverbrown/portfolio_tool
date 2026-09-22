"""The actual multi-year search -- see package docstring for the overall
approach. Everything here is dependency-free (no numpy/scipy): the search
itself is a from-scratch coordinate-ascent / pattern search (repeatedly
nudge one year's conversion amount up or down, keep whatever's better,
shrink the step size once a full pass finds no improvement, try a few
different starting points), which is the "real numerical search" the
roadmap item asked for while staying in the spirit of pt's own
dependency-free, easily-auditable design. It is NOT guaranteed to find the
true global optimum -- see optimize()'s docstring.

Objective ("after-tax total estate value" -- see after_tax_estate_value()):
Traditional dollars (owned or inherited) are fully taxable as ordinary
income to whoever eventually withdraws them (the household's own future
RMDs, or a non-spouse heir's own SECURE Act 10-year rule); Roth dollars
pass tax-free; Taxable-brokerage dollars get a cost-basis step-up at death
in real life (no capital-gains tax to heirs on appreciation through the
date of death -- pt's own "Documented simplifications" already notes this
isn't modeled during the household's OWN lifetime withdrawals, but IS the
right assumption for what heirs receive). heir_tax_rate is a single
flat rate standing in for the eventual tax on Traditional dollars, since
this tool has no visibility into an heir's own future tax situation --
see README.md for the full discussion of this simplification."""
import copy
import itertools
import random

from pt import projection as projection_mod
from pt import social_security as social_security_mod


class OptimizerError(Exception):
    pass


def after_tax_estate_value(row: dict, heir_tax_rate: float) -> float:
    """row: one row from project()'s output -- see estate_valuation_row()
    for which one this package actually scores against (NOT necessarily
    the last). heir_tax_rate: a fraction (e.g. 0.24 for 24%), NOT a
    percentage -- see _parse_heir_tax_rate(). Returns the estate's value
    as if every Traditional dollar (owned or inherited) were taxed at
    heir_tax_rate on its way to whoever eventually withdraws it, while
    Taxable and Roth dollars (owned or inherited) pass through at full
    value -- see module docstring for why."""
    traditional = row["traditional_balance"] + row["inherited_traditional_balance"]
    tax_free = row["taxable_balance"] + row["roth_balance"] + row["inherited_roth_balance"]
    return tax_free + traditional * (1 - heir_tax_rate)


def lifetime_taxes_paid(rows: list, target_row: dict) -> float:
    """Sum of every row's own 'tax' field (see pt/projection.py's
    project() -- each year's federal_tax() owed, ordinary income plus
    capital gains) from the start of the projection through target_row,
    inclusive (matched by year). Pass estate_valuation_row()'s own
    return value as target_row so this sums through the SAME horizon
    after_tax_estate_value() is scored against (see that function's
    docstring for exactly which row that is and why).

    Deliberately NOT project()'s own cumulative_tax_in_retirement field
    -- that one is only accumulated for years the household is marked
    retired (see project()'s module docstring), but a Roth conversion
    window can start before retirement, so summing 'tax' directly here
    captures every dollar of tax the household pays over its lifetime
    under this schedule, working years included."""
    total = 0.0
    for row in rows:
        total += row["tax"]
        if row["year"] >= target_row["year"]:
            break
    return total


def _people_from_profile(profile: dict) -> dict:
    """{name (lowercase): birthdate} for primary and, if present, spouse
    -- same shape/rule as pt.projection's own _people(), kept as its own
    copy here (that one is private to pt/projection.py) -- same
    convention _load_profile_for() in cli.py already follows."""
    primary = profile["people"]["primary"]
    people = {primary["name"].lower(): primary["birthdate"]}
    spouse = profile.get("people", {}).get("spouse")
    if spouse:
        people[spouse["name"].lower()] = spouse["birthdate"]
    return people


def life_expectancy_end_year(scenario: dict, profile: dict):
    """The LATER of the household's own stated life expectancies
    (retirement.life_expectancy for the primary, retirement.
    spouse_life_expectancy for a spouse, if the profile has one) --
    each as a calendar year (that person's birth year + their own life
    expectancy). Returns None if neither is configured (the caller falls
    back to project()'s own natural last row in that case)."""
    retirement = scenario.get("retirement") or {}
    people = _people_from_profile(profile)
    primary_name = profile["people"]["primary"]["name"].lower()
    spouse_name = next((n for n in people if n != primary_name), None)

    end_years = []
    primary_life_expectancy = retirement.get("life_expectancy")
    if primary_life_expectancy is not None:
        end_years.append(people[primary_name].year + primary_life_expectancy)
    if spouse_name:
        spouse_life_expectancy = retirement.get("spouse_life_expectancy")
        if spouse_life_expectancy is not None:
            end_years.append(people[spouse_name].year + spouse_life_expectancy)
    return max(end_years) if end_years else None


def estate_valuation_row(rows: list, scenario: dict, profile: dict) -> dict:
    """The row this package actually scores with after_tax_estate_value()
    -- NOT necessarily the LAST row project() returns. project()'s own
    last row reflects scenario.mortality (or age 100 if that's unset) --
    the right choice for `pt.cli project`/`report`/`monte-carlo`, meant
    to show the full, detailed trajectory (survivor spending/filing-
    status changes and all, if mortality is configured) -- those commands
    are entirely UNAFFECTED by this function; they still use project()'s
    own last row exactly as documented.

    For OPTIMIZATION/PLANNING purposes, though (every function in this
    module), the estate is instead valued as of the household's own
    stated life expectancy -- retirement.life_expectancy/
    spouse_life_expectancy, the LATER of the two for a couple, since
    that's when the estate actually passes to heirs -- see
    life_expectancy_end_year() -- rather than an unrealistic age-100
    assumption, and without requiring a full scenario.mortality block
    (with its own survivor-spending/filing-status mechanics) just to get
    a realistic answer out of a search. This IS a real simplification:
    the row at that year still reflects whatever cash-flow behavior
    project() computed WITHOUT mortality applied (both still "alive",
    MFJ filing, etc., if scenario.mortality isn't also set) -- only the
    BALANCE fields this reads are treated as if that's when the estate
    transfers, not a fully re-simulated survivor scenario. Set
    scenario.mortality too (matching your own life_expectancy figures,
    say) if you want the two to agree exactly, or leave the two
    deliberately different if you want the optimizer to plan against a
    different horizon than what `report` displays.

    Falls back to project()'s own last row if life_expectancy isn't
    configured for anyone."""
    target_year = life_expectancy_end_year(scenario, profile)
    if target_year is None:
        return rows[-1]
    for row in rows:
        if row["year"] >= target_year:
            return row
    return rows[-1]


def _project_without_mortality(profile: dict, scenario: dict, account_rows: list, blended_rate: float,
                                start_year: int = None, bucket_rates: dict = None) -> list:
    """Runs project() against a deep copy of `scenario` with any
    configured scenario.mortality stripped out, so neither the primary's
    nor a spouse's configured death date can cut the run short --
    guarantees the run reaches the primary person's own age-100 year,
    exactly as project()'s module docstring describes its default
    (no-mortality) stopping behavior. `scenario` here is expected to
    already have a schedule applied (see apply_schedule()) or be the
    as-configured scenario itself -- this doesn't touch anything but
    mortality.

    Called ONCE per schedule (not once per requested age) -- see
    _row_at_age(), which then picks whichever ages optimize()'s own
    projection_ages parameter asks for out of this single run's rows.
    Used only for the age-projection points on the iteration-log scatter
    chart -- the optimizer's own scoring everywhere else still uses
    estate_valuation_row()'s life-expectancy row, never this."""
    no_mortality_scenario = copy.deepcopy(scenario)
    no_mortality_scenario.pop("mortality", None)
    return projection_mod.project(profile, no_mortality_scenario, account_rows, blended_rate,
                                   start_year=start_year, bucket_rates=bucket_rates)


def _row_at_age(rows: list, profile: dict, age: int) -> dict:
    """The row matching the primary person's `age`-th birthday year
    (their own birth year + age) -- the same "first row at/after the
    target year, else the last row" fallback estate_valuation_row() uses
    for a configured life expectancy, just parameterized by an explicit
    age instead. Pass `rows` from _project_without_mortality() so this
    fallback only bites for an age past 100 (what that function's own
    run covers), not because scenario.mortality cut the run short."""
    primary_name = profile["people"]["primary"]["name"].lower()
    birth_year = _people_from_profile(profile)[primary_name].year
    target_year = birth_year + age
    for row in rows:
        if row["year"] >= target_year:
            return row
    return rows[-1]


# One entry per kind of window the search can draw decision variables
# from -- which top-level scenario section it lives in, the row field
# project() reports its ACTUAL amount under (see actual_conversions()),
# and the constraints field name for its own flat per-year dollar cap
# (see optimize()'s var_upper_bounds) -- None if that kind has no such
# cap mechanism (inherited_withdrawal: see _distribution_windows()).
_WINDOW_KINDS = {
    "roth_conversion": {
        "section": "roth_conversions",
        "actual_field": "roth_conversion_by_owner",
        "cap_field": "max_conversion_per_year",
    },
    "ira_distribution": {
        "section": "ira_distributions",
        "actual_field": "ira_distribution_by_owner",
        "cap_field": "max_distribution_per_year",
    },
    "inherited_withdrawal": {
        "section": "inherited_accounts",
        "actual_field": "inherited_distribution_by_account",
        "cap_field": None,
    },
}


def _distribution_windows(scenario: dict, include_ira_distributions: bool = False,
                           include_inherited_withdrawals: bool = False) -> list:
    """Extracts (owner, actual_key, start_year, end_year, constraints_dict,
    kind, section, section_index) from scenario.roth_conversions and,
    depending on the flags, scenario.ira_distributions and/or
    scenario.inherited_accounts too -- one window per entry, in that
    section order, each section in its own original order. These define
    WHICH year/kind combinations the optimizer searches over.
    `actual_key` is what to look up in that kind's actual_field row dict
    (see _WINDOW_KINDS) -- an owner name for roth_conversion/
    ira_distribution, an account_number for inherited_withdrawal (see
    below). Only owner/start_date/end_date/constraints matter here;
    whatever annual_amount (if any) an entry already has is irrelevant --
    the optimizer always replaces it with its own per-year schedule.

    include_ira_distributions (default False, for backward compatibility --
    plain `optimize()` calls search roth_conversions only, exactly as
    before): when True, scenario.ira_distributions entries become a
    SECOND family of decision variables, searched JOINTLY with
    roth_conversions in the same pattern-search pass -- not as a separate
    optimization -- since both draw from the same owner's Traditional
    dollars and compete for the same annual bracket/IRMAA room each year
    (see pt/projection.py's project(), which processes roth_conversions
    then ira_distributions against a shared running ordinary-income
    total). This lets the search decide, dollar for dollar: convert to
    Roth (tax now, tax-free forever), or take a plain taxable IRA
    distribution (tax now, lands in Taxable -- more liquid, and future
    growth there is only partially taxed via cap gains) -- instead of the
    two being tuned independently. A scenario with no ira_distributions
    section at all is unaffected either way.

    include_inherited_withdrawals (default False): a THIRD family --
    scenario.inherited_accounts[i].planned_withdrawal's own annual_amount
    (see pt/projection.py's _planned_withdrawal_floor(), which now
    accepts a per-year list the same way roth_conversions/
    ira_distributions do). This is a FLOOR on top of that account's own
    legally-required 10-year-rule distribution (project() always takes
    the greater of the two, and always forces the full remaining balance
    out by the 10th year regardless of what's searched here) -- so what's
    actually being searched is "how much EXTRA, above the legal minimum,
    to withdraw this year," e.g. to smooth tax brackets across the
    10-year window instead of taking the legally-required stretch amount
    every year and a large lump in year 10. Unlike the other two kinds,
    this one has NO dynamic bracket/IRMAA-avoidance constraint mechanism
    of its own (planned_withdrawal supports no `constraints` -- the
    search's own objective is what discourages withdrawing so much it
    pushes into a worse tax outcome, not an explicit per-year cap); its
    only natural bound is the account's own remaining balance, which
    project() already enforces. Only an inherited_accounts entry that (a)
    already has a planned_withdrawal {start_date, end_date, ...} window
    configured (the search fills in annual_amount; the window itself
    isn't invented from nothing, matching how a roth_conversions/
    ira_distributions window must already exist too) and (b) is a REAL
    already-held account (has its own account_number, not a hypothetical
    future inheritance identified only by a beneficiary/decedent-death-
    year pair) is searchable -- entries missing either are silently
    skipped, not an error, since not every inherited account need be
    part of the search.

    Raises OptimizerError if there isn't at least one window (from any
    included section) to search."""
    windows = []
    kinds = ["roth_conversion"]
    if include_ira_distributions:
        kinds.append("ira_distribution")
    if include_inherited_withdrawals:
        kinds.append("inherited_withdrawal")

    for kind in kinds:
        section = _WINDOW_KINDS[kind]["section"]
        if kind == "inherited_withdrawal":
            for i, entry in enumerate(scenario.get(section) or []):
                planned = entry.get("planned_withdrawal")
                account_number = entry.get("account_number")
                if not planned or account_number is None:
                    continue
                windows.append({
                    "kind": kind,
                    "section": section,
                    "section_index": i,
                    "owner": entry.get("beneficiary"),
                    "actual_key": account_number,
                    "account_number": account_number,
                    "start_year": planned["start_date"].year,
                    "end_year": planned["end_date"].year,
                    "constraints": None,
                })
            continue
        for i, entry in enumerate(scenario.get(section) or []):
            for field in ("owner", "start_date", "end_date"):
                if field not in entry:
                    raise OptimizerError(f"{section} entry is missing '{field}'.")
            windows.append({
                "kind": kind,
                "section": section,
                "section_index": i,
                "owner": entry["owner"],
                "actual_key": entry["owner"],
                "account_number": None,
                "start_year": entry["start_date"].year,
                "end_year": entry["end_date"].year,
                "constraints": entry.get("constraints"),
            })
    if not windows:
        sections = " or ".join(_WINDOW_KINDS[k]["section"] for k in kinds)
        raise OptimizerError(
            f"scenario.{sections} has no entries to optimize -- add at least one "
            "{owner, start_date, end_date} window (annual_amount isn't needed; the optimizer "
            "decides that -- constraints, if given, are kept as caps during the search)."
        )
    return windows


def conversion_owners(scenario: dict) -> list:
    """Distinct owners appearing in scenario.roth_conversions, in order of
    first appearance -- used by `priority` (see reorder_by_owner_priority()
    below) to know which owners there are to compare orderings for."""
    owners = []
    for entry in scenario.get("roth_conversions") or []:
        owner = entry.get("owner")
        if owner and owner not in owners:
            owners.append(owner)
    return owners


def reorder_by_owner_priority(scenario: dict, first_owner: str) -> dict:
    """Returns a deep copy of `scenario` with its roth_conversions list
    reordered so every entry belonging to `first_owner` comes before every
    entry belonging to any other owner -- a STABLE reorder (Python's sort
    is stable), so two entries for the same owner, or two other owners'
    entries relative to each other, keep their original relative order.

    This is what actually controls which owner's conversion gets first
    claim on a shared household bracket ceiling each year: project()'s
    roth_conversions loop accumulates ordinary_income_so_far strictly in
    scenario.roth_conversions' own list order (see its module docstring),
    so whoever's entry comes first sees a lower ordinary_income_so_far and
    gets first access to whatever max_marginal_bracket/avoid_irmaa_tier
    room remains THAT YEAR -- the other owner's conversion that same year
    is capped by whatever's left over, if anything, even if they still
    have Traditional balance and search-requested amount available. Only
    matters when two owners' constraints draw from a genuinely SHARED
    limit (a household MFJ bracket, say) and at least one of them would
    otherwise convert enough to bump into it -- otherwise reordering
    changes nothing.

    Raises OptimizerError if `first_owner` has no roth_conversions entries
    at all."""
    owners = conversion_owners(scenario)
    if first_owner not in owners:
        raise OptimizerError(
            f"'{first_owner}' has no scenario.roth_conversions entries -- owners present: "
            f"{owners or '(none)'}"
        )
    candidate = copy.deepcopy(scenario)
    candidate["roth_conversions"] = sorted(
        candidate["roth_conversions"],
        key=lambda entry: 0 if entry.get("owner") == first_owner else 1,
    )
    return candidate


def apply_schedule(scenario: dict, windows: list, schedule: list) -> dict:
    """Returns a deep copy of `scenario` with each window's own entry (in
    whichever section -- see each window's "section"/"section_index")
    having its annual_amount replaced by schedule[i] (a list of one
    dollar amount per year in windows[i]'s window, in order) --
    everything else about that entry is left unchanged, so any
    constraints configured on it still apply as caps on top of the
    schedule (see pt/projection.py's _constrained_distribution_amount()).
    An inherited_withdrawal window (see _distribution_windows()) writes
    into that entry's NESTED planned_withdrawal.annual_amount instead of
    the entry's own top-level annual_amount, since that's where
    _planned_withdrawal_floor() reads it from -- everything else about
    the inherited_accounts entry (its own legally-required 10-year-rule
    schedule, balance, etc.) is untouched.
    `windows` must be _distribution_windows(scenario)'s own return value
    (or match its order/length/section/section_index/kind exactly) --
    this doesn't re-derive it, to avoid computing it fresh on every one
    of a search's many evaluations."""
    candidate = copy.deepcopy(scenario)
    for i, w in enumerate(windows):
        entry = candidate[w["section"]][w["section_index"]]
        if w["kind"] == "inherited_withdrawal":
            entry["planned_withdrawal"]["annual_amount"] = list(schedule[i])
        else:
            entry["annual_amount"] = list(schedule[i])
    return candidate


def evaluate_schedule(profile: dict, scenario: dict, account_rows: list, blended_rate: float,
                       windows: list, schedule: list, heir_tax_rate: float, start_year: int = None,
                       bucket_rates: dict = None, iteration_log: list = None) -> float:
    """Runs project() with `schedule` applied (see apply_schedule()) and
    returns after_tax_estate_value() of the row estate_valuation_row()
    picks -- the single number optimize() is trying to maximize (scored
    against the household's own life_expectancy, not necessarily
    project()'s own last row -- see that function's docstring).
    bucket_rates (optional): see optimize()'s docstring -- passed
    straight through to project().

    iteration_log (optional): if given, appends {"lifetime_taxes":
    lifetime_taxes_paid(...), "after_tax_estate_value": <the value this
    call returns}} to this list -- one entry per call, i.e. per candidate
    schedule this function scores. optimize() threads its own
    iteration_log parameter through to every evaluation the pattern
    search performs (see that function's docstring), so passing one
    there records the whole tradeoff the search explored -- lifetime
    taxes paid vs. after-tax estate value for every schedule tried, not
    just the winner -- meant for plotting that tradeoff as a scatter,
    the same kind of chart a Monte-Carlo-style Roth optimizer (e.g.
    rothblueprint.com) shows."""
    candidate = apply_schedule(scenario, windows, schedule)
    rows = projection_mod.project(profile, candidate, account_rows, blended_rate, start_year=start_year,
                                   bucket_rates=bucket_rates)
    row = estate_valuation_row(rows, scenario, profile)
    value = after_tax_estate_value(row, heir_tax_rate)
    if iteration_log is not None:
        iteration_log.append({
            "lifetime_taxes": lifetime_taxes_paid(rows, row),
            "after_tax_estate_value": value,
        })
    return value


def actual_conversions(profile: dict, scenario: dict, account_rows: list, blended_rate: float,
                        windows: list, schedule: list, start_year: int = None,
                        bucket_rates: dict = None) -> list:
    """`schedule` is what a window's own annual_amount REQUESTS -- but a
    dynamic constraint on that window (max_marginal_bracket,
    avoid_irmaa_tier, preserve_cash_reserve; see project()'s
    _constrained_distribution_amount()) can clamp the ACTUAL amount for a
    given year down to less than requested, since those depend on income
    known only at simulation time (unlike a static max_conversion_per_year/
    max_distribution_per_year, which optimize() already uses as the
    search's own per-variable bound -- see optimize()'s docstring). This
    runs the schedule through project() once and reads back each window's
    REAL per-year amount (project()'s roth_conversion_by_owner or
    ira_distribution_by_owner row field, per that window's own "kind" --
    see _WINDOW_KINDS) -- what actually happens, for accurate reporting,
    as opposed to schedule's raw requested numbers.

    Returns a list of per-window lists (one dollar amount per year, same
    shape as `schedule`) -- if two windows of the SAME kind share the
    same actual_key (e.g. two roth_conversion windows for the same
    owner -- unusual, but not disallowed), both get its COMBINED actual
    total for a year they overlap in, since project() doesn't track
    amounts more finely than per-actual_key-per-kind; windows of
    different kinds stay independent of each other (different row
    fields) even for the same owner/account. bucket_rates (optional):
    see optimize()'s docstring -- passed straight through to project()."""
    candidate = apply_schedule(scenario, windows, schedule)
    rows = projection_mod.project(profile, candidate, account_rows, blended_rate, start_year=start_year,
                                   bucket_rates=bucket_rates)
    rows_by_year = {r["year"]: r for r in rows}
    actual = []
    for w in windows:
        actual_field = _WINDOW_KINDS[w["kind"]]["actual_field"]
        actual.append([
            rows_by_year.get(year, {}).get(actual_field, {}).get(w["actual_key"], 0.0)
            for year in range(w["start_year"], w["end_year"] + 1)
        ])
    return actual


def _flatten(windows: list) -> list:
    """[(window_index, year), ...] -- one entry per decision variable, in
    a fixed order used throughout the search."""
    var_specs = []
    for i, w in enumerate(windows):
        for year in range(w["start_year"], w["end_year"] + 1):
            var_specs.append((i, year))
    return var_specs


def _unflatten(windows: list, var_specs: list, x: list) -> list:
    """The inverse of _flatten() -- x (one value per var_specs entry) back
    into a list of per-window lists, in windows' order, for
    apply_schedule()."""
    schedule = [[0.0] * (w["end_year"] - w["start_year"] + 1) for w in windows]
    for (window_idx, year), amount in zip(var_specs, x):
        year_idx = year - windows[window_idx]["start_year"]
        schedule[window_idx][year_idx] = amount
    return schedule


def optimize(profile: dict, scenario: dict, account_rows: list, blended_rate: float,
             heir_tax_rate: float, upper_bound: float = None, min_step: float = 1000.0,
             restarts: int = 3, max_rounds: int = 200, seed: int = None,
             start_year: int = None, progress_callback=None, bucket_rates: dict = None,
             include_ira_distributions: bool = False,
             include_inherited_withdrawals: bool = False, iteration_log: list = None,
             projection_ages: list = None) -> dict:
    """The actual multi-year search. One decision variable per (owner,
    year) across every scenario.roth_conversions window, PLUS (if
    `include_ira_distributions`) every scenario.ira_distributions window
    too (see _distribution_windows()) -- e.g. two owners with 11- and
    15-year roth_conversions windows gives 26 variables; adding a matching
    pair of ira_distributions windows would give 52. Each is a dollar
    amount in [0, upper_bound]; project()'s own logic already floors an
    over-large request at whatever's actually available in that owner's
    Traditional pools that year (see project()'s module docstring), and
    any constraints configured on a window (max_marginal_bracket,
    avoid_irmaa_tier, max_conversion_per_year/max_distribution_per_year,
    preserve_cash_reserve) still cap it too -- see apply_schedule().

    Algorithm: coordinate-ascent / pattern search (a classic
    derivative-free method, not a call into any external solver -- see
    package docstring). From each of a few starting points (all-zero,
    evenly-spread, and a couple of random ones), repeatedly cycles
    through every decision variable in a random order, nudging each one
    up or down by the current step size and keeping whichever direction
    improves the objective (or leaving it alone if neither does).
    Once a full pass finds no improvement anywhere, the step size halves;
    the search for that starting point stops once the step size drops
    below min_step (dollars) or max_rounds passes have run. Keeps
    whichever starting point's result is best. This finds a GOOD schedule,
    not a certified globally-optimal one -- see README.md.

    upper_bound (optional): the largest single-year conversion amount the
    search considers, before any of the capping above -- defaults to the
    household's current total account value (generous; realistic caps
    come from the constraints/balance logic instead). min_step: the
    search stops refining a given starting point once it's nudging
    amounts by less than this many dollars. restarts: how many starting
    points to try (>=1; the all-zero and evenly-spread points always run
    first, additional ones are random). seed (optional): makes the whole
    search reproducible (random starting points AND the order variables
    are visited each pass); omit for a fresh result each call.
    progress_callback (optional): called as progress_callback(restart_index,
    total_restarts, round_number, step, best_value_so_far) after each
    pattern-search round, e.g. for a CLI to print progress on a long run.
    bucket_rates (optional): a {bucket_key: rate} dict (see
    projection.bucket_allocation_rows()/pt.cli's --per-bucket-growth) --
    grows each tax bucket at its own blended rate instead of applying
    blended_rate uniformly, for every project() call this function makes
    (the search itself, zero_conversions_value, original_value, and the
    final actual_schedule) -- so "optimal" is judged consistently against
    the same growth assumption throughout. blended_rate is still required
    even when this is given: it's the fallback rate for any bucket this
    dict doesn't cover (see project()'s own bucket_rates docs), and it's
    what zero_conversions_value/original_value would use if bucket_rates
    were omitted. Static for the whole search (unlike --per-bucket-growth's
    Monte Carlo counterpart, this optimizer's search is deterministic, so
    there's no year-to-year correlated draw to thread through here).
    include_ira_distributions (default False, backward compatible):
    jointly searches scenario.ira_distributions windows alongside
    roth_conversions in the SAME pattern-search pass, instead of only
    ever tuning roth_conversions -- see _distribution_windows()'s
    docstring for why joint (not two separate searches) is the right
    model here. False (the default) searches roth_conversions only,
    exactly as before this parameter existed.
    include_inherited_withdrawals (default False): jointly searches a
    THIRD family -- each already-configured scenario.inherited_accounts
    planned_withdrawal window (see _distribution_windows()'s docstring
    for exactly what this searches and why it has no dynamic constraint
    mechanism of its own, unlike the other two kinds).

    iteration_log (optional): if given, every evaluate_schedule() call
    the pattern search performs -- every candidate schedule it scores,
    across every restart, not just improving moves or each restart's
    final result -- appends {"lifetime_taxes", "after_tax_estate_value"}
    to this list (see evaluate_schedule()'s own iteration_log docstring).
    Does NOT include the zero_conversions_value/original_value reference
    points computed below (those aren't schedules the search itself
    tried). Intended for plotting the tradeoff the search explored, e.g.
    a lifetime-taxes-paid vs. after-tax-estate-value scatter.

    projection_ages (optional): extra ages (e.g. [85, 90, 95, 100]) to
    ALSO value the winning/zero_conversions/original-as-configured
    schedules at, alongside the household's own life-expectancy row --
    each age re-runs that schedule with scenario.mortality stripped (see
    _project_without_mortality()) and reads back the row at the primary
    person's own that-age birthday (see _row_at_age()), one project()
    call per schedule REGARDLESS of how many ages are requested (every
    age is read from that one run). None/empty (the default) skips this
    entirely -- no extra project() calls, and the returned
    age_projections key is None. Meant for the scatter chart's optional
    "how does each schedule's own estate value keep growing past life
    expectancy" trajectory -- unlike the age-100-only version this
    replaced, an unbounded age list can stretch the chart's own axis
    scale enough to compress the main cloud, so this is opt-in and the
    caller (see roth_optimizer/cli.py's --optimizer-projection-ages)
    picks which ages actually matter rather than always reaching for 100.

    Raises OptimizerError if none of the included families (roth_
    conversions always; ira_distributions if include_ira_distributions;
    inherited_accounts planned_withdrawal windows if
    include_inherited_withdrawals) has any entries to search.

    Returns:
      windows: _distribution_windows()'s own return value (owner/
        start_year/end_year/constraints/kind/section/section_index per
        entry, in order) -- schedule below is aligned to this same order
      schedule: list of per-window lists (one dollar amount per year,
        rounded to the nearest dollar) -- the winning schedule's REQUESTED
        amounts (what ends up in annual_amount if you use --out-scenario)
      actual_schedule: same shape as schedule, but the ACTUAL amount that
        converts/distributes each year once any dynamic constraint (see
        actual_conversions()) clamps a request down -- what really
        happens; use this for display, `schedule` for round-tripping back
        into a scenario file (project() will re-derive the same actual
        amounts from it, given the same constraints)
      value: the winning schedule's after_tax_estate_value()
      lifetime_taxes: the winning schedule's own lifetime_taxes_paid() --
        paired with `value`, the same (x, y) an iteration_log entry
        carries, for whichever schedule this search actually returns
      zero_conversions_value: after_tax_estate_value() with every included
        family's discretionary lever fully backed off -- NO Roth
        conversions; NO IRA distributions if include_ira_distributions;
        and, if include_inherited_withdrawals, each searched inherited
        account's planned_withdrawal floor zeroed (its own legally-
        required 10-year-rule schedule is NEVER zeroed -- that's
        mandatory, not a lever this search controls) -- the floor this is
        being compared against; still just roth_conversions removed when
        neither other flag is set, exactly as before those parameters
        existed
      zero_conversions_lifetime_taxes: lifetime_taxes_paid() to go with
        zero_conversions_value -- the two paired the same way as
        `value`/`lifetime_taxes` above
      original_value: after_tax_estate_value() with the INPUT scenario's
        roth_conversions/ira_distributions/planned_withdrawal windows used
        exactly as given (whatever annual_amount/constraints the
        household already configured) -- None if that raises a
        ProjectionError (e.g. the household only gave owner/start_date/
        end_date with no annual_amount or a flat cap, meaning there's no
        "as configured" schedule to compare against in the first place)
      original_lifetime_taxes: lifetime_taxes_paid() to go with
        original_value -- None under the same condition original_value is
      life_expectancy_age: the primary person's own age in
        life_expectancy_end_year(scenario, profile) -- the calendar
        year `value`/`lifetime_taxes` (and zero_conversions_value/
        original_value) are actually valued as of -- None if
        life_expectancy isn't configured for anyone
      projection_ages: the ages actually used (sorted, deduplicated), or
        None if the `projection_ages` parameter was empty/omitted --
        echoes the input back so a caller (e.g. cli.py) doesn't have to
        hang onto its own copy just to know what age_projections covers
      age_projections: None if `projection_ages` was empty/omitted;
        otherwise a dict with three keys -- "winner", "zero_conversions",
        and "original" (this last one None exactly when original_value
        is) -- each a list of {"age", "lifetime_taxes",
        "after_tax_estate_value"} dicts, one per projection_ages entry,
        in the same order, for that schedule re-valued at each age (see
        this parameter's own docstring above and _row_at_age())
      evaluations: number of candidate schedules the pattern search itself
        scored (equals len(iteration_log) when one is passed) -- for a
        sense of how much work the search did. Deliberately excludes the
        handful of fixed extra project() runs outside the search: the
        zero_conversions/original reference runs, the one read-back of
        the winner's own lifetime taxes, and any age_projections runs --
        those are constant overhead, not search effort.
    """
    windows = _distribution_windows(scenario, include_ira_distributions=include_ira_distributions,
                                     include_inherited_withdrawals=include_inherited_withdrawals)
    var_specs = _flatten(windows)
    n = len(var_specs)
    rng = random.Random(seed)
    evaluations = 0

    if upper_bound is None:
        upper_bound = sum(r["value"] for r in account_rows)
    upper_bound = max(upper_bound, min_step)

    # A static max_conversion_per_year constraint (if configured on a
    # window) becomes THAT window's own upper bound during the search --
    # not just left for project() to clamp -- otherwise any requested
    # amount above the cap simulates identically (all clamp to the same
    # actual conversion), so the search has no reason to land exactly at
    # the boundary and the reported schedule would misleadingly show a
    # bigger number than what actually gets converted. Dynamic,
    # income-dependent constraints (max_marginal_bracket, avoid_irmaa_tier,
    # preserve_cash_reserve) can't be pre-bounded this way -- project()
    # still enforces those on every evaluation either way, so the
    # simulated OUTCOME is always correct regardless; only the reported
    # schedule's exact numbers could still slightly overstate what a
    # dynamic constraint ends up allowing in a given year.
    var_upper_bounds = []
    for window_idx, _year in var_specs:
        bound = upper_bound
        w = windows[window_idx]
        cap_field = _WINDOW_KINDS[w["kind"]]["cap_field"]
        cap = (w.get("constraints") or {}).get(cap_field)
        if cap is not None:
            bound = min(bound, cap)
        var_upper_bounds.append(bound)

    def evaluate(x):
        nonlocal evaluations
        evaluations += 1
        schedule = _unflatten(windows, var_specs, x)
        return evaluate_schedule(profile, scenario, account_rows, blended_rate, windows, schedule,
                                  heir_tax_rate, start_year=start_year, bucket_rates=bucket_rates,
                                  iteration_log=iteration_log)

    zero_conversions_scenario = copy.deepcopy(scenario)
    zero_conversions_scenario["roth_conversions"] = []
    if include_ira_distributions:
        zero_conversions_scenario["ira_distributions"] = []
    if include_inherited_withdrawals:
        # Zero only the searched entries' OWN planned_withdrawal floor --
        # never remove the inherited_accounts entry itself or touch its
        # legally-required 10-year-rule schedule, which project() enforces
        # regardless and isn't a lever this search (or a household) controls.
        for w in windows:
            if w["kind"] == "inherited_withdrawal":
                zero_conversions_scenario["inherited_accounts"][w["section_index"]][
                    "planned_withdrawal"]["annual_amount"] = 0
    zero_rows = projection_mod.project(profile, zero_conversions_scenario, account_rows, blended_rate,
                                        start_year=start_year, bucket_rates=bucket_rates)
    zero_conversions_row = estate_valuation_row(zero_rows, scenario, profile)
    zero_conversions_value = after_tax_estate_value(zero_conversions_row, heir_tax_rate)
    zero_conversions_lifetime_taxes = lifetime_taxes_paid(zero_rows, zero_conversions_row)

    try:
        original_rows = projection_mod.project(profile, scenario, account_rows, blended_rate,
                                                 start_year=start_year, bucket_rates=bucket_rates)
        original_row = estate_valuation_row(original_rows, scenario, profile)
        original_value = after_tax_estate_value(original_row, heir_tax_rate)
        original_lifetime_taxes = lifetime_taxes_paid(original_rows, original_row)
    except projection_mod.ProjectionError:
        original_value = None
        original_lifetime_taxes = None

    # The life-expectancy row's YEAR depends only on scenario/profile
    # (retirement.life_expectancy/spouse_life_expectancy), never on which
    # schedule is applied -- so the household's age there is the same
    # for the winner, zero_conversions, and original comparisons alike;
    # compute it once here rather than three times below.
    life_expectancy_target_year = life_expectancy_end_year(scenario, profile)
    if life_expectancy_target_year is not None:
        primary_name = profile["people"]["primary"]["name"].lower()
        primary_birth_year = _people_from_profile(profile)[primary_name].year
        life_expectancy_age = life_expectancy_target_year - primary_birth_year
    else:
        life_expectancy_age = None

    starting_points = [[0.0] * n, [b / 4 for b in var_upper_bounds]]
    while len(starting_points) < max(1, restarts):
        starting_points.append([rng.uniform(0.0, b / 4) for b in var_upper_bounds])

    best_x, best_value = None, float("-inf")
    for restart_idx, start_x in enumerate(starting_points):
        x = list(start_x)
        value = evaluate(x)
        step = upper_bound / 10
        for round_num in range(max_rounds):
            if step < min_step:
                break
            improved = False
            order = list(range(n))
            rng.shuffle(order)
            for i in order:
                current = x[i]
                current_value = value
                for candidate in (current - step, current + step):
                    candidate = max(0.0, min(var_upper_bounds[i], candidate))
                    if candidate == current:
                        continue
                    trial = list(x)
                    trial[i] = candidate
                    trial_value = evaluate(trial)
                    if trial_value > current_value:
                        current, current_value = candidate, trial_value
                if current != x[i]:
                    x[i], value = current, current_value
                    improved = True
            if progress_callback:
                progress_callback(restart_idx, len(starting_points), round_num, step, max(value, best_value))
            if not improved:
                step /= 2
        if value > best_value:
            best_value, best_x = value, x

    schedule = [[round(v) for v in row] for row in _unflatten(windows, var_specs, best_x)]
    actual_schedule = [
        [round(v) for v in row]
        for row in actual_conversions(profile, scenario, account_rows, blended_rate, windows, schedule,
                                       start_year=start_year, bucket_rates=bucket_rates)
    ]
    # One more evaluation at the exact winning (unrounded) best_x, purely to
    # read back ITS OWN lifetime_taxes_paid() alongside best_value -- cheap
    # (one extra project() call) and avoids trying to re-locate the winning
    # entry inside iteration_log by matching floats.
    winner_log = []
    evaluate_schedule(profile, scenario, account_rows, blended_rate, windows,
                       _unflatten(windows, var_specs, best_x), heir_tax_rate, start_year=start_year,
                       bucket_rates=bucket_rates, iteration_log=winner_log)
    winner_lifetime_taxes = winner_log[0]["lifetime_taxes"]

    # Age-projection trajectories (opt-in -- see this function's own
    # projection_ages docstring). One _project_without_mortality() call
    # per schedule, REGARDLESS of how many ages were requested -- every
    # age in the list is read back out of that same run via _row_at_age().
    projection_ages = sorted(set(projection_ages)) if projection_ages else None
    age_projections = None
    if projection_ages:
        def _age_points(age_scenario):
            age_rows = _project_without_mortality(profile, age_scenario, account_rows, blended_rate,
                                                    start_year=start_year, bucket_rates=bucket_rates)
            points = []
            for age in projection_ages:
                age_row = _row_at_age(age_rows, profile, age)
                points.append({
                    "age": age,
                    "lifetime_taxes": lifetime_taxes_paid(age_rows, age_row),
                    "after_tax_estate_value": after_tax_estate_value(age_row, heir_tax_rate),
                })
            return points

        age_projections = {
            "winner": _age_points(apply_schedule(scenario, windows, schedule)),
            "zero_conversions": _age_points(zero_conversions_scenario),
            "original": None,
        }
        if original_value is not None:
            try:
                age_projections["original"] = _age_points(scenario)
            except projection_mod.ProjectionError:
                age_projections["original"] = None

    return {
        "windows": windows,
        "schedule": schedule,
        "actual_schedule": actual_schedule,
        "value": best_value,
        "lifetime_taxes": winner_lifetime_taxes,
        "zero_conversions_value": zero_conversions_value,
        "zero_conversions_lifetime_taxes": zero_conversions_lifetime_taxes,
        "original_value": original_value,
        "original_lifetime_taxes": original_lifetime_taxes,
        "life_expectancy_age": life_expectancy_age,
        "projection_ages": projection_ages,
        "age_projections": age_projections,
        "evaluations": evaluations,
    }


def claim_age_candidates(scenario: dict) -> list:
    """Names (from scenario.social_security) that have pia_monthly
    configured -- these are the people search_claim_ages() can vary,
    since only a Primary Insurance Amount can be recomputed at a
    different claim age (see pt/social_security.py's
    benefit_at_claim_age()); a name with only a flat monthly_benefit
    (the tool's original convention -- you already know the benefit AT
    your chosen claim_age) is left alone, since there's nothing to
    recompute FROM. In order of appearance in scenario.social_security."""
    return [name for name, info in (scenario.get("social_security") or {}).items() if "pia_monthly" in info]


def apply_claim_ages(scenario: dict, claim_ages: dict) -> dict:
    """Returns a deep copy of `scenario` with
    scenario.social_security[name]["claim_age"] overridden for each name
    in `claim_ages` ({name: age}) -- pia_monthly and everything else
    about that entry is left unchanged, so project() (via
    _resolve_social_security_config()) recomputes monthly_benefit from
    it at the new age. Names not in `claim_ages` are untouched."""
    candidate = copy.deepcopy(scenario)
    for name, age in claim_ages.items():
        candidate["social_security"][name]["claim_age"] = age
    return candidate


def evaluate_claim_ages(profile: dict, scenario: dict, account_rows: list, blended_rate: float,
                         claim_ages: dict, heir_tax_rate: float, start_year: int = None,
                         bucket_rates: dict = None) -> float:
    """Runs project() with `claim_ages` applied (see apply_claim_ages())
    and returns after_tax_estate_value() of the row estate_valuation_row()
    picks -- the SAME objective the dollar-amount search (optimize())
    uses (scored against the household's own life_expectancy, not
    necessarily project()'s own last row), so a claim-age comparison and
    a conversion-schedule comparison mean the same thing. Whatever
    roth_conversions/ira_distributions/inherited_accounts the scenario
    already configures run exactly as given -- this does NOT also re-run
    the dollar-amount search for each claim-age combination (see
    search_claim_ages()'s docstring for why)."""
    candidate = apply_claim_ages(scenario, claim_ages)
    rows = projection_mod.project(profile, candidate, account_rows, blended_rate, start_year=start_year,
                                   bucket_rates=bucket_rates)
    return after_tax_estate_value(estate_valuation_row(rows, scenario, profile), heir_tax_rate)


def search_claim_ages(profile: dict, scenario: dict, account_rows: list, blended_rate: float,
                       heir_tax_rate: float, candidate_ages: list = None, start_year: int = None,
                       bucket_rates: dict = None) -> dict:
    """Brute-force grid search over Social Security claim_age for every
    scenario.social_security name with pia_monthly configured (see
    claim_age_candidates()) -- unlike the dollar-amount search
    (optimize()), this doesn't need a pattern search: claim_age only ever
    takes one of social_security.MAX_CLAIM_AGE - MIN_CLAIM_AGE + 1 = 9
    whole-year values, so even a two-person household (9 x 9 = 81
    combinations) is cheap to try exhaustively -- one project() call per
    combination, no iterative refinement needed.

    Deliberately does NOT jointly re-run the dollar-amount search
    (roth_conversions/ira_distributions/inherited_accounts stay exactly
    as the scenario already configures them, for every combination tried)
    -- claim age changes the household's Social Security income, which
    does shift how much bracket/IRMAA room a Roth conversion has each
    year, so the true joint optimum could differ from running each
    search separately; this keeps the two independent for now, a
    documented scope boundary (see roth_optimizer/README.md), not an
    oversight. Compare the two searches' own results by hand if you want
    to sanity-check how much that might matter for a given household.

    candidate_ages (optional): the ages to try for EVERY candidate name
    (default: every whole year 62-70). bucket_rates (optional): see
    optimize()'s docstring -- passed straight through to project() for
    every evaluation.

    Raises OptimizerError if no scenario.social_security entry has
    pia_monthly configured.

    Returns {"names": claim_age_candidates()'s own return value,
    "best_claim_ages": {name: age} for the winning combination,
    "best_value": its after_tax_estate_value(), "results": every
    combination tried, as [{"claim_ages": {...}, "value": float}, ...],
    in the order evaluated}."""
    names = claim_age_candidates(scenario)
    if not names:
        raise OptimizerError(
            "No scenario.social_security entry has pia_monthly configured -- claim-age search needs at "
            "least one (a flat monthly_benefit can't be recomputed for a different claim age; see "
            "pt/social_security.py)."
        )
    ages = candidate_ages or list(range(social_security_mod.MIN_CLAIM_AGE, social_security_mod.MAX_CLAIM_AGE + 1))

    results = []
    best_value, best_claim_ages = float("-inf"), None
    for combo in itertools.product(ages, repeat=len(names)):
        claim_ages = dict(zip(names, combo))
        value = evaluate_claim_ages(profile, scenario, account_rows, blended_rate, claim_ages, heir_tax_rate,
                                     start_year=start_year, bucket_rates=bucket_rates)
        results.append({"claim_ages": claim_ages, "value": value})
        if value > best_value:
            best_value, best_claim_ages = value, claim_ages

    return {"names": names, "best_claim_ages": best_claim_ages, "best_value": best_value, "results": results}
