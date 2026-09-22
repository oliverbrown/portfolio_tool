"""Investment Policy Statement targets: storage and comparison against actuals.

Targets can be set at either level of the classifier.ASSET_CLASS_HIERARCHY --
a macro class (e.g. "Growth") or a subclass (e.g. "US Equity") -- and either
may or may not have a target set at all; see compare_to_targets() /
compare_to_macro_targets(). Both levels share the same ips_targets /
ips_targets_by_account tables (a single asset_class/target_pct column pair),
distinguished only by whether the stored name is a macro or subclass name --
see classifier.MACRO_CLASSES / classifier.ASSET_CLASSES, which are disjoint
sets by construction. Per-account overrides are subclass-only: the by-account
report operates at subclass granularity (see compare_to_targets_by_account()),
so a macro-level override would have nothing to compare against there.
"""
from collections import defaultdict

from .classifier import ASSET_CLASSES, MACRO_CLASSES, TARGETABLE_CLASSES


def set_target(conn, class_name: str, target_pct: float, account_number: str = None):
    """class_name may be a subclass (e.g. "US Equity") or a macro class (e.g.
    "Growth"). target_pct as a fraction, e.g. 0.60 for 60%. If account_number
    is given, sets a per-account override instead of the household-wide
    target -- only valid for a subclass, not a macro class (see module
    docstring); a subclass with no override for an account falls back to its
    household target (see get_effective_targets())."""
    if class_name not in TARGETABLE_CLASSES:
        raise ValueError(
            f"'{class_name}' is not a recognized macro or asset class. "
            f"Macro classes: {', '.join(MACRO_CLASSES)}. "
            f"Asset classes: {', '.join(ASSET_CLASSES)}"
        )
    if not (0 <= target_pct <= 1):
        raise ValueError("target_pct must be a fraction between 0 and 1 (e.g. 0.60 for 60%)")
    if account_number:
        if class_name not in ASSET_CLASSES:
            raise ValueError(
                f"Per-account targets are only supported for asset subclasses, not macro "
                f"classes -- '{class_name}' is a macro class. Set a household-wide macro "
                "target instead (omit --account), or target one of its subclasses per-account."
            )
        conn.execute(
            "INSERT INTO ips_targets_by_account (account_number, asset_class, target_pct) "
            "VALUES (?, ?, ?) ON CONFLICT(account_number, asset_class) "
            "DO UPDATE SET target_pct=excluded.target_pct",
            (account_number, class_name, target_pct),
        )
    else:
        conn.execute(
            "INSERT INTO ips_targets (asset_class, target_pct) VALUES (?, ?) "
            "ON CONFLICT(asset_class) DO UPDATE SET target_pct=excluded.target_pct",
            (class_name, target_pct),
        )
    conn.commit()


def clear_target(conn, class_name: str, account_number: str = None) -> bool:
    """Removes a previously-set target -- household-wide, or one account's
    override if account_number is given. Returns False if there was nothing
    to remove (already unset)."""
    if class_name not in TARGETABLE_CLASSES:
        raise ValueError(
            f"'{class_name}' is not a recognized macro or asset class. "
            f"Macro classes: {', '.join(MACRO_CLASSES)}. "
            f"Asset classes: {', '.join(ASSET_CLASSES)}"
        )
    if account_number:
        cur = conn.execute(
            "DELETE FROM ips_targets_by_account WHERE account_number = ? AND asset_class = ?",
            (account_number, class_name),
        )
    else:
        cur = conn.execute("DELETE FROM ips_targets WHERE asset_class = ?", (class_name,))
    conn.commit()
    return cur.rowcount > 0


def get_targets(conn):
    """All household-wide targets, macro and subclass both. Most callers
    want get_subclass_targets() or get_macro_targets() instead, to avoid a
    macro target leaking into a subclass-level comparison (or vice versa)."""
    rows = conn.execute(
        "SELECT asset_class, target_pct FROM ips_targets ORDER BY target_pct DESC"
    ).fetchall()
    return {r["asset_class"]: r["target_pct"] for r in rows}


def get_subclass_targets(conn):
    return {k: v for k, v in get_targets(conn).items() if k in ASSET_CLASSES}


def get_macro_targets(conn):
    return {k: v for k, v in get_targets(conn).items() if k in MACRO_CLASSES}


def get_account_target_overrides(conn, account_number: str) -> dict:
    """Just this account's own overrides (not merged with household targets).
    Always subclass-level -- see set_target()."""
    rows = conn.execute(
        "SELECT asset_class, target_pct FROM ips_targets_by_account WHERE account_number = ?",
        (account_number,),
    ).fetchall()
    return {r["asset_class"]: r["target_pct"] for r in rows}


def get_effective_targets(conn, account_number: str):
    """Household subclass targets with this account's overrides applied on
    top. Returns (targets_dict, set_of_overridden_asset_classes)."""
    targets = dict(get_subclass_targets(conn))
    overrides = get_account_target_overrides(conn, account_number)
    targets.update(overrides)
    return targets, set(overrides.keys())


def _compare(targets: dict, actual_by_class: dict, total: float, overridden: set = frozenset()):
    all_classes = sorted(set(targets.keys()) | set(actual_by_class.keys()))
    comparison = []
    for ac in all_classes:
        actual_pct = actual_by_class.get(ac, 0.0)
        target_pct = targets.get(ac, 0.0)
        diff_pct = actual_pct - target_pct
        diff_dollars = diff_pct * total
        if abs(diff_pct) < 0.005:  # within 0.5 percentage points -> call it on target
            action = "On target"
        elif diff_pct > 0:
            action = "Overweight -- trim"
        else:
            action = "Underweight -- add"
        comparison.append({
            "asset_class": ac,
            "actual_pct": actual_pct,
            "target_pct": target_pct,
            "diff_pct": diff_pct,
            "diff_dollars": diff_dollars,
            "action": action,
            "is_override": ac in overridden,
        })
    return comparison


def compare_to_targets(conn, allocation_rows, household_total):
    """
    allocation_rows: output of allocation.allocation_by_asset_class()
    Returns a list of dicts: asset_class, actual_pct, target_pct, diff_pct,
    diff_dollars (positive = overweight / sell, negative = underweight / buy), action
    """
    targets = get_subclass_targets(conn)
    actual_by_class = {r["asset_class"]: r["pct"] for r in allocation_rows}
    return _compare(targets, actual_by_class, household_total)


def compare_to_macro_targets(conn, macro_allocation_rows, household_total):
    """Same shape as compare_to_targets(), but at the macro-class level.
    macro_allocation_rows: output of allocation.allocation_by_macro_class()."""
    targets = get_macro_targets(conn)
    actual_by_class = {r["asset_class"]: r["pct"] for r in macro_allocation_rows}
    return _compare(targets, actual_by_class, household_total)


def compare_to_targets_by_account(conn, by_account_rows):
    """
    by_account_rows: output of allocation.allocation_by_account() (has
    account_number, account_name, asset_class, pct_of_account, account_total).

    Only accounts with at least one per-account target override are
    included -- an account with no overrides compares identically to the
    household-wide comparison already shown, so repeating it here would
    just be noise.

    Returns dict[account_number] -> {"account_name", "account_total",
    "rows": [... same shape as compare_to_targets(), plus "is_override" ...]}
    """
    by_account = defaultdict(list)
    meta = {}
    for r in by_account_rows:
        by_account[r["account_number"]].append(r)
        meta[r["account_number"]] = (r["account_name"], r["account_total"])

    result = {}
    for acct_num, rows in by_account.items():
        overrides = get_account_target_overrides(conn, acct_num)
        if not overrides:
            continue
        targets, overridden = get_effective_targets(conn, acct_num)
        account_name, account_total = meta[acct_num]
        actual_by_class = {r["asset_class"]: r["pct_of_account"] for r in rows}
        result[acct_num] = {
            "account_name": account_name,
            "account_total": account_total,
            "rows": _compare(targets, actual_by_class, account_total, overridden),
        }
    return result
