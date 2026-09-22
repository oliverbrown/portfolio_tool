"""
Command-line interface for the scenario sweep engine -- a companion tool
to pt (see pt/README.md), run the same way (`python3 -m
scenario_engine.cli ...` from the repo root, alongside `python3 -m
pt.cli ...`) but a separate package/entry point.

Typical workflow:
    python3 -m scenario_engine.cli generate --sweep my_sweep.yaml --out-dir scenarios/
    python3 -m scenario_engine.cli run --scenarios-dir scenarios/ --out-dir results/

Or, to search for the best of a set of parameters (per combination of a
separate set of "context" parameters) instead of generating/running
every combination:
    python3 -m scenario_engine.cli optimize --sweep my_sweep.yaml --out-dir optimized/

See README.md in this directory for the sweep config's syntax (including
optimize's) and the full walkthrough.
"""
import argparse
import csv
import sys
from pathlib import Path

import yaml

from pt import allocation as alloc
from pt import db as db_mod
from pt import planning
from pt import projection as projection_mod
from pt import report as report_mod
from pt import scenario as scenario_mod

from . import sweep as sweep_mod


def _dedupe_filename(filename: str, seen: set) -> str:
    """filename, with a __2/__3/... suffix inserted before the extension
    if it's already in `seen` -- a final safety net against two
    different combinations landing on the same scenario_filename()
    (only plausible in an extreme, many-parameter case where per-label
    truncation kicks in -- see sweep.py's scenario_filename()), so two
    results can never silently overwrite each other. Mutates `seen`."""
    if filename not in seen:
        seen.add(filename)
        return filename
    stem, ext = filename.rsplit(".", 1)
    n = 2
    while f"{stem}__{n}.{ext}" in seen:
        n += 1
    deduped = f"{stem}__{n}.{ext}"
    seen.add(deduped)
    return deduped


def cmd_generate(args):
    try:
        config = sweep_mod.load_sweep_config(args.sweep)
    except sweep_mod.SweepError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    # base_scenario in the sweep config is relative to the sweep config's
    # OWN location (not the current working directory), so a sweep config
    # can sit next to its base scenario and be run from anywhere.
    base_path = Path(config["base_scenario"])
    if not base_path.is_absolute():
        base_path = Path(args.sweep).resolve().parent / base_path
    try:
        base_scenario = scenario_mod.load_scenario(base_path)
    except scenario_mod.ScenarioError as e:
        print(f"Error loading base_scenario ({base_path}): {e}", file=sys.stderr)
        sys.exit(1)

    try:
        combos = sweep_mod.build_matrix(base_scenario, config["parameters"], max_combinations=args.max_combinations)
    except sweep_mod.SweepError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    base_stem = base_path.stem
    param_paths = sorted(config["parameters"])

    print(f"Generating {len(combos)} scenario(s) from {base_path.name} into {out_dir}/ ...")
    manifest_rows = []
    seen_filenames = set()
    for overrides, scenario in combos:
        filename = _dedupe_filename(sweep_mod.scenario_filename(base_stem, overrides), seen_filenames)
        out_path = out_dir / filename
        with open(out_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(scenario, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
        manifest_rows.append({"filename": filename, **overrides})
        print(f"  {filename}")

    manifest_path = out_dir / "manifest.csv"
    with open(manifest_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["filename"] + param_paths)
        writer.writeheader()
        writer.writerows(manifest_rows)

    print(f"\nGenerated {len(combos)} scenario(s) in {out_dir}/")
    print(f"Manifest (filename -> swept parameter values): {manifest_path}")


def cmd_run(args):
    conn = db_mod.get_connection(args.db)
    snapshot_id = args.snapshot or alloc.get_latest_snapshot_id(conn)
    if snapshot_id is None:
        print("No snapshots yet. Run 'python3 -m pt.cli import' first.", file=sys.stderr)
        sys.exit(1)

    try:
        profile = planning.load_profile(args.profile or planning.DEFAULT_PROFILE_PATH)
    except planning.ProfileError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    scenarios_dir = Path(args.scenarios_dir)
    scenario_paths = sorted(set(scenarios_dir.glob("*.yaml")) | set(scenarios_dir.glob("*.yml")))
    if not scenario_paths:
        print(f"No .yaml/.yml scenario files found in {scenarios_dir}.", file=sys.stderr)
        sys.exit(1)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    total = alloc.household_total_value(conn, snapshot_id)
    allocation_rows, _ = alloc.allocation_by_asset_class(conn, snapshot_id)
    account_rows = alloc.account_values(conn, snapshot_id)

    print(f"Running {len(scenario_paths)} scenario(s) from {scenarios_dir}/ into {out_dir}/ "
          f"(format: {args.format}{', verbose' if args.verbose else ''}) ...")
    summary_rows = []
    for path in scenario_paths:
        print(f"  {path.name} ...", end=" ", flush=True)
        try:
            scenario = scenario_mod.load_scenario(path)
        except scenario_mod.ScenarioError as e:
            print(f"SKIPPED ({e})")
            continue
        # --verbose: same per-account columns as `pt.cli project --verbose`
        # -- built per scenario (not once up front), since a swept
        # inherited_accounts config could in principle vary account
        # structure between scenarios, same as pt.cli's own cmd_project().
        account_columns = projection_mod.build_account_columns(
            account_rows, projection_mod.build_inherited_schedules(scenario, profile), profile
        ) if args.verbose else None
        try:
            blended_rate = projection_mod.compute_blended_growth_rate(allocation_rows, total, scenario)
            rows = projection_mod.project(profile, scenario, account_rows, blended_rate=blended_rate,
                                           account_columns=account_columns)
        except projection_mod.ProjectionError as e:
            print(f"SKIPPED ({e})")
            continue

        stem = path.stem
        if args.format in ("txt", "both"):
            text = projection_mod.format_scenario_config(scenario) + projection_mod.format_table(
                rows, profile, blended_rate, account_columns=account_columns
            )
            (out_dir / f"{stem}.txt").write_text(text)
        if args.format in ("xlsx", "both"):
            wb = report_mod.build_projection_workbook(rows, profile, blended_rate, account_columns=account_columns,
                                                        scenario=scenario)
            wb.save(out_dir / f"{stem}.xlsx")

        outcome = projection_mod.summarize_outcome(rows, profile)
        summary_rows.append({"scenario": stem, **outcome})
        print(outcome["outcome"])

    if not summary_rows:
        print("\nNothing ran successfully.")
        return

    summary_path = out_dir / "summary.csv"
    fieldnames = ["scenario", "outcome", "first_shortfall_year", "final_year", "final_total_balance"]
    with open(summary_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in summary_rows:
            writer.writerow({k: row.get(k) for k in fieldnames})

    print(f"\n{'Scenario':<70} {'Outcome':<10} {'Final Year':>10} {'Final Total Balance':>20}")
    print("-" * 114)
    for row in sorted(summary_rows, key=lambda r: r["final_total_balance"], reverse=True):
        print(f"{row['scenario']:<70} {row['outcome']:<10} {row['final_year']:>10} "
              f"${row['final_total_balance']:>18,.0f}")
    print(f"\nSummary saved to {summary_path}")


def _hits_boundary(value, spec: dict) -> bool:
    """True if value landed on spec's own min or max -- a signal the true
    optimum might lie outside the range actually searched."""
    return abs(value - spec["min"]) < 1e-9 or abs(value - spec["max"]) < 1e-9


def cmd_optimize(args):
    try:
        config = sweep_mod.load_sweep_config(args.sweep)
    except sweep_mod.SweepError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    if not config.get("optimize"):
        print(f"Error: {args.sweep} has no 'optimize' section -- see scenario_engine/README.md.", file=sys.stderr)
        sys.exit(1)

    base_path = Path(config["base_scenario"])
    if not base_path.is_absolute():
        base_path = Path(args.sweep).resolve().parent / base_path
    try:
        base_scenario = scenario_mod.load_scenario(base_path)
    except scenario_mod.ScenarioError as e:
        print(f"Error loading base_scenario ({base_path}): {e}", file=sys.stderr)
        sys.exit(1)

    outer_params = config.get("parameters") or {}
    inner_params = config["optimize"].get("parameters") or {}
    if not inner_params:
        print(f"Error: {args.sweep}'s optimize section has no 'parameters' to search over.", file=sys.stderr)
        sys.exit(1)

    try:
        terms = sweep_mod.parse_objective(config)
        outer_count = sweep_mod.combination_count(base_scenario, outer_params, "parameters")
        inner_count = sweep_mod.combination_count(base_scenario, inner_params, "optimize.parameters")
    except sweep_mod.SweepError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    total = outer_count * inner_count
    if total > args.max_combinations:
        print(
            f"Error: this search would run {total} total projections ({outer_count} outer x {inner_count} "
            f"inner), over the {args.max_combinations} safety limit -- narrow the ranges/steps, or pass "
            "--max-combinations to raise the limit if you really want this many.",
            file=sys.stderr,
        )
        sys.exit(1)

    conn = db_mod.get_connection(args.db)
    snapshot_id = args.snapshot or alloc.get_latest_snapshot_id(conn)
    if snapshot_id is None:
        print("No snapshots yet. Run 'python3 -m pt.cli import' first.", file=sys.stderr)
        sys.exit(1)
    try:
        profile = planning.load_profile(args.profile or planning.DEFAULT_PROFILE_PATH)
    except planning.ProfileError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    household_total = alloc.household_total_value(conn, snapshot_id)
    allocation_rows, _ = alloc.allocation_by_asset_class(conn, snapshot_id)
    account_rows = alloc.account_values(conn, snapshot_id)

    # Fail fast on a typo'd objective field name (or a base scenario that
    # can't even project) BEFORE committing to the full search -- every
    # candidate's row has the same shape, so one check up front is enough;
    # the tight loop below doesn't need to repeat it.
    try:
        test_rate = projection_mod.compute_blended_growth_rate(allocation_rows, household_total, base_scenario)
        test_rows = projection_mod.project(profile, base_scenario, account_rows, blended_rate=test_rate)
        sweep_mod.objective_value(test_rows[-1], terms)
    except projection_mod.ProjectionError as e:
        print(f"Error: the base scenario itself failed to project: {e}", file=sys.stderr)
        sys.exit(1)
    except sweep_mod.SweepError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    outer_combos = sweep_mod.build_matrix(base_scenario, outer_params, max_combinations=args.max_combinations,
                                           section_label="parameters")
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    base_stem = base_path.stem
    outer_paths = sorted(outer_params)
    inner_paths = sorted(inner_params)
    objective_label = sweep_mod.format_objective(terms)
    # Candidates are always compared/picked on the raw (always-minimize)
    # composite score internally -- but a PURE maximize_sum_of objective
    # (no minimize_sum_of at all) would otherwise show/log that score
    # negated (e.g. "-23,395,685" for a $23M total_balance), which reads
    # like an error rather than a large positive balance. Whenever at
    # least one minimize_sum_of field is present, the raw composite
    # already reads naturally (it's a net-cost figure: minimized fields
    # minus maximized ones), so only the pure-maximize case needs
    # flipping back for anything shown to a human -- comparisons/best-
    # tracking below always use the raw, un-flipped value regardless.
    has_minimize = any(sign > 0 for _, sign in terms)

    def _display_value(raw_obj):
        return raw_obj if has_minimize else -raw_obj

    better_note = "lower reported score is better" if has_minimize else "higher reported score is better"
    print(f"Searching {inner_count} candidate(s) per outer combination x {outer_count} outer combination(s) "
          f"= {total} total projections, {objective_label} ({better_note}) ...\n")

    search_rows = []
    seen_filenames = set()
    all_candidate_rows = [] if args.log_all_candidates else None
    for i, (overrides_outer, scenario_outer) in enumerate(outer_combos, 1):
        label = ", ".join(f"{p}={overrides_outer[p]}" for p in outer_paths) or "(no outer parameters)"
        print(f"[{i}/{outer_count}] {label} ...", end=" ", flush=True)

        inner_combos = sweep_mod.build_matrix(scenario_outer, inner_params, max_combinations=args.max_combinations,
                                               section_label="optimize.parameters")
        best = None  # (objective, overrides_inner, scenario, rows, blended_rate)
        skipped = 0
        for overrides_inner, candidate in inner_combos:
            try:
                blended_rate = projection_mod.compute_blended_growth_rate(allocation_rows, household_total, candidate)
                rows = projection_mod.project(profile, candidate, account_rows, blended_rate=blended_rate)
            except projection_mod.ProjectionError:
                skipped += 1
                continue
            obj = sweep_mod.objective_value(rows[-1], terms)
            if best is None or obj < best[0]:
                best = (obj, overrides_inner, candidate, rows, blended_rate)
            if all_candidate_rows is not None:
                all_candidate_rows.append({
                    **{f"outer.{p}": overrides_outer[p] for p in outer_paths},
                    **{f"inner.{p}": overrides_inner[p] for p in inner_paths},
                    "objective": _display_value(obj),
                })

        if best is None:
            print(f"no valid candidates ({skipped} skipped)")
            search_rows.append({
                **{p: overrides_outer[p] for p in outer_paths},
                **{p: None for p in inner_paths},
                "objective": None, "outcome": "no_valid_candidates", "at_boundary": "", "filename": "",
            })
            continue

        obj, overrides_inner, winning_scenario, winning_rows, winning_rate = best
        boundary = [p for p in inner_paths if _hits_boundary(overrides_inner[p], inner_params[p])]
        filename = _dedupe_filename(
            sweep_mod.scenario_filename(base_stem, {**overrides_outer, **overrides_inner}), seen_filenames
        )
        filename_stem = filename[:-len(".yaml")]

        # --verbose: same per-account columns as `pt.cli project --verbose`.
        # Only computed for the WINNER, once per outer combination -- never
        # during the search loop above, since account_columns doesn't affect
        # the objective (it only adds a display-only per-row breakdown), so
        # building it for every candidate would be pure wasted work.
        account_columns = None
        if args.verbose:
            account_columns = projection_mod.build_account_columns(
                account_rows, projection_mod.build_inherited_schedules(winning_scenario, profile), profile
            )
            winning_rows = projection_mod.project(profile, winning_scenario, account_rows,
                                                    blended_rate=winning_rate, account_columns=account_columns)

        with open(out_dir / f"{filename_stem}.yaml", "w", encoding="utf-8") as f:
            yaml.safe_dump(winning_scenario, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
        if args.format in ("txt", "both"):
            text = projection_mod.format_scenario_config(winning_scenario) + projection_mod.format_table(
                winning_rows, profile, winning_rate, account_columns=account_columns
            )
            (out_dir / f"{filename_stem}.txt").write_text(text)
        if args.format in ("xlsx", "both"):
            wb = report_mod.build_projection_workbook(winning_rows, profile, winning_rate,
                                                        account_columns=account_columns, scenario=winning_scenario)
            wb.save(out_dir / f"{filename_stem}.xlsx")

        outcome = projection_mod.summarize_outcome(winning_rows, profile)
        search_rows.append({
            **{p: overrides_outer[p] for p in outer_paths},
            **{p: overrides_inner[p] for p in inner_paths},
            "objective": _display_value(obj), "outcome": outcome["outcome"],
            "at_boundary": ", ".join(boundary), "filename": f"{filename_stem}.yaml",
        })
        boundary_note = f" [AT BOUNDARY: {', '.join(boundary)}]" if boundary else ""
        print(f"best {objective_label}={_display_value(obj):,.0f}{boundary_note} ({skipped} skipped)")

    if not search_rows:
        print("\nNothing searched successfully.")
        return

    search_path = out_dir / "search_results.csv"
    fieldnames = outer_paths + inner_paths + ["objective", "outcome", "at_boundary", "filename"]
    with open(search_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(search_rows)
    print(f"\nSearch results saved to {search_path}")

    if all_candidate_rows is not None:
        all_path = out_dir / "all_candidates.csv"
        all_fieldnames = [f"outer.{p}" for p in outer_paths] + [f"inner.{p}" for p in inner_paths] + ["objective"]
        with open(all_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=all_fieldnames)
            writer.writeheader()
            writer.writerows(all_candidate_rows)
        print(f"All {len(all_candidate_rows)} candidate(s) logged to {all_path}")

    scored = [r for r in search_rows if r["objective"] is not None]
    if scored:
        # "objective" here is already the human-facing _display_value() --
        # ascending (best first) when has_minimize, since that reads as a
        # net-cost figure; descending for a pure maximize_sum_of objective,
        # since that's a natural-signed figure where higher is better.
        scored.sort(key=lambda r: r["objective"], reverse=not has_minimize)
        print(f"\nBest outer combination(s), by {objective_label}:")
        for r in scored[:10]:
            outer_label = ", ".join(f"{p}={r[p]}" for p in outer_paths) or "(no outer parameters)"
            inner_label = ", ".join(f"{p}={r[p]}" for p in inner_paths)
            boundary_note = f" [AT BOUNDARY: {r['at_boundary']}]" if r["at_boundary"] else ""
            print(f"  {outer_label}  ->  {inner_label}  =  {r['objective']:,.0f}{boundary_note}")
        if len(scored) > 10:
            print(f"  ... and {len(scored) - 10} more -- see {search_path}")


def build_parser():
    p = argparse.ArgumentParser(
        prog="scenario_engine",
        description="Generates a matrix of retirement scenario YAML files from a base scenario, and runs "
                    "pt's own projection engine against a directory of scenario files.",
    )
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser(
        "generate",
        help="Generate a matrix of scenario YAML files from a base scenario + a sweep config",
    )
    sp.add_argument("--sweep", required=True, help="Path to the sweep config YAML (see README.md)")
    sp.add_argument("--out-dir", default="scenarios", help="Directory to write the generated scenario files to "
                                                             "(default: scenarios/)")
    sp.add_argument("--max-combinations", type=int, default=sweep_mod.DEFAULT_MAX_COMBINATIONS,
                     help=f"Safety limit on how many scenarios one sweep can generate "
                          f"(default: {sweep_mod.DEFAULT_MAX_COMBINATIONS})")
    sp.set_defaults(func=cmd_generate)

    sp = sub.add_parser(
        "run",
        help="Run pt's projection engine against every scenario file in a directory",
    )
    sp.add_argument("--scenarios-dir", required=True, help="Directory of scenario YAML files to run "
                                                             "(e.g. the --out-dir a 'generate' run wrote to)")
    sp.add_argument("--out-dir", default="results", help="Directory to write results to (default: results/)")
    sp.add_argument("--format", choices=("txt", "xlsx", "both"), default="both",
                     help="Which output format(s) to write per scenario (default: both)")
    sp.add_argument("--db", default=db_mod.DEFAULT_DB_PATH, help="Path to SQLite database file (same as pt.cli)")
    sp.add_argument("--profile", default=None,
                     help=f"Path to the planning profile YAML (default: {planning.DEFAULT_PROFILE_PATH})")
    sp.add_argument("--snapshot", type=int, default=None,
                     help="Snapshot id to use for the starting portfolio value/allocation (default: latest)")
    sp.add_argument("-v", "--verbose", action="store_true",
                     help=f"Add a column for every account currently over "
                          f"${projection_mod.VERBOSE_ACCOUNT_THRESHOLD:,.0f} to each result -- same as "
                          "`pt.cli project --verbose`")
    sp.set_defaults(func=cmd_run)

    sp = sub.add_parser(
        "optimize",
        help="For each combination of the sweep config's outer 'parameters', search its 'optimize.parameters' "
             "for the combination minimizing/maximizing a sum of final-year projection fields, and keep only "
             "the winner",
    )
    sp.add_argument("--sweep", required=True, help="Path to the sweep config YAML (see README.md) -- needs "
                                                     "both 'parameters' (optional -- the outer/context grid) and "
                                                     "an 'optimize' section (the inner grid + objective)")
    sp.add_argument("--out-dir", default="optimized", help="Directory to write the winning scenario(s) and "
                                                             "results to (default: optimized/)")
    sp.add_argument("--format", choices=("txt", "xlsx", "both"), default="both",
                     help="Which output format(s) to write per winning scenario (default: both)")
    sp.add_argument("--max-combinations", type=int, default=sweep_mod.DEFAULT_MAX_OPTIMIZE_COMBINATIONS,
                     help="Safety limit on the TOTAL (outer x inner) number of projections one search can run "
                          f"(default: {sweep_mod.DEFAULT_MAX_OPTIMIZE_COMBINATIONS} -- much higher than "
                          "generate's, since none of these are written to disk individually)")
    sp.add_argument("--log-all-candidates", action="store_true",
                     help="Also write every candidate's objective value (not just each outer combination's "
                          "winner) to all_candidates.csv in --out-dir -- can be a large file for a big search")
    sp.add_argument("--db", default=db_mod.DEFAULT_DB_PATH, help="Path to SQLite database file (same as pt.cli)")
    sp.add_argument("--profile", default=None,
                     help=f"Path to the planning profile YAML (default: {planning.DEFAULT_PROFILE_PATH})")
    sp.add_argument("--snapshot", type=int, default=None,
                     help="Snapshot id to use for the starting portfolio value/allocation (default: latest)")
    sp.add_argument("-v", "--verbose", action="store_true",
                     help=f"Add a column for every account currently over "
                          f"${projection_mod.VERBOSE_ACCOUNT_THRESHOLD:,.0f} to each WINNING result -- same as "
                          "`pt.cli project --verbose` -- computed only for the winner, not during the search "
                          "itself, so this doesn't slow the search down")
    sp.set_defaults(func=cmd_optimize)

    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
