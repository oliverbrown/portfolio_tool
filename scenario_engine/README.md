# Scenario Sweep Engine

A companion tool to `pt` (see the top-level [README.md](../README.md)) --
generates a matrix of retirement scenario YAML files from a base scenario
plus a "sweep config" describing which parameters to vary and over what
range, then either runs `pt`'s own projection engine against every
scenario in a directory (`run`), or searches for the single best
combination of a set of parameters per combination of another set
(`optimize`) -- producing a `.txt` and/or `.xlsx` per result plus a
summary comparing outcomes.

A separate top-level package from `pt` -- `pt` is considered stable;
this reuses `pt`'s scenario/projection/report/planning/db/allocation
modules rather than duplicating them, but ships and runs independently.
Run everything from the repo root, the same way as `pt.cli`:

```bash
python3 -m scenario_engine.cli generate --sweep my_sweep.yaml --out-dir scenarios/
python3 -m scenario_engine.cli run --scenarios-dir scenarios/ --out-dir results/
python3 -m scenario_engine.cli optimize --sweep my_sweep.yaml --out-dir optimized/
```

## `generate`: build the matrix

```bash
python3 -m scenario_engine.cli generate --sweep my_sweep.yaml
python3 -m scenario_engine.cli generate --sweep my_sweep.yaml --out-dir scenarios/   # default anyway
python3 -m scenario_engine.cli generate --sweep my_sweep.yaml --max-combinations 500 # raise the safety limit
```

Takes a **sweep config** -- a small YAML file you write, naming a base
scenario and which of its fields to vary:

```yaml
# Path to the scenario YAML to start from -- relative to THIS sweep
# config file's own location (not your current directory), so a sweep
# config can sit next to its base scenario and be run from anywhere.
base_scenario: retirement_scenario.yaml

# One entry per field to vary, keyed by its DOTTED PATH into the scenario
# dict -- e.g. "retirement.retirement_age" for
#   retirement:
#     retirement_age: 64
# Each is a numeric range: min, max, step -- inclusive of max if it lands
# exactly on a step (e.g. min: 60, max: 66, step: 2 -> 60, 62, 64, 66).
parameters:
  retirement.retirement_age:
    min: 60
    max: 66
    step: 2
  income.target_spending:
    min: 120000
    max: 160000
    step: 20000
  returns.growth:
    min: 6.0
    max: 10.0
    step: 2.0
  # A returns.asset_classes entry (see the top-level README.md's
  # "Retirement scenario") is just another nested dict, so it sweeps the
  # same dotted-path way -- a subclass name with a space in it (e.g. "US
  # Equity") is still ONE path segment, not split further:
  #returns.asset_classes.US Equity.expected_return:
  #  min: 6.0
  #  max: 9.0
  #  step: 1.0
```

`generate` writes one scenario YAML per combination in the **Cartesian
product** across every parameter's value list (3 retirement ages x 3
spending levels x 3 growth rates above = 27 scenarios) into `--out-dir`
(default `scenarios/`) -- each a full copy of the base scenario with just
that combination's values overridden; everything else (mortality,
`roth_conversions`, `inherited_accounts`, Social Security, health
insurance, etc.) carries over completely unchanged. A safety limit
(`--max-combinations`, default 200) guards against a step that's an order
of magnitude too fine silently generating thousands of files -- raise it
if you really want that many.

Filenames are descriptive, e.g.:
```
retirement_scenario__retirement_age-64__target_spending-140000__growth-8.0.yaml
```
Each swept field is shortened to just its own distinguishing part -- the
last dotted segment for a plain path (`income.target_spending` ->
`target_spending`), or `<owner>_<last segment>` for a `section[owner]`
selector (`roth_conversions[alex].constraints.max_conversion_per_year`
-> `alex_max_conversion_per_year`, since the owner is what actually
disambiguates two entries in the same section, not the section/nested
field scaffolding around it). If two DIFFERENT swept fields would still
shorten to the same label (e.g. `health_insurance.alex.monthly_amount`
and `health_insurance.jamie.monthly_amount` both end in
`monthly_amount`), both fall back to their full dotted path instead, so
a collision never silently loses information.

**If the result would still run past 180 characters** (possible with
several swept parameters at once, especially `section[owner]` selector
paths -- e.g. optimize's inner grid often has a couple) -- each label
gets clipped to 16 characters (values are never truncated, only the
parameter-name labels), rather than replacing the whole name with
something opaque. Excel (even on Mac -- a legacy Windows-derived limit)
can flatly refuse to open a file whose full path runs much past ~218
characters, which an uncapped descriptive name can reach fast once
several parameters are swept at once. The full, untruncated parameter
values are always in `manifest.csv`/`search_results.csv` regardless, so
nothing is ever actually lost -- a long filename is just possibly
abbreviated. In the rare case two different combinations still land on
the identical filename after all of that, a `__2`/`__3`/... suffix is
added so results can never silently overwrite each other.

A `manifest.csv` is also written alongside the generated files, mapping
each filename back to the parameter values that produced it.

**What can be varied**: any field inside a nested MAPPING in the scenario
-- `retirement.*`, `income.target_spending`, `inflation.*`, `returns.*`,
`cash_reserve.years`, `social_security.<name>.*`,
`health_insurance.<name>.monthly_amount` all work, since those sections
are all nested dicts.

A field inside `roth_conversions`, `ira_distributions`, `rollovers`, or
`retirement_contributions` -- each a LIST of one entry per owner -- can
also be varied, using a `section[owner]` selector at the start of the
path: the entry in that section whose own `owner` field matches the
bracketed name (case-insensitive), then an ordinary dotted path from
there:

```yaml
parameters:
  roth_conversions[alex].constraints.max_marginal_bracket:
    min: 22
    max: 32
    step: 5   # NOTE: step must land on a real bracket rate -- 10, 12, 22,
              # 24, 32, 35, 37 -- see the note below
  roth_conversions[alex].constraints.avoid_irmaa_tier:
    min: 1
    max: 4
    step: 1
  retirement_contributions[jamie].employer_match.amount:
    min: 2000
    max: 6000
    step: 2000
```

This does **not** work for `inherited_accounts` (entries are identified
by `account_number`/`hypothetical_value`, not a single owner -- several
can share the same beneficiary) or `taxable_cash_flows` (entries have no
owner field at all); varying those isn't supported. A typo'd or
unsupported parameter path -- including a `section[owner]` selector
naming a section that isn't a list, or an owner not present in it --
fails immediately with a clear error, before anything is written.

**A generated combination can still be individually invalid** even when
the sweep config itself is fine -- e.g. a swept `max_marginal_bracket`
step that doesn't land on a real federal bracket rate (10, 12, 22, 24,
32, 35, 37). `generate` doesn't catch this (it doesn't know pt's
validation rules), but `run` does: that one scenario is skipped with pt's
own error message, and every other scenario in the batch still runs
normally.

## `run`: batch-run a directory of scenarios

```bash
python3 -m scenario_engine.cli run --scenarios-dir scenarios/
python3 -m scenario_engine.cli run --scenarios-dir scenarios/ --out-dir results/   # default anyway
python3 -m scenario_engine.cli run --scenarios-dir scenarios/ --format txt         # or xlsx, or both (default)
python3 -m scenario_engine.cli run --scenarios-dir scenarios/ --profile some_profile.yaml --db some.db --snapshot 5
python3 -m scenario_engine.cli run --scenarios-dir scenarios/ --verbose            # per-account columns, see below
```

Runs `pt`'s projection engine (the exact same model as `pt.cli
project`, including the SCENARIO CONFIGURATION block at the top/bottom of
each output) against every `*.yaml`/`*.yml` file in `--scenarios-dir` --
not necessarily ones `generate` produced; any directory of valid scenario
files works, including ones you hand-authored or hand-edited after
generating. For each, writes a same-named `.txt` and/or `.xlsx` (per
`--format`) into `--out-dir` (default `results/`), against the
household's current portfolio (same `--profile`/`--db`/`--snapshot` flags
as `pt.cli project`/`report`). `-v`/`--verbose` adds a column for every
account currently over `$500` to every result, exactly like `pt.cli
project --verbose` -- see pt's top-level README for what those columns
mean (which are exact vs. a derived proportional share). A scenario that fails to load or project
(e.g. a bad edit) is skipped with a clear message rather than aborting
the whole batch.

After running everything, prints (and saves to `results/summary.csv`) a
table comparing each scenario's outcome, sorted by final balance:
- **outcome**: `ok` (ran to age 100 with no shortfall), `shortfall` (ran
  out of money at some point -- see `first_shortfall_year`), or `estate`
  (everyone in the household died before age 100 under this scenario's
  `mortality` dates -- `final_total_balance` is the estate's value)
- **final_year** / **final_total_balance**: the last projected year and
  the household's total balance then (or the estate's value, for
  `estate`)

This is the fast way to answer "which of these 27 what-ifs actually look
good" without opening 27 spreadsheets one at a time -- open the specific
scenario's own `.txt`/`.xlsx` afterward for the full year-by-year detail
once the summary has narrowed down which ones are interesting.

## `optimize`: find the best of a set, per context

```bash
python3 -m scenario_engine.cli optimize --sweep my_sweep.yaml --out-dir optimized/
python3 -m scenario_engine.cli optimize --sweep my_sweep.yaml --format txt          # skip xlsx
python3 -m scenario_engine.cli optimize --sweep my_sweep.yaml --max-combinations 100000
python3 -m scenario_engine.cli optimize --sweep my_sweep.yaml --log-all-candidates  # also dump every candidate
python3 -m scenario_engine.cli optimize --sweep my_sweep.yaml --verbose             # per-account columns, winner only
```

`generate`/`run` produce every combination. `optimize` is for a different
question: *"for each of these contexts, which of these OTHER values gives
the best result?"* -- e.g. "for every combination of retirement age,
spending, and market return, which Roth-conversion caps minimize total
tax + healthcare cost?" It searches without writing a file per candidate
(a single in-memory projection takes well under a millisecond, so tens of
thousands of candidates take seconds, not hours) and keeps only each
context's winner -- "only the results that satisfy the goal," not the
whole matrix.

A sweep config for `optimize` has two grids instead of one:

```yaml
base_scenario: retirement_scenario.yaml

# OUTER grid (optional) -- same shape as generate's `parameters`, one
# independent search per combination. Omit entirely to search just once,
# against the base scenario unchanged.
parameters:
  retirement.retirement_age: {min: 62, max: 65, step: 1}
  income.target_spending: {min: 130000, max: 170000, step: 20000}
  returns.growth: {min: 4.0, max: 8.0, step: 1.0}

# INNER grid -- searched per outer combination (or once, if there's no
# outer grid); only the winner is kept
optimize:
  minimize_sum_of:            # minimize this sum...
    - cumulative_tax_in_retirement
    - cumulative_healthcare_expense
  maximize_sum_of:            # ...AND maximize this one, at the same time
    - total_balance
  parameters:
    roth_conversions[alex].constraints.max_conversion_per_year:
      min: 100000
      max: 300000
      step: 100000
    roth_conversions[jamie].constraints.max_conversion_per_year:
      min: 20000
      max: 40000
      step: 10000
```

`optimize.parameters` uses the exact same addressing as `generate`'s
`parameters` -- plain dotted paths and `section[owner]` selectors both
work, including everything the section above documents.

**The objective** (`minimize_sum_of`/`maximize_sum_of` -- at least one of
them, both are fine together) is a list of projection row field names,
summed at the candidate's **last projected row** -- i.e. the final
"total cumulative X" as of the end of that run. Any numeric field from a
projection row works -- `cumulative_tax_in_retirement`,
`cumulative_healthcare_expense`, `total_balance`, `tax`, `spending`, etc.
(see `pt/projection.py`'s `project()` for the full row shape). A typo'd
field name fails immediately, before the search runs -- and so does a
typo'd KEY under `optimize` itself (e.g. `maximumize_sum_of`), with a
suggested correction when one's obvious, rather than silently being
ignored as an unrecognized key and producing a confusing "no objective
given" error instead.

**Minimizing and maximizing at the same time** (giving both
`minimize_sum_of` and `maximize_sum_of`) combines everything into ONE
score -- minimized fields count normally, maximized fields count
negated -- and finds the combination minimizing that combined score,
which simultaneously pushes the minimized fields down and the maximized
fields up. This is a straight EQUAL-WEIGHT combination: if the fields
being minimized and maximized aren't on comparable scales (e.g. a ~$1M
tax figure vs. a ~$20M balance figure), the larger-magnitude field will
dominate the ranking -- check the actual field values in
`search_results.csv`/`all_candidates.csv` (`--log-all-candidates`), not
just which combination "won," when mixing scales that differ a lot.

**The reported/logged score's sign** depends on what you gave: with any
`minimize_sum_of` field present (whether alone or mixed with
`maximize_sum_of`), lower is better, and the number is a net-cost-style
figure (minimized fields minus maximized ones -- can go quite negative
when a large maximized field dominates, that's expected, not an error).
With `maximize_sum_of` ALONE (no `minimize_sum_of` at all), the score is
flipped back to its natural sign so it reads as the real
(higher-is-better) value -- e.g. `maximize_sum_of: [total_balance]` on
its own reports the actual total_balance, not its negation. Either way,
the printed search header says which direction is better for that run.

**Output**, per outer combination (just once, if there's no outer grid):
one scenario YAML + `.txt`/`.xlsx` for the WINNING inner combination only
(named from both the outer and inner values -- see `generate`'s filename
note above for the labeling/truncation rules; optimize's combined
outer+inner parameter count means it hits the truncation case more often),
plus one row in `search_results.csv` (outer values, winning inner
values, the objective score, `outcome`, and `at_boundary`).

**`at_boundary`** flags when a winning value landed exactly on the min or
max of its own swept range -- a real signal the true optimum may lie
outside the range you searched, worth widening and re-running. It shows
up constantly if a range is so wide it never actually constrains
anything (e.g. a conversion cap far above what the household could ever
convert) -- in that case every value in the range scores identically and
the "winner" is just whichever was tried first, not a meaningful choice;
narrow the range to where it actually matters.

**`--log-all-candidates`** additionally writes `all_candidates.csv` with
every single candidate's objective value (not just the winners) --
useful for inspecting the shape of the search space, at the cost of a
much bigger file for a big search.

**`-v`/`--verbose`** adds the same per-account columns as `pt.cli
project --verbose` -- but only to each outer combination's WINNING
result, computed once after the search picks it, not for every candidate
along the way (account_columns has no effect on the objective, so
building it during the search itself would just be wasted work).

Same safety-limit idea as `generate`, but on the TOTAL (outer x inner)
projection count and with a much higher default (`--max-combinations`,
default 50,000) -- since nothing here is written to disk per candidate,
a much larger search is cheap.

**Iterating**: since everything is config-driven, trying a different set
of values to sweep/optimize is just editing the YAML and re-running --
no code changes. A common workflow: run once, check `at_boundary` and
`search_results.csv`, narrow or shift the ranges that hit a boundary or
look interesting, run again.

## Notes

- Nothing here writes to your portfolio database -- all three commands
  only read your holdings (for the starting balance) and never modify
  anything in `~/.portfolio_tool/portfolio.db` or your planning profile.
- Generated scenario files are completely ordinary scenario YAML -- open
  one in `pt.cli project --scenario ...` directly any time, hand-edit
  it, or feed a hand-edited copy back through `run` on its own.
- This is intentionally a thin, separate tool: it does not modify `pt`
  at all, and could in principle be deleted without affecting `pt` in
  any way.
