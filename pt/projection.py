"""Year-by-year retirement cash-flow projection: portfolio growth, Social
Security, RMDs (including inherited-account 10-year-rule schedules),
federal taxes, and a withdrawal strategy -- from today through the primary
person's age-100 year.

This models the household's balances in tax-treatment buckets, not one lump
sum:
    taxable                    -- Brokerage accounts (any owner)
    traditional[name][type]    -- Traditional IRA/401(k), pooled per person
                                  AND per account type -- e.g. all of one
                                  person's IRAs combine, separately from all
                                  their 401(k)s. RMDs are computed per pool
                                  (and summed -- same total as computing on
                                  the person's combined balance, since the
                                  RMD divisor only depends on age). A Roth
                                  conversion draws from all of that person's
                                  Traditional pools (IRA and 401(k)) -- see
                                  ROTH_CONVERSION_SOURCE_TYPES.
    roth[name]                 -- Roth IRA, one bucket per person (no
                                  lifetime RMD applies to either person's
                                  own Roth, but a conversion has to land in
                                  the same person's own Roth, so it's
                                  tracked separately too, not combined)
    inherited (list)     -- each inherited IRA/Roth IRA, tracked separately
                            with its own SECURE Act 10-year-rule schedule
                            (see pt/rmd.py)

Withdrawal strategy (per the household's own direction): each inherited
account distributes the *greater of* its legally-required 10-year-rule
amount (annual stretch RMD where required, full depletion in year 10 --
see pt/rmd.py) and an optional planned_withdrawal floor the household can
set per account for a date range (see _planned_withdrawal_floor()) -- e.g.
"withdraw at least $50k/year from this account from 2025 through 2030,"
which only matters in years it exceeds the RMD; outside that account's
configured date range (or with none configured at all), the RMD alone
applies, unchanged from before. This happens regardless of spending need;
any distributed amount not needed for spending is reinvested into the
taxable bucket. Everything else follows this order once forced
distributions and Social Security don't cover a year's spending (or tax
bill): **taxable, then Traditional (own), then Roth (own), last.**

Roth conversions (optional, scenario.roth_conversions -- see
build_roth_conversions()): each year, after that year's RMD is satisfied
(conversions can't happen until the RMD is, per IRS rules -- this falls out
naturally since conversions are processed after the RMD step below), an
owner with an active conversion window moves min(annual_amount, their
remaining Traditional balance) from their own Traditional pools (IRA and
401(k) -- see ROTH_CONVERSION_SOURCE_TYPES) to their own Roth bucket. This
is a taxable event (added to that year's ordinary income, exactly like a
Traditional withdrawal) but generates no spendable cash -- the money doesn't
leave the household, it just changes tax treatment. The resulting tax bill
is paid the same way any
other tax bill is (taxable, then Traditional, then Roth), which naturally
tends to draw from the taxable bucket first -- the standard advice to pay
conversion tax from outside the IRA falls out of the existing withdrawal
order rather than needing special-casing.

Roth conversion constraints (optional, scenario.roth_conversions[].
constraints -- see build_roth_conversions()): instead of (or in addition
to) a flat annual_amount, a conversion can name limits that get
re-evaluated every year and cap whatever amount was requested that year:
max_marginal_bracket (don't let the marginal federal rate on the
conversion exceed a named bracket -- "fill the bracket" style),
avoid_irmaa_tier (don't let this year's MAGI spill into the next Medicare
IRMAA tier up -- see below), max_conversion_per_year (a flat dollar cap),
and preserve_cash_reserve (don't let the conversion's own estimated tax
cost draw the Taxable bucket below the scenario's cash_reserve policy).
Whichever constraints are set, the SMALLEST of their resulting caps wins.
This is a greedy, year-by-year heuristic (convert as much as this year's
constraints allow), not a true multi-year optimizer that would look ahead
to find the schedule maximizing total estate value -- see the module's
"Documented simplifications" below.

Medicare Part B premium + Part D IRMAA surcharge (pt/medicare.py):
computed unconditionally for anyone MEDICARE_ELIGIBILITY_AGE (65) or
older, regardless of whether any roth_conversions are configured -- a
real cost every Medicare-eligible household faces. Uses the actual 2-year
income lookback (this year's premium is set by MAGI from 2 years ago,
which this projection already computed and recorded), inflated using the
scenario's medical inflation rate if set (scenario.inflation.medical,
falling back to general inflation otherwise). Modeled as a required cash
outflow alongside spending, covered by the same forced-cash/Social-
Security-first, then-withdrawal-order logic -- not as a reduction to
Social Security's gross benefit for tax purposes (only for how much cash
actually reaches the household), matching how Part B premiums are
typically withheld directly from a Social Security check in real life.

IRA distributions (optional, scenario.ira_distributions -- see
build_ira_distributions()): same mechanics and constraints as a Roth
conversion above, processed right after (sharing the same running
ordinary-income total, so a distribution correctly sees any conversion
income already added this year) -- but the money lands in the TAXABLE
bucket, not Roth. A plain Traditional withdrawal (ordinary income, same
as an RMD), just taken voluntarily ahead of when it's needed for
spending -- e.g. to use up room in a lower tax bracket while it's
available. Shown in the table's **IRA Distribution** column.

Rollovers (optional, scenario.rollovers -- see build_rollovers()): a
ONE-TIME event (not a window, unlike conversions/distributions) that
moves money from an owner's 401(k) pool to their IRA pool, processed
right after that year's growth (step 1.5, before RMDs) -- either a
percentage of the 401(k)'s balance or a flat amount. NOT a taxable event
(a direct 401(k)-to-Traditional-IRA rollover doesn't trigger tax, unlike
a Roth conversion) -- and doesn't change that year's total RMD either
(the Uniform Lifetime divisor only depends on age, so which pool the
money sits in doesn't matter for the total, only for which --verbose
column shows the balance). Shown in the table's **Rollover** column.

Taxable cash flows (optional, scenario.taxable_cash_flows -- see
build_cash_flows()): a hypothetical one-time deposit or withdrawal
applied DIRECTLY to the Taxable bucket, before that year's growth (step
0) -- a deposit adds new principal (e.g. an inheritance, home-sale
proceeds already accounted for elsewhere), a withdrawal removes it (e.g.
a one-time large purchase), floored at $0. Deliberately NOT run through
the tax engine at all, per the household's own direction -- no
capital-gains tax on a withdrawal, no cost basis added on a deposit.

Hypothetical inherited accounts (optional -- see
build_inherited_schedules()'s hypothetical_value): a scenario.
inherited_accounts entry doesn't have to be a real, already-held account
-- one can instead describe a FUTURE inheritance not yet held at all,
which "appears" in the simulation (step 0, before that year's growth) at
its configured decedent_death_date, starting from its configured
hypothetical_value, then follows the exact same SECURE Act 10-year-rule
schedule as a real inherited account from that point on.

Mortality (optional, scenario.mortality -- see build_mortality()):
independently optional primary_death_date and spouse_death_date. A
person is treated as alive THROUGH their own death year -- matching real
tax rules (MFJ still applies for the year of death itself; that year's
RMD is still required) -- and everything death triggers starts the
FOLLOWING year:
  - their Traditional/Roth pools merge into their surviving spouse's own
    pools (step 0, before growth) -- a real-world spousal rollover,
    which (unlike a non-spouse inheritance) isn't subject to the SECURE
    Act 10-year rule, so no separate schedule is needed; it just becomes
    the survivor's own money
  - the survivor's Social Security becomes the LARGER of the two
    people's own eligible benefits (a simplified survivor benefit -- see
    "Documented simplifications" for what this doesn't model)
  - that person no longer counts toward Medicare eligibility
  - the survivor's federal filing status switches from MFJ to Single --
    see pt/tax.py's Single brackets/standard deduction, meaningfully
    smaller than MFJ's, so a survivor's tax can jump noticeably even at
    the same income
If EVERY person in the household ends up with a configured death date
(both, for a two-person household; the one, for a single-person one),
the projection stops once the LATER of them has been processed, and
format_table()'s summary line reports the estate's value (that final
row's total_balance) instead of the usual "ran through age 100" message.

401(k) contributions (optional, scenario.retirement_contributions -- see
build_retirement_contributions()/pt/contributions.py): for each
configured owner, every year from the start of the projection through
the year BEFORE that owner's own retirement (see _own_retirement_years()
-- each person's OWN retirement age, not household_retirement_year,
which is the LATER of the two), step 0 adds new money from outside the
model (this year's paycheck), the same as a taxable_cash_flows deposit,
not drawn from any other bucket. The total requested is the legal max
(elective deferral limit + whatever catch-up applies at that year's age)
by default, or a configured amount/pct_of_max instead -- NOT capped at
the legal max: of the requested total, the portion up to the elective
deferral limit is "regular" and always lands in the Traditional 401(k)
pool; the next slice, up to the legal max, is "catch-up" BY DEFINITION
(the real IRS rule -- not about maxing out, just whatever exceeds the
base limit), and also lands in Traditional UNLESS the owner's
roth_catchup_start_year has arrived, in which case SECURE 2.0's mandatory
Roth catch-up rule sends it to the Roth pool instead; anything left over
ABOVE the legal max is modeled as an after-tax contribution immediately
converted to Roth (the "mega backdoor Roth" strategy -- assumes minimal
growth before conversion, so it's effectively tax-free; an after-tax
contribution that instead stays in the 401(k) long-term, where its
growth would be taxed as ordinary income, isn't modeled), tracked
separately from the SECURE 2.0 catch-up since it's a different IRS
provision even though both land in the same Roth pool. None of this
triggers a tax event here -- this tool doesn't model wage income/payroll
tax at all (see "Documented simplifications"), so there's no income to
reduce for the pre-tax portion and no already-taxed income to reconcile
for either Roth portion. Shown in the table's **401k Trad.**, **401k
Roth**, and **AT->Roth** columns.

Employer match (optional, part of the same retirement_contributions
entry): computed alongside the employee's own contribution above, from
EITHER a flat employer_match.amount OR employer_match.pct_of_salary times
employer_match.salary. ALWAYS lands in the Traditional pool -- the
standard real-world treatment, even in a year where the owner's own
catch-up goes to Roth -- and is shown separately in its own **Er Match**
column rather than folded into 401k Trad., so it's visible on its own.
Unlike a one-off event's dollar amount elsewhere in this scenario file,
amount/salary here ARE treated as recurring, wage-like figures and
inflate forward with general inflation, the same convention as
income.target_spending and social_security.monthly_benefit.

Private health insurance (optional, scenario.health_insurance -- see
build_health_insurance()): a dict keyed by person name (same shape as
social_security), each entry's monthly_amount is an estimated cost for
the private plan that person needs between retiring and Medicare
eligibility -- a real gap for anyone who retires before
MEDICARE_ELIGIBILITY_AGE (65). Applied every year from that person's OWN
retirement (not household_retirement_year, the LATER of the two -- see
_own_retirement_years()) through the year before they turn 65, inflated
forward like a wage (the same convention as retirement_contributions.
amount/employer_match, not a one-off event). Modeled as a required cash
outflow alongside spending and Medicare, covered by the same
forced-cash/Social-Security-first, then-withdrawal-order logic. Shown in
the table's **Health Ins.** column.

Cumulative healthcare expense: a running total of Medicare Part B + Part
D IRMAA + private health insurance (the only healthcare costs this tool
tracks) from the start of the projection -- unlike
cumulative_tax_in_retirement, NOT gated on retired, since Medicare
eligibility can arrive before retirement (and private health insurance,
by definition, only ever applies once a person has personally retired,
but that can be before the household's own combined retirement date --
see above). Shown in the table's **Health Exp.** column.

Advisory expense (optional, scenario.advisory_expense.pct_of_portfolio
-- see _parse_advisory_expense_rate()): a SINGLE global advisory/AUM fee
for the whole portfolio, set directly in the scenario file -- NOT
derived from any real account's own expense_ratio in the database
(`accounts set-expense-ratio`); that per-account figure, and a symbol's
own per-symbol expense ratio, both remain informational-only, reported
by pt.cli report (see pt/allocation.py's expense_summary()) but never
modeled in a projection. This is the one and only advisory-expense
number project() ever uses. Applied every year against that year's
total portfolio value (buckets + inherited) as this one fixed rate --
the same "one flat rate, applied uniformly" simplification this tool
already uses for growth (see compute_blended_growth_rate()), just
supplied directly rather than blended from account data. NOT gated on
retirement status (an AUM fee is owed regardless). Modeled as a
required cash outflow alongside spending, Medicare, and private health
insurance, covered by the same forced-cash/Social-Security-first, then-
withdrawal-order logic (taxable, then Traditional, then Roth). Shown in
the table's **Advisory Exp.** column. Defaults to 0.0 (no advisory
expense modeled) if scenario.advisory_expense isn't set -- distinct
from Cumulative Healthcare Expense, which this is NOT folded into.

Each row also reports a running total of tax paid *while retired* --
cumulative_tax_in_retirement sums federal_tax() only for years where
retired is True, so it excludes any tax from forced distributions (e.g. an
inherited account's RMD) that land before the household's own retirement
date; it resets to (i.e. starts accumulating from) 0 at the first retired
year, not from the top of the projection.

Asset-class return/volatility assumptions (scenario.returns.asset_classes,
optional -- see resolve_asset_class_returns()): each macro or subclass the
household holds (classifier.ASSET_CLASS_HIERARCHY -- e.g. "US Equity",
"International Equity", "US Bonds", "Liquidity") can get its OWN expected
return and volatility, instead of one blended growth/fixed_income pair.
compute_blended_growth_rate()/compute_blended_volatility() weight each
held class's own numbers by its actual current dollar allocation
(allocation.allocation_by_asset_class()) into the single blended_rate/
volatility every deterministic project() call still uses. A scenario with
no returns.asset_classes section falls back to the original returns.
growth/.fixed_income 2-bucket shape entirely unchanged (see
_legacy_returns_translation()) -- every scenario file written before this
existed keeps producing identical output.

Monte Carlo variant (run_monte_carlo(), see pt.cli monte-carlo): the plain
project() call above holds blended_rate fixed every single year -- the
same average return, in the same order, every time. run_monte_carlo()
instead runs project() many times (`trials`), letting each YEAR of each
trial draw its own growth rate independently -- not from one blended
Normal(blended_rate, volatility) draw for the whole portfolio, but from
build_correlated_return_sampler(): every asset class the household holds
draws its OWN correlated random return that year (see
_resolve_correlation()), summed by weight into that year's realized
portfolio return. This captures sequence-of-returns risk (a bad year
early in retirement hurts far more than the same bad year late, even
though the average return across the whole projection is identical) AND
real diversification (classes that don't move in lockstep partially
cancel out -- the gap compute_blended_volatility()'s own docstring flags
as understated by a simple weighted-average volatility), which a single
deterministic run, or the old single-blended-draw Monte Carlo, structurally
can't show. Every other mechanic in project() -- RMDs, taxes, Roth
conversions, Social Security, Medicare, mortality, all of it -- runs
completely unchanged for each trial; only the realized growth rate varies.
Reports a success rate (the fraction of trials that never hit a
shortfall) and a percentile "fan chart" of total_balance by year (10th/
50th/90th percentile across all trials that reached that year), instead
of format_table()'s single deterministic row per year -- see
format_monte_carlo_table().

Hypothetical accounts (optional, scenario.hypothetical_accounts -- see
build_hypothetical_accounts()): lets project()/run_monte_carlo() run
against a made-up household -- no CSV import, no database snapshot, no
ticker classification -- so someone can try the tool immediately. Each
entry is a single account_number/account_type/owner/value plus EITHER one
pct_growth number (what fraction of that account's value counts as
growth-like/equity -- the original, coarser option) OR an asset_classes
map (per-class percentages, same names as scenario.returns.asset_classes,
for finer control) -- either feeds compute_blended_growth_rate()/
compute_blended_volatility()/build_correlated_return_sampler() the same
way a real account's classified holdings would. pt.cli project/monte-
carlo use this INSTEAD of the database entirely whenever a scenario
configures it -- real and hypothetical accounts are never combined in one
run. A scenario that also configures hypothetical_accounts can
additionally embed its own top-level `people` section (same primary/
spouse.name/birthdate shape as pt/planning.py's profile file -- see
pt.cli's _load_profile_for()) so a single self-contained scenario file
needs no separate profile file at all; an explicit --profile always
overrides this, and a scenario with no hypothetical_accounts ignores an
embedded `people` section entirely (real usage keeps loading the actual
profile file, unaffected).

Documented simplifications:
- One blended growth rate for the whole projection (see
  compute_blended_growth_rate), applied uniformly to every bucket, even
  though it's now a weighted blend of every held asset class's own rate
  rather than a 2-bucket blend. No rebalancing, glide path, or per-bucket
  allocation drift -- e.g. "the Roth is 100% equities, the Traditional
  IRA is bonds" isn't modeled; the same household-wide composition
  applies to every bucket.
- Monte Carlo (run_monte_carlo(), see build_correlated_return_sampler()):
  each year's per-class returns are drawn from a joint Normal distribution
  (correlated across classes, per _resolve_correlation()'s default macro-
  level correlation table or the scenario's own returns.correlations
  override) -- no autocorrelation/mean-reversion between consecutive
  years (some real research suggests equity returns mean-revert somewhat
  over long horizons; this doesn't model that) and no fat tails (a Normal
  distribution understates the real-world frequency of extreme years in
  either direction). The default correlation table itself is a rough,
  historically-informed approximation, not tailored to any specific fund
  lineup -- fully overridable. Every bucket still gets the SAME realized
  portfolio rate in a given year/trial (no per-bucket allocation drift,
  same simplification as the deterministic rate above).
- Hypothetical accounts (scenario.hypothetical_accounts, see
  build_hypothetical_accounts()) get one pct_growth number OR one
  asset_classes map per account, not real per-symbol holdings -- coarser
  than a real classified portfolio even with asset_classes, meant for a
  quick trial, not precise allocation modeling. An "Inherited IRA"/
  "Inherited Roth IRA" account_type here is just a Traditional/Roth
  balance with no RMD/10-year-rule schedule -- it doesn't automatically
  link up with scenario.inherited_accounts (which is where that schedule
  comes from for a REAL inherited account); the two features don't
  currently compose.
- Federal tax only (see pt/tax.py) -- no state tax, matching this
  household's own choice. MFJ filing status, standard deduction only.
  Taxable-brokerage withdrawals are treated as 100% long-term capital gain
  (cost basis isn't tracked/excluded).
- A year's tax bill is computed from that year's spending-driven
  withdrawals, then paid via one additional withdrawal in the same
  conventional order -- the small additional tax on *that* withdrawal
  itself isn't re-computed (avoids solving a circular equation; the
  omitted amount is typically small relative to the whole tax bill).
- HSA accounts aren't given special tax treatment yet (would need pre-tax
  contribution / tax-free-if-medical modeling) -- if present, they're
  folded into the taxable bucket, which understates their tax advantage.
- Social Security benefit amounts inflate at the scenario's general
  inflation rate (a reasonable proxy for COLA, not an exact match to the
  actual CPI-W-based COLA formula).
- No survivor spending reduction; life_expectancy values are informational
  only. The table always runs through the primary's age-100 year.
- Roth conversions don't model the 5-year conversion-seasoning rule (each
  conversion needs 5 years before it can be withdrawn penalty-free if the
  owner is under 59 1/2) -- not a concern for a household already past that
  age by the time conversions typically start, but worth knowing if you
  configure an early conversion for a younger owner.
- Roth conversions draw from all of a person's Traditional pools -- IRA and
  401(k) alike (see ROTH_CONVERSION_SOURCE_TYPES) -- per this household's
  own direction. If a conversion's annual_amount exceeds everything
  available across those pools, the excess is simply not converted that
  year.
- Inherited-account handling assumes a non-eligible-designated-beneficiary
  under the SECURE Act 10-year rule (see pt/rmd.py) -- not the
  alternative life-expectancy stretch available to a spouse or other EDB.
- Constraints (max_marginal_bracket, avoid_irmaa_tier, preserve_cash_reserve
  -- shared by both Roth conversions and IRA distributions) are a GREEDY
  per-year heuristic, not a true multi-year optimizer -- each year
  converts/distributes as much as that year's constraints allow, without
  looking ahead to whether more (or less) this year would produce a
  better outcome (lower lifetime tax, or a larger total estate) over the
  rest of the projection.
- Those same constraints estimate this year's ordinary income/MAGI using
  only what's known at the time a conversion/distribution is decided
  (RMDs plus any other owner's conversion/distribution already processed
  this year, plus an estimated taxable-Social-Security amount) -- that
  year's eventual capital-gains income from a taxable-brokerage
  withdrawal (determined afterward, when spending is covered) isn't
  included, which can make a constrained amount slightly over- or
  under-shoot its target in a high-spending year.
- Medicare/IRMAA tier assumes the standard premium (tier 0) for the
  projection's first two years, since the tool has no visibility into the
  household's actual MAGI from before the projection starts -- if a
  household's real recent income would already put them in a higher
  IRMAA tier, those first two years' premiums are understated.
- MAGI (for both the avoid_irmaa_tier constraint and the household's
  actual Medicare premium) omits tax-exempt interest, since that isn't
  tracked anywhere else in this tool -- understates MAGI for a household
  with meaningful municipal-bond income.
- An IRA distribution isn't a Roth conversion -- the money it moves to
  Taxable is spendable cash, unlike a conversion, but it's still ordinary
  income like an RMD; if the household doesn't actually spend it, it just
  sits in and grows the Taxable bucket (taxed as capital gains on any
  FUTURE withdrawal of the growth, same as any other taxable-brokerage
  money -- cost basis still isn't tracked, see above).
- A rollover doesn't change that year's total Traditional RMD (the
  Uniform Lifetime divisor only depends on age, not which pool the money
  is in), only which pool it's drawn from -- see ROTH_CONVERSION_SOURCE_TYPES
  for the analogous point about Roth conversions/IRA distributions.
- Taxable cash flows (deposits/withdrawals) are deliberately NOT run
  through the tax engine -- no capital-gains tax on a withdrawal, no cost
  basis added on a deposit -- per the household's own direction (see
  build_cash_flows()).
- A hypothetical inherited account uses the same non-spouse/non-EDB
  10-year-rule assumption as a real one (see above), and requires an
  assumed decedent birthdate/death date the household supplies -- there's
  no way to validate those against reality since the person hasn't (yet)
  died in this scenario.
- The surviving-spouse Social Security "survivor benefit" is a
  simplification: real Social Security lets a survivor claim as early as
  age 60 (with a reduction) even before their OWN normal claim_age, and
  the exact survivor amount depends on when the deceased claimed and the
  survivor's own claiming age. This tool just takes the larger of each
  person's own already-configured claim_age/monthly_benefit -- it doesn't
  model an early, reduced survivor claim before that age.
- No federal estate tax, heir tax on inherited Traditional/Roth balances,
  cost-basis step-up on Taxable holdings, or probate is modeled for the
  estate reported once every person in the household is deceased -- it's
  a gross total_balance snapshot, not a net-to-heirs figure.
- If the deceased was themselves the beneficiary of a real inherited
  account (scenario.inherited_accounts), that account's SECURE Act
  10-year-rule schedule continues completely unchanged (still computed
  from the ORIGINAL beneficiary's age) -- re-inheritance by whoever
  receives it next isn't modeled.
- This tool has no filing status for a single person who was never
  married (or who divorced) -- see pt/tax.py's module docstring --
  filing_status only ever becomes "single" here as the result of a
  configured spouse's death; a single-person household with no mortality
  configured stays "mfj" throughout, an existing gap this feature doesn't
  address.
- No wage income or payroll tax (Social Security/Medicare FICA tax, or
  federal/state income tax on salary) is modeled at all, before or after
  a 401(k) contribution -- this tool has only ever modeled the
  distribution phase (withdrawals, RMDs, Social Security benefits), not
  the accumulation phase, so a 401(k) contribution is added as pure
  principal with no tax consequence either way (correct for the Roth
  catch-up portion, which is already after-tax; a simplification for the
  pre-tax portion, which in reality reduces that year's taxable wages --
  not modeled here since wages themselves aren't tracked).
- Employer match (optional, retirement_contributions[].employer_match) is
  a simple flat-amount-or-percent-of-salary figure, not a real plan's
  tiered match formula (e.g. "100% up to 3% of salary, then 50% up to
  5%") -- the household supplies whatever single number/percentage
  represents their actual expected match. No IRS combined
  employee+employer "annual additions limit" (a separate, much higher
  cap than the employee elective-deferral limit) is checked -- an
  aggressive combination of contribution + match isn't capped here.
- A retirement_contributions amount/pct_of_max configured ABOVE the
  legal elective-deferral+catch-up max is assumed to be an after-tax
  401(k) contribution immediately converted to Roth (the "mega backdoor
  Roth" strategy), landing tax-free in the Roth pool. This assumes
  minimal growth before conversion -- an after-tax contribution that
  instead stays in the 401(k) long-term isn't modeled (its future growth
  would be taxable as ordinary income on withdrawal, unlike Roth, which
  would require real cost-basis tracking this tool doesn't do anywhere).
- The 2025 IRS contribution limits (including SECURE 2.0's enhanced
  60-63 catch-up) are inflated forward using the scenario's general
  inflation rate for later years -- the real annual IRS adjustment
  follows a wage-index formula rounded to $500 increments, not pure CPI
  (the same simplification this tool already makes for tax brackets and
  Medicare/IRMAA thresholds).
- A person's own retirement year for contribution purposes
  (_own_retirement_years()) assumes a spouse with no spouse_retirement_age
  configured retires at the same AGE as the primary (not the same
  calendar year) -- a fallback used only for this feature, since nothing
  else in the projection needs a spouse's own retirement year in
  isolation (household_retirement_year, the LATER of the two, is what
  everything else uses).
- Private health insurance (scenario.health_insurance) is a single flat
  monthly_amount the household supplies -- not modeled from real ACA
  marketplace plan pricing, premium tax credits/subsidies (which depend
  on that year's actual MAGI and could reduce the real cost, sometimes
  substantially), COBRA, or plan-tier variation. The household is
  expected to already have a reasonable estimate in mind.
- Advisory expense (scenario.advisory_expense.pct_of_portfolio) is a
  single flat rate the household supplies directly -- not derived from,
  or reconciled against, any real account's own expense_ratio in the
  database, even if one is set (that stays informational-only, reported
  by pt.cli report). Applied uniformly to total portfolio value every
  year; doesn't get recomputed as buckets grow/shrink at different rates
  relative to each other, and doesn't track any individual real
  account's own balance over time (this tool's buckets are pooled by
  owner/account-type, not by individual account -- see the module's
  balance-bucket description at the top).
- Pre-retirement income (scenario.pre_retirement_income.<name>.
  estimated_gross_income, see build_pre_retirement_income()) is either a
  single flat number (doesn't grow with a raise/promotion beyond general
  inflation) or a per-year list a household types by hand (for a real
  year-specific trajectory, e.g. a one-time bonus year) -- either way it
  doesn't stop early for a layoff, and the wage income itself is never
  taxed as a cash flow (this tool still has no wage income/payroll tax
  model at all -- see the note above) -- a household is assumed to cover
  that from its paycheck/withholding, separately from the portfolio. It
  has two
  real effects: it makes a Roth-conversion/IRA-distribution constraint
  (max_marginal_bracket, avoid_irmaa_tier) and a later year's Medicare/
  IRMAA lookback see a realistic ordinary-income baseline for someone
  still working, not the zero this tool would otherwise assume
  pre-retirement; and it makes that year's tax_owed on the household's
  OWN RMD/conversion/distribution income an INCREMENT on top of that
  baseline (tax on baseline+own-income minus tax on baseline alone), so
  that income is taxed at the real marginal bracket it actually falls
  in, without ever billing the wage income's own tax to the portfolio.
- Liabilities (scenario.liabilities, see build_liabilities()) -- a
  mortgage/car loan/similar -- only affect cash flow (added to
  cash_need, drawn via the same taxable->Traditional->Roth withdrawal
  order as everything else). No amortization/interest-vs-principal
  split is tracked, mortgage interest isn't tax-deductible here, and
  there's no separate liability-balance ledger reducing net worth or
  the final estate value -- the loan's cost is already fully captured
  through the cash it forces out of the portfolio while its window is
  open. The home/car itself isn't modeled as an asset either -- this is
  purely the payment's cash-flow effect, not a net-worth statement.
"""
import math
import random
from collections import Counter
from datetime import date, datetime

import yaml

from . import contributions as contributions_mod
from . import importer as importer_mod
from . import medicare as medicare_mod
from . import rmd as rmd_mod
from . import social_security as social_security_mod
from . import tax as tax_mod
from .classifier import ASSET_CLASSES, MACRO_CLASSES, OTHER, SUBCLASS_MACRO, TARGETABLE_CLASSES, UNCLASSIFIED

# Legacy 2-bucket translation (see resolve_asset_class_returns()): which
# macro classes a household's returns.growth/.fixed_income (no
# returns.asset_classes given) counts as "equity-like" vs "fixed-income-
# like". Kept only for that translation -- returns.asset_classes-based
# scenarios don't use this at all, each class gets its own real number.
EQUITY_LIKE_MACROS = {"Growth", "Alternatives"}

# Assumed annual return volatility (standard deviation, percentage points),
# one rough default per MACRO class -- used by resolve_asset_class_returns()
# when a returns.asset_classes entry gives expected_return but omits
# volatility (or omits the class entirely and it falls back to its macro).
# These are rough, asset-class-level historical figures, not tailored to
# any specific fund lineup -- same caveat DEFAULT_GROWTH_VOLATILITY/
# DEFAULT_FIXED_INCOME_VOLATILITY (the 2-bucket predecessors of this) always
# carried.
DEFAULT_VOLATILITY_BY_MACRO = {
    "Growth": 18.0,
    "Income": 7.0,
    "Liquidity": 1.5,
    "Alternatives": 30.0,
    # Fixed Indexed Annuities: principal-protected with an index-linked
    # crediting rate (typically capped/participation-rated), so realized
    # volatility is well below the index it tracks -- a rough middle
    # ground between Liquidity and Income, not tailored to any specific
    # contract's actual cap/floor terms.
    "Insurance": 5.0,
}
# Legacy names, still used by the growth/fixed_income translation path.
DEFAULT_GROWTH_VOLATILITY = 16.0
DEFAULT_FIXED_INCOME_VOLATILITY = 5.0

# Default correlation between two DIFFERENT macro classes' annual returns
# (see draw_correlated_return()) -- a documented, roughly historical,
# not-tailored-to-any-specific-fund-lineup default, fully overridable via
# scenario.returns.correlations. Symmetric; a macro vs. itself is handled
# separately (SAME_MACRO_CORRELATION for two distinct subclasses under it,
# 1.0 for a class against itself).
DEFAULT_MACRO_CORRELATIONS = {
    frozenset(("Growth", "Income")): 0.15,
    frozenset(("Growth", "Liquidity")): 0.05,
    frozenset(("Growth", "Alternatives")): 0.30,
    frozenset(("Income", "Liquidity")): 0.20,
    frozenset(("Income", "Alternatives")): 0.05,
    frozenset(("Liquidity", "Alternatives")): 0.00,
    # Insurance (Fixed Indexed Annuities): mildly positive to Growth (the
    # crediting rate tracks an equity index, damped by its cap/floor),
    # low to everything else -- a principal-protected contract barely
    # moves with bonds/cash/alternatives at all.
    frozenset(("Insurance", "Growth")): 0.15,
    frozenset(("Insurance", "Income")): 0.10,
    frozenset(("Insurance", "Liquidity")): 0.05,
    frozenset(("Insurance", "Alternatives")): 0.05,
}
# Two DIFFERENT subclasses under the SAME macro (e.g. US Equity vs.
# International Equity) -- higher than any cross-macro figure above, since
# they're much more alike than two classes from different macros.
SAME_MACRO_CORRELATION = 0.85

# Built-in scenario.returns.asset_classes.<class>.tracks/floor/cap defaults
# (see resolve_asset_class_tracking()) for a class whose Monte Carlo draw
# isn't an independent Normal(mean, vol) at all -- it CREDITS a
# floored-and-capped copy of another class's own annual draw that year,
# the mechanism behind a Fixed Indexed Annuity's point-to-point index
# crediting (principal-protected -- a 0% floor -- with the upside on its
# tracked index, usually the S&P 500, capped well below the index's own
# volatility). Rough, product-specific figures -- real contracts' caps
# vary roughly 4-12% by index/term/prevailing rates -- fully overridable
# per scenario via that same class's own tracks/floor/cap entry, same as
# every other default here.
DEFAULT_TRACKING = {
    "Fixed Indexed Annuities": {"tracks": "US Equity", "floor": 0.0, "cap": 8.0},
}

# account_type (see pt/importer.py ACCOUNT_TYPES) -> bucket kind.
# "Inherited IRA"/"Inherited Roth IRA" are handled separately (matched by
# account_number against scenario.inherited_accounts), not through this map.
TAXABLE_ACCOUNT_TYPES = {"Brokerage"}
TRADITIONAL_ACCOUNT_TYPES = {"IRA", "401(k)"}
ROTH_ACCOUNT_TYPES = {"Roth IRA"}
INHERITED_ACCOUNT_TYPES = {"Inherited IRA", "Inherited Roth IRA"}

# Which of a person's Traditional pools (see build_buckets()) a Roth
# conversion can draw from, and in what order -- both IRA and 401(k), per
# the household's own direction (their 401(k) plan allows this in their
# situation), IRA first. The order doesn't affect any tax total (both are
# ordinary income either way), only which pool a --verbose column shows the
# reduction against once a conversion needs more than one pool to cover
# annual_amount.
ROTH_CONVERSION_SOURCE_TYPES = ("IRA", "401(k)")


class ProjectionError(Exception):
    pass


def _legacy_returns_translation(scenario: dict) -> dict:
    """Translates the old 2-bucket returns.growth/.fixed_income (+ optional
    .growth_volatility/.fixed_income_volatility) into the same
    {class_name: (expected_return_fraction, volatility_pct)} shape
    resolve_asset_class_returns() returns -- every macro in
    EQUITY_LIKE_MACROS (Growth, Alternatives) gets the growth number,
    every other macro (Income, Liquidity, Insurance) gets the
    fixed_income number. Used only when the scenario has no
    returns.asset_classes at all, so every scenario file written before
    that existed keeps producing numerically identical output. Raises
    ProjectionError (matching the pre-existing message) if
    returns.growth/.fixed_income are themselves missing too."""
    try:
        growth_rate = scenario["returns"]["growth"] / 100
        fixed_rate = scenario["returns"]["fixed_income"] / 100
    except KeyError as e:
        raise ProjectionError(f"Scenario is missing returns.{e.args[0]}.")
    growth_vol = scenario.get("returns", {}).get("growth_volatility", DEFAULT_GROWTH_VOLATILITY)
    fixed_vol = scenario.get("returns", {}).get("fixed_income_volatility", DEFAULT_FIXED_INCOME_VOLATILITY)
    growth_entry = (growth_rate, growth_vol)
    fixed_entry = (fixed_rate, fixed_vol)
    resolved = {m: (growth_entry if m in EQUITY_LIKE_MACROS else fixed_entry) for m in MACRO_CLASSES}
    # The OLD compute_blended_growth_rate() only ever singled out
    # EQUITY_LIKE_MACROS -- everything else, OTHER/UNCLASSIFIED included,
    # implicitly landed in the fixed-rate weight with no explicit lookup
    # at all. Match that exactly here (rather than letting OTHER/
    # UNCLASSIFIED fall through to _resolve_one_class()'s "classify this
    # instead" error, which is only appropriate for the NEW returns.
    # asset_classes style) so a household with an Unclassified/Other
    # remainder and a legacy-shaped scenario keeps working unchanged.
    resolved[OTHER] = fixed_entry
    resolved[UNCLASSIFIED] = fixed_entry
    return resolved


def resolve_asset_class_returns(scenario: dict) -> dict:
    """The scenario's return/volatility assumptions, keyed by every macro
    or subclass name given in scenario.returns.asset_classes (see module
    docstring) -- {class_name: (expected_return_fraction, volatility_pct)}.
    volatility defaults to DEFAULT_VOLATILITY_BY_MACRO[that class's macro]
    if the entry omits it. Falls back to _legacy_returns_translation()
    (returns.growth/.fixed_income) entirely if returns.asset_classes isn't
    present at all -- the two styles aren't merged; a scenario uses one or
    the other. Raises ProjectionError for an asset_classes key that isn't a
    real macro/subclass name (classifier.TARGETABLE_CLASSES) or
    classifier.OTHER/UNCLASSIFIED (the two names outside the macro
    hierarchy -- valid here since a household can genuinely hold value in
    either, see _resolve_one_class()), or an entry missing expected_return
    (no default -- same fail-fast style as the legacy returns.growth).
    OTHER/UNCLASSIFIED have no macro to fall back to for a default
    volatility, so (unlike every real macro/subclass) they must give
    volatility explicitly if given at all."""
    asset_classes = scenario.get("returns", {}).get("asset_classes")
    if not asset_classes:
        return _legacy_returns_translation(scenario)
    valid_names = TARGETABLE_CLASSES + [OTHER, UNCLASSIFIED]
    resolved = {}
    for class_name, entry in asset_classes.items():
        if class_name not in valid_names:
            raise ProjectionError(
                f"'{class_name}' in scenario.returns.asset_classes is not a recognized macro or asset "
                f"class. Macro classes: {', '.join(MACRO_CLASSES)}. Asset classes: {', '.join(ASSET_CLASSES)}. "
                f"Also accepted: {OTHER}, {UNCLASSIFIED}."
            )
        if "expected_return" not in entry:
            raise ProjectionError(
                f"scenario.returns.asset_classes.{class_name} is missing expected_return."
            )
        if class_name in (OTHER, UNCLASSIFIED):
            if "volatility" not in entry:
                raise ProjectionError(
                    f"scenario.returns.asset_classes.{class_name} is missing volatility -- required for "
                    f"{class_name} since (unlike a real macro/asset class) it has no macro to fall back to "
                    "for a default."
                )
            default_vol = None
        else:
            macro = class_name if class_name in MACRO_CLASSES else SUBCLASS_MACRO[class_name]
            default_vol = DEFAULT_VOLATILITY_BY_MACRO[macro]
        resolved[class_name] = (entry["expected_return"] / 100, entry.get("volatility", default_vol))
    return resolved


def _resolve_one_class(class_name: str, resolved: dict, value: float, total: float) -> tuple:
    """One held class's (expected_return_fraction, volatility_pct), via the
    resolution order documented on resolve_asset_class_returns()'s caller:
    exact subclass/macro entry in `resolved`, else that subclass's OWN
    macro entry in `resolved`, else ProjectionError naming the class and
    its current dollar weight. `class_name` may be a real subclass, a
    macro (from allocation_by_macro_class()'s rows, or a legacy-shaped
    hypothetical_accounts result), or classifier.OTHER/UNCLASSIFIED (never
    have a macro to fall back to -- always need their own explicit entry,
    or a classification cleanup instead, per the error message below)."""
    if class_name in resolved:
        return resolved[class_name]
    macro = SUBCLASS_MACRO.get(class_name)
    if macro and macro in resolved:
        return resolved[macro]
    pct = (value / total * 100) if total else 0.0
    suggestion = (
        "-- classifying the underlying holding(s) properly (`classify`/`classify-unclassified`) is usually "
        "the real fix, rather than assigning it a return"
        if class_name in (OTHER, UNCLASSIFIED)
        else f"-- add a `returns.asset_classes.{class_name}` entry, or one for its macro class"
        f" ('{macro}')" if macro else ""
    )
    raise ProjectionError(
        f"scenario.returns.asset_classes has no expected_return for '{class_name}', which is "
        f"{pct:.1f}% (${value:,.0f}) of the household's current allocation {suggestion}."
    )


def compute_blended_growth_rate(allocation_rows: list, total: float, scenario: dict) -> float:
    """Blends every held asset class's own expected return (see
    resolve_asset_class_returns()) by its current dollar weight. Returns a
    fraction (e.g. 0.0723 for 7.23%). allocation_rows: macro- or subclass-
    level rows (allocation.allocation_by_asset_class()'s shape -- typically
    subclass-level for a real household, or allocation_by_macro_class()'s
    coarser rows, which also work via the same class-or-its-macro
    resolution order)."""
    if total <= 0:
        return 0.0
    resolved = resolve_asset_class_returns(scenario)
    return sum(
        _resolve_one_class(r["asset_class"], resolved, r["value"], total)[0] * (r["value"] / total)
        for r in allocation_rows
    )


def compute_blended_volatility(allocation_rows: list, total: float, scenario: dict) -> float:
    """Same weighted-average approach as compute_blended_growth_rate(), but
    for volatility -- a single headline percentage (e.g. 12.3 for a
    12.3-percentage-point annual standard deviation) for display (the
    Monte Carlo summary's "volatility" field, format_monte_carlo_table()).
    This is a simplification even with per-class inputs: a true portfolio
    volatility needs the correlation between classes (normally well below
    1, which lowers a real blended portfolio's volatility below this
    simple weighted average) -- run_monte_carlo() itself no longer uses
    this number to drive the simulation, see draw_correlated_return()."""
    if total <= 0:
        return 0.0
    resolved = resolve_asset_class_returns(scenario)
    return sum(
        _resolve_one_class(r["asset_class"], resolved, r["value"], total)[1] * (r["value"] / total)
        for r in allocation_rows
    )


def _resolve_correlation(class_a: str, class_b: str, scenario: dict) -> float:
    """Correlation between two held classes' annual returns, for the
    covariance matrix draw_correlated_return() builds. Resolution order:
    an exact override in scenario.returns.correlations (keyed "A,B" or
    "B,A", either order); else 1.0 if they're the same class; else
    SAME_MACRO_CORRELATION if they're two different subclasses under the
    same macro; else DEFAULT_MACRO_CORRELATIONS for their two (possibly
    equal) macros. All of DEFAULT_MACRO_CORRELATIONS/
    SAME_MACRO_CORRELATION are themselves overridable the same way, via a
    "Growth,Income"/"Growth,Growth"-style key in returns.correlations."""
    if class_a == class_b:
        return 1.0
    overrides = scenario.get("returns", {}).get("correlations", {})
    for key in (f"{class_a},{class_b}", f"{class_b},{class_a}"):
        if key in overrides:
            return overrides[key]
    macro_a = class_a if class_a in MACRO_CLASSES else SUBCLASS_MACRO.get(class_a, class_a)
    macro_b = class_b if class_b in MACRO_CLASSES else SUBCLASS_MACRO.get(class_b, class_b)
    if macro_a == macro_b:
        for key in (f"{macro_a},{macro_a}",):
            if key in overrides:
                return overrides[key]
        return SAME_MACRO_CORRELATION
    for key in (f"{macro_a},{macro_b}", f"{macro_b},{macro_a}"):
        if key in overrides:
            return overrides[key]
    return DEFAULT_MACRO_CORRELATIONS.get(frozenset((macro_a, macro_b)), 0.0)


def resolve_asset_class_tracking(scenario: dict) -> dict:
    """Optional tracks/floor/cap configuration -- see DEFAULT_TRACKING's
    docstring for the mechanism this drives (a class's Monte Carlo draw
    that CREDITS a floored-and-capped copy of another class's own draw,
    instead of its own independent Normal(mean, vol)). Only consulted by
    build_correlated_return_sampler() -- project()'s single deterministic
    blended rate, and every class's own expected_return, are computed
    exactly as before; this only reshapes the MONTE CARLO year-to-year
    draw for whichever classes use it.

    Reads each scenario.returns.asset_classes.<class> entry's own
    `tracks` (a macro or subclass name) / `floor` / `cap` (both
    percentage points, same units as expected_return -- floor defaults to
    0.0 if omitted) -- falling back to DEFAULT_TRACKING for a class (like
    "Fixed Indexed Annuities") that has a built-in default, so it works
    with just an expected_return given. Set `tracks: false` on the entry
    to opt a class with a built-in default OUT of tracking entirely (a
    plain independent draw instead). A class with no `tracks`, no
    default, or an explicit `tracks: false` isn't included in the
    returned dict at all.

    Returns {class_name: {"tracks": <macro or subclass name>, "floor":
    fraction, "cap": fraction}}. Raises ProjectionError if `tracks` names
    something invalid, a class tracks itself, a class tracks ANOTHER
    tracking class (chained tracking isn't supported), `cap` is missing
    with no built-in default, or floor > cap."""
    asset_classes = scenario.get("returns", {}).get("asset_classes") or {}
    valid_names = TARGETABLE_CLASSES + [OTHER, UNCLASSIFIED]
    tracking = {}
    for class_name, entry in asset_classes.items():
        default = DEFAULT_TRACKING.get(class_name, {})
        tracks = entry["tracks"] if "tracks" in entry else default.get("tracks")
        if not tracks:  # None (never configured) or explicit `tracks: false`
            continue
        if tracks not in valid_names:
            raise ProjectionError(
                f"scenario.returns.asset_classes.{class_name}.tracks ('{tracks}') is not a recognized "
                f"macro or asset class. Macro classes: {', '.join(MACRO_CLASSES)}. "
                f"Asset classes: {', '.join(ASSET_CLASSES)}."
            )
        if tracks == class_name:
            raise ProjectionError(
                f"scenario.returns.asset_classes.{class_name}.tracks can't be '{class_name}' itself."
            )
        if "cap" in entry:
            cap_pts = entry["cap"]
        elif "cap" in default:
            cap_pts = default["cap"]
        else:
            raise ProjectionError(
                f"scenario.returns.asset_classes.{class_name} tracks '{tracks}' but has no 'cap' -- the "
                "maximum annual credited return, in percentage points (e.g. 8.0 for an 8% cap), and this "
                "class has no built-in default. 'floor' defaults to 0.0 if omitted."
            )
        floor_pts = entry.get("floor", default.get("floor", 0.0))
        if floor_pts > cap_pts:
            raise ProjectionError(
                f"scenario.returns.asset_classes.{class_name}: floor ({floor_pts}%) can't be greater "
                f"than cap ({cap_pts}%)."
            )
        tracking[class_name] = {"tracks": tracks, "floor": floor_pts / 100, "cap": cap_pts / 100}
    for class_name, spec in tracking.items():
        if spec["tracks"] in tracking:
            raise ProjectionError(
                f"scenario.returns.asset_classes.{class_name} tracks '{spec['tracks']}', which itself "
                "tracks another class -- chained tracking isn't supported; track a plain (non-tracking) "
                "class instead."
            )
    return tracking


def _cholesky(matrix: list) -> list:
    """Lower-triangular Cholesky factor L (matrix = L . L^T) of a small
    symmetric positive-semi-definite matrix, pure Python (no numpy -- see
    module docstring's dependency philosophy; these are at most ~12x12 in
    practice, one class per classifier.ASSET_CLASSES leaf). A tiny
    diagonal nudge (1e-9) guards against a matrix that's positive-
    SEMI-definite rather than strictly positive-definite (e.g. a
    correlation of exactly 1.0 between two distinct classes), which would
    otherwise take sqrt() of a slightly-negative floating-point residual."""
    n = len(matrix)
    L = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1):
            s = sum(L[i][k] * L[j][k] for k in range(j))
            if i == j:
                L[i][j] = math.sqrt(max(matrix[i][i] - s, 1e-9))
            else:
                L[i][j] = (matrix[i][j] - s) / L[j][j]
    return L


def _build_class_return_drawer(allocation_rows: list, total: float, scenario: dict, rng):
    """Shared setup for build_correlated_return_sampler() (one portfolio-
    wide draw) and build_bucket_correlated_return_sampler() (the same
    underlying draw, sliced differently per tax bucket) -- builds the
    covariance matrix and its Cholesky factor ONCE from every class the
    HOUSEHOLD actually holds (> 0 value in allocation_rows) across every
    bucket combined (weights/rates/volatilities/correlations don't change
    year to year or trial to trial -- same "one fixed composition for the
    whole projection" simplification project() already makes for the mean
    return), not per year.

    A class configured with tracks/floor/cap (see
    resolve_asset_class_tracking() -- "Fixed Indexed Annuities" gets this
    by default) is handled differently: instead of drawing its OWN
    independent/correlated Normal, its return that year is a
    floor/cap-clipped copy of its TRACKED class's own draw -- so it moves
    in lockstep with, say, US Equity below the cap and above the floor,
    exactly like a real Fixed Indexed Annuity's point-to-point index
    crediting. The tracked class doesn't have to be something the
    household separately holds -- it still gets its own zero-weight
    "reference" row purely so there's a draw to clip.

    rng: an object with a .gauss(mu, sigma) method (e.g. a seeded
    random.Random()) -- the SAME rng run_monte_carlo() already threads
    through project(), so a seeded Monte Carlo run stays fully
    reproducible.

    Returns (classes, draw_class_returns): `classes` is the list of class
    names each draw is indexed by; draw_class_returns() is a zero-argument
    function -- call it once per simulated year -- returning {class_name:
    realized_return} for that year, already tracking-adjusted. A caller
    weights this same dict by whatever it wants a return FOR (the whole
    household, or one bucket) -- see the two public wrappers below."""
    held = [r for r in allocation_rows if r["value"] > 0]
    resolved = resolve_asset_class_returns(scenario)
    tracking = resolve_asset_class_tracking(scenario)

    # Classes that draw their own independent/correlated Normal -- every
    # held class EXCEPT the tracking ones, which instead clip a copy of
    # their tracked class's draw (added below).
    modeled = [r for r in held if r["asset_class"] not in tracking]
    tracked = [r for r in held if r["asset_class"] in tracking]

    classes = [r["asset_class"] for r in modeled]
    # A tracking class's tracked reference needs its own draw even if the
    # household doesn't separately hold that class -- a zero-weight row
    # purely to generate the index draw the tracking class(es) clip.
    for r in tracked:
        ref = tracking[r["asset_class"]]["tracks"]
        if ref not in classes:
            classes.append(ref)

    means = []
    vols = []
    for class_name in classes:
        # A real held class's actual dollar value; a zero-weight reference
        # row (added above) has none of its own.
        value = next((r["value"] for r in modeled if r["asset_class"] == class_name), 0.0)
        mean, vol = _resolve_one_class(class_name, resolved, value, total)
        means.append(mean)
        vols.append(vol / 100)
    n = len(classes)
    if n == 0:
        return classes, (lambda: {})
    covariance = [
        [vols[i] * vols[j] * _resolve_correlation(classes[i], classes[j], scenario) for j in range(n)]
        for i in range(n)
    ]
    L = _cholesky(covariance)
    # (class_name, index of its tracked reference class, floor, cap)
    track_specs = [(r["asset_class"], classes.index(tracking[r["asset_class"]]["tracks"]),
                    tracking[r["asset_class"]]["floor"], tracking[r["asset_class"]]["cap"])
                   for r in tracked]

    def draw_class_returns() -> dict:
        z = [rng.gauss(0, 1) for _ in range(n)]
        raw = [means[i] + sum(L[i][k] * z[k] for k in range(i + 1)) for i in range(n)]
        class_returns = dict(zip(classes, raw))
        for class_name, ref_idx, floor, cap in track_specs:
            class_returns[class_name] = min(max(raw[ref_idx], floor), cap)
        return class_returns

    return classes, draw_class_returns


def build_correlated_return_sampler(allocation_rows: list, total: float, scenario: dict, rng):
    """Builds and returns a zero-argument function -- call it once per
    simulated year -- that draws ONE correlated return for the whole
    portfolio (see _build_class_return_drawer() for the mechanism, incl.
    tracks/floor/cap classes), instead of run_monte_carlo()'s old single
    blended Normal(blended_rate, volatility) draw. This is what captures
    real diversification: with correlation < 1 between classes (see
    _resolve_correlation()), the resulting portfolio variance comes out
    BELOW compute_blended_volatility()'s naive weighted average -- the gap
    that function's own docstring calls out."""
    _, draw_class_returns = _build_class_return_drawer(allocation_rows, total, scenario, rng)
    held = [r for r in allocation_rows if r["value"] > 0]
    weights = {r["asset_class"]: r["value"] / total for r in held} if total > 0 else {}

    def draw() -> float:
        class_returns = draw_class_returns()
        return sum(w * class_returns[c] for c, w in weights.items())

    return draw


def build_bucket_correlated_return_sampler(household_allocation_rows: list, household_total: float,
                                            bucket_allocations: dict, scenario: dict, rng):
    """The per-tax-bucket counterpart to build_correlated_return_sampler():
    returns a zero-argument function -- call it once per simulated year --
    that draws ONE shared per-asset-class "economy" for the year (see
    _build_class_return_drawer(), built from household_allocation_rows/
    household_total so every class ANY bucket holds is included and stays
    correctly correlated with every other), then returns {bucket_key:
    realized_return} by weighting that SAME draw by each bucket's own
    asset mix (bucket_allocations -- see bucket_allocation_rows()). This
    is what keeps buckets correlated with each other (a bad year for
    equities hits every bucket holding equities, in the same year, at the
    same underlying draw) while letting each realize a DIFFERENT blended
    return based on what it actually holds -- unlike
    build_correlated_return_sampler(), which collapses to one number for
    the whole household."""
    _, draw_class_returns = _build_class_return_drawer(
        household_allocation_rows, household_total, scenario, rng
    )
    bucket_weights = {}
    for bucket_key, (rows, total) in bucket_allocations.items():
        held = [r for r in rows if r["value"] > 0]
        bucket_weights[bucket_key] = {r["asset_class"]: r["value"] / total for r in held} if total > 0 else {}

    def draw() -> dict:
        class_returns = draw_class_returns()
        return {
            bucket_key: sum(w * class_returns[c] for c, w in weight_map.items())
            for bucket_key, weight_map in bucket_weights.items()
        }

    return draw


def _parse_advisory_expense_rate(scenario: dict) -> float:
    """scenario.advisory_expense.pct_of_portfolio (optional) -- a SINGLE
    global advisory/AUM fee for the whole portfolio, set directly in the
    scenario file (see pt/scenario.py) -- this is the ONLY advisory
    expense project() models. It is NOT derived from any real account's
    own expense_ratio in the database (`accounts set-expense-ratio`) --
    that per-account figure, and a symbol's own per-symbol expense ratio,
    both remain informational-only, reported by pt.cli report (see
    pt/allocation.py's expense_summary()) but never modeled here, so
    there's exactly one number driving what project() actually simulates,
    set in exactly one place. Returns a fraction (e.g. 0.01 for a
    1.00%/year fee), 0.0 if not configured. Raises ProjectionError if
    given but outside a plausible 0-25% range."""
    pct = (scenario.get("advisory_expense") or {}).get("pct_of_portfolio")
    if pct is None:
        return 0.0
    if not (0 <= pct <= 25):
        raise ProjectionError(
            "scenario.advisory_expense.pct_of_portfolio should be a percentage, e.g. 1.0 for 1.00%/year "
            f"(got {pct}, which is outside a plausible 0-25% range)."
        )
    return pct / 100


def _get(d: dict, *path, error_ctx: str):
    cur = d
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            raise ProjectionError(f"{error_ctx} is missing '{'.'.join(path)}'.")
        cur = cur[key]
    return cur


def _people(profile: dict) -> dict:
    """{name (lowercase): birthdate} for primary and, if present, spouse."""
    primary = _get(profile, "people", "primary", error_ctx="Profile")
    people = {primary["name"].lower(): primary["birthdate"]}
    spouse = profile.get("people", {}).get("spouse")
    if spouse:
        people[spouse["name"].lower()] = spouse["birthdate"]
    return people


def build_hypothetical_accounts(scenario: dict, profile: dict) -> tuple:
    """Parses scenario.hypothetical_accounts (optional list -- returns
    ([], [], 0.0) if absent) into the same shapes pt/allocation.py's
    account_values()/allocation_by_macro_class()/household_total_value()
    produce from a real imported snapshot -- lets project()/
    run_monte_carlo() run against a made-up household with NO import, NO
    database snapshot, and NO ticker classification at all, so someone
    can try the tool immediately without any real data. See pt.cli
    project/monte-carlo, which use this INSTEAD of the database entirely
    whenever a scenario configures this section -- real accounts and
    hypothetical ones are never combined in the same run.

    Each entry:
        account_number -- any unique string (doesn't need to match, or
            even resemble, a real account number)
        account_type -- one of importer.ACCOUNT_TYPES (Brokerage, IRA,
            401(k), Roth IRA, Inherited IRA, Inherited Roth IRA, HSA) --
            same values `accounts set-type` accepts, sorts into the same
            taxable/Traditional/Roth/inherited buckets build_buckets()
            uses for a real account. An "Inherited IRA"/"Inherited Roth
            IRA" account_type here is just a Traditional/Roth balance
            with no RMD/10-year-rule schedule attached (use
            scenario.inherited_accounts instead if you want to model
            that -- account_number would need to match one of these
            entries', which isn't required, so the two features don't
            currently compose).
        owner -- matches a people.primary/spouse.name in the profile
        value -- current balance, today's dollars
        EXACTLY ONE of:
          pct_growth -- 0-100, the percentage of THIS account's value
            treated as "growth-like" (equity) for the blended growth-
            rate/volatility purpose a real account's classified holdings
            serve (see compute_blended_growth_rate()/compute_blended_
            volatility()) -- the remainder is treated as fixed-income-
            like (internally: {Growth: pct_growth, Income: 100 -
            pct_growth}). A coarse, single-number-per-account legacy
            option, kept working unchanged for any existing scenario file.
          asset_classes -- {class_name: pct, ...}, percentages summing to
            100 (+/- 0.01 float tolerance), same macro-or-subclass names
            as scenario.returns.asset_classes (classifier.
            TARGETABLE_CLASSES) -- lets a hypothetical account's
            composition be as fine-grained as a real classified account's
            (see allocation.allocation_by_asset_class()), instead of the
            single growth/fixed-income split above.

    Returns (account_rows, allocation_rows, total): account_rows matches
    pt/allocation.py's account_values() shape (one dict per account:
    account_number, account_type, owner, value); allocation_rows/total
    match allocation_by_asset_class()'s pair shape (or, for an entry using
    legacy pct_growth, allocation_by_macro_class()'s coarser "Growth"/
    "Income" shape) -- aggregated across every hypothetical account's own
    split. compute_blended_growth_rate()/build_correlated_return_sampler()
    resolve either shape identically (a class name or its own macro).

    Raises ProjectionError for an unrecognized account_type, an owner not
    in the profile, a duplicate account_number, a negative value, neither
    (or both) of pct_growth/asset_classes given, an asset_classes key
    that isn't a real macro/subclass name, or asset_classes percentages
    not summing to 100."""
    account_rows, class_rows = _parse_hypothetical_accounts(scenario, profile)
    class_values = {}
    for r in class_rows:
        if r["value"]:
            class_values[r["asset_class"]] = class_values.get(r["asset_class"], 0.0) + r["value"]
    total = sum(class_values.values())
    allocation_rows = [
        {"asset_class": name, "value": v, "pct": (v / total if total else 0.0)}
        for name, v in class_values.items()
    ]
    return account_rows, allocation_rows, total


def build_hypothetical_account_class_rows(scenario: dict, profile: dict) -> list:
    """Per-account asset-class breakdown for scenario.hypothetical_accounts
    -- {account_number, account_type, owner, asset_class, value} rows, the
    hypothetical-scenario counterpart to allocation.allocation_by_account()
    (a real household's per-account breakdown). Needed to compute a growth
    rate PER TAX BUCKET instead of one household-wide blend -- see
    bucket_allocation_rows() -- since build_hypothetical_accounts() itself
    only returns the household-wide aggregate. Same validation/errors as
    build_hypothetical_accounts() (both share _parse_hypothetical_accounts())."""
    _, class_rows = _parse_hypothetical_accounts(scenario, profile)
    return class_rows


def _parse_hypothetical_accounts(scenario: dict, profile: dict) -> tuple:
    """Shared validation/parsing for build_hypothetical_accounts() and
    build_hypothetical_account_class_rows() -- see build_hypothetical_
    accounts()'s docstring for the entry shape and every error this
    raises. Returns (account_rows, class_rows): account_rows matches
    allocation.account_values()'s shape (account_number, account_type,
    owner, value); class_rows is the same per-account, per-asset-class
    breakdown allocation.allocation_by_account() returns for a real
    household (account_number, account_type, owner, asset_class, value)
    -- one entry per (account, class) pair (two per account for the
    legacy pct_growth shape: Growth + Income)."""
    entries = scenario.get("hypothetical_accounts", [])
    people = _people(profile)
    account_rows = []
    class_rows = []
    seen_numbers = set()
    for entry in entries:
        account_number = str(entry.get("account_number") or "").strip()
        if not account_number:
            raise ProjectionError("hypothetical_accounts entry is missing 'account_number'.")
        if account_number in seen_numbers:
            raise ProjectionError(
                f"hypothetical_accounts has more than one entry with account_number '{account_number}'."
            )
        seen_numbers.add(account_number)

        account_type = entry.get("account_type")
        if account_type not in importer_mod.ACCOUNT_TYPES:
            raise ProjectionError(
                f"hypothetical_accounts entry '{account_number}' has account_type={account_type!r}, "
                f"must be one of: {', '.join(importer_mod.ACCOUNT_TYPES)}."
            )
        owner = (entry.get("owner") or "").lower()
        if owner not in people:
            raise ProjectionError(
                f"hypothetical_accounts entry '{account_number}' has owner '{entry.get('owner')}', which "
                "doesn't match a people.primary/spouse name in the profile."
            )
        value = entry.get("value")
        if value is None or value < 0:
            raise ProjectionError(f"hypothetical_accounts entry '{account_number}' needs a non-negative 'value'.")

        pct_growth = entry.get("pct_growth")
        asset_classes = entry.get("asset_classes")
        if pct_growth is not None and asset_classes is not None:
            raise ProjectionError(
                f"hypothetical_accounts entry '{account_number}' has both 'pct_growth' and 'asset_classes' -- "
                "use exactly one."
            )
        if asset_classes is not None:
            pct_sum = sum(asset_classes.values())
            if abs(pct_sum - 100) > 0.01:
                raise ProjectionError(
                    f"hypothetical_accounts entry '{account_number}''s asset_classes percentages sum to "
                    f"{pct_sum}, not 100."
                )
            for class_name, pct in asset_classes.items():
                if class_name not in TARGETABLE_CLASSES:
                    raise ProjectionError(
                        f"hypothetical_accounts entry '{account_number}' has asset_classes.{class_name!r}, "
                        f"which is not a recognized macro or asset class. Macro classes: "
                        f"{', '.join(MACRO_CLASSES)}. Asset classes: {', '.join(ASSET_CLASSES)}"
                    )
                class_rows.append({"account_number": account_number, "account_type": account_type,
                                    "owner": owner, "asset_class": class_name, "value": value * (pct / 100)})
        elif pct_growth is not None:
            if not (0 <= pct_growth <= 100):
                raise ProjectionError(
                    f"hypothetical_accounts entry '{account_number}' needs 'pct_growth' between 0 and 100."
                )
            class_rows.append({"account_number": account_number, "account_type": account_type, "owner": owner,
                                "asset_class": "Growth", "value": value * (pct_growth / 100)})
            class_rows.append({"account_number": account_number, "account_type": account_type, "owner": owner,
                                "asset_class": "Income", "value": value * (1 - pct_growth / 100)})
        else:
            raise ProjectionError(
                f"hypothetical_accounts entry '{account_number}' needs either 'pct_growth' (0-100) or "
                "'asset_classes' ({class_name: pct, ...} summing to 100)."
            )
        account_rows.append({
            "account_number": account_number,
            "account_type": account_type,
            "owner": owner,
            "value": value,
        })

    return account_rows, class_rows


def build_inherited_schedules(scenario: dict, profile: dict) -> list:
    """Builds an rmd.InheritedAccountSchedule for each entry in
    scenario.inherited_accounts (optional section -- returns [] if absent).
    Each entry: type ("traditional"/"roth"), decedent_birthdate,
    decedent_death_date, beneficiary (a name matching a people entry in the
    profile), an optional planned_withdrawal {start_date, end_date,
    annual_amount} -- see _planned_withdrawal_floor() for what that does --
    and EXACTLY ONE of:
        account_number -- a real account already held (matched against
            account_rows in build_buckets()), starting from its CURRENT
            balance
        hypothetical_value -- a HYPOTHETICAL future inheritance not yet
            held at all -- its balance doesn't exist until
            decedent_death_date's year, at which point it "appears" in the
            simulation at this starting value and follows the exact same
            SECURE Act 10-year-rule schedule from then on (see project()).
            Lets you model "what if I inherit an IRA in 2032" without
            already holding the account -- decedent_death_date IS the
            hypothetical inheritance year, so give your assumed decedent's
            actual birthdate but whatever death date matches the year you
            want to model.
    Returns a normalized list of {account_number (a synthetic id for a
    hypothetical entry -- "hypothetical:<beneficiary>:<year>"), kind,
    schedule, planned_withdrawal, hypothetical_value, hypothetical_year
    (both None for a real account)}."""
    entries = scenario.get("inherited_accounts", [])
    people = _people(profile)
    schedules = []
    for entry in entries:
        try:
            beneficiary_name = entry["beneficiary"].lower()
            beneficiary_birthdate = people[beneficiary_name]
        except KeyError:
            raise ProjectionError(
                f"inherited_accounts entry for account {entry.get('account_number')} has "
                f"beneficiary '{entry.get('beneficiary')}', which doesn't match a "
                "people.primary/spouse name in the profile."
            )
        account_number = entry.get("account_number")
        hypothetical_value = entry.get("hypothetical_value")
        if account_number is None and hypothetical_value is None:
            raise ProjectionError(
                f"inherited_accounts entry for beneficiary '{entry['beneficiary']}' needs either "
                "account_number (a real, already-held account) or hypothetical_value (a future, "
                "not-yet-held inheritance)."
            )
        if account_number is not None and hypothetical_value is not None:
            raise ProjectionError(
                f"inherited_accounts entry for beneficiary '{entry['beneficiary']}' has both "
                "account_number and hypothetical_value -- it's either a real account you already "
                "hold, or a hypothetical future one, not both."
            )
        schedule = rmd_mod.InheritedAccountSchedule(
            account_kind=entry["type"],
            decedent_birthdate=entry["decedent_birthdate"],
            decedent_death_date=entry["decedent_death_date"],
            beneficiary_birthdate=beneficiary_birthdate,
        )
        planned = entry.get("planned_withdrawal")
        if planned:
            for field in ("start_date", "end_date", "annual_amount"):
                if field not in planned:
                    raise ProjectionError(
                        f"inherited_accounts entry for account {entry.get('account_number')}'s "
                        f"planned_withdrawal is missing '{field}'."
                    )
            if isinstance(planned["annual_amount"], list):
                expected_len = planned["end_date"].year - planned["start_date"].year + 1
                if len(planned["annual_amount"]) != expected_len:
                    raise ProjectionError(
                        f"inherited_accounts entry for account {entry.get('account_number')}'s "
                        f"planned_withdrawal has annual_amount as a list of "
                        f"{len(planned['annual_amount'])} values, but its start_date/end_date window "
                        f"covers {expected_len} years ({planned['start_date'].year}-"
                        f"{planned['end_date'].year}) -- give exactly one value per year, in order."
                    )
        hypothetical_year = entry["decedent_death_date"].year if hypothetical_value is not None else None
        schedules.append({
            "account_number": str(account_number) if account_number is not None
                               else f"hypothetical:{beneficiary_name}:{hypothetical_year}",
            "kind": entry["type"],
            "schedule": schedule,
            "planned_withdrawal": planned,
            "beneficiary": beneficiary_name,
            "hypothetical_value": hypothetical_value,
            "hypothetical_year": hypothetical_year,
        })
    return schedules


def _parse_distribution_constraints(entry: dict, owner: str, error_prefix: str, valid_rates: set,
                                     policy_reserve_years, max_amount_field: str) -> dict:
    """Shared by build_roth_conversions() and build_ira_distributions() --
    both accept the same constraints shape (only where the money ends up
    differs). Returns {annual_amount, <max_amount_field>, max_marginal_bracket
    (a fraction, or None), avoid_irmaa_tier, preserve_cash_reserve_years}.
    See build_roth_conversions()'s docstring for what each constraint means
    -- error_prefix names the calling section (e.g. "roth_conversions
    entry") for clear error messages, and max_amount_field is that
    section's name for its flat per-year dollar cap (e.g.
    "max_conversion_per_year" vs. "max_distribution_per_year")."""
    constraints = entry.get("constraints") or {}
    annual_amount = entry.get("annual_amount")
    max_amount_per_year = constraints.get(max_amount_field)
    if annual_amount is None and max_amount_per_year is None:
        raise ProjectionError(
            f"{error_prefix} for '{owner}' needs either annual_amount or "
            f"constraints.{max_amount_field} to know how much to convert toward."
        )

    max_marginal_bracket = constraints.get("max_marginal_bracket")
    if max_marginal_bracket is not None:
        if isinstance(max_marginal_bracket, str):
            max_marginal_bracket = max_marginal_bracket.strip().rstrip("%")
        max_marginal_bracket = float(max_marginal_bracket) / 100
        if not any(abs(max_marginal_bracket - r) < 1e-9 for r in valid_rates):
            valid = ", ".join(f"{r*100:g}" for _, r in tax_mod.ORDINARY_BRACKETS_2024_MFJ)
            raise ProjectionError(
                f"{error_prefix} for '{owner}' has constraints.max_marginal_bracket="
                f"{constraints['max_marginal_bracket']}, which isn't one of this year's federal "
                f"bracket rates ({valid})."
            )

    avoid_irmaa_tier = constraints.get("avoid_irmaa_tier")
    if avoid_irmaa_tier is None and constraints.get("avoid_irmaa"):
        avoid_irmaa_tier = 0
    if avoid_irmaa_tier is not None and not (0 <= avoid_irmaa_tier < len(medicare_mod.IRMAA_TIERS_2024_MFJ)):
        raise ProjectionError(
            f"{error_prefix} for '{owner}' has constraints.avoid_irmaa_tier="
            f"{avoid_irmaa_tier}, which must be 0-{len(medicare_mod.IRMAA_TIERS_2024_MFJ) - 1}."
        )

    preserve_cash_reserve = constraints.get("preserve_cash_reserve")
    preserve_cash_reserve_years = None
    if isinstance(preserve_cash_reserve, bool):
        if preserve_cash_reserve:
            if policy_reserve_years is None:
                raise ProjectionError(
                    f"{error_prefix} for '{owner}' has constraints.preserve_cash_reserve: true, "
                    "but the scenario has no top-level cash_reserve.years policy set -- either add one "
                    "(cash_reserve: {years: N}) or set preserve_cash_reserve to a number of years directly."
                )
            preserve_cash_reserve_years = policy_reserve_years
    elif preserve_cash_reserve is not None:
        preserve_cash_reserve_years = preserve_cash_reserve

    return {
        "annual_amount": annual_amount,
        max_amount_field: max_amount_per_year,
        "max_marginal_bracket": max_marginal_bracket,
        "avoid_irmaa_tier": avoid_irmaa_tier,
        "preserve_cash_reserve_years": preserve_cash_reserve_years,
    }


def build_roth_conversions(scenario: dict, profile: dict) -> list:
    """Parses scenario.roth_conversions (optional list -- returns [] if
    absent). Each entry: owner (matches a people.primary/spouse name),
    start_date, end_date (the conversion window, by year), and EITHER a
    flat annual_amount OR a constraints dict (or both -- constraints, if
    present, always cap whatever annual_amount/max_conversion_per_year
    requests). annual_amount can also be a LIST of one dollar amount per
    year in the window (start_date's year through end_date's year,
    inclusive, in order) instead of a single flat number -- lets each
    year convert a different amount, still subject to whatever
    constraints are also configured. Mainly meant for a Roth-conversion
    optimizer to hand project() a specific per-year schedule (see
    roth_optimizer/) rather than something a household types by hand, but
    nothing stops you from doing that too. constraints (all optional, but
    at least one of annual_amount / constraints.max_conversion_per_year is
    required so there's some target to convert toward):
        max_conversion_per_year -- a flat dollar cap, in today's dollars
        max_marginal_bracket -- a percentage matching one of this year's
            federal ordinary brackets -- accepts either a bare number
            (24) or a percent string ("24%") -- caps the conversion so
            the marginal rate on it doesn't exceed that bracket (see
            tax.bracket_ceiling())
        avoid_irmaa_tier -- an integer 0-5 (see pt/medicare.py) -- caps
            the conversion so this year's MAGI doesn't spill into the next
            IRMAA tier up. A bare avoid_irmaa: true is also accepted as
            shorthand for avoid_irmaa_tier: 0 (never pay a surcharge at
            all); avoid_irmaa_tier takes precedence if both are given.
        preserve_cash_reserve -- caps the conversion so its OWN estimated
            tax cost doesn't draw the Taxable bucket below a reserve
            target -- either a number (years of spending to preserve,
            self-contained) or true (uses the scenario's top-level
            cash_reserve.years policy, see project() -- an error if that
            policy isn't set)
    Unrecognized keys under constraints are ignored (not an error) --
    e.g. a `sequence` hint isn't implemented yet; conversions always draw
    IRA-then-401(k), see ROTH_CONVERSION_SOURCE_TYPES.
    Returns a normalized list of {owner, start_year, end_year,
    annual_amount, max_conversion_per_year, max_marginal_bracket (a
    fraction, not a percentage, or None), avoid_irmaa_tier,
    preserve_cash_reserve_years (a resolved year count, or None if not
    set)}. The conversion itself draws from all of `owner`'s Traditional
    pools (IRA and 401(k)) -- see ROTH_CONVERSION_SOURCE_TYPES and
    project()."""
    people = _people(profile)
    valid_rates = {r for _, r in tax_mod.ORDINARY_BRACKETS_2024_MFJ}
    policy_reserve_years = scenario.get("cash_reserve", {}).get("years")
    conversions = []
    for entry in scenario.get("roth_conversions", []):
        owner = (entry.get("owner") or "").lower()
        if owner not in people:
            raise ProjectionError(
                f"roth_conversions entry has owner '{entry.get('owner')}', which doesn't "
                "match a people.primary/spouse name in the profile."
            )
        for field in ("start_date", "end_date"):
            if field not in entry:
                raise ProjectionError(f"roth_conversions entry for '{owner}' is missing '{field}'.")
        parsed = _parse_distribution_constraints(
            entry, owner, "roth_conversions entry", valid_rates, policy_reserve_years,
            max_amount_field="max_conversion_per_year",
        )
        start_year = entry["start_date"].year
        end_year = entry["end_date"].year
        if isinstance(parsed["annual_amount"], list):
            expected_len = end_year - start_year + 1
            if len(parsed["annual_amount"]) != expected_len:
                raise ProjectionError(
                    f"roth_conversions entry for '{owner}' has annual_amount as a list of "
                    f"{len(parsed['annual_amount'])} values, but its start_date/end_date window "
                    f"covers {expected_len} years ({start_year}-{end_year}) -- give exactly one "
                    "value per year, in order."
                )
        conversions.append({
            "owner": owner,
            "start_year": start_year,
            "end_year": end_year,
            **parsed,
        })
    return conversions


def build_ira_distributions(scenario: dict, profile: dict) -> list:
    """Parses scenario.ira_distributions (optional list -- returns [] if
    absent). Structurally identical to roth_conversions (owner, start_date,
    end_date, and EITHER a flat annual_amount OR constraints -- see
    build_roth_conversions()'s docstring for the full constraint semantics;
    here the flat per-year dollar cap is spelled
    constraints.max_distribution_per_year instead of
    constraints.max_conversion_per_year). The difference is where the money
    ends up: a Roth conversion moves Traditional money to Roth (changing
    its tax treatment, no cash to spend); an IRA distribution moves it to
    the TAXABLE bucket instead -- a plain Traditional withdrawal (ordinary
    income, same as an RMD), taken before it's needed for spending, e.g. to
    use up room in a lower tax bracket while it's available, or to build
    up liquid savings ahead of retirement. Returns a normalized list of
    {owner, start_year, end_year, annual_amount,
    max_distribution_per_year, max_marginal_bracket, avoid_irmaa_tier,
    preserve_cash_reserve_years} -- same draw order as a conversion (IRA
    pool first, then 401(k) -- see ROTH_CONVERSION_SOURCE_TYPES)."""
    people = _people(profile)
    valid_rates = {r for _, r in tax_mod.ORDINARY_BRACKETS_2024_MFJ}
    policy_reserve_years = scenario.get("cash_reserve", {}).get("years")
    distributions = []
    for entry in scenario.get("ira_distributions", []):
        owner = (entry.get("owner") or "").lower()
        if owner not in people:
            raise ProjectionError(
                f"ira_distributions entry has owner '{entry.get('owner')}', which doesn't "
                "match a people.primary/spouse name in the profile."
            )
        for field in ("start_date", "end_date"):
            if field not in entry:
                raise ProjectionError(f"ira_distributions entry for '{owner}' is missing '{field}'.")
        parsed = _parse_distribution_constraints(
            entry, owner, "ira_distributions entry", valid_rates, policy_reserve_years,
            max_amount_field="max_distribution_per_year",
        )
        distributions.append({
            "owner": owner,
            "start_year": entry["start_date"].year,
            "end_year": entry["end_date"].year,
            **parsed,
        })
    return distributions


def build_rollovers(scenario: dict, profile: dict) -> list:
    """Parses scenario.rollovers (optional list -- returns [] if absent).
    Each entry: owner (matches a people.primary/spouse name), date (a
    ONE-TIME event, by year -- unlike conversions/distributions, a rollover
    isn't a recurring window), and EITHER pct (0-100, percent of that
    owner's 401(k) pool, AFTER that year's growth, to roll over) OR amount
    (a flat dollar cap, in today's dollars, not inflated) -- defaults to
    pct: 100 if neither is given. Moves money from owner's 401(k) pool to
    their IRA pool -- the only direction modeled, since that's the
    real-world case (an employer 401(k) rolled into an IRA after leaving
    the job or at retirement). NOT a taxable event -- a direct
    401(k)-to-Traditional-IRA rollover doesn't trigger tax, unlike a Roth
    conversion. Returns a normalized list of {owner, year, pct, amount} --
    exactly one of pct/amount is not None."""
    people = _people(profile)
    rollovers = []
    for entry in scenario.get("rollovers", []):
        owner = (entry.get("owner") or "").lower()
        if owner not in people:
            raise ProjectionError(
                f"rollovers entry has owner '{entry.get('owner')}', which doesn't match a "
                "people.primary/spouse name in the profile."
            )
        if "date" not in entry:
            raise ProjectionError(f"rollovers entry for '{owner}' is missing 'date'.")
        amount = entry.get("amount")
        pct = entry.get("pct")
        if amount is not None and pct is not None:
            raise ProjectionError(f"rollovers entry for '{owner}' has both 'pct' and 'amount' -- use only one.")
        if amount is None and pct is None:
            pct = 100
        rollovers.append({"owner": owner, "year": entry["date"].year, "pct": pct, "amount": amount})
    return rollovers


def build_cash_flows(scenario: dict, profile: dict) -> list:
    """Parses scenario.taxable_cash_flows (optional list -- returns [] if
    absent). Each entry: date (a one-time event, by year), type ("deposit"
    or "withdrawal"), amount (positive, in today's dollars, not inflated --
    same convention as roth_conversions.annual_amount). A hypothetical
    one-time cash event applied DIRECTLY to the Taxable bucket -- a
    deposit adds new principal (e.g. an inheritance, home-sale proceeds
    already accounted for elsewhere), a withdrawal removes it (e.g. a
    one-time large purchase), floored at whatever the Taxable balance
    actually holds that year if a withdrawal exceeds it. Deliberately NOT
    run through the tax engine at all -- no capital-gains tax on a
    withdrawal, no cost basis added on a deposit -- if you want a
    withdrawal taxed like a normal spending withdrawal instead, adjust
    income.target_spending for that year. Returns a normalized list of
    {year, amount} -- signed: positive for a deposit, negative for a
    withdrawal."""
    flows = []
    for entry in scenario.get("taxable_cash_flows", []):
        if "date" not in entry:
            raise ProjectionError("taxable_cash_flows entry is missing 'date'.")
        flow_type = entry.get("type")
        if flow_type not in ("deposit", "withdrawal"):
            raise ProjectionError(
                f"taxable_cash_flows entry for {entry['date']} has type={flow_type!r}, must be "
                "'deposit' or 'withdrawal'."
            )
        if "amount" not in entry:
            raise ProjectionError(f"taxable_cash_flows entry for {entry['date']} is missing 'amount'.")
        amount = entry["amount"]
        flows.append({"year": entry["date"].year, "amount": amount if flow_type == "deposit" else -amount})
    return flows


def build_mortality(scenario: dict, profile: dict) -> dict:
    """Parses scenario.mortality (optional -- returns {} if absent). Two
    independently optional dates: primary_death_date and
    spouse_death_date (an error if the latter is given but the profile
    has no people.spouse). Returns {name: death_year}. See project()'s
    module docstring for what a configured death triggers each year AFTER
    it (spousal-rollover account merge, Social Security survivor
    treatment, a Single filing status for the survivor, that person no
    longer counting for Medicare eligibility) -- and, if EVERY person in
    the household ends up with a configured death date, the year the
    projection stops early and reports the estate's value (the LATER of
    the two dates, for a two-person household)."""
    people = _people(profile)
    primary_name = _get(profile, "people", "primary", "name", error_ctx="Profile").lower()
    spouse_name = next((n for n in people if n != primary_name), None)
    mortality_config = scenario.get("mortality", {})
    result = {}
    if "primary_death_date" in mortality_config:
        result[primary_name] = mortality_config["primary_death_date"].year
    if "spouse_death_date" in mortality_config:
        if not spouse_name:
            raise ProjectionError("scenario.mortality.spouse_death_date is set, but the profile has no spouse.")
        result[spouse_name] = mortality_config["spouse_death_date"].year
    return result


def _own_retirement_years(scenario: dict, profile: dict) -> dict:
    """{name: retirement_year} for EACH person individually (not the
    household's combined household_retirement_year, which is the LATER of
    the two) -- used by build_retirement_contributions() to know when each
    person's own contributions stop. A spouse with no spouse_retirement_age
    configured is assumed to retire at the same AGE as the primary (not
    the same year) -- a fallback used only here, since nothing else in the
    projection needs a spouse's own retirement year separately."""
    people = _people(profile)
    primary_name = _get(profile, "people", "primary", "name", error_ctx="Profile").lower()
    spouse_name = next((n for n in people if n != primary_name), None)
    retirement = scenario.get("retirement", {})
    primary_retirement_age = _get(scenario, "retirement", "retirement_age", error_ctx="Scenario")
    years = {primary_name: people[primary_name].year + primary_retirement_age}
    if spouse_name:
        spouse_retirement_age = retirement.get("spouse_retirement_age", primary_retirement_age)
        years[spouse_name] = people[spouse_name].year + spouse_retirement_age
    return years


def build_retirement_contributions(scenario: dict, profile: dict) -> list:
    """Parses scenario.retirement_contributions (optional list -- returns
    [] if absent). Each entry: owner (matches a people.primary/spouse
    name -- contributes to THAT person's own 401(k)/Roth pools), an
    optional roth_catchup_start_date (YYYY-MM-DD, only the year matters),
    an optional employer_match, and EITHER amount OR pct_of_max (at most
    one -- omit both to assume the legal max, the default). Every year
    from the start of the projection through the year BEFORE that owner's
    own retirement (see _own_retirement_years()):
      - the employee's own contribution for the year is: the legal max
        (elective deferral limit + whatever catch-up applies at that
        year's age -- see pt/contributions.py) by default; or, if
        amount is set, that flat dollar figure (today's dollars, inflated
        forward like a wage -- unlike a one-off event's dollar amount
        elsewhere in this scenario file); or, if pct_of_max is set, that
        percentage of the year's legal max -- in every case capped at the
        legal max itself. Given that total, the portion up to the
        elective deferral limit is "regular" and always goes to the
        Traditional pool; anything ABOVE the deferral limit is
        "catch-up" BY DEFINITION (the real IRS rule -- catch-up isn't
        about maxing out, it's whatever exceeds the base limit), and also
        goes to Traditional UNLESS roth_catchup_start_date is set and
        this year is on or after it, in which case SECURE 2.0's mandatory
        Roth catch-up rule applies and it goes to the Roth pool instead
        (a high earner's catch-up must be designated Roth -- omit
        roth_catchup_start_date entirely for someone never subject to
        this, e.g. because their wages stay under the threshold)
      - employer_match, if configured, ALWAYS lands in the Traditional
        pool (the standard real-world treatment -- employer contributions
        are pre-tax even when the employee's own catch-up is Roth), shown
        separately from the employee's own contribution (see the table's
        Employer Match column). EITHER amount (a flat dollar figure) OR
        BOTH pct_of_salary (a percentage) and salary must be given --
        unlike a one-off event's dollar amount elsewhere in this scenario
        file, employer_match.amount/salary are treated as recurring,
        wage-like figures and DO inflate forward with the scenario's
        general inflation rate (see project()), the same convention as
        income.target_spending and social_security.monthly_benefit.
    Returns a normalized list of {owner, until_year (the first year NOT
    contributed -- their own retirement year), roth_catchup_start_year
    (or None), amount (or None), pct_of_max (a fraction, or None),
    employer_match_amount (or None), employer_match_pct (a fraction, or
    None), employer_match_salary (or None)}."""
    people = _people(profile)
    own_retirement_years = _own_retirement_years(scenario, profile)
    contributions = []
    for entry in scenario.get("retirement_contributions", []):
        owner = (entry.get("owner") or "").lower()
        if owner not in people:
            raise ProjectionError(
                f"retirement_contributions entry has owner '{entry.get('owner')}', which doesn't "
                "match a people.primary/spouse name in the profile."
            )
        roth_catchup_start_year = None
        if "roth_catchup_start_date" in entry:
            roth_catchup_start_year = entry["roth_catchup_start_date"].year

        amount = entry.get("amount")
        pct_of_max = entry.get("pct_of_max")
        if amount is not None and pct_of_max is not None:
            raise ProjectionError(
                f"retirement_contributions entry for '{owner}' has both amount and pct_of_max -- use only one."
            )

        employer_match = entry.get("employer_match") or {}
        match_amount = employer_match.get("amount")
        match_pct = employer_match.get("pct_of_salary")
        match_salary = employer_match.get("salary")
        if match_amount is not None and match_pct is not None:
            raise ProjectionError(
                f"retirement_contributions entry for '{owner}' has both employer_match.amount and "
                "employer_match.pct_of_salary -- use only one."
            )
        if match_pct is not None and match_salary is None:
            raise ProjectionError(
                f"retirement_contributions entry for '{owner}' has employer_match.pct_of_salary but no "
                "employer_match.salary to apply it to."
            )
        if match_amount is None and match_pct is None and employer_match:
            raise ProjectionError(
                f"retirement_contributions entry for '{owner}' has an employer_match section but neither "
                "amount nor pct_of_salary -- nothing to compute."
            )

        contributions.append({
            "owner": owner,
            "until_year": own_retirement_years[owner],
            "roth_catchup_start_year": roth_catchup_start_year,
            "amount": amount,
            "pct_of_max": pct_of_max / 100 if pct_of_max is not None else None,
            "employer_match_amount": match_amount,
            "employer_match_pct": match_pct / 100 if match_pct is not None else None,
            "employer_match_salary": match_salary,
        })
    return contributions


def build_health_insurance(scenario: dict, profile: dict) -> list:
    """Parses scenario.health_insurance (optional dict keyed by a
    people.primary/spouse name -- returns [] if absent). Each entry:
    <name>.monthly_amount, an estimated cost (today's dollars) for a
    private health insurance plan covering the real gap between that
    person's OWN retirement and Medicare eligibility -- someone who
    retires before MEDICARE_ELIGIBILITY_AGE (65) needs to cover their own
    insurance until Medicare starts. Applied every year from that
    person's own retirement (see _own_retirement_years() -- NOT the
    household's combined household_retirement_year) through the year
    BEFORE they turn 65, inflated forward like a wage (the same
    convention as retirement_contributions.amount/employer_match -- a
    recurring cost, not a one-off scenario event). A person who retires
    at or after 65 has a zero-length window and no cost is ever applied
    for them, regardless of what's configured here; likewise, mortality
    (see build_mortality()) ends the cost the year after that person
    dies, same as it ends their Medicare eligibility.
    Returns a normalized list of {owner, monthly_amount, start_year (that
    owner's own retirement year)}."""
    people = _people(profile)
    own_retirement_years = _own_retirement_years(scenario, profile)
    entries = []
    for owner, info in (scenario.get("health_insurance") or {}).items():
        owner = owner.lower()
        if owner not in people:
            raise ProjectionError(
                f"health_insurance has an entry for '{owner}', which doesn't match a "
                "people.primary/spouse name in the profile."
            )
        monthly_amount = (info or {}).get("monthly_amount")
        if monthly_amount is None:
            raise ProjectionError(f"health_insurance.{owner} is missing monthly_amount.")
        entries.append({
            "owner": owner,
            "monthly_amount": monthly_amount,
            "start_year": own_retirement_years[owner],
        })
    return entries


def _amortized_annual_payment(principal: float, interest_rate: float, term_years: float) -> float:
    """The standard fixed monthly payment for a level-payment amortized
    loan (the same formula a mortgage/auto-loan calculator uses),
    returned as an ANNUAL figure (monthly * 12) to match this module's
    own year-granularity -- see build_liabilities(). interest_rate is a
    percentage (6.5 for 6.5% APR), not a fraction."""
    monthly_rate = interest_rate / 100 / 12
    n_payments = term_years * 12
    if monthly_rate == 0:
        monthly_payment = principal / n_payments
    else:
        monthly_payment = (
            principal * monthly_rate * (1 + monthly_rate) ** n_payments
            / ((1 + monthly_rate) ** n_payments - 1)
        )
    return monthly_payment * 12


def build_liabilities(scenario: dict, profile: dict) -> list:
    """Parses scenario.liabilities (optional list -- returns [] if
    absent). Each entry models a recurring, FIXED-dollar debt payment (a
    mortgage, car loan, or similar) -- unlike income.target_spending, the
    payment amount is a flat NOMINAL dollar figure that does NOT inflate
    forward (a fixed-rate loan payment doesn't grow with inflation -- if
    anything it gets cheaper in real terms over time), and unlike
    health_insurance/retirement_contributions, its window is a
    household-level [start_date, end_date] (by year), not tied to either
    person's own retirement.

    Optional name/type are for display only (the report's per-liability
    breakdown and Notes) -- they don't affect the math.

    The payment itself: EITHER a flat monthly_payment/annual_payment
    (give whichever is more natural -- exactly one of the two), OR the
    loan's own terms -- principal, interest_rate (a percentage, e.g. 6.5
    for 6.5%), and term_years -- which computes the standard fixed
    amortized payment itself (see _amortized_annual_payment()), so you
    don't have to already know the payment. Exactly one of
    {monthly_payment, annual_payment} or {principal, interest_rate,
    term_years} is required.

    end_date is optional when principal/interest_rate/term_years are
    given (defaults to a window of exactly term_years years starting at
    start_date -- e.g. a 5-year loan starting 2028 runs 2028-2032
    inclusive); it's REQUIRED when a flat payment is given directly,
    since there's nothing else to compute a payoff date from. An
    explicit end_date alongside loan terms is allowed too (e.g. to model
    an early payoff) -- term_years then only affects the computed
    payment amount, not how long the cash flow lasts in the projection.

    This only affects cash flow (added to cash_need, same pool as
    spending/health insurance/the advisory expense, drawn via the same
    taxable->Traditional->Roth withdrawal order) -- no amortization/
    interest-vs-principal split is tracked, there's no tax treatment
    (mortgage interest isn't deducted), and there's no separate
    liability-balance ledger reducing net worth -- the loan's cost is
    already fully captured through the cash it forces out of the
    portfolio while the window is open. A down payment or other one-time
    cost at origination isn't part of this section -- use
    scenario.taxable_cash_flows for that.

    Returns a normalized list of {name (for display/breakdown -- always
    a non-empty string), start_year, end_year, annual_payment}."""
    entries = []
    for i, entry in enumerate(scenario.get("liabilities", [])):
        label = entry.get("name") or entry.get("type") or f"liability_{i + 1}"
        start_date = entry.get("start_date")
        if start_date is None:
            raise ProjectionError(f"liabilities entry '{label}' is missing start_date.")

        monthly_payment = entry.get("monthly_payment")
        annual_payment = entry.get("annual_payment")
        principal = entry.get("principal")
        interest_rate = entry.get("interest_rate")
        term_years = entry.get("term_years")

        flat_given = monthly_payment is not None or annual_payment is not None
        loan_terms_given = principal is not None or interest_rate is not None or term_years is not None
        if flat_given and loan_terms_given:
            raise ProjectionError(
                f"liabilities entry '{label}' has both a flat payment (monthly_payment/"
                "annual_payment) and loan terms (principal/interest_rate/term_years) -- give "
                "exactly one way to determine the payment."
            )
        if not flat_given and not loan_terms_given:
            raise ProjectionError(
                f"liabilities entry '{label}' needs either monthly_payment/annual_payment, or "
                "principal + interest_rate + term_years, to know how much to pay."
            )

        if loan_terms_given:
            missing = [f for f in ("principal", "interest_rate", "term_years") if entry.get(f) is None]
            if missing:
                raise ProjectionError(
                    f"liabilities entry '{label}' is missing {', '.join(missing)} -- principal, "
                    "interest_rate, and term_years are all required together to compute the payment."
                )
            resolved_annual_payment = _amortized_annual_payment(principal, interest_rate, term_years)
            end_year = entry["end_date"].year if entry.get("end_date") else start_date.year + int(term_years) - 1
        else:
            if monthly_payment is not None and annual_payment is not None:
                raise ProjectionError(
                    f"liabilities entry '{label}' has both monthly_payment and annual_payment -- "
                    "give exactly one."
                )
            resolved_annual_payment = annual_payment if annual_payment is not None else monthly_payment * 12
            if entry.get("end_date") is None:
                raise ProjectionError(
                    f"liabilities entry '{label}' is missing end_date -- required when a flat "
                    "payment is given directly (there's nothing else to compute a payoff date from)."
                )
            end_year = entry["end_date"].year

        entries.append({
            "name": label,
            "start_year": start_date.year,
            "end_year": end_year,
            "annual_payment": resolved_annual_payment,
        })
    return entries


def build_pre_retirement_income(scenario: dict, profile: dict) -> list:
    """Parses scenario.pre_retirement_income (optional dict keyed by a
    people.primary/spouse name -- returns [] if absent). Each entry:
    <name>.estimated_gross_income, that person's OWN gross W-2 income
    (Box 1, "Wages, tips, other compensation") while still working --
    deliberately NOT total AGI, which can double-count income this tool
    already tracks separately (investment income from the taxable
    bucket), and NOT gross pay before payroll deductions, which would
    OVERSTATE real taxable wages (Box 1 already excludes pre-tax
    401(k)/HSA/etc. contributions, which is correct -- those really
    aren't taxable). Applied every year through the year BEFORE that
    person's own retirement (see _own_retirement_years() -- NOT the
    household's combined household_retirement_year), same window
    convention as retirement_contributions/health_insurance.

    estimated_gross_income is either a single flat number (today's
    dollars, inflated forward like a wage -- the common case), or a LIST
    of one already-nominal dollar amount per year (no further inflation
    applied, same convention as roth_conversions/ira_distributions/
    inherited_accounts' own annual_amount lists -- see
    build_roth_conversions()'s docstring) for a household with real,
    year-specific figures (e.g. an actual W-2 plus a one-time bonus
    year) that a flat growth rate can't represent. A list REQUIRES an
    explicit start_date (there's nothing else to anchor index 0 to) --
    one entry per year, start_date's year through the year before that
    person's own retirement, inclusive.

    This does NOT add wage income itself, or ANY tax attributable to wage
    income alone, to the household's own cash flow -- this tool still has
    no wage-income/payroll-tax model, and a household is assumed to cover
    that from its paycheck/withholding, entirely separately from the
    portfolio modeled here. It has two real effects, though: (1) it's
    added to the running ordinary-income/MAGI baseline
    (_constrained_distribution_amount()'s ordinary_income_so_far) that a
    Roth conversion's or IRA distribution's max_marginal_bracket/
    avoid_irmaa_tier constraint is evaluated against, and to that year's
    magi_history entry (for a LATER year's Medicare/IRMAA lookback) -- so
    a conversion started before retirement (nothing stops scenario.
    roth_conversions' start_date from being earlier than retirement_age)
    gets capped against the bracket/IRMAA tier the household ACTUALLY
    expects to be in that year, instead of one computed as if a working
    person had zero other income; (2) project()'s own tax_owed for that
    year is computed as an INCREMENT on top of it -- the tax on (pre-
    retirement income + the household's own RMD/conversion/distribution
    income) minus the tax on pre-retirement income alone -- so the
    household's own income is taxed (and its tax withdrawn from its own
    accounts) at the REAL marginal bracket it actually falls in once
    wages are accounted for, without ever billing the wage income's own
    tax to the portfolio. Without this section, a pre-retirement
    conversion's constraints are evaluated, and its tax computed, against
    RMD/conversion/Social-Security income only -- usually near zero
    before retirement -- which would both let a bracket/IRMAA-based
    constraint approve a much larger conversion than the household's real
    tax situation would allow, AND understate what it would really cost
    in tax.

    Returns a normalized list of {owner, estimated_gross_income,
    start_year (the list case's anchor year, or None for a flat number),
    end_year (that owner's own retirement year, exclusive -- applies
    through the year before it)}."""
    people = _people(profile)
    own_retirement_years = _own_retirement_years(scenario, profile)
    entries = []
    for owner, info in (scenario.get("pre_retirement_income") or {}).items():
        owner = owner.lower()
        if owner not in people:
            raise ProjectionError(
                f"pre_retirement_income has an entry for '{owner}', which doesn't match a "
                "people.primary/spouse name in the profile."
            )
        info = info or {}
        if "estimated_agi" in info:
            raise ProjectionError(
                f"pre_retirement_income.{owner} uses the old estimated_agi key, which is no longer "
                "accepted -- rename it to estimated_gross_income and use that person's gross W-2 income "
                "(Box 1, \"Wages, tips, other compensation\"), not AGI -- AGI can double-count investment "
                "income this tool already tracks separately via the taxable bucket. See README.md's "
                "\"Retirement scenario\" section for the full explanation."
            )
        estimated_gross_income = info.get("estimated_gross_income")
        if estimated_gross_income is None:
            raise ProjectionError(f"pre_retirement_income.{owner} is missing estimated_gross_income.")
        end_year = own_retirement_years[owner]
        start_year = None
        if isinstance(estimated_gross_income, list):
            start_date = info.get("start_date")
            if start_date is None:
                raise ProjectionError(
                    f"pre_retirement_income.{owner}.estimated_gross_income is a list, which needs a "
                    "start_date to anchor its first entry to a calendar year."
                )
            start_year = start_date.year
            expected_len = end_year - start_year
            if len(estimated_gross_income) != expected_len:
                raise ProjectionError(
                    f"pre_retirement_income.{owner}.estimated_gross_income has "
                    f"{len(estimated_gross_income)} values, but its start_date ({start_year}) through "
                    f"the year before {owner}'s own retirement ({end_year - 1}) covers {expected_len} "
                    "years -- give exactly one value per year, in order."
                )
        entries.append({
            "owner": owner,
            "estimated_gross_income": estimated_gross_income,
            "start_year": start_year,
            "end_year": end_year,
        })
    return entries


def _pre_retirement_income_for_year(entry: dict, year: int, years_from_start: int, inflation_rate: float) -> float:
    """This entry's (see build_pre_retirement_income()) estimated gross
    income for `year` -- the flat-number case inflates forward from
    today like a wage; the list case (entry["start_year"] is not None)
    looks up that exact year's already-nominal value directly, no further
    inflation applied (same convention as roth_conversions/
    ira_distributions/inherited_accounts' own annual_amount lists)."""
    estimated_gross_income = entry["estimated_gross_income"]
    if entry["start_year"] is None:
        return estimated_gross_income * (1 + inflation_rate) ** years_from_start
    idx = year - entry["start_year"]
    return estimated_gross_income[idx] if 0 <= idx < len(estimated_gross_income) else 0.0


def _constrained_distribution_amount(dist: dict, cap_field: str, flat: dict, ss_income: float,
                                      ordinary_income_so_far: float, anticipated_ltcg: float,
                                      target_spending: float, inflation_rate: float,
                                      medical_inflation_rate: float, year: int, years_from_start: int,
                                      filing_status: str = "mfj") -> float:
    """Shared by project()'s Roth-conversion and IRA-distribution steps --
    both draw from a person's Traditional pools under the same constraint
    rules (see build_roth_conversions()'s docstring for what each one
    means), only where the money ends up differs, which the caller handles
    itself. Returns the (still source-uncapped -- the caller checks actual
    pool balances) dollar amount this year's constraints allow for `dist`,
    a normalized entry from build_roth_conversions()/
    build_ira_distributions() -- cap_field names that entry's flat
    per-year dollar field ("max_conversion_per_year" or
    "max_distribution_per_year"). filing_status ("mfj" or "single") --
    see project()'s mortality handling -- picks which year's brackets the
    bracket/rate-based constraints use."""
    requested = dist["annual_amount"]
    if isinstance(requested, list):
        # A per-year schedule (see build_roth_conversions()'s docstring) --
        # dist["start_year"] is this entry's own window start, already
        # validated to have exactly one entry per year in the window.
        idx = year - dist["start_year"]
        requested = requested[idx] if 0 <= idx < len(requested) else 0.0
    if requested is None:
        requested = dist[cap_field]
    elif dist[cap_field] is not None:
        requested = min(requested, dist[cap_field])
    cap = requested

    taxable_ss_estimate = tax_mod.social_security_taxable_amount(
        ss_income, other_income=ordinary_income_so_far, filing_status=filing_status
    )
    if dist["max_marginal_bracket"] is not None:
        # ceiling is a TAXABLE (post-standard-deduction) threshold, but
        # ordinary_income_so_far/taxable_ss_estimate are GROSS. Solving
        # taxable_income(gross_before + X) <= ceiling for X directly --
        # rather than flooring gross_before into a taxable figure first via
        # taxable_income() and subtracting from ceiling -- avoids
        # under-allowing by the standard deduction whenever gross_before is
        # still below it (i.e. the deduction hasn't been "used" yet): that
        # floor silently discards how much of the deduction remains, so a
        # second entry evaluated later in the same year (once
        # ordinary_income_so_far reflects the first entry's amount) would
        # get to "reclaim" it, letting the two entries' combined cap exceed
        # the household's single, once-per-year standard deduction.
        ceiling = tax_mod.bracket_ceiling(dist["max_marginal_bracket"], inflation_rate, year, filing_status)
        gross_ordinary_before = ordinary_income_so_far + taxable_ss_estimate
        deduction = tax_mod.inflated_standard_deduction(inflation_rate, year, filing_status)
        cap = min(cap, max(0.0, ceiling + deduction - gross_ordinary_before))

    if dist["avoid_irmaa_tier"] is not None:
        # No analogous gross/taxable mismatch here: MAGI never has the
        # standard deduction subtracted from it (real IRMAA MAGI is AGI +
        # tax-exempt interest, pre-deduction), and tier_magi_ceiling() is on
        # that same gross MAGI scale (matching magi_history's own gross
        # entries below) -- so baseline_magi and magi_ceiling are already
        # apples-to-apples without going through taxable_income().
        baseline_magi = ordinary_income_so_far + taxable_ss_estimate + anticipated_ltcg
        magi_ceiling = medicare_mod.tier_magi_ceiling(dist["avoid_irmaa_tier"], medical_inflation_rate, year)
        cap = min(cap, max(0.0, magi_ceiling - baseline_magi))

    if dist["preserve_cash_reserve_years"]:
        reserve_target = sum(
            target_spending * (1 + inflation_rate) ** (years_from_start + k)
            for k in range(int(dist["preserve_cash_reserve_years"]))
        )
        cushion = max(0.0, flat["taxable"] - reserve_target)
        marginal_rate = tax_mod.marginal_ordinary_rate(
            ordinary_income_so_far + taxable_ss_estimate, inflation_rate, year, filing_status
        )
        if marginal_rate > 0:
            cap = min(cap, cushion / marginal_rate)

    return max(0.0, cap)


def _household_tax_owed(pre_retirement_income_total: float, ordinary_income: float, taxable_ss: float,
                         ltcg_income: float, ss_income: float, inflation_rate: float, year: int,
                         filing_status: str = "mfj") -> float:
    """project()'s step 5 -- the federal tax withdrawn from the
    household's OWN accounts this year, on ordinary_income (RMDs, Roth
    conversions, IRA distributions, any discretionary Traditional
    withdrawal -- see build_pre_retirement_income() for why
    pre_retirement_income_total, this year's estimated wage income for
    anyone still working, ISN'T part of ordinary_income itself) plus
    taxable_ss and ltcg_income.

    Deliberately computed as an INCREMENT: the tax on everything
    (pre_retirement_income_total + the household's own income) minus the
    tax on pre_retirement_income_total ALONE -- rather than taxing the
    household's own income in isolation, which is what this looked like
    before pre_retirement_income_total existed. This still never bills
    the wage income's own tax to the portfolio (a household is assumed to
    cover that from its paycheck/withholding, same as always), but it
    DOES place the household's own income at the correct, real marginal
    bracket -- higher than the isolated calculation whenever a
    conversion/distribution/RMD is stacked on top of real wages, e.g. a
    large pre-retirement conversion crossing from the 24% into the
    32%/35% bracket once wages are properly accounted for.

    When pre_retirement_income_total is 0 (the common case, and every
    year after each owner's own retirement), the subtracted term is 0 and
    this is identical to taxing ordinary_income + taxable_ss + ltcg_income
    directly, exactly as before this existed."""
    full_tax = tax_mod.federal_tax(
        pre_retirement_income_total + ordinary_income + taxable_ss, ltcg_income,
        inflation_rate, year, filing_status,
    )
    if pre_retirement_income_total <= 0.0:
        return max(0.0, full_tax)
    pre_retirement_taxable_ss = tax_mod.social_security_taxable_amount(
        ss_income, other_income=pre_retirement_income_total, filing_status=filing_status
    )
    pre_retirement_only_tax = tax_mod.federal_tax(
        pre_retirement_income_total + pre_retirement_taxable_ss, 0.0, inflation_rate, year, filing_status
    )
    return max(0.0, full_tax - pre_retirement_only_tax)


def _planned_withdrawal_floor(planned: dict, year: int, balance: float) -> float:
    """The household's own planned withdrawal amount for this inherited
    account in `year`, or 0.0 if there's no planned_withdrawal configured or
    `year` falls outside its [start_date, end_date] window (by year -- the
    day/month don't matter here, everything else in this model is annual).
    This is a FLOOR, not a replacement: the caller takes
    max(required_distribution, this), since the legally-required minimum
    still applies even if it happens to exceed what was planned.

    annual_amount can also be a LIST of one dollar amount per year in the
    window (start_date's year through end_date's year, inclusive, in
    order) instead of a single flat number -- same convention as
    roth_conversions/ira_distributions' own annual_amount (see
    build_roth_conversions()'s docstring) -- mainly meant for
    roth_optimizer to hand project() a specific per-year drawdown
    sequence (see roth_optimizer/) rather than something a household
    types by hand."""
    if not planned:
        return 0.0
    if not (planned["start_date"].year <= year <= planned["end_date"].year):
        return 0.0
    amount = planned["annual_amount"]
    if isinstance(amount, list):
        idx = year - planned["start_date"].year
        amount = amount[idx] if 0 <= idx < len(amount) else 0.0
    return min(balance, amount)


def build_buckets(account_rows: list, inherited_schedules: list, profile: dict) -> dict:
    """Sorts current account balances (allocation.account_values() rows)
    into buckets. Returns {"taxable": float, "traditional": {name:
    {account_type: float}}, "roth": {name: float}, "inherited":
    [{"account_number", "kind", "schedule", "balance"}, ...]}.

    Traditional is grouped by (owner, account_type) -- e.g. all of one
    person's IRAs combine into one "IRA" pool, all their 401(k)s into a
    separate "401(k)" pool -- rather than one pool per owner covering every
    Traditional account type. This keeps each type's balance exact in
    --verbose (see build_account_columns()) even though a Roth conversion
    (see build_roth_conversions()) can still draw from either pool. Roth
    stays pooled per owner across account type (a conversion just needs *a*
    Roth account for that owner to land in, not a specific one)."""
    people = _people(profile)
    inherited_numbers = {s["account_number"] for s in inherited_schedules}
    inherited_by_number = {s["account_number"]: dict(s, balance=0.0) for s in inherited_schedules}

    taxable = 0.0
    traditional = {name: {t: 0.0 for t in TRADITIONAL_ACCOUNT_TYPES} for name in people}
    roth = {name: 0.0 for name in people}

    for row in account_rows:
        value = row["value"] or 0.0
        key = _bucket_key_for_account(row, people, inherited_numbers)
        if key.startswith("inherited:"):
            inherited_by_number[key.split(":", 1)[1]]["balance"] += value
        elif key == "taxable":
            taxable += value
        elif key.startswith("roth:"):
            roth[key.split(":", 1)[1]] += value
        else:  # "traditional:<name>:<account_type>"
            _, name, account_type = key.split(":", 2)
            traditional[name][account_type] += value

    return {"taxable": taxable, "traditional": traditional, "roth": roth,
            "inherited": list(inherited_by_number.values())}


def _bucket_key_for_account(row: dict, people: dict, inherited_numbers: set) -> str:
    """Which bucket a real account's balance belongs to: "taxable",
    "roth:<name>", "traditional:<name>:<account_type>", or
    "inherited:<account_number>". Shared by build_buckets() (the
    simulation) and build_account_columns() (verbose per-account display),
    so they can never disagree about where an account's money goes."""
    account_number = str(row["account_number"])
    if account_number in inherited_numbers:
        return f"inherited:{account_number}"
    account_type = row["account_type"]
    owner = (row["owner"] or "").lower()
    if account_type in TAXABLE_ACCOUNT_TYPES:
        return "taxable"
    if account_type in TRADITIONAL_ACCOUNT_TYPES:
        return f"traditional:{owner}:{account_type}" if owner in people else "taxable"  # unrecognized owner -- can't track RMDs
    if account_type in ROTH_ACCOUNT_TYPES:
        return f"roth:{owner}" if owner in people else "taxable"  # unrecognized owner -- can't target conversions
    # Inherited-but-not-in-scenario, HSA, or anything else not specially
    # modeled -- see module docstring.
    return "taxable"


def bucket_allocation_rows(class_rows: list, inherited_schedules: list, profile: dict) -> dict:
    """Groups a per-account, per-asset-class breakdown (allocation.
    allocation_by_account()'s shape for a real household, or
    build_hypothetical_account_class_rows()'s for a hypothetical one) into
    per-TAX-BUCKET allocation, using the exact same bucket_key convention
    build_buckets()/_bucket_key_for_account() use ("taxable",
    "traditional:<owner>:<account_type>", "roth:<owner>",
    "inherited:<account_number>") -- so a PER-BUCKET growth rate (see
    `project`/`monte-carlo --per-bucket-growth`, cmd_project()/cmd_monte_
    carlo() in pt/cli.py) reflects only what THAT bucket actually holds --
    e.g. a Traditional 401(k) invested more conservatively than a Roth IRA
    blends to its own lower rate, instead of one household-wide number
    applied to both.

    Returns {bucket_key: (allocation_rows, total)} -- each pair in the
    exact shape allocation.allocation_by_asset_class() returns, so
    compute_blended_growth_rate()/compute_blended_volatility()/
    build_correlated_return_sampler() all work on a bucket's rows
    completely unchanged. A bucket with no matching accounts (e.g. a
    Traditional pool a person hasn't opened) simply isn't a key here --
    callers fall back to the household-wide blended rate for those."""
    people = _people(profile)
    inherited_numbers = {s["account_number"] for s in inherited_schedules}
    class_totals_by_bucket = {}
    for row in class_rows:
        key = _bucket_key_for_account(row, people, inherited_numbers)
        bucket = class_totals_by_bucket.setdefault(key, {})
        bucket[row["asset_class"]] = bucket.get(row["asset_class"], 0.0) + (row["value"] or 0.0)
    result = {}
    for key, class_totals in class_totals_by_bucket.items():
        total = sum(class_totals.values())
        allocation_rows = [
            {"asset_class": ac, "value": v, "pct": (v / total if total else 0.0)}
            for ac, v in class_totals.items()
        ]
        result[key] = (allocation_rows, total)
    return result


def bucket_label(bucket_key: str) -> str:
    """A human-readable label for a bucket_key (see
    _bucket_key_for_account()) -- "Taxable", "Traditional (Alex:
    401(k))", "Roth (Alex)", "Inherited (...1234)" -- for the
    --per-bucket-growth breakdown in format_table()/format_monte_carlo_
    table() and the report's MODEL PARAMETERS block. Falls back to the
    raw bucket_key for anything unrecognized (shouldn't happen with a
    bucket_key this module produced itself)."""
    if bucket_key == "taxable":
        return "Taxable"
    parts = bucket_key.split(":")
    if len(parts) == 2 and parts[0] == "roth":
        return f"Roth ({parts[1].capitalize()})"
    if len(parts) == 2 and parts[0] == "inherited":
        return f"Inherited (...{parts[1][-4:]})"
    if len(parts) == 3 and parts[0] == "traditional":
        return f"Traditional ({parts[1].capitalize()}: {parts[2]})"
    return bucket_key


VERBOSE_ACCOUNT_THRESHOLD = 500.0

ACCOUNT_TYPE_LABELS = {
    "Brokerage": "Brok", "IRA": "IRA", "401(k)": "401k", "Roth IRA": "Roth",
    "Inherited IRA": "InhIRA", "Inherited Roth IRA": "InhRoth", "HSA": "HSA",
}


def build_account_columns(account_rows: list, inherited_schedules: list, profile: dict,
                           threshold: float = VERBOSE_ACCOUNT_THRESHOLD) -> list:
    """For --verbose display: one entry per real account whose CURRENT
    balance exceeds `threshold` -- except a Roth IRA, which always gets a
    column regardless of its current balance, since it's the destination of
    a Roth conversion (see build_roth_conversions()) and can go from near-$0
    to substantial over the projection even though it starts under
    threshold. A hypothetical inherited account (see
    build_inherited_schedules()) also always gets a column, for the same
    reason -- it starts at $0 (it doesn't exist yet) and can become
    substantial once its inheritance year arrives. Each entry:
    account_number, label (a compact column header), bucket_key, share,
    current_value.

    Inherited accounts are already simulated individually (share=1.0,
    exact -- see build_buckets()). Traditional is pooled per (owner,
    account_type) -- e.g. "alex:IRA" -- so if someone holds just one IRA
    and one 401(k), each gets its own exact pool too; only a person with
    *multiple* IRAs (or multiple 401(k)s) sees those split back into a
    DERIVED share of their shared pool. Taxable and Roth are always pooled
    (across every account of that kind, any/that owner respectively), so
    their columns are always a derived share: an account's fixed starting
    percentage of its bucket's current total, scaled by that bucket's
    simulated balance every year -- assumes every account in the bucket
    grows and gets drawn down in exact proportion to the bucket average, a
    presentation aid, not an independently modeled quantity."""
    people = _people(profile)
    inherited_numbers = {s["account_number"] for s in inherited_schedules}

    bucket_totals = {}
    for row in account_rows:
        key = _bucket_key_for_account(row, people, inherited_numbers)
        bucket_totals[key] = bucket_totals.get(key, 0.0) + (row["value"] or 0.0)

    columns = []
    for row in account_rows:
        value = row["value"] or 0.0
        key = _bucket_key_for_account(row, people, inherited_numbers)
        if value <= threshold and not key.startswith("roth:"):
            continue
        share = 1.0 if key.startswith("inherited:") else (value / bucket_totals[key] if bucket_totals[key] else 0.0)
        owner_initial = (row["owner"] or "?")[:1].upper()
        type_label = ACCOUNT_TYPE_LABELS.get(row["account_type"], row["account_type"][:6])
        columns.append({
            "account_number": str(row["account_number"]),
            "label": f"{owner_initial}:{type_label}-{str(row['account_number'])[-4:]}",
            "bucket_key": key,
            "share": share,
            "current_value": value,
        })

    for s in inherited_schedules:
        if s["hypothetical_value"] is None:
            continue
        owner_initial = (s["beneficiary"] or "?")[:1].upper()
        type_label = "HypIRA" if s["kind"] == "traditional" else "HypRoth"
        columns.append({
            "account_number": s["account_number"],
            "label": f"{owner_initial}:{type_label}-{s['hypothetical_year']}",
            "bucket_key": f"inherited:{s['account_number']}",
            "share": 1.0,
            "current_value": 0.0,
        })

    columns.sort(key=lambda c: -c["current_value"])
    return columns


def _withdraw(needed: float, buckets: dict, order: list) -> tuple:
    """Withdraws `needed` from buckets (a flat {key: balance} dict, mutated
    in place) in `order`. Returns ({key: amount_withdrawn}, shortfall)."""
    withdrawals = {}
    remaining = needed
    for key in order:
        if remaining <= 1e-9:
            break
        available = buckets.get(key, 0.0)
        take = min(available, remaining)
        if take > 0:
            buckets[key] -= take
            withdrawals[key] = take
            remaining -= take
    return withdrawals, max(0.0, remaining)


def _resolve_social_security_config(ss_config: dict, people: dict) -> dict:
    """Returns a NEW {name: {...}} dict (same shape as scenario.
    social_security) with each entry's monthly_benefit RESOLVED --
    either used as-is (a flat monthly_benefit, unchanged -- the tool's
    original convention: you already know the benefit AT your chosen
    claim_age, from ssa.gov's own calculator or a statement), or computed
    from pia_monthly (the Primary Insurance Amount -- the benefit
    claiming EXACTLY at Full Retirement Age would pay) via
    social_security.benefit_at_claim_age(), using that person's own
    birth year and claim_age. Exactly one of monthly_benefit/pia_monthly
    is required if an entry gives either at all; an entry giving NEITHER
    is passed through unchanged (still missing monthly_benefit) --
    project()'s own social-security step already skips an entry missing
    it, the same lenient behavior this had before pia_monthly existed.

    Raises ProjectionError if an entry gives BOTH monthly_benefit and
    pia_monthly (ambiguous -- pick one), gives pia_monthly without a
    claim_age (needed to resolve it), or gives pia_monthly for a name
    that doesn't match a people.primary/spouse (needed for their birth
    year)."""
    resolved = {}
    for name, info in ss_config.items():
        info = dict(info)
        has_flat = "monthly_benefit" in info
        has_pia = "pia_monthly" in info
        if has_flat and has_pia:
            raise ProjectionError(
                f"social_security entry for '{name}' has both monthly_benefit and pia_monthly -- give "
                "exactly one: monthly_benefit if you already know the benefit AT this claim_age (e.g. "
                "from ssa.gov's own calculator or a statement), or pia_monthly (the Primary Insurance "
                "Amount -- the benefit at full retirement age) to have the claim-age adjustment computed "
                "for you."
            )
        if has_pia:
            lname = name.lower()
            if lname not in people:
                raise ProjectionError(
                    f"social_security entry for '{name}' has pia_monthly, which needs a matching "
                    "people.primary/spouse name in the profile to know their birth year."
                )
            if "claim_age" not in info:
                raise ProjectionError(f"social_security entry for '{name}' has pia_monthly but no claim_age.")
            try:
                info["monthly_benefit"] = social_security_mod.benefit_at_claim_age(
                    info["pia_monthly"], people[lname].year, info["claim_age"]
                )
            except ValueError as e:
                raise ProjectionError(f"social_security entry for '{name}': {e}")
        resolved[name] = info
    return resolved


def project(profile: dict, scenario: dict, account_rows: list, blended_rate: float,
            start_year: int = None, account_columns: list = None,
            volatility: float = None, rng=None, year_rate_fn=None,
            bucket_rates: dict = None, bucket_rate_fn=None) -> list:
    """Returns a list of yearly rows from start_year (default: the current
    year) through the primary person's age-100 year. Raises ProjectionError
    if the profile/scenario is missing something required.

    account_columns (optional, from build_account_columns()): if given,
    each row also gets an "accounts" dict of {account_number: value} for
    that year -- see build_account_columns()'s docstring for what those
    values mean (exact for inherited accounts, a derived proportional share
    of its bucket for everything else).

    volatility (optional, a percentage -- see compute_blended_volatility()):
    if given (and non-zero), each year's actual growth rate is drawn
    independently from a Normal(blended_rate, volatility/100) distribution
    instead of using blended_rate itself every year. rng (optional): an
    object with a .gauss(mu, sigma) method (e.g. a seeded random.Random(),
    for reproducibility) -- defaults to the `random` module's own top-level
    functions (unseeded) if volatility is set but rng isn't. Ignored
    entirely (blended_rate used exactly, today's behavior) if volatility is
    None or 0.

    year_rate_fn (optional, a zero-argument callable returning a fraction):
    if given, called once per simulated year INSTEAD of the
    volatility/rng single-blended-Normal draw above -- see
    run_monte_carlo()/build_correlated_return_sampler(), the only normal
    caller, which uses this to draw a properly correlated per-asset-class
    return each year rather than one Normal draw for the whole portfolio.
    Takes priority over volatility/rng if both are somehow given.

    bucket_rates (optional, {bucket_key: fraction}) / bucket_rate_fn
    (optional, a zero-argument callable returning {bucket_key: fraction}
    for one year -- see run_monte_carlo()/build_bucket_correlated_return_
    sampler()): PER-TAX-BUCKET growth instead of one rate for the whole
    portfolio -- bucket_key matches _bucket_key_for_account()'s convention
    ("taxable", "traditional:<owner>:<account_type>", "roth:<owner>",
    "inherited:<account_number>"). bucket_rate_fn takes priority over
    bucket_rates (deterministic vs. Monte Carlo, same relationship as
    year_rate_fn vs. volatility above); either takes priority over
    year_rate_fn/volatility/blended_rate for any bucket_key it covers -- a
    bucket_key NOT covered (e.g. a hypothetical future inheritance with no
    asset mix of its own yet) still falls back to the single blended
    rate/draw that year, exactly as if bucket mode weren't used at all.

    Either way the realized rate is floored at -100% per bucket (a bucket
    can't lose more than everything in one year from growth alone).
    Ignored entirely (blended_rate used exactly, one rate for the whole
    portfolio) if NONE of year_rate_fn/volatility/bucket_rates/
    bucket_rate_fn are given -- every pre-existing caller (roth_optimizer,
    scenario_engine, a plain `pt.cli project`) passes none of them and is
    completely unaffected."""
    people = _people(profile)
    primary_name = _get(profile, "people", "primary", "name", error_ctx="Profile").lower()
    primary_birthdate = people[primary_name]
    spouse_name = next((n for n in people if n != primary_name), None)

    retirement = scenario.get("retirement", {})
    primary_retirement_age = _get(scenario, "retirement", "retirement_age", error_ctx="Scenario")
    household_retirement_year = primary_birthdate.year + primary_retirement_age
    if spouse_name and "spouse_retirement_age" in retirement:
        spouse_retirement_year = people[spouse_name].year + retirement["spouse_retirement_age"]
        household_retirement_year = max(household_retirement_year, spouse_retirement_year)

    target_spending = _get(scenario, "income", "target_spending", error_ctx="Scenario")
    inflation_rate = _get(scenario, "inflation", "general", error_ctx="Scenario") / 100
    # A single global advisory/AUM fee (optional, scenario.advisory_expense
    # -- see _parse_advisory_expense_rate()) -- the ONLY advisory expense
    # modeled here; per-account/per-symbol expense ratios stay
    # informational-only, reported by pt.cli report, never here.
    blended_expense_rate = _parse_advisory_expense_rate(scenario)
    # Medicare costs have historically outpaced general CPI -- an optional
    # separate rate, falling back to general inflation if not set.
    medical_inflation = scenario.get("inflation", {}).get("medical")
    medical_inflation_rate = medical_inflation / 100 if medical_inflation is not None else inflation_rate

    ss_config = _resolve_social_security_config(scenario.get("social_security", {}), people)

    inherited_schedules = build_inherited_schedules(scenario, profile)
    buckets = build_buckets(account_rows, inherited_schedules, profile)
    roth_conversions = build_roth_conversions(scenario, profile)
    ira_distributions = build_ira_distributions(scenario, profile)
    rollovers = build_rollovers(scenario, profile)
    cash_flows = build_cash_flows(scenario, profile)
    retirement_contributions = build_retirement_contributions(scenario, profile)
    health_insurance = build_health_insurance(scenario, profile)
    liabilities = build_liabilities(scenario, profile)
    pre_retirement_income = build_pre_retirement_income(scenario, profile)
    death_years = build_mortality(scenario, profile)
    # If EVERY person in the household has a configured death date, the
    # projection stops after processing the LATER of them (a single-person
    # household stops right at its own person's death) -- see step 0 and
    # the end of the loop below.
    estate_stop_year = max(death_years.values()) if set(death_years) == set(people) and death_years else None
    merged_on_death = set()

    start_year = start_year or datetime.now().year
    end_year = primary_birthdate.year + 100
    traditional_types = sorted(TRADITIONAL_ACCOUNT_TYPES)
    withdrawal_order = (
        ["taxable"]
        + [f"traditional:{n}:{t}" for n in people for t in traditional_types]
        + [f"roth:{n}" for n in people]
    )

    rows = []
    cumulative_tax_in_retirement = 0.0
    # Running total of Medicare Part B + Part D IRMAA (the only healthcare
    # cost this tool tracks -- see the "Cumulative Healthcare Expense"
    # column and "Documented simplifications" for what this doesn't cover),
    # from the start of the projection -- unlike cumulative_tax_in_retirement,
    # this isn't gated on retired, since Medicare eligibility can arrive
    # before retirement.
    cumulative_healthcare_expense = 0.0
    # This household's MAGI each year, in order -- IRMAA_LOOKBACK_YEARS
    # entries behind is what sets that year's Medicare premium (see step 2c
    # below and pt/medicare.py's module docstring).
    magi_history = []
    for year in range(start_year, end_year + 1):
        years_from_start = year - start_year
        ages = {name: year - bd.year for name, bd in people.items()}
        retired = year >= household_retirement_year

        # A person is treated as alive THROUGH their death year (matching
        # real tax rules -- MFJ still applies for the year of death itself,
        # RMDs for that year are still required) -- everything death
        # triggers (account merge, Social Security survivor treatment, a
        # Single filing status, Medicare eligibility ending) starts the
        # FOLLOWING year. See build_mortality()/module docstring.
        alive = {name: not (name in death_years and year > death_years[name]) for name in people}
        alive_count = sum(alive.values())
        # Filing status only ever changes for a two-person household that
        # loses exactly one spouse -- a single-person household (or an
        # already-both-deceased one, which the loop wouldn't reach --
        # see the stop check at the end of this loop) keeps "mfj", matching
        # this tool's pre-existing single-filer gap that isn't addressed
        # here (see module docstring).
        filing_status = "single" if (spouse_name and alive_count == 1) else "mfj"

        # --- 0. A person who just died (as of THIS year, not before) has
        # their Traditional/Roth pools merged into their surviving spouse's
        # own pools -- a real-world spousal rollover, which (unlike a
        # non-spouse inheritance) isn't subject to the SECURE Act 10-year
        # rule, so no separate schedule is needed; they just become the
        # survivor's own money. Hypothetical inherited accounts "appear"
        # the year they're configured to be inherited (see
        # build_inherited_schedules()), and hypothetical one-time Taxable
        # cash flows (see build_cash_flows()) land -- all of this BEFORE
        # this year's growth (step 1), so it participates in it like any
        # other balance would.
        survivor_name = next((n for n in people if alive[n]), None)
        for name in people:
            if not alive[name] and name not in merged_on_death:
                merged_on_death.add(name)
                if survivor_name and survivor_name != name:
                    for t, bal in buckets["traditional"][name].items():
                        buckets["traditional"][survivor_name][t] += bal
                        buckets["traditional"][name][t] = 0.0
                    buckets["roth"][survivor_name] += buckets["roth"][name]
                    buckets["roth"][name] = 0.0
        for inh in buckets["inherited"]:
            if inh["hypothetical_year"] == year:
                inh["balance"] += inh["hypothetical_value"]
        for flow in cash_flows:
            if flow["year"] == year:
                buckets["taxable"] = max(0.0, buckets["taxable"] + flow["amount"])
        # 401(k) employee contributions (see build_retirement_contributions()
        # / pt/contributions.py) -- new money from outside the model (this
        # year's paycheck), same as a taxable_cash_flows deposit, not drawn
        # from any other bucket. The regular deferral always lands in the
        # Traditional 401(k) pool; the catch-up portion does too, UNLESS
        # roth_catchup_start_year has arrived, in which case SECURE 2.0's
        # mandatory Roth catch-up rule sends it to the Roth pool instead.
        # Neither is a taxable event here -- this tool doesn't model wage
        # income/payroll tax at all (see "Documented simplifications"), so
        # there's no income to reduce for the pre-tax portion and no
        # already-taxed income to reconcile for the Roth portion.
        # Employer match (if configured) always lands in the Traditional
        # pool -- the standard real-world treatment, even for an owner
        # whose own catch-up is Roth -- shown separately in its own
        # column. amount/salary are recurring, wage-like figures, so
        # (unlike a one-off event elsewhere in this scenario file) they DO
        # inflate forward with general inflation.
        contribution_traditional = 0.0
        contribution_roth = 0.0
        after_tax_roth_total = 0.0
        employer_match_total = 0.0
        for contrib in retirement_contributions:
            if year >= contrib["until_year"]:
                continue
            owner = contrib["owner"]
            deferral_limit = contributions_mod.elective_deferral_limit(inflation_rate, year)
            catchup_limit = contributions_mod.catchup_limit(ages[owner], inflation_rate, year)
            max_total = deferral_limit + catchup_limit
            if contrib["amount"] is not None:
                requested = contrib["amount"] * (1 + inflation_rate) ** years_from_start
            elif contrib["pct_of_max"] is not None:
                requested = max_total * contrib["pct_of_max"]
            else:
                requested = max_total
            requested = max(0.0, requested)
            # Anything ABOVE the legal elective-deferral+catch-up max isn't
            # capped away -- it's modeled as an after-tax contribution
            # immediately converted to Roth (the "mega backdoor Roth"
            # strategy -- see build_retirement_contributions()), tracked
            # separately from the SECURE 2.0 catch-up Roth designation
            # since it's a different IRS provision.
            elective = min(requested, max_total)
            after_tax_roth = requested - elective
            # The real IRS rule: catch-up isn't "whatever's left after
            # maxing out" -- it's specifically whatever exceeds the base
            # deferral limit, however much someone actually contributes.
            deferral = min(elective, deferral_limit)
            catchup = elective - deferral
            roth_catchup = (
                contrib["roth_catchup_start_year"] is not None and year >= contrib["roth_catchup_start_year"]
            )
            traditional_amount = deferral + (0.0 if roth_catchup else catchup)
            buckets["traditional"][owner]["401(k)"] += traditional_amount
            contribution_traditional += traditional_amount
            if roth_catchup and catchup > 0:
                buckets["roth"][owner] += catchup
                contribution_roth += catchup
            if after_tax_roth > 0:
                buckets["roth"][owner] += after_tax_roth
                after_tax_roth_total += after_tax_roth

            match = 0.0
            if contrib["employer_match_amount"] is not None:
                match = contrib["employer_match_amount"] * (1 + inflation_rate) ** years_from_start
            elif contrib["employer_match_pct"] is not None:
                salary = contrib["employer_match_salary"] * (1 + inflation_rate) ** years_from_start
                match = salary * contrib["employer_match_pct"]
            if match > 0:
                buckets["traditional"][owner]["401(k)"] += match
                employer_match_total += match

        # --- 1. Grow every bucket first. Normally blended_rate exactly
        # (held constant every year); with a Monte Carlo run, this year's
        # actual rate is instead drawn independently -- via year_rate_fn
        # (see run_monte_carlo()/build_correlated_return_sampler(), a
        # correlated per-asset-class draw) if given, else the older
        # single-blended Normal(blended_rate, volatility/100) draw --
        # floored at -100% either way. That ONE rate applies uniformly to
        # every bucket UNLESS bucket_rates/bucket_rate_fn was given (see
        # project()'s docstring) -- then each bucket instead uses its OWN
        # rate/draw for this year (falling back to the single rate above
        # for any bucket_key not covered, e.g. a hypothetical future
        # inheritance with no asset mix of its own yet).
        year_rate = blended_rate
        if year_rate_fn is not None:
            year_rate = max(-1.0, year_rate_fn())
        elif volatility:
            year_rate = max(-1.0, (rng or random).gauss(blended_rate, volatility / 100))
        this_year_bucket_rates = bucket_rate_fn() if bucket_rate_fn is not None else bucket_rates

        def _rate_for(bucket_key: str) -> float:
            if this_year_bucket_rates is not None and bucket_key in this_year_bucket_rates:
                return max(-1.0, this_year_bucket_rates[bucket_key])
            return year_rate

        flat = {"taxable": buckets["taxable"]}
        for name, by_type in buckets["traditional"].items():
            for t, bal in by_type.items():
                flat[f"traditional:{name}:{t}"] = bal
        for name, bal in buckets["roth"].items():
            flat[f"roth:{name}"] = bal
        pre_growth_total = sum(flat.values()) + sum(inh["balance"] for inh in buckets["inherited"])
        growth_total = 0.0
        for key in flat:
            g = flat[key] * _rate_for(key)
            flat[key] += g
            growth_total += g
        for inh in buckets["inherited"]:
            g = inh["balance"] * _rate_for(f"inherited:{inh['account_number']}")
            inh["balance"] += g
            growth_total += g
        # A single "the" rate no longer describes bucket mode -- report the
        # row's actual weighted-average realized rate instead (identical to
        # year_rate when bucket mode isn't in play).
        if this_year_bucket_rates is not None:
            year_rate = (growth_total / pre_growth_total) if pre_growth_total else year_rate

        # --- 1.5. Rollovers (one-time, see build_rollovers()) -- moves
        # money from a 401(k) pool to the matching IRA pool, after this
        # year's growth. Not a taxable event (a direct 401(k)-to-IRA
        # rollover doesn't trigger tax). Total Traditional RMD for the
        # year is unaffected either way (same total, different pool -- see
        # step 2's comment), so processing this before RMDs doesn't change
        # any tax outcome, only which pool a --verbose column draws from.
        rollover_total = 0.0
        for roll in rollovers:
            if roll["year"] != year:
                continue
            key_401k = f"traditional:{roll['owner']}:401(k)"
            key_ira = f"traditional:{roll['owner']}:IRA"
            available = flat.get(key_401k, 0.0)
            amount = min(roll["amount"], available) if roll["amount"] is not None else available * (roll["pct"] / 100)
            if amount > 0:
                flat[key_401k] -= amount
                flat[key_ira] = flat.get(key_ira, 0.0) + amount
                rollover_total += amount

        # --- 2. Forced distributions: each person's own Traditional RMD
        # (summed across their IRA/401(k) pools -- the Uniform Lifetime
        # divisor only depends on age, so computing it per pool and summing
        # gives the same total as computing it on the combined balance),
        # and each inherited account's required distribution this year.
        forced_ordinary = 0.0
        forced_cash = 0.0
        for name in people:
            for t in traditional_types:
                key = f"traditional:{name}:{t}"
                req = rmd_mod.owner_rmd(flat[key], ages[name], people[name].year)
                if req > 0:
                    flat[key] -= req
                    forced_cash += req
                    forced_ordinary += req
        inherited_traditional_distribution = 0.0
        inherited_roth_distribution = 0.0
        inherited_distribution_by_account = {}
        for inh in buckets["inherited"]:
            req = inh["schedule"].required_distribution(year, inh["balance"])
            planned = _planned_withdrawal_floor(inh.get("planned_withdrawal"), year, inh["balance"])
            req = max(req, planned)
            if req > 0:
                inh["balance"] -= req
                forced_cash += req
                inherited_distribution_by_account[inh["account_number"]] = req
                if inh["kind"] == "traditional":
                    forced_ordinary += req
                    inherited_traditional_distribution += req
                else:
                    inherited_roth_distribution += req

        # --- 2a. Social Security (once each person has claimed) -- moved
        # ahead of Roth conversions (2b) because a conversion's
        # max_marginal_bracket/avoid_irmaa_tier constraints need this
        # year's Social Security income to estimate ordinary income/MAGI.
        # Each person's OWN eligible benefit is computed regardless of
        # alive status (what they'd receive if alive/eligible) -- if both
        # are alive (or it's a single-person household), they're simply
        # summed as before; if exactly one survives, the survivor gets the
        # LARGER of the two (a simplified Social Security survivor
        # benefit -- see module docstring for what this doesn't model).
        own_benefits = {}
        for name, info in ss_config.items():
            name = name.lower()
            if name not in ages or "monthly_benefit" not in info or "claim_age" not in info:
                continue
            if ages[name] >= info["claim_age"]:
                own_benefits[name] = info["monthly_benefit"] * 12 * (1 + inflation_rate) ** years_from_start
        if alive_count == len(people):
            ss_income = sum(own_benefits.values())
        elif alive_count == 1:
            ss_income = max(own_benefits.values()) if own_benefits else 0.0
        else:
            ss_income = 0.0

        # --- 2b. Medicare Part B premium + Part D IRMAA surcharge, for
        # anyone MEDICARE_ELIGIBILITY_AGE or older, based on household MAGI
        # from IRMAA_LOOKBACK_YEARS ago (see pt/medicare.py's module
        # docstring) -- assumes tier 0 (the standard premium) for the first
        # two projected years, since this tool has no visibility into the
        # household's actual MAGI before the projection starts. Computed
        # here (ahead of Roth conversions) since it doesn't depend on this
        # year's conversion, and conversions need it -- along with spending
        # below -- to anticipate this year's taxable-brokerage withdrawal.
        lookback = medicare_mod.IRMAA_LOOKBACK_YEARS
        magi_lookback = magi_history[-lookback] if len(magi_history) >= lookback else 0.0
        irmaa_tier = medicare_mod.tier_for_magi(magi_lookback, medical_inflation_rate, year - lookback)
        medicare_eligible_count = sum(
            1 for name in people if ages[name] >= medicare_mod.MEDICARE_ELIGIBILITY_AGE and alive[name]
        )
        medicare_part_b = medicare_eligible_count * medicare_mod.annual_part_b_premium(
            irmaa_tier, medical_inflation_rate, year
        )
        medicare_part_d_irmaa = medicare_eligible_count * medicare_mod.annual_part_d_irmaa(
            irmaa_tier, medical_inflation_rate, year
        )
        medicare_total = medicare_part_b + medicare_part_d_irmaa
        cumulative_healthcare_expense += medicare_total

        # --- 2b-2. Private health insurance (optional, scenario.
        # health_insurance -- see build_health_insurance()): each
        # configured owner's monthly_amount, inflated forward like a
        # wage, applies every year from THEIR OWN retirement (not the
        # household's combined household_retirement_year) through the
        # year before they turn MEDICARE_ELIGIBILITY_AGE -- the real gap
        # between retiring and Medicare eligibility. Ends early (like
        # Medicare) the year after that person dies.
        health_insurance_total = sum(
            hi["monthly_amount"] * 12 * (1 + inflation_rate) ** years_from_start
            for hi in health_insurance
            if year >= hi["start_year"] and ages[hi["owner"]] < medicare_mod.MEDICARE_ELIGIBILITY_AGE
            and alive[hi["owner"]]
        )
        cumulative_healthcare_expense += health_insurance_total

        # --- 2b-3. Advisory expense (optional, scenario.advisory_expense
        # -- see _parse_advisory_expense_rate()): this year's total
        # portfolio value (buckets + inherited, after this year's growth/
        # rollovers/RMDs above) times the household's own flat rate -- not
        # gated on retirement status, since an AUM-based advisory fee is
        # owed regardless of whether the household has retired yet.
        portfolio_value = sum(flat.values()) + sum(inh["balance"] for inh in buckets["inherited"])
        advisory_expense = portfolio_value * blended_expense_rate

        # --- 2b-4. Liabilities (optional, scenario.liabilities -- see
        # build_liabilities()): each configured entry's annual_payment,
        # a flat NOMINAL dollar figure (NOT inflated, unlike spending/
        # health insurance -- a fixed-rate loan payment doesn't grow),
        # applies every year in its own [start_year, end_year] window --
        # a household-level window, not gated on retirement status (a
        # mortgage doesn't care whether you've retired).
        liability_payment_by_name = {
            li["name"]: li["annual_payment"] for li in liabilities
            if li["start_year"] <= year <= li["end_year"]
        }
        liability_payment_total = sum(liability_payment_by_name.values())

        # --- 2c. Spending (only once the household is fully retired), plus
        # Medicare premiums (owed regardless of retirement status, once
        # eligible), private health insurance (owed starting at that
        # person's OWN retirement, until Medicare eligibility), the
        # advisory expense, and any liabilities above -- computed here,
        # ahead of conversions, for the same reason as Medicare above.
        spending = target_spending * (1 + inflation_rate) ** years_from_start if retired else 0.0
        cash_need = spending + medicare_total + health_insurance_total + advisory_expense + liability_payment_total

        # --- 2d. Roth conversions (after this year's RMDs are satisfied --
        # see build_roth_conversions() / module docstring). Taxable but
        # generates no spendable cash. Draws from the owner's Traditional
        # pools in ROTH_CONVERSION_SOURCE_TYPES order. If constraints are
        # configured, the requested amount (annual_amount and/or
        # max_conversion_per_year, whichever is smaller) is further capped
        # by whichever of them are set -- each cap is estimated using
        # ordinary income/MAGI accrued SO FAR this year (RMDs plus any
        # other owner's conversion already processed), on top of an
        # estimated taxable-Social-Security amount. avoid_irmaa_tier also
        # adds an ANTICIPATED capital-gains estimate -- this year's
        # spending/Medicare shortfall not covered by forced cash/Social
        # Security, assumed to come from a taxable-brokerage withdrawal
        # (first in the withdrawal order) up to however much the Taxable
        # bucket holds -- since that capital gain counts toward MAGI too,
        # and it's independent of the conversion amount so there's no
        # circularity in estimating it first. max_marginal_bracket doesn't
        # need this estimate: capital gains stack for LTCG-bracket purposes
        # only, not the ordinary bracket that constraint manages (see
        # tax.federal_tax()). The actual withdrawal happens later (step 4);
        # if it ends up smaller than anticipated (e.g. RMDs/Social Security
        # already covered most of cash_need), a constrained conversion can
        # undershoot its target slightly that year.
        anticipated_shortfall = max(0.0, cash_need - forced_cash - ss_income)
        anticipated_ltcg = min(anticipated_shortfall, flat["taxable"])
        # Pre-retirement income estimate (optional, scenario.
        # pre_retirement_income -- see build_pre_retirement_income()):
        # doesn't touch cash flow or the household's own tax bill (this
        # tool still doesn't model wage income/payroll tax) -- ONLY seeds
        # ordinary_income_so_far below, so a Roth conversion/IRA
        # distribution started before that owner's own retirement gets
        # its bracket/IRMAA constraints evaluated against real expected
        # income, not zero.
        pre_retirement_income_total = sum(
            _pre_retirement_income_for_year(e, year, years_from_start, inflation_rate)
            for e in pre_retirement_income
            if year < e["end_year"] and alive[e["owner"]]
        )
        conversion_ordinary = 0.0
        conversion_total = 0.0
        conversion_by_owner = {}
        ordinary_income_so_far = forced_ordinary + pre_retirement_income_total
        for conv in roth_conversions:
            if not (conv["start_year"] <= year <= conv["end_year"]):
                continue
            remaining = _constrained_distribution_amount(
                conv, "max_conversion_per_year", flat, ss_income, ordinary_income_so_far, anticipated_ltcg,
                target_spending, inflation_rate, medical_inflation_rate, year, years_from_start, filing_status,
            )
            for t in ROTH_CONVERSION_SOURCE_TYPES:
                if remaining <= 1e-9:
                    break
                trad_key = f"traditional:{conv['owner']}:{t}"
                amount = min(remaining, flat.get(trad_key, 0.0))
                if amount > 0:
                    flat[trad_key] -= amount
                    flat[f"roth:{conv['owner']}"] += amount
                    conversion_ordinary += amount
                    conversion_total += amount
                    conversion_by_owner[conv["owner"]] = conversion_by_owner.get(conv["owner"], 0.0) + amount
                    remaining -= amount
                    ordinary_income_so_far += amount

        # --- 2e. IRA distributions (same mechanics/constraints as Roth
        # conversions just above -- see build_ira_distributions() -- and
        # sharing the same ordinary_income_so_far running total, so a
        # distribution correctly sees any conversion income already added
        # this year). The only difference: the money lands in Taxable, not
        # Roth -- a plain Traditional withdrawal, not a conversion.
        distribution_ordinary = 0.0
        distribution_total = 0.0
        distribution_by_owner = {}
        for dist in ira_distributions:
            if not (dist["start_year"] <= year <= dist["end_year"]):
                continue
            remaining = _constrained_distribution_amount(
                dist, "max_distribution_per_year", flat, ss_income, ordinary_income_so_far, anticipated_ltcg,
                target_spending, inflation_rate, medical_inflation_rate, year, years_from_start, filing_status,
            )
            for t in ROTH_CONVERSION_SOURCE_TYPES:
                if remaining <= 1e-9:
                    break
                trad_key = f"traditional:{dist['owner']}:{t}"
                amount = min(remaining, flat.get(trad_key, 0.0))
                if amount > 0:
                    flat[trad_key] -= amount
                    flat["taxable"] += amount
                    distribution_ordinary += amount
                    distribution_total += amount
                    distribution_by_owner[dist["owner"]] = distribution_by_owner.get(dist["owner"], 0.0) + amount
                    remaining -= amount
                    ordinary_income_so_far += amount

        # --- 4. Cover spending + Medicare: forced distributions + Social
        # Security first; any surplus is reinvested into taxable. Any gap
        # is covered by a discretionary withdrawal in conventional order.
        available = forced_cash + ss_income
        discretionary = {}
        shortfall = 0.0
        if available >= cash_need:
            flat["taxable"] += available - cash_need
        else:
            discretionary, shortfall = _withdraw(cash_need - available, flat, withdrawal_order)

        # --- 5. Taxes on this year's income, paid via another withdrawal.
        # See _household_tax_owed()'s docstring for why this is an
        # INCREMENT on top of pre_retirement_income_total rather than a
        # tax on ordinary_income + taxable_ss in isolation.
        ordinary_income = forced_ordinary + conversion_ordinary + distribution_ordinary + sum(
            amt for key, amt in discretionary.items() if key.startswith("traditional:")
        )
        taxable_ss = tax_mod.social_security_taxable_amount(
            ss_income, other_income=pre_retirement_income_total + ordinary_income, filing_status=filing_status
        )
        ltcg_income = discretionary.get("taxable", 0.0)
        taxable_income_amt = tax_mod.taxable_income(
            ordinary_income + taxable_ss, ltcg_income, inflation_rate, year, filing_status
        )
        tax_owed = _household_tax_owed(
            pre_retirement_income_total, ordinary_income, taxable_ss, ltcg_income,
            ss_income, inflation_rate, year, filing_status,
        )
        tax_withdrawals, tax_shortfall = _withdraw(tax_owed, flat, withdrawal_order) if tax_owed > 0 else ({}, 0.0)
        shortfall += tax_shortfall
        if retired:
            cumulative_tax_in_retirement += tax_owed

        # This year's MAGI, recorded for a FUTURE year's Medicare premium
        # (see step 2c) -- ordinary income (including taxable Social
        # Security) plus capital-gains income, plus this year's
        # pre_retirement_income_total (see above -- so a Medicare premium
        # computed shortly after retirement correctly reflects real
        # income from the working years just before it, instead of the
        # zero this tool would otherwise assume); tax-exempt interest
        # isn't tracked anywhere in this tool, so it's simply omitted
        # (see pt/medicare.py's module docstring).
        magi_history.append(ordinary_income + taxable_ss + ltcg_income + pre_retirement_income_total)

        # --- 6. Write back bucket balances.
        buckets["taxable"] = flat["taxable"]
        for name in people:
            for t in traditional_types:
                buckets["traditional"][name][t] = flat[f"traditional:{name}:{t}"]
            buckets["roth"][name] = flat[f"roth:{name}"]

        total_traditional = sum(sum(by_type.values()) for by_type in buckets["traditional"].values())
        total_withdrawal = sum(discretionary.values()) + sum(tax_withdrawals.values()) + forced_cash
        total_balance = (
            buckets["taxable"] + total_traditional + sum(buckets["roth"].values())
            + sum(inh["balance"] for inh in buckets["inherited"])
        )

        account_detail = None
        if account_columns:
            inherited_balance_by_number = {inh["account_number"]: inh["balance"] for inh in buckets["inherited"]}
            account_detail = {}
            for col in account_columns:
                bucket_key = col["bucket_key"]
                if bucket_key.startswith("inherited:"):
                    value = inherited_balance_by_number.get(bucket_key.split(":", 1)[1], 0.0)
                elif bucket_key == "taxable":
                    value = buckets["taxable"] * col["share"]
                elif bucket_key.startswith("roth:"):
                    value = buckets["roth"].get(bucket_key.split(":", 1)[1], 0.0) * col["share"]
                else:  # "traditional:<name>:<account_type>"
                    _, name, account_type = bucket_key.split(":", 2)
                    value = buckets["traditional"].get(name, {}).get(account_type, 0.0) * col["share"]
                account_detail[col["account_number"]] = value

        rows.append({
            "year": year,
            "primary_age": ages[primary_name],
            "spouse_age": ages.get(spouse_name) if spouse_name else None,
            "retired": retired,
            "primary_alive": alive[primary_name],
            "spouse_alive": alive.get(spouse_name) if spouse_name else None,
            "filing_status": filing_status,
            "deaths_this_year": [name for name in people if death_years.get(name) == year],
            "taxable_balance": buckets["taxable"],
            "traditional_balance": total_traditional,
            "roth_balance": sum(buckets["roth"].values()),
            "inherited_balance": sum(inh["balance"] for inh in buckets["inherited"]),
            # inherited_balance split by kind -- not shown in format_table()'s single
            # "Inherited" column (which stays the combined total for space), but
            # needed anywhere Traditional and Roth dollars aren't interchangeable,
            # e.g. roth_optimizer's after-tax estate value objective.
            "inherited_traditional_balance": sum(
                inh["balance"] for inh in buckets["inherited"] if inh["kind"] == "traditional"
            ),
            "inherited_roth_balance": sum(
                inh["balance"] for inh in buckets["inherited"] if inh["kind"] == "roth"
            ),
            "total_balance": total_balance,
            "growth": growth_total,
            "realized_growth_rate": year_rate,
            "ss_income": ss_income,
            "rmd_own": forced_ordinary - inherited_traditional_distribution,
            "rmd_inherited_traditional": inherited_traditional_distribution,
            "inherited_roth_distribution": inherited_roth_distribution,
            # Same purpose as roth_conversion_by_owner below, keyed by
            # inherited account_number instead of owner -- since a
            # household can hold more than one inherited account, and
            # rmd_inherited_traditional/inherited_roth_distribution above
            # are combined totals across all of them (a required-RMD
            # amount PLUS any planned_withdrawal floor, whichever is
            # greater -- see _planned_withdrawal_floor()).
            "inherited_distribution_by_account": inherited_distribution_by_account,
            "roth_conversion": conversion_total,
            # roth_conversion split by owner -- not shown in format_table()'s
            # single combined column, but needed anywhere the REQUESTED
            # amount for one owner's conversion isn't the same as what
            # actually converted once constraints clamped it down, e.g.
            # roth_optimizer reporting each owner's actual per-year amount
            # rather than its own raw search variable.
            "roth_conversion_by_owner": conversion_by_owner,
            "ira_distribution": distribution_total,
            # Same purpose as roth_conversion_by_owner above, for IRA
            # distributions -- see that field's comment.
            "ira_distribution_by_owner": distribution_by_owner,
            "rollover": rollover_total,
            "contribution_traditional": contribution_traditional,
            "contribution_roth": contribution_roth,
            "after_tax_roth_contribution": after_tax_roth_total,
            "employer_match": employer_match_total,
            "health_insurance": health_insurance_total,
            "medicare_part_b": medicare_part_b,
            "medicare_part_d_irmaa": medicare_part_d_irmaa,
            "irmaa_tier": irmaa_tier,
            "cumulative_healthcare_expense": cumulative_healthcare_expense,
            "advisory_expense": advisory_expense,
            "liability_payment": liability_payment_total,
            # Per-liability breakdown -- not shown in format_table()'s
            # single combined column, but needed to disambiguate when a
            # household has more than one (e.g. a mortgage AND a car
            # loan active the same year) -- see build_liabilities().
            "liability_payment_by_name": liability_payment_by_name,
            "spending": spending,
            # This year's estimated pre-retirement wage income (see
            # build_pre_retirement_income()) -- 0 whenever nobody in the
            # household has one configured, or after everyone with one
            # has retired. NOT included in taxable_income below (which
            # stays the household's OWN RMD/conversion/distribution
            # income only) -- shown here so tax's real basis is visible:
            # tax is the INCREMENT of (this + taxable_income) over (this
            # alone) -- see _household_tax_owed() -- so tax can look
            # large relative to taxable_income alone whenever this is
            # nonzero; that's the real marginal cost of stacking the
            # household's own income on top of real wages, not a
            # mismatch between the two figures.
            "pre_retirement_income": pre_retirement_income_total,
            "taxable_income": taxable_income_amt,
            "tax": tax_owed,
            "cumulative_tax_in_retirement": cumulative_tax_in_retirement,
            "withdrawal": total_withdrawal,
            "shortfall": shortfall,
            "accounts": account_detail,
        })

        # If EVERY person in the household now has a configured death date
        # (see build_mortality()), the projection stops once the LATER of
        # them has been processed -- the row just appended reports the
        # estate's value (total_balance) as of then. format_table() infers
        # this happened by comparing the last row's year to what the full
        # run-through-age-100 would have reached.
        if estate_stop_year is not None and year >= estate_stop_year:
            break

    return rows


def format_scenario_config(scenario: dict) -> str:
    """The raw scenario dict (exactly as loaded from the scenario YAML --
    see pt/scenario.py), dumped back out as YAML so a `project` run's
    output is self-documenting about which assumptions produced it --
    useful when comparing runs across different --scenario files, or
    coming back to a saved report later. Deliberately the SAME format the
    scenario file itself uses (rather than converting to JSON) so it's
    directly comparable/copy-pasteable; sort_keys=False preserves the
    scenario dict's own key order (matching the order fields were read in)
    rather than alphabetizing. yaml.safe_dump() natively round-trips the
    date objects YAML parsing already produced back to the same bare
    YYYY-MM-DD form, no special-casing needed."""
    return (
        "=" * 80 + "\n"
        "SCENARIO CONFIGURATION\n"
        + "=" * 80 + "\n"
        + yaml.safe_dump(scenario, default_flow_style=False, sort_keys=False, allow_unicode=True) + "\n"
    )


def summarize_outcome(rows: list, profile: dict) -> dict:
    """The same end-of-projection verdict format_table()'s closing summary
    line reports, factored out here so another tool (e.g. a scenario-sweep
    engine comparing many runs) can get it without re-parsing the printed
    table. Returns:
      outcome: "estate" (every person in the household died before the
        primary's age-100 year -- see build_mortality()/module docstring),
        "shortfall" (ran to age 100 but couldn't cover spending/taxes in at
        least one year), or "ok" (ran to age 100 with no shortfall)
      ran_to_age_100: bool -- False only means "estate" above; the ONLY way
        rows end short of the primary's age-100 year is estate_stop_year in
        project()
      first_shortfall_year: the first year with rows[i]["shortfall"] > 0,
        or None
      final_year, final_total_balance: the last row's year and
        total_balance -- for "estate", this IS the estate's value (gross,
        no heir/estate tax, cost-basis step-up, or probate -- see
        "Documented simplifications")
    Returns all-empty/ok defaults if rows is empty (shouldn't happen in
    practice -- project() always returns at least the start year)."""
    if not rows:
        return {"outcome": "ok", "ran_to_age_100": True, "first_shortfall_year": None,
                "final_year": None, "final_total_balance": 0.0}
    primary_birthdate = profile["people"]["primary"]["birthdate"]
    ran_to_age_100 = rows[-1]["year"] >= primary_birthdate.year + 100
    first_shortfall_year = next((r["year"] for r in rows if r["shortfall"] > 0), None)
    if not ran_to_age_100:
        outcome = "estate"
    elif first_shortfall_year:
        outcome = "shortfall"
    else:
        outcome = "ok"
    return {
        "outcome": outcome,
        "ran_to_age_100": ran_to_age_100,
        "first_shortfall_year": first_shortfall_year,
        "final_year": rows[-1]["year"],
        "final_total_balance": rows[-1]["total_balance"],
    }


def _percentile(values: list, p: float) -> float:
    """Linear-interpolation percentile of `values` (0 <= p <= 1) -- no
    numpy dependency (this project deliberately has none, see
    requirements.txt)."""
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    k = (len(s) - 1) * p
    f, c = int(k), min(int(k) + 1, len(s) - 1)
    if f == c:
        return s[f]
    return s[f] + (s[c] - s[f]) * (k - f)


def run_monte_carlo(profile: dict, scenario: dict, account_rows: list, blended_rate: float,
                     volatility: float, allocation_rows: list, total: float, trials: int = 1000,
                     start_year: int = None, seed: int = None, bucket_allocations: dict = None) -> dict:
    """Monte Carlo variant of project(): runs it `trials` times, each time
    letting every year's growth rate be drawn independently -- captures
    sequence-of-returns risk (the same average return, realized in a
    different order across the years, can produce a very different ending
    balance -- a single deterministic project() run can't show this).
    Every other mechanic (RMDs, taxes, Roth conversions, Social Security,
    Medicare, mortality, ...) is identical to a normal project() call for
    each trial -- only the realized growth rate varies trial to trial, so
    nothing here is duplicated from project() itself.

    Each year's draw comes from build_correlated_return_sampler()
    (allocation_rows/total -- the same subclass-level rows/total
    compute_blended_growth_rate() uses, typically allocation.
    allocation_by_asset_class()'s output): every asset class the household
    actually holds gets its OWN correlated random return that year (see
    _resolve_correlation()), summed by weight into that year's portfolio
    return -- not one blended Normal draw for the whole portfolio. This is
    what makes the percentile bands below actually reflect diversification
    (classes that don't move in lockstep partially cancel out), rather
    than the volatility overstatement compute_blended_volatility()'s own
    docstring flags for the old single-blended-draw approach.

    volatility: no longer drives the simulation itself (see above) -- kept
    as a parameter purely so it's echoed back in the returned dict's
    `volatility` field for display (format_monte_carlo_table(), the Excel
    workbook), same "one headline number" a human wants to see, computed
    via compute_blended_volatility().

    seed (optional): if given, trial i uses random.Random(seed + i), making
    the entire run byte-for-byte reproducible; omit for a fresh
    (non-reproducible) result each call.

    bucket_allocations (optional, from projection.bucket_allocation_rows()
    -- see `monte-carlo --per-bucket-growth` in pt/cli.py): PER-TAX-BUCKET
    growth instead of one draw for the whole portfolio -- each trial uses
    build_bucket_correlated_return_sampler() (allocation_rows/total still
    the household-wide ones, so every class ANY bucket holds stays
    correctly correlated) instead of build_correlated_return_sampler(),
    passing project() a bucket_rate_fn instead of year_rate_fn. Omit for
    today's behavior (one correlated draw applied to every bucket).

    Raises ProjectionError if trials < 1 or volatility < 0.

    Returns:
      trials, volatility: echoed back
      success_rate: fraction of trials that reached age 100 (or, for a
        household with mortality configured, its estate-stop year) without
        ever hitting a shortfall -- see summarize_outcome(); an "estate"
        outcome counts as success (mortality ending the projection early
        isn't itself a failure), only "shortfall" counts against it
      outcomes: {"ok": n, "shortfall": n, "estate": n} trial counts
      first_shortfall_years: sorted list of first_shortfall_year, one entry
        per trial that had one -- shows WHEN failures tend to cluster, not
        just how often they happen
      median_final_balance: median of every trial's final-year total_balance
        (for a "shortfall" trial, whatever's left, typically small or zero;
        for "estate", the estate's gross value -- see summarize_outcome())
      median_cumulative_tax: median of every trial's final-year
        cumulative_tax_in_retirement (see project()'s module docstring --
        federal tax paid while retired only, resetting to 0 at the first
        retired year, not the top of the projection). Useful for comparing
        two scenarios (e.g. Roth conversions on vs. off) on tax cost, not
        just success rate/ending balance -- a scenario can win on one and
        lose on the other.
      percentiles_by_year: [{"year", "p10", "p50", "p90"}, ...] of
        total_balance across every trial that reached that year -- a trial
        stopped early by mortality's estate_stop_year simply stops
        contributing to later years' percentiles rather than being padded
        with a fabricated value.
    """
    if trials < 1:
        raise ProjectionError("Monte Carlo trials must be at least 1.")
    if volatility < 0:
        raise ProjectionError("Monte Carlo volatility can't be negative.")

    outcomes = {"ok": 0, "shortfall": 0, "estate": 0}
    first_shortfall_years = []
    final_balances = []
    final_cumulative_taxes = []
    balances_by_year = {}

    for i in range(trials):
        rng = random.Random(seed + i) if seed is not None else random.Random()
        if bucket_allocations is not None:
            bucket_rate_fn = build_bucket_correlated_return_sampler(
                allocation_rows, total, bucket_allocations, scenario, rng
            )
            rows = project(profile, scenario, account_rows, blended_rate, start_year=start_year,
                            bucket_rate_fn=bucket_rate_fn)
        else:
            year_rate_fn = build_correlated_return_sampler(allocation_rows, total, scenario, rng)
            rows = project(profile, scenario, account_rows, blended_rate, start_year=start_year,
                            year_rate_fn=year_rate_fn)
        summary = summarize_outcome(rows, profile)
        outcomes[summary["outcome"]] += 1
        if summary["first_shortfall_year"] is not None:
            first_shortfall_years.append(summary["first_shortfall_year"])
        final_balances.append(summary["final_total_balance"])
        final_cumulative_taxes.append(rows[-1]["cumulative_tax_in_retirement"])
        for r in rows:
            balances_by_year.setdefault(r["year"], []).append(r["total_balance"])

    percentiles_by_year = [
        {"year": year, "p10": _percentile(vals, 0.10), "p50": _percentile(vals, 0.50), "p90": _percentile(vals, 0.90)}
        for year, vals in sorted(balances_by_year.items())
    ]

    return {
        "trials": trials,
        "volatility": volatility,
        "success_rate": (outcomes["ok"] + outcomes["estate"]) / trials,
        "outcomes": outcomes,
        "first_shortfall_years": sorted(first_shortfall_years),
        "median_final_balance": _percentile(final_balances, 0.50),
        "median_cumulative_tax": _percentile(final_cumulative_taxes, 0.50),
        "percentiles_by_year": percentiles_by_year,
    }


def shortfall_year_distribution(mc: dict) -> list:
    """[(year, count), ...] sorted by year -- how many trials' FIRST
    shortfall fell in each year (see run_monte_carlo()'s
    first_shortfall_years: one entry per trial that ever ran short, the
    year of its earliest one). Only years with at least one occurrence
    are included -- NOT every year of the projection. Empty list if no
    trial ever ran short. Used by format_monte_carlo_table() and
    report._populate_monte_carlo_sheet() to show WHEN failures cluster,
    not just the bare "N trials ran short" count."""
    return sorted(Counter(mc["first_shortfall_years"]).items())


def format_table(rows: list, profile: dict, blended_rate: float, account_columns: list = None,
                  bucket_rates: dict = None) -> str:
    has_spouse = bool(profile.get("people", {}).get("spouse"))
    primary_name = profile["people"]["primary"].get("name", "Primary").capitalize()
    spouse_name = profile["people"]["spouse"].get("name", "Spouse").capitalize() if has_spouse else None

    lines = []
    lines.append("=" * 130)
    lines.append("RETIREMENT GROWTH PROJECTION")
    lines.append("=" * 130)
    lines.append(f"Blended growth rate (household summary, current allocation, held constant): "
                 f"{blended_rate*100:.2f}%")
    if bucket_rates:
        lines.append("Per-bucket growth rate (--per-bucket-growth -- each tax bucket grows at its OWN "
                      "blended rate instead of the household summary above):")
        for key in sorted(bucket_rates, key=bucket_label):
            lines.append(f"    {bucket_label(key):<28} {bucket_rates[key]*100:>6.2f}%")
    lines.append("Federal tax only (no state), MFJ, standard deduction. See pt/projection.py for the full")
    lines.append("list of simplifications (bucketed balances, RMDs, inherited 10-year rules, Social Security).")
    if any(r["pre_retirement_income"] for r in rows):
        lines.append("Pre-Ret. Inc. (scenario.pre_retirement_income): a still-working person's estimated wage")
        lines.append("income for that year -- never itself taxed/withdrawn here (assumed paid via wage")
        lines.append("withholding) -- but Tax for that year is the INCREMENT of (this + Taxable Inc.) over (this")
        lines.append("alone), so it reflects the real marginal bracket once wages are stacked on top, and can")
        lines.append("look large relative to Taxable Inc. alone -- see pt/projection.py's _household_tax_owed().")
    if account_columns:
        lines.append("")
        lines.append(f"Verbose: per-account columns for every account over ${VERBOSE_ACCOUNT_THRESHOLD:,.0f} "
                      "(label = owner initial : account type - last 4 of account number). Inherited-account "
                      "columns are exact (each is independently simulated), as are Traditional columns for "
                      "anyone with just one IRA and one 401(k) (each is its own pool -- see pt/projection.py's "
                      "build_buckets()); Taxable, Roth, and a Traditional column for someone with MULTIPLE IRAs "
                      "(or multiple 401(k)s) are a DERIVED share of their shared pool (that account's fixed "
                      "starting proportion, scaled by the pool's simulated balance) -- not independently "
                      "modeled account-by-account.")
    lines.append("")

    p_hdr = f"{primary_name[:8]} Age"
    s_hdr = f"{spouse_name[:8]} Age" if has_spouse else ""
    header = f"{'Year':<6}{p_hdr:>11}"
    if has_spouse:
        header += f"{s_hdr:>11}"
    header += (f"{'Taxable':>13}{'Trad.':>13}{'Roth':>13}{'Inherited':>13}{'Total':>15}"
               f"{'SS':>11}{'RMD':>11}{'Rollover':>12}{'Roth Conv.':>12}{'IRA Dist.':>12}"
               f"{'401k Trad.':>12}{'401k Roth':>12}{'AT->Roth':>11}{'Er Match':>11}"
               f"{'Health Ins.':>12}{'Medicare B':>11}{'Medicare D':>11}{'IRMAA':>6}{'Health Exp.':>14}"
               f"{'Advisory Exp.':>14}{'Liability Pmt.':>15}"
               f"{'Spending':>11}{'Pre-Ret. Inc.':>14}{'Taxable Inc.':>14}{'Tax':>10}{'Cum. Tax Retired':>18}")
    for col in account_columns or []:
        header += f"{col['label']:>15}"
    header += "  Notes"
    lines.append(header)
    lines.append("-" * len(header))

    for r in rows:
        line = f"{r['year']:<6}{r['primary_age']:>11}"
        if has_spouse:
            line += f"{r['spouse_age']:>11}"
        rmd_total = r["rmd_own"] + r["rmd_inherited_traditional"]
        line += (f"{r['taxable_balance']:>13,.0f}{r['traditional_balance']:>13,.0f}"
                 f"{r['roth_balance']:>13,.0f}{r['inherited_balance']:>13,.0f}{r['total_balance']:>15,.0f}"
                 f"{r['ss_income']:>11,.0f}{rmd_total:>11,.0f}{r['rollover']:>12,.0f}"
                 f"{r['roth_conversion']:>12,.0f}{r['ira_distribution']:>12,.0f}"
                 f"{r['contribution_traditional']:>12,.0f}{r['contribution_roth']:>12,.0f}"
                 f"{r['after_tax_roth_contribution']:>11,.0f}{r['employer_match']:>11,.0f}"
                 f"{r['health_insurance']:>12,.0f}"
                 f"{r['medicare_part_b']:>11,.0f}{r['medicare_part_d_irmaa']:>11,.0f}{r['irmaa_tier']:>6}"
                 f"{r['cumulative_healthcare_expense']:>14,.0f}"
                 f"{r['advisory_expense']:>14,.0f}{r['liability_payment']:>15,.0f}"
                 f"{r['spending']:>11,.0f}"
                 f"{r['pre_retirement_income']:>14,.0f}"
                 f"{r['taxable_income']:>14,.0f}{r['tax']:>10,.0f}{r['cumulative_tax_in_retirement']:>18,.0f}")
        for col in account_columns or []:
            line += f"{r['accounts'].get(col['account_number'], 0.0):>15,.0f}"
        notes = []
        if r["retired"]:
            notes.append("retired")
        for name in r["deaths_this_year"]:
            who = primary_name if name == profile["people"]["primary"].get("name", "").lower() else spouse_name
            notes.append(f"{who or name} deceased")
        if r["filing_status"] == "single":
            notes.append("filing single")
        if r["shortfall"] > 0:
            notes.append(f"SHORTFALL ${r['shortfall']:,.0f}")
        line += ("  " + ", ".join(notes)) if notes else ""
        lines.append(line)

    lines.append("=" * 130)
    if rows:
        summary = summarize_outcome(rows, profile)
        if summary["outcome"] == "estate":
            who = f"Both {primary_name} and {spouse_name}" if has_spouse else primary_name
            lines.append(
                f"{who} {'are' if has_spouse else 'is'} projected to have passed away by {summary['final_year']} "
                f"under this scenario's assumptions -- the estate is valued at "
                f"${summary['final_total_balance']:,.0f} as of then (gross balance; no heir tax, estate tax, "
                "cost-basis step-up, or probate is modeled -- see pt/projection.py's \"Documented "
                "simplifications\")."
            )
        elif summary["outcome"] == "shortfall":
            lines.append(f"Portfolio is projected to be unable to cover spending/taxes starting "
                          f"{summary['first_shortfall_year']} under this scenario's assumptions.")
        else:
            lines.append("Portfolio is not projected to run short through age 100 under this scenario.")
    lines.append("=" * 130)
    return "\n".join(lines)


def format_monte_carlo_table(mc: dict, blended_rate: float, bucket_rates: dict = None) -> str:
    """Text rendering of run_monte_carlo()'s result: a percentile "fan
    chart" in table form (10th/50th/90th percentile total balance per
    year, across every trial that reached that year) plus a summary
    verdict -- mirrors format_table()'s style. If any trial ran short, a
    second small table follows the verdict: shortfall_year_distribution()'s
    (year, count) pairs -- how many trials' FIRST shortfall fell in each
    year, so a reader can see whether failures cluster (e.g. right after
    an early bad sequence of returns) rather than just the bare count.
    bucket_rates (optional, from `monte-carlo --per-bucket-growth`): each
    bucket's own mean blended rate, printed as a breakdown under the
    household summary (the ACTUAL per-year draw is still a properly
    correlated per-asset-class one, per bucket -- see
    build_bucket_correlated_return_sampler() -- these are just the means
    for display)."""
    lines = []
    lines.append("=" * 90)
    lines.append("MONTE CARLO RETIREMENT PROJECTION")
    lines.append("=" * 90)
    lines.append(f"Trials: {mc['trials']:,}   Mean blended return (household summary): {blended_rate*100:.2f}%   "
                 f"Return volatility (annual stdev): {mc['volatility']:.1f} pts")
    if bucket_rates:
        lines.append("Per-bucket mean blended return (--per-bucket-growth -- each tax bucket draws its OWN "
                      "correlated return, from its own asset mix, instead of the household summary above):")
        for key in sorted(bucket_rates, key=bucket_label):
            lines.append(f"    {bucket_label(key):<28} {bucket_rates[key]*100:>6.2f}%")
    lines.append("Each trial draws an INDEPENDENT annual return for every year from a "
                 "Normal(mean, stdev) distribution, instead of one fixed rate held constant the "
                 "whole projection -- see pt/projection.py's \"Documented simplifications\" for "
                 "what this does and doesn't capture.")
    lines.append("")
    header = f"{'Year':<6}{'10th %ile':>18}{'Median':>18}{'90th %ile':>18}"
    lines.append(header)
    lines.append("-" * len(header))
    for row in mc["percentiles_by_year"]:
        lines.append(f"{row['year']:<6}{row['p10']:>18,.0f}{row['p50']:>18,.0f}{row['p90']:>18,.0f}")
    lines.append("=" * 90)
    successes = mc["outcomes"]["ok"] + mc["outcomes"]["estate"]
    lines.append(
        f"Success rate: {mc['success_rate']*100:.1f}% ({successes:,} of {mc['trials']:,} trials reached "
        "age 100 -- or the household's configured estate-stop year -- without ever running short)."
    )
    if mc["outcomes"]["shortfall"]:
        lines.append(
            f"{mc['outcomes']['shortfall']:,} trial(s) ran short -- as early as "
            f"{mc['first_shortfall_years'][0]}, as late as {mc['first_shortfall_years'][-1]}."
        )
        lines.append("")
        lines.append("Shortfall year distribution (first year each failing trial ran short):")
        dist_header = f"{'Year':<6}{'Trials':>8}"
        lines.append(dist_header)
        lines.append("-" * len(dist_header))
        for year, count in shortfall_year_distribution(mc):
            lines.append(f"{year:<6}{count:>8,}")
    lines.append(f"Median final-year total balance across all trials: ${mc['median_final_balance']:,.0f}")
    lines.append(f"Median cumulative tax paid while retired across all trials: ${mc['median_cumulative_tax']:,.0f}")
    lines.append("=" * 90)
    return "\n".join(lines)
