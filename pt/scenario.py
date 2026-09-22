"""Retirement scenario: the "what if" assumptions a growth projection runs
against -- retirement ages, spending target, inflation, expected returns,
and Social Security claim ages. Split out from the retirement planning
profile (pt/planning.py, which just holds birthdates) so a household can
keep several scenario files side by side (e.g. retirement_scenario.yaml,
retirement_scenario_early_retirement.yaml,
retirement_scenario_conservative_returns.yaml) all joined against the same
people/birthdates -- there's no fixed scenario filename or name registry;
every command that uses a scenario takes an explicit --scenario PATH,
defaulting to DEFAULT_SCENARIO_PATH.

Like the profile, this lives next to the SQLite database (local-only, never
committed to the project's git repo) and is meant to be hand-edited; there's
no CLI 'set' command for it.

Shape (see README.md "Retirement scenario" for the full example):
    retirement.retirement_age, .spouse_retirement_age, .life_expectancy,
        .spouse_life_expectancy -- ages (ints). spouse_* keys are only
        meaningful if the profile has a people.spouse.
    income.target_spending -- annual household spending, in today's dollars.
    inflation.general -- a percentage point, e.g. 2.5 means 2.5%, not 0.025.
    inflation.medical (optional) -- a separate rate for Medicare Part B/
        IRMAA premiums (see pt/medicare.py), which have historically run
        faster than general CPI. Falls back to inflation.general if unset.
    returns.asset_classes (recommended) -- {class_name: {expected_return,
        volatility}, ...}, one entry per macro or subclass the household
        holds (classifier.ASSET_CLASS_HIERARCHY -- e.g. "US Equity",
        "International Equity", "US Bonds", "Liquidity"), both percentages
        (expected_return same 7.5-means-7.5% convention as below;
        volatility optional per entry, falls back to a rough per-macro
        default -- DEFAULT_VOLATILITY_BY_MACRO). A class not itemized
        falls back to its own macro's entry if one is given, else raises a
        clear error naming the class -- see pt/projection.py's
        resolve_asset_class_returns() for the exact resolution order (and
        an explanation of why "Other"/"Unclassified" always need their own
        entry, with no macro to fall back to). compute_blended_growth_
        rate()/compute_blended_volatility() weight each held class's own
        numbers by the household's actual current allocation into a
        single blended_rate/volatility every `project` run still uses;
        `monte-carlo` additionally draws each held class its own
        correlated random return every simulated year (see
        build_correlated_return_sampler()) rather than one blended draw
        for the whole portfolio -- optionally override the default
        cross-class correlation assumptions via returns.correlations (see
        pt/projection.py's _resolve_correlation()).
    returns.growth, .fixed_income (legacy, still supported) -- percentages,
        same convention, used ONLY if returns.asset_classes isn't present
        at all -- a simpler 2-bucket (equity-like vs. fixed-income-like)
        blend, exactly how every scenario file worked before returns.
        asset_classes existed. returns.growth_volatility, .fixed_income_
        volatility (both optional, percentage points) -- annual standard
        deviation for each in this legacy shape; default to a broad-market
        assumption (DEFAULT_GROWTH_VOLATILITY/DEFAULT_FIXED_INCOME_
        VOLATILITY) if unset. A plain `project` run only ever needs the
        expected-return side of whichever shape is used; the volatility
        side only matters for `pt.cli monte-carlo`.
    cash_reserve.years (optional) -- a policy used by a roth_conversions
        entry's constraints.preserve_cash_reserve: true (see below) -- how
        many years of target_spending, counted forward from the current
        projection year, to keep untouched in the Taxable bucket.
    social_security.<name>.claim_age, .monthly_benefit -- <name> matches a
        people.primary/spouse.name from the profile. monthly_benefit is in
        today's dollars (inflated forward like everything else -- see
        pt/projection.py); Social Security's actual COLA formula isn't
        modeled separately.
    inherited_accounts (optional, a list) -- one entry per inherited IRA/
        Roth IRA, each: type ("traditional" or "roth"), decedent_birthdate,
        decedent_death_date (both YYYY-MM-DD), beneficiary (matches a
        people.primary/spouse.name), and EXACTLY ONE of:
          - account_number (matches a real account already held in the
            database), starting from its current balance -- the normal
            case
          - hypothetical_value -- a HYPOTHETICAL future inheritance not
            yet held at all. Its balance doesn't exist until
            decedent_death_date's year, when it "appears" in the
            simulation at this starting value (see
            pt/projection.py's project()) -- lets you model "what if I
            inherit an IRA in 2032" without already holding the account.
            decedent_death_date IS the hypothetical inheritance year here
            -- give your assumed decedent's real birthdate but whatever
            death date matches the year you want to model.
        Used to apply the SECURE Act 10-year rule -- see pt/rmd.py for
        what that computes and which assumptions it makes (non-spouse,
        non-EDB beneficiary). A real, already-held inherited account not
        listed here is folded into the taxable bucket instead (see
        pt/projection.py), which is a worse approximation than including
        it properly.

        Each entry may also have an optional planned_withdrawal: {start_date,
        end_date, annual_amount}. Within that date range (by year), the
        account withdraws the *greater of* annual_amount and that year's
        legally-required RMD -- a floor, not a replacement, since the RMD
        minimum always applies regardless. Outside the range (or with no
        planned_withdrawal at all), the RMD alone applies as usual. Use this
        to model deliberately front-loading withdrawals from an inherited
        account faster than required (e.g. to smooth taxable income instead
        of taking one large distribution in the final year).
    roth_conversions (optional, a list) -- one entry per planned Roth
        conversion window, each: owner (matches a people.primary/spouse.name
        from the profile -- the conversion moves money from that person's
        OWN Traditional accounts to their OWN Roth bucket, never a
        spouse's), start_date, end_date (both YYYY-MM-DD -- only the year
        matters, matching planned_withdrawal's convention), and EITHER a
        flat annual_amount (in today's dollars, not inflated) OR a
        constraints section (or both -- constraints, if present, always
        cap whatever annual_amount/max_conversion_per_year requests).
        annual_amount can also be a LIST of one dollar amount per year in
        the window instead of a single flat number -- mainly meant for
        roth_optimizer/ (a companion tool that searches for a good
        per-year schedule instead of a single flat amount) to hand
        project() its result, though nothing stops a household from
        writing one by hand. An entry may also carry an optional
        scatter_chart (a path, relative to this scenario file's own
        directory, to the HTML chart `roth_optimizer optimize
        --save-iterations` wrote) -- ignored by project(); `pt.cli
        report` reads it to add an "Optimizer Search" tab (see
        pt/report.py's _add_optimizer_search_sheet()). Each
        year in the window, the resulting amount converts -- see
        pt/projection.py's build_roth_conversions()/module docstring for
        the full mechanics (processed after that year's RMD, taxed as
        ordinary income, generates no spendable cash). Draws from the
        owner's IRA(s) first, then their 401(k) if the IRA balance isn't
        enough to cover that year's amount (see ROTH_CONVERSION_SOURCE_TYPES
        in pt/projection.py).

        constraints (optional, all individually optional -- at least one of
        annual_amount / constraints.max_conversion_per_year is required so
        there's some target to convert toward; whichever constraints are
        set, the SMALLEST of their resulting caps wins each year -- this is
        a greedy per-year heuristic, not a multi-year optimizer, see
        pt/projection.py's module docstring):
            max_conversion_per_year -- a flat dollar cap, in today's dollars
            max_marginal_bracket -- caps the conversion so its marginal
                federal rate doesn't exceed a named bracket -- a number
                matching one of this year's ordinary bracket rates (e.g.
                24), or the same as a percent string ("24%")
            avoid_irmaa_tier -- caps the conversion so this year's MAGI
                doesn't spill into the next Medicare IRMAA tier up -- an
                integer 0 (the standard premium) through 5 (the top
                surcharge), see pt/medicare.py. A bare avoid_irmaa: true
                is also accepted as shorthand for avoid_irmaa_tier: 0.
            preserve_cash_reserve -- caps the conversion so its OWN
                estimated tax cost doesn't draw the Taxable bucket below a
                reserve target -- either a number (years of spending to
                preserve, self-contained) or true (uses the top-level
                cash_reserve.years policy above -- an error if that isn't
                set)
    ira_distributions (optional, a list) -- structurally identical to
        roth_conversions (same fields, same constraints -- but the flat
        per-year dollar cap is spelled constraints.max_distribution_per_year
        instead of constraints.max_conversion_per_year) -- see
        pt/projection.py's build_ira_distributions()/module docstring.
        The difference is where the money ends up: a Roth conversion moves
        Traditional money to Roth (no spendable cash, just changes tax
        treatment); an IRA distribution moves it to the TAXABLE bucket
        instead -- a plain Traditional withdrawal (ordinary income, same
        as an RMD), taken before it's needed for spending, e.g. to use up
        room in a lower tax bracket while it's available.
    rollovers (optional, a list) -- one entry per ONE-TIME 401(k)-to-IRA
        rollover (the only direction modeled -- see
        pt/projection.py's build_rollovers()), each: owner (matches a
        people.primary/spouse.name -- moves that person's OWN 401(k) to
        their OWN IRA), date (YYYY-MM-DD, only the year matters), and
        EITHER pct (0-100, percent of that owner's 401(k) balance, after
        that year's growth) OR amount (a flat dollar cap, in today's
        dollars) -- defaults to pct: 100 if neither is given. NOT a
        taxable event.
    taxable_cash_flows (optional, a list) -- one entry per hypothetical
        one-time cash event applied DIRECTLY to the Taxable bucket, each:
        date (YYYY-MM-DD, only the year matters), type ("deposit" or
        "withdrawal"), amount (positive, in today's dollars, not
        inflated). A deposit adds new principal (e.g. an inheritance,
        home-sale proceeds already accounted for elsewhere); a withdrawal
        removes it (e.g. a one-time large purchase), floored at whatever
        the Taxable balance actually holds that year. Deliberately NOT run
        through the tax engine at all -- see
        pt/projection.py's build_cash_flows() for why.
    mortality (optional) -- independently optional primary_death_date and
        spouse_death_date (YYYY-MM-DD, only the year matters;
        spouse_death_date is an error without a people.spouse in the
        profile). See pt/projection.py's build_mortality()/module
        docstring for the full mechanics, but briefly: a person is
        treated as alive THROUGH their own death year (matching real tax
        rules), and starting the year after, their Traditional/Roth
        balances merge into their surviving spouse's own pools (a
        spousal rollover), the survivor's Social Security becomes the
        LARGER of the two people's own benefits (a simplified survivor
        benefit), that person no longer counts for Medicare, and the
        survivor's federal filing status switches from MFJ to Single
        (with the correspondingly smaller Single-filer brackets/standard
        deduction -- see pt/tax.py). If EVERY person in the household
        ends up with a configured death date, the projection stops after
        processing the LATER of them and reports the estate's value (the
        household's total balance as of then) instead of running through
        age 100.
    retirement_contributions (optional, a list) -- one entry per person
        still contributing to their own 401(k), each: owner (matches a
        people.primary/spouse.name), an optional roth_catchup_start_date
        (YYYY-MM-DD, only the year matters), and EITHER amount OR
        pct_of_max (at most one -- omit both to assume the legal max, the
        default). Every year from the start of the projection through the
        year BEFORE that owner's own retirement (their own retirement_age/
        spouse_retirement_age, not the household's combined later one --
        see pt/projection.py's _own_retirement_years()):
          - the employee's own contribution for the year is the legal max
            (elective deferral limit + whatever catch-up applies at that
            year's age -- see pt/contributions.py for the actual 2025
            IRS limits, including SECURE 2.0's enhanced catch-up for ages
            60-63, and how they're inflated forward) by default; or, if
            amount is set, that flat dollar figure (today's dollars,
            inflated forward like a wage, unlike a one-off event's dollar
            amount elsewhere in this file); or, if pct_of_max is set,
            that percentage of the year's legal max
          - of that total, the portion up to the elective deferral limit
            is "regular" and always goes to the Traditional pool;
            anything from there up to the legal max is "catch-up" BY
            DEFINITION (the real IRS rule -- not about maxing out, just
            whatever exceeds the base limit), and also goes to
            Traditional UNLESS roth_catchup_start_date is set and this
            year is on or after it, in which case SECURE 2.0's mandatory
            Roth catch-up rule applies (a high earner's catch-up must be
            designated Roth) and it goes to the Roth pool instead --
            omit roth_catchup_start_date entirely for someone never
            subject to this (e.g. their wages stay under the threshold)
          - anything ABOVE the legal max (from an amount/pct_of_max
            configured higher than it) is treated as an after-tax
            contribution immediately converted to Roth -- the "mega
            backdoor Roth" strategy, tracked separately in its own
            after_tax_roth_contribution/"401(k) After-Tax to Roth" column
            since it's a different IRS provision from the SECURE 2.0
            catch-up above, even though both land in the same Roth pool.
            This assumes the conversion happens with minimal growth
            first (so it's effectively tax-free) -- an after-tax
            contribution that instead stays in the 401(k) long-term
            (where its future growth would be taxed as ordinary income)
            isn't modeled.
        Not a taxable event either way -- this tool doesn't model wage
        income/payroll tax at all, so there's nothing to reduce for the
        pre-tax portion or reconcile for the already-taxed Roth portion.

        employer_match (optional) -- EITHER amount (a flat dollar figure)
        OR BOTH pct_of_salary (a percentage, e.g. 4 means 4%) and salary
        must be given. ALWAYS lands in the Traditional pool (the standard
        real-world treatment, even for an owner whose own catch-up is
        Roth), shown separately in its own column. Unlike a one-off
        event's dollar amount elsewhere in this file, amount/salary here
        ARE treated as recurring, wage-like figures and inflate forward
        with inflation.general, the same convention as
        income.target_spending and social_security.monthly_benefit. No
        IRS combined employee+employer "annual additions limit" is
        checked -- see pt/projection.py's "Documented simplifications".
    health_insurance (optional) -- a dict keyed by a
        people.primary/spouse.name (same shape as social_security), each
        an estimated <name>.monthly_amount (today's dollars) for a
        private health insurance plan covering that person's own gap
        between retiring and Medicare eligibility (age 65) -- a real cost
        for anyone who retires before 65. Applies every year from that
        PERSON'S OWN retirement (their own retirement_age/
        spouse_retirement_age, not the household's combined later
        household_retirement_year -- see pt/projection.py's
        _own_retirement_years()) through the year before they turn 65,
        inflated forward like a wage (the same convention as
        retirement_contributions.amount/employer_match, not a one-off
        event elsewhere in this file). Shown in the projection table's
        Health Ins. column and included in Cumulative Healthcare Expense
        alongside Medicare Part B/Part D IRMAA -- see
        pt/projection.py's build_health_insurance()/module docstring.
        Someone who retires at or after 65 has a zero-length window, so
        configuring this for them has no effect.
    pre_retirement_income (optional) -- a dict keyed by a
        people.primary/spouse.name (same shape as health_insurance), each
        an estimated <name>.estimated_gross_income -- that person's GROSS
        W-2 income (Box 1, "Wages, tips, other compensation"), NOT total
        AGI (which can double-count investment income this tool already
        tracks separately via the taxable bucket) and NOT gross pay
        before payroll deductions (Box 1 already excludes pre-tax
        401(k)/HSA/etc. contributions, which is correct -- those aren't
        taxable). Either a single flat number (today's dollars, inflated
        forward like a wage) or a per-year list (one already-nominal
        dollar amount per year, no further inflation -- same convention
        as roth_conversions' own annual_amount lists, for a real,
        year-specific trajectory a flat growth rate can't represent, e.g.
        a one-time bonus year; requires an explicit start_date to anchor
        its first entry). Applies every year through the year BEFORE that
        person's own retirement (same window convention as
        health_insurance/retirement_contributions). Matters for a
        roth_conversions or ira_distributions entry whose start_date is
        before that owner's own retirement (nothing stops that) --
        WITHOUT this section, such an entry's max_marginal_bracket/
        avoid_irmaa_tier constraints would be evaluated (and its tax
        computed) as if a still-working person had zero other income,
        letting them approve a much bigger conversion than the
        household's real tax situation allows, AND understating what it
        would really cost in tax. This section does NOT add wage income
        (or any tax attributable to wage income alone) to the household's
        own cash flow anywhere -- this tool has never modeled wage
        income/payroll tax (see pt/projection.py's "Documented
        simplifications") and this doesn't change that -- see
        pt/projection.py's build_pre_retirement_income()/
        _household_tax_owed() for the full detail.
    advisory_expense (optional) -- a single global advisory/AUM fee for
        the whole portfolio: advisory_expense.pct_of_portfolio, a
        percentage (e.g. 1.0 means 1.00%/year, not a 0-1 fraction).
        Applied every year against that year's total portfolio value,
        the same "one flat rate, applied uniformly" convention as the
        blended growth rate itself (see returns.asset_classes/returns.
        growth above) -- see pt/projection.py's
        _parse_advisory_expense_rate()/module docstring. This is
        deliberately the ONLY advisory expense a projection models --
        it is NOT derived from, or reconciled against, any real
        account's own expense ratio set via `accounts
        set-expense-ratio` (pt.cli), which stays informational-only,
        reported by `pt.cli report`'s Investment Expense Summary
        instead. Shown in the projection table's Advisory Exp. column.
        Omit this section entirely for a household with no advisory
        fee to model (defaults to $0).
    liabilities (optional, a list) -- a recurring, FIXED-dollar debt
        payment (a mortgage, car loan, or similar). Unlike
        income.target_spending, the payment is a flat NOMINAL dollar
        figure that does NOT inflate forward (a fixed-rate loan payment
        doesn't grow with inflation); unlike health_insurance/
        retirement_contributions, its window is a household-level
        start_date/end_date (by year), not tied to either person's own
        retirement. Each entry needs a start_date, an optional name/type
        (display only), and EITHER a flat monthly_payment/annual_payment
        (end_date then required) OR principal + interest_rate (a
        percentage) + term_years, which computes the standard amortized
        payment itself (end_date then optional, defaulting to a
        term_years-long window). Only affects cash flow -- added to
        cash_need alongside spending/health insurance/the advisory
        expense, drawn via the same withdrawal order -- no amortization/
        interest tracking, no mortgage-interest tax deduction, and no
        separate liability-balance ledger reducing net worth; a down
        payment or other one-time cost at origination is a
        taxable_cash_flows entry instead. Shown in the projection
        table's Liability Pmt. column. See pt/projection.py's
        build_liabilities().
    hypothetical_accounts (optional, a list) -- run `pt.cli project`/
        `monte-carlo` against a made-up household instead of a real
        imported one: no CSV import, no database snapshot, no ticker
        classification. Each entry: account_number (any unique string),
        account_type (one of pt/importer.py's ACCOUNT_TYPES), owner
        (matches a people.primary/spouse.name), value (current balance),
        pct_growth (0-100 -- % of that account treated as growth-like/
        equity for the blended growth rate, same purpose a real account's
        classified holdings serve). See pt/projection.py's
        build_hypothetical_accounts(). Present, this REPLACES the
        database entirely for that run (--db/--snapshot are ignored).
    people (optional, only meaningful alongside hypothetical_accounts) --
        the SAME shape as pt/planning.py's profile file (people.primary/
        spouse.name/birthdate). Lets a scenario that also configures
        hypothetical_accounts skip needing a separate profile file at
        all -- an explicit --profile always overrides this, and this key
        is ignored entirely for a scenario with no hypothetical_accounts
        (real usage keeps loading the actual profile file, unaffected --
        see pt.cli's _load_profile_for()).
    _include (optional, a path string or a list of them) -- merges one or
        more other YAML files in UNDER this file, so several scenarios
        that share a block (typically returns.asset_classes, the most
        commonly duplicated section) can point at one shared file instead
        of copy-pasting it into every scenario. A relative path resolves
        against the INCLUDING file's own directory, not the current
        working directory, so an included file can itself live anywhere
        (a shared library directory, say) regardless of where each
        scenario that uses it lives; `~` is expanded. Merging is a
        recursive dict merge -- a shared file's returns.asset_classes can
        provide ten classes and this file can override just one of them
        (or add an eleventh) without repeating the other nine -- but a
        list (roth_conversions, hypothetical_accounts, ...) or a scalar is
        simply replaced wholesale if both this file and an include define
        it, never merged element-by-element. Multiple entries in a list
        merge left to right (later ones win on conflicts), and this
        file's own keys always win over anything it includes, no matter
        the order they appear in the file. Included files can themselves
        use _include (resolved the same way, against THEIR OWN directory)
        -- an include cycle (directly or through a longer chain) raises
        ScenarioError rather than recursing forever. See
        `_load_scenario_file()` for the implementation.
"""
from pathlib import Path

import yaml

DEFAULT_SCENARIO_PATH = Path.home() / ".portfolio_tool" / "retirement_scenario.yaml"


class ScenarioError(Exception):
    pass


def _deep_merge(base: dict, override: dict) -> dict:
    """Returns a NEW dict with `override` merged on top of `base` --
    recursively, but only where a key's value is a dict on BOTH sides (so
    overriding one entry of returns.asset_classes doesn't discard the
    rest); anything else (a list, a scalar, or a dict on only one side) is
    simply replaced wholesale by override's value, never merged
    element-by-element. Neither input is mutated."""
    merged = dict(base)
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _load_scenario_file(path: Path, _ancestors: frozenset = frozenset()) -> dict:
    """Loads one scenario YAML file and resolves its own `_include` (see
    load_scenario()'s docstring for the full merge rules), recursively --
    an included file can have its own `_include`. `_ancestors` is the set
    of resolved paths currently being loaded along THIS include chain (not
    every file loaded anywhere), used only to detect a cycle -- the same
    file legitimately included from two different unrelated branches
    (a "diamond") is not a cycle and is simply loaded twice."""
    path = Path(path).resolve()
    if not path.exists():
        raise ScenarioError(
            f"No retirement scenario found at {path}. Create one there as YAML with "
            "top-level sections: retirement, income, inflation, returns, "
            "social_security -- see README.md 'Retirement scenario', or pass "
            "--scenario to point at a different file."
        )
    if path in _ancestors:
        chain = " -> ".join(str(p) for p in (*_ancestors, path))
        raise ScenarioError(f"_include cycle detected: {chain}")
    with open(path, encoding="utf-8") as f:
        try:
            data = yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise ScenarioError(f"Couldn't parse {path} as YAML: {e}")
    if not isinstance(data, dict):
        raise ScenarioError(f"{path} doesn't look like a valid scenario (expected a YAML mapping).")

    include = data.pop("_include", None)
    if include is None:
        return data

    if isinstance(include, str):
        include_paths = [include]
    elif isinstance(include, list) and all(isinstance(p, str) for p in include):
        include_paths = include
    else:
        raise ScenarioError(f"{path}: _include must be a path string or a list of path strings.")

    ancestors_here = _ancestors | {path}
    merged = {}
    for raw in include_paths:
        expanded = Path(raw).expanduser()
        include_path = expanded if expanded.is_absolute() else (path.parent / expanded)
        included = _load_scenario_file(include_path, ancestors_here)
        merged = _deep_merge(merged, included)
    return _deep_merge(merged, data)


def load_scenario(path: Path = DEFAULT_SCENARIO_PATH) -> dict:
    """Loads and parses a retirement scenario, resolving any `_include`
    (see this module's docstring for the merge rules). Raises
    ScenarioError with a clear message if the file (or one it includes)
    doesn't exist, fails to parse, isn't a YAML mapping, or an include
    cycle is detected."""
    return _load_scenario_file(Path(path))
