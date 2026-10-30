"""Computes household-level asset allocation from a stored snapshot."""
from collections import defaultdict

from . import attributes
from .classifier import SUBCLASS_MACRO, UNCLASSIFIED, get_symbol_weights


def get_latest_snapshot_id(conn):
    row = conn.execute(
        "SELECT id FROM snapshots ORDER BY imported_at DESC LIMIT 1"
    ).fetchone()
    return row["id"] if row else None


def household_total_value(conn, snapshot_id: int) -> float:
    row = conn.execute(
        "SELECT SUM(current_value) AS total FROM holdings WHERE snapshot_id = ?",
        (snapshot_id,),
    ).fetchone()
    return row["total"] or 0.0


def allocation_by_asset_class(conn, snapshot_id: int):
    """Returns list of rows: asset_class, value, pct_of_household.
    A holding classified as a Fund (multiple asset classes) has its value
    split across classes according to its stored weights."""
    total = household_total_value(conn, snapshot_id)
    holdings = conn.execute(
        "SELECT symbol, current_value FROM holdings WHERE snapshot_id = ?",
        (snapshot_id,),
    ).fetchall()

    totals = defaultdict(float)
    for h in holdings:
        value = h["current_value"] or 0.0
        for asset_class, weight in get_symbol_weights(conn, h["symbol"]).items():
            totals[asset_class] += value * weight

    result = [
        {"asset_class": ac, "value": v, "pct": (v / total if total else 0.0)}
        for ac, v in totals.items()
    ]
    result.sort(key=lambda r: -r["value"])
    return result, total


def allocation_by_macro_class(conn, snapshot_id: int):
    """Same shape as allocation_by_asset_class(), but rolled up to macro
    class (see classifier.MACRO_CLASSES) via SUBCLASS_MACRO. A
    subclass with no macro (Unclassified, or a Fund's "Other" remainder --
    see classifier.OTHER) rolls up under its own name unchanged, so no value
    silently disappears from the total.

    Returns (rows, household_total) -- same pair shape as
    allocation_by_asset_class(), so callers (e.g. ips.compare_to_targets())
    work with either one."""
    subclass_rows, total = allocation_by_asset_class(conn, snapshot_id)
    return _macro_rollup(subclass_rows, total), total


def _macro_rollup(subclass_rows, total):
    """allocation_by_macro_class()'s rollup of allocation_by_asset_class()-
    shaped rows. A name that's already a macro (a hypothetical account's
    asset_classes may name one directly) maps to itself."""
    totals = defaultdict(float)
    for r in subclass_rows:
        macro = SUBCLASS_MACRO.get(r["asset_class"], r["asset_class"])
        totals[macro] += r["value"]

    result = [
        {"asset_class": macro, "value": v, "pct": (v / total if total else 0.0)}
        for macro, v in totals.items()
    ]
    result.sort(key=lambda r: -r["value"])
    return result


def account_values(conn, snapshot_id: int):
    """One row per account: account_number, account_type, owner, value.
    Used by pt/projection.py to sort accounts into tax-treatment buckets
    (taxable/Traditional/Roth/inherited) by account_type and owner."""
    rows = conn.execute(
        """
        SELECT a.account_number, a.account_type, a.owner, SUM(h.current_value) AS value
        FROM accounts a
        JOIN holdings h ON h.account_number = a.account_number
        WHERE h.snapshot_id = ?
        GROUP BY a.account_number
        """,
        (snapshot_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def allocation_by_account_type(conn, snapshot_id: int):
    total = household_total_value(conn, snapshot_id)
    rows = conn.execute(
        """
        SELECT a.account_type AS account_type, SUM(h.current_value) AS value
        FROM holdings h
        JOIN accounts a ON a.account_number = h.account_number
        WHERE h.snapshot_id = ?
        GROUP BY a.account_type
        ORDER BY value DESC
        """,
        (snapshot_id,),
    ).fetchall()
    result = []
    for r in rows:
        pct = (r["value"] / total) if total else 0.0
        result.append({"account_type": r["account_type"], "value": r["value"], "pct": pct})
    return result, total


def allocation_by_account(conn, snapshot_id: int):
    """Per specific account (not account type), asset class breakdown --
    both the dollar amount and the % that asset class makes up *within
    that account*. Fund holdings are split across classes by weight, same
    as allocation_by_asset_class().

    Grouped by account_number (not account_name) throughout, so two
    different accounts that happen to share a name (e.g. both spouses
    having a "Rollover IRA") are never merged together.

    Returns list of dicts: account_number, account_name, account_type,
    owner, asset_class, value, pct_of_account -- sorted by account name,
    then account number (for a stable order when names collide), then by
    value descending within each account.
    """
    holdings = conn.execute(
        """
        SELECT h.account_number, a.account_name, a.account_type, a.owner,
               h.symbol, h.current_value
        FROM holdings h
        JOIN accounts a ON a.account_number = h.account_number
        WHERE h.snapshot_id = ?
        """,
        (snapshot_id,),
    ).fetchall()

    class_totals = defaultdict(float)   # (account_number, asset_class) -> value
    account_totals = defaultdict(float)  # account_number -> value
    account_meta = {}

    for h in holdings:
        value = h["current_value"] or 0.0
        acct_num = h["account_number"]
        account_totals[acct_num] += value
        account_meta[acct_num] = (h["account_name"], h["account_type"], h["owner"])
        for asset_class, weight in get_symbol_weights(conn, h["symbol"]).items():
            class_totals[(acct_num, asset_class)] += value * weight

    rows = []
    for (acct_num, asset_class), value in class_totals.items():
        account_name, account_type, owner = account_meta[acct_num]
        acct_total = account_totals[acct_num]
        pct_of_account = (value / acct_total) if acct_total else 0.0
        rows.append({
            "account_number": acct_num,
            "account_name": account_name,
            "account_type": account_type,
            "owner": owner,
            "asset_class": asset_class,
            "value": value,
            "pct_of_account": pct_of_account,
        })

    for row in rows:
        row["account_total"] = account_totals[row["account_number"]]

    rows.sort(key=lambda r: (r["account_name"], r["account_number"], -r["value"]))
    return rows


def accounts_by_account_type(conn, snapshot_id: int):
    """Debug helper for allocation_by_account_type(): for each account type,
    the individual accounts (and their dollar values) that sum to that
    type's total. Lets you see e.g. exactly which accounts are being counted
    as "Brokerage" when that total looks off.

    Returns dict[account_type] -> list of {account_number, account_name,
    owner, value, account_type_source}, each list sorted by value descending.
    account_type_source is 'manual' if it was set with `accounts set-type`,
    'inferred' if it came from the account name (see importer.infer_account_type).
    """
    rows = conn.execute(
        """
        SELECT a.account_type AS account_type, a.account_number, a.account_name,
               a.owner, a.account_type_source, SUM(h.current_value) AS value
        FROM holdings h
        JOIN accounts a ON a.account_number = h.account_number
        WHERE h.snapshot_id = ?
        GROUP BY a.account_type, a.account_number
        ORDER BY a.account_type, value DESC
        """,
        (snapshot_id,),
    ).fetchall()

    result = defaultdict(list)
    for r in rows:
        result[r["account_type"]].append({
            "account_number": r["account_number"],
            "account_name": r["account_name"],
            "owner": r["owner"],
            "value": r["value"],
            "account_type_source": r["account_type_source"],
        })
    return result


def expense_summary(conn, snapshot_id: int):
    """Per-account (and household-total) calculated expense ratio and
    annual dollar cost -- combines each holding's own per-symbol expense
    ratio (attributes.get_expense_ratio(), weighted by that holding's
    value) with the account's own flat expense_ratio if set (`accounts
    set-expense-ratio` -- e.g. a managed account's advisory fee), which
    ADDS TO the holdings' own ratios rather than replacing them.

    A holding with no expense ratio set on file contributes $0 to the
    numerator, but its value still counts in the denominator -- so an
    account's blended_ratio UNDERSTATES the true cost whenever some of
    its holdings have no expense ratio set. missing_value tracks how much
    of that account's value is affected, so the report can flag it
    honestly (see pt/report.py) instead of presenting an unqualified
    number as if it were complete.

    Returns (accounts, totals):
      accounts -- list of dicts, one per account with at least one
        holding, sorted by account_name then account_number:
        {account_number, account_name, account_type, owner, value,
         account_fee_pct (the account's own flat rate, or None),
         account_fee_dollars, holdings_expense_dollars,
         total_expense_dollars, blended_ratio, missing_value}
      totals -- the household-wide equivalent: {value,
        total_expense_dollars, blended_ratio, missing_value}
    """
    holdings = conn.execute(
        """
        SELECT h.account_number, a.account_name, a.account_type, a.owner,
               a.expense_ratio AS account_fee_pct, h.symbol, h.current_value
        FROM holdings h
        JOIN accounts a ON a.account_number = h.account_number
        WHERE h.snapshot_id = ?
        """,
        (snapshot_id,),
    ).fetchall()

    by_account = {}
    for h in holdings:
        acct_num = h["account_number"]
        acc = by_account.setdefault(acct_num, {
            "account_number": acct_num,
            "account_name": h["account_name"],
            "account_type": h["account_type"],
            "owner": h["owner"],
            "account_fee_pct": h["account_fee_pct"],
            "value": 0.0,
            "holdings_expense_dollars": 0.0,
            "missing_value": 0.0,
        })
        value = h["current_value"] or 0.0
        acc["value"] += value
        symbol_ratio = attributes.get_expense_ratio(conn, h["symbol"])
        if symbol_ratio is not None:
            acc["holdings_expense_dollars"] += value * symbol_ratio
        else:
            acc["missing_value"] += value

    accounts = []
    total_value = 0.0
    total_expense = 0.0
    total_missing = 0.0
    for acc in by_account.values():
        account_fee_dollars = acc["value"] * acc["account_fee_pct"] if acc["account_fee_pct"] else 0.0
        total_expense_dollars = acc["holdings_expense_dollars"] + account_fee_dollars
        blended_ratio = (total_expense_dollars / acc["value"]) if acc["value"] else 0.0
        accounts.append({
            **acc,
            "account_fee_dollars": account_fee_dollars,
            "total_expense_dollars": total_expense_dollars,
            "blended_ratio": blended_ratio,
        })
        total_value += acc["value"]
        total_expense += total_expense_dollars
        total_missing += acc["missing_value"]

    accounts.sort(key=lambda a: (a["account_name"], a["account_number"]))
    totals = {
        "value": total_value,
        "total_expense_dollars": total_expense,
        "blended_ratio": (total_expense / total_value) if total_value else 0.0,
        "missing_value": total_missing,
    }
    return accounts, totals


def account_list(conn, snapshot_id: int):
    """One row per account, largest first: account_number, account_name,
    account_type, owner, value -- the report's Accounts tab."""
    rows = conn.execute(
        """
        SELECT a.account_number, a.account_name, a.account_type, a.owner,
               SUM(h.current_value) AS value
        FROM accounts a
        JOIN holdings h ON h.account_number = a.account_number
        WHERE h.snapshot_id = ?
        GROUP BY a.account_number
        ORDER BY value DESC
        """,
        (snapshot_id,),
    ).fetchall()
    return [dict(r) for r in rows]


# ------------------------------------------------------- household views
#
# The report (pt/report.py) renders the same handful of allocation views
# whether the household is a real imported snapshot or a scenario's
# hypothetical_accounts. Both functions below return a dict with the same
# keys, each matching the shape of the snapshot function named:
#   alloc_rows, total   allocation_by_asset_class()
#   macro_rows          allocation_by_macro_class()
#   acct_type_rows      allocation_by_account_type()
#   by_account          allocation_by_account()
#   by_type             accounts_by_account_type()
#   accounts            account_list()
#   hypothetical        True for hypothetical_views()

def snapshot_views(conn, snapshot_id: int) -> dict:
    alloc_rows, total = allocation_by_asset_class(conn, snapshot_id)
    acct_type_rows, _ = allocation_by_account_type(conn, snapshot_id)
    return {
        "alloc_rows": alloc_rows,
        "total": total,
        "macro_rows": _macro_rollup(alloc_rows, total),
        "acct_type_rows": acct_type_rows,
        "by_account": allocation_by_account(conn, snapshot_id),
        "by_type": accounts_by_account_type(conn, snapshot_id),
        "accounts": account_list(conn, snapshot_id),
        "hypothetical": False,
    }


def hypothetical_views(account_rows, class_rows) -> dict:
    """The same views from projection._parse_hypothetical_accounts()'s
    (account_rows, class_rows) -- no database involved. A hypothetical
    account has no name, so its account_number stands in for one."""
    total = sum(a["value"] for a in account_rows)

    def share(value):
        return value / total if total else 0.0

    class_totals = defaultdict(float)
    for r in class_rows:
        class_totals[r["asset_class"]] += r["value"]
    alloc_rows = sorted(
        ({"asset_class": ac, "value": v, "pct": share(v)} for ac, v in class_totals.items()),
        key=lambda r: -r["value"],
    )

    type_totals = defaultdict(float)
    by_type = defaultdict(list)
    for a in sorted(account_rows, key=lambda a: -a["value"]):
        type_totals[a["account_type"]] += a["value"]
        by_type[a["account_type"]].append({
            "account_number": a["account_number"], "account_name": a["account_number"],
            "owner": a["owner"], "value": a["value"], "account_type_source": "scenario",
        })
    acct_type_rows = sorted(
        ({"account_type": t, "value": v, "pct": share(v)} for t, v in type_totals.items()),
        key=lambda r: -r["value"],
    )

    account_totals = {a["account_number"]: a["value"] for a in account_rows}
    by_account = [
        {
            "account_number": r["account_number"], "account_name": r["account_number"],
            "account_type": r["account_type"], "owner": r["owner"], "asset_class": r["asset_class"],
            "value": r["value"],
            "pct_of_account": (r["value"] / account_totals[r["account_number"]]
                               if account_totals[r["account_number"]] else 0.0),
            "account_total": account_totals[r["account_number"]],
        }
        for r in class_rows
    ]
    by_account.sort(key=lambda r: (r["account_name"], r["account_number"], -r["value"]))

    accounts = sorted(
        ({"account_number": a["account_number"], "account_name": a["account_number"],
          "account_type": a["account_type"], "owner": a["owner"], "value": a["value"]}
         for a in account_rows),
        key=lambda a: -a["value"],
    )
    return {
        "alloc_rows": alloc_rows,
        "total": total,
        "macro_rows": _macro_rollup(alloc_rows, total),
        "acct_type_rows": acct_type_rows,
        "by_account": by_account,
        "by_type": by_type,
        "accounts": accounts,
        "hypothetical": True,
    }
