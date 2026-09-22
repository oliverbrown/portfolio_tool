"""Builds a matrix of retirement scenario dicts from a base scenario plus
a "sweep config" describing which parameters to vary and over what range
-- see README.md for the sweep config's full syntax.

Each combination in the Cartesian product across every swept parameter's
value list becomes its own scenario -- a deep copy of the base scenario
with just that combination's values overridden. Everything else in the
base scenario (mortality, roth_conversions, inherited_accounts, social
security, etc.) carries over completely unchanged.

Parameters are addressed by DOTTED PATH into the scenario dict's nested
mappings -- e.g. "retirement.retirement_age", "income.target_spending",
"health_insurance.alex.monthly_amount" (health_insurance and
social_security are dicts keyed by person name, so a path into a
specific person's own field works fine).

A path into a LIST-valued section (roth_conversions, ira_distributions,
rollovers, retirement_contributions -- each a list of one entry per
owner) can ALSO be addressed, using a "section[owner]" selector at the
start of the path -- e.g.
"roth_conversions[alex].constraints.max_marginal_bracket" or
"retirement_contributions[alex].employer_match.amount" -- matching
that section's entry whose own "owner" field equals the bracketed name
(case-insensitive), then continuing as an ordinary dotted path from
there. This does NOT work for inherited_accounts (entries are identified
by account_number/hypothetical_value, not a single owner -- several can
share the same beneficiary) or taxable_cash_flows (entries have no owner
field at all); varying those isn't supported.

A sweep config can ALSO have an 'optimize' section, for a nested search
rather than a plain sweep -- see parse_objective()/objective_value() and
scenario_engine/cli.py's cmd_optimize(). The top-level 'parameters' (if
any) become an OUTER grid, each combination its own independent scenario
context; optimize.parameters become an INNER grid searched, per outer
combination, purely in memory (no files written for a candidate) to find
the one minimizing/maximizing optimize.minimize_sum_of/maximize_sum_of
(a sum of the LAST projected year's row fields) -- only that winning
combination is written out. This is what makes a search over tens of
thousands of candidates take seconds rather than hours: build_matrix()
itself doesn't care whether it's building an outer or inner grid, or
whether its result gets written to disk at all -- cmd_optimize() is the
only place that distinction is made."""
import difflib
import itertools
import re
from copy import deepcopy
from pathlib import Path

import yaml

# Matches a "section[owner].rest.of.path" parameter path -- see the
# module docstring. Anchored to the START of the path only; a bracket
# selector partway through a path isn't supported (there's no known use
# case for one nested section inside another right now).
_LIST_SELECTOR_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\[([^\]]+)\]\.(.+)$")

# A soft safety net against a step that's an order of magnitude too fine
# (e.g. step: 0.1 instead of 1) silently generating thousands of files --
# not a hard ceiling on how large a sweep can be; --max-combinations on
# the CLI raises it for a household that really does want more.
DEFAULT_MAX_COMBINATIONS = 200

# optimize's total (outer x inner) combination count gets its own, much
# higher default -- unlike generate, none of those candidates are written
# to disk (see the module docstring), so a much larger search is cheap:
# a single in-memory projection takes under a millisecond, so even 50,000
# of them is well under a minute.
DEFAULT_MAX_OPTIMIZE_COMBINATIONS = 50_000


class SweepError(Exception):
    pass


def load_sweep_config(path) -> dict:
    """Loads and parses a sweep config file. Raises SweepError with a
    clear message if it doesn't exist, fails to parse, or is missing a
    required top-level key. base_scenario is always required; at least
    one of 'parameters' (a plain sweep -- see build_matrix()) or
    'optimize' (a nested search -- see parse_objective()/
    optimize_search()) must be present, but neither is required on its
    own -- generate/run need 'parameters', optimize needs 'optimize', and
    each command checks for its own requirement specifically so the
    error names the right thing."""
    path = Path(path)
    if not path.exists():
        raise SweepError(f"No sweep config found at {path}.")
    with open(path, encoding="utf-8") as f:
        try:
            config = yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise SweepError(f"Couldn't parse {path} as YAML: {e}")
    if not isinstance(config, dict):
        raise SweepError(f"{path} doesn't look like a valid sweep config (expected a YAML mapping).")
    if not config.get("base_scenario"):
        raise SweepError(f"{path} is missing required key 'base_scenario' (path to the scenario YAML to vary).")
    if not config.get("parameters") and not config.get("optimize"):
        raise SweepError(f"{path} has neither 'parameters' nor 'optimize' -- nothing to do.")
    return config


def _values_for_range(path: str, spec: dict) -> list:
    """spec: {min, max, step}. Returns the inclusive list of values from
    min to max in step increments. Computed as min + n*step (not repeated
    addition) so a float step (e.g. 0.5) doesn't accumulate rounding
    drift; each value is then rounded to 10 decimal places to clean up
    the float noise that's still possible in that one multiplication
    (e.g. 6.0 + 1*0.1 landing on 6.099999999999999), and normalized to an
    int when it's a whole number (8.0 -> 8) purely for a cleaner
    filename/YAML -- it doesn't change what the value means anywhere
    downstream, since pt treats an int and an equal-valued float
    identically."""
    missing = [k for k in ("min", "max", "step") if k not in spec]
    if missing:
        raise SweepError(f"parameters.{path} is missing {', '.join(missing)} (need all of min/max/step).")
    lo, hi, step = spec["min"], spec["max"], spec["step"]
    if step <= 0:
        raise SweepError(f"parameters.{path}.step must be positive (got {step}).")
    if hi < lo:
        raise SweepError(f"parameters.{path}: max ({hi}) is less than min ({lo}).")
    values = []
    n = 0
    while True:
        v = round(lo + n * step, 10)
        if v > hi + 1e-9:
            break
        if isinstance(v, float) and v.is_integer():
            v = int(v)
        values.append(v)
        n += 1
    return values


def _resolve_container(d: dict, path: str):
    """If path starts with a "section[owner]." selector (see the module
    docstring), resolves into the matching list entry -- the one in
    d[section] whose own "owner" field equals `owner`, case-insensitive
    -- and returns (that entry dict, the rest of the path) so the
    caller's plain dotted-path logic can continue unchanged from there.
    Otherwise (an ordinary dotted path, no selector) returns (d, path)
    as-is. Raises SweepError if the named section isn't a list in this
    scenario, or no entry in it has that owner -- both almost certainly
    mean the path has a typo, or targets a scenario that doesn't
    actually have this owner configured for that section."""
    m = _LIST_SELECTOR_RE.match(path)
    if not m:
        return d, path
    section, owner, rest = m.groups()
    entries = d.get(section)
    if not isinstance(entries, list):
        raise SweepError(
            f"'{section}' isn't a list in this scenario (needed to resolve '{path}') -- check for a typo, "
            "or that the base scenario actually has this section configured."
        )
    owner_lower = owner.strip().lower()
    for entry in entries:
        if isinstance(entry, dict) and (entry.get("owner") or "").strip().lower() == owner_lower:
            return entry, rest
    raise SweepError(
        f"No entry with owner '{owner}' found in '{section}' (needed to resolve '{path}') -- check the "
        "spelling, or that the base scenario actually has an entry for this owner in this section."
    )


def _get_path(d: dict, path: str):
    """None if any segment of path is missing -- used only to validate a
    swept path actually exists somewhere in the base scenario before
    generating anything, so a typo'd parameter name fails fast with one
    clear error instead of silently producing scenarios where the
    "override" just never took effect. Can also raise SweepError itself,
    via _resolve_container(), for a bad "section[owner]" selector."""
    node, path = _resolve_container(d, path)
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def _set_path(d: dict, path: str, value):
    node, path = _resolve_container(d, path)
    parts = path.split(".")
    for part in parts[:-1]:
        if not isinstance(node.get(part), dict):
            node[part] = {}
        node = node[part]
    node[parts[-1]] = value


def _value_lists(base_scenario: dict, parameters: dict, section_label: str = "parameters"):
    """Validates every path in `parameters` (see _values_for_range() for
    the min/max/step shape, _get_path() for the existence check) and
    returns (paths, value_lists) -- paths sorted for a deterministic
    order regardless of the sweep config's own key order, value_lists
    the corresponding per-path value list. section_label only affects
    error message wording (e.g. "parameters.X" vs "optimize.parameters.X"),
    so a typo in either section names itself correctly. Shared by
    build_matrix() and combination_count() so they can never disagree
    about what a given `parameters` dict expands to."""
    paths = sorted(parameters)
    for path in paths:
        if _get_path(base_scenario, path) is None:
            raise SweepError(
                f"{section_label}.{path} doesn't match anything in the base scenario -- check for a typo, or "
                "that the base scenario actually has this section (e.g. a commented-out optional section "
                "needs uncommenting there first)."
            )
    return paths, [_values_for_range(f"{section_label}.{p}", parameters[p]) for p in paths]


def combination_count(base_scenario: dict, parameters: dict, section_label: str = "parameters") -> int:
    """How many combinations `parameters` would expand to against
    base_scenario, without materializing any of them -- used to validate
    a search's total size (e.g. optimize's outer x inner product) before
    committing to running it. Same validation as build_matrix() (a typo'd
    path still fails here)."""
    _, value_lists = _value_lists(base_scenario, parameters, section_label)
    total = 1
    for vs in value_lists:
        total *= len(vs)
    return total


def build_matrix(base_scenario: dict, parameters: dict, max_combinations: int = DEFAULT_MAX_COMBINATIONS,
                  section_label: str = "parameters") -> list:
    """parameters: {dotted.path: {min, max, step}, ...} -- see
    _values_for_range(). Returns a list of (overrides, scenario) pairs, one
    per combination in the Cartesian product across every parameter's
    value list: overrides is {dotted.path: value} for that one
    combination (used to build a readable filename and the manifest);
    scenario is a full deep copy of base_scenario with those values
    applied. An EMPTY parameters dict is valid and returns exactly one
    combination -- ({}, a deep copy of base_scenario unchanged) -- rather
    than zero, so a sweep config with no outer grid (just 'optimize') can
    reuse this the same way. Raises SweepError if a swept path doesn't
    exist anywhere in base_scenario (likely a typo), or if the product
    would exceed max_combinations."""
    paths, value_lists = _value_lists(base_scenario, parameters, section_label)

    total = 1
    for vs in value_lists:
        total *= len(vs)
    if total > max_combinations:
        raise SweepError(
            f"This sweep would generate {total} scenarios, over the {max_combinations} safety limit -- narrow "
            "the ranges/steps, or pass --max-combinations to raise the limit if you really want this many."
        )

    combos = []
    for combo in itertools.product(*value_lists):
        overrides = dict(zip(paths, combo))
        scenario = deepcopy(base_scenario)
        for path, value in overrides.items():
            _set_path(scenario, path, value)
        combos.append((overrides, scenario))
    return combos


def parse_objective(config: dict):
    """Reads the sweep config's 'optimize' section's minimize_sum_of/
    maximize_sum_of -- each an optional list of projection row field
    names (e.g. cumulative_tax_in_retirement, cumulative_healthcare_
    expense, total_balance -- see pt/projection.py's project() for the
    full set of fields a row has); AT LEAST ONE of the two must be
    given, but unlike earlier versions of this tool, BOTH may be given
    together -- minimizing one set of fields and maximizing another at
    the same time (e.g. minimize tax + healthcare cost while also
    maximizing the final balance).

    Returns `terms`: a list of (field, sign) pairs -- sign +1.0 for each
    minimize_sum_of field, -1.0 for each maximize_sum_of field. The
    single scalar score objective_value() computes from `terms` (sum of
    sign*row[field]) is always MINIMIZED by the caller -- a maximize_
    sum_of field achieves "maximize this" by minimizing its negation
    alongside whatever else is being minimized. This is a straight EQUAL-
    WEIGHT linear combination: if the fields being minimized and maximized
    aren't on comparable scales (e.g. a $1M tax figure vs. a $20M balance
    figure), the larger-magnitude field will dominate the combined score
    -- worth checking search_results.csv's actual field values, not just
    trusting the ranking, when mixing scales.

    Also raises SweepError for any key under 'optimize' other than
    minimize_sum_of/maximize_sum_of/parameters -- a typo'd key (e.g.
    "maximumize_sum_of") would otherwise just be silently ignored,
    since YAML has no schema of its own to catch it, producing exactly
    this function's "neither is given" error with no clue why; naming
    the actual bad key (and, via difflib, a likely intended one) is far
    more useful than that generic message alone.

    Raises SweepError if neither is given, or both are given but empty."""
    opt = config.get("optimize") or {}
    known_keys = {"minimize_sum_of", "maximize_sum_of", "parameters"}
    for key in opt:
        if key not in known_keys:
            suggestion = difflib.get_close_matches(key, known_keys, n=1)
            hint = f" -- did you mean '{suggestion[0]}'?" if suggestion else ""
            raise SweepError(f"optimize has an unrecognized key '{key}'{hint}")

    minimize = opt.get("minimize_sum_of") or []
    maximize = opt.get("maximize_sum_of") or []
    if not minimize and not maximize:
        raise SweepError(
            "optimize needs minimize_sum_of and/or maximize_sum_of -- each a list of projection row field "
            "names to sum (e.g. minimize_sum_of: [cumulative_tax_in_retirement, "
            "cumulative_healthcare_expense], maximize_sum_of: [total_balance])."
        )
    return [(f, 1.0) for f in minimize] + [(f, -1.0) for f in maximize]


def objective_value(row: dict, terms: list) -> float:
    """The sum of sign*row[field] for each (field, sign) in `terms` (see
    parse_objective()) -- row is normally a projection's LAST row, so
    this is a combination of "total cumulative X" values as of the end
    of that projection. Lower is always better -- a maximize_sum_of
    field's negative sign is already baked into `terms`. Raises
    SweepError (not KeyError) if a field name doesn't match a real row
    field -- almost certainly a typo, since the field list comes
    straight from the sweep config."""
    total = 0.0
    for f, sign in terms:
        if f not in row:
            raise SweepError(
                f"optimize field '{f}' isn't a real projection row field -- see pt/projection.py's project() "
                "for the full list (e.g. cumulative_tax_in_retirement, cumulative_healthcare_expense, "
                "total_balance, tax, spending)."
            )
        total += sign * (row[f] or 0.0)
    return total


def format_objective(terms: list) -> str:
    """A human-readable label for `terms`, e.g.
    "minimize(cumulative_tax_in_retirement + cumulative_healthcare_expense) &
    maximize(total_balance)" -- or just the one half, if only
    minimize_sum_of or only maximize_sum_of was given."""
    minimize_fields = [f for f, sign in terms if sign > 0]
    maximize_fields = [f for f, sign in terms if sign < 0]
    parts = []
    if minimize_fields:
        parts.append(f"minimize({' + '.join(minimize_fields)})")
    if maximize_fields:
        parts.append(f"maximize({' + '.join(maximize_fields)})")
    return " & ".join(parts)


def _sanitize_label(path: str) -> str:
    """path, made filesystem-friendly: dots -> underscores, and a
    "section[owner]" selector's brackets -> underscores/dropped (e.g.
    "roth_conversions[alex].constraints.max_marginal_bracket" ->
    "roth_conversions_alex_constraints_max_marginal_bracket"). The FULL
    path -- verbose, but unambiguous; used as scenario_filename()'s
    fallback when a shorter label would collide with another swept
    path's."""
    return path.replace(".", "_").replace("[", "_").replace("]", "")


def _compact_label(path: str) -> str:
    """A short label for one swept path, keeping only what actually
    distinguishes it: the last dotted segment (e.g.
    "retirement.retirement_age" -> "retirement_age",
    "income.target_spending" -> "target_spending") -- or, for a
    "section[owner].rest" selector, "<owner>_<last segment of rest>"
    (e.g. "roth_conversions[alex].constraints.max_conversion_per_year"
    -> "alex_max_conversion_per_year") -- the owner is what actually
    disambiguates two entries in the same list section, not which
    section/nested field within it, so it's kept while the verbose
    "roth_conversions_constraints" scaffolding is dropped. Can collide
    with another swept path's compact label (e.g. two different sections
    both ending in the same field name) -- scenario_filename() checks
    for that across the actual set being named and falls back to
    _sanitize_label()'s full path for just the colliding ones."""
    m = _LIST_SELECTOR_RE.match(path)
    if m:
        _, owner, rest = m.groups()
        return f"{owner}_{rest.rsplit('.', 1)[-1]}"
    return path.rsplit(".", 1)[-1]


# Excel (even on Mac -- a legacy Windows-derived limit) can flatly refuse
# to open a file once its full PATH gets much past ~218 characters. This
# module only controls the FILENAME, not the directory it ends up saved
# in, so it stays well under that on its own -- generous enough for a
# handful of swept parameters even with compact labels, while leaving
# real headroom for whatever directory depth the file is saved at.
MAX_FILENAME_LENGTH = 180

# Last-resort per-label truncation if compact labels alone still aren't
# short enough (many swept parameters at once) -- clips each label (not
# its value) to this many characters rather than replacing the whole
# name with a hash, so a truncated name still shows real information,
# just abbreviated, instead of becoming opaque.
_MAX_LABEL_LENGTH = 16


def scenario_filename(base_stem: str, overrides: dict) -> str:
    """A descriptive filename for one combination, e.g.
    "retirement_scenario__retirement_age-64__target_spending-140000.yaml"
    -- see _compact_label() for how each swept path becomes a short
    label. Two swept paths whose compact labels would collide (e.g. two
    list-section entries for different owners with different leaf
    fields, or -- pathologically -- two plain paths that happen to share
    a last segment) each fall back to _sanitize_label()'s full,
    unambiguous path instead, so the filename never loses information to
    a collision.

    If the result would still exceed MAX_FILENAME_LENGTH (many swept
    parameters at once), each label is further clipped to
    _MAX_LABEL_LENGTH characters -- values are never truncated, only the
    parameter-name labels -- rather than replacing the name with a hash;
    the full parameter values are always in whichever CSV generate/
    optimize also writes (manifest.csv / search_results.csv) regardless,
    so nothing is ever actually lost, just possibly abbreviated in the
    filename itself."""
    paths = sorted(overrides)
    compact_counts = {}
    for path in paths:
        compact_counts[_compact_label(path)] = compact_counts.get(_compact_label(path), 0) + 1
    labels = {
        path: _compact_label(path) if compact_counts[_compact_label(path)] == 1 else _sanitize_label(path)
        for path in paths
    }

    parts = [base_stem] + [f"{labels[p]}-{overrides[p]}" for p in paths]
    full = "__".join(parts) + ".yaml"
    if len(full) <= MAX_FILENAME_LENGTH:
        return full

    parts = [base_stem] + [f"{labels[p][:_MAX_LABEL_LENGTH]}-{overrides[p]}" for p in paths]
    return "__".join(parts) + ".yaml"
