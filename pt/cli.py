"""
Command-line interface for the portfolio tool (v1.7).

Typical workflow:
    python3 -m pt.cli seed-classifications
    python3 -m pt.cli targets set "Growth" 0.60
    python3 -m pt.cli targets set "Income" 0.30
    python3 -m pt.cli targets set "Liquidity" 0.05
    python3 -m pt.cli targets set "Alternatives" 0.05
    python3 -m pt.cli targets set "US Equity" 0.45
    python3 -m pt.cli targets set "International Equity" 0.15
    python3 -m pt.cli targets set "US Bonds" 0.25
    python3 -m pt.cli targets set "International Bonds" 0.05
    python3 -m pt.cli import ~/Downloads/Portfolio_Positions_Aug-22-2026.csv
    python3 -m pt.cli classify-unclassified
    python3 -m pt.cli classify NEWTICKER "US Equity"
    python3 -m pt.cli accounts list
    python3 -m pt.cli accounts set-type 12345678 "401(k)"
    python3 -m pt.cli seed-styles
    python3 -m pt.cli seed-sectors
    python3 -m pt.cli style set VXUS "Large Blend" 0.6 "Large Growth" 0.4
    python3 -m pt.cli sector set VXUS "Information Technology" 0.35 "Financials" 0.15
    python3 -m pt.cli expense-ratio set VXUS 0.05
    python3 -m pt.cli targets set "US Equity" 0.80 --account 12345678
    python3 -m pt.cli report
    python3 -m pt.cli profile show
    python3 -m pt.cli project
    python3 -m pt.cli backup
"""
import argparse
import sys
from pathlib import Path

import yaml

from . import attributes
from . import backup as backup_mod
from . import db as db_mod
from . import importer
from .importer_schwab import SchwabImportError
from .importer_etrade import EtradeImportError
from . import planning
from . import store
from . import classifier
from . import ips as ips_mod
from . import allocation as alloc
from . import projection as projection_mod
from . import report as report_mod
from . import scenario as scenario_mod


def _parse_weighted_pairs(tokens):
    """tokens: either [category] (100% of that category implied), or
    [category, weight, category, weight, ...]. Returns a list of
    (category, weight_or_None) tuples. Raises ValueError on a malformed
    token list; weight validity/category validity is checked by the caller
    (each weighted-classification domain has its own valid-category list)."""
    if len(tokens) == 1:
        return [(tokens[0], None)]
    if len(tokens) % 2 != 0:
        raise ValueError(
            f'Expected category/weight pairs, e.g. "{tokens[0]}" 0.45 "..." 0.20 -- '
            "got an odd number of arguments."
        )
    pairs = []
    for i in range(0, len(tokens), 2):
        category = tokens[i]
        try:
            weight = float(tokens[i + 1])
        except ValueError:
            raise ValueError(f"'{tokens[i + 1]}' is not a number (expected a weight for '{category}').")
        pairs.append((category, weight))
    return pairs


def _parse_csv_owner_args(tokens):
    """Splits a flat list of CLI tokens into (csv_path, owner) pairs.

    Owner is optional per file. A token is treated as the start of a new
    CSV path; the token right after it is treated as that file's owner
    *unless* it looks like a path itself (ends in .csv, or exists on disk),
    in which case it's assumed to be the next CSV with no owner given.

    Examples:
        [a.csv, "Jane", b.csv, "John"] -> [(a.csv, "Jane"), (b.csv, "John")]
        [a.csv, b.csv]                 -> [(a.csv, None), (b.csv, None)]
        [a.csv, "Jane", b.csv]         -> [(a.csv, "Jane"), (b.csv, None)]
    """
    pairs = []
    i = 0
    n = len(tokens)
    while i < n:
        path = tokens[i]
        owner = None
        if i + 1 < n:
            nxt = tokens[i + 1]
            looks_like_a_path = nxt.lower().endswith(".csv") or Path(nxt).exists()
            if not looks_like_a_path:
                owner = nxt
                i += 1
        pairs.append((path, owner))
        i += 1
    return pairs


def _parse_file_mapping_args(entries, flag_name):
    """Parses a repeated `--account FILENAME:VALUE` (or similarly-shaped)
    argparse option into {basename: value}. Raises SystemExit with a clear
    message on a malformed entry (missing ':', or a filename that doesn't
    match any of the CSVs actually being imported isn't checked here --
    that's caught downstream by whichever parser actually needed the
    value, e.g. EtradeImportError, since a typo'd filename should still
    fail loudly rather than silently do nothing)."""
    mapping = {}
    for entry in entries or []:
        if ":" not in entry:
            raise SystemExit(
                f"--{flag_name} expects FILENAME:VALUE (e.g. --{flag_name} my_export.csv:X1234), got {entry!r}."
            )
        filename, _, value = entry.partition(":")
        mapping[Path(filename).name] = value
    return mapping


def cmd_import(args):
    conn = db_mod.get_connection(args.db)
    pairs = _parse_csv_owner_args(args.csv_path)
    account_numbers = _parse_file_mapping_args(args.account, "account")
    broker_overrides = _parse_file_mapping_args(args.broker, "broker")
    try:
        as_of_date, accounts, holdings, skipped = importer.parse_multiple_csvs(
            pairs, account_numbers=account_numbers, broker_overrides=broker_overrides,
        )
    except (importer.FidelityImportError, SchwabImportError, EtradeImportError) as e:
        print(f"Import failed: {e}", file=sys.stderr)
        sys.exit(1)

    source_file = "; ".join(Path(p).name for p, _owner in pairs)
    snapshot_id = store.save_snapshot(
        conn, source_file=source_file, as_of_date=as_of_date,
        accounts=accounts, holdings=holdings, note=args.note,
    )
    total_value = sum(h["current_value"] or 0 for h in holdings)
    print(f"Imported snapshot #{snapshot_id}: {len(holdings)} holdings across "
          f"{len(accounts)} accounts, ${total_value:,.0f} total.")

    print("\nAccounts and owners:")
    for acct_num, info in accounts.items():
        owner_label = info.get("owner") or "(unspecified)"
        if args.debug:
            # Show the type actually persisted in the DB, not just what this
            # file's account name would infer -- a prior `accounts set-type`
            # override for this account survives this import untouched.
            stored = store.get_account(conn, acct_num)
            actual_type = stored["account_type"] if stored else info["account_type"]
            note = ""
            if stored and stored["account_type_source"] == "manual" and actual_type != info["account_type"]:
                note = f"  (name would infer '{info['account_type']}'; using manual override)"
            print(f"  {acct_num}  {info['account_name']:<32}  Owner: {owner_label:<12}  "
                  f"Type: {actual_type}{note}")
        else:
            print(f"  {acct_num}  {info['account_name']:<32}  Owner: {owner_label}")

    if skipped:
        print(f"\n{len(skipped)} account(s) appeared in more than one file and were "
              f"marked as Joint (holdings kept from their first occurrence only):")
        for s in skipped:
            print(f"  {s['account_number']}  {s['account_name']}  (also seen in {s['source_file']})")

    unclassified = classifier.list_unclassified(conn, snapshot_id)
    if unclassified:
        uc_value = sum(r["total_value"] for r in unclassified)
        print(f"\n{len(unclassified)} symbols (${uc_value:,.0f}) have no asset-class "
              f"classification yet. Run 'python3 -m pt.cli classify-unclassified' to see them.")


def cmd_seed_classifications(args):
    conn = db_mod.get_connection(args.db)
    count = classifier.seed_classifications(conn, overwrite=args.overwrite)
    print(f"Seeded/updated {count} symbol classifications.")


def cmd_seed_styles(args):
    conn = db_mod.get_connection(args.db)
    seed_path = args.file or attributes.STYLE_SEED_PATH
    count = attributes.seed_styles(conn, seed_path=seed_path, overwrite=args.overwrite)
    print(f"Seeded/updated style data for {count} symbols (from {seed_path}).")


def cmd_seed_sectors(args):
    conn = db_mod.get_connection(args.db)
    seed_path = args.file or attributes.SECTOR_SEED_PATH
    count = attributes.seed_sectors(conn, seed_path=seed_path, overwrite=args.overwrite)
    print(f"Seeded/updated sector data for {count} symbols (from {seed_path}).")


def cmd_classify(args):
    conn = db_mod.get_connection(args.db)
    tokens = args.classification

    if len(tokens) == 1:
        try:
            classifier.classify_symbol(conn, args.symbol, tokens[0], notes=args.notes)
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        print(f"Classified {args.symbol.upper()} as '{tokens[0]}'.")
        return

    if tokens[0].strip().lower() != "fund":
        print(
            "Error: to classify a symbol into a single asset class, give just "
            'one argument (e.g. classify SYMBOL "US Equity"). To split a fund '
            'across multiple classes, start with "Fund": classify SYMBOL Fund '
            '"US Equity" 0.45 "US Bonds" 0.20 ...',
            file=sys.stderr,
        )
        sys.exit(1)

    rest = tokens[1:]
    if not rest:
        print('Error: list at least one asset class after "Fund".', file=sys.stderr)
        sys.exit(1)

    try:
        pairs = _parse_weighted_pairs(rest)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        weights = classifier.classify_fund(conn, args.symbol, pairs, notes=args.notes)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    label = ", ".join(f"{ac} {w*100:.1f}%" for ac, w in sorted(weights.items(), key=lambda kv: -kv[1]))
    print(f"Classified {args.symbol.upper()} as a Fund: {label}")


def cmd_classify_unclassified(args):
    conn = db_mod.get_connection(args.db)
    snapshot_id = args.snapshot or alloc.get_latest_snapshot_id(conn)
    if snapshot_id is None:
        print("No snapshots yet. Run 'import' first.", file=sys.stderr)
        sys.exit(1)
    rows = classifier.list_unclassified(conn, snapshot_id)
    if not rows:
        print("Nothing unclassified in this snapshot.")
        return
    print(f"{'Symbol':<10} {'Value':>14}  Description")
    for r in rows:
        print(f"{r['symbol']:<10} ${r['total_value']:>13,.0f}  {r['description'] or ''}")
    print(f"\nClassify with: python3 -m pt.cli classify SYMBOL \"Asset Class\"")
    print(f"Valid asset classes: {', '.join(classifier.ASSET_CLASSES)}")


def _cmd_weighted_set(args, setter, label):
    conn = db_mod.get_connection(args.db)
    try:
        pairs = _parse_weighted_pairs(args.weights)
        weights = setter(conn, args.symbol, pairs)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    summary = ", ".join(f"{c} {w*100:.1f}%" for c, w in sorted(weights.items(), key=lambda kv: -kv[1]))
    print(f"Set {label} for {args.symbol.upper()}: {summary}")


def _cmd_weighted_show(args, getter, label):
    conn = db_mod.get_connection(args.db)
    weights = getter(conn, args.symbol)
    if not weights:
        print(f"No {label} data for {args.symbol.upper()}.")
        return
    for category, weight in sorted(weights.items(), key=lambda kv: -kv[1]):
        print(f"  {category:<24} {weight*100:>6.1f}%")


def cmd_style_set(args):
    _cmd_weighted_set(args, attributes.set_style, "style")


def cmd_style_show(args):
    _cmd_weighted_show(args, attributes.get_style_weights, "style")


def cmd_sector_set(args):
    _cmd_weighted_set(args, attributes.set_sector, "sector")


def cmd_sector_show(args):
    _cmd_weighted_show(args, attributes.get_sector_weights, "sector")


def cmd_expense_ratio_set(args):
    conn = db_mod.get_connection(args.db)
    try:
        attributes.set_expense_ratio(conn, args.symbol, args.pct)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"Set expense ratio for {args.symbol.upper()} to {args.pct:.2f}%.")


def cmd_expense_ratio_show(args):
    conn = db_mod.get_connection(args.db)
    ratio = attributes.get_expense_ratio(conn, args.symbol)
    if ratio is None:
        print(f"No expense ratio set for {args.symbol.upper()}.")
        return
    print(f"{args.symbol.upper()}: {ratio*100:.2f}%")


def cmd_targets_set(args):
    conn = db_mod.get_connection(args.db)
    try:
        ips_mod.set_target(conn, args.asset_class, args.pct, account_number=args.account)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    scope = f"account {args.account}" if args.account else "household"
    print(f"Target for '{args.asset_class}' ({scope}) set to {args.pct*100:.1f}%.")


def cmd_targets_clear(args):
    conn = db_mod.get_connection(args.db)
    try:
        removed = ips_mod.clear_target(conn, args.asset_class, account_number=args.account)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    scope = f"account {args.account}" if args.account else "household"
    if removed:
        print(f"Cleared the {scope} target for '{args.asset_class}'.")
    else:
        print(f"No {scope} target was set for '{args.asset_class}' -- nothing to clear.")


def cmd_targets_show(args):
    conn = db_mod.get_connection(args.db)
    if args.account:
        targets, overridden = ips_mod.get_effective_targets(conn, args.account)
        if not targets:
            print(f"No IPS targets set yet (checked account {args.account} and the household).")
            return
        total = sum(targets.values())
        for ac, pct in targets.items():
            tag = "  [account override]" if ac in overridden else ""
            print(f"  {ac:<24} {pct*100:>6.1f}%{tag}")
        flag = "  <-- WARNING: targets don't sum to 100%" if abs(total - 1.0) > 0.001 else ""
        print(f"  {'TOTAL':<24} {total*100:>6.1f}%{flag}")
        return

    targets = ips_mod.get_targets(conn)
    if not targets:
        print("No IPS targets set yet.")
        return
    total = sum(targets.values())
    for ac, pct in targets.items():
        print(f"  {ac:<24} {pct*100:>6.1f}%")
    flag = "  <-- WARNING: targets don't sum to 100%" if abs(total - 1.0) > 0.001 else ""
    print(f"  {'TOTAL':<24} {total*100:>6.1f}%{flag}")


def cmd_accounts_list(args):
    conn = db_mod.get_connection(args.db)
    rows = store.list_accounts(conn)
    if not rows:
        print("No accounts yet. Run 'import' first.")
        return
    print(f"{'Account #':<12} {'Account Name':<32} {'Type':<20} {'Owner':<14} {'Expense Ratio':<13}")
    for r in rows:
        tag = " [manual]" if r["account_type_source"] == "manual" else ""
        type_label = f"{r['account_type']}{tag}"
        er_label = f"{r['expense_ratio']*100:.2f}%" if r["expense_ratio"] is not None else ""
        print(f"{r['account_number']:<12} {r['account_name']:<32} {type_label:<20} "
              f"{r['owner'] or '':<14} {er_label}")


def cmd_accounts_set_type(args):
    conn = db_mod.get_connection(args.db)
    if args.account_type not in importer.ACCOUNT_TYPES:
        print(f"Error: '{args.account_type}' is not a recognized account type. "
              f"Choose one of: {', '.join(importer.ACCOUNT_TYPES)}", file=sys.stderr)
        sys.exit(1)

    account = store.get_account(conn, args.account_number)
    if account is None:
        print(f"Error: no account with number '{args.account_number}' found. "
              "Run 'accounts list' (or check the import summary) for known account numbers.",
              file=sys.stderr)
        sys.exit(1)

    store.set_account_type(conn, args.account_number, args.account_type)
    print(f"Set {args.account_number} ({account['account_name']}) to account type "
          f"'{args.account_type}'. This overrides name-based inference and persists "
          "across future imports of this account.")


def cmd_accounts_set_expense_ratio(args):
    conn = db_mod.get_connection(args.db)
    account = store.get_account(conn, args.account_number)
    if account is None:
        print(f"Error: no account with number '{args.account_number}' found. "
              "Run 'accounts list' (or check the import summary) for known account numbers.",
              file=sys.stderr)
        sys.exit(1)
    try:
        store.set_account_expense_ratio(conn, args.account_number, args.pct)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"Set {args.account_number} ({account['account_name']}) expense ratio to {args.pct:.2f}%. "
          "This is a flat account-level fee (e.g. a managed account's advisory fee) -- ADDS TO, doesn't "
          "replace, its holdings' own expense ratios in 'report's expense summary.")


def cmd_profile_show(args):
    path = args.file or planning.DEFAULT_PROFILE_PATH
    try:
        profile = planning.load_profile(path)
    except planning.ProfileError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"Planning profile: {path}\n")
    print(yaml.safe_dump(profile, sort_keys=False, default_flow_style=False))


def cmd_backup(args):
    try:
        result = backup_mod.backup(
            db_path=args.db,
            backup_dir=args.out_dir or backup_mod.DEFAULT_BACKUP_DIR,
            include_profile=not args.no_profile,
        )
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    db_size = result["database"].stat().st_size
    print(f"Backed up database ({db_size:,} bytes) to {result['database']}")
    if result["profile"]:
        print(f"Backed up planning profile to {result['profile']}")
    elif not args.no_profile:
        print("(No planning profile found to back up -- see 'profile show'.)")
    for scenario_backup_path in result["scenarios"]:
        print(f"Backed up retirement scenario to {scenario_backup_path}")


def cmd_snapshots_list(args):
    conn = db_mod.get_connection(args.db)
    rows = store.list_snapshots(conn)
    if not rows:
        print("No snapshots yet.")
        return
    for r in rows:
        print(f"#{r['id']:<4} {r['imported_at']}  {r['source_file']}"
              f"{'  (' + r['note'] + ')' if r['note'] else ''}")


def cmd_report(args):
    conn = db_mod.get_connection(args.db)
    snapshot_id = args.snapshot or alloc.get_latest_snapshot_id(conn)
    if snapshot_id is None:
        print("No snapshots yet. Run 'import' first.", file=sys.stderr)
        sys.exit(1)

    text = report_mod.build_text_report(conn, snapshot_id, debug=args.debug, public=args.public)
    text_path = Path(args.text_out)
    text_path.write_text(text)
    print(text)
    print(f"\n[Text report saved to {text_path}]  ({'public/shareable' if args.public else 'private/full'} view)")

    wb = report_mod.build_workbook(conn, snapshot_id, profile_path=args.profile, scenario_path=args.scenario,
                                    verbose=args.verbose, monte_carlo=args.monte_carlo,
                                    trials=args.trials, seed=args.seed, public=args.public,
                                    per_bucket_growth=args.per_bucket_growth)
    xlsx_path = Path(args.out)
    xlsx_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(xlsx_path)
    print(f"[Workbook saved to {xlsx_path}]")
    if args.monte_carlo and "Monte Carlo" not in wb.sheetnames:
        print("\n[--monte-carlo: no 'Monte Carlo' tab was added -- it needs the same planning profile "
              "and retirement scenario as the Projection tab, and those either didn't load or didn't "
              "project cleanly. Run `pt project --scenario ... --profile ...` to see the error.]",
              file=sys.stderr)


def _bucket_allocations_for(args, scenario: dict, profile: dict, inherited_schedules: list) -> dict:
    """Per-tax-bucket allocation for --per-bucket-growth (see
    projection.bucket_allocation_rows()) -- reads a real household's
    per-account classification from the database, or a hypothetical
    scenario's own per-account asset_classes, mirroring cmd_project()/
    cmd_monte_carlo()'s own real-vs-hypothetical branch. May raise
    projection_mod.ProjectionError (a hypothetical scenario) or exit via
    the same "no snapshot" message as the caller (a real one, though this
    is only reached after the caller has already confirmed one exists)."""
    if scenario.get("hypothetical_accounts"):
        class_rows = projection_mod.build_hypothetical_account_class_rows(scenario, profile)
    else:
        conn = db_mod.get_connection(args.db)
        snapshot_id = args.snapshot or alloc.get_latest_snapshot_id(conn)
        class_rows = alloc.allocation_by_account(conn, snapshot_id)
    return projection_mod.bucket_allocation_rows(class_rows, inherited_schedules, profile)


def _load_profile_for(args, scenario: dict) -> dict:
    """Loads the planning profile for `project`/`monte-carlo` -- a thin
    wrapper around planning.resolve_profile() using this command's own
    --profile flag. May raise planning.ProfileError."""
    return planning.resolve_profile(args.profile, scenario)


def cmd_project(args):
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

    try:
        if scenario.get("hypothetical_accounts"):
            # A made-up household -- no import, no database snapshot, no
            # ticker classification -- see build_hypothetical_accounts().
            # --db/--snapshot are ignored entirely in this mode.
            account_rows, allocation_rows, total = projection_mod.build_hypothetical_accounts(scenario, profile)
        else:
            conn = db_mod.get_connection(args.db)
            snapshot_id = args.snapshot or alloc.get_latest_snapshot_id(conn)
            if snapshot_id is None:
                print("No snapshots yet. Run 'import' first (or add scenario.hypothetical_accounts "
                      "to try the tool without one -- see README.md).", file=sys.stderr)
                sys.exit(1)
            total = alloc.household_total_value(conn, snapshot_id)
            allocation_rows, _ = alloc.allocation_by_asset_class(conn, snapshot_id)
            account_rows = alloc.account_values(conn, snapshot_id)

        inherited_schedules = projection_mod.build_inherited_schedules(scenario, profile)
        account_columns = projection_mod.build_account_columns(
            account_rows, inherited_schedules, profile) if args.verbose else None
        blended_rate = projection_mod.compute_blended_growth_rate(allocation_rows, total, scenario)
        bucket_rates = None
        if args.per_bucket_growth:
            bucket_allocations = _bucket_allocations_for(args, scenario, profile, inherited_schedules)
            bucket_rates = {
                key: projection_mod.compute_blended_growth_rate(bucket_rows, bucket_total, scenario)
                for key, (bucket_rows, bucket_total) in bucket_allocations.items()
            }
        rows = projection_mod.project(profile, scenario, account_rows, blended_rate=blended_rate,
                                       account_columns=account_columns, bucket_rates=bucket_rates)
    except projection_mod.ProjectionError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    text = projection_mod.format_scenario_config(scenario) + projection_mod.format_table(
        rows, profile, blended_rate, account_columns=account_columns, bucket_rates=bucket_rates
    )
    print(text)
    # --out is repeatable (e.g. --out projection.txt --out projection.xlsx)
    # so one run can save more than one output format at once; each path is
    # routed independently by its own extension, same as a single --out
    # always was.
    for out in args.out or []:
        out_path = Path(out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if out_path.suffix.lower() == ".xlsx":
            wb = report_mod.build_projection_workbook(rows, profile, blended_rate, account_columns=account_columns,
                                                        scenario=scenario, bucket_rates=bucket_rates)
            wb.save(out_path)
            print(f"\n[Projection workbook saved to {out_path}]")
        elif out_path.suffix.lower() == ".png":
            try:
                from . import charts as charts_mod
            except ImportError:
                print("\n--out *.png needs the matplotlib package, which isn't installed.\n"
                      "Install it with:\n\n    pip3 install matplotlib\n\n"
                      "It's intentionally NOT in requirements.txt -- the core pt tool never needs it "
                      "(see requirements-optional.txt).", file=sys.stderr)
                sys.exit(1)
            charts_mod.save_projection_chart(rows, profile, out_path)
            print(f"\n[Projection chart saved to {out_path}]")
        else:
            out_path.write_text(text)
            print(f"\n[Projection saved to {out_path}]")


def cmd_monte_carlo(args):
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

    try:
        if scenario.get("hypothetical_accounts"):
            # A made-up household -- no import, no database snapshot, no
            # ticker classification -- see build_hypothetical_accounts().
            # --db/--snapshot are ignored entirely in this mode.
            account_rows, allocation_rows, total = projection_mod.build_hypothetical_accounts(scenario, profile)
        else:
            conn = db_mod.get_connection(args.db)
            snapshot_id = args.snapshot or alloc.get_latest_snapshot_id(conn)
            if snapshot_id is None:
                print("No snapshots yet. Run 'import' first (or add scenario.hypothetical_accounts "
                      "to try the tool without one -- see README.md).", file=sys.stderr)
                sys.exit(1)
            total = alloc.household_total_value(conn, snapshot_id)
            allocation_rows, _ = alloc.allocation_by_asset_class(conn, snapshot_id)
            account_rows = alloc.account_values(conn, snapshot_id)

        blended_rate = projection_mod.compute_blended_growth_rate(allocation_rows, total, scenario)
        volatility = projection_mod.compute_blended_volatility(allocation_rows, total, scenario)
        bucket_rates = None
        bucket_allocations = None
        if args.per_bucket_growth:
            inherited_schedules = projection_mod.build_inherited_schedules(scenario, profile)
            bucket_allocations = _bucket_allocations_for(args, scenario, profile, inherited_schedules)
            bucket_rates = {
                key: projection_mod.compute_blended_growth_rate(bucket_rows, bucket_total, scenario)
                for key, (bucket_rows, bucket_total) in bucket_allocations.items()
            }
        mc = projection_mod.run_monte_carlo(profile, scenario, account_rows, blended_rate, volatility,
                                             allocation_rows, total, trials=args.trials, seed=args.seed,
                                             bucket_allocations=bucket_allocations)
    except projection_mod.ProjectionError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    text = projection_mod.format_scenario_config(scenario) + projection_mod.format_monte_carlo_table(
        mc, blended_rate, bucket_rates=bucket_rates
    )
    print(text)
    for out in args.out or []:
        out_path = Path(out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if out_path.suffix.lower() == ".xlsx":
            wb = report_mod.build_monte_carlo_workbook(mc, blended_rate, scenario=scenario, seed=args.seed,
                                                        bucket_rates=bucket_rates)
            wb.save(out_path)
            print(f"\n[Monte Carlo workbook saved to {out_path}]")
        elif out_path.suffix.lower() == ".png":
            try:
                from . import charts as charts_mod
            except ImportError:
                print("\n--out *.png needs the matplotlib package, which isn't installed.\n"
                      "Install it with:\n\n    pip3 install matplotlib\n\n"
                      "It's intentionally NOT in requirements.txt -- the core pt tool never needs it "
                      "(see requirements-optional.txt).", file=sys.stderr)
                sys.exit(1)
            charts_mod.save_monte_carlo_chart(mc, out_path)
            print(f"\n[Monte Carlo chart saved to {out_path}]")
        else:
            out_path.write_text(text)
            print(f"\n[Monte Carlo projection saved to {out_path}]")


def build_parser():
    p = argparse.ArgumentParser(prog="pt", description="Household portfolio tool (v1.7)")
    p.add_argument("--db", default=db_mod.DEFAULT_DB_PATH, help="Path to SQLite database file")
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("import", help="Import one or more brokerage CSV exports "
                                        "(Fidelity, Schwab, E*TRADE) as a new snapshot")
    sp.add_argument("csv_path", nargs="+",
                     help="One or more CSV file paths, each optionally followed by an owner "
                          'name, e.g.: file1.csv "Jane" file2.csv "John". If an account '
                          "number appears in more than one file, it's marked as owned by "
                          '"Joint" and only its first occurrence\'s holdings are kept. '
                          "Broker format (Fidelity/Schwab/E*TRADE) is auto-detected per file.")
    sp.add_argument("--account", action="append", default=[], metavar="FILENAME:ACCOUNT_NUMBER",
                     help="Required for an E*TRADE file, since that export never reveals its own "
                          "account number -- e.g. --account my_etrade_export.csv:X1234. Repeatable, "
                          "one per E*TRADE file. Ignored for Fidelity/Schwab, which self-identify. "
                          "Place this (and --broker) BEFORE the csv_path list on the command line -- "
                          "argparse can't reliably resume collecting csv_path values after an "
                          "option appears in the middle of them.")
    sp.add_argument("--broker", action="append", default=[], metavar="FILENAME:{fidelity,schwab,etrade}",
                     help="Override auto-detection for one file, e.g. --broker my_export.csv:schwab -- "
                          "only needed if detection guesses wrong. Place before csv_path, same as --account.")
    sp.add_argument("--note", default=None, help="Optional note to attach to this snapshot")
    sp.add_argument("-d", "--debug", action="store_true",
                     help="Also print the inferred account type for each imported account")
    sp.set_defaults(func=cmd_import)

    sp = sub.add_parser("seed-classifications", help="Load the starter ticker classification list")
    sp.add_argument("--overwrite", action="store_true", help="Overwrite existing classifications")
    sp.set_defaults(func=cmd_seed_classifications)

    sp = sub.add_parser("seed-styles", help="Load investment-style data (bundled seed file, or --file a Fidelity export)")
    sp.add_argument("--file", default=None,
                     help="Path to a style CSV -- either the clean symbol/style/weight format, or a "
                          "raw Fidelity 'Style Diversification' export (Positions > Style diagnostics "
                          "> Download). Defaults to the bundled data/style_seed.csv.")
    sp.add_argument("--overwrite", action="store_true", help="Overwrite existing style data")
    sp.set_defaults(func=cmd_seed_styles)

    sp = sub.add_parser("seed-sectors", help="Load sector data (bundled seed file, or --file a Fidelity export)")
    sp.add_argument("--file", default=None,
                     help="Path to a sector CSV -- either the clean symbol/sector/weight format, or a "
                          "raw Fidelity 'Sector Diversification' export (Positions > Sector diagnostics "
                          "> Download). Defaults to the bundled data/sector_seed.csv.")
    sp.add_argument("--overwrite", action="store_true", help="Overwrite existing sector data")
    sp.set_defaults(func=cmd_seed_sectors)

    sp = sub.add_parser("classify", help="Classify a symbol into an asset class, or as a multi-class Fund")
    sp.add_argument("symbol")
    sp.add_argument(
        "classification", nargs="+",
        help='Either a single asset class (e.g. "US Equity"), or "Fund" followed by '
             'asset-class/weight pairs (e.g. Fund "US Equity" 0.45 "US Bonds" 0.20). '
             'A single class after "Fund" with no weight is treated as 100%%. '
             "If weights don't add up to 100%%, the remainder is classified as 'Other'."
    )
    sp.add_argument("--notes", default=None)
    sp.set_defaults(func=cmd_classify)

    sp = sub.add_parser("classify-unclassified", help="List symbols with no asset-class classification")
    sp.add_argument("--snapshot", type=int, default=None, help="Snapshot id (default: latest)")
    sp.set_defaults(func=cmd_classify_unclassified)

    sp = sub.add_parser("style", help="View or manually set a symbol's investment style (style-box weights)")
    stsub = sp.add_subparsers(dest="style_command", required=True)

    stp = stsub.add_parser(
        "set",
        help="Set a symbol's style, e.g. a single box (style set VXUS \"Large Blend\") or several "
             'weighted boxes (style set VXUS "Large Blend" 0.6 "Large Growth" 0.4)',
    )
    stp.add_argument("symbol")
    stp.add_argument("weights", nargs="+",
                      help=f"One category, or category/weight pairs. Valid categories: "
                           f"{', '.join(attributes.STYLE_CATEGORIES)}")
    stp.set_defaults(func=cmd_style_set)

    stp = stsub.add_parser("show", help="Show a symbol's style-box weights")
    stp.add_argument("symbol")
    stp.set_defaults(func=cmd_style_show)

    sp = sub.add_parser("sector", help="View or manually set a symbol's sector weights")
    sesub = sp.add_subparsers(dest="sector_command", required=True)

    sep = sesub.add_parser(
        "set",
        help="Set a symbol's sector(s), e.g. sector set NVDA \"Information Technology\", or several "
             'weighted sectors (sector set VTI "Information Technology" 0.35 "Financials" 0.12 ...)',
    )
    sep.add_argument("symbol")
    sep.add_argument("weights", nargs="+",
                      help=f"One category, or category/weight pairs. Valid categories: "
                           f"{', '.join(attributes.SECTOR_CATEGORIES)}")
    sep.set_defaults(func=cmd_sector_set)

    sep = sesub.add_parser("show", help="Show a symbol's sector weights")
    sep.add_argument("symbol")
    sep.set_defaults(func=cmd_sector_show)

    sp = sub.add_parser("expense-ratio", help="View or set a symbol's expense ratio")
    ersub = sp.add_subparsers(dest="expense_ratio_command", required=True)

    erp = ersub.add_parser("set", help="Set a symbol's expense ratio")
    erp.add_argument("symbol")
    erp.add_argument("pct", type=float, help="Expense ratio as a percentage, e.g. 0.03 for 0.03%% or 0.75 for 0.75%%")
    erp.set_defaults(func=cmd_expense_ratio_set)

    erp = ersub.add_parser("show", help="Show a symbol's expense ratio")
    erp.add_argument("symbol")
    erp.set_defaults(func=cmd_expense_ratio_show)

    sp = sub.add_parser("targets", help="Manage IPS targets, globally or per account")
    tsub = sp.add_subparsers(dest="targets_command", required=True)

    tsp = tsub.add_parser(
        "set",
        help="Set target %% for a macro class or an asset subclass (globally, or for one "
             "account with --account, subclass only)",
    )
    tsp.add_argument("asset_class",
                      help=f"A macro class ({', '.join(classifier.MACRO_CLASSES)}) or an asset "
                           f"subclass ({', '.join(classifier.ASSET_CLASSES)}). Macro and subclass "
                           "targets are independent -- set either, both, or neither for a given branch "
                           "of the hierarchy.")
    tsp.add_argument("pct", type=float, help="Target as a fraction, e.g. 0.45 for 45%%")
    tsp.add_argument("--account", default=None,
                      help="Account number to set a per-account override for, instead of the "
                           "household-wide target. Subclass only, not macro. A subclass with no "
                           "override for an account falls back to the household target.")
    tsp.set_defaults(func=cmd_targets_set)

    tsp = tsub.add_parser(
        "clear",
        help="Remove a previously-set target (household, or one account's override with --account)",
    )
    tsp.add_argument("asset_class",
                      help=f"A macro class ({', '.join(classifier.MACRO_CLASSES)}) or an asset "
                           f"subclass ({', '.join(classifier.ASSET_CLASSES)})")
    tsp.add_argument("--account", default=None,
                      help="Clear this account's override instead of the household-wide target")
    tsp.set_defaults(func=cmd_targets_clear)

    tsp = tsub.add_parser("show", help="Show current IPS targets (globally, or effective targets for one account with --account)")
    tsp.add_argument("--account", default=None,
                      help="Show this account's effective targets (household targets with any "
                           "of its own overrides applied on top)")
    tsp.set_defaults(func=cmd_targets_show)

    sp = sub.add_parser("accounts", help="View accounts and manually correct a misclassified account type")
    asub = sp.add_subparsers(dest="accounts_command", required=True)

    asp = asub.add_parser("list", help="List known accounts with their type (and whether it was overridden) and owner")
    asp.set_defaults(func=cmd_accounts_list)

    asp = asub.add_parser(
        "set-type",
        help="Manually override an account's type, e.g. when the account name gives no hint "
             "(a 401(k) named after the employer). Persists across future imports of that account.",
    )
    asp.add_argument("account_number", help="Account number, e.g. as shown in 'accounts list'")
    asp.add_argument("account_type", help=f"One of: {', '.join(importer.ACCOUNT_TYPES)}")
    asp.set_defaults(func=cmd_accounts_set_type)

    asp = asub.add_parser(
        "set-expense-ratio",
        help="Set a flat account-level fee (e.g. a managed account's advisory fee) -- "
             "adds to, doesn't replace, its holdings' own expense ratios in 'report's expense summary",
    )
    asp.add_argument("account_number", help="Account number, e.g. as shown in 'accounts list'")
    asp.add_argument("pct", type=float, help="A percentage, e.g. 1.0 for 1.00%%/year (not a 0-1 fraction)")
    asp.set_defaults(func=cmd_accounts_set_expense_ratio)

    sp = sub.add_parser(
        "profile",
        help="View the retirement planning profile (retirement ages, spending, "
             "inflation, returns, Social Security -- see pt/planning.py)",
    )
    prsub = sp.add_subparsers(dest="profile_command", required=True)

    prp = prsub.add_parser("show", help="Print the planning profile")
    prp.add_argument("--file", default=None,
                      help=f"Path to the profile YAML (default: {planning.DEFAULT_PROFILE_PATH})")
    prp.set_defaults(func=cmd_profile_show)

    sp = sub.add_parser(
        "backup",
        help="Back up the database (and planning profile) to a timestamped copy",
    )
    sp.add_argument("--out-dir", default=None,
                     help=f"Directory to write the backup into (default: {backup_mod.DEFAULT_BACKUP_DIR})")
    sp.add_argument("--no-profile", action="store_true",
                     help="Don't also back up the retirement planning profile YAML")
    sp.set_defaults(func=cmd_backup)

    sp = sub.add_parser("snapshots", help="List saved snapshots")
    sp.set_defaults(func=cmd_snapshots_list)

    sp = sub.add_parser("report", help="Generate rebalancing report (text + Excel workbook)")
    view = sp.add_mutually_exclusive_group()
    view.add_argument("--public", dest="public", action="store_true", default=True,
                       help="Shareable view (DEFAULT). Drops the Account Number column, the Holdings / "
                            "Investment Expenses / IPS Comparison / IPS by Account / Unclassified tabs, and "
                            "the Summary tab's two IPS-target tables; strips the same content (plus "
                            "account-number suffixes) from the text report; redacts account numbers in the "
                            "Projection/Monte Carlo scenario dump.")
    view.add_argument("--private", dest="public", action="store_false",
                       help="Full internal view -- every tab and column, nothing redacted.")
    sp.add_argument("--snapshot", type=int, default=None, help="Snapshot id (default: latest)")
    sp.add_argument("--out", default="rebalancing_report.xlsx", help="Excel output path")
    sp.add_argument("--text-out", default="rebalancing_report.txt", help="Text report output path")
    sp.add_argument("-d", "--debug", action="store_true",
                     help="Include a debug section showing which accounts feed into each "
                          "ALLOCATION BY ACCOUNT TYPE total")
    sp.add_argument("--profile", default=None,
                     help="Path to the planning profile YAML, for the workbook's Projection tab "
                          f"(default: {planning.DEFAULT_PROFILE_PATH}; tab is skipped if not found)")
    sp.add_argument("--scenario", default=None,
                     help="Path to the retirement scenario YAML, for the workbook's Projection tab "
                          f"(default: {scenario_mod.DEFAULT_SCENARIO_PATH}; tab is skipped if not found)")
    sp.add_argument("-v", "--verbose", action="store_true",
                     help="On the workbook's Projection tab, add a column for every account currently over "
                          f"${projection_mod.VERBOSE_ACCOUNT_THRESHOLD:,.0f} -- same as `project --verbose` "
                          "(no effect on the text report, which has no Projection section)")
    sp.add_argument("--monte-carlo", action="store_true",
                     help="Also run a Monte Carlo simulation from the same scenario/profile and add a "
                          "'Monte Carlo' tab (immediately after 'Projection') -- percentile fan-chart "
                          "table + chart, same as the standalone `monte-carlo` command. Both tabs also "
                          "get a MODEL PARAMETERS block (blended return/volatility, per-asset-class "
                          "assumptions).")
    sp.add_argument("--trials", type=int, default=1000,
                     help="Monte Carlo trial count for --monte-carlo (default: 1000). No effect without it.")
    sp.add_argument("--seed", type=int, default=None,
                     help="Monte Carlo random seed for --monte-carlo, for a reproducible tab "
                          "(default: different every run). No effect without it.")
    sp.add_argument("--per-bucket-growth", action="store_true",
                     help="Grow each tax bucket (Taxable, each Traditional owner/type pool, each Roth "
                          "owner, each Inherited account) at its OWN blended rate/correlated Monte Carlo "
                          "draw from what THAT bucket actually holds, instead of one household-wide rate "
                          "applied to every bucket (today's default). Adds a per-bucket breakdown to the "
                          "MODEL PARAMETERS block alongside the household summary.")
    sp.set_defaults(func=cmd_report)

    sp = sub.add_parser(
        "project",
        help="Year-by-year retirement cash-flow projection (portfolio growth, spending, "
             "withdrawals) from a retirement scenario",
    )
    sp.add_argument("--snapshot", type=int, default=None,
                     help="Snapshot id to use for the starting portfolio value/allocation (default: latest)")
    sp.add_argument("--profile", default=None,
                     help=f"Path to the planning profile YAML (default: {planning.DEFAULT_PROFILE_PATH})")
    sp.add_argument("--scenario", default=None,
                     help=f"Path to the retirement scenario YAML (default: {scenario_mod.DEFAULT_SCENARIO_PATH}) "
                          "-- point at a different file to compare scenarios")
    sp.add_argument("--out", action="append", default=None,
                     help="Also save the projection to this path -- an Excel workbook (.xlsx, with an "
                          "embedded chart), a chart image (.png, needs matplotlib -- see "
                          "requirements-optional.txt), or a text file (anything else). Repeatable to "
                          "save more than one output at once, e.g. --out projection.txt --out "
                          "projection.xlsx --out projection.png")
    sp.add_argument("-v", "--verbose", action="store_true",
                     help=f"Add a column for every account currently over "
                          f"${projection_mod.VERBOSE_ACCOUNT_THRESHOLD:,.0f}, on the right side of the table")
    sp.add_argument("--per-bucket-growth", action="store_true",
                     help="Grow each tax bucket (Taxable, each Traditional owner/type pool, each Roth "
                          "owner, each Inherited account) at its OWN blended rate from what THAT bucket "
                          "actually holds, instead of one household-wide rate applied to every bucket "
                          "(today's default). Prints a per-bucket breakdown under the household summary.")
    sp.set_defaults(func=cmd_project)

    sp = sub.add_parser(
        "monte-carlo",
        help="Monte Carlo variant of `project` -- runs many trials with an independently "
             "randomized return each year (instead of one fixed blended rate), reporting a "
             "success rate and percentile balance bands rather than a single deterministic table",
    )
    sp.add_argument("--snapshot", type=int, default=None,
                     help="Snapshot id to use for the starting portfolio value/allocation (default: latest)")
    sp.add_argument("--profile", default=None,
                     help=f"Path to the planning profile YAML (default: {planning.DEFAULT_PROFILE_PATH})")
    sp.add_argument("--scenario", default=None,
                     help=f"Path to the retirement scenario YAML (default: {scenario_mod.DEFAULT_SCENARIO_PATH})")
    sp.add_argument("--trials", type=int, default=1000, help="Number of simulated trials (default: 1000)")
    sp.add_argument("--seed", type=int, default=None,
                     help="Random seed for a reproducible result (default: different every run)")
    sp.add_argument("--out", action="append", default=None,
                     help="Also save the result to this path -- an Excel workbook (.xlsx, with embedded "
                          "fan charts), a fan-chart image (.png, needs matplotlib -- see "
                          "requirements-optional.txt), or a text file (anything else). Repeatable, "
                          "e.g. --out mc.txt --out mc.xlsx --out mc.png")
    sp.add_argument("--per-bucket-growth", action="store_true",
                     help="Draw each tax bucket's (Taxable, each Traditional owner/type pool, each Roth "
                          "owner, each Inherited account) own correlated return from what THAT bucket "
                          "actually holds every year, instead of one household-wide draw applied to every "
                          "bucket (today's default) -- buckets stay correlated with each other (the same "
                          "underlying per-asset-class economy each year), they just realize different "
                          "results based on their own mix. Prints a per-bucket mean-return breakdown.")
    sp.set_defaults(func=cmd_monte_carlo)

    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
