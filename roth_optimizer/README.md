# Roth Conversion Optimizer

A companion tool to `pt` (see the top-level [README.md](../README.md)) --
`pt.cli project`'s own `scenario.roth_conversions` is a GREEDY per-year
heuristic (see `pt/projection.py`'s "Documented simplifications"): each
year, convert as much as that year's constraints allow, without looking
ahead to whether converting more (or less) that year would leave the
household better off later. This tool instead searches across the WHOLE
conversion schedule at once -- a separate dollar amount per owner per
year -- to maximize a defined **after-tax total estate value** objective.

A separate top-level package from `pt` -- `pt` is considered stable; this
reuses `pt`'s projection engine (specifically, `roth_conversions[].
annual_amount` accepting a list of one dollar amount per year instead of
a single flat number -- see `pt/projection.py`'s `build_roth_conversions()`)
rather than duplicating it, but ships and runs independently. Dependency-free,
same as the rest of this project -- the search (see "The search" below) is a
from-scratch method, not a call into an external optimizer library. Run from
the repo root, the same way as `pt.cli`/`scenario_engine.cli`:

```bash
python3 -m roth_optimizer.cli optimize --heir-tax-rate 24
```

## The objective: after-tax total estate value

Two households could end a projection with the identical `total_balance`
and be in very different positions, because a dollar sitting in a
Traditional IRA still owes ordinary-income tax whenever it's eventually
withdrawn (by the household itself, or by an heir under the SECURE Act's
10-year rule), while a Roth dollar never does, and a Taxable-brokerage
dollar gets a cost-basis step-up at death (no capital-gains tax to heirs
on appreciation through the date of death). Comparing raw `total_balance`
across two conversion strategies would silently favor whichever one just
happens to leave more in Traditional, even if a smarter strategy left
*less* raw balance but *more* of it in Roth. So this tool scores a
schedule by:

```
after_tax_estate_value = taxable_balance + roth_balance + inherited_roth_balance
                        + (traditional_balance + inherited_traditional_balance) * (1 - heir_tax_rate)
```

using the row at the household's OWN stated `retirement.life_expectancy`
(the LATER of `life_expectancy`/`spouse_life_expectancy`, for a couple)
-- **not** necessarily the last row `pt.cli project`/`report`/
`monte-carlo` would show. Those commands still use `scenario.mortality`
(or age 100 if that's unset) exactly as documented in `pt/projection.py`
-- meant to display the full, detailed trajectory, survivor effects and
all, if you've configured mortality. This package deliberately scores
against `life_expectancy` instead, so you get a realistic answer out of
a search WITHOUT needing a full `scenario.mortality` block (with its own
survivor-spending/filing-status mechanics) just to bound the horizon --
see `optimizer.estate_valuation_row()`'s docstring for the exact rule
(and the real simplification it makes: the row it reads still reflects
whatever cash flow `project()` computed WITHOUT mortality applied, if
you haven't also set it -- only the balance fields are treated as the
estate's value at that point). If `life_expectancy` isn't configured at
all, every function in this package falls back to `project()`'s own
last row, same as before this existed. `--heir-tax-rate` (required) is a
single flat percentage standing in for whatever rate eventually applies
to eventual Traditional withdrawals -- this tool has no visibility into
an heir's own bracket, state, or the household's own future tax
situation decades out, so you supply your own best estimate (a
household's own current marginal rate is a reasonable starting point if
you have no better information about your heirs').

This *already* accounts for the household's own lifetime cost of
converting -- more aggressive conversions mean more current tax paid,
which draws down the buckets sooner, which is reflected in a lower
`total_balance` at the end; the objective doesn't need a separate
"minus lifetime tax paid" term, since the simulation itself already
paid it before reaching that final row.

## The search: coordinate-ascent / pattern search

One decision variable per (owner, year) across every
`scenario.roth_conversions` window -- e.g. two owners with an 11-year and
a 15-year window gives 26 variables, each a dollar amount to convert that
year. From a few different starting points (all-zero, evenly-spread
across the search space, and a couple of random ones), the search
repeatedly cycles through every variable in a random order, nudging each
one up or down by the current step size and keeping whichever direction
improves the objective (or leaving it alone if neither does). Once a full
pass finds no improvement anywhere, the step size halves; a given
starting point's search stops once its step size drops below `--min-step`
dollars. Whichever starting point ends up best is the winner.

This is a classic derivative-free method (sometimes called Hooke-Jeeves
pattern search), not a call into any external solver -- consistent with
the rest of this project's dependency-free, easily-auditable design (see
`pt/README.md`). **It finds a GOOD schedule, not a certified globally
optimal one** -- with dozens of interacting variables (shared Traditional
balances, tax brackets, IRMAA thresholds, RMDs all moving together),
there's no guarantee a different search algorithm (or more restarts)
couldn't find something better. In practice it consistently beats both
"no conversions" and a flat/greedy schedule by a meaningful margin (see
the worked example below) -- treat it as a strong starting point for your
own judgment, not a final answer.

A window's own `constraints.max_conversion_per_year` (if set) becomes
that variable's own upper bound during the search itself, not just
something `pt.cli project` clamps down to afterward -- otherwise the
search would have no reason to land exactly at the cap (any requested
amount above it behaves identically once simulated). `max_marginal_
bracket`/`avoid_irmaa_tier`/`preserve_cash_reserve` are income-dependent
and can't be pre-bounded the same way -- `pt.cli project` still enforces
those on every single evaluation the search performs, so the SIMULATED
outcome (and the objective value reported) is always correct either way.
The printed schedule shows what ACTUALLY converts each year (see
`optimizer.actual_conversions()`) -- when a dynamic constraint reduces a
year below what the search requested, that's called out explicitly right
in the output (`requested $X -- a dynamic constraint reduced it`), so you
always see the real numbers, not just what was asked for.

## Setting up `scenario.roth_conversions` for the optimizer

Same shape `pt.cli project` already uses (see the top-level README's
"Growth projection" section) -- owner, `start_date`, `end_date`, and
optionally `constraints` -- except `annual_amount` isn't needed (the
optimizer decides it) and, if given anyway, is simply ignored/replaced:

```yaml
roth_conversions:
  - owner: alex
    start_date: 2028-01-01
    end_date: 2039-12-31
    constraints:
      max_marginal_bracket: 24        # optional -- kept as a hard cap during the search
      avoid_irmaa_tier: 3             # optional -- same
  - owner: sam
    start_date: 2028-01-01
    end_date: 2043-12-31
    constraints:
      max_conversion_per_year: 48000  # optional -- becomes this variable's own search bound
```

At least one entry is required -- `roth_optimizer.OptimizerError` (shown
as a plain CLI error) if `scenario.roth_conversions` is empty or absent.

### Starting conversions before retirement

Nothing stops a window's `start_date` from being earlier than
`retirement_age`/`spouse_retirement_age` -- a common real strategy is
converting during lower-income years right before retiring, or even
during working years if there's room in a bracket. But `pt.cli project`
has no concept of wage income at all (see its own "Documented
simplifications"), so WITHOUT anything else configured, a
`max_marginal_bracket`/`avoid_irmaa_tier` constraint on a pre-retirement
window gets evaluated as if that person had zero other income --
letting the search convert far more than someone's real salary would
actually leave room for, AND that conversion's own tax gets computed in
isolation too, at whatever bracket it looks like alone -- typically much
lower than the household's REAL bracket once wages are stacked on top,
making a large pre-retirement conversion look artificially cheap to the
search. Add `scenario.pre_retirement_income` (see the top-level README's
"Retirement scenario" section) with your own gross W-2 income estimate to
fix both:

```yaml
pre_retirement_income:
  alex:
    estimated_gross_income: 300000   # today's dollars -- your GROSS W-2
                                      # income (Box 1), NOT total AGI --
                                      # AGI can double-count investment
                                      # income this tool already tracks
                                      # separately via the taxable bucket
```

A single flat number inflates forward like a wage -- fine for a steady
salary. For a real, non-flat year (a one-time bonus, say), give a
per-year LIST instead -- one already-nominal dollar amount per year, no
further inflation, same convention as `roth_conversions`' own
`annual_amount` lists -- anchored by a required `start_date`:

```yaml
pre_retirement_income:
  sam:
    start_date: 2026-01-01
    estimated_gross_income: [80000, 400000, 83000, 86000]  # 2026-2029:
                                                            # one bonus
                                                            # year, then
                                                            # back to normal
```

This never bills the wage income's own tax to the portfolio -- this tool
still has no wage-income model at all, and a household is assumed to
cover that from its paycheck/withholding -- but it does two real things:
feeds the bracket/IRMAA constraint math and a later year's Medicare/
IRMAA premium lookback, so the search (and a hand-configured
`max_marginal_bracket`/`avoid_irmaa_tier` constraint on `project` itself)
sees a realistic picture of how much bracket room is actually left; and
makes that year's tax on the household's own RMD/conversion/distribution
income an INCREMENT on top of this baseline (tax on baseline+own-income
minus tax on baseline alone), so the search actually pays the REAL
marginal cost of converting while still working, instead of the
understated, wage-blind cost it would otherwise see -- which is what
makes the search prefer smaller conversions during working years and
larger ones after retirement, rather than being indifferent between the
two. See `pt/projection.py`'s `build_pre_retirement_income()`,
`_household_tax_owed()`, and "Documented simplifications" for the full
detail.

## Usage

```bash
python3 -m roth_optimizer.cli optimize --heir-tax-rate 24
python3 -m roth_optimizer.cli optimize --heir-tax-rate 24 --seed 42 --restarts 5
python3 -m roth_optimizer.cli optimize --heir-tax-rate 24 \
  --out-scenario optimized_scenario.yaml --out optimized_projection.xlsx
```

Same `--db`/`--snapshot`/`--profile`/`--scenario` flags as `pt.cli project`,
defaulting the same way; also works against `scenario.hypothetical_accounts`
with no real data at all (see the top-level README's "Hypothetical
accounts" section) -- including its own top-level `people` section, so a
single self-contained scenario file needs no separate profile file here
either, exactly like `pt.cli project`/`monte-carlo`.

```
Searching (restarts=3, seed=42, heir tax rate=22%) ...
Done in 2.3s (2,588 project() evaluations).

After-tax total estate value (see roth_optimizer/README.md for what this means):
  No Roth conversions at all:        $15,597,627
  This scenario's own configuration: $16,249,038
  Optimized schedule:                $16,815,140
  Improvement over this scenario's own configuration: $566,103
  Improvement over no conversions at all:              $1,217,513

Optimized per-year conversion schedule (what actually converts, after any
max_marginal_bracket/avoid_irmaa_tier/preserve_cash_reserve constraint clamps it):
  alex (2027-2039):
    2027: $23,930  (requested $200,000 -- a dynamic constraint reduced it)
    2028: $25,353  (requested $200,000 -- a dynamic constraint reduced it)
    2029: $0
    ...
```

`$X (requested $Y -- a dynamic constraint reduced it)` shows up whenever
`max_marginal_bracket`/`avoid_irmaa_tier`/`preserve_cash_reserve` clamped
that year's request down -- the number shown is always what actually
converts, never the raw request.

"This scenario's own configuration" is what `pt.cli project` would
compute today, running your `roth_conversions` entries exactly as given
(whatever `annual_amount`/constraints you already have) -- shown as `n/a`
if those entries have neither (nothing to compare against, since there's
no "as configured" schedule to run in the first place -- the usual case
when a scenario file was set up purely to hand the optimizer a window).

`--out-scenario PATH.yaml` (optional) saves a full copy of your scenario
with `roth_conversions` replaced by the winning schedule -- ready to run
straight through `pt.cli project`/`monte-carlo` (or another
`roth_optimizer` search, or `scenario_engine`) to see it in full, or as a
starting point to hand-tune further. `--out PATH` (optional, repeatable,
`.txt` or `.xlsx`) saves the winning schedule's full year-by-year
projection directly, without a separate `pt.cli project` step.

`--per-bucket-growth` (optional, opt-in) grows each tax bucket (Taxable,
each Traditional owner/type pool, each Roth owner, each Inherited
account) at its OWN blended rate -- same mechanism as the top-level
README's "Per-bucket growth" section, but threaded through the search
itself: every `project()` call `optimize()` makes (the search's own
evaluations, `zero_conversions_value`, `original_value`, and the final
`actual_schedule`) uses the same bucket rates, so "optimal" is judged
against a consistent growth assumption throughout, not just shown
afterward in a follow-up report. Prints the per-bucket breakdown before
searching, same as `--out`'s projection would show.

`--save-iterations PATH` (optional, repeatable) saves every candidate
schedule the pattern search evaluated -- every restart, every trial, not
just the winner -- as lifetime taxes paid vs. after-tax estate value, one
entry per evaluation. The extension picks the format, and the search
itself only runs once no matter how many times you pass the flag:

```bash
python3 -m roth_optimizer.cli optimize --heir-tax-rate 24 --save-iterations search.csv
python3 -m roth_optimizer.cli optimize --heir-tax-rate 24 --save-iterations search.html
python3 -m roth_optimizer.cli optimize --heir-tax-rate 24 \
  --save-iterations search.csv --save-iterations search.html   # both, from the same search
```

`.csv` writes a plain 3-column file (`iteration, lifetime_taxes,
after_tax_estate_value`) for your own analysis/plotting. `.html`/`.htm`
instead writes a self-contained, dependency-free interactive scatter
chart of the same data (open it directly in a browser -- no server, no
external files) -- x-axis lifetime taxes paid, y-axis after-tax estate
value, one dot per schedule the search tried. The winning schedule and
the zero-conversions/current-plan reference points are each marked with
their own shape (so you can see exactly where they sit among the dots)
AND with a horizontal dashed line across the full chart (so you can read
their after-tax estate value straight off the y-axis, without also
needing their x-position); hover any point for its exact numbers. The
optimized/winning marker is also labeled with the household's own age at
the life-expectancy point (its primary person's age in whichever year
`retirement.life_expectancy`/`spouse_life_expectancy` resolves to -- see
the top-level README's "Retirement scenario" section), when that's
configured.

`--optimizer-projection-ages AGES` (optional, comma-separated, e.g.
`85,90,95,100`) additionally traces each of those three schedules'
after-tax estate value through the ages you give (scenario.mortality
can't shorten this second projection -- see `optimizer.py`'s
`_project_without_mortality()`/`_row_at_age()`), one project() run per
schedule no matter how many ages you list. Only affects `--save-
iterations *.html`:

```bash
python3 -m roth_optimizer.cli optimize --heir-tax-rate 24   --save-iterations search.html --optimizer-projection-ages 85,90,95,100
```

Each schedule's own trajectory is drawn as a short dotted line from its
life-expectancy marker through each requested age (ascending), with a
small hollow marker and a compact age number at every stop -- the exact
dollar numbers are in a tooltip on hover, not more on-chart text, so an
arbitrarily long age list never floods the chart. **This flag is opt-in
on purpose**: adding a far-out age like 100 stretches the chart's own
axis scale to fit it, which can compress the main cloud of candidate
schedules down into a corner (a household's estate can easily grow
several times over between life expectancy and age 100). Omit the flag
for the plain two-reference-point chart, or start with ages closer to
the household's own life expectancy rather than reaching straight for
100. See `roth_optimizer/scatter_chart.py`.

Because the search is coordinate-ascent (a few starting points, each
nudged toward a local improvement) rather than a random Monte Carlo
sweep, the resulting cloud traces the paths the search actually explored
-- it isn't a uniform random sample of every possible schedule, the way
a tool like rothblueprint.com's own scatter is. The upper-left edge of
the cloud (highest estate value for the least lifetime tax) is the part
comparable to that kind of chart; the density/shape of the interior
isn't.

**Including the chart in `pt.cli report`**: when `--save-iterations` is
given a `.html` path AND `--out-scenario` is given, the saved
`*_optimized*.yaml` also records that chart file as a `scatter_chart:`
field on its first `roth_conversions` entry -- a path relative to the
scenario file's own directory (so the two can be moved together; only the
first `.html` counts, `.csv` doesn't). `pt.cli report --scenario
<that file>` then adds an optional **Optimizer Search** tab: Excel can't
show an HTML page inside a sheet, so the tab redraws the chart as a
native Excel scatter from the data embedded in the HTML (every candidate
schedule, the optimized schedule, the No conversions/Current plan
reference points, and any `--optimizer-projection-ages` trajectories),
with every point in a table beside it and a link to the original HTML for
the interactive hover version. No `scatter_chart` field means no tab. If
the named file is missing or isn't a chart this tool wrote, `report` just
prints a warning to stderr and skips the tab -- it never fails. (A plain
`project --out *.xlsx` workbook doesn't get the tab -- only `report`.)
`scripts/run_optimizer.sh` writes both files into the same directory, so
its `*_optimized_<rate>.yaml` is wired up automatically.

**Convenience script**: `scripts/run_optimizer.sh <scenario.yaml>
[heir_tax_rate] [out_dir]` (from the repo root) wraps `optimize` with
`--per-bucket-growth` ON by default (set `PER_BUCKET_GROWTH=0` to turn it
off) and the output naming shown above -- see the top-level README's
"Project layout" for its full option list (`RESTARTS`/`SEED` env vars).

## Sweeping `--heir-tax-rate`

`--heir-tax-rate` is an assumption (what tax rate whoever eventually
inherits/withdraws Traditional dollars pays), not something the search
picks for you -- and a LOWER assumed rate always makes the raw
"Optimized schedule" dollar total bigger for ANY schedule, since it's
baked directly into the objective (`traditional_balance * (1 -
heir_tax_rate)` -- see "The objective" above). That means comparing raw
totals across different rate assumptions to find "the best rate" doesn't
work -- it would trivially always pick the lowest rate you tried. The
`sweep` subcommand exists for comparing rates anyway, without that trap:
it runs `optimize` once per rate (loading the scenario/profile/accounts
only once, not per rate) and prints every rate's numbers side by side,
deliberately WITHOUT declaring a single winner:

```bash
python3 -m roth_optimizer.cli sweep --heir-tax-rates 10,12,22,24,32,35,37
python3 -m roth_optimizer.cli sweep --heir-tax-rates 24,32,35 --per-bucket-growth --out-dir ./out
```

```
Sweeping 3 heir tax rate(s): 12%, 24%, 35% (restarts=3, seed=1)

--- 12% ---
Done in 4.1s (4,075 project() evaluations). Optimized: $21,690,977
--- 24% ---
Done in 3.8s (3,803 project() evaluations). Optimized: $21,693,054
--- 35% ---
Done in 2.1s (2,103 project() evaluations). Optimized: $21,571,272

==========================================================================================================
Heir tax rate sweep -- retirement_scenario_5_hypothetical.yaml
==========================================================================================================
  Rate      No conversions          Own config           Optimized   Gain vs own config   Gain vs no conv.
   12%         $18,708,136         $21,327,583         $21,690,977             $363,394         $2,982,841
   24%         $18,217,749         $21,327,583         $21,693,054             $365,471         $3,475,305
   35%         $17,768,228         $21,327,583         $21,571,272             $243,689         $3,803,044
```

Compare the **Gain** columns, not the raw totals -- they isolate how much
value the search itself adds over your own configuration, or over doing
no conversions at all, under each assumption (and, unlike the raw totals,
aren't mechanically ordered by the rate itself). `--out-dir DIR` (optional)
saves each rate's winning schedule/projection as
`DIR/<stem>_optimized_<rate>.yaml`/`_projection.txt` -- same naming
`optimize --out-scenario`/`--out` would produce for that single rate
(a fractional rate like `22.5` becomes `22p5` in the filename). Every
other flag below works the same as `optimize`, applied to every rate in
the sweep; `--heir-tax-rate` itself is replaced by `--heir-tax-rates`
(comma-separated, default: the current federal marginal bracket rates,
`10,12,22,24,32,35,37`).

`scripts/run_optimizer.sh` wraps this too -- give it a comma-separated
rate list instead of one rate and it switches to `sweep` automatically:

```bash
scripts/run_optimizer.sh retirement_scenario.yaml 10,12,22,24,32,35,37
```

## Comparing conversion priority (`priority`)

`project()`'s roth_conversions step processes entries strictly in
`scenario.roth_conversions`' own list order, accumulating a running
ordinary-income total across them within each year (see
`pt/projection.py`'s module docstring) -- so when two owners' entries
share a household bracket ceiling (`max_marginal_bracket`/
`avoid_irmaa_tier`, both computed on the household's combined MFJ income),
whoever's entry comes FIRST gets first claim on that shared room; the
other owner's conversion that year is capped by whatever's left over,
even if they still have Traditional balance and search-requested amount
available. Which owner is "first" is otherwise just an accident of file
order -- `priority` runs the search twice, once with each owner
prioritized, and reports which ordering actually wins:

```bash
python3 -m roth_optimizer.cli priority --heir-tax-rate 24
```

```
Comparing conversion priority between alex and sam (restarts=3, seed=1, heir tax rate=24%)

--- alex prioritized ---
Done in 2.3s (2,562 project() evaluations). Optimized: $23,921,054

--- sam prioritized ---
Done in 2.1s (2,318 project() evaluations). Optimized: $23,903,988

==========================================================================================
Conversion priority comparison -- retirement_scenario_1.yaml
==========================================================================================
After-tax total estate value:
  alex prioritized: $23,921,054
  sam prioritized: $23,903,988

alex prioritized wins by $17,066 over sam prioritized.
```

Unlike `sweep`'s `--heir-tax-rate`, reordering does NOT mechanically bias
the objective one way or the other -- declaring a winner here is
meaningful, not a trap. If the two owners' constraints/balances never
actually compete for the same shared room (no year where both would
otherwise convert enough to bump into a shared ceiling), the two
orderings come out identical and `priority` says so rather than
reporting a meaningless tiny difference. Needs `scenario.roth_conversions`
entries from exactly two different owners; `--out-dir DIR` (optional)
saves each ordering's winning schedule/projection as
`DIR/<stem>_priority_<owner>first.yaml`/`_projection.txt`.
`scripts/run_priority.sh` wraps this the same way `run_optimizer.sh`
wraps `optimize`/`sweep` (`--per-bucket-growth` ON by default).

## Accelerated IRA drawdown (`--optimize-ira-distributions`)

By default, this tool only ever searches `scenario.roth_conversions` --
`scenario.ira_distributions` (a plain taxable Traditional withdrawal
taken before it's needed for spending, e.g. to use up room in a lower
bracket -- see the top-level README's "Growth projection") stays exactly
as you configured it, untouched. `--optimize-ira-distributions` searches
BOTH jointly, in the same pattern-search pass -- not as two separate
optimizations -- since a Roth conversion and an IRA distribution draw
from the same owner's Traditional dollars and compete for the same
annual bracket/IRMAA room each year:

```bash
python3 -m roth_optimizer.cli optimize --heir-tax-rate 24 --optimize-ira-distributions
```

This lets the search decide, dollar for dollar: convert to Roth (tax
now, tax-free forever), or take a plain taxable distribution instead
(tax now, lands in Taxable -- more liquid, and its future growth is only
partially taxed via capital gains, unlike a Roth's completely tax-free
growth)? In practice, with no near-term spending need, the search
usually finds Roth conversion dominates a plain distribution dollar for
dollar (same tax cost today, better tax treatment forever after) -- IRA
distributions only pull ahead when the household actually needs the
liquidity a Taxable dollar provides that a Roth dollar doesn't (early,
before age 59½, or simply to fund near-term spending without touching
the 5-year Roth conversion clock). Needs at least one
`scenario.ira_distributions` `{owner, start_date, end_date}` window (same
shape as `roth_conversions` -- see the top-level README's "Growth
projection" section -- `constraints.max_distribution_per_year` is that
section's own name for the flat per-year dollar cap). A scenario with no
`ira_distributions` section at all is unaffected either way -- the flag
just has nothing to search. `scripts/run_optimizer.sh`/`run_priority.sh`
both support `OPTIMIZE_IRA_DISTRIBUTIONS=1` (default: off, unlike
`PER_BUCKET_GROWTH`, since most scenarios have no `ira_distributions`
section to search in the first place).

## Inherited account drawdown sequence (`--optimize-inherited-withdrawals`)

An inherited account's `planned_withdrawal` (see the top-level README's
"Growth projection" section) is a FLOOR on top of that account's own
legally-required SECURE Act 10-year-rule distribution -- `project()`
always takes the greater of the two, and always forces the full
remaining balance out by the 10th year regardless of what's configured.
`annual_amount` there now also accepts a per-year LIST, the same
convention `roth_conversions`/`ira_distributions` already use.
`--optimize-inherited-withdrawals` searches that list as a THIRD family
of decision variables, jointly with whatever else is being searched:

```bash
python3 -m roth_optimizer.cli optimize --heir-tax-rate 24 --optimize-inherited-withdrawals
```

What's actually being searched is "how much EXTRA, above the legal
minimum, to withdraw this year" -- e.g. an account requiring annual
stretch RMDs (the original owner died on or after their own Required
Beginning Date) has a minimum that naturally grows each year as the
Single Life Table divisor shrinks, then a forced full-balance lump sum
in year 10 -- a textbook bracket-jumping shape. The search can smooth
that out by taking more than the bare minimum in earlier, lower-bracket
years, so the household pays real (often lower, progressive) tax sooner
instead of a large chunk landing in a worse bracket later. Unlike the
other two kinds, this one has no `constraints` (no
`max_marginal_bracket`/`avoid_irmaa_tier` mechanism) -- its only natural
bound is the account's own remaining balance; the search's own objective
(not an explicit per-year cap) is what discourages withdrawing so
aggressively it creates a worse tax outcome.

Needs an inherited_accounts entry that (a) already has a
`planned_withdrawal` `{start_date, end_date, ...}` window configured (the
search fills in `annual_amount`; the window itself isn't invented from
nothing, same rule as the other two kinds) and (b) is a REAL,
already-held account (has its own `account_number`) -- a *hypothetical*
future inheritance (identified only by `hypothetical_value` and a
decedent death date, no `account_number` yet) is silently skipped, since
there's no stable identifier yet to report its actual withdrawal under.
`scripts/run_optimizer.sh`/`run_priority.sh` both support
`OPTIMIZE_INHERITED_WITHDRAWALS=1` (default: off).

## Social Security claim age (`claim-age`)

`scenario.social_security.<name>.monthly_benefit` is normally a flat
number you already know (from ssa.gov's own calculator or a benefit
statement) -- the benefit AT your chosen `claim_age`, fixed. There was no
way to ask "what if I claimed at a different age instead" without
looking that number up yourself for every age you wanted to compare.
`pia_monthly` (the Primary Insurance Amount -- the benefit claiming
EXACTLY at Full Retirement Age would pay) fixes that: give it instead of
`monthly_benefit`, and the tool computes the claim-age-adjusted benefit
itself (see `pt/social_security.py`'s `benefit_at_claim_age()` -- SSA's
own early-reduction/Delayed-Retirement-Credit formula). The `claim-age`
subcommand then brute-forces every combination:

```bash
python3 -m roth_optimizer.cli claim-age --heir-tax-rate 24
```

Claim age only ever takes one of 9 whole-year values (62-70), so even a
two-person household (81 combinations) is cheap to try exhaustively --
no pattern search needed, unlike the dollar-amount search. Needs at
least one `scenario.social_security` entry with `pia_monthly` configured
(a name with only a flat `monthly_benefit` is left alone -- there's
nothing to recompute FROM). `--claim-ages 62,66,70` restricts which ages
to try (for every candidate name) instead of all nine.

**Unlike `sweep`'s `--heir-tax-rate`, claim_age is a real decision the
household makes, not an assumption baked into the objective** -- so this
DOES declare a winner. But there's a real property of this tool worth
knowing before you trust that winner: **with no `retirement.
life_expectancy` (or `spouse_life_expectancy`) configured, this search
scores against an unbounded lifetime, so claiming LATER will almost
always win** -- more guaranteed dollars collected over a longer assumed
lifetime, not a bug, just what an unbounded-lifetime comparison
mechanically rewards (see "The objective" above for exactly which row
every search in this package scores against). `claim-age` prints this
exact caveat whenever neither is set. Set `retirement.life_expectancy`
(and `spouse_life_expectancy`, for a couple) to the age(s) you actually
want this scored against and re-run -- the optimum can flip toward
claiming EARLY once a household's own assumed lifespan is short enough
that delaying doesn't pay for itself. This does NOT require touching
`scenario.mortality` at all -- that's a separate, independent setting
that only affects `pt.cli project`/`report`/`monte-carlo`'s own detailed
trajectory, not this search.

This deliberately does **NOT** jointly re-run the dollar-amount search
for every claim-age combination -- whatever `roth_conversions`/
`ira_distributions`/`inherited_accounts` the scenario already configures
run exactly as given, for every age tried. Claim age does shift how much
bracket/IRMAA room a Roth conversion has each year (different Social
Security income), so the true joint optimum could differ from running
the two searches separately -- a documented scope boundary, not an
oversight (see "Documented simplifications" below). `--out-scenario`/
`--out` save the single winning combination, same as `optimize`.
`scripts/run_claim_age.sh <scenario.yaml> [heir_tax_rate] [output_dir]`
wraps this (`CLAIM_AGES`/`PER_BUCKET_GROWTH`/`PT_REPO` env vars).

## Options

| Flag | Default | Meaning |
|---|---|---|
| `--heir-tax-rate PCT` | *(required)* | See "The objective" above -- a percentage, e.g. `24` for 24% |
| `--restarts N` | 3 | Search starting points to try, keeping the best |
| `--min-step N` | 1000 | Stop refining once nudging a year's amount by less than this many dollars |
| `--upper-bound N` | household's current total account value | Largest single-year amount the search considers, before any constraint capping |
| `--seed N` | none (fresh each run) | Makes the whole search reproducible |
| `--out-scenario PATH` | none | Save the winning schedule as a full scenario YAML |
| `--out PATH` | none | Save the winning schedule's projection (repeatable) |
| `--per-bucket-growth` | off | Grow each tax bucket at its own blended rate throughout the search (see above) |
| `--optimize-ira-distributions` | off | Jointly search scenario.ira_distributions too (see above) |
| `--optimize-inherited-withdrawals` | off | Jointly search inherited_accounts planned_withdrawal windows too (see above) |

`sweep` takes the same flags except `--heir-tax-rate` (replaced by
`--heir-tax-rates`, a comma-separated list) and `--out-scenario`/`--out`
(replaced by `--out-dir`, one file pair per rate) -- see "Sweeping
`--heir-tax-rate`" above. `priority` takes the same flags as `optimize`
except `--out-scenario`/`--out` (replaced by `--out-dir`, one file pair
per ordering) -- see "Comparing conversion priority" above; note its own
`--optimize-ira-distributions`/`--optimize-inherited-withdrawals` only
extend what's SEARCHED for each ordering -- reordering itself still only
affects `roth_conversions`' priority, not `ira_distributions`'/
`inherited_accounts`' (which keep their own scenario order regardless of
which owner is prioritized). `claim-age` takes `--heir-tax-rate`,
`--out-scenario`/`--out`, `--per-bucket-growth`, and its own
`--claim-ages` (see "Social Security claim age" above) -- it has no
`--restarts`/`--min-step`/`--upper-bound`/`--seed` (a brute-force grid
search needs none of the pattern search's own tuning) and no
`--optimize-ira-distributions`/`--optimize-inherited-withdrawals` (it
never re-runs the dollar-amount search at all).

## Documented simplifications

- **Not a certified global optimum** -- see "The search" above. More
  `--restarts`, a smaller `--min-step`, or a different random `--seed`
  can all find a somewhat different (occasionally better) schedule.
- **A single flat `--heir-tax-rate`** -- doesn't model a specific heir's
  actual bracket, filing status, state tax, or how the 10-year rule's
  forced withdrawals might stack with THEIR other income across those 10
  years (which could push them into a higher bracket than a flat
  assumption reflects).
- **No federal estate tax, generation-skipping tax, or the exemption
  threshold** either might trigger -- same simplification `pt`'s own
  "Documented simplifications" already notes for a reported estate value.
- **Scores against `retirement.life_expectancy`, a simple year cutoff**
  -- not a full survivor-modeled scenario. The row read at that year
  still reflects whatever cash flow `project()` computed WITHOUT
  mortality applied (both still "alive", MFJ filing, etc., unless
  `scenario.mortality` is ALSO set) -- see "The objective" above. Set
  `scenario.mortality` too if you want a fully-modeled survivor scenario
  to also be what's scored, or leave the two independent if you want
  this package to plan against a different horizon than what
  `pt.cli report` displays.
- **A window's dynamic constraints (`max_marginal_bracket`,
  `avoid_irmaa_tier`, `preserve_cash_reserve`) aren't pre-bounded in the
  search itself** -- see "The search" above for what this can mean for
  the exact numbers in the printed schedule (never for the objective
  value itself, which `pt.cli project`'s own enforcement always keeps
  correct).
- **The dollar-amount search (`optimize`/`sweep`/`priority`) and the
  claim-age search (`claim-age`) are independent, not jointly optimized
  together** -- `optimize`'s own family (roth_conversions,
  `--optimize-ira-distributions`, `--optimize-inherited-withdrawals`) and
  conversion priority (`priority`) run with Social Security exactly as
  the scenario configures it; `claim-age` runs with roth_conversions/
  ira_distributions/inherited_accounts exactly as configured too. Since
  claim age changes the household's Social Security income, which shifts
  how much bracket/IRMAA room a Roth conversion has each year, the true
  joint optimum across BOTH could differ from optimizing each separately
  -- run both and compare by hand if you want a sense of how much that
  might matter for a given household. Retirement age, withdrawal order,
  or other `scenario` sections are held fixed at whatever the input
  scenario says by every subcommand here. Use `scenario_engine`'s
  `optimize` (a discrete grid search over externally-specified parameter
  values, a different kind of search from either of these) to explore
  those separately.
