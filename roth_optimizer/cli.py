"""
Command-line interface for the Roth conversion optimizer -- a companion
tool to pt (see pt/README.md), run the same way (`python3 -m
roth_optimizer.cli ...` from the repo root, alongside `python3 -m
pt.cli ...`) but a separate package/entry point.

    python3 -m roth_optimizer.cli optimize --heir-tax-rate 24
    python3 -m roth_optimizer.cli sweep --heir-tax-rates 10,12,22,24,32,35,37
    python3 -m roth_optimizer.cli priority --heir-tax-rate 24
    python3 -m roth_optimizer.cli claim-age --heir-tax-rate 24

See README.md in this directory for the objective/search algorithm and a
full walkthrough; see optimizer.py for the implementation.
"""
import argparse
import csv
import os
import sys
import time
from pathlib import Path

import yaml

from pt import allocation as alloc
from pt import db as db_mod
from pt import planning
from pt import projection as projection_mod
from pt import report as report_mod
from pt import scenario as scenario_mod
from pt import social_security as social_security_mod

from . import optimizer as optimizer_mod
from . import scatter_chart as scatter_chart_mod


def _load_profile_for(args, scenario: dict) -> dict:
    """Same rule as pt.cli's own _load_profile_for() (see that function's
    docstring) -- kept as its own copy here rather than a shared import,
    the same way scenario_engine/cli.py keeps its own account-fetching
    logic rather than reusing pt.cli's internals."""
    if args.profile:
        return planning.load_profile(args.profile)
    if scenario.get("hypothetical_accounts") and scenario.get("people"):
        return {"people": scenario["people"]}
    return planning.load_profile(planning.DEFAULT_PROFILE_PATH)


def _load_accounts_for(args, scenario: dict):
    """Returns (account_rows, allocation_rows, total) -- from
    scenario.hypothetical_accounts if configured (see pt/projection.py's
    build_hypothetical_accounts()), otherwise from the database snapshot,
    exactly mirroring pt.cli's project/monte-carlo commands so this tool
    can be tried with no real data the same way those can."""
    if scenario.get("hypothetical_accounts"):
        return projection_mod.build_hypothetical_accounts(scenario, _load_profile_for(args, scenario))
    conn = db_mod.get_connection(args.db)
    snapshot_id = args.snapshot or alloc.get_latest_snapshot_id(conn)
    if snapshot_id is None:
        print("No snapshots yet. Run 'python3 -m pt.cli import' first (or add "
              "scenario.hypothetical_accounts to try this without one -- see pt/README.md).",
              file=sys.stderr)
        sys.exit(1)
    total = alloc.household_total_value(conn, snapshot_id)
    allocation_rows, _ = alloc.allocation_by_asset_class(conn, snapshot_id)
    account_rows = alloc.account_values(conn, snapshot_id)
    return account_rows, allocation_rows, total


def _bucket_allocations_for(args, scenario: dict, profile: dict, inherited_schedules: list) -> dict:
    """Per-tax-bucket allocation for --per-bucket-growth (see
    projection.bucket_allocation_rows()) -- same rule as pt.cli's own
    _bucket_allocations_for() (see that function's docstring): a
    hypothetical scenario's own per-account asset_classes, or a real
    household's per-account classification from the database. Kept as
    its own copy here rather than a shared import, the same way
    _load_profile_for() above does."""
    if scenario.get("hypothetical_accounts"):
        class_rows = projection_mod.build_hypothetical_account_class_rows(scenario, profile)
    else:
        conn = db_mod.get_connection(args.db)
        snapshot_id = args.snapshot or alloc.get_latest_snapshot_id(conn)
        class_rows = alloc.allocation_by_account(conn, snapshot_id)
    return projection_mod.bucket_allocation_rows(class_rows, inherited_schedules, profile)


def _parse_heir_tax_rate(pct: float) -> float:
    if not (0 <= pct <= 100):
        raise SystemExit(f"--heir-tax-rate should be a percentage 0-100 (got {pct}).")
    return pct / 100


# The actual current federal marginal bracket rates -- see project()'s own
# max_marginal_bracket validation, which only accepts these same values.
# `sweep`'s default --heir-tax-rates list: every plausible "flat rate a
# future bracket might land at" a household might want to compare against,
# not a recommendation that any one of them is more likely than another.
DEFAULT_SWEEP_RATES = [10, 12, 22, 24, 32, 35, 37]


def _parse_rate_list(text: str) -> list:
    """'10,12,22' -> [10.0, 12.0, 22.0], validated the same way
    _parse_heir_tax_rate() validates a single rate. Whitespace around a
    value is ignored; empty tokens (a trailing comma, e.g.) are skipped."""
    rates = []
    for token in text.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            pct = float(token)
        except ValueError:
            raise SystemExit(f"--heir-tax-rates: '{token}' isn't a number.")
        if not (0 <= pct <= 100):
            raise SystemExit(f"--heir-tax-rates: {pct} isn't a percentage 0-100.")
        rates.append(pct)
    if not rates:
        raise SystemExit("--heir-tax-rates: no rates given.")
    return rates


def _rate_tag(pct: float) -> str:
    """Filename-safe tag for a percentage -- e.g. 24 -> '24', 22.5 ->
    '22p5' -- matches scripts/run_optimizer.sh's own naming convention,
    so `sweep --out-dir` and that script's single-rate output land on the
    same names."""
    return f"{pct:g}".replace(".", "p")


def _parse_claim_age_list(text: str) -> list:
    """'62,65,70' -> [62, 65, 70], each validated against social_security.
    MIN_CLAIM_AGE/MAX_CLAIM_AGE. Whitespace around a value is ignored;
    empty tokens (a trailing comma, e.g.) are skipped."""
    ages = []
    for token in text.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            age = int(token)
        except ValueError:
            raise SystemExit(f"--claim-ages: '{token}' isn't a whole number.")
        if not (social_security_mod.MIN_CLAIM_AGE <= age <= social_security_mod.MAX_CLAIM_AGE):
            raise SystemExit(
                f"--claim-ages: {age} isn't {social_security_mod.MIN_CLAIM_AGE}-"
                f"{social_security_mod.MAX_CLAIM_AGE}."
            )
        ages.append(age)
    if not ages:
        raise SystemExit("--claim-ages: no ages given.")
    return ages


def _parse_projection_age_list(text: str) -> list:
    """'85,90,95,100' -> [85, 90, 95, 100] -- ages to also value the
    optimized/current-plan/no-conversions schedules at, on top of the
    household's own life-expectancy row (see optimize()'s own
    projection_ages parameter). Whitespace around a value is ignored;
    empty tokens (a trailing comma, e.g.) are skipped. Not bounds-checked
    against anything (unlike --claim-ages) -- project() itself simply
    can't run past the primary person's age 100 (see optimizer.py's
    _project_without_mortality()), so an age past that just falls back
    to the age-100 row, same as estate_valuation_row()'s own
    out-of-range fallback; ordering/dedup happens in optimize() itself."""
    ages = []
    for token in text.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            age = int(token)
        except ValueError:
            raise SystemExit(f"--optimizer-projection-ages: '{token}' isn't a whole number.")
        if age <= 0:
            raise SystemExit(f"--optimizer-projection-ages: {age} isn't a positive age.")
        ages.append(age)
    if not ages:
        raise SystemExit("--optimizer-projection-ages: no ages given.")
    return ages


def _fmt(value) -> str:
    return "n/a" if value is None else f"${value:,.0f}"


_KIND_LABELS = {
    "roth_conversion": "Roth conversion",
    "ira_distribution": "IRA distribution",
    "inherited_withdrawal": "Inherited withdrawal",
}


def _window_label(w: dict) -> str:
    if w["kind"] == "inherited_withdrawal":
        return (f"{w['owner']} (Inherited withdrawal, account {w['account_number']}, "
                f"{w['start_year']}-{w['end_year']})")
    return f"{w['owner']} ({_KIND_LABELS[w['kind']]}, {w['start_year']}-{w['end_year']})"


def _scenario_stem(args) -> str:
    """The scenario file's own basename, minus its .yaml/.yml extension --
    used by `sweep`/`priority`'s --out-dir to name each variant's output
    files, matching scripts/run_optimizer.sh's own naming convention."""
    stem = Path(args.scenario or scenario_mod.DEFAULT_SCENARIO_PATH).name
    for suffix in (".yaml", ".yml"):
        if stem.endswith(suffix):
            return stem[: -len(suffix)]
    return stem


def _save_iteration_output(path: str, iteration_log: list, result: dict, heir_tax_rate: float,
                            scenario_label: str, seed, restarts: int) -> None:
    """Writes optimize()'s own iteration_log (see that function's
    docstring) to `path` -- one row per candidate schedule the pattern
    search evaluated (every restart, every trial, not just improving
    moves or each restart's own final result). Extension picks the
    format, the same convention build_parser()'s --out already uses
    (Excel vs text):
      .csv (or anything else): a 3-column CSV -- iteration (1-based, in
        evaluation order), lifetime_taxes, after_tax_estate_value.
      .html/.htm: a self-contained, dependency-free interactive scatter
        chart of the SAME data (see roth_optimizer/scatter_chart.py) --
        lifetime taxes paid vs. after-tax estate value, with the winning
        schedule and the zero-conversions/current-plan reference points
        (result's own "lifetime_taxes"/"value",
        "zero_conversions_lifetime_taxes"/"zero_conversions_value",
        "original_lifetime_taxes"/"original_value" -- see optimize()'s
        Returns docstring) marked directly on the plot, alongside the
        household's own age at the life-expectancy point
        (result["life_expectancy_age"]). If `optimize --optimizer-
        projection-ages` was given, each of those three points also gets
        a short dotted trajectory through the requested ages (result's
        own "age_projections" key -- see optimize()'s docstring).
    Either way, meant for the tradeoff the search explored -- e.g. the
    same kind of chart a Monte-Carlo-style Roth optimizer (like
    rothblueprint.com) shows -- NOT a substitute for `--out-scenario`/
    `--out` (neither format here has per-year schedule detail, just the
    two scored totals for each candidate)."""
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.suffix.lower() in (".html", ".htm"):
        original_point = None
        if result["original_value"] is not None:
            original_point = (result["original_lifetime_taxes"], result["original_value"])
        html = scatter_chart_mod.render_scatter_html(
            iteration_log,
            winner=(result["lifetime_taxes"], result["value"]),
            zero_point=(result["zero_conversions_lifetime_taxes"], result["zero_conversions_value"]),
            original_point=original_point,
            heir_tax_rate=heir_tax_rate,
            scenario_label=scenario_label,
            seed=seed,
            restarts=restarts,
            evaluations=len(iteration_log),
            winner_age=result["life_expectancy_age"],
            age_projections=result["age_projections"],
        )
        out_path.write_text(html, encoding="utf-8")
    else:
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["iteration", "lifetime_taxes", "after_tax_estate_value"])
            for i, entry in enumerate(iteration_log, start=1):
                writer.writerow([i, f"{entry['lifetime_taxes']:.2f}", f"{entry['after_tax_estate_value']:.2f}"])


def _save_optimizer_outputs(profile: dict, scenario: dict, account_rows: list, blended_rate: float,
                             bucket_rates, result: dict, out_scenario, out_paths,
                             scatter_chart_path=None) -> None:
    """Shared by cmd_optimize and cmd_sweep -- saves a finished optimize()
    result's --out-scenario/--out files (see build_parser()'s help text
    for what each does) and prints the same status lines either command
    already printed for these before this was factored out.

    scatter_chart_path (optional -- cmd_optimize passes the first
    --save-iterations *.html/*.htm it wrote): recorded in the saved
    --out-scenario file as a `scatter_chart` field on its first
    roth_conversions entry, as a path relative to that scenario file's own
    directory (so the two can be moved together), which is how `pt.cli
    report` knows to add an "Optimizer Search" tab -- see pt/report.py's
    _add_optimizer_search_sheet(). Ignored without --out-scenario, and
    when there's no roth_conversions entry to hang it on."""
    winning_scenario = optimizer_mod.apply_schedule(scenario, result["windows"], result["schedule"])
    if scatter_chart_path and out_scenario and winning_scenario.get("roth_conversions"):
        winning_scenario["roth_conversions"][0]["scatter_chart"] = os.path.relpath(
            Path(scatter_chart_path).resolve(), Path(out_scenario).resolve().parent
        )
    if out_scenario:
        out_path = Path(out_scenario)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(winning_scenario, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
        print(f"[Optimized scenario saved to {out_path} -- run `python3 -m pt.cli project --scenario "
              f"{out_path}` (or monte-carlo) to see it year by year]")
    for out in out_paths or []:
        rows = projection_mod.project(profile, winning_scenario, account_rows, blended_rate,
                                       bucket_rates=bucket_rates)
        text = projection_mod.format_scenario_config(winning_scenario) + projection_mod.format_table(
            rows, profile, blended_rate, bucket_rates=bucket_rates
        )
        out_path = Path(out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if out_path.suffix.lower() == ".xlsx":
            wb = report_mod.build_projection_workbook(rows, profile, blended_rate, scenario=winning_scenario,
                                                        bucket_rates=bucket_rates)
            wb.save(out_path)
            print(f"[Optimized projection workbook saved to {out_path}]")
        else:
            out_path.write_text(text)
            print(f"[Optimized projection saved to {out_path}]")


def cmd_optimize(args):
    try:
        scenario = scenario_mod.load_scenario(args.scenario or scenario_mod.DEFAULT_SCENARIO_PATH)
    except scenario_mod.ScenarioError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    try:
        profile = _load_profile_for(args, scenario)
    except planning.ProfileError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    account_rows, allocation_rows, total = _load_accounts_for(args, scenario)
    heir_tax_rate = _parse_heir_tax_rate(args.heir_tax_rate)
    # --optimizer-projection-ages only matters to the scatter chart
    # --save-iterations *.html writes -- skip parsing/computing it
    # entirely (no extra project() calls below) when --save-iterations
    # wasn't even given. Parsed up front, same as heir_tax_rate above,
    # so a bad age list fails before printing "Searching..." rather than
    # after.
    projection_ages = (_parse_projection_age_list(args.optimizer_projection_ages)
                        if args.save_iterations and args.optimizer_projection_ages else None)

    try:
        blended_rate = projection_mod.compute_blended_growth_rate(allocation_rows, total, scenario)
        bucket_rates = None
        if args.per_bucket_growth:
            inherited_schedules = projection_mod.build_inherited_schedules(scenario, profile)
            bucket_allocations = _bucket_allocations_for(args, scenario, profile, inherited_schedules)
            bucket_rates = {
                key: projection_mod.compute_blended_growth_rate(bucket_rows, bucket_total, scenario)
                for key, (bucket_rows, bucket_total) in bucket_allocations.items()
            }
        print(f"Searching (restarts={args.restarts}, seed={args.seed}, heir tax rate={args.heir_tax_rate:g}%"
              + (", per-bucket growth" if args.per_bucket_growth else "") + ") ...")
        if bucket_rates:
            print("Per-bucket growth rates (each tax bucket grows at its own blended rate during the search):")
            for key in sorted(bucket_rates, key=projection_mod.bucket_label):
                print(f"  {projection_mod.bucket_label(key)}: {bucket_rates[key] * 100:.2f}%")
        t0 = time.time()
        iteration_log = [] if args.save_iterations else None
        result = optimizer_mod.optimize(
            profile, scenario, account_rows, blended_rate, heir_tax_rate,
            upper_bound=args.upper_bound, min_step=args.min_step, restarts=args.restarts, seed=args.seed,
            bucket_rates=bucket_rates, include_ira_distributions=args.optimize_ira_distributions,
            include_inherited_withdrawals=args.optimize_inherited_withdrawals, iteration_log=iteration_log,
            projection_ages=projection_ages,
        )
        elapsed = time.time() - t0
    except (projection_mod.ProjectionError, optimizer_mod.OptimizerError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    zero_bits = ["Roth conversions"]
    if args.optimize_ira_distributions:
        zero_bits.append("IRA distributions")
    if args.optimize_inherited_withdrawals:
        zero_bits.append("extra inherited-account withdrawals")
    zero_label = f"No {' or '.join(zero_bits)} at all:"
    print(f"Done in {elapsed:.1f}s ({result['evaluations']:,} project() evaluations).\n")
    print("After-tax total estate value (see roth_optimizer/README.md for what this means):")
    print(f"  {zero_label} {_fmt(result['zero_conversions_value'])}")
    print(f"  This scenario's own configuration: {_fmt(result['original_value'])}"
          + ("" if result["original_value"] is not None else
             "  (no annual_amount/max_conversion_per_year configured -- nothing to compare)"))
    print(f"  Optimized schedule:                {_fmt(result['value'])}")
    if result["original_value"] is not None:
        print(f"  Improvement over this scenario's own configuration: "
              f"{_fmt(result['value'] - result['original_value'])}")
    print(f"  Improvement over no conversions at all:              "
          f"{_fmt(result['value'] - result['zero_conversions_value'])}")

    print("\nOptimized per-year schedule (what actually converts/distributes, after any "
          "max_marginal_bracket/avoid_irmaa_tier/preserve_cash_reserve constraint clamps it):")
    any_clamped = False
    for w, requested, actual in zip(result["windows"], result["schedule"], result["actual_schedule"]):
        print(f"  {_window_label(w)}:")
        for year, req, act in zip(range(w["start_year"], w["end_year"] + 1), requested, actual):
            if abs(req - act) > 0.5:
                print(f"    {year}: ${act:,.0f}  (requested ${req:,.0f} -- a dynamic constraint reduced it)")
                any_clamped = True
            else:
                print(f"    {year}: ${act:,.0f}")
    if any_clamped:
        print("\nSome years' requested amounts got reduced by a dynamic constraint (see roth_optimizer/"
              "README.md's \"The search\" section) -- the numbers above already reflect that; --heir-tax-rate's "
              "reported value is unaffected either way, since it's always scored against what actually happens.")

    for save_path in args.save_iterations or []:
        scenario_label = Path(args.scenario or scenario_mod.DEFAULT_SCENARIO_PATH).name
        _save_iteration_output(save_path, iteration_log, result, heir_tax_rate, scenario_label,
                                args.seed, args.restarts)
        kind = "chart" if Path(save_path).suffix.lower() in (".html", ".htm") else "CSV"
        print(f"[Every candidate schedule's lifetime taxes paid / after-tax estate value saved as a {kind} to "
              f"{save_path} ({len(iteration_log):,} rows)]")

    scatter_chart_path = next(
        (p for p in (args.save_iterations or []) if Path(p).suffix.lower() in (".html", ".htm")), None
    )
    print()
    _save_optimizer_outputs(profile, scenario, account_rows, blended_rate, bucket_rates, result,
                             args.out_scenario, args.out, scatter_chart_path=scatter_chart_path)


def cmd_sweep(args):
    """Runs `optimize` once per --heir-tax-rates value against the SAME
    scenario/profile/accounts (loaded once, not reloaded per rate), then
    prints every rate's numbers side by side -- deliberately WITHOUT
    picking a single "best" rate. --heir-tax-rate is an assumption (what
    tax rate whoever eventually inherits Traditional dollars pays), not a
    knob the search itself chooses -- a LOWER assumed rate always makes
    the raw dollar total bigger for ANY schedule (it's baked directly
    into after_tax_estate_value(): traditional_balance * (1 -
    heir_tax_rate)), so "the rate that scores highest" would trivially
    always be the lowest rate in the list and wouldn't mean anything.
    The Gain columns (vs. no conversions / vs. this scenario's own
    configuration) are the rate-comparable numbers -- see the table's own
    footer note."""
    try:
        scenario = scenario_mod.load_scenario(args.scenario or scenario_mod.DEFAULT_SCENARIO_PATH)
    except scenario_mod.ScenarioError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    try:
        profile = _load_profile_for(args, scenario)
    except planning.ProfileError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    account_rows, allocation_rows, total = _load_accounts_for(args, scenario)
    rates = _parse_rate_list(args.heir_tax_rates)
    scenario_stem = _scenario_stem(args)

    try:
        blended_rate = projection_mod.compute_blended_growth_rate(allocation_rows, total, scenario)
        bucket_rates = None
        if args.per_bucket_growth:
            inherited_schedules = projection_mod.build_inherited_schedules(scenario, profile)
            bucket_allocations = _bucket_allocations_for(args, scenario, profile, inherited_schedules)
            bucket_rates = {
                key: projection_mod.compute_blended_growth_rate(bucket_rows, bucket_total, scenario)
                for key, (bucket_rows, bucket_total) in bucket_allocations.items()
            }
    except projection_mod.ProjectionError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Sweeping {len(rates)} heir tax rate(s): {', '.join(f'{r:g}%' for r in rates)} "
          f"(restarts={args.restarts}, seed={args.seed}"
          + (", per-bucket growth" if args.per_bucket_growth else "") + ")")
    if bucket_rates:
        print("Per-bucket growth rates (each tax bucket grows at its own blended rate during the search):")
        for key in sorted(bucket_rates, key=projection_mod.bucket_label):
            print(f"  {projection_mod.bucket_label(key)}: {bucket_rates[key] * 100:.2f}%")
    print()

    rows_out = []
    for pct in rates:
        heir_tax_rate = pct / 100
        print(f"--- {pct:g}% ---")
        t0 = time.time()
        try:
            result = optimizer_mod.optimize(
                profile, scenario, account_rows, blended_rate, heir_tax_rate,
                upper_bound=args.upper_bound, min_step=args.min_step, restarts=args.restarts, seed=args.seed,
                bucket_rates=bucket_rates, include_ira_distributions=args.optimize_ira_distributions,
                include_inherited_withdrawals=args.optimize_inherited_withdrawals,
            )
        except optimizer_mod.OptimizerError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        elapsed = time.time() - t0
        print(f"Done in {elapsed:.1f}s ({result['evaluations']:,} project() evaluations). "
              f"Optimized: {_fmt(result['value'])}")

        if args.out_dir:
            out_dir = Path(args.out_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            tag = _rate_tag(pct)
            _save_optimizer_outputs(
                profile, scenario, account_rows, blended_rate, bucket_rates, result,
                out_scenario=out_dir / f"{scenario_stem}_optimized_{tag}.yaml",
                out_paths=[out_dir / f"{scenario_stem}_optimized_{tag}_projection.txt"],
            )
        print()

        gain_vs_own = (result["value"] - result["original_value"]
                       if result["original_value"] is not None else None)
        rows_out.append({
            "rate": pct,
            "zero": result["zero_conversions_value"],
            "own": result["original_value"],
            "optimized": result["value"],
            "gain_vs_own": gain_vs_own,
            "gain_vs_zero": result["value"] - result["zero_conversions_value"],
        })

    header = f"{'Rate':>6}  {'No conversions':>18}  {'Own config':>18}  {'Optimized':>18}  " \
             f"{'Gain vs own config':>19}  {'Gain vs no conv.':>17}"
    print("=" * len(header))
    print(f"Heir tax rate sweep -- {args.scenario or scenario_mod.DEFAULT_SCENARIO_PATH}")
    print("=" * len(header))
    print(header)
    for r in rows_out:
        print(f"{r['rate']:>5g}%  {_fmt(r['zero']):>18}  {_fmt(r['own']):>18}  {_fmt(r['optimized']):>18}  "
              f"{_fmt(r['gain_vs_own']):>19}  {_fmt(r['gain_vs_zero']):>17}")
    print()
    print("Note: --heir-tax-rate is an ASSUMPTION (what tax rate whoever eventually inherits/withdraws\n"
          "Traditional dollars pays), not a knob the search picks for you -- a LOWER assumed rate always\n"
          "makes every dollar column above bigger for ANY schedule (traditional_balance * (1 -\n"
          "heir_tax_rate) is baked directly into the objective), so there's no single \"best\" row --\n"
          "compare the Gain columns instead, which show how much value the SEARCH ITSELF (vs. this\n"
          "scenario's own configuration) or ANY Roth conversions (vs. none) add under each assumption.")


def cmd_priority(args):
    """Runs `optimize` twice against the SAME scenario/profile/accounts
    (loaded once) -- once with each owner's roth_conversions entries
    reordered to go first (see optimizer.reorder_by_owner_priority()) --
    and reports which ordering wins. Unlike `sweep`'s --heir-tax-rate,
    reordering does NOT mechanically bias the objective one way or the
    other, so declaring a winner here is meaningful, not a trap."""
    try:
        scenario = scenario_mod.load_scenario(args.scenario or scenario_mod.DEFAULT_SCENARIO_PATH)
    except scenario_mod.ScenarioError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    try:
        profile = _load_profile_for(args, scenario)
    except planning.ProfileError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    owners = optimizer_mod.conversion_owners(scenario)
    if len(owners) != 2:
        print(f"Error: `priority` needs scenario.roth_conversions to have entries from exactly two "
              f"different owners to compare orderings for -- found {len(owners)}: {owners or '(none)'}.",
              file=sys.stderr)
        sys.exit(1)

    account_rows, allocation_rows, total = _load_accounts_for(args, scenario)
    heir_tax_rate = _parse_heir_tax_rate(args.heir_tax_rate)
    scenario_stem = _scenario_stem(args)

    try:
        blended_rate = projection_mod.compute_blended_growth_rate(allocation_rows, total, scenario)
        bucket_rates = None
        if args.per_bucket_growth:
            inherited_schedules = projection_mod.build_inherited_schedules(scenario, profile)
            bucket_allocations = _bucket_allocations_for(args, scenario, profile, inherited_schedules)
            bucket_rates = {
                key: projection_mod.compute_blended_growth_rate(bucket_rows, bucket_total, scenario)
                for key, (bucket_rows, bucket_total) in bucket_allocations.items()
            }
    except projection_mod.ProjectionError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Comparing conversion priority between {owners[0]} and {owners[1]} "
          f"(restarts={args.restarts}, seed={args.seed}, heir tax rate={args.heir_tax_rate:g}%"
          + (", per-bucket growth" if args.per_bucket_growth else "") + ")")
    if bucket_rates:
        print("Per-bucket growth rates (each tax bucket grows at its own blended rate during the search):")
        for key in sorted(bucket_rates, key=projection_mod.bucket_label):
            print(f"  {projection_mod.bucket_label(key)}: {bucket_rates[key] * 100:.2f}%")
    print()

    values = {}
    for first_owner in owners:
        variant_scenario = optimizer_mod.reorder_by_owner_priority(scenario, first_owner)
        print(f"--- {first_owner} prioritized ---")
        t0 = time.time()
        try:
            result = optimizer_mod.optimize(
                profile, variant_scenario, account_rows, blended_rate, heir_tax_rate,
                upper_bound=args.upper_bound, min_step=args.min_step, restarts=args.restarts, seed=args.seed,
                bucket_rates=bucket_rates, include_ira_distributions=args.optimize_ira_distributions,
                include_inherited_withdrawals=args.optimize_inherited_withdrawals,
            )
        except optimizer_mod.OptimizerError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        elapsed = time.time() - t0
        print(f"Done in {elapsed:.1f}s ({result['evaluations']:,} project() evaluations). "
              f"Optimized: {_fmt(result['value'])}")

        if args.out_dir:
            out_dir = Path(args.out_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            _save_optimizer_outputs(
                profile, variant_scenario, account_rows, blended_rate, bucket_rates, result,
                out_scenario=out_dir / f"{scenario_stem}_priority_{first_owner}first.yaml",
                out_paths=[out_dir / f"{scenario_stem}_priority_{first_owner}first_projection.txt"],
            )
        print()
        values[first_owner] = result["value"]

    header = f"Conversion priority comparison -- {args.scenario or scenario_mod.DEFAULT_SCENARIO_PATH}"
    print("=" * len(header))
    print(header)
    print("=" * len(header))
    print("After-tax total estate value:")
    for owner in owners:
        print(f"  {owner} prioritized: {_fmt(values[owner])}")
    diff = values[owners[0]] - values[owners[1]]
    if abs(diff) < 0.5:
        print("\nNo meaningful difference between orderings for this scenario -- the two owners' "
              "constraints/balances aren't actually competing for the same shared room here.")
    else:
        winner, loser = (owners[0], owners[1]) if diff > 0 else (owners[1], owners[0])
        print(f"\n{winner} prioritized wins by {_fmt(abs(diff))} over {loser} prioritized.")


def cmd_claim_age(args):
    """Brute-force grid search over Social Security claim_age -- see
    optimizer.search_claim_ages()'s docstring for the algorithm and its
    scope boundary (doesn't jointly re-run the dollar-amount search).
    Unlike `sweep`'s --heir-tax-rate, claim_age is a real decision the
    household makes, not an assumption baked into the objective -- so
    declaring a winner here is meaningful."""
    try:
        scenario = scenario_mod.load_scenario(args.scenario or scenario_mod.DEFAULT_SCENARIO_PATH)
    except scenario_mod.ScenarioError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    try:
        profile = _load_profile_for(args, scenario)
    except planning.ProfileError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    account_rows, allocation_rows, total = _load_accounts_for(args, scenario)
    heir_tax_rate = _parse_heir_tax_rate(args.heir_tax_rate)
    claim_ages_to_try = _parse_claim_age_list(args.claim_ages) if args.claim_ages else None

    try:
        blended_rate = projection_mod.compute_blended_growth_rate(allocation_rows, total, scenario)
        bucket_rates = None
        if args.per_bucket_growth:
            inherited_schedules = projection_mod.build_inherited_schedules(scenario, profile)
            bucket_allocations = _bucket_allocations_for(args, scenario, profile, inherited_schedules)
            bucket_rates = {
                key: projection_mod.compute_blended_growth_rate(bucket_rows, bucket_total, scenario)
                for key, (bucket_rows, bucket_total) in bucket_allocations.items()
            }
        t0 = time.time()
        result = optimizer_mod.search_claim_ages(
            profile, scenario, account_rows, blended_rate, heir_tax_rate,
            candidate_ages=claim_ages_to_try, bucket_rates=bucket_rates,
        )
        elapsed = time.time() - t0
    except (projection_mod.ProjectionError, optimizer_mod.OptimizerError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    names = result["names"]
    print(f"Searching {len(result['results'])} claim-age combination(s) for {', '.join(names)} "
          f"(heir tax rate={args.heir_tax_rate:g}%"
          + (", per-bucket growth" if args.per_bucket_growth else "") + f") ... done in {elapsed:.1f}s")
    if optimizer_mod.life_expectancy_end_year(scenario, profile) is None:
        print("\nNOTE: no retirement.life_expectancy (or spouse_life_expectancy) configured -- this "
              "search scores against an unbounded lifetime, so claiming LATER (more total guaranteed "
              "dollars over a longer assumed lifetime) will almost always win. This is a real property "
              "of an unbounded-lifetime comparison, not a bug -- set retirement.life_expectancy (and "
              "spouse_life_expectancy, for a couple) to the age(s) you actually want this scored "
              "against, and re-run.")
    print()

    print(f"Best: {result['best_claim_ages']} -> {_fmt(result['best_value'])}")
    print()

    if len(names) == 1:
        name = names[0]
        print(f"{name}'s claim age:")
        for r in sorted(result["results"], key=lambda r: r["claim_ages"][name]):
            print(f"  {r['claim_ages'][name]}: {_fmt(r['value'])}")
    elif len(names) == 2:
        a, b = names
        ages_a = sorted({r["claim_ages"][a] for r in result["results"]})
        ages_b = sorted({r["claim_ages"][b] for r in result["results"]})
        by_combo = {(r["claim_ages"][a], r["claim_ages"][b]): r["value"] for r in result["results"]}
        header = f"{a:>8}\\{b:<8}" + "".join(f"{age:>13}" for age in ages_b)
        print(header)
        for age_a in ages_a:
            print(f"{age_a:>16} " + "".join(f"{_fmt(by_combo[(age_a, age_b)]):>13}" for age_b in ages_b))
    else:
        print(f"Top 10 of {len(result['results'])} combinations:")
        for r in sorted(result["results"], key=lambda r: -r["value"])[:10]:
            print(f"  {r['claim_ages']}: {_fmt(r['value'])}")

    if args.out_scenario or args.out:
        winning_scenario = optimizer_mod.apply_claim_ages(scenario, result["best_claim_ages"])
        print()
        if args.out_scenario:
            out_path = Path(args.out_scenario)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with open(out_path, "w", encoding="utf-8") as f:
                yaml.safe_dump(winning_scenario, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
            print(f"[Best claim-age scenario saved to {out_path} -- run `python3 -m pt.cli project "
                  f"--scenario {out_path}` (or monte-carlo) to see it year by year]")
        for out in args.out or []:
            rows = projection_mod.project(profile, winning_scenario, account_rows, blended_rate,
                                           bucket_rates=bucket_rates)
            text = projection_mod.format_scenario_config(winning_scenario) + projection_mod.format_table(
                rows, profile, blended_rate, bucket_rates=bucket_rates
            )
            out_path = Path(out)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            if out_path.suffix.lower() == ".xlsx":
                wb = report_mod.build_projection_workbook(rows, profile, blended_rate, scenario=winning_scenario,
                                                            bucket_rates=bucket_rates)
                wb.save(out_path)
                print(f"[Best claim-age projection workbook saved to {out_path}]")
            else:
                out_path.write_text(text)
                print(f"[Best claim-age projection saved to {out_path}]")


def build_parser():
    p = argparse.ArgumentParser(
        prog="roth_optimizer",
        description="Multi-year Roth conversion optimizer -- a companion tool to pt",
    )
    p.add_argument("--db", default=db_mod.DEFAULT_DB_PATH, help="Path to SQLite database file")
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser(
        "optimize",
        help="Search scenario.roth_conversions' whole schedule (not just one year at a time) to maximize "
             "an after-tax total estate value objective",
    )
    sp.add_argument("--snapshot", type=int, default=None, help="Snapshot id (default: latest)")
    sp.add_argument("--profile", default=None,
                     help=f"Path to the planning profile YAML (default: {planning.DEFAULT_PROFILE_PATH})")
    sp.add_argument("--scenario", default=None,
                     help=f"Path to the retirement scenario YAML (default: {scenario_mod.DEFAULT_SCENARIO_PATH}) "
                          "-- must have at least one scenario.roth_conversions entry (owner/start_date/end_date; "
                          "annual_amount isn't needed, constraints are kept as caps)")
    sp.add_argument("--heir-tax-rate", type=float, required=True,
                     help="Assumed flat tax rate (percentage, e.g. 24 for 24%%) on Traditional dollars whoever "
                          "eventually inherits/withdraws them pays -- see README.md for why this number matters")
    sp.add_argument("--restarts", type=int, default=3,
                     help="Number of search starting points to try, keeping the best (default: 3)")
    sp.add_argument("--min-step", type=float, default=1000.0,
                     help="Stop refining once nudging a year's amount by less than this many dollars "
                          "(default: 1000)")
    sp.add_argument("--upper-bound", type=float, default=None,
                     help="Largest single-year conversion amount the search considers (default: the "
                          "household's current total account value)")
    sp.add_argument("--seed", type=int, default=None,
                     help="Random seed for a reproducible search (default: different each run)")
    sp.add_argument("--out-scenario", default=None,
                     help="Save a full copy of the scenario with roth_conversions replaced by the winning "
                          "schedule to this YAML path, ready to run through `pt.cli project`/`monte-carlo`")
    sp.add_argument("--out", action="append", default=None,
                     help="Also save the winning schedule's full year-by-year projection to this path -- "
                          "Excel if it ends in .xlsx, otherwise text. Repeatable.")
    sp.add_argument("--save-iterations", action="append", default=None,
                     help="Save every candidate schedule the pattern search evaluated (every restart, "
                          "every trial -- not just the winner), one row/point per evaluation: "
                          "iteration, lifetime_taxes, after_tax_estate_value. .csv saves a plain CSV; "
                          ".html/.htm instead saves a self-contained interactive scatter chart of the "
                          "same data (lifetime taxes paid vs. after-tax estate value, winner and "
                          "zero-conversions/current-plan reference points marked) -- see "
                          "roth_optimizer/README.md and roth_optimizer/scatter_chart.py. Repeatable -- "
                          "e.g. pass both a .csv and a .html path to get both from the same search "
                          "(the search itself only runs once either way).")
    sp.add_argument("--optimizer-projection-ages", default=None,
                     help="Comma-separated ages (e.g. '85,90,95,100') to ALSO value the optimized/"
                          "current-plan/no-conversions schedules at, alongside the household's own "
                          "life-expectancy row -- only affects --save-iterations *.html (ignored "
                          "otherwise, and ignored entirely if --save-iterations isn't given). Each "
                          "schedule's own trajectory through these ages is drawn as a short dotted line "
                          "with a small hollow marker per age (hover for exact numbers). Omit for the "
                          "plain two-reference-point chart (default) -- a far-out age like 100 can "
                          "stretch the chart's own axis scale enough to compress the main cloud of "
                          "candidate schedules into a corner, so start with just the ages you actually "
                          "care about rather than reaching straight for 100.")
    sp.add_argument("--per-bucket-growth", action="store_true",
                     help="Grow each tax bucket (Taxable, each Traditional owner/type pool, each Roth "
                          "owner, each Inherited account) at its OWN blended rate during the search "
                          "(and in any --out projection), instead of one household-wide rate applied to "
                          "every bucket (default). Opt-in, same as pt.cli project/monte-carlo/report's "
                          "own --per-bucket-growth -- see README.md's 'Per-bucket growth' section.")
    sp.add_argument("--optimize-ira-distributions", action="store_true",
                     help="Also search scenario.ira_distributions windows, JOINTLY with roth_conversions "
                          "in the same search (not two separate optimizations) -- lets the search decide, "
                          "dollar for dollar, whether to convert to Roth or take a plain taxable IRA "
                          "distribution instead. Opt-in -- default behavior (roth_conversions only) is "
                          "unchanged. Needs at least one scenario.ira_distributions {owner, start_date, "
                          "end_date} window -- see 'Accelerated IRA drawdown' in README.md.")
    sp.add_argument("--optimize-inherited-withdrawals", action="store_true",
                     help="Also search each already-configured scenario.inherited_accounts "
                          "planned_withdrawal window -- how much EXTRA, above that account's own "
                          "legally-required 10-year-rule minimum, to withdraw each year (e.g. to smooth "
                          "tax brackets across the window instead of a bracket-jumping final-year lump "
                          "sum). Opt-in. Needs a REAL (not hypothetical) inherited_accounts entry with an "
                          "existing planned_withdrawal {start_date, end_date, annual_amount} window -- see "
                          "'Inherited account drawdown sequence' in README.md.")
    sp.set_defaults(func=cmd_optimize)

    sp = sub.add_parser(
        "sweep",
        help="Run `optimize` once per --heir-tax-rates value (same scenario/profile/accounts, loaded once) "
             "and print every rate's numbers side by side -- see cmd_sweep()'s docstring for why this "
             "deliberately doesn't pick a single 'best' rate",
    )
    sp.add_argument("--snapshot", type=int, default=None, help="Snapshot id (default: latest)")
    sp.add_argument("--profile", default=None,
                     help=f"Path to the planning profile YAML (default: {planning.DEFAULT_PROFILE_PATH})")
    sp.add_argument("--scenario", default=None,
                     help=f"Path to the retirement scenario YAML (default: {scenario_mod.DEFAULT_SCENARIO_PATH}) "
                          "-- must have at least one scenario.roth_conversions entry (owner/start_date/end_date; "
                          "annual_amount isn't needed, constraints are kept as caps)")
    sp.add_argument("--heir-tax-rates", default=",".join(str(r) for r in DEFAULT_SWEEP_RATES),
                     help="Comma-separated list of assumed flat tax rates (percentages) to compare -- see "
                          f"--heir-tax-rate's own help on 'optimize'. Default: the current federal marginal "
                          f"bracket rates ({', '.join(str(r) for r in DEFAULT_SWEEP_RATES)})")
    sp.add_argument("--restarts", type=int, default=3,
                     help="Number of search starting points to try per rate, keeping the best (default: 3)")
    sp.add_argument("--min-step", type=float, default=1000.0,
                     help="Stop refining once nudging a year's amount by less than this many dollars "
                          "(default: 1000)")
    sp.add_argument("--upper-bound", type=float, default=None,
                     help="Largest single-year conversion amount the search considers (default: the "
                          "household's current total account value)")
    sp.add_argument("--seed", type=int, default=None,
                     help="Random seed, reused for every rate in the sweep (default: different each run)")
    sp.add_argument("--out-dir", default=None,
                     help="Save each rate's winning schedule as a scenario YAML plus its projection text, "
                          "named <scenario>_optimized_<rate>.yaml / _projection.txt in this directory -- "
                          "same naming as scripts/run_optimizer.sh's single-rate output. Omit to only "
                          "print the comparison table.")
    sp.add_argument("--per-bucket-growth", action="store_true",
                     help="Grow each tax bucket at its OWN blended rate during every rate's search -- see "
                          "'optimize's own --per-bucket-growth help.")
    sp.add_argument("--optimize-ira-distributions", action="store_true",
                     help="Also search scenario.ira_distributions windows, jointly with roth_conversions, "
                          "at every rate -- see 'optimize's own --optimize-ira-distributions help.")
    sp.add_argument("--optimize-inherited-withdrawals", action="store_true",
                     help="Also search each already-configured inherited_accounts planned_withdrawal "
                          "window, at every rate -- see 'optimize's own --optimize-inherited-withdrawals "
                          "help.")
    sp.set_defaults(func=cmd_sweep)

    sp = sub.add_parser(
        "priority",
        help="Run `optimize` twice -- once with each owner's roth_conversions entries prioritized -- and "
             "report which ordering wins (see cmd_priority()'s docstring for why order matters at all)",
    )
    sp.add_argument("--snapshot", type=int, default=None, help="Snapshot id (default: latest)")
    sp.add_argument("--profile", default=None,
                     help=f"Path to the planning profile YAML (default: {planning.DEFAULT_PROFILE_PATH})")
    sp.add_argument("--scenario", default=None,
                     help=f"Path to the retirement scenario YAML (default: {scenario_mod.DEFAULT_SCENARIO_PATH}) "
                          "-- must have scenario.roth_conversions entries from exactly two different owners")
    sp.add_argument("--heir-tax-rate", type=float, required=True,
                     help="Assumed flat tax rate (percentage, e.g. 24 for 24%%) on Traditional dollars whoever "
                          "eventually inherits/withdraws them pays -- see 'optimize's own help")
    sp.add_argument("--restarts", type=int, default=3,
                     help="Number of search starting points to try per ordering, keeping the best (default: 3)")
    sp.add_argument("--min-step", type=float, default=1000.0,
                     help="Stop refining once nudging a year's amount by less than this many dollars "
                          "(default: 1000)")
    sp.add_argument("--upper-bound", type=float, default=None,
                     help="Largest single-year conversion amount the search considers (default: the "
                          "household's current total account value)")
    sp.add_argument("--seed", type=int, default=None,
                     help="Random seed, reused for both orderings (default: different each run)")
    sp.add_argument("--out-dir", default=None,
                     help="Save each ordering's winning schedule as a scenario YAML plus its projection "
                          "text, named <scenario>_priority_<owner>first.yaml / _projection.txt in this "
                          "directory. Omit to only print the comparison.")
    sp.add_argument("--per-bucket-growth", action="store_true",
                     help="Grow each tax bucket at its OWN blended rate during each ordering's search -- "
                          "see 'optimize's own --per-bucket-growth help.")
    sp.add_argument("--optimize-ira-distributions", action="store_true",
                     help="Also search scenario.ira_distributions windows, jointly with roth_conversions, "
                          "for each ordering -- see 'optimize's own --optimize-ira-distributions help. NOTE: "
                          "reordering only affects roth_conversions' priority; ira_distributions entries "
                          "keep their own scenario order regardless of which owner is prioritized.")
    sp.add_argument("--optimize-inherited-withdrawals", action="store_true",
                     help="Also search each already-configured inherited_accounts planned_withdrawal "
                          "window, for each ordering -- see 'optimize's own "
                          "--optimize-inherited-withdrawals help. Unaffected by which owner is "
                          "prioritized (that only reorders roth_conversions).")
    sp.set_defaults(func=cmd_priority)

    sp = sub.add_parser(
        "claim-age",
        help="Brute-force grid search over Social Security claim_age (see cmd_claim_age()'s docstring) "
             "-- unlike sweep's --heir-tax-rate, claim_age is a real decision, so this DOES declare a "
             "winner",
    )
    sp.add_argument("--snapshot", type=int, default=None, help="Snapshot id (default: latest)")
    sp.add_argument("--profile", default=None,
                     help=f"Path to the planning profile YAML (default: {planning.DEFAULT_PROFILE_PATH})")
    sp.add_argument("--scenario", default=None,
                     help=f"Path to the retirement scenario YAML (default: {scenario_mod.DEFAULT_SCENARIO_PATH}) "
                          "-- must have at least one scenario.social_security entry with pia_monthly "
                          "configured (a flat monthly_benefit can't be recomputed for a different claim "
                          "age)")
    sp.add_argument("--heir-tax-rate", type=float, required=True,
                     help="Assumed flat tax rate (percentage, e.g. 24 for 24%%) on Traditional dollars whoever "
                          "eventually inherits/withdraws them pays -- see 'optimize's own help")
    sp.add_argument("--claim-ages", default=None,
                     help="Comma-separated list of whole-year ages to try for EVERY candidate name "
                          f"(default: every age {social_security_mod.MIN_CLAIM_AGE}-"
                          f"{social_security_mod.MAX_CLAIM_AGE})")
    sp.add_argument("--out-scenario", default=None,
                     help="Save a full copy of the scenario with the winning claim_age(s) applied to "
                          "this YAML path, ready to run through `pt.cli project`/`monte-carlo`")
    sp.add_argument("--out", action="append", default=None,
                     help="Also save the winning claim-age combination's full year-by-year projection to "
                          "this path -- Excel if it ends in .xlsx, otherwise text. Repeatable.")
    sp.add_argument("--per-bucket-growth", action="store_true",
                     help="Grow each tax bucket at its OWN blended rate for every combination evaluated -- "
                          "see 'optimize's own --per-bucket-growth help.")
    sp.set_defaults(func=cmd_claim_age)

    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
