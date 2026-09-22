"""
Parses a Charles Schwab "Positions" CSV export into pt's canonical
(as_of_date, accounts, holdings) shape -- see pt/importer.py's module
docstring for the shared contract every broker parser follows.

Schwab's export (Accounts > Positions > Export) looks structurally
different from Fidelity's: there is no Account Number *column* at all.
Instead, a file can hold ONE OR MORE accounts, each as its own block --
a quoted title line naming the account, then a column-header row, then
that account's holdings, then an "Account Total" footer row, then
(usually) a blank line before the next account's title line:

    "Positions for Individual XXXX1234 as of 09:12 AM ET, 09/06/2026"

    Symbol,Description,Quantity,Price,Price Change $,Price Change %,Market Value,Day Change $,Day Change %,Cost Basis,Gain/Loss $,Gain/Loss %,Reinvest Dividends?,Capital Gains?,% Of Account,Dividend Yield,Last Dividend,Ex-Dividend Date,P/E Ratio,52 Week Low,52 Week High,Volume,Intrinsic Value,In The Money,Security Type,
    AAPL,APPLE INC,100,$150.00,...
    Cash & Cash Investments,,,,,,,,,,,,,,,,,,,,,,,,
    Account Total,,,,,,,,,,,,,,,,,,,,,,,,

    "Positions for Roth IRA XXXX5678 as of 09:12 AM ET, 09/06/2026"
    Symbol,Description,...
    ...

This format (title line, exact column list, "Account Total" footer,
"Cash & Cash Investments" cash row) is verified against a real working
parser (jbms/beancount-import's schwab_csv.py), not guessed -- Schwab has
been known to tweak the exact optional trailing columns over time, so
this is defensive about extra/missing columns the same way
pt/importer.py is for Fidelity, requiring only the columns it actually
needs.
"""
import csv
import io
import re
from datetime import datetime
from pathlib import Path

from .importer import clean_number, infer_account_type

# The title line naming each account block, e.g.:
#   "Positions for Individual XXXX1234 as of 09:12 AM ET, 09/06/2026"
#   "Positions for Roth IRA XXXX5678 as of 09:12 AM ET, 09/06/2026"
# group("type") feeds infer_account_type() the same way a Fidelity account
# NAME does (it's already keyword-based -- "Roth IRA"/"Individual" text
# matches its existing rules with no changes needed); group("number") is
# the account number. group("date") is this account's own as-of date --
# more reliable than Fidelity's filename-regex fallback, since it comes
# straight from the export itself.
TITLE_RE = re.compile(
    r'^"?Positions for (?P<type>[A-Za-z][A-Za-z\s]*?)\s+(?P<number>\S+)'
    r' as of (?P<time>[^,]+), (?P<date>[\d/]+)"?$'
)

# Canonical field -> possible header spellings, same defensive spirit as
# pt/importer.py's COLUMN_ALIASES -- Schwab has tweaked optional trailing
# columns before; this only requires the ones actually used below.
COLUMN_ALIASES = {
    "symbol": ["Symbol"],
    "description": ["Description"],
    "quantity": ["Quantity"],
    "price": ["Price"],
    "current_value": ["Market Value"],
    "cost_basis_total": ["Cost Basis"],
    "percent_of_account": ["% Of Account"],
}

REQUIRED_FIELDS = ["symbol", "description", "quantity", "current_value"]

# Schwab's own label for uninvested cash -- normalized to the single
# literal symbol CASH (same as E*TRADE's, see pt/importer_etrade.py),
# seeded in data/classifications_seed.csv as 100% the "Cash" subclass so
# it classifies correctly immediately after import, no manual step needed.
CASH_LABEL = "Cash & Cash Investments"
CASH_SYMBOL = "CASH"

# Rows to skip -- the block's own subtotal, not a real holding.
FOOTER_SYMBOLS = {"Account Total"}


class SchwabImportError(Exception):
    pass


def _split_csv_line(line: str) -> list:
    """Splits one already-read line the way csv.reader would (handles a
    quoted field with an embedded comma) -- used because this module reads
    the file as raw lines first, to find each account's title line between
    blocks, rather than one flat DictReader pass over the whole file the
    way pt/importer.py's single-account-shape Fidelity format allows."""
    return next(csv.reader(io.StringIO(line)))


def _build_header_map(fieldnames):
    header_map = {}
    normalized = {h.strip().lower(): h for h in fieldnames if h}
    for canonical, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            if alias.lower() in normalized:
                header_map[canonical] = normalized[alias.lower()]
                break
    return header_map


def _parse_title(line: str):
    """(account_type_text, account_number, as_of_date_or_None) if `line`
    is an account title line, else None."""
    m = TITLE_RE.match(line.strip())
    if not m:
        return None
    try:
        as_of = datetime.strptime(m.group("date"), "%m/%d/%Y").date().isoformat()
    except ValueError:
        as_of = None
    return m.group("type").strip(), m.group("number").strip(), as_of


def parse_schwab_csv(path):
    """
    Returns:
        as_of_date: str or None -- the latest account block's as-of date
        accounts: dict[account_number] -> {"account_name", "account_type"}
        holdings: list of dicts with keys matching the `holdings` table
                  columns (minus snapshot_id, which the caller assigns)

    Raises SchwabImportError if no account title line is found at all, an
    account's own header row is missing a required column, or no holdings
    rows are found anywhere in the file.
    """
    path = Path(path)
    if not path.exists():
        raise SchwabImportError(f"File not found: {path}")

    with open(path, newline="", encoding="utf-8-sig") as f:
        raw_lines = [line.rstrip("\r\n") for line in f]

    accounts = {}
    holdings = []
    as_of_date = None
    current_account = None   # account_number of the block being read
    fieldnames = None        # this block's own header row's column names, in order
    header_map = None        # canonical field -> actual column name, for this block

    for line in raw_lines:
        stripped = line.strip()
        if not stripped:
            continue

        title = _parse_title(stripped)
        if title:
            account_type_text, account_number, block_as_of = title
            if account_type_text.lower().replace("-", " ").strip() == "all accounts":
                # A combined-view wrapper title (e.g. "Positions for
                # All-Accounts as of ...") -- each real account's own
                # title line follows separately; nothing to record here.
                current_account = None
                fieldnames = header_map = None
                continue
            current_account = account_number
            accounts.setdefault(account_number, {
                "account_name": account_type_text,
                "account_type": infer_account_type(account_type_text),
            })
            if block_as_of:
                as_of_date = block_as_of if as_of_date is None else max(as_of_date, block_as_of)
            fieldnames = header_map = None
            continue

        if current_account is None:
            continue  # a stray line before any title -- shouldn't happen in a real export

        if header_map is None:
            fieldnames = _split_csv_line(line)
            header_map = _build_header_map(fieldnames)
            missing = [f for f in REQUIRED_FIELDS if f not in header_map]
            if missing:
                raise SchwabImportError(
                    f"Couldn't find expected columns in '{path.name}' for account {current_account}: "
                    f"{missing}. Found columns: {fieldnames}. Schwab's export format may have changed -- "
                    "update pt/importer_schwab.py COLUMN_ALIASES to match."
                )
            continue

        values = _split_csv_line(line)
        row = dict(zip(fieldnames, values))
        symbol = (row.get(header_map["symbol"]) or "").strip()
        if not symbol or symbol in FOOTER_SYMBOLS:
            continue

        description = (row.get(header_map.get("description", ""), "") or "").strip()
        if symbol == CASH_LABEL:
            symbol = CASH_SYMBOL
            description = description or CASH_LABEL

        holdings.append({
            "account_number": current_account,
            "symbol": symbol,
            "description": description,
            "quantity": clean_number(row.get(header_map.get("quantity", ""))),
            "last_price": clean_number(row.get(header_map.get("price", ""))),
            "current_value": clean_number(row.get(header_map["current_value"])),
            "cost_basis_total": clean_number(row.get(header_map.get("cost_basis_total", ""))),
            "percent_of_account": clean_number(row.get(header_map.get("percent_of_account", ""))),
        })

    if not holdings:
        raise SchwabImportError(
            f"No holdings rows found in '{path.name}'. Is this a Schwab 'Positions' export "
            "(Accounts > Positions > Export)?"
        )

    return as_of_date, accounts, holdings
