"""Manages the security classification table (symbol -> asset class)."""
import csv
from pathlib import Path

# Two-level taxonomy: every holding is classified at the subclass level (the
# leaves below); each subclass belongs to exactly one macro class. Macro
# totals are a derived rollup (see allocation.allocation_by_macro_class()),
# not stored anywhere -- this dict is the single source of truth for which
# subclass rolls up to which macro.
ASSET_CLASS_HIERARCHY = {
    "Growth": [
        "US Equity",
        "International Equity",
        "REITs",
    ],
    "Income": [
        "US Bonds",
        "International Bonds",
        "Intermediate Treasuries (3-7 year)",
        "Long Treasuries (+7 Year)",
    ],
    "Liquidity": [
        "Cash",
        "CDs",
        "Money Market",
        "Short Treasuries (< 3 year)",
    ],
    "Alternatives": [
        "Cryptocurrency",
        "Commodities",
        "Gold",
    ],
    "Insurance": [
        "Fixed Indexed Annuities",
    ],
}

MACRO_CLASSES = list(ASSET_CLASS_HIERARCHY.keys())

# Flat list of subclasses -- what a holding actually gets classified as via
# `classify` / `classify ... Fund`. Same role as the old flat ASSET_CLASSES.
ASSET_CLASSES = [sub for subs in ASSET_CLASS_HIERARCHY.values() for sub in subs]

# subclass -> its macro class.
SUBCLASS_MACRO = {sub: macro for macro, subs in ASSET_CLASS_HIERARCHY.items() for sub in subs}

# Every target-able name, macro or subclass -- used to validate `targets set`,
# which can target either level (see ips.set_target()).
TARGETABLE_CLASSES = MACRO_CLASSES + ASSET_CLASSES

UNCLASSIFIED = "Unclassified"

# "Other" is a deliberate escape hatch outside the macro hierarchy, for a
# holding that genuinely doesn't fit any subclass above -- same role as
# UNCLASSIFIED, but for something you've looked at and decided doesn't fit,
# rather than something you haven't classified yet. It's also where
# classify_fund() parks any remainder when a Fund's weights don't sum to
# 100%, which is why it's still accepted there even though it's not in
# ASSET_CLASSES (a holding can't be *directly* classified "Other" via
# `classify SYMBOL "Other"` -- only reached as a Fund remainder).
OTHER = "Other"

SEED_PATH = Path(__file__).parent.parent / "data" / "classifications_seed.csv"


def seed_classifications(conn, seed_path: Path = SEED_PATH, overwrite=False):
    """Load the starter symbol -> asset class mapping. Only inserts symbols
    that aren't already classified, unless overwrite=True."""
    if not Path(seed_path).exists():
        return 0
    count = 0
    with open(seed_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            symbol = row["symbol"].strip().upper()
            asset_class = row["asset_class"].strip()
            description = row.get("description", "").strip()
            if overwrite:
                conn.execute(
                    "INSERT INTO classifications (symbol, asset_class, description) "
                    "VALUES (?, ?, ?) ON CONFLICT(symbol) DO UPDATE SET "
                    "asset_class=excluded.asset_class, description=excluded.description",
                    (symbol, asset_class, description),
                )
            else:
                conn.execute(
                    "INSERT OR IGNORE INTO classifications (symbol, asset_class, description) "
                    "VALUES (?, ?, ?)",
                    (symbol, asset_class, description),
                )
            count += 1
    conn.commit()
    return count


def classify_symbol(conn, symbol: str, asset_class: str, notes: str = None):
    """Classify a symbol as a single asset class (100% of that class).

    Overwrites any existing Fund (multi-class) classification for this
    symbol, since a symbol is either a simple single-class holding or a
    Fund -- not both.
    """
    symbol = symbol.strip().upper()
    if asset_class not in ASSET_CLASSES:
        raise ValueError(
            f"'{asset_class}' is not a recognized asset class. "
            f"Choose one of: {', '.join(ASSET_CLASSES)}"
        )
    conn.execute("DELETE FROM fund_allocations WHERE symbol = ?", (symbol,))
    conn.execute(
        "INSERT INTO classifications (symbol, asset_class, notes) VALUES (?, ?, ?) "
        "ON CONFLICT(symbol) DO UPDATE SET asset_class=excluded.asset_class, "
        "notes=COALESCE(excluded.notes, classifications.notes)",
        (symbol, asset_class, notes),
    )
    conn.commit()


def classify_fund(conn, symbol: str, class_weight_pairs, notes: str = None):
    """Classify a symbol as a Fund split across multiple asset classes.

    class_weight_pairs: list of (asset_class, weight_or_None) tuples.
        - If exactly one pair is given with weight=None, that class gets 100%.
        - Otherwise every pair must have a weight (fraction, e.g. 0.45 for 45%).
    Weights for the same asset class are summed if it appears more than once.
    If the total is less than 100%, the remainder is added to "Other".
    Raises ValueError if the total exceeds 100%, or an asset class /
    weight is invalid.

    Overwrites any existing simple classification for this symbol.
    Returns the final {asset_class: weight} mapping actually stored
    (including any auto-added "Other" remainder).
    """
    symbol = symbol.strip().upper()
    if not class_weight_pairs:
        raise ValueError("Provide at least one asset class to classify a Fund.")

    # A single class with no weight given means "100% of this class".
    if len(class_weight_pairs) == 1 and class_weight_pairs[0][1] is None:
        class_weight_pairs = [(class_weight_pairs[0][0], 1.0)]

    weights = {}
    for asset_class, weight in class_weight_pairs:
        if weight is None:
            raise ValueError(
                f"Missing weight for '{asset_class}'. When a Fund has more than "
                'one asset class, every class needs a weight, e.g. "US Equity" 0.45'
            )
        if asset_class not in ASSET_CLASSES:
            raise ValueError(
                f"'{asset_class}' is not a recognized asset class. "
                f"Choose one of: {', '.join(ASSET_CLASSES)}"
            )
        if weight < 0:
            raise ValueError(f"Weight for '{asset_class}' cannot be negative.")
        weights[asset_class] = weights.get(asset_class, 0.0) + weight

    total = sum(weights.values())
    if total > 1.0 + 1e-6:
        raise ValueError(
            f"Weights add up to {total*100:.1f}%, which is over 100%. "
            "Please adjust so they total 100% or less."
        )

    remainder = 1.0 - total
    if remainder > 1e-6:
        weights[OTHER] = weights.get(OTHER, 0.0) + remainder

    conn.execute("DELETE FROM classifications WHERE symbol = ?", (symbol,))
    conn.execute("DELETE FROM fund_allocations WHERE symbol = ?", (symbol,))
    for asset_class, weight in weights.items():
        if weight <= 1e-9:
            continue
        conn.execute(
            "INSERT INTO fund_allocations (symbol, asset_class, weight) VALUES (?, ?, ?)",
            (symbol, asset_class, weight),
        )
    conn.commit()
    return weights


def is_fund(conn, symbol: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM fund_allocations WHERE symbol = ? LIMIT 1",
        (symbol.strip().upper(),),
    ).fetchone()
    return row is not None


def get_symbol_weights(conn, symbol: str) -> dict:
    """Returns {asset_class: weight} for a symbol, weights summing to 1.0.
    Checks Fund classifications first, then simple classifications, then
    falls back to 100% Unclassified."""
    symbol = symbol.strip().upper()
    fund_rows = conn.execute(
        "SELECT asset_class, weight FROM fund_allocations WHERE symbol = ?",
        (symbol,),
    ).fetchall()
    if fund_rows:
        return {r["asset_class"]: r["weight"] for r in fund_rows}

    row = conn.execute(
        "SELECT asset_class FROM classifications WHERE symbol = ?", (symbol,)
    ).fetchone()
    if row:
        return {row["asset_class"]: 1.0}

    return {UNCLASSIFIED: 1.0}


def get_classification_label(conn, symbol: str) -> str:
    """A display-friendly label for a symbol's classification: either the
    single asset class, or "Fund: X 45%, Y 15%, ..." for a multi-class Fund."""
    weights = get_symbol_weights(conn, symbol)
    if len(weights) == 1:
        return next(iter(weights))
    parts = [f"{ac} {w*100:.0f}%" for ac, w in sorted(weights.items(), key=lambda kv: -kv[1])]
    return "Fund: " + ", ".join(parts)


def get_classification(conn, symbol: str) -> str:
    row = conn.execute(
        "SELECT asset_class FROM classifications WHERE symbol = ?",
        (symbol.strip().upper(),),
    ).fetchone()
    return row["asset_class"] if row else UNCLASSIFIED


def list_unclassified(conn, snapshot_id: int):
    """Distinct symbols in a snapshot that have no classification entry at
    all (neither a simple classification nor a Fund split), with their
    total value across the household so you know what's worth doing first."""
    rows = conn.execute(
        """
        SELECT h.symbol, h.description, SUM(h.current_value) AS total_value
        FROM holdings h
        LEFT JOIN classifications c ON c.symbol = h.symbol
        LEFT JOIN fund_allocations f ON f.symbol = h.symbol
        WHERE h.snapshot_id = ? AND c.symbol IS NULL AND f.symbol IS NULL
        GROUP BY h.symbol
        ORDER BY total_value DESC
        """,
        (snapshot_id,),
    ).fetchall()
    return rows
