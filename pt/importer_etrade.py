"""
Parses an E*TRADE "Positions" CSV export into pt's canonical (as_of_date,
accounts, holdings) shape -- see pt/importer.py's module docstring for
the shared contract every broker parser follows.

E*TRADE's export (Portfolios > Positions, download) is the simplest of
the three formats this tool supports, but also the most limited: it's
ALWAYS exactly one account per file, and the file never reveals which
account that is anywhere in it -- no account number, no account name, no
account-type hint. This is verified against a real working parser
(gmadrid/etrade-csv-tool), not guessed. Because of that, parse_etrade_csv()
requires the caller to supply `account_number` explicitly (pt.cli import's
--account flag) -- there's no way to proceed without it.

Header (confirmed real, in this order):
    Symbol,Last Price $,Change $,Change %,Day's Gain $,Qty #,Price Paid $,Total Gain $,Total Gain %,Value $

Notably absent compared to Fidelity/Schwab: no Description column (left
blank here -- already an optional field everywhere else in pt), and no
total-cost-basis column, only Price Paid $ (a PER-SHARE cost) -- this
module computes cost_basis_total = quantity * price_paid when both parse
cleanly, rather than leaving it blank whenever it doesn't have to.

The cash position appears as a row with Symbol "CASH" -- normalized to
the same literal CASH symbol Schwab's "Cash & Cash Investments" row uses
(see pt/importer_schwab.py), seeded in data/classifications_seed.csv as
100% the "Cash" subclass.
"""
from pathlib import Path

from .importer import clean_number, infer_account_type

COLUMN_ALIASES = {
    "symbol": ["Symbol"],
    "last_price": ["Last Price $"],
    "quantity": ["Qty #"],
    "price_paid": ["Price Paid $"],
    "current_value": ["Value $"],
}

REQUIRED_FIELDS = ["symbol", "quantity", "current_value"]

CASH_SYMBOL = "CASH"


class EtradeImportError(Exception):
    pass


def _build_header_map(fieldnames):
    header_map = {}
    normalized = {h.strip().lower(): h for h in fieldnames if h}
    for canonical, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            if alias.lower() in normalized:
                header_map[canonical] = normalized[alias.lower()]
                break
    return header_map


def parse_etrade_csv(path, account_number: str):
    """
    account_number: required -- see module docstring for why E*TRADE's own
    export can't supply this. Also doubles as this account's account_name
    and feeds infer_account_type() -- since there's no separate account-
    name text to infer from either, this will usually fall through to
    "Brokerage" unless account_number itself happens to contain a hint
    (e.g. "Roth IRA X1234"); fix a wrong guess after import the same way
    as any other account: `pt.cli accounts set-type ACCOUNT_NUMBER TYPE`.

    Returns:
        as_of_date: None -- E*TRADE's export doesn't embed one anywhere
            (unlike Fidelity's filename convention or Schwab's title
            line); the snapshot's own imported_at timestamp is the only
            date pt has for this file.
        accounts: dict[account_number] -> {"account_name", "account_type"}
        holdings: list of dicts with keys matching the `holdings` table
                  columns (minus snapshot_id, which the caller assigns)

    Raises EtradeImportError if account_number is empty/None, the file's
    header is missing a required column, or no holdings rows are found.
    """
    if not (account_number or "").strip():
        raise EtradeImportError(
            "An E*TRADE export doesn't include its own account number -- pass one explicitly, "
            "e.g.: pt.cli import --account "
            f"{Path(path).name}:YOUR_ACCOUNT_NUMBER {path}"
        )
    account_number = account_number.strip()

    path = Path(path)
    if not path.exists():
        raise EtradeImportError(f"File not found: {path}")

    import csv
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise EtradeImportError("CSV has no header row.")
        header_map = _build_header_map(reader.fieldnames)

        missing = [f for f in REQUIRED_FIELDS if f not in header_map]
        if missing:
            raise EtradeImportError(
                "Couldn't find expected columns in this CSV: "
                f"{missing}. Found columns: {reader.fieldnames}. "
                "E*TRADE's export format may have changed -- update "
                "pt/importer_etrade.py COLUMN_ALIASES to match."
            )

        holdings = []
        for row in reader:
            symbol = (row.get(header_map["symbol"]) or "").strip()
            if not symbol:
                continue  # blank/footer/disclaimer row

            if symbol.upper() == CASH_SYMBOL:
                symbol = CASH_SYMBOL
                description = "Cash"
            else:
                description = ""

            quantity = clean_number(row.get(header_map.get("quantity", "")))
            price_paid = clean_number(row.get(header_map.get("price_paid", "")))
            cost_basis_total = quantity * price_paid if quantity is not None and price_paid is not None else None

            holdings.append({
                "account_number": account_number,
                "symbol": symbol,
                "description": description,
                "quantity": quantity,
                "last_price": clean_number(row.get(header_map.get("last_price", ""))),
                "current_value": clean_number(row.get(header_map["current_value"])),
                "cost_basis_total": cost_basis_total,
                "percent_of_account": None,  # not reported by this export
            })

    if not holdings:
        raise EtradeImportError(
            f"No holdings rows found in '{path.name}'. Is this an E*TRADE 'Positions' export "
            "(Portfolios > Positions > download)?"
        )

    inferred_type = infer_account_type(account_number)
    accounts = {account_number: {"account_name": account_number, "account_type": inferred_type}}
    return None, accounts, holdings
