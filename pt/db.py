"""SQLite schema and connection handling for the portfolio tool."""
import sqlite3
from pathlib import Path

DEFAULT_DB_PATH = Path.home() / ".portfolio_tool" / "portfolio.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    imported_at TEXT NOT NULL,       -- ISO timestamp of when we ran the import
    source_file TEXT NOT NULL,       -- original CSV filename
    as_of_date TEXT,                 -- date Fidelity generated the export, if known
    note TEXT
);

CREATE TABLE IF NOT EXISTS accounts (
    account_number TEXT PRIMARY KEY,
    account_name TEXT NOT NULL,      -- raw name as it appears in Fidelity export
    account_type TEXT NOT NULL,      -- Brokerage | IRA | Roth IRA | Inherited IRA | Inherited Roth IRA | Other
    account_type_source TEXT NOT NULL DEFAULT 'inferred',  -- 'inferred' | 'manual' (see accounts set-type)
    owner TEXT,                      -- optional, for household-level grouping later
    expense_ratio REAL               -- optional flat account-level fee (e.g. a managed
                                      -- account's advisory fee), stored as a fraction of
                                      -- the account's value (e.g. 0.01 for 1.00%/year) --
                                      -- see accounts set-expense-ratio. ADDS TO, doesn't
                                      -- replace, its holdings' own per-symbol expense
                                      -- ratios (security_metadata.expense_ratio) when the
                                      -- report totals expenses.
);

CREATE TABLE IF NOT EXISTS holdings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id),
    account_number TEXT NOT NULL REFERENCES accounts(account_number),
    symbol TEXT NOT NULL,
    description TEXT,
    quantity REAL,
    last_price REAL,
    current_value REAL,
    cost_basis_total REAL,
    percent_of_account REAL
);

CREATE TABLE IF NOT EXISTS classifications (
    symbol TEXT PRIMARY KEY,
    asset_class TEXT NOT NULL,       -- US Equity | Intl Equity | US Bond | Intl Bond | Cash/Fixed | Other
    description TEXT,
    notes TEXT
);

-- A symbol classified as a "Fund" (multiple asset classes) has one row per
-- asset class here instead of a single row in `classifications`. Weights are
-- fractions of that holding's value and should sum to 1.0 across a symbol's
-- rows (classify_fund() enforces this, topping up "Other" with any remainder).
CREATE TABLE IF NOT EXISTS fund_allocations (
    symbol TEXT NOT NULL,
    asset_class TEXT NOT NULL,
    weight REAL NOT NULL,
    PRIMARY KEY (symbol, asset_class)
);

CREATE TABLE IF NOT EXISTS ips_targets (
    asset_class TEXT PRIMARY KEY,
    target_pct REAL NOT NULL         -- stored as a fraction, e.g. 0.60 for 60%
);

-- Per-account IPS target overrides. An asset class with no row here for a
-- given account falls back to that class's household-wide ips_targets row.
CREATE TABLE IF NOT EXISTS ips_targets_by_account (
    account_number TEXT NOT NULL REFERENCES accounts(account_number),
    asset_class TEXT NOT NULL,
    target_pct REAL NOT NULL,        -- stored as a fraction, e.g. 0.60 for 60%
    PRIMARY KEY (account_number, asset_class)
);

-- Morningstar-style style-box weights per symbol (e.g. "Large Blend" 0.60,
-- "Large Growth" 0.40). Same shape/intent as fund_allocations, but for
-- investment style rather than asset class -- weights should sum to ~1.0.
CREATE TABLE IF NOT EXISTS symbol_styles (
    symbol TEXT NOT NULL,
    style TEXT NOT NULL,
    weight REAL NOT NULL,
    PRIMARY KEY (symbol, style)
);

-- Sector weights per symbol, same shape as symbol_styles.
CREATE TABLE IF NOT EXISTS symbol_sectors (
    symbol TEXT NOT NULL,
    sector TEXT NOT NULL,
    weight REAL NOT NULL,
    PRIMARY KEY (symbol, sector)
);

-- Scalar per-symbol metadata that doesn't fit the classification/weight
-- tables above. Currently just expense ratio; a symbol only needs a row
-- here once something has been set for it.
CREATE TABLE IF NOT EXISTS security_metadata (
    symbol TEXT PRIMARY KEY,
    expense_ratio REAL               -- stored as a fraction, e.g. 0.0003 for 0.03%
);

CREATE INDEX IF NOT EXISTS idx_holdings_snapshot ON holdings(snapshot_id);
CREATE INDEX IF NOT EXISTS idx_holdings_account ON holdings(account_number);
"""


def _migrate(conn: sqlite3.Connection):
    """Add columns to tables created by an older version of this tool.
    CREATE TABLE IF NOT EXISTS in SCHEMA only handles brand-new databases --
    existing ones need an explicit ALTER TABLE for any new column."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(accounts)").fetchall()}
    if "account_type_source" not in cols:
        conn.execute(
            "ALTER TABLE accounts ADD COLUMN account_type_source TEXT NOT NULL DEFAULT 'inferred'"
        )
    if "expense_ratio" not in cols:
        conn.execute("ALTER TABLE accounts ADD COLUMN expense_ratio REAL")
    conn.commit()


def get_connection(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn
