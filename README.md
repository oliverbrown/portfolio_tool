# Portfolio Tool (pt) — v1.67

A small local tool to import brokerage account exports, classify holdings by
asset class, and check your household allocation against your IPS targets.
Everything runs locally — your data stays in a SQLite file on your machine,
nothing is sent anywhere. Currently supports importing Fidelity's "Portfolio
Positions" CSV export; other brokerages could be added as separate parsers
later (see "Ideas" below).

**New here?** See [`QUICKSTART.md`](QUICKSTART.md) for a five-minute
walkthrough using the synthetic data in [`test_data/`](test_data/) — nothing
below touches your real accounts until you're ready.

## What v1.67 does

- Imports one or more brokerage CSV exports -- Fidelity, Schwab, and
  E*TRADE, auto-detected per file (Vanguard not yet -- see "Getting your
  brokerage export" below) -- each optionally tagged with an account
  owner; accounts that appear in more than one file are automatically
  recorded as owned by "Joint"
- Stores every import as a timestamped snapshot in SQLite (so you keep history)
- Maintains a security classification table (ticker → asset class) that you
  build up over time, seeded with ~40 common index funds/ETFs
- Supports **multi-class Fund classifications** for holdings that mix asset
  classes (target-date funds, balanced funds), with weighted splits and an
  automatic "Other" remainder
- Infers account type (Brokerage / IRA / Roth IRA / Inherited IRA / Inherited
  Roth IRA / 401(k) / HSA) from the account name Fidelity gives it
- Computes household asset allocation (by asset class, by account type, and
  by individual account), with an account-level total shown in the
  "By Account" breakdown
- Compares actual allocation to your IPS targets and flags drift
- Produces a text rebalancing report and an Excel workbook with one tab per view
- `-d`/`--debug` on `import` and `report` for troubleshooting: shows the
  inferred account type per account at import time, and (in the report)
  exactly which accounts feed into each ALLOCATION BY ACCOUNT TYPE total
- `accounts list` / `accounts set-type` to manually correct an account whose
  name gives no hint of its type (e.g. an employer 401(k) plan named after
  the company) — the override persists across every future import of that
  account
- **Per-account IPS targets**: `targets set ... --account ACCOUNT_NUMBER`
  overrides the household target for one account and one asset class; any
  asset class without an override falls back to the household target. The
  report gets a new "by account" comparison section for accounts using this
- **Investment style** (Morningstar-style style-box weights, e.g. "Large
  Blend 60%, Large Growth 40%") and **sector weights** per symbol, settable
  by hand (`style set`, `sector set`) or bulk-loaded from a Fidelity "Style
  Diversification" / "Sector Diversification" export (`seed-styles`,
  `seed-sectors`) — both show up in the Holdings tab of the Excel workbook
- **Expense ratio** per symbol (`expense-ratio set`), also shown in the
  Holdings tab
- **Account-level expense ratio** (`accounts set-expense-ratio`) -- a flat
  fee (e.g. a managed account's advisory fee), ADDING TO (not replacing)
  its holdings' own per-symbol expense ratios
- **Investment expense summary** in `report`: each account's calculated
  blended expense ratio and annual dollar cost (holdings' own expense
  ratios, weighted by value, plus any account-level flat fee), a
  household total, and a note showing how much value has no
  expense-ratio data on file -- new "Investment Expenses" tab in the
  workbook, new "INVESTMENT EXPENSE SUMMARY" section in the text report
- **Advisory expense in `project`** (optional,
  `scenario.advisory_expense.pct_of_portfolio`): a single global
  advisory/AUM fee the household supplies directly, applied to total
  portfolio value every year and paid taxable-then-Traditional-then-
  Roth like any other cash need -- new **Advisory Exp.** column.
  Deliberately separate from (and NOT derived from) the account-level/
  per-symbol expense ratios above, which stay informational-only,
  reported by `report` and never modeled in a projection
- **Two-level asset-class hierarchy**: every asset subclass (`US Equity`,
  `CDs`, `Gold`, ...) belongs to one macro class (`Growth`, `Income`,
  `Liquidity`, `Alternatives`, `Insurance`). IPS targets can be set at either level,
  independently and optionally, and the report shows actual totals -- and
  target comparisons where set -- at both levels
- **Retirement planning profile** (household names/birthdates) and
  **retirement scenario** (retirement ages, spending, inflation, returns,
  Social Security claim ages) -- two hand-edited YAML files; a household has
  one profile but can keep several scenario files to compare "what if"s
  against it -- see "Retirement planning profile" / "Retirement scenario" below
- **Growth projection** (`project`): a year-by-year cash-flow table from
  today through age 100, tracking Taxable/Traditional (per person)/Roth (per
  person)/Inherited balances separately -- portfolio growth, Social
  Security, RMDs (including inherited-account SECURE Act 10-year-rule
  schedules), federal tax, and a taxable-then-Traditional-then-Roth
  withdrawal order; `-v`/`--verbose` adds a column for every account over
  $500; also shows up as a "Projection" tab in the `report` workbook when a
  profile and scenario are configured -- see "Growth projection" below
- **Roth conversions** (optional, `scenario.roth_conversions`): model
  converting a chosen amount per year from one person's own Traditional
  bucket to their own Roth bucket over a date range -- taxed as ordinary
  income the year it happens, shown in a new "Roth Conversion" column -- see
  "Growth projection" below
- **Roth conversion constraints** (optional, `roth_conversions[].
  constraints`): instead of a flat amount, cap what converts each year by a
  target federal bracket, a Medicare IRMAA tier to avoid, a flat per-year
  dollar limit, and/or a Taxable-bucket cash reserve to preserve -- a
  greedy per-year heuristic (see "Growth projection" below)
- **Medicare Part B premium + Part D IRMAA surcharge**: modeled for anyone
  65+, using the real 2-year income lookback, with three new columns
  (Medicare Part B, Medicare Part D IRMAA, IRMAA Tier) -- see "Growth
  projection" below
- **IRA distributions** (optional, `scenario.ira_distributions`): same
  mechanics/constraints as a Roth conversion, but the money lands in the
  Taxable bucket instead of Roth -- a plain Traditional withdrawal, taken
  before it's needed for spending -- see "Growth projection" below
- **Rollovers** (optional, `scenario.rollovers`): a one-time event moving a
  percentage or flat amount from an owner's 401(k) to their IRA -- not a
  taxable event -- see "Growth projection" below
- **Taxable cash flows** (optional, `scenario.taxable_cash_flows`): a
  hypothetical one-time deposit or withdrawal applied directly to the
  Taxable bucket in a given year (e.g. an inheritance, a large planned
  purchase) -- deliberately untaxed -- see "Growth projection" below
- **Hypothetical inherited accounts** (optional,
  `inherited_accounts[].hypothetical_value`): model a future inheritance
  you don't yet hold -- it "appears" in the simulation at its configured
  value once its (assumed) inheritance year arrives -- see "Growth
  projection" below
- **Mortality** (optional, `scenario.mortality`): model either or both of
  you dying on independent dates -- a survivor's Traditional/Roth accounts
  absorb the deceased's (a spousal rollover), Social Security becomes the
  larger of the two benefits, and federal filing status switches from MFJ
  to Single (meaningfully smaller brackets/deduction). If both dates are
  set, the projection stops at the later one and reports the estate's
  value instead of running to age 100 -- see "Growth projection" below
- **401(k) contributions** (optional, `scenario.retirement_contributions`):
  model maxing out employee elective deferrals plus catch-up (including
  SECURE 2.0's enhanced catch-up for ages 60-63) every year until each
  person's own retirement, with SECURE 2.0's mandatory Roth catch-up rule
  for high earners, plus an optional employer match (a flat amount or a
  percent of salary) -- new "401k Trad.", "401k Roth", and "Er Match"
  columns -- see "Growth projection" below
- **Private health insurance** (optional, `scenario.health_insurance`):
  an estimated monthly cost per person, applied from THEIR OWN
  retirement (not the household's combined retirement date) until they
  reach Medicare eligibility (65) -- the real gap between retiring early
  and Medicare starting -- new "Health Ins." column, inflated forward
  like a wage -- see "Growth projection" below
- **Cumulative Healthcare Expense column**: a running total of Medicare
  Part B + Part D IRMAA + private health insurance since the start of the
  projection -- see "Growth projection" below
- **Automatic sector/expense-ratio/style fetching** (optional,
  `scripts/update_market_data.py`): looks up your currently-held symbols on
  Yahoo Finance and proposes updates -- kept as a separate, explicitly-run
  script since it's the one thing in this project that makes a network
  call; the core tool still never does -- see "Investment style, sector,
  and expense ratio" below
- **Style category writes**: a symbol whose Yahoo Morningstar category
  (`funds_data.fund_overview['categoryName']`) is an EXACT match to one of
  pt's STYLE_CATEGORIES is now proposed as a 100%-that-category write by
  `update_market_data.py`, same dry-run/`--apply`/`--overwrite-existing`
  rules as sector/expense ratio; `--show-style-categories` additionally
  reports non-matching categories too (informational only)
- **Expanded style categories**: `pt/attributes.py`'s STYLE_CATEGORIES now
  also covers fixed-income, international, and target-date funds
  (Intermediate Core Bond, Long Government, Ultrashort Bond, Intermediate
  Government, Target-Date 2035, Foreign Large Blend), on top of the
  original 3x3 domestic-equity style box -- added once `--show-style-
  categories` surfaced held funds Yahoo classified into these
- **Sector-weight rounding fix**: Yahoo's per-sector floats can sum to a
  hair over 100% (floating-point representation error, not bad data) --
  `update_market_data.py` now scales proportionally back to 100% instead
  of letting `attributes.set_sector()`'s "can't exceed 100%" check crash
  the whole run partway through
- **Raw yfinance data reporting** (`--show-raw-data` on
  `update_market_data.py`): shows the underlying raw Yahoo fields behind
  the sector/expense-ratio/style proposals -- the unmapped sector-weight
  dict, all three possible expense-ratio fields with their original
  values, and the raw fund-overview dict -- informational only, for
  sanity-checking the automatic mapping/conversion logic -- see
  "Investment style, sector, and expense ratio" below
- `backup` writes a timestamped copy of the database and planning profile to
  `~/.portfolio_tool/backups/`
- **`report -v`/`--verbose`**: the workbook's Projection tab now supports
  the same per-account columns as `project --verbose` -- previously the
  flag didn't exist at all on `report`, so passing it errored with
  "unrecognized arguments"
- **`report --public` (now the default) / `--private`**: `report` produces
  a shareable view by default -- no Account Number column, no Holdings /
  Investment Expenses / IPS Comparison / IPS by Account / Unclassified
  tabs, no Summary IPS-target tables; the text report loses its
  IPS-target / by-account / unclassified / expense sections and all
  `#...1234` account suffixes; and the Projection/Monte Carlo scenario
  dump has its `account_number`s redacted. `--private` restores the full
  workbook + text report. Either way, the **Accounts tab now shows each
  inherited account's decedent date of birth, date of death, and SECURE
  Act 10-year deadline** (from `scenario.inherited_accounts`). See
  "Public vs. private report" below.
- **`report --public` also redacts the source CSV filename(s)**: the
  Summary tab and text-report header showed the imported file's basename
  verbatim (e.g. `Portfolio_Positions_Aug-26-2026-alex.csv`), which can
  embed a household member's name -- now shown as `<redacted> (N files)`
  in the public view; unchanged in `--private`.
- **`--per-bucket-growth`** (`project`/`monte-carlo`/`report`): each tax
  bucket (Taxable, each Traditional owner/type pool, each Roth owner,
  each Inherited account) grows at its OWN blended rate from what THAT
  bucket actually holds, instead of one household-wide rate applied to
  every bucket. Monte Carlo still draws one shared, correlated
  per-asset-class economy each year (see `build_bucket_correlated_
  return_sampler()`) -- each bucket just weights that same draw by its
  own mix, so buckets stay correlated with each other but realize
  genuinely different results. Text output and the Excel MODEL
  PARAMETERS block both show a household summary rate plus the
  per-bucket breakdown underneath. Opt-in -- default behavior (one rate
  for the whole portfolio) is unchanged; `scenario_engine` never passes
  this and is unaffected. `roth_optimizer` gained its own
  `--per-bucket-growth` in v1.53 (see below). See "Per-bucket growth"
  below.
- **Bug fix**: `report`'s Projection/Monte Carlo tabs (including
  `--per-bucket-growth`) always used the database snapshot's real
  accounts for their own math, even when `--scenario` pointed at a
  self-contained `hypothetical_accounts` scenario -- silently ignoring
  it. `build_workbook()` now branches the same way `project`/
  `monte-carlo` already did: a hypothetical scenario's growth rate,
  per-bucket breakdown, and simulation come entirely from its own
  made-up household; every other tab (Holdings, Allocation, IPS
  comparisons) still reports on the real snapshot, since those have
  nothing to do with the scenario. Also extracted the "self-contained
  hypothetical scenario doesn't need `--profile`" rule (previously
  `project`/`monte-carlo`-only) into `planning.resolve_profile()`, now
  shared with `report` too -- it no longer needs a `--profile` file (or
  errors if none exists) for a scenario that embeds its own `people`.
- **New macro class `Insurance`, subclass `Fixed Indexed Annuities`**:
  added to `classifier.ASSET_CLASS_HIERARCHY` -- `classify`/`targets
  set`/IPS comparisons/the Allocation tab all pick it up automatically
  (nothing hardcodes the old 4-macro list). For `returns.asset_classes`,
  it gets a documented default volatility (5.0 pts -- a principal-
  protected, index-linked contract realizes far less volatility than the
  index it tracks) and default correlations (mildly positive to Growth,
  low to Income/Liquidity/Alternatives -- see
  `pt/projection.py`'s `DEFAULT_VOLATILITY_BY_MACRO`/
  `DEFAULT_MACRO_CORRELATIONS`), and counts as fixed-income-like for the
  older 2-bucket `returns.growth`/`.fixed_income` scenario shape. All
  overridable per scenario, same as every other class.
- **`Fixed Indexed Annuities` gets a realistic Monte Carlo model**:
  instead of an independent Normal(mean, volatility) draw like every
  other class, it now CREDITS a floored-and-capped copy of another
  class's own draw that year (`returns.asset_classes.<class>.tracks` /
  `.floor` / `.cap`) -- the actual mechanism behind an FIA's
  point-to-point index crediting (principal-protected, so a 0% floor;
  upside on the tracked index -- typically the S&P 500, i.e. `US
  Equity` -- capped well below the index's own volatility). Built-in
  default for "Fixed Indexed Annuities" specifically (tracks `US
  Equity`, floor 0%, cap 8%) if you give it just an `expected_return`;
  set your own `tracks`/`floor`/`cap` to match a real contract's terms,
  or `tracks: false` to fall back to a plain independent draw. The
  tracked class doesn't have to be something the household separately
  holds. `project`'s deterministic run and every class's own
  `expected_return` are unaffected -- this only reshapes the Monte
  Carlo year-to-year draw. See `pt/projection.py`'s
  `resolve_asset_class_tracking()`/`DEFAULT_TRACKING`, and the
  MODEL PARAMETERS block on `report`'s Projection/Monte Carlo tabs,
  which now shows each tracking class's tracks/floor/cap.
- **`scripts/run_scenario.sh` now generates both reports**: it runs
  `report` twice per scenario -- `<stem>_private_report.{xlsx,txt}` and
  `<stem>_public_report.{xlsx,txt}` -- instead of one `--private`-only
  `<stem>_report.{xlsx,txt}`.
- **`.gitignore` widened** to cover `scripts/run_scenario.sh`'s
  `<scenario-stem>_{project,mc,report}.{png,xlsx,txt}` output naming
  (plus `report --private`/`--public` and other ad hoc `--out` names like
  `mc.xlsx`/`monte_carlo.png`/`retirement_scenario_1.xlsx`) and
  SlickEdit's `*.vpwwildcardcache`, so running the tools by hand stops
  leaving untracked scratch files in the repo root.
- **`report --monte-carlo`** (+ `--trials`/`--seed`): also runs a Monte
  Carlo simulation from the same scenario/profile and adds a "Monte Carlo"
  tab right after "Projection" -- the percentile fan-chart table and chart,
  same as the standalone `monte-carlo` command. Both tabs now also carry a
  **MODEL PARAMETERS** block (blended return, and for Monte Carlo the
  blended volatility / trials / seed, plus every asset class's resolved
  return and volatility assumption) above the full scenario YAML dump.
  `scripts/run_scenario.sh` passes `--monte-carlo` through, so its
  `*_report.xlsx` carries the fan chart too.
- **Workbook layout**: on the Projection and Monte Carlo tabs/workbooks
  the chart(s) now sit directly below their data table, with the MODEL
  PARAMETERS block and full scenario YAML moved underneath -- so each
  table stays next to its own chart at the top of the sheet instead of a
  long scroll past the config to reach it
- **Bug fix**: the text report's `--debug` section (accounts contributing
  to each account type) used a fixed-width account-name column, which
  could overflow for a longer real account name (e.g. "Cash Management
  (Individual - TOD)") and throw off alignment for every column after it
  in that row. Now sized dynamically from the longest account name
  actually being printed.
- **Scenario configuration in `project`'s output**: the raw scenario dict
  (exactly as loaded), dumped back out as YAML -- the same format the
  scenario file itself uses -- now appears at the top of every `project`
  run's printed/text output, and again at the bottom of the sheet for
  `project --out *.xlsx` -- so a saved run is self-documenting about
  which assumptions produced it -- see "Growth projection" below
- **`project --out` is repeatable**: pass it more than once (e.g. `--out
  projection.txt --out projection.xlsx`) to save every format you name in
  one run instead of running `project` again per format
- **`scenario_engine/`, a separate companion tool**: generates a matrix
  of scenario YAML files from a base scenario + a sweep config (min/max/
  step per varied field), then batch-runs `pt`'s own projection engine
  against a whole directory of scenarios, producing a `.txt`/`.xlsx` per
  scenario plus a summary comparing outcomes across all of them -- run
  as `python3 -m scenario_engine.cli generate`/`run`, the same way as
  `pt.cli`, but a separate package -- see `scenario_engine/README.md`
- **`.gitignore` added**: Python bytecode caches, `.DS_Store`, this
  household's own personal data (Fidelity CSV exports,
  `retirement_scenario*.yaml`/`retirement_profile.yaml` -- at any depth,
  so a copy placed inside the repo by mistake is still caught),
  generated report/projection output, and `scenario_engine/`'s default
  output directories are all excluded now. The 18 `pt/__pycache__/*.pyc`
  files that had been tracked since early on are untracked too (removed
  from git's index only -- nothing deleted from disk).
- **`scenario_engine`'s sweep config can now vary `roth_conversions`/
  `ira_distributions`/`rollovers`/`retirement_contributions` fields**,
  using a `section[owner]` selector (e.g.
  `roth_conversions[alex].constraints.max_marginal_bracket`) to pick
  which list entry a path targets -- previously only fields inside a
  nested mapping (not a list) could be swept -- see
  `scenario_engine/README.md`
- **`scenario_engine optimize`**: a third `scenario_engine` command,
  alongside `generate`/`run`, for a different question than a plain
  sweep answers -- "for each combination of these (outer) parameters,
  which combination of these OTHER (inner) parameters minimizes/
  maximizes a given objective?" Searches entirely in memory (no file
  written per candidate), keeping only each outer combination's winner
  -- tens of thousands of candidate projections run in seconds, not
  hours, and only the results that satisfy the goal are ever written to
  disk. See `scenario_engine/README.md`.
- **`scenario_engine`'s generated filenames are shorter and collision-
  safe**: each swept field now shortens to just its distinguishing part
  (e.g. `alex_max_conversion_per_year` instead of the full
  `roth_conversions_alex_constraints_max_conversion_per_year`) --
  fixes a real failure where a several-parameter sweep produced
  filenames long enough that Excel refused to open the result. Two
  fields that would shorten to the same label fall back to their full
  path automatically; an extreme case gets its labels clipped (never a
  hash) rather than the whole name replaced with something opaque.
- **`scenario_engine run`/`optimize` gain `-v`/`--verbose`**: adds the
  same per-account columns as `pt.cli project --verbose` to each result
  (for `optimize`, computed once for each outer combination's winner
  only, so it doesn't slow the search itself down).
- **`scenario_engine optimize` can minimize AND maximize at the same
  time**: `minimize_sum_of` and `maximize_sum_of` can now both be given
  together (previously exactly one was required) -- combined into one
  score that pushes the minimized fields down and the maximized fields
  up simultaneously. A `maximize_sum_of`-only objective now reports its
  natural (positive) value instead of a confusing negation. An
  unrecognized key under `optimize` (e.g. a typo like
  `maximumize_sum_of`) now fails immediately with a suggested
  correction, instead of being silently ignored. See
  `scenario_engine/README.md`.
- **Renamed from "Fidelity Household Portfolio Tool" (`fpt`) to "Portfolio
  Tool" (`pt`)**: the Python package is now `pt` (`python3 -m pt.cli ...`),
  and the local config/data directory moved from the old `~/.fidelity_tool/`
  to `~/.portfolio_tool/` (rename your existing directory once by hand if
  upgrading -- there's no auto-migration). "Fidelity" now only appears
  where it names the actual import format (`pt/importer.py`'s
  `parse_fidelity_csv`, the `FidelityImportError` exception, and the CSV
  layout itself)
- **`QUICKSTART.md`** and **`test_data/`**: a synthetic two-person
  household (11 accounts, every account type `pt` recognizes, one joint
  account split across two files) plus a matching profile/scenario, so a
  new user (or a regression check) can run `import` through `project`
  without touching real data
- **Refined asset-class returns** (`scenario.returns.asset_classes`,
  recommended): give each asset class you actually hold -- macro (e.g.
  `Growth`, `Liquidity`) or subclass (e.g. `US Equity`, `International
  Equity`, `US Bonds`) -- its own expected return AND volatility, instead
  of one flat `growth`/`fixed_income` pair. `project`/`monte-carlo`/
  `roth_optimizer`/`scenario_engine` all blend these by your household's
  ACTUAL current dollar allocation in each class (the same classification
  `pt.cli report`'s Asset Allocation section already shows). A class you
  don't itemize falls back to its macro's own entry if you gave one, else
  a clear error naming exactly which class is unconfigured. The older
  2-bucket `returns.growth`/`.fixed_income` shape still works unchanged
  for any scenario that doesn't need this -- see "Retirement scenario"
  below for the full shape and fallback rules.
- **`pt.cli monte-carlo`**: a Monte Carlo variant of `project` -- instead
  of holding one blended growth rate constant for the whole projection,
  runs it many times (`--trials`, default 1000), letting EVERY year of
  EVERY trial draw its own return independently. With `returns.
  asset_classes` configured, this means every asset class you hold draws
  its OWN correlated random return that year (equities move together more
  than equities vs. bonds; cash barely moves at all -- a documented
  default correlation table, overridable via `returns.correlations`) and
  sums by weight into that year's realized portfolio return -- not one
  blended Normal draw for the whole portfolio. This captures BOTH
  sequence-of-returns risk (a bad year early in retirement hurts more
  than the same bad year late, even with an identical average return) AND
  real diversification (classes that don't move in lockstep partially
  cancel out), neither of which a single deterministic run, or a plain
  single-blended-draw Monte Carlo, can show. Reports a success rate (the
  fraction of trials that never ran short) and a 10th/50th/90th-percentile
  balance band per year, instead of one row per year, plus the median
  final balance and median cumulative tax paid while retired across all
  trials -- useful for comparing two scenarios (e.g. Roth conversions on
  vs. off) on tax cost, not just success rate/ending balance, since a
  scenario can win on one and lose on the other. `--seed N` for a
  reproducible run, `--out` for text/Excel, same as `project` -- see
  "Monte Carlo projection" below
- **Hypothetical accounts** (`scenario.hypothetical_accounts`, optional):
  run `project`/`monte-carlo` against a made-up household -- NO CSV
  import, NO database snapshot, NO ticker classification -- so someone
  can try either command immediately. Each account is just an
  account_number/account_type/owner/value plus EITHER one `pct_growth`
  number (what fraction of that account counts as growth-like/equity for
  the blended growth rate -- the original, coarser option) OR an
  `asset_classes` map (per-class percentages, same names as `returns.
  asset_classes`, for finer control) instead of real classified holdings.
  Present in the scenario file, this REPLACES the database entirely for
  that run (`--db`/`--snapshot` are ignored). Such a scenario can also
  embed its own `people` section (same shape as the profile file), so a
  single
  self-contained scenario file needs no separate profile file either --
  see "Hypothetical accounts" below
- **MIT License** -- see `LICENSE` and "License" below
- **Regression suite** (`tests/`, stdlib `unittest`, no new dependency) --
  runs entirely against `test_data/`'s synthetic household in throwaway
  temp databases; see "Project layout" and `tests/README.md` for what it
  covers
- **Disclaimer** -- a plain-English restatement of the MIT License's
  liability clause plus a recommendation to double-check results with a
  fiduciary advisor before acting on them -- see "Disclaimer" below
- **`roth_optimizer/`** (new companion package): a true multi-year Roth
  conversion optimizer -- searches the WHOLE `scenario.roth_conversions`
  schedule at once (a separate dollar amount per owner per year) to
  maximize an "after-tax total estate value" objective (Roth/Taxable
  dollars pass to heirs at full value, Traditional dollars discounted by
  an assumed `--heir-tax-rate`), instead of `project`'s existing
  per-year-greedy constraint heuristic. A dependency-free coordinate-
  ascent/pattern search, not a call into an external solver -- finds a
  good schedule, not a certified global optimum. `python3 -m
  roth_optimizer.cli optimize --heir-tax-rate 24` -- see
  `roth_optimizer/README.md`
- **`scenario.pre_retirement_income`** (optional): each still-working
  person's estimated GROSS W-2 income (Box 1, "Wages, tips, other
  compensation") -- deliberately NOT total AGI (which can double-count
  investment income this tool already tracks separately via the taxable
  bucket) and NOT gross pay before payroll deductions (Box 1 already
  excludes pre-tax 401(k)/HSA/etc. contributions, correctly -- those
  aren't taxable). Either a single flat number (inflated forward like a
  wage) or a per-year LIST (one already-nominal dollar amount per year,
  no further inflation -- same convention as `roth_conversions`' own
  `annual_amount` lists, requiring an explicit `start_date` to anchor it)
  for a household with a real, non-flat trajectory a flat growth rate
  can't represent, e.g. a one-time bonus year. So that a
  `roth_conversions`/`ira_distributions` entry that starts BEFORE that
  person's own retirement (nothing stops that) gets its
  `max_marginal_bracket`/`avoid_irmaa_tier` constraints evaluated against
  a realistic income baseline instead of the zero this tool would
  otherwise assume for a still-working person -- also feeds a later
  year's Medicare/IRMAA premium lookback correctly, AND makes that year's
  tax on the household's own RMD/conversion/distribution income an
  INCREMENT on top of this baseline (tax on baseline+own-income minus tax
  on baseline alone -- see `pt/projection.py`'s `_household_tax_owed()`),
  so that income is taxed at the REAL marginal bracket it actually falls
  in once wages are stacked on, instead of understating it as if the
  household had no other income. Still never bills the wage income's own
  tax to the portfolio -- this tool still has no wage-income/payroll-tax
  model at all -- a household is assumed to cover that from its own
  paycheck/withholding. See "Retirement scenario" below and
  `roth_optimizer/README.md`'s "Starting conversions before retirement"
- **Charts for `project`/`monte-carlo`**: `--out *.xlsx` now embeds native
  Excel charts (no new dependency, openpyxl's own chart support)
  alongside the data table it already wrote -- balance by bucket and
  cumulative tax/healthcare expense for `project`, total balance's
  10th/median/90th percentile for `monte-carlo`. Styled to match the PNG
  as closely as Excel's chart model allows: real numeric axes on both
  sides (round year multiples of 5 on the x-axis, a dollar axis that
  never goes negative and steps in clean 1/2/2.5/5/10-per-decade
  increments abbreviated as "$2.5M" rather than the full dollar amount),
  16pt/14pt fonts, and a white-boxed legend. `--out *.png` (new, needs
  `matplotlib` -- see requirements-optional.txt, NOT a core dependency)
  saves the same data as a standalone chart image instead -- for
  `monte-carlo`, a shaded 10th-90th percentile band, which Excel's native
  charts can't easily do. See "Growth projection"/"Monte Carlo
  projection" below.
- **`roth_optimizer` gains `--per-bucket-growth` and a new `sweep`
  subcommand**: `optimize --per-bucket-growth` (opt-in, same convention as
  `pt.cli`) grows each tax bucket at its own blended rate throughout the
  search itself (not just in a follow-up `project` run) -- every
  `project()` call the search makes (the search itself,
  `zero_conversions_value`, `original_value`, and the final
  `actual_schedule`) uses the same bucket rates, so "optimal" is judged
  consistently. `python3 -m roth_optimizer.cli sweep --heir-tax-rates
  10,12,22,24,32,35,37` runs `optimize` once per rate (scenario/profile/
  accounts loaded once, not per rate) and prints every rate's numbers
  side by side -- deliberately WITHOUT declaring a single "best" rate,
  since `--heir-tax-rate` is an assumption, not something the search
  picks: a LOWER assumed rate always makes every dollar total bigger for
  ANY schedule (it's baked directly into the objective -- see "The
  objective" in `roth_optimizer/README.md`), so "the rate that scores
  highest" would trivially always be the lowest rate given and wouldn't
  mean anything. New convenience script **`scripts/run_optimizer.sh
  <scenario.yaml> [heir_tax_rate_or_csv_list] [output_dir]`** wraps both:
  a single rate behaves like before (saves
  `<stem>_optimized_<rate>.yaml`/`_projection.txt`, `--per-bucket-growth`
  ON by default -- unlike the CLI's own opt-in default, since that's what
  this script is for); a comma-separated list of rates (e.g.
  `10,12,22,24,32,35,37`) switches to `sweep` mode instead, still saving
  one file pair per rate. See `roth_optimizer/README.md`'s "Sweeping
  `--heir-tax-rate`" section.
- **Scenario files can `_include` another YAML file**: a top-level
  `_include` key (a path, or a list of them) recursively merges one or
  more other scenario files in underneath this one -- meant for a block
  several scenarios share (`returns.asset_classes`, the most commonly
  duplicated one) to live in ONE file instead of being copy-pasted into
  every scenario. Recursive dict merge (overriding one
  `returns.asset_classes` entry doesn't drop the rest an include
  provides), but a list (`roth_conversions`, `hypothetical_accounts`, ...)
  is always replaced wholesale, never merged item-by-item; a relative
  path resolves against the INCLUDING file's own directory, not the
  current directory; an include cycle raises a clear error instead of
  hanging. See "Sharing assumptions across scenario files" below and
  `pt/scenario.py`'s module docstring for the full rules. Also ships
  **`data/shared_returns_2026.yaml`**, a ready-to-`_include` capital
  market assumptions file (see its own header comments for sourcing).
- **`roth_optimizer` gains a `priority` subcommand**: `project()`'s
  roth_conversions step processes entries strictly in
  `scenario.roth_conversions`' own list order, accumulating a shared
  running ordinary-income total across owners within each year -- so
  when two owners' entries compete for the same household bracket
  ceiling, whoever's entry comes first gets first claim on it, and which
  owner that is was previously just an accident of file order. `python3
  -m roth_optimizer.cli priority --heir-tax-rate 24` runs the search
  twice (once with each owner prioritized, scenario/profile/accounts
  loaded once) and reports which ordering actually wins -- unlike
  `sweep`'s `--heir-tax-rate`, reordering doesn't mechanically bias the
  objective, so declaring a winner here is meaningful. New convenience
  script **`scripts/run_priority.sh <scenario.yaml> [heir_tax_rate]
  [output_dir]`**, mirroring `run_optimizer.sh`'s conventions
  (`--per-bucket-growth` on by default). See `roth_optimizer/README.md`'s
  "Comparing conversion priority" section.
- **`roth_optimizer` gains `--optimize-ira-distributions` (accelerated IRA
  drawdown)**: by default, the search only ever tunes
  `scenario.roth_conversions` -- `scenario.ira_distributions` (a plain
  taxable Traditional withdrawal taken before it's needed for spending)
  stays exactly as configured, untouched. This flag searches BOTH
  jointly, in the same pattern-search pass -- not two separate
  optimizations -- since a Roth conversion and an IRA distribution draw
  from the same Traditional dollars and compete for the same annual
  bracket/IRMAA room each year, letting the search decide, dollar for
  dollar, between converting to Roth (tax now, tax-free forever) or
  taking a plain taxable distribution (tax now, more liquid, partially
  taxed via cap gains going forward). Opt-in -- default behavior
  (roth_conversions only) is unchanged; a scenario with no
  `ira_distributions` section is unaffected either way.
  `scripts/run_optimizer.sh`/`run_priority.sh` both support
  `OPTIMIZE_IRA_DISTRIBUTIONS=1` (default off). Also added
  `ira_distribution_by_owner` to `project()`'s own row output (mirroring
  the existing `roth_conversion_by_owner`), needed to report each
  owner's ACTUAL per-year IRA distribution once a dynamic constraint
  clamps a request down. See `roth_optimizer/README.md`'s "Accelerated
  IRA drawdown" section.
- **`roth_optimizer` gains `--optimize-inherited-withdrawals` (inherited
  account drawdown sequence)**: an inherited account's
  `planned_withdrawal.annual_amount` (a FLOOR on top of that account's own
  legally-required SECURE Act 10-year-rule distribution -- `project()`
  always takes the greater of the two, and always forces the full
  remaining balance out by the 10th year regardless) now also accepts a
  per-year LIST, the same convention `roth_conversions`/`ira_distributions`
  already use. This flag searches that list as a THIRD family of decision
  variables -- how much EXTRA, above the legal minimum, to withdraw each
  year, e.g. to smooth out an account's naturally bracket-jumping
  shape (a growing annual stretch RMD plus a large forced lump sum in
  year 10) by taking more in earlier, lower-bracket years instead. Unlike
  the other two kinds, this one has no dynamic constraint mechanism of
  its own -- only the account's own remaining balance bounds it, and the
  search's own objective (not an explicit cap) discourages over-
  withdrawing. Needs an already-configured `planned_withdrawal` window on
  a REAL (not hypothetical) inherited account. Also added
  `inherited_distribution_by_account` to `project()`'s own row output,
  needed to disambiguate withdrawals when a household holds more than
  one inherited account. `scripts/run_optimizer.sh`/`run_priority.sh`
  both support `OPTIMIZE_INHERITED_WITHDRAWALS=1` (default off). See
  `roth_optimizer/README.md`'s "Inherited account drawdown sequence"
  section.
- **`roth_optimizer` gains a `claim-age` subcommand (Social Security
  claim age)**: `scenario.social_security.<name>` now also accepts
  `pia_monthly` (the Primary Insurance Amount -- the benefit claiming
  EXACTLY at Full Retirement Age would pay) as an alternative to the
  existing flat `monthly_benefit` -- new `pt/social_security.py` computes
  the claim-age-adjusted benefit itself (SSA's own early-reduction/
  Delayed-Retirement-Credit formula, matching its well-known published
  percentages exactly, e.g. 70%/132% at age 62/70 for a 67/66 Full
  Retirement Age). `python3 -m roth_optimizer.cli claim-age
  --heir-tax-rate 24` brute-forces every combination of whole-year claim
  ages (62-70) for every `pia_monthly`-configured name -- cheap enough
  (81 combinations for a couple) that no pattern search is needed, unlike
  the dollar-amount search. Unlike `sweep`'s `--heir-tax-rate`, claim age
  is a real decision, so this DOES declare a winner -- but **with no
  `retirement.life_expectancy` (or `spouse_life_expectancy`) configured,
  claiming LATER will almost always win** (more guaranteed dollars over
  an unbounded assumed lifetime, not a bug); `claim-age` prints that
  caveat whenever neither is set. This also changes what EVERY function
  in `roth_optimizer` scores against, not just `claim-age`: the whole
  package now values the estate as of the household's own stated
  `life_expectancy` (the LATER of the two, for a couple) instead of
  `pt.cli project`'s own last row -- `pt.cli project`/`report`/
  `monte-carlo` are entirely unaffected and keep using `scenario.
  mortality` (or age 100) exactly as before; the two settings are
  deliberately independent, so a household can plan against a different
  horizon than what a report displays. Falls back to `project()`'s last
  row if `life_expectancy` isn't configured for anyone, exactly as
  before this existed. Deliberately independent from the dollar-amount
  search (roth_conversions/ira_distributions/inherited_accounts run
  exactly as configured, for
  every claim-age combination tried) -- a documented scope boundary, not
  an oversight. New convenience script **`scripts/run_claim_age.sh
  <scenario.yaml> [heir_tax_rate] [output_dir]`**. See
  `roth_optimizer/README.md`'s "Social Security claim age" section.
- **`scenario.pre_retirement_income` now correctly prices its own tax
  cost, not just its bracket-room cap**: previously, a Roth conversion/
  IRA distribution/RMD started before that person's own retirement had
  its `max_marginal_bracket`/`avoid_irmaa_tier` constraints correctly
  capped against a realistic wage-inclusive income baseline, but the
  actual TAX WITHDRAWN for it was still computed as if that income were
  the household's ONLY income that year -- understating its real cost
  (and making `roth_optimizer`'s search indifferent to converting during
  high-wage years vs. after retirement) whenever wages pushed the real
  combined return into a higher bracket than the isolated calculation
  showed. `pt/projection.py`'s new `_household_tax_owed()` computes this
  year's tax as an INCREMENT instead -- the tax on (pre-retirement income
  + the household's own income) minus the tax on pre-retirement income
  ALONE -- so that income is taxed at the REAL marginal bracket it
  actually falls in, without ever billing the wage income's own tax to
  the portfolio (still assumed paid via wage withholding, same as
  always). Backward compatible: identical to before whenever nobody has
  pre-retirement income configured, or after everyone with one has
  retired. This also makes `roth_optimizer`'s search itself prefer
  smaller conversions during working years and larger ones after
  retirement, rather than being blind to the difference. New **Pre-Ret.
  Inc.** column in `project`'s table/`--verbose` output and the `report`
  workbook's Projection tab shows the wage baseline used each year, so
  Tax's real basis (versus Taxable Income, which stays the household's
  own income only) is visible instead of silently assumed.
- **`scenario.pre_retirement_income.<name>.estimated_agi` renamed to
  `estimated_gross_income`** (the old key now raises a clear error
  telling you to rename it): enter that person's GROSS W-2 income (Box
  1, "Wages, tips, other compensation") instead of AGI -- AGI can
  double-count investment income this tool already tracks separately via
  the taxable bucket, and gross pay before payroll deductions would
  OVERSTATE real taxable wages (Box 1 already excludes pre-tax
  401(k)/HSA/etc. contributions, correctly). Also gains **per-year LIST
  support**, same convention as `roth_conversions`' own `annual_amount`
  lists (one already-nominal dollar amount per year, no further
  inflation, anchored by a required `start_date`) -- for a household with
  a real, non-flat trajectory a flat number can't represent, e.g. a
  one-time bonus year. Backward compatible for the flat-number case
  (still inflates forward like a wage); a list is opt-in.
- **`scenario.liabilities`** (optional, new): model a future mortgage,
  car loan, or similar recurring debt payment. Unlike
  `income.target_spending`, the payment is a flat NOMINAL dollar figure
  that does NOT inflate forward (a fixed-rate loan payment doesn't grow
  with inflation -- it gets cheaper in real terms over time); unlike
  `health_insurance`/`retirement_contributions`, its window is a
  household-level `start_date`/`end_date` (by year), not tied to either
  person's own retirement -- so it can apply during working years, in
  retirement, or both. Give EITHER a flat `monthly_payment`/
  `annual_payment` directly (then `end_date` is required), OR the loan's
  own `principal` + `interest_rate` (a percentage) + `term_years`, which
  computes the standard fixed amortized payment itself -- the same
  formula a mortgage/auto-loan calculator uses -- so you don't have to
  already know the payment (`end_date` then optional, defaulting to a
  `term_years`-long window; give it explicitly to model an early
  payoff). Only affects cash flow -- added to `cash_need` alongside
  spending/health insurance/the advisory expense, drawn via the same
  taxable->Traditional->Roth withdrawal order -- deliberately no
  amortization/interest-vs-principal tracking, no mortgage-interest tax
  deduction, and no separate liability-balance ledger reducing net
  worth or the final estate value (the home/car itself isn't modeled as
  an asset either -- this is purely the payment's cash-flow effect). A
  down payment or other one-time cost at origination isn't part of this
  section -- use `taxable_cash_flows` for that (see "Retirement
  scenario" below). New **Liability Pmt.** column in `project`'s
  table/`--verbose` output and the `report` workbook's Projection tab.

- **`report` adds an optional "Optimizer Search" tab for an
  `*_optimized*.yaml`**: `roth_optimizer optimize --out-scenario ...
  --save-iterations ....html` now records the chart file as a
  `scatter_chart:` field on the saved scenario's first `roth_conversions`
  entry (path relative to the scenario file). `report --scenario` on that
  file adds a tab redrawing the search's lifetime-taxes-vs-estate-value
  scatter as a native Excel chart (Excel can't render the HTML itself)
  with a link to the interactive HTML. No field, no tab; a missing or
  unreadable chart file only prints a warning -- the report never fails
  over it. See `roth_optimizer/README.md`'s "Including the chart in
  `pt.cli report`".

## Setup

```bash
cd portfolio_tool
pip3 install -r requirements.txt
```

Requires Python 3.9+. Dependencies: `openpyxl`, `PyYAML`. (Optionally,
`pip3 install -r requirements-optional.txt` for `yfinance`, needed only by
`scripts/update_market_data.py` -- see "Investment style, sector, and
expense ratio" below; nothing else in this project needs it.)

## Getting your brokerage export

`pt` auto-detects which of these formats a CSV is -- no flag needed to
say which broker it's from, just pass the file to `import`.

- **Fidelity**: **Accounts & Trade → Positions → Download** (top right).
  Downloads a file like `Portfolio_Positions_Aug-22-2026.csv`.
- **Schwab**: **Accounts → Positions → Export** (or select one account
  first to export just that one). A file with "All Accounts" selected
  holds every account in one CSV -- `pt` reads each one out correctly.
- **E*TRADE**: **Portfolios → Positions → Download**. E*TRADE's own
  export is single-account-per-file and never states its own account
  number anywhere in the file -- pass one explicitly with `--account
  FILENAME:ACCOUNT_NUMBER` (see the `import` example below), or `pt`
  will tell you it's missing rather than guess.
- **Vanguard**: not supported yet -- investigated, but Vanguard's
  personal-account site doesn't currently offer a clean "all current
  holdings" CSV the way the three above do (only a cost-basis export
  limited to taxable brokerage accounts, or an 18-month transaction
  history, neither of which is a full point-in-time snapshot). Revisit
  if Vanguard adds one, or if a specific household needs it enough to
  build around the cost-basis export's taxable-only limitation.

## Workflow

```bash
# One-time setup: load the starter ticker classifications
python3 -m pt.cli seed-classifications

# One-time setup: set your IPS targets (fractions, must sum to ~1.0 at
# whichever level you use -- see "Asset class hierarchy" below). Macro and
# subclass targets are independent; use either, both, or neither.
python3 -m pt.cli targets set "Growth" 0.60
python3 -m pt.cli targets set "Income" 0.30
python3 -m pt.cli targets set "Liquidity" 0.05
python3 -m pt.cli targets set "Alternatives" 0.05
python3 -m pt.cli targets set "US Equity" 0.45
python3 -m pt.cli targets set "International Equity" 0.15
python3 -m pt.cli targets set "US Bonds" 0.25
python3 -m pt.cli targets set "International Bonds" 0.05
python3 -m pt.cli targets show

# Import your export (one file, or several at once if your household's
# accounts are split across multiple exports/brokers -- Fidelity, Schwab,
# and E*TRADE all work, auto-detected per file). Optionally tag each file
# with an owner, used for the account breakdown and future features like
# RMD calculations.
python3 -m pt.cli import ~/Downloads/Portfolio_Positions_Aug-22-2026.csv
python3 -m pt.cli import ~/Downloads/his_positions.csv "John" ~/Downloads/her_positions.csv "Jane"
# Mixing brokers -- E*TRADE needs --account (its own export never states
# its account number); place --account/--broker BEFORE the file list:
python3 -m pt.cli import --account etrade_export.csv:X1234 \
    ~/Downloads/his_positions.csv "John" ~/Downloads/schwab_export.csv "Jane" \
    ~/Downloads/etrade_export.csv "Jane"

# See what wasn't recognized (funds/stocks not in the seed list)
python3 -m pt.cli classify-unclassified
python3 -m pt.cli classify VTSAX "US Equity"
python3 -m pt.cli classify SOMEFUND "Fund" "US Equity" 0.45 "US Bonds" 0.20

# Generate the report (text printed to screen + saved, plus an Excel workbook)
python3 -m pt.cli report --out rebalancing_report.xlsx --text-out rebalancing_report.txt
```

Each month, just re-run `import` on the new export — it creates a new
snapshot without touching your classification or target settings, so
`snapshots` builds a history you can look back on.

```bash
python3 -m pt.cli snapshots                 # list all saved snapshots
python3 -m pt.cli report --snapshot 3        # report on a specific past snapshot
python3 -m pt.cli report --private           # the full internal workbook + text report
```

### Public vs. private report

`report` produces a **public** (shareable) view by default; pass `--private`
for the full internal one. The public view drops everything that identifies
individual accounts or is internal-planning detail:

| | public (default) | `--private` |
|---|---|---|
| Account Number column (Accounts tab) | — | shown |
| Holdings tab | — | shown |
| Investment Expenses tab | — | shown |
| IPS Comparison + IPS Comparison (Macro) tabs | — | shown |
| IPS by Account tab | — | shown |
| Unclassified tab | — | shown |
| Summary tab's two IPS-target tables | — | shown |
| text report: IPS-target / by-account IPS / unclassified / expense sections, `#...1234` suffixes | — | shown |
| account numbers in the Projection/Monte Carlo scenario dump | `<redacted>` | shown |
| source CSV filename(s) (Summary tab + text report header -- these can embed a household member's name, e.g. `..._Aug-26-2026-alex.csv`) | `<redacted> (N files)` | shown |
| Summary, Allocation, Projection, Monte Carlo tabs | shown | shown |
| **Inherited-account decedent date of birth / date of death / 10-year deadline** (Accounts tab) | **shown** | **shown** |

The inherited-account decedent dates are new in both views: for every
`scenario.inherited_accounts` entry tied to a held account, the Accounts
tab now carries the decedent's date of birth, date of death, and the
SECURE Act 10-year "must be $0 by Dec 31 of" deadline year.

## Debugging an unexpected account-type total

If a total in ALLOCATION BY ACCOUNT TYPE (e.g. "Brokerage") looks higher or
lower than you expect, add `-d`/`--debug` to `report` to see exactly which
accounts are being summed into each type:

```bash
python3 -m pt.cli report -d
```

This inserts a "DEBUG: ACCOUNTS CONTRIBUTING TO EACH ACCOUNT TYPE" section
into the text report, listing every account (name, last-4, owner, value)
under the type it's currently bucketed into, tagged `[manual override]` if
you've corrected it. Since account type is inferred from the account name
(see "Where things live" below), an account with a name that doesn't hint at
its type -- e.g. an employer 401(k) plan named after the company, or an old
employer stock account -- will default to "Brokerage" even if that's not
what you'd call it. The same `-d`/`--debug` flag on `import` prints the
inferred type for each account right after import, which is the other place
to catch a bad inference.

Once you've spotted a wrong one, fix it permanently:

```bash
python3 -m pt.cli accounts list                       # find the account number
python3 -m pt.cli accounts set-type 12345678 "401(k)"     # override its type
```

This is keyed by account number rather than matching on the account name or
a holding's ticker, so it's precise (no risk of matching the wrong account)
and durable — it survives every future `import` of that account, since
`account_type` is never overwritten by a later import once it's been
inferred or manually set (only `account_name` and `owner` refresh on
re-import). Valid types: `Inherited Roth IRA`, `Inherited IRA`, `Roth IRA`,
`IRA`, `401(k)`, `HSA`, `Brokerage`.

## Classifying symbols

Most symbols are simple — 100% one asset class:

```bash
python3 -m pt.cli classify VTSAX "US Equity"
```

Some funds (target-date funds, balanced funds, asset-allocation funds) hold a
mix of asset classes. Classify those as a **Fund**, giving each class a
weight (a fraction of the holding's value):

```bash
python3 -m pt.cli classify SOMEFUND "Fund" "US Equity" 0.45 "International Equity" 0.15 "US Bonds" 0.20 "International Bonds" 0.05 "Money Market" 0.10
```

- If the weights don't add up to 100%, the remainder is automatically
  classified as **Other** (in the example above, 0.45+0.15+0.20+0.05+0.10 =
  0.95, so the remaining 5% becomes "Other" -- see "Asset class hierarchy"
  below for what "Other" means now that there's a fixed subclass list).
- If you give just one class after `Fund` with no weight, it's treated as
  100% of that class: `classify SOMEFUND "Fund" "US Bonds"`.
- Weights that add up to more than 100% are rejected.
- Re-running `classify` on a symbol (either form) replaces its previous
  classification entirely.

Fund holdings show up throughout the reports with their value already split
across classes — e.g. a $10,000 holding classified 45%/55% contributes $4,500
to US Equity and $5,500 to US Bonds in the allocation totals.

## Asset class hierarchy: macro classes and subclasses

Every holding is classified at the **subclass** level (`classify SYMBOL
"US Equity"`, as above); each subclass belongs to exactly one **macro**
class:

```
Growth
  US Equity
  International Equity
  REITs
Income
  US Bonds
  International Bonds
  Intermediate Treasuries (3-7 year)
  Long Treasuries (+7 Year)
Liquidity
  Cash
  CDs
  Money Market
  Short Treasuries (< 3 year)
Alternatives
  Cryptocurrency
  Commodities
  Gold
Insurance
  Fixed Indexed Annuities
```

Macro totals are a rollup of their subclasses, computed on the fly -- there's
nothing to keep in sync. **IPS targets can be set at either level**,
independently: a macro target, a subclass target, both, or neither, per
branch of the tree:

```bash
python3 -m pt.cli targets set "Growth" 0.60          # macro-level target
python3 -m pt.cli targets set "US Equity" 0.45        # subclass-level target, doesn't have to relate to Growth's 60%
python3 -m pt.cli targets clear "US Equity"          # remove it again (household-wide; add --account to clear one account's override)
```

The report shows two parallel "ACTUAL vs. IPS TARGET" sections -- by macro
class and by asset (sub)class -- each showing actual totals regardless of
whether a target is set for that row, and the target/diff/action columns
only where one is. The Excel workbook mirrors this with an "IPS Comparison
(Macro)" tab alongside the existing "IPS Comparison" (subclass) tab, and the
Allocation tab gets a "Macro Class" column. (The IPS-target sections/tabs
are private-view only -- see "Public vs. private report" above.)

`Other` is a deliberate escape hatch **outside** this hierarchy, for a
holding that genuinely doesn't fit any subclass above -- same idea as
`Unclassified`, but for something you've looked at and decided doesn't fit,
rather than something you haven't classified yet. You can't classify a
holding directly as `Other` (`classify SYMBOL "Other"` is rejected) -- it's
only reached as the automatic remainder when a Fund's weights don't add up
to 100%. It shows up in both the macro and subclass allocation totals as its
own unrolled-up line, so nothing silently vanishes from the total.

## Per-account IPS targets

Household-wide targets (`targets set "US Equity" 0.45`) are still the
default for every account. To give one account a different target for one
**subclass** -- e.g. a Roth IRA you deliberately run 100% equity because
it's the longest time horizon -- add `--account`:

```bash
python3 -m pt.cli targets set "US Equity" 1.0 --account 12345678
python3 -m pt.cli targets show --account 12345678     # effective targets: household + this account's overrides
```

Per-account overrides are subclass-only, not macro -- `targets set "Growth"
0.7 --account 12345678` is rejected, since the by-account report section works
at subclass granularity. Only the subclasses you override change for that
account; everything else still falls back to the household target (so the
account's own targets don't need to sum to 100% by themselves -- the
*effective* set, household plus overrides, is what should sum to 100%;
`targets show --account` warns if it doesn't). The report's household-level
"ACTUAL vs. IPS TARGET" sections are unchanged; a "by account" section
appears underneath them, but only for accounts that actually have an
override -- accounts with none would just duplicate the household numbers.

## Investment style, sector, and expense ratio

Three more pieces of per-symbol data, independent of asset-class
classification:

```bash
# Bulk-load from a Fidelity "Style Diversification" / "Sector Diversification"
# export (Accounts & Trade > Positions > Style/Sector diagnostics > Download).
# Seed data ships in data/style_seed.csv and data/sector_seed.csv.
python3 -m pt.cli seed-styles
python3 -m pt.cli seed-sectors

# Or set by hand -- same weighted-pairs syntax as `classify ... Fund`
python3 -m pt.cli style set VXUS "Large Blend" 0.6 "Large Growth" 0.4
python3 -m pt.cli sector set VXUS "Information Technology" 0.35 "Financials" 0.15
python3 -m pt.cli expense-ratio set VXUS 0.05    # a percentage: 0.05 means 0.05%, not 5%

python3 -m pt.cli style show VXUS
python3 -m pt.cli sector show VXUS
python3 -m pt.cli expense-ratio show VXUS
```

Like `classify ... Fund`, a symbol can be split across several style boxes
or sectors with weights (funds usually are); a single category with no
weight is treated as 100%. Seeding is additive like `seed-classifications`:
a symbol that already has style/sector data is left alone unless you pass
`--overwrite`, so it won't clobber anything you set by hand. None of this
feeds into asset-class allocation or the IPS comparison -- it's descriptive
data surfaced in the Holdings tab of the Excel workbook (blank for any
symbol you haven't set or seeded).

#### Account-level expense ratio and the expense summary

A per-symbol expense ratio (above) only covers a fund's own cost --
it doesn't capture a flat fee an account itself charges, e.g. a managed
account's advisory fee. Set one directly on the account instead:

```bash
python3 -m pt.cli accounts set-expense-ratio 12345678 1.00   # a percentage: 1.00 means 1.00%/year
```

This ADDS TO (never replaces) that account's holdings' own per-symbol
expense ratios -- the household's actual total cost is layered, not
either/or. `report` shows the result in a new **INVESTMENT EXPENSE
SUMMARY** section (text) / **Investment Expenses** tab (workbook): each
account's calculated blended
expense ratio and annual dollar cost (holdings' own ratios, weighted by
value, plus the account's flat fee if set), a household total row, and
-- if any holdings have no expense ratio on file -- a note showing how
much value that affects, since those holdings contribute $0 to the
calculation (not an assumed "average" cost), which UNDERSTATES the true
total whenever some data is missing. Like style/sector/expense ratio
above, none of this feeds into asset-class allocation or the IPS
comparison -- **and none of it feeds `project` either.** Per-symbol and
per-account expense ratios are informational-only, reported here by
`report` and nowhere else. If you want an advisory/AUM fee actually
modeled in the growth projection, set it directly in the scenario file
instead -- see `advisory_expense` under "Growth projection" below --
which is deliberately a separate, single number, not derived from
whatever's set here.

#### Fetching sector/expense-ratio/style data automatically

`pt`'s core commands never make a network call -- your data stays local
(see the top of this file). `scripts/update_market_data.py` is the one
deliberate exception: an optional, separately-run helper that looks up
your CURRENTLY HELD symbols on Yahoo Finance (via the `yfinance` package)
and proposes sector-weight/expense-ratio/style updates, so you don't have
to hand-enter every symbol.

```bash
pip3 install -r requirements-optional.txt      # installs yfinance

python3 scripts/update_market_data.py                    # dry run -- shows what WOULD change
python3 scripts/update_market_data.py --apply             # writes the proposed changes
python3 scripts/update_market_data.py --apply --overwrite-existing  # also replace symbols you already set
python3 scripts/update_market_data.py --symbol VTI --symbol VXUS    # just these, skip the DB lookup
python3 scripts/update_market_data.py --show-style-categories       # report only, see below
python3 scripts/update_market_data.py --show-raw-data               # report only, see below
```

What it can and can't get you:
- **Expense ratio** -- decent coverage for ETFs and mutual funds, though
  Yahoo's own data is occasionally stale or wrong for a specific fund
  (spotted this myself testing against real holdings) -- review the dry
  run before trusting it for a fund you know well.
- **Sector weights** -- excellent for individual stocks (one sector, 100%
  weight); good for large/common ETFs; spotty-to-absent for
  actively-managed mutual funds, which often have no Yahoo Finance
  profile at all.
- **Style category** (Large Blend, Small Growth, etc.) -- Yahoo Finance
  *does* carry a fund's Morningstar category for free (`funds_data.
  fund_overview['categoryName']`). When it's an EXACT match to one of
  pt's STYLE_CATEGORIES (the original 3x3 domestic-equity grid, plus
  Intermediate Core Bond, Long Government, Ultrashort Bond, Intermediate
  Government, Target-Date 2035, and Foreign Large Blend, added once real
  holdings surfaced them), it's proposed as a write -- 100% that category
  -- with the same dry-run/`--apply`/`--overwrite-existing` rules as
  sector and expense ratio, so a symbol that already has (finer) style
  data from `seed-styles` is left alone by default. A category with no
  exact match is never guessed at; `--show-style-categories` additionally
  reports those too (other bond funds, other target-date vintages, sector
  funds, etc.) so you can decide whether any are worth adding to
  STYLE_CATEGORIES. Yahoo gives one category per fund, not the finer
  weighted split (e.g. "Large Blend 60% / Large Growth 40%") a Fidelity
  "Style Diversification" export gives you via `seed-styles` -- that
  remains the way to get a weighted split rather than a single 100%
  category.

`--show-raw-data` prints the RAW, unprocessed Yahoo fields behind the
proposals above -- for each symbol with any: the sector-weight dict exactly
as Yahoo returned it (before mapping to pt's GICS categories), all three
possible expense-ratio fields side by side with their original values
(`netExpenseRatio`, `annualReportExpenseRatio`, `expenseRatio` -- only one
is usually populated, and they use different units, which is exactly the
kind of thing worth being able to see directly), and the raw
`fund_overview` dict (category, fund family, legal type). Also
INFORMATIONAL ONLY -- nothing here is written to the database; it exists
to sanity-check the mapping/conversion logic against what Yahoo actually
sent.

Since this data barely changes, results are cached at
`~/.portfolio_tool/market_data_cache.json` and only re-fetched once older
than `--max-age-days` (default 90; `--force-refresh` ignores the cache).
A cache entry missing a field this version of the script expects (e.g.
after an update adds a new field) is automatically re-fetched once, so
older cache entries self-heal without a full `--force-refresh`.
Symbols that don't look like real tickers (CUSIPs for individually-held
bonds, Fidelity's cash-sweep notation like `SPAXX**`, crypto pairs, plan
codes) are filtered out before ever touching the network. Dry run is the
default specifically so a scraped value never silently overwrites
something you or a Fidelity export already set -- review the proposed
changes, then re-run with `--apply` once they look right; a symbol that
already has sector/expense-ratio/style data is left alone unless you also
pass `--overwrite-existing`.

## Retirement planning profile

The household's fixed identity facts -- who's in it, and when they were
born -- live in a small YAML file:

```
~/.portfolio_tool/retirement_profile.yaml
```

Unlike everything else in this tool, there's no CLI command to *set* pieces
of it -- edit the YAML directly in a text editor. `profile show` reads it
back so you can confirm it parses:

```bash
python3 -m pt.cli profile show
python3 -m pt.cli profile show --file some/other/profile.yaml
```

It lives next to `portfolio.db` (same directory, same treatment: local-only,
never committed to this project's git repo), not as rows in the database.
Expected shape (values below are placeholders -- put your own household's
names and birthdates in your actual file):

```yaml
people:
  primary:
    name: jane                     # lowercase, matches a scenario's social_security key
    birthdate: 1972-07-14          # YYYY-MM-DD
  spouse:                          # optional -- omit for a single-person household
    name: john
    birthdate: 1975-11-22
```

`people.primary`/`people.spouse` name the two roles a retirement scenario
refers to (`retirement_age` vs `spouse_retirement_age`, below) and give each
a birthdate; a scenario's `social_security` section and `import`'s owner
names key off the same lowercase name, so a feature can go from "this
account's owner" to "their birthdate/claim age" without re-deriving who's
who. Retirement ages, spending, inflation, returns, and Social Security
claim ages -- the assumptions that vary by "what if" -- live in a separate
**retirement scenario** file instead, described next, since one household
(one set of birthdates) may want to compare several scenarios against them.

## Retirement scenario

The assumptions a growth projection (see "Growth projection" below) runs
against -- retirement ages, spending target, inflation, expected returns,
Social Security claim ages:

```
~/.portfolio_tool/retirement_scenario.yaml
```

Like the profile, it's hand-edited (no CLI `set` command), lives next to
`portfolio.db` (local-only, never committed), and `project` reads it
directly -- there's no separate `scenario show`. Expected shape (values
below are placeholders):

**Sharing assumptions across scenario files (`_include`)**: if you keep
several scenarios side by side (a base case, a hypothetical, an optimized
copy from `roth_optimizer` or `scenario_engine`, ...), a top-level
`_include` key merges another YAML file in underneath this one, so a
block like `returns.asset_classes` -- the one most worth sharing -- only
needs to live in one place:

```yaml
# shared_returns.yaml
returns:
  asset_classes:
    US Equity: {expected_return: 8.0, volatility: 18}
    US Bonds:  {expected_return: 4.5, volatility: 7}
    ...

# retirement_scenario_1.yaml
_include: shared_returns.yaml    # or a list: [shared_returns.yaml, shared_ss.yaml]
retirement: {retirement_age: 64}
...
returns:
  asset_classes:
    US Equity: {expected_return: 7.5, volatility: 18}   # overrides just this one class
```

A relative `_include` path resolves against the *including* file's own
directory (not your current directory), so a shared file can live
anywhere and every scenario that points at it stays correct regardless of
where each one lives; `~` is expanded. The merge is recursive for nested
mappings -- overriding one `returns.asset_classes` entry doesn't drop the
rest the shared file provides -- but a list (`roth_conversions`,
`hypothetical_accounts`, ...) is always replaced wholesale if both files
define it, never merged item-by-item. An included file can itself use
`_include`; a cycle raises a clear error instead of hanging. See
`pt/scenario.py`'s module docstring for the full rules.

```yaml
retirement:
  retirement_age: 64               # primary's target retirement age
  spouse_retirement_age: 60
  life_expectancy: 95               # informational only -- see "Growth projection"
  spouse_life_expectancy: 95

income:
  target_spending: 120000          # annual, in today's dollars

inflation:
  general: 2.5                     # percentage points, i.e. 2.5 means 2.5%
  medical: 5.0                     # optional -- Medicare/IRMAA only, see below;
                                    # falls back to general if not set

returns:
  # One expected_return/volatility pair per asset class you hold -- macro
  # (Growth/Income/Liquidity/Alternatives) or subclass (US Equity,
  # International Equity, US Bonds, ...; see pt/classifier.py's
  # ASSET_CLASS_HIERARCHY for the full list). Both percentage points.
  # volatility is optional per class (`monte-carlo` only -- a plain
  # `project` run ignores it -- falls back to a broad-market default if
  # unset); expected_return is required for any class you itemize. A
  # class you don't itemize falls back to its macro's own entry if you
  # gave one, else `project`/`monte-carlo` raise a clear error naming the
  # unconfigured class -- see "Documented simplifications" below for the
  # full resolution order.
  asset_classes:
    US Equity:              {expected_return: 7.5, volatility: 18}
    International Equity:   {expected_return: 7.0, volatility: 20}
    US Bonds:                {expected_return: 4.0, volatility: 7}
    Liquidity:               {expected_return: 2.5, volatility: 1}
  # Optional -- overrides the default correlation between two classes'
  # annual returns (`monte-carlo` only), keyed "ClassA,ClassB" (either
  # order). Otherwise a documented default table applies: distinct
  # subclasses under the same macro correlate at 0.85, different macros
  # per a rough historical 5x5 table (equities vs. bonds low, equities vs.
  # alternatives moderate, everything vs. cash near zero, Insurance mildly
  # positive to equities and low to everything else).
  #correlations:
  #  US Equity,US Bonds: 0.0
  # Optional per class -- for one whose Monte Carlo return isn't its own
  # independent draw at all, but instead CREDITS a floored-and-capped copy
  # of another class's draw that year (`tracks`) -- the mechanism behind a
  # Fixed Indexed Annuity's point-to-point index crediting. `floor`/`cap`
  # are percentage points, same units as expected_return; `floor` defaults
  # to 0.0 if omitted, `cap` is required (no universal default -- it's
  # your contract's own term). "Fixed Indexed Annuities" gets a BUILT-IN
  # default (tracks US Equity, floor 0%, cap 8% -- rough, real contracts'
  # caps vary ~4-12% by index/term/prevailing rates) if you give it just
  # an expected_return; set `tracks: false` to opt back out to a plain
  # independent Normal(mean, volatility) draw instead. Chained tracking
  # (tracking a class that itself tracks another) isn't supported.
  #  Fixed Indexed Annuities: {expected_return: 4.0}                          # uses the built-in default above
  #  Fixed Indexed Annuities: {expected_return: 4.0, tracks: US Equity, floor: 0, cap: 6}   # your contract's real cap
  #  Fixed Indexed Annuities: {expected_return: 4.0, tracks: false, volatility: 5}          # plain independent draw

# The older 2-bucket shape (no asset_classes section at all) still works
# unchanged for a scenario that doesn't need per-class detail:
#returns:
#  growth: 8.0                      # percentage points
#  fixed_income: 4.0
#  #growth_volatility: 18.0         # optional -- `monte-carlo` only;
#  #fixed_income_volatility: 4.0    # defaults to a broad-market assumption

# Optional -- used by a roth_conversions constraints.preserve_cash_reserve:
# true below. How many years of target_spending (counted forward from the
# current projection year) to keep untouched in the Taxable bucket.
cash_reserve:
  years: 2

social_security:
  jane:                            # key matches a people.primary/spouse.name
    claim_age: 70                  # from the profile
    monthly_benefit: 3800          # monthly, in today's dollars (SS is always quoted monthly)
  john:
    claim_age: 67
    # pia_monthly (an alternative to monthly_benefit above): the Primary
    # Insurance Amount -- the benefit claiming EXACTLY at Full Retirement
    # Age would pay -- and the tool computes the claim-age-adjusted
    # benefit itself (SSA's own early-reduction/Delayed-Retirement-Credit
    # formula -- see pt/social_security.py). Give exactly one of
    # monthly_benefit/pia_monthly, never both. Needed if you want
    # `roth_optimizer claim-age` to compare different claim ages for this
    # person -- see roth_optimizer/README.md's "Social Security claim
    # age" -- monthly_benefit alone can't be recomputed for a different
    # age.
    pia_monthly: 2600

# Optional -- only needed if the household holds an inherited IRA/Roth IRA
# (or wants to model a hypothetical FUTURE one it doesn't hold yet -- see
# the third entry below). See "Growth projection" below for what this
# drives (the SECURE Act 10-year rule) and its assumptions (non-spouse,
# non-EDB beneficiary).
inherited_accounts:
  - account_number: "555010203"    # matches an account_number in the database
    type: traditional              # "traditional" or "roth"
    decedent_birthdate: 1940-03-08
    decedent_death_date: 2023-01-20
    beneficiary: jane               # matches a people.primary/spouse.name
  - account_number: "555040506"
    type: roth
    decedent_birthdate: 1940-03-08
    decedent_death_date: 2023-01-20
    beneficiary: jane
    planned_withdrawal:            # optional -- see "Growth projection" below
      start_date: 2025-01-01
      end_date: 2030-12-31
      annual_amount: 50000
  # A HYPOTHETICAL future inheritance -- not held today. Appears in the
  # simulation starting decedent_death_date's year, at hypothetical_value.
  # Give your assumed decedent's real birthdate, but whatever death date
  # matches the year you want to model. No account_number for this kind.
  - type: traditional
    decedent_birthdate: 1950-06-01
    decedent_death_date: 2032-01-01
    beneficiary: jane
    hypothetical_value: 200000

# Optional -- one entry per planned Roth conversion window. Each year in
# [start_date, end_date] (by year), converts an amount from owner's own
# IRA(s) first, then their 401(k) if the IRA isn't enough, to their own
# Roth bucket. See "Growth projection" below for the full mechanics.
roth_conversions:
  # A flat amount every year in the window.
  - owner: jane                    # matches a people.primary/spouse.name
    start_date: 2026-01-01
    end_date: 2030-12-31
    annual_amount: 50000           # in today's dollars, not inflated

  # Or, constraints instead of (or on top of) annual_amount -- converts as
  # much as that YEAR's constraints allow, re-evaluated annually. Whichever
  # constraints are set, the smallest of their resulting caps wins -- see
  # "Growth projection" below for what each one means and its assumptions.
  - owner: john
    start_date: 2029-01-01
    end_date: 2039-12-31
    constraints:
      max_marginal_bracket: 24           # or "24%" -- must match a real bracket rate
      avoid_irmaa_tier: 1                # 0 (standard) - 5 (top surcharge); avoid_irmaa: true also
                                          # accepted as shorthand for avoid_irmaa_tier: 0
      max_conversion_per_year: 150000
      preserve_cash_reserve: true        # uses the cash_reserve.years policy above
                                          # (or give a number here directly instead)

# Optional -- structurally identical to roth_conversions (same fields, same
# constraints, just constraints.max_distribution_per_year instead of
# constraints.max_conversion_per_year) -- but the money lands in Taxable,
# not Roth. A plain Traditional withdrawal (ordinary income), taken before
# it's needed for spending -- e.g. to use up room in a low bracket while
# it's available. See "Growth projection" below.
ira_distributions:
  - owner: john
    start_date: 2026-01-01
    end_date: 2035-12-31
    constraints:
      max_marginal_bracket: 12
      max_distribution_per_year: 75000

# Optional -- one entry per ONE-TIME 401(k)-to-IRA rollover (the only
# direction modeled). Moves that owner's own 401(k) to their own IRA, NOT a
# taxable event. See "Growth projection" below.
rollovers:
  - owner: jane
    date: 2027-01-01               # a single date, not a window
    pct: 100                       # percent of the 401(k) balance (after that year's
                                    # growth) to roll over -- or use `amount` instead
  - owner: john
    date: 2028-06-01
    amount: 50000                  # a flat dollar amount instead of a percentage

# Optional -- one entry per hypothetical one-time cash event applied
# directly to the Taxable bucket. Deliberately untaxed -- see "Growth
# projection" below.
taxable_cash_flows:
  - date: 2029-03-01
    type: deposit                  # or "withdrawal"
    amount: 100000                 # positive, in today's dollars, not inflated

# Optional -- model either or both of you dying, on independent dates.
# Both keys are independently optional. If BOTH end up set (for a
# two-person household), the projection stops at the later date and
# reports the estate's value instead of running to age 100. See "Growth
# projection" below for the full mechanics (spousal account rollover,
# Social Security survivor treatment, Single filing status).
mortality:
  primary_death_date: 2055-06-01
  spouse_death_date: 2058-03-01

# Optional -- one entry per person still contributing to their own 401(k).
# Every year through the year BEFORE that owner's OWN retirement (not the
# household's combined later one), a contribution is made -- see "Growth
# projection" below for the full mechanics and pt/contributions.py for the
# actual IRS limits.
retirement_contributions:
  - owner: jane
    # Omit amount/pct_of_max entirely to assume the legal max (deferral +
    # catch-up) every year -- the default. The portion above the base
    # deferral limit counts as "catch-up" by definition, however much you
    # actually contribute -- not just when you're maxing out. An
    # amount/pct_of_max configured ABOVE the legal max isn't capped --
    # e.g. amount: 46000 here (legal max is ~$35,600 for a 61-year-old in
    # 2026) would send the ~$10,400 excess to Roth as an after-tax
    # "mega backdoor Roth" contribution, not drop it.
    roth_catchup_start_date: 2026-01-01  # already a high earner subject to
                                          # SECURE 2.0's mandatory Roth catch-up
    employer_match:
      pct_of_salary: 4                   # 4% of salary -- requires salary below
      salary: 250000                     # in today's dollars -- unlike a one-off
                                          # event elsewhere in this file, this DOES
                                          # inflate forward (it's a recurring,
                                          # wage-like figure)
  - owner: john
    amount: 15000                        # a flat dollar figure instead of the max --
                                          # today's dollars, inflates forward like a
                                          # wage -- or use pct_of_max: 50 for 50% of
                                          # that year's legal max instead
    roth_catchup_start_date: 2027-01-01  # becomes subject to it next year --
                                          # omit this key entirely for someone
                                          # never subject (catch-up stays pre-tax)
    employer_match:
      amount: 8000                       # or a flat dollar amount instead --
                                          # also inflates forward

# Optional -- estimated private health insurance cost per person, same
# shape as social_security above. Applies every year from that PERSON'S
# OWN retirement (not the household's combined later one) through the
# year before they turn 65 (Medicare eligibility) -- the real gap between
# retiring early and Medicare starting. In today's dollars, inflates
# forward like a wage. A person retiring at or after 65 has a zero-length
# window, so this has no effect for them either way.
health_insurance:
  jane:
    monthly_amount: 850
  john:
    monthly_amount: 900

# Optional -- each still-working person's estimated GROSS W-2 income
# (Box 1, "Wages, tips, other compensation"), same shape/window
# convention as health_insurance above (applies through the year BEFORE
# that person's own retirement). Deliberately NOT total AGI -- AGI can
# double-count investment income this tool already tracks separately via
# the taxable bucket -- and NOT gross pay before payroll deductions --
# Box 1 already excludes pre-tax 401(k)/HSA/etc. contributions, correctly,
# since those aren't taxable. Never bills the wage income's OWN tax to
# the portfolio -- this tool still has no wage-income model at all, and a
# household is assumed to cover that from its paycheck/withholding -- but
# it feeds a roth_conversions/ira_distributions entry's max_marginal_
# bracket/avoid_irmaa_tier constraint math (and a later year's Medicare/
# IRMAA lookback) with a realistic baseline for a conversion window that
# starts before retirement (nothing stops that -- see
# roth_optimizer/README.md's "Starting conversions before retirement"),
# AND makes that year's tax on the household's own RMD/conversion/
# distribution income an INCREMENT on top of this baseline, so it's taxed
# at the REAL marginal bracket once wages are accounted for. Without
# this, both the constraint and the tax itself would be computed as if a
# still-working person had zero other income -- letting a conversion both
# convert more, and look cheaper, than it really would.
pre_retirement_income:
  jane:
    estimated_gross_income: 300000
  # A per-year LIST instead of a flat number -- for a real, non-flat
  # trajectory a flat growth rate can't represent (e.g. a one-time bonus
  # year) -- one already-nominal dollar amount per year, no further
  # inflation applied (same convention as roth_conversions' own
  # annual_amount lists), anchored by a REQUIRED start_date:
  # john:
  #   start_date: 2026-01-01
  #   estimated_gross_income: [80000, 400000, 83000, 86000]  # 2026-2029:
  #                                                           # one bonus year,
  #                                                           # then back to normal

# Optional -- a single global advisory/AUM fee for the whole portfolio,
# applied against total portfolio value every year -- see "Growth
# projection" below. Deliberately NOT derived from any real account's
# own expense ratio (`accounts set-expense-ratio`), which stays
# informational-only, reported by `report` instead -- this is the one
# and only advisory expense a projection ever models, set here and
# nowhere else. Omit this section entirely for no advisory expense.
advisory_expense:
  pct_of_portfolio: 1.0            # a percentage: 1.0 means 1.00%/year

# Optional -- a recurring, FIXED-dollar debt payment (a mortgage, car
# loan, or similar) -- see "Growth projection" below. Deliberately NOT
# inflated forward (a fixed-rate loan payment doesn't grow with
# inflation, unlike target_spending above), and NOT tied to either
# person's own retirement -- start_date/end_date are a plain
# household-level window instead. A down payment or other one-time cost
# at origination is a taxable_cash_flows entry (above), not this.
liabilities:
  - name: "Vacation home mortgage"    # optional, display only
    type: mortgage                    # optional, display only
    start_date: 2030-06-01
    end_date: 2060-05-31              # payoff date
    monthly_payment: 3200             # a flat NOMINAL dollar figure
  - name: "Car loan"
    type: auto_loan
    start_date: 2028-01-01
    principal: 45000                  # alternative to monthly_payment/
    interest_rate: 6.5                # annual_payment: give the loan's own
    term_years: 5                     # terms and the standard amortized
                                       # payment is computed for you (end_date
                                       # then defaults to start_date + term_years)
```

**The filename is deliberately flexible** -- there's no fixed name or
scenario registry. `retirement_scenario.yaml` is just the default `project`
looks for; save as many variations as you want under any name
(`retirement_scenario_early_retirement.yaml`,
`retirement_scenario_conservative_returns.yaml`, a plain `.yml`, whatever)
and point at one explicitly:

```bash
python3 -m pt.cli project --scenario ~/.portfolio_tool/retirement_scenario_early_retirement.yaml
```

All of them join against the same `retirement_profile.yaml` -- only the
scenario changes.

## Growth projection

A year-by-year cash-flow table from this year through the primary person's
age-100 year, using a retirement scenario, the planning profile, and the
household's current account balances:

```bash
python3 -m pt.cli project
python3 -m pt.cli project --scenario some_other_scenario.yaml --profile some_other_profile.yaml
python3 -m pt.cli project --snapshot 5          # project from a specific past snapshot's value
python3 -m pt.cli project --out projection.txt  # also save the table as text
python3 -m pt.cli project --out projection.xlsx # ...or as a one-sheet Excel workbook -- same
                                                  # table, detected by the .xlsx extension
python3 -m pt.cli project --out projection.txt --out projection.xlsx  # --out is repeatable --
                                                  # save both formats from the same run
python3 -m pt.cli project --out projection.png  # ...or a chart image (needs matplotlib --
                                                  # see requirements-optional.txt)
python3 -m pt.cli project --verbose             # add a column per account -- see below
                                                  # (works with either --out format)
```

It's also included automatically as a "Projection" tab in `report`'s Excel
workbook, if both a profile and a scenario are found at the default paths
(or the ones you pass `report --profile`/`--scenario`) -- silently skipped
otherwise, so `report` works the same as always if you haven't set either
up. `project --out *.xlsx` is the same table as a standalone one-sheet
workbook instead, on its own without the rest of `report`'s tabs.
`--verbose`'s per-account columns work the same way on both -- `project
--verbose` and `report --verbose` (see above).

**`report --monte-carlo`**: adds a "Monte Carlo" tab immediately after
"Projection", running `monte-carlo` (see below) from the same profile and
scenario -- the percentile fan-chart table + chart. `--trials`/`--seed`
on `report` feed it, exactly like the standalone `monte-carlo` command
(default 1000 trials, fresh randomness each run). Both the Projection and
Monte Carlo tabs also carry a **MODEL PARAMETERS** block (blended growth
rate, and for Monte Carlo the blended volatility / trial count / seed,
plus every asset class's resolved expected-return and volatility
assumption), then the full SCENARIO CONFIGURATION YAML dump -- both go
*below* the chart(s), so each table stays next to its own chart at the top
of the sheet.

**Charts**: an `.xlsx` output (either `project`'s own, or `report`'s
embedded Projection tab) gets two native Excel line charts right below the
table -- balance by bucket (Taxable/Traditional/Roth/Inherited/Total) and
cumulative tax paid while retired + cumulative healthcare expense, both
vs. year -- using openpyxl's own chart support (no new dependency).
`project --out *.png` (needs `matplotlib`, NOT a core dependency -- see
requirements-optional.txt) saves the same two panels as a standalone
chart image instead, for when you want a picture rather than a workbook.

### Per-bucket growth (`--per-bucket-growth`)

By default, `project`/`monte-carlo`/`report` blend the household's ENTIRE
allocation into one growth rate and apply it uniformly to every tax
bucket (Taxable, each Traditional owner/type pool, each Roth owner, each
Inherited account) -- so an account invested 100% in equities grows at
the exact same rate as one invested 100% in cash, as long as they're both
part of the same household blend. `--per-bucket-growth` makes each bucket
grow at its OWN blended rate instead, computed from what THAT bucket
actually holds:

```bash
python3 -m pt.cli project --per-bucket-growth
python3 -m pt.cli monte-carlo --per-bucket-growth --trials 2000 --seed 1
python3 -m pt.cli report --private --per-bucket-growth
```

Text output and the Excel MODEL PARAMETERS block both show a household
summary rate PLUS the per-bucket breakdown underneath it, one line per
bucket (Taxable / `Traditional (Alex: 401(k))` / `Roth (Sam)` /
`Inherited (...2424)` -- see `pt/projection.py`'s `bucket_label()`).
Traditional is broken out per (owner, account type), not one combined
number, since that's the actual granularity `build_buckets()` grows at --
someone's 401(k) and IRA can (and often do) hold a different mix.

For Monte Carlo, this isn't just "look up a different mean" per bucket --
each year still draws ONE shared, correlated per-asset-class economy (see
`build_bucket_correlated_return_sampler()`), so a bad year for equities
hits every bucket holding equities at the same time; each bucket just
weights that shared draw by its OWN mix instead of the household's,
which is what lets a Roth invested more aggressively than a 401(k)
realize a genuinely different (but still correctly correlated) outcome
each trial.

A bucket with no accounts of its own (e.g. a Traditional pool nobody has
opened yet) simply isn't part of the breakdown -- and a bucket that
starts at $0 with no defined mix (a hypothetical future inheritance, see
"Hypothetical accounts" below) falls back to the household rate/draw
until it has one. `roth_optimizer`/`scenario_engine` are unaffected --
they only ever call `project()` with a single blended_rate, never this
flag, so their search results don't change.

Every `project` run also prints the raw scenario configuration itself
(the loaded scenario dict, dumped back out as YAML -- the same format
the scenario file itself uses) at the very top of the output -- and, for
`project --out *.xlsx`, again at the bottom of the same sheet below the
table -- so a saved run is self-documenting about which assumptions
produced it, without having to go dig up the `--scenario` file it was
run against later.

#### `--verbose`: per-account columns

`project`'s table normally shows just the four bucket totals (Taxable/
Traditional/Roth/Inherited -- Traditional is summed across both people
*and* both account types, Roth across both people, for this top-level
column, even though each is tracked more finely internally). `-v`/
`--verbose` adds one more column on the right for every real account
currently over $500, labeled `<owner initial>:<account type>-<last 4 of
account number>` (e.g. `O:401k-7820`).

These columns aren't all modeled the same way:
- **Inherited accounts are exact** -- each one is already independently
  simulated (see "What it models" below), so its column is the real number.
- **A Traditional account is exact too, if it's the only account of its
  type (IRA or 401(k)) that person holds** -- e.g. someone with just one
  IRA and one 401(k) gets two exact columns, since each is already its own
  pool internally (see "What it models" below). If they hold *multiple*
  IRAs (or multiple 401(k)s), those specific columns fall back to a derived
  share of their shared pool, same as Taxable/Roth below.
- **Taxable, Roth, and multi-account Traditional columns are a derived
  share, not an independent simulation.** Those buckets are each tracked as
  one pooled number (see below) -- there's no per-account RMD or withdrawal
  logic for, say, one specific IRA out of several. Each account's column is
  its fixed starting percentage of its pool, scaled by that pool's
  simulated balance every year -- i.e. it assumes every account in a pool
  grows and gets drawn down in exact proportion to the pool average. Real
  life doesn't have to work that way (you might spend down one specific
  IRA before another), so treat these as an illustrative split of the
  pool total, not a prediction for that specific account.

Accounts at or under $500 don't get a column, but their value is still
included in their bucket's total -- nothing is dropped from the numbers,
just from having its own column. **Roth IRAs and hypothetical inherited
accounts are the two exceptions** -- they always get a column no matter
how small their current balance, since a Roth conversion/IRA distribution
(see below) can take a Roth from near-$0 to substantial over the course of
the projection, and a hypothetical inherited account (see "Retirement
scenario" above) starts at exactly $0 by definition until its inheritance
year arrives -- hiding either at the start would make that growth
invisible.

### What it models

Unlike a single lump-sum balance, the projection tracks the household's
money in separate tax-treatment buckets, because RMDs, taxes, and the
withdrawal order all depend on *which kind* of account money sits in:

- **Taxable** -- Brokerage accounts (any owner)
- **Traditional** -- IRA/401(k), pooled per person AND per account type --
  e.g. all of one person's IRAs combine into one pool, separately from all
  their 401(k)s into another. RMDs are computed per pool and summed (same
  total as computing on the person's combined balance, since the RMD
  divisor only depends on age, not which account it's in). A Roth
  conversion or IRA distribution draws from either pool -- IRA first, then
  401(k) once the IRA is exhausted -- see below; a rollover moves money
  the other way, 401(k) to IRA
- **Roth** -- Roth IRA, one bucket per person (no lifetime RMD applies
  to either person's own Roth, but tracked per person rather than combined
  because a Roth conversion has to land in the *same* person's own Roth)
- **Inherited** -- each inherited IRA/Roth IRA listed in the scenario's
  `inherited_accounts`, tracked separately with its own SECURE Act
  10-year-rule schedule (see `pt/rmd.py`) -- including a HYPOTHETICAL
  future inheritance (`hypothetical_value` instead of `account_number`),
  which simply doesn't exist until its configured `decedent_death_date`

Each year:

1. **Anyone who died as of THIS year** (see `mortality` above -- a person
   is treated as alive through their own death year, matching real tax
   rules) has their Traditional/Roth pools merged into their surviving
   spouse's own pools -- a real-world spousal rollover, not subject to
   the SECURE Act 10-year rule the way a non-spouse inheritance is, so it
   just becomes the survivor's own money. **A hypothetical inherited
   account "appears"** if this is its configured `decedent_death_date`
   year, at its configured `hypothetical_value` -- and any
   `taxable_cash_flows` entry for this year lands directly in the Taxable
   bucket (a deposit adds, a withdrawal subtracts, floored at $0). **A
   401(k) contribution**, for anyone configured in
   `retirement_contributions` who hasn't reached their own retirement
   year yet (their own `retirement_age`/`spouse_retirement_age`, not the
   household's combined later one), also lands here. The total
   *requested* is the legal max (elective deferral limit + whatever
   catch-up applies at that year's age -- 2025 IRS limits, inflated
   forward, see `pt/contributions.py`, including SECURE 2.0's enhanced
   catch-up for ages 60-63) by default -- or, if `amount` (a flat dollar
   figure, inflated forward like a wage) or `pct_of_max` (a percentage of
   that year's legal max) is set instead, that amount. This is **NOT**
   capped at the legal max: of the requested total, the portion up to the
   deferral limit is "regular" and always goes to the Traditional 401(k)
   pool; the next slice, up to the legal max, is "catch-up" **by
   definition** (the real IRS rule -- not about maxing out, just whatever
   exceeds the base limit), and also goes to Traditional UNLESS
   `roth_catchup_start_date` has arrived, in which case SECURE 2.0's
   mandatory Roth catch-up rule for high earners sends it to the Roth
   pool instead; anything requested ABOVE the legal max (e.g. `amount:
   46000` when the legal max is ~$35,600) is modeled as an after-tax
   contribution immediately converted to Roth -- the "mega backdoor Roth"
   strategy, tracked separately in its own **AT->Roth** column since it's
   a different IRS provision (assumes minimal growth before conversion,
   so it's effectively tax-free -- an after-tax contribution that instead
   stays in the 401(k) long-term isn't modeled). If `employer_match` is
   also configured (either a flat
   `amount` or `pct_of_salary` times `salary`), it ALWAYS lands in the
   Traditional pool -- the standard real-world treatment, even in a year
   the owner's own catch-up goes to Roth -- shown separately in its own
   **Er Match** column rather than folded into 401k Trad. so it stays
   visible on its own (unlike a one-off event's dollar amount elsewhere
   in this scenario file, `employer_match.amount`/`salary` ARE treated as
   recurring, wage-like figures and inflate forward with general
   inflation). None of this is a taxable event -- new money from outside
   the model (that year's paycheck), like a `taxable_cash_flows` deposit;
   this tool doesn't model wage income/payroll tax at all, so there's no
   income to reduce for the pre-tax portion. Shown in the table's **401k
   Trad.** and **401k Roth** columns. All of this happens BEFORE this
   year's growth, so it participates in it normally.
2. **Every bucket grows** at the blended rate (see "One blended growth
   rate," below).
3. **Rollovers**, if any are configured in the scenario's `rollovers`
   (optional -- see "Retirement scenario" above), process right after
   growth, before RMDs -- a ONE-TIME move (not a recurring window like a
   conversion/distribution) of a percentage or flat amount from an owner's
   401(k) pool to their own IRA pool. NOT a taxable event, and doesn't
   change that year's total RMD either (the RMD divisor only depends on
   age, not which pool the money is in) -- only which pool a `--verbose`
   column draws the balance from. Shown in the table's **Rollover** column.
4. **Forced distributions come out first, regardless of spending:**
   - Each person's own Traditional RMD, once they reach RMD age (73 or 75,
     per SECURE 2.0's birth-year rule) -- IRS Uniform Lifetime Table.
   - Each inherited account's required distribution for that year: an
     inherited Traditional IRA requires annual "stretch" RMDs (IRS Single
     Life Expectancy Table, using the beneficiary's age, reduced by 1 each
     year) if the original owner died on or after their own RMD age, plus
     full distribution by the end of the year containing the 10th
     anniversary of death either way; an inherited Roth IRA never requires
     an annual RMD (a Roth's original owner has no RMD age), only the
     year-10 full distribution. If the account has a `planned_withdrawal`
     configured, its `annual_amount` acts as a *floor* during
     `[start_date, end_date]` (by year) -- the account distributes
     whichever is larger, the RMD or the planned amount -- so you can
     model deliberately front-loading withdrawals (e.g. to smooth taxable
     income instead of one large final-year distribution); outside that
     window, or with none configured, the RMD alone applies.
   - **Roth conversions**, if any are configured in the scenario's
     `roth_conversions` (optional -- see "Retirement scenario" above),
     process right after RMDs -- so a conversion can't happen until that
     year's RMD is satisfied, matching the IRS rule, without needing a
     separate check. For each owner with an active conversion window (by
     year, `[start_date, end_date]`), the resulting amount moves to their
     own Roth bucket -- drawn from their own IRA pool(s) first, then their
     own 401(k) pool if the IRA balance alone isn't enough (if even that's
     not enough, the shortfall just isn't converted). This is a taxable
     event (added to ordinary income, same as a Traditional withdrawal)
     but generates no spendable cash -- the money doesn't leave the
     household, it just changes tax treatment. The resulting tax bill is
     paid the normal way (taxable, then Traditional, then Roth), which
     naturally draws from the taxable bucket first -- the standard advice
     to pay conversion tax from outside the IRA falls out of the existing
     withdrawal order rather than needing special-casing. Shown in the
     table's **Roth Conversion** column.

     **The amount itself** is either a flat `annual_amount`, or -- if
     `constraints` are configured instead (or on top of it) -- re-evaluated
     every year as the SMALLEST of whichever of these are set:
     `max_conversion_per_year` (a flat dollar cap); `max_marginal_bracket`
     (caps the conversion so its own marginal federal rate doesn't exceed a
     named bracket -- "fill the bracket" style, using this year's ordinary
     income/RMDs/other owner's conversion so far as the starting point);
     `avoid_irmaa_tier` (caps the conversion so this year's MAGI doesn't
     spill into the next Medicare IRMAA tier up two years from now -- see
     "Medicare & IRMAA" below; a bare `avoid_irmaa: true` also works as
     shorthand for tier 0, i.e. never pay a surcharge at all); and
     `preserve_cash_reserve` (caps the conversion so its OWN estimated tax
     cost -- this year's marginal rate applied to the conversion amount --
     doesn't draw the Taxable bucket below a reserve target, either a
     number of years given directly or `true` to use the scenario's
     top-level `cash_reserve.years` policy). This is a **greedy, one-year-
     at-a-time heuristic** -- each year converts as much as that year's
     constraints allow, without looking ahead to whether converting more or
     less this year would produce a better outcome over the rest of the
     projection (lower lifetime tax, or a larger total estate); a true
     multi-year optimizer for that is a bigger, separate undertaking (see
     "Ideas for v1.68+" below).
   - **IRA distributions**, if any are configured in the scenario's
     `ira_distributions` (optional -- see "Retirement scenario" above),
     process right after Roth conversions, sharing the same running
     ordinary-income total (so a distribution correctly sees any
     conversion income already added this year). Identical mechanics and
     constraints to a Roth conversion above (same fields, just
     `constraints.max_distribution_per_year` instead of
     `constraints.max_conversion_per_year`) -- the only difference is
     where the money ends up: a conversion moves it to Roth (no spendable
     cash, just changes tax treatment); a distribution moves it to
     **Taxable** instead -- a plain Traditional withdrawal (ordinary
     income, same as an RMD), just taken voluntarily ahead of when it's
     needed for spending, e.g. to use up room in a low bracket while it's
     available. Shown in the table's **IRA Distribution** column.
5. **Medicare Part B premium + Part D IRMAA surcharge**, for anyone alive
   and 65 or older, computed unconditionally (regardless of whether any
   `roth_conversions` are configured) -- shown in the **Medicare Part B**,
   **Medicare Part D IRMAA**, and **IRMAA Tier** (0-5) columns. Uses the
   real 2-year income lookback (this year's premium is set by the
   household's MAGI from 2 years ago, which the projection already
   computed and recorded), inflated using `inflation.medical` if set
   (falling back to `inflation.general`). Treated as a required cash
   outflow alongside spending -- covered by forced cash/Social Security
   first, then the normal withdrawal order -- rather than as a reduction to
   Social Security's gross benefit for tax purposes, matching how Part B
   premiums are typically withheld directly from a Social Security check
   in real life (the household still receives less net cash, it just
   doesn't shrink what's taxable). The **Cumulative Healthcare Expense**
   column is a running total of Medicare Part B + Part D IRMAA + private
   health insurance (see next) since the start of the projection (unlike
   Cumulative Tax (Retirement), NOT gated on being retired, since Medicare
   eligibility can arrive first) -- the only healthcare costs this tool
   tracks; other healthcare costs (out-of-pocket, long-term care) are
   assumed folded into `income.target_spending` already.
6. **Private health insurance** (optional, `scenario.health_insurance` --
   see "Retirement scenario" above), shown in the **Health Ins.** column:
   each configured person's `monthly_amount`, inflated forward like a
   wage, applies every year from THAT PERSON'S OWN retirement (not the
   household's combined later `household_retirement_year`) through the
   year before they turn 65 -- the real gap between retiring early and
   Medicare starting. Treated as a required cash outflow the same way as
   Medicare above (forced cash/Social Security first, then the normal
   withdrawal order), and included in Cumulative Healthcare Expense. A
   person retiring at or after 65 has a zero-length window, so this has
   no effect for them regardless of what's configured.
7. **Advisory expense** (optional, `scenario.advisory_expense.
   pct_of_portfolio` -- see "Retirement scenario" below), shown in the
   **Advisory Exp.** column: a SINGLE global advisory/AUM fee the
   household supplies directly in the scenario file, applied against
   that year's total portfolio value every year. Deliberately NOT
   derived from any real account's own expense ratio (`accounts
   set-expense-ratio`, "Investment style, sector, and expense ratio"
   above) -- that stays informational-only, reported by `report`'s
   Investment Expense Summary; this is the one and only advisory
   expense number a projection ever uses, set in exactly one place. NOT
   gated on retirement status (an AUM-based advisory fee is owed
   regardless), and treated as a required cash outflow the same way as
   Medicare/health insurance (forced cash/Social Security first, then
   the normal withdrawal order). Defaults to $0 if
   `scenario.advisory_expense` isn't set at all.

   **Example**: `advisory_expense: {pct_of_portfolio: 1.0}` in the
   scenario file, on a household with a $2,000,000 total portfolio,
   means Advisory Expense is 1.00% of that year's total portfolio value
   every year -- $20,000 in a year the portfolio is still around
   $2,000,000, more once it's grown, paid taxable-then-Traditional-
   then-Roth like any other cash need.
8. **Liabilities** (optional, `scenario.liabilities` -- see "Retirement
   scenario" above), shown in the **Liability Pmt.** column: a mortgage,
   car loan, or similar recurring debt payment, over its own
   `start_date`/`end_date` window (a household-level window, NOT tied to
   either person's own retirement -- unlike Social Security/health
   insurance above, so this can apply pre-retirement too). Unlike
   `spending` below, the payment is a flat NOMINAL dollar figure that
   does NOT inflate forward -- a fixed-rate loan payment doesn't grow
   with inflation. Give either a flat `monthly_payment`/
   `annual_payment`, or `principal`/`interest_rate`/`term_years` to have
   the standard amortized payment computed for you. NOT gated on
   retirement status, and treated as a required cash outflow the same
   way as Medicare/health insurance/advisory expense (forced cash/Social
   Security first, then the normal withdrawal order). No amortization/
   interest-vs-principal split is tracked, and no separate
   liability-balance ledger reduces net worth -- this is purely the
   payment's cash-flow effect, not a net-worth statement including the
   home/car itself.
9. **Social Security** is added for each person once they reach their
   `claim_age`, inflated from today's `monthly_benefit` by the scenario's
   general inflation rate (a proxy for COLA, not the actual CPI-W formula).
   If a `mortality` death date has left exactly one spouse surviving, the
   survivor instead gets the LARGER of the two people's own eligible
   benefits -- a simplified survivor benefit (see "Documented
   simplifications" for what this doesn't model, like an early reduced
   survivor claim before the survivor's own `claim_age`).
10. **Spending** (`income.target_spending`, inflated) applies once the
    household is fully retired -- the *later* of `retirement_age` and
    `spouse_retirement_age`, same as before.
11. If forced distributions + Social Security exceed spending + Medicare +
    private health insurance + advisory expense + liabilities, the surplus is
    reinvested into the taxable bucket. Otherwise, the gap -- and
    separately, the year's federal tax bill -- is covered by a
    discretionary withdrawal in this order: **taxable, then Traditional
    (own), then Roth (own), last.** Inherited accounts distribute only
    their forced amount each year; the withdrawal order doesn't reach
    into them further.
12. **Federal tax** (current 2024 brackets, inflated forward each year;
    **MFJ**, unless exactly one spouse has died -- see `mortality` above --
    in which case the survivor's filing status switches to **Single** the
    year AFTER the death, with the correspondingly smaller Single brackets
    and standard deduction) is computed on: Traditional/inherited-Traditional
    distributions (ordinary income) plus the taxable portion of Social
    Security (the IRS "combined income" formula -- up to 85% taxable,
    different thresholds for MFJ vs. Single), plus taxable-brokerage
    withdrawals (treated as long-term capital gains). Roth withdrawals are
    tax-free and never enter the calculation. The table's **Taxable
    Income** column shows the Form-1040-style figure this is actually
    computed from -- ordinary income (including taxable Social Security)
    minus the standard deduction, floored at $0, plus the capital-gains
    amount -- so it's the same number the Tax column is based on, not a
    separate estimate. The **Cumulative Tax (Retirement)** column right
    after Tax is a running total of tax paid, but *only* counting years
    where the household is retired -- it starts accumulating from the
    first retired year, so it excludes any tax from forced distributions
    (e.g. an inherited account's required RMD) that land before
    retirement, which this household's own accounts do.

If EVERY person in the household ends up with a configured `mortality`
death date (both, for a two-person household), the projection stops once
the LATER of them has been processed -- the table's last row and the
summary line at the bottom report the estate's value (that row's Total
Balance) instead of running through age 100.

### Documented simplifications (see `pt/projection.py`, `pt/rmd.py`, `pt/tax.py` for the full detail)

- **One blended growth rate for the whole projection, applied to every
  bucket.** Computed once from the household's *current* asset-class
  allocation, weighting each held class's own `returns.asset_classes`
  entry by its dollar weight (or, with the older `returns.growth`/
  `.fixed_income` shape, `Growth`/`Alternatives` at `growth`, everything
  else at `fixed_income`) -- and held constant every year. No rebalancing,
  glide path, or per-bucket allocation drift -- e.g. "the Roth is 100%
  equities, the Traditional IRA is bonds" isn't modeled; the same
  household-wide composition applies to every bucket. A class you hold
  but don't itemize in `returns.asset_classes` (directly or via its
  macro) raises a clear error rather than guessing a rate for it.
- **Federal tax only** -- no state tax, standard deduction only (no
  itemizing, no credits). Filing status is MFJ (unless `mortality` leaves
  a surviving spouse filing Single -- see above); this tool has no
  Head-of-Household or single-never-married status, so a single-person
  household with no `mortality` configured stays MFJ throughout, an
  existing gap this feature doesn't address. No state tax was this
  household's own choice; a flat-effective-rate mode was the simpler
  alternative considered.
- **Taxable-brokerage withdrawals are 100% long-term capital gain** -- cost
  basis isn't tracked or excluded, which somewhat overstates tax on that
  bucket.
- **A year's tax bill is paid via one additional withdrawal**, in the same
  conventional order, computed from that year's spending-driven withdrawals
  -- the small additional tax *on that withdrawal itself* isn't re-computed
  (avoids solving a circular equation each year; the omitted amount is
  typically small).
- **Inherited-account rules assume a non-spouse, non-eligible-designated
  beneficiary** under the SECURE Act 10-year rule -- not the alternative
  life-expectancy stretch available to a spouse or other EDB (minor child,
  disabled/chronically ill beneficiary, or someone not more than 10 years
  younger than the decedent). If that applies to your situation, the
  numbers here will be wrong for that account.
- **HSA accounts aren't given special tax treatment** -- if present, an
  HSA is folded into the taxable bucket (see `pt/projection.py`'s
  `TAXABLE_ACCOUNT_TYPES`), which understates its actual tax advantage.
- **An inherited account not listed in the scenario's `inherited_accounts`**
  is also folded into taxable, rather than left unmodeled -- add it there
  for correct RMD/10-year-rule treatment.
- **Roth conversions don't model the 5-year conversion-seasoning rule**
  (each converted amount needs 5 years before it can be withdrawn
  penalty-free if the owner is under 59 1/2) -- not a concern for a
  household already past that age by the time conversions typically start,
  but worth knowing if you configure an early conversion for a younger
  owner.
- **Roth conversions draw from a person's IRA pool first, then their
  401(k) pool** if the IRA alone doesn't cover `annual_amount` that year --
  per this household's own direction. If even both pools combined aren't
  enough, the excess simply isn't converted.
- **Constraints (shared by Roth conversions and IRA distributions) are a
  greedy per-year heuristic, not a true multi-year optimizer** -- see the
  numbered step above. There's also no lookahead within a single year:
  each constraint estimates that year's ordinary income/MAGI using only
  what's known when the conversion/distribution is decided (RMDs plus any
  other owner's conversion/distribution already processed, an estimated
  taxable-Social-Security amount, `scenario.pre_retirement_income`'s
  estimate for anyone still working that year if configured, and, for
  `avoid_irmaa_tier` only, an ANTICIPATED capital-gains estimate for that
  year's likely taxable-brokerage withdrawal) -- the actual withdrawal happens
  afterward and can come in smaller (e.g. if RMDs/Social Security already
  covered most of spending), so a constrained amount can undershoot its
  target slightly, or -- since `max_marginal_bracket` doesn't include
  that anticipated capital gain at all (it doesn't affect which ordinary
  bracket applies, only IRMAA/MAGI does) -- very rarely overshoot.
- **An IRA distribution isn't a Roth conversion** -- the money it moves to
  Taxable is spendable cash, unlike a conversion, but it's still ordinary
  income like an RMD. If the household doesn't actually spend it, it just
  sits in and grows the Taxable bucket -- taxed as capital gains on any
  future withdrawal of the growth, same as any other taxable-brokerage
  money.
- **A rollover doesn't change that year's total Traditional RMD** -- the
  Uniform Lifetime divisor only depends on age, not which pool the money
  is in -- only which pool a `--verbose` column shows the balance in.
- **Taxable cash flows (deposits/withdrawals) are deliberately not run
  through the tax engine at all** -- no capital-gains tax on a withdrawal,
  no cost basis added on a deposit -- per the household's own direction.
  If you want a withdrawal taxed like a normal spending withdrawal
  instead, adjust `income.target_spending` for that year.
- **A hypothetical inherited account uses the same non-spouse/non-EDB
  10-year-rule assumption as a real one** (see above), and needs an
  assumed decedent birthdate/death date the household supplies -- there's
  no way to validate those against reality since the person hasn't (yet)
  died in this scenario.
- **The Social Security survivor benefit is a simplification** -- real
  Social Security lets a survivor claim as early as age 60 (with a
  reduction) even before their own normal `claim_age`, and the exact
  survivor amount depends on when the deceased claimed and the survivor's
  own claiming age. This tool just takes the larger of each person's own
  already-configured `claim_age`/`monthly_benefit` -- it doesn't model an
  early, reduced survivor claim.
- **No federal estate tax, heir tax on inherited Traditional/Roth
  balances, cost-basis step-up on Taxable holdings, or probate** is
  modeled for the estate reported once everyone in the household is
  deceased -- it's a gross `total_balance` snapshot, not a net-to-heirs
  figure.
- **If the deceased was themselves the beneficiary of a real inherited
  account** (`inherited_accounts` with an `account_number`), that
  account's SECURE Act 10-year-rule schedule continues completely
  unchanged (still computed from the ORIGINAL beneficiary's age) --
  re-inheritance by whoever receives it next isn't modeled.
- **Medicare/IRMAA tier assumes the standard premium (tier 0) for the
  projection's first two years** -- the tool has no visibility into a
  household's actual MAGI from before the projection starts, so those two
  years' premiums may be understated if real recent income was already
  higher.
- **MAGI (for both `avoid_irmaa_tier` and the household's actual Medicare
  premium) omits tax-exempt interest** -- not tracked anywhere else in
  this tool, so this understates MAGI for a household with meaningful
  municipal-bond income.
- **If spending or taxes exceed what's available** in a given year, the
  withdrawal is capped at what's available, the unmet amount is shown
  separately as a shortfall, and affected buckets stay at $0 (with a
  growing shortfall) going forward.
- **No survivor spending reduction.** `life_expectancy`/
  `spouse_life_expectancy` are informational only -- the table always runs
  through the primary's age-100 year regardless, and spending doesn't drop
  if one person's life expectancy is reached first.
- **No wage income or payroll tax is modeled at all**, before or after a
  401(k) contribution -- this tool has only ever modeled the distribution
  phase (withdrawals, RMDs, Social Security), not the accumulation phase.
  A contribution is added as pure principal with no tax consequence
  either way -- correct for the Roth catch-up portion (already after-tax
  by definition), a simplification for the pre-tax portion (which in
  reality reduces that year's taxable wages -- not modeled here since
  wages themselves aren't tracked).
- **Employer match (`employer_match`) is a simple flat-amount-or-percent-
  of-salary figure, not a real plan's tiered match formula** (e.g. "100%
  up to 3% of salary, then 50% up to 5%") -- the household supplies
  whatever single number represents their actual expected match. No IRS
  combined employee+employer "annual additions limit" (a separate, much
  higher cap than the employee elective-deferral limit) is checked -- an
  aggressive combination of contribution + match isn't capped here.
- **Private health insurance (`health_insurance`) is a single flat
  `monthly_amount` the household supplies**, not modeled from real ACA
  marketplace plan pricing, premium tax credits/subsidies (which depend
  on that year's actual MAGI and can meaningfully reduce the real cost),
  COBRA, or plan-tier variation -- the household is expected to already
  have a reasonable estimate in mind.
- **Advisory expense (`advisory_expense.pct_of_portfolio`) is a single
  flat rate the household supplies directly**, applied uniformly to
  total portfolio value every year -- not derived from, or reconciled
  against, any real account's own expense ratio in the database, even
  if one is set via `accounts set-expense-ratio` (that stays
  informational-only, reported by `report`). It also doesn't track any
  individual real account's own balance over time (this tool's buckets
  are pooled by owner/account-type, not by individual account).
- **A `retirement_contributions` `amount`/`pct_of_max` configured ABOVE
  the legal elective-deferral+catch-up max is assumed to be an after-tax
  401(k) contribution immediately converted to Roth** (the "mega
  backdoor Roth" strategy), landing tax-free in the Roth pool. This
  assumes minimal growth before conversion -- an after-tax contribution
  that instead stays in the 401(k) long-term isn't modeled (its future
  growth would be taxable as ordinary income on withdrawal, unlike Roth,
  which would require real cost-basis tracking this tool doesn't do
  anywhere).
- **The 2025 IRS 401(k) contribution limits (including SECURE 2.0's
  enhanced 60-63 catch-up) are inflated forward using general inflation**
  for later years -- the real annual IRS adjustment follows a wage-index
  formula rounded to $500 increments, not pure CPI (the same
  simplification this tool already makes for tax brackets and
  Medicare/IRMAA thresholds).
- **A spouse with no `spouse_retirement_age` configured is assumed, for
  401(k)-contribution purposes only, to retire at the same AGE as the
  primary** (not the same calendar year) -- this fallback is used only to
  know when that spouse's own contributions stop; everything else in the
  projection uses `household_retirement_year`, the LATER of the two.
- **Withdrawal order is the household's own single choice**, not a
  comparison across multiple strategies -- see "Ideas for v1.68+" below for
  what a multi-strategy comparison (e.g. tax-bracket-filling, pro-rata)
  would add.

This is tax and retirement-planning *modeling*, not advice -- the IRS
tables and tax brackets reflect current published guidance as of when this
was built, and tax law changes.

## Monte Carlo projection

`project` holds ONE blended growth rate constant for the entire
projection -- the same average return, in the same order, every single
year. In reality, returns vary year to year, and the SAME long-run average
return can produce very different outcomes depending on the order it
arrives in (a bad year early in retirement, while you're actively
withdrawing, does far more damage than the same bad year late, even though
both scenarios average out identically) -- this is "sequence-of-returns
risk," and a single deterministic run structurally can't show it.

```bash
python3 -m pt.cli monte-carlo                          # 1000 trials, default
python3 -m pt.cli monte-carlo --trials 5000 --seed 42   # more trials, reproducible
python3 -m pt.cli monte-carlo --out monte_carlo.xlsx    # save as Excel too
python3 -m pt.cli monte-carlo --out monte_carlo.png     # ...or as a chart image (needs matplotlib)
python3 -m pt.cli report --monte-carlo --trials 5000 --seed 42  # ...or as a tab in the report workbook
```

`report --monte-carlo` runs this same simulation and drops the result in
as a "Monte Carlo" tab right after the report's "Projection" tab (same
`--trials`/`--seed`); see "Growth projection" above.

Each of `--trials` runs is a full `project()` run, but with every single
year's growth rate drawn independently -- with `returns.asset_classes`
configured (see "Retirement scenario" above), every asset class you hold
draws its OWN correlated random return that year (equities move together
more than equities vs. bonds; cash barely moves at all) and sums by
weight into that year's realized portfolio return, instead of one blended
Normal draw for the whole portfolio -- everything else (RMDs, taxes, Roth
conversions, Social Security, Medicare, mortality, all of it) works
exactly the same as a normal `project` run for every trial. One
exception: a class configured with `tracks` (e.g. `Fixed Indexed
Annuities`, by default) doesn't draw independently at all -- it credits a
floored-and-capped copy of its tracked class's own draw that year instead
-- see "Retirement scenario" above. Output is a percentile table instead
of one deterministic row per year:

```
Year           10th %ile            Median         90th %ile
------------------------------------------------------------
2026           2,966,310         3,464,287         3,940,642
...
2065           1,397,689         7,778,895        27,525,747
==========================================================================================
Success rate: 96.0% (96 of 100 trials reached age 100 -- or the household's configured estate-stop year -- without ever running short).
4 trial(s) ran short -- as early as 2049, as late as 2063.
Median final-year total balance across all trials: $7,778,895
Median cumulative tax paid while retired across all trials: $2,260,855
```

**Success rate** is the fraction of trials that never hit a shortfall (see
`shortfall` in "Growth projection" above); a household ending in "estate"
(mortality configured, both spouses deceased before age 100) counts as a
success too -- only running out of money counts against it.

Notice the **median** final balance is typically noticeably LOWER than
`project`'s single deterministic number for the same household and same
average return -- this is expected, not a bug: compounding a volatile
sequence of returns produces a lower *typical* (median) outcome than
compounding the same *average* return every year, even though the mean
outcome across many trials is close to the deterministic figure. This
"volatility drag" is exactly the effect a deterministic projection can't
show you.

**Median cumulative tax paid while retired** (see `cumulative_tax_in_retirement`
in "Growth projection" above) is reported alongside median final balance
so two scenarios can be compared on tax cost too, not just success
rate/ending balance -- e.g. turning Roth conversions on typically trades
some success rate and ending balance for a meaningfully lower lifetime
tax bill (paying tax now, at a known rate, instead of a potentially larger
one later via RMDs on a Traditional balance that's had years to compound).
A scenario can win on one of these measures and lose on another; which
matters more is a household's own call, not something this tool decides
for you.

**Charts**: `--out *.xlsx` embeds a native Excel line chart (no new
dependency) -- total balance's 10th/median/90th percentile as three
separate lines. `--out *.png` (needs `matplotlib` -- see
requirements-optional.txt) saves the same data instead as a proper
shaded "fan chart" -- a 10th-90th percentile band with the median as a
solid line -- which matplotlib handles cleanly but Excel's native charts
can't easily do. (Cumulative tax/healthcare charts are `project`'s own,
not `monte-carlo`'s -- see "Growth projection" above.)

**Return volatility per class** comes from `returns.asset_classes.*
.volatility` (optional per class -- falls back to a rough per-macro
default if you set `expected_return` but not `volatility` for a class --
see `pt/projection.py`'s `DEFAULT_VOLATILITY_BY_MACRO`). **Correlation
between classes** (how much they tend to move together) comes from a
documented default table -- distinct subclasses under the same macro
correlate at 0.85, different macros per a rough historical 5x5 table
(equities vs. bonds low, equities vs. alternatives moderate, everything
vs. cash near zero, Insurance mildly positive to equities and low to
everything else) -- overridable per pair via `returns.correlations`:

```yaml
returns:
  asset_classes:
    US Equity:              {expected_return: 7.5, volatility: 18}
    International Equity:   {expected_return: 7.0, volatility: 20}
    US Bonds:                {expected_return: 4.0, volatility: 7}
    Liquidity:               {expected_return: 2.5, volatility: 1}
  #correlations:
  #  US Equity,US Bonds: 0.0   # optional override, "ClassA,ClassB" (either order)
```

**A class configured with `tracks`** (see "Retirement scenario" above --
`Fixed Indexed Annuities` gets this by default) skips the independent
Normal draw entirely: its `volatility` figure above is display-only (the
report's MODEL PARAMETERS block still shows it), and its actual Monte
Carlo return each year is a copy of its tracked class's own draw that
year, clamped to `[floor, cap]`.

With the older `returns.growth`/`.fixed_income` 2-bucket shape instead
(no `asset_classes` section), volatility works the same way via
`growth_volatility`/`fixed_income_volatility` (both optional, default
16%/5% -- see `DEFAULT_GROWTH_VOLATILITY`/`DEFAULT_FIXED_INCOME_
VOLATILITY`), and the whole portfolio is treated as exactly 2 correlated
classes (Growth+Alternatives vs. Income+Liquidity) under the same default
correlation table.

`--seed N` makes a run byte-for-byte reproducible (trial *i* always uses
the same random draws); omit it for a fresh, non-reproducible result each
time. This is a simplification: each asset class's own return is an
INDEPENDENT-across-years draw (no real-world autocorrelation/mean-
reversion between consecutive years, and a Normal distribution
understates how often real markets produce extreme years), the default
correlation table is a rough, historically-informed approximation, not
tailored to any specific fund lineup, and every bucket (Taxable/
Traditional/Roth/Inherited) still gets the SAME realized portfolio rate
in a given year/trial -- no per-bucket allocation drift, e.g. "the Roth
is 100% equities" isn't modeled -- see `pt/projection.py`'s "Documented
simplifications" for the full detail.

## Hypothetical accounts (skip the import entirely)

`project` and `monte-carlo` both normally read a household's accounts
from a database snapshot (`import` a CSV first -- see "Getting your
Fidelity export"). `scenario.hypothetical_accounts` is a way around that:
describe a made-up household directly in the scenario file, and both
commands run against it instead -- no CSV, no `import`, no ticker
classification, no database at all. Useful for trying either command out
before you have real data, or for a pure "what if I had a portfolio like
this" exploration that isn't about any specific real account.

```yaml
# Optional -- only meaningful alongside hypothetical_accounts below. The
# SAME shape as a real retirement_profile.yaml's people section -- lets
# this one file be fully self-contained, with no separate profile file
# needed at all. Omit this and pass --profile instead if you'd rather
# keep using a real profile file (an explicit --profile always wins over
# this section either way).
people:
  primary:
    name: jordan
    birthdate: 1966-04-12
  spouse:
    name: casey
    birthdate: 1968-09-30

retirement:
  retirement_age: 65
  spouse_retirement_age: 63
  life_expectancy: 95
  spouse_life_expectancy: 95

income:
  target_spending: 80000

inflation:
  general: 2.5

returns:
  asset_classes:
    Growth:  {expected_return: 7.5, volatility: 18}   # pct_growth accounts
    Income:  {expected_return: 4.0, volatility: 7}    # below need these two
                                                        # macro-level entries
                                                        # resolvable -- see
                                                        # the asset_classes
                                                        # per-account note
                                                        # below for a finer
                                                        # alternative

social_security:
  jordan:
    claim_age: 67
    monthly_benefit: 2500
  casey:
    claim_age: 67
    monthly_benefit: 2000

hypothetical_accounts:
  - account_number: "demo-brokerage"   # any unique string -- doesn't need
    account_type: "Brokerage"          # to look like a real account number
    owner: jordan                      # matches a people.primary/spouse.name
    value: 400000                      # current balance, today's dollars
    pct_growth: 70                     # 0-100 -- % of this account treated
                                        # as growth-like/equity (the rest is
                                        # fixed-income-like)
  - account_number: "demo-401k"
    account_type: "401(k)"
    owner: jordan
    value: 600000
    pct_growth: 80
  - account_number: "demo-roth"
    account_type: "Roth IRA"
    owner: casey
    value: 150000
    pct_growth: 90
```

Or, for finer control, `asset_classes` instead of `pct_growth` on any
account -- per-class percentages summing to 100, matching the SAME
subclass names `returns.asset_classes` itself can itemize elsewhere (see
"Retirement scenario" above):
```yaml
returns:
  asset_classes:
    US Equity:              {expected_return: 7.5, volatility: 18}
    International Equity:   {expected_return: 7.0, volatility: 20}
    US Bonds:                {expected_return: 4.0, volatility: 7}
    Liquidity:               {expected_return: 2.5, volatility: 1}
hypothetical_accounts:
  - account_number: "demo-401k"
    account_type: "401(k)"
    owner: jordan
    value: 600000
    asset_classes:
      US Equity: 60
      US Bonds: 30
      Liquidity: 10
```
Mixing `pct_growth` and `asset_classes` across accounts in the same
scenario works too, as long as `returns.asset_classes` itemizes (or gives
a macro-level fallback for) whatever each style ends up needing -- a
`pct_growth` account always needs `Growth`/`Income` resolvable one way or
another, since that's the fixed 2-class split it always produces (as in
the first example above).

With the `people` section included like this, ONE file is all you need --
no separate profile file, no `--profile` flag:

```bash
python3 -m pt.cli project --scenario my_hypothetical.yaml
python3 -m pt.cli monte-carlo --scenario my_hypothetical.yaml --trials 1000
```

`--db`/`--snapshot` are simply ignored -- neither command touches the
database at all once `hypothetical_accounts` is present in the loaded
scenario. `account_type` accepts the same values as `accounts set-type`
(Brokerage, IRA, 401(k), Roth IRA, Inherited IRA, Inherited Roth IRA,
HSA), and each account sorts into the same taxable/Traditional/Roth/
inherited buckets a real account would.

This is deliberately coarser than a real classified portfolio -- one
`pct_growth` number OR one `asset_classes` map per account instead of
real per-symbol asset-class weights -- appropriate for a quick trial, not
precise allocation modeling. `report` (the rebalancing report) isn't
supported this way --
it needs real classified holdings for its asset-class/style/sector
breakdowns. An "Inherited IRA"/"Inherited Roth IRA" `account_type` here
is just a Traditional/Roth balance with no RMD/10-year-rule schedule
attached -- it doesn't automatically link up with the separate
`scenario.inherited_accounts` section (see "Retirement scenario" above),
which is where that schedule actually comes from; the two features don't
currently compose.

The scenario's embedded `people` section only ever applies when
`hypothetical_accounts` is ALSO present -- a real scenario file (no
`hypothetical_accounts`) ignores a `people` section entirely and keeps
loading the real profile file, unaffected. An explicit `--profile PATH`
always wins over an embedded `people` section too, if you want to mix a
hypothetical portfolio with a real household's actual birthdates for some
reason.

## Backing up

```
python3 -m pt.cli [--db PATH] backup [--out-dir DIR] [--no-profile]
```

| Flag | Default | Meaning |
|---|---|---|
| `--db PATH` | `~/.portfolio_tool/portfolio.db` | Database to back up (global flag, goes before `backup`) |
| `--out-dir DIR` | `~/.portfolio_tool/backups/` | Where to write the backup files |
| `--no-profile` | off | Skip backing up the planning profile and any scenario files; database only |

Examples:

```bash
python3 -m pt.cli backup                        # database + profile + scenario(s) -> ~/.portfolio_tool/backups/
python3 -m pt.cli backup --out-dir ~/Desktop     # write there instead
python3 -m pt.cli backup --no-profile            # database only
python3 -m pt.cli --db /path/to/other.db backup  # back up a non-default database
```

Each run writes new timestamped files (`portfolio-20260825-185406.db`,
`retirement_profile-20260825-185406.yaml`) rather than overwriting the last
backup, so nothing is lost by running it repeatedly. The database copy uses
SQLite's own online backup API (`sqlite3.Connection.backup()`), not a plain
file copy, so it's a consistent snapshot even if something else has the file
open at the time. The profile is skipped (with a note, not an error) if you
haven't created one yet. Any `retirement_scenario*.yaml`/`.yml` files next
to the profile (see "Retirement scenario" above -- there can be several,
under any name matching that pattern) are picked up automatically, each
backed up under its own name.

## Importing multiple CSVs at once, with owners

`import` accepts more than one file, each optionally followed by an owner
string:

```bash
python3 -m pt.cli import ~/Downloads/his_positions.csv "John" ~/Downloads/her_positions.csv "Jane"
```

All files are merged into a single snapshot. The owner is recorded against
every account found in that file (used today just for display in the "By
Account" breakdown). Give it as a plain name, e.g. `"Jane"` -- not `"Jane/62"`
-- matching a key under the planning profile's `people` section (see
"Retirement planning profile" above), so a later feature can look up that
person's age/birthdate from the profile by owner name rather than parsing it
out of the account data (e.g. for RMD calculations). Owner is optional -- you
can leave it off any or all files.

If the same account number shows up in more than one file (e.g. a joint
brokerage account that both spouses' logins can see), it's automatically
recorded as owned by **"Joint"**, regardless of what owner was given for
either file -- and only its first occurrence's holdings are kept, so the
account isn't double-counted. The import summary tells you which accounts
this happened to.

Two accounts that happen to share the same name (e.g. both spouses having a
"Rollover IRA") are never merged -- they're tracked internally by account
number and shown as separate entries everywhere, with owner making it obvious
which is which.

## Where things live

- Database: `~/.portfolio_tool/portfolio.db` by default (override with `--db path`)
- Retirement planning profile: `~/.portfolio_tool/retirement_profile.yaml`
  (see "Retirement planning profile" above)
- Retirement scenario(s): `~/.portfolio_tool/retirement_scenario*.yaml` by
  convention, but any path/filename works with `--scenario` (see
  "Retirement scenario" above)
- Back up the database, profile, and any `retirement_scenario*.yaml`/`.yml`
  files next to the profile with `python3 -m pt.cli backup` -- see
  "Backing up" above for full syntax
- Asset (sub)classes and their macro classes are fixed to keep v1.26 simple --
  see "Asset class hierarchy" above for the full list. Anything you haven't
  classified yet shows up as `Unclassified` in the report so it's never
  silently dropped or misclassified.
- Account type is inferred from the Fidelity account name (looks for ROTH,
  INHERIT/BDA, IRA, 401K, HSA, etc., defaulting to "Brokerage" if none
  match). Fix a wrong guess with `accounts set-type` (see "Debugging an
  unexpected account-type total" above) — no need to touch the SQLite file
  directly.

## Project layout

```
pt/
  db.py           SQLite schema + connection
  importer.py     Broker-agnostic import entry points (detect_broker(),
                  parse_csv(), parse_multiple_csvs()), Fidelity CSV parsing,
                  account-type inference, shared helpers the two below use
  importer_schwab.py   Schwab CSV parsing (multi-account-per-file format)
  importer_etrade.py   E*TRADE CSV parsing (single-account, no account number
                  in the file itself -- see the `import --account` flag)
  store.py        Persists a parsed import as a snapshot
  classifier.py   Security classification table (ticker -> asset class)
  attributes.py   Per-symbol style, sector, and expense ratio
  allocation.py   Household allocation math
  ips.py          IPS target storage + actual-vs-target comparison (household and per-account)
  planning.py     Retirement planning profile loader (~/.portfolio_tool/retirement_profile.yaml)
  scenario.py     Retirement scenario loader (~/.portfolio_tool/retirement_scenario.yaml, or any --scenario path)
  rmd.py          IRS RMD tables + SECURE Act 10-year-rule inherited-account schedules
  tax.py          Federal income tax estimate (brackets, standard deduction, Social Security taxability)
  medicare.py     Medicare Part B premium + Part D IRMAA surcharge (income-tier tables)
  social_security.py  Full Retirement Age + claim-age benefit adjustment (early reduction/Delayed
                       Retirement Credit) -- see "Social Security claim age" in roth_optimizer/README.md
  contributions.py  401(k) employee elective-deferral/catch-up limits (SECURE 2.0)
  projection.py   Year-by-year growth projection model (buckets, RMDs, Social Security, tax, withdrawals)
  backup.py       Timestamped database + profile backups
  report.py       Text report + Excel workbook generation (incl. native charts)
  charts.py       Optional matplotlib chart images for project/monte-carlo
                  --out *.png (needs matplotlib -- see requirements-optional.txt)
  cli.py          Command-line interface
data/
  classifications_seed.csv   Starter ticker -> asset class mapping
  style_seed.csv             Starter ticker -> style-box weights
  sector_seed.csv            Starter ticker -> sector weights
  shared_returns_2026.yaml   Shared returns.asset_classes capital market
                              assumptions, meant to be pulled into a
                              scenario file via `_include` instead of
                              copy-pasting -- see "Sharing assumptions
                              across scenario files" above. Copy forward
                              (shared_returns_2027.yaml, ...) rather than
                              editing in place once a scenario references
                              it, so past runs stay reproducible
scripts/
  update_market_data.py   Optional: fetches sector/expense-ratio/style data from
                           Yahoo Finance for held symbols (needs yfinance,
                           see requirements-optional.txt) -- the only place
                           in this project that makes a network call
  run_scenario.sh         Convenience: `run_scenario.sh <scenario.yaml>
                           [out_dir]` runs project + monte-carlo + report
                           (both --private and --public) for one scenario
                           in a single go, writing a PNG chart and/or an
                           Excel workbook for each, all named after the
                           scenario file -- report's two runs are
                           `<stem>_private_report.{xlsx,txt}` and
                           `<stem>_public_report.{xlsx,txt}`
                           (TRIALS/SEED/PT_REPO env vars)
  run_optimizer.sh        Convenience: `run_optimizer.sh <scenario.yaml>
                           [heir_tax_rate_or_csv_list] [out_dir]` runs
                           `roth_optimizer.cli optimize` (a single rate) or
                           `sweep` (a comma-separated rate list) and saves
                           `<stem>_optimized_<rate>.yaml`/`_projection.txt`
                           per rate -- `--per-bucket-growth` ON by default
                           (RESTARTS/SEED/PER_BUCKET_GROWTH/
                           OPTIMIZE_IRA_DISTRIBUTIONS/
                           OPTIMIZE_INHERITED_WITHDRAWALS/PT_REPO env vars)
  run_priority.sh         Convenience: `run_priority.sh <scenario.yaml>
                           [heir_tax_rate] [out_dir]` runs `roth_optimizer.
                           cli priority` (which owner's conversion should
                           get first claim on a shared bracket ceiling) and
                           saves `<stem>_priority_<owner>first.yaml`/
                           `_projection.txt` per ordering --
                           `--per-bucket-growth` ON by default
                           (RESTARTS/SEED/PER_BUCKET_GROWTH/
                           OPTIMIZE_IRA_DISTRIBUTIONS/
                           OPTIMIZE_INHERITED_WITHDRAWALS/PT_REPO env vars)
  run_claim_age.sh        Convenience: `run_claim_age.sh <scenario.yaml>
                           [heir_tax_rate] [out_dir]` runs `roth_optimizer.
                           cli claim-age` (brute-force grid search over
                           Social Security claim age) and saves
                           `<stem>_claimage_<rate>.yaml`/`_projection.txt`
                           -- `--per-bucket-growth` ON by default
                           (CLAIM_AGES/PER_BUCKET_GROWTH/PT_REPO env vars)
scenario_engine/          Separate companion tool: generates a matrix of
                           scenario YAML files from a base scenario + a
                           sweep config, batch-runs pt's own projection
                           engine against a directory of scenarios, and
                           can search for the best combination of a set
                           of parameters per combination of another set
                           -- see scenario_engine/README.md
roth_optimizer/           Separate companion tool: a true multi-year Roth
                           conversion optimizer -- searches the whole
                           schedule at once (not one year at a time) to
                           maximize an after-tax total estate value
                           objective, via a dependency-free coordinate-
                           ascent search -- see roth_optimizer/README.md
test_data/                Synthetic sample household (CSVs + profile/scenario
                           YAML) for trying the tool without real data --
                           see test_data/README.md and QUICKSTART.md
tests/                    Regression suite (stdlib unittest, no new
                           dependency) -- runs entirely against test_data/'s
                           synthetic household in throwaway temp databases,
                           never your real one -- see tests/README.md
QUICKSTART.md              Five-minute first-run walkthrough
```

Run the regression suite with `python3 -m unittest discover -s tests -t . -v`
-- see [tests/README.md](tests/README.md) for what it covers (and
doesn't).

## Known v1.67 limitations (by design — see roadmap ideas below)

- Single household only; no multi-household support
- No cost-basis / tax-lot analysis, no tax-loss harvesting logic
- No automatic ticker classification lookup (no API calls) — you classify
  by hand, which keeps this dependency-free and fully offline
- No specific trade suggestions (which lot, which account) — just
  dollar drift by asset class
- No CLI command to fix an account's *owner* after the fact (only its
  *type*, via `accounts set-type`) — re-import to change owner
- Style and sector weights are informational only -- there's no "actual vs.
  target" comparison for them the way there is for asset class
- E*TRADE's own export never reveals its account number -- you supply one
  with `--account FILENAME:ACCOUNT_NUMBER` on import (see "Getting your
  brokerage export" above); Vanguard isn't supported yet at all (no clean
  positions export exists on its site today)
- The growth projection is intentionally basic -- see "Growth projection"
  above for the full list of simplifications: one blended growth rate held
  constant (no rebalancing/glide path), federal tax only (no state), taxable
  withdrawals treated as 100% capital gain (no cost basis), inherited
  accounts assume a non-spouse/non-EDB beneficiary, HSAs aren't specially
  modeled, no survivor spending reduction, and only one withdrawal-order
  strategy (not a multi-strategy comparison)

## Ideas for v1.68+ (not built yet)

- CLI command to correct an account's owner after the fact (same pattern as
  `accounts set-type`)
- Actual-vs-target comparison for style and sector, mirroring the asset-class one
- Multiple withdrawal-order strategies to compare side by side (e.g.
  tax-bracket-filling, pro-rata across buckets, RMD-driven) -- currently
  only the household's single chosen order (taxable -> Traditional -> Roth,
  with inherited accounts on their own forced schedule) is modeled
- State income tax (currently federal-only, per this household's choice)
- Eligible-designated-beneficiary handling for inherited accounts (spousal
  rollover, or the life-expectancy stretch for a minor child, disabled/
  chronically ill beneficiary, or someone not more than 10 years younger
  than the decedent) -- currently only the non-spouse/non-EDB 10-year rule
  is modeled
- HSA-specific tax treatment (currently folded into the taxable bucket)
- Cost-basis tracking for taxable-brokerage withdrawals, instead of
  assuming 100% long-term capital gain
- Survivor spending reduction and/or per-scenario allocation glide path in
  the growth projection
- Trade suggestions that prefer tax-advantaged accounts for the biggest moves
- Historical drift chart across snapshots
- Vanguard import support (Fidelity/Schwab/E*TRADE already supported --
  see "Getting your brokerage export" above -- Vanguard deferred: no
  clean "all current holdings" CSV exists on Vanguard's site today, only
  a taxable-only cost-basis export or an 18-month transaction history)

## Disclaimer

This tool is tax and retirement-planning *modeling*, not financial, tax,
or legal advice. It encodes a set of assumptions (see "Growth projection"
above) that may not match your actual circumstances, and current tax law
may change. **Double-check any output against a fiduciary financial
advisor and/or tax professional before making an actual investment,
withdrawal, Roth conversion, or other financial decision based on it.**

In the words of the MIT License this project ships under (see "License"
below), in plain English: the author provides this software as-is, with
no warranty that it's correct, and is not liable for any damages or
losses -- financial or otherwise -- arising from its use. The actual
legal text, for the record:

> IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY
> CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT,
> TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE
> SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

## License

MIT -- see [LICENSE](LICENSE). This is tax and retirement-planning
*modeling*, not financial advice -- see "Disclaimer" above.
