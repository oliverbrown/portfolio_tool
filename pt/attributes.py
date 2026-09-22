"""Per-symbol investment metadata beyond asset-class classification:
Morningstar-style style-box weights, sector weights, and expense ratio.

Style and sector follow the same shape as classifier.classify_fund()'s
fund_allocations table -- a symbol can have its value split across several
categories with weights summing to ~1.0 -- since that's exactly how
Fidelity's own "Style Diversification" and "Sector Diversification" exports
describe a fund (see data/style_seed.csv, data/sector_seed.csv)."""
import csv
from collections import defaultdict
from pathlib import Path

STYLE_CATEGORIES = [
    # The classic 3x3 domestic-equity style box (market cap x value/blend/growth).
    "Large Value", "Large Blend", "Large Growth",
    "Medium Value", "Medium Blend", "Medium Growth",
    "Small Value", "Small Blend", "Small Growth",
    # Added to cover fixed-income, international, and target-date/allocation
    # funds that don't fit the domestic-equity grid above -- these are
    # Yahoo Finance's own Morningstar category names verbatim (see
    # scripts/update_market_data.py --show-style-categories), not weighted
    # style-box splits, so a symbol in one of these is normally 100% one
    # category rather than split across several. Note "Target-Date 2035" is
    # tied to that one vintage year -- a different target-date fund (2040,
    # 2050, ...) won't match it and would need its own category added.
    "Intermediate Core Bond", "Long Government", "Ultrashort Bond",
    "Intermediate Government", "Target-Date 2035", "Foreign Large Blend",
]

SECTOR_CATEGORIES = [
    "Communication Services", "Consumer Discretionary", "Consumer Staples",
    "Energy", "Financials", "Health Care", "Industrials",
    "Information Technology", "Materials", "Real Estate", "Utilities",
]

STYLE_SEED_PATH = Path(__file__).parent.parent / "data" / "style_seed.csv"
SECTOR_SEED_PATH = Path(__file__).parent.parent / "data" / "sector_seed.csv"


def _set_weights(conn, table, category_col, valid_categories, symbol, class_weight_pairs):
    symbol = symbol.strip().upper()
    if not class_weight_pairs:
        raise ValueError(f"Provide at least one {category_col} to set.")

    # A single category with no weight given means "100% of this category".
    if len(class_weight_pairs) == 1 and class_weight_pairs[0][1] is None:
        class_weight_pairs = [(class_weight_pairs[0][0], 1.0)]

    weights = {}
    for category, weight in class_weight_pairs:
        if weight is None:
            raise ValueError(
                f"Missing weight for '{category}'. When more than one {category_col} is "
                f'given, every one needs a weight, e.g. "{valid_categories[0]}" 0.45'
            )
        if category not in valid_categories:
            raise ValueError(
                f"'{category}' is not a recognized {category_col}. "
                f"Choose one of: {', '.join(valid_categories)}"
            )
        if weight < 0:
            raise ValueError(f"Weight for '{category}' cannot be negative.")
        weights[category] = weights.get(category, 0.0) + weight

    total = sum(weights.values())
    if total > 1.0 + 1e-6:
        raise ValueError(
            f"Weights add up to {total*100:.1f}%, which is over 100%. "
            "Please adjust so they total 100% or less."
        )

    conn.execute(f"DELETE FROM {table} WHERE symbol = ?", (symbol,))
    for category, weight in weights.items():
        if weight <= 1e-9:
            continue
        conn.execute(
            f"INSERT INTO {table} (symbol, {category_col}, weight) VALUES (?, ?, ?)",
            (symbol, category, weight),
        )
    conn.commit()
    return weights


def _get_weights(conn, table, category_col, symbol):
    symbol = symbol.strip().upper()
    rows = conn.execute(
        f"SELECT {category_col} AS category, weight FROM {table} WHERE symbol = ?", (symbol,)
    ).fetchall()
    return {r["category"]: r["weight"] for r in rows}


def _get_label(conn, table, category_col, symbol):
    """Display-friendly summary, e.g. "Large Blend" or "Large Blend 60%, Large
    Growth 40%". Empty string if nothing's been set for this symbol."""
    weights = _get_weights(conn, table, category_col, symbol)
    if not weights:
        return ""
    if len(weights) == 1:
        return next(iter(weights))
    parts = [f"{c} {w*100:.0f}%" for c, w in sorted(weights.items(), key=lambda kv: -kv[1])]
    return ", ".join(parts)


def _load_seed_rows(seed_path, category_col):
    """Yields (symbol, category, weight_fraction) triples from either:

    - the bundled clean seed format (header: symbol,<category_col>,weight --
      weight already a 0-1 fraction), or
    - a raw Fidelity "Style Diversification" / "Sector Diversification"
      export (Positions > Style/Sector diagnostics > Download; header:
      Symbol,Description,Account,Style|Sector,Weight,Current value, plus a
      disclaimer footer with no Symbol). Fidelity repeats the same weight
      once per account holding a symbol, so rows are deduplicated here by
      (symbol, category), keeping the first weight seen -- confirmed the
      weight for a given symbol/category is identical across every account.
    """
    with open(seed_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        is_raw_export = "Account" in fieldnames and "Weight" in fieldnames
        raw_category_field = category_col.capitalize()  # "style" -> "Style", "sector" -> "Sector"
        seen = set()
        for row in reader:
            if is_raw_export:
                symbol = (row.get("Symbol") or "").strip().upper()
                category = (row.get(raw_category_field) or "").strip()
                weight_raw = (row.get("Weight") or "").strip()
                if not symbol or not category or not weight_raw:
                    continue  # footer/disclaimer rows have no Symbol
                key = (symbol, category)
                if key in seen:
                    continue
                seen.add(key)
                weight = float(weight_raw.rstrip("%")) / 100.0
            else:
                symbol = row["symbol"].strip().upper()
                category = row[category_col].strip()
                weight = float(row["weight"])
            yield symbol, category, weight


def _seed_weights(conn, table, category_col, seed_path, overwrite):
    """Loads a symbol -> category/weight seed CSV (columns: symbol, <category_col>,
    weight). A symbol's rows are seeded as a group -- if it already has any
    rows in `table` and overwrite=False, the whole symbol is skipped rather
    than merging seed rows in alongside a user's own classification.

    seed_path may also be a raw Fidelity "Style Diversification" / "Sector
    Diversification" export (Positions > Style/Sector diagnostics >
    Download) -- see _load_seed_rows()."""
    if not Path(seed_path).exists():
        return 0
    rows_by_symbol = defaultdict(list)
    for symbol, category, weight in _load_seed_rows(seed_path, category_col):
        rows_by_symbol[symbol].append((category, weight))

    count = 0
    for symbol, cat_weights in rows_by_symbol.items():
        has_existing = conn.execute(
            f"SELECT 1 FROM {table} WHERE symbol = ? LIMIT 1", (symbol,)
        ).fetchone()
        if has_existing and not overwrite:
            continue
        conn.execute(f"DELETE FROM {table} WHERE symbol = ?", (symbol,))
        for category, weight in cat_weights:
            conn.execute(
                f"INSERT INTO {table} (symbol, {category_col}, weight) VALUES (?, ?, ?)",
                (symbol, category, weight),
            )
        count += 1
    conn.commit()
    return count


def set_style(conn, symbol, class_weight_pairs):
    return _set_weights(conn, "symbol_styles", "style", STYLE_CATEGORIES, symbol, class_weight_pairs)


def get_style_weights(conn, symbol):
    return _get_weights(conn, "symbol_styles", "style", symbol)


def get_style_label(conn, symbol):
    return _get_label(conn, "symbol_styles", "style", symbol)


def seed_styles(conn, seed_path=STYLE_SEED_PATH, overwrite=False):
    return _seed_weights(conn, "symbol_styles", "style", seed_path, overwrite)


def set_sector(conn, symbol, class_weight_pairs):
    return _set_weights(conn, "symbol_sectors", "sector", SECTOR_CATEGORIES, symbol, class_weight_pairs)


def get_sector_weights(conn, symbol):
    return _get_weights(conn, "symbol_sectors", "sector", symbol)


def get_sector_label(conn, symbol):
    return _get_label(conn, "symbol_sectors", "sector", symbol)


def seed_sectors(conn, seed_path=SECTOR_SEED_PATH, overwrite=False):
    return _seed_weights(conn, "symbol_sectors", "sector", seed_path, overwrite)


def set_expense_ratio(conn, symbol, expense_ratio_pct: float):
    """expense_ratio_pct is a percentage, e.g. 0.03 for 0.03% (typical for an
    index fund) or 0.75 for 0.75% -- not a 0-1 fraction, since that invites a
    100x entry error for a number this small. Stored internally as a
    fraction (0.0003) for consistency with the rest of the app's percentages."""
    symbol = symbol.strip().upper()
    if not (0 <= expense_ratio_pct <= 25):
        raise ValueError(
            "Expense ratio should be a percentage, e.g. 0.03 for 0.03% or 0.75 for "
            f"0.75% (got {expense_ratio_pct}, which is outside a plausible 0-25% range)."
        )
    conn.execute(
        "INSERT INTO security_metadata (symbol, expense_ratio) VALUES (?, ?) "
        "ON CONFLICT(symbol) DO UPDATE SET expense_ratio=excluded.expense_ratio",
        (symbol, expense_ratio_pct / 100.0),
    )
    conn.commit()


def get_expense_ratio(conn, symbol):
    """Returns the expense ratio as a fraction (e.g. 0.0003 for 0.03%), or
    None if it hasn't been set for this symbol."""
    row = conn.execute(
        "SELECT expense_ratio FROM security_metadata WHERE symbol = ?",
        (symbol.strip().upper(),),
    ).fetchone()
    return row["expense_ratio"] if row and row["expense_ratio"] is not None else None
