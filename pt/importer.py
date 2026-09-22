"""
Parses brokerage CSV exports into pt's canonical (as_of_date, accounts,
holdings) shape -- see save_snapshot() (pt/store.py) for what those two
dicts/lists need to look like. Everything downstream of import (asset
classification, allocation, reports, projections) only ever sees that
canonical shape and has no idea which broker a holding came from.

This module itself parses Fidelity's "Portfolio Positions" CSV export.
Other brokers get their own sibling module (same parse_<broker>_csv()
contract -- returns (as_of_date, accounts, holdings), raises a broker-
specific *ImportError): pt/importer_schwab.py, pt/importer_etrade.py.
ACCOUNT_TYPES, infer_account_type(), and clean_number() below are shared
by all of them (imported from here, not duplicated) since none of that is
actually Fidelity-specific. detect_broker()/parse_csv()/
parse_multiple_csvs() are the broker-agnostic entry points pt.cli import
actually calls -- see their docstrings.

Fidelity's export (Accounts > Positions > Download) typically has columns like:
  Account Number, Account Name, Symbol, Description, Quantity, Last Price,
  Last Price Change, Current Value, Today's Gain/Loss Dollar,
  Today's Gain/Loss Percent, Total Gain/Loss Dollar, Total Gain/Loss Percent,
  Percent Of Account, Cost Basis Total, Average Cost Basis, Type

...followed by several blank/disclaimer lines at the bottom of the file.
This module is defensive about column-name variants and about that footer junk,
since Fidelity has changed exact wording/columns over time.
"""
import csv
import re
from datetime import datetime
from pathlib import Path

# Canonical column name -> list of possible header spellings we've seen from Fidelity
COLUMN_ALIASES = {
    "account_number": ["Account Number"],
    "account_name": ["Account Name"],
    "symbol": ["Symbol"],
    "description": ["Description"],
    "quantity": ["Quantity"],
    "last_price": ["Last Price"],
    "current_value": ["Current Value"],
    "percent_of_account": ["Percent Of Account", "Percent of Account"],
    "cost_basis_total": ["Cost Basis Total"],
}

REQUIRED_FIELDS = ["account_number", "account_name", "symbol", "current_value"]

# Recognized account types, in the order infer_account_type() checks them
# (most specific first). Also the valid values for `accounts set-type`.
ACCOUNT_TYPES = [
    "Inherited Roth IRA",
    "Inherited IRA",
    "Roth IRA",
    "IRA",
    "401(k)",
    "HSA",
    "Brokerage",
]


class FidelityImportError(Exception):
    pass


def clean_number(raw):
    """Turn '$1,234.56', '1.23%', '--', 'n/a', '' into a float or None."""
    if raw is None:
        return None
    s = str(raw).strip()
    if s in ("", "--", "n/a", "N/A", "-"):
        return None
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    s = s.replace("$", "").replace(",", "").replace("%", "").strip()
    if s in ("", "-"):
        return None
    try:
        val = float(s)
    except ValueError:
        return None
    return -val if neg else val


def _build_header_map(fieldnames):
    """Map canonical field -> actual CSV column name present in this file."""
    header_map = {}
    normalized = {h.strip().lower(): h for h in fieldnames if h}
    for canonical, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            if alias.lower() in normalized:
                header_map[canonical] = normalized[alias.lower()]
                break
    return header_map


def infer_account_type(account_name: str) -> str:
    """Classify a Fidelity account based on the text of its account name.

    Fidelity account names vary by household (e.g. 'ROTH IRA', 'ROLLOVER IRA',
    'JOHN SMITH - INDIVIDUAL', 'INHERITED IRA BDA'). We match on keywords,
    checking the more specific ones (inherited) before the general ones.

    This is a best-effort guess -- an account whose name gives no hint (e.g.
    an employer 401(k) plan named after the company) will fall through to
    'Brokerage'. Fix a wrong guess after import with:
        python3 -m pt.cli accounts set-type ACCOUNT_NUMBER "401(k)"
    which persists across future imports of that account (see store.py:
    account_type is never overwritten by a later import once set).
    """
    name = (account_name or "").upper()

    is_inherited = "INHERIT" in name or " BDA" in name or "BENE" in name
    is_roth = "ROTH" in name

    if is_inherited and is_roth:
        return "Inherited Roth IRA"
    if is_inherited:
        return "Inherited IRA"
    if is_roth:
        return "Roth IRA"
    if "IRA" in name or "ROLLOVER" in name or "SEP" in name or "SIMPLE" in name:
        return "IRA"
    if "401K" in name or "401(K)" in name:
        return "401(k)"
    if "HSA" in name:
        return "HSA"
    return "Brokerage"


def parse_fidelity_csv(path: Path):
    """
    Returns:
        as_of_date: str or None -- date embedded in the filename, if we can find one
        accounts: dict[account_number] -> {"account_name": ..., "account_type": ...}
        holdings: list of dicts with keys matching the `holdings` table columns
                  (minus snapshot_id, which the caller assigns)
    """
    path = Path(path)
    if not path.exists():
        raise FidelityImportError(f"File not found: {path}")

    # Fidelity filenames often look like Portfolio_Positions_Aug-22-2026.csv
    as_of_date = None
    m = re.search(r"(\w{3}-\d{1,2}-\d{4})", path.name)
    if m:
        try:
            as_of_date = datetime.strptime(m.group(1), "%b-%d-%Y").date().isoformat()
        except ValueError:
            pass

    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise FidelityImportError("CSV has no header row.")
        header_map = _build_header_map(reader.fieldnames)

        missing = [f for f in REQUIRED_FIELDS if f not in header_map]
        if missing:
            raise FidelityImportError(
                "Couldn't find expected columns in this CSV: "
                f"{missing}. Found columns: {reader.fieldnames}. "
                "Fidelity's export format may have changed -- update "
                "pt/importer.py COLUMN_ALIASES to match."
            )

        accounts = {}
        holdings = []

        for row in reader:
            acct_num = (row.get(header_map["account_number"]) or "").strip()
            acct_name = (row.get(header_map["account_name"]) or "").strip()
            symbol = (row.get(header_map["symbol"]) or "").strip()

            # Footer/disclaimer rows and blank rows have no account number or symbol
            if not acct_num or not symbol:
                continue
            # Fidelity sometimes inserts a "Pending Activity" pseudo-row per account
            if symbol.upper() in ("PENDING ACTIVITY",):
                continue

            if acct_num not in accounts:
                accounts[acct_num] = {
                    "account_name": acct_name,
                    "account_type": infer_account_type(acct_name),
                }

            holdings.append({
                "account_number": acct_num,
                "symbol": symbol,
                "description": (row.get(header_map.get("description", ""), "") or "").strip(),
                "quantity": clean_number(row.get(header_map.get("quantity", ""))),
                "last_price": clean_number(row.get(header_map.get("last_price", ""))),
                "current_value": clean_number(row.get(header_map["current_value"])),
                "cost_basis_total": clean_number(row.get(header_map.get("cost_basis_total", ""))),
                "percent_of_account": clean_number(row.get(header_map.get("percent_of_account", ""))),
            })

    if not holdings:
        raise FidelityImportError(
            "No holdings rows found. Is this a Fidelity 'Portfolio Positions' "
            "download? (Accounts & Trade > Positions > Download)"
        )

    return as_of_date, accounts, holdings


def _peek_lines(path, count=8):
    """First `count` non-blank lines of a file, for detect_broker() to
    sniff -- cheap (small, fixed read), doesn't touch the real csv.reader
    pass each parser does afterward."""
    lines = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if line:
                lines.append(line)
            if len(lines) >= count:
                break
    return lines


def detect_broker(path) -> str:
    """Sniffs a CSV's first few lines to guess which broker produced it --
    "fidelity", "schwab", or "etrade". Each format has a distinctive,
    unambiguous signal (see the sibling parser modules' own docstrings for
    exactly what a real export looks like):
      fidelity -- a header row containing "Account Number"
      schwab   -- a first line matching Positions for ... as of ...
      etrade   -- a header row containing both "Qty #" and "Value $"
    Raises FidelityImportError (reused here as the generic "couldn't tell
    what this file is" error, since it's the error pt.cli import already
    catches) if none match -- the message points at --broker to override
    detection by hand."""
    lines = _peek_lines(path)
    if not lines:
        raise FidelityImportError(f"'{path}' looks empty -- no lines to read.")

    if re.match(r'^"?Positions for .+ as of .+"?$', lines[0]):
        return "schwab"
    for line in lines:
        lower = line.lower()
        if "account number" in lower and "symbol" in lower:
            return "fidelity"
        if "qty #" in lower and "value $" in lower:
            return "etrade"
    raise FidelityImportError(
        f"Couldn't tell which broker '{Path(path).name}' is from (checked Fidelity/Schwab/E*TRADE's "
        f"known formats). First line: {lines[0]!r}. Pass --broker {{fidelity,schwab,etrade}} to skip "
        "detection and say which one it is."
    )


def parse_csv(path, account_number: str = None, broker: str = None):
    """The one broker-agnostic entry point pt.cli import actually calls per
    file: detects (or uses the given `broker` override) which format
    `path` is, dispatches to that broker's own parse_<broker>_csv(), and
    returns (broker, as_of_date, accounts, holdings) -- the broker name is
    echoed back so callers/error messages can say which parser ran.
    account_number: only used (and required) for etrade, whose export
    never reveals its own account number -- see pt/importer_etrade.py.
    Imports the sibling modules lazily (inside this function, not at
    module load time) to avoid a circular import, since they in turn
    import ACCOUNT_TYPES/infer_account_type/clean_number from here."""
    broker = broker or detect_broker(path)
    if broker == "fidelity":
        as_of_date, accounts, holdings = parse_fidelity_csv(path)
    elif broker == "schwab":
        from . import importer_schwab
        as_of_date, accounts, holdings = importer_schwab.parse_schwab_csv(path)
    elif broker == "etrade":
        from . import importer_etrade
        as_of_date, accounts, holdings = importer_etrade.parse_etrade_csv(path, account_number)
    else:
        raise FidelityImportError(f"Unknown broker '{broker}' -- must be one of: fidelity, schwab, etrade.")
    return broker, as_of_date, accounts, holdings


def parse_multiple_csvs(path_owner_pairs, account_numbers: dict = None, broker_overrides: dict = None):
    """
    Parses several brokerage CSVs (any mix of Fidelity/Schwab/E*TRADE --
    see parse_csv()) and merges them into one combined (as_of_date,
    accounts, holdings) tuple, for the case where a household's accounts
    are split across more than one export (e.g. different brokers, or two
    logins for the same broker, one per spouse).

    path_owner_pairs: list of (path, owner_or_None) tuples. `owner` is a
    free-form string (e.g. "Jane" -- ideally matching a name under the
    retirement planning profile's `people` section, see pt/planning.py)
    recorded against every account found in that file.

    account_numbers: optional {basename(path): account_number} -- required
    for an E*TRADE file (see pt/importer_etrade.py), since that format
    never reveals its own account number; ignored for every other broker.

    broker_overrides: optional {basename(path): "fidelity"|"schwab"|
    "etrade"} -- skips detect_broker() for that file, for the rare case
    auto-detection guesses wrong.

    If the same account number appears in more than one file, only the
    *first* file's holdings for that account are kept -- later occurrences'
    holdings are skipped, on the assumption they're a duplicate/overlapping
    export rather than a second account that happens to share a number.
    However, an account appearing in more than one file usually means it's
    genuinely shared (e.g. a joint brokerage account visible in both
    spouses' exports), so its owner is set to "Joint" regardless of what
    owner was given for either file.

    Returns:
        as_of_date: str or None -- the most recent as_of_date found across files
        accounts: dict[account_number] -> {"account_name", "account_type", "owner"}
        holdings: list of holding dicts (duplicates excluded)
        skipped: list of dicts {"account_number", "account_name", "source_file"}
                 describing which accounts had holdings ignored, and from which file
    """
    account_numbers = account_numbers or {}
    broker_overrides = broker_overrides or {}
    combined_accounts = {}
    combined_holdings = []
    skipped = []
    as_of_date = None
    seen_accounts = set()

    for path, owner in path_owner_pairs:
        basename = Path(path).name
        _broker, file_as_of_date, accounts, holdings = parse_csv(
            path, account_number=account_numbers.get(basename), broker=broker_overrides.get(basename),
        )
        if file_as_of_date:
            as_of_date = file_as_of_date if as_of_date is None else max(as_of_date, file_as_of_date)

        file_dupe_accounts = set()
        for acct_num, info in accounts.items():
            if acct_num in seen_accounts:
                file_dupe_accounts.add(acct_num)
                skipped.append({
                    "account_number": acct_num,
                    "account_name": info["account_name"],
                    "source_file": basename,
                })
                # Seen in more than one file -> treat as a shared/joint account,
                # regardless of either file's specified owner.
                combined_accounts[acct_num]["owner"] = "Joint"
            else:
                seen_accounts.add(acct_num)
                combined_accounts[acct_num] = {
                    "account_name": info["account_name"],
                    "account_type": info["account_type"],
                    "owner": owner,
                }

        for h in holdings:
            if h["account_number"] in file_dupe_accounts:
                continue
            combined_holdings.append(h)

    return as_of_date, combined_accounts, combined_holdings, skipped
