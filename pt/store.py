"""Writes a parsed portfolio import into the database as a timestamped snapshot."""
from datetime import datetime, timezone


def save_snapshot(conn, source_file: str, as_of_date, accounts: dict, holdings: list, note: str = None) -> int:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    cur = conn.execute(
        "INSERT INTO snapshots (imported_at, source_file, as_of_date, note) VALUES (?, ?, ?, ?)",
        (now, source_file, as_of_date, note),
    )
    snapshot_id = cur.lastrowid

    for acct_num, info in accounts.items():
        conn.execute(
            "INSERT INTO accounts (account_number, account_name, account_type, account_type_source, owner) "
            "VALUES (?, ?, ?, 'inferred', ?) "
            "ON CONFLICT(account_number) DO UPDATE SET "
            "account_name=excluded.account_name, "
            "owner=COALESCE(excluded.owner, accounts.owner)",
            (acct_num, info["account_name"], info["account_type"], info.get("owner")),
        )
        # Note: account_type / account_type_source are deliberately absent from
        # the UPDATE above -- a manual override (see set_account_type()) must
        # survive every later re-import of the same account.

    for h in holdings:
        conn.execute(
            """
            INSERT INTO holdings
                (snapshot_id, account_number, symbol, description, quantity,
                 last_price, current_value, cost_basis_total, percent_of_account)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot_id, h["account_number"], h["symbol"], h["description"],
                h["quantity"], h["last_price"], h["current_value"],
                h["cost_basis_total"], h["percent_of_account"],
            ),
        )

    conn.commit()
    return snapshot_id


def list_snapshots(conn):
    return conn.execute(
        "SELECT id, imported_at, source_file, as_of_date, note FROM snapshots ORDER BY imported_at DESC"
    ).fetchall()


def list_accounts(conn):
    return conn.execute(
        "SELECT account_number, account_name, account_type, account_type_source, owner, expense_ratio "
        "FROM accounts ORDER BY account_name, account_number"
    ).fetchall()


def get_account(conn, account_number: str):
    return conn.execute(
        "SELECT account_number, account_name, account_type, account_type_source, owner, expense_ratio "
        "FROM accounts WHERE account_number = ?",
        (account_number,),
    ).fetchone()


def set_account_type(conn, account_number: str, account_type: str) -> bool:
    """Manually override an account's type. Returns False if the account
    number isn't known yet (import it first)."""
    cur = conn.execute(
        "UPDATE accounts SET account_type = ?, account_type_source = 'manual' WHERE account_number = ?",
        (account_type, account_number),
    )
    conn.commit()
    return cur.rowcount > 0


def set_account_expense_ratio(conn, account_number: str, expense_ratio_pct: float) -> bool:
    """A flat account-level fee (e.g. a managed account's advisory fee),
    ADDED TO (not replacing) its holdings' own per-symbol expense ratios
    when the report totals expenses -- see pt/allocation.py's
    expense_summary(). expense_ratio_pct is a percentage, e.g. 1.0 for
    1.00%/year -- not a 0-1 fraction, same convention as
    attributes.set_expense_ratio(). Returns False if the account number
    isn't known yet (import it first)."""
    if not (0 <= expense_ratio_pct <= 25):
        raise ValueError(
            "Expense ratio should be a percentage, e.g. 1.0 for 1.00% or 0.75 for 0.75% "
            f"(got {expense_ratio_pct}, which is outside a plausible 0-25% range)."
        )
    cur = conn.execute(
        "UPDATE accounts SET expense_ratio = ? WHERE account_number = ?",
        (expense_ratio_pct / 100.0, account_number),
    )
    conn.commit()
    return cur.rowcount > 0
