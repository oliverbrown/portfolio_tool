#!/usr/bin/env python3
"""Optional helper: fetches sector weights, expense ratios, and style
categories for your CURRENTLY HELD symbols from Yahoo Finance (via the
yfinance package) and proposes updates to pt's local database.

Deliberately kept OUTSIDE `pt.cli` -- the core tool never makes a network
call on its own (see README "Everything runs locally"); this script is the
one place that does, and it only runs when you invoke it yourself. Nothing
here is wired into `import`, `report`, or any other pt command.

What this can and can't get you:
  - Expense ratio: reasonable coverage for ETFs and mutual funds, via
    whichever of a few inconsistently-populated Yahoo fields has data.
  - Sector weights: excellent for individual stocks (one sector, 100%
    weight); good for large/common ETFs; spotty-to-absent for
    actively-managed mutual funds, which often have no Yahoo Finance
    profile at all.
  - Style category (Large Blend, Small Growth, etc.): a fund's Morningstar
    category IS available for free, via yfinance's
    funds_data.fund_overview['categoryName']. When it's an EXACT match to
    one of pt's STYLE_CATEGORIES (see pt/attributes.py; the original
    3x3 domestic-equity grid plus a handful of fixed-income/
    international/target-date categories added once real holdings
    surfaced them), it's proposed as a 100%-that-category write, same
    dry-run/--apply/--overwrite-existing rules as sector and expense
    ratio below -- it's one category per fund rather than the finer
    weighted split (e.g. "Large Blend 60% / Large Growth 40%") a Fidelity
    "Style Diversification" export gives you via `seed-styles`, so a
    fund with existing (finer) style data is left alone by default. A
    category with NO exact match (Yahoo reports plenty that don't
    correspond to any current STYLE_CATEGORIES entry -- other bond/
    target-date vintages, sector funds, etc.) is never guessed at --
    see --show-style-categories to review those and decide whether any
    are worth adding to STYLE_CATEGORIES.

Caching: since this data barely changes, results are cached locally at
~/.portfolio_tool/market_data_cache.json and only re-fetched once a cache
entry is older than --max-age-days (default 90). Re-run any time with
--force-refresh to ignore the cache.

Safety: dry-run by default -- prints what WOULD change without touching
the database. Pass --apply to actually write. By default, a symbol that
ALREADY has sector/expense-ratio data in pt's database is left alone
(your own or a Fidelity-exported value is assumed authoritative over a
scraped one) -- pass --overwrite-existing to let fetched data replace it.

Usage:
    pip3 install yfinance
    python3 scripts/update_market_data.py                     # dry run, latest snapshot's holdings
    python3 scripts/update_market_data.py --apply              # write the proposed changes
    python3 scripts/update_market_data.py --apply --overwrite-existing
    python3 scripts/update_market_data.py --force-refresh      # ignore the cache
    python3 scripts/update_market_data.py --show-style-categories  # report Morningstar
                                                                     # categories found (no writes)
    python3 scripts/update_market_data.py --show-raw-data          # report raw Yahoo fields
                                                                     # behind the proposals (no writes)
    python3 scripts/update_market_data.py --symbol VTI --symbol VXUS  # just these, skip DB lookup
"""
import argparse
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pt import attributes, db as db_mod  # noqa: E402

CACHE_PATH = Path.home() / ".portfolio_tool" / "market_data_cache.json"
DEFAULT_MAX_AGE_DAYS = 90

# A plausible ticker: 1-5 letters, optionally a share-class suffix like
# BRK.B. Filters out CUSIPs (bond identifiers, e.g. "06053CJA5"), Fidelity's
# cash-sweep notation ("SPAXX**", "USD***"), crypto pairs ("BTC/USD"), and
# internal plan codes ("NON40OMR5") BEFORE ever hitting the network -- these
# were never going to resolve to a real Yahoo Finance quote, and some
# malformed values (like a "/" in a symbol) make yfinance's own HTTP layer
# print a raw error page to stderr instead of failing cleanly.
_TICKER_RE = re.compile(r"^[A-Z]{1,5}(\.[A-Z])?$")


def _looks_like_ticker(symbol: str) -> bool:
    return bool(_TICKER_RE.match(symbol))

# Yahoo Finance uses TWO DIFFERENT sector-name formats depending on which
# field the data comes from -- info['sector'] for an individual stock gives
# Title Case names ("Consumer Cyclical"), while a fund's
# funds_data.sector_weightings gives lowercase/underscore keys
# ("consumer_cyclical") -- and neither matches Fidelity/Morningstar's GICS
# names this tool's SECTOR_CATEGORIES use (see pt/attributes.py). Both get
# normalized to a canonical (lowercase, no spaces/underscores) key before
# lookup here, so one table covers both formats.
_SECTOR_CANONICAL = {
    "technology": "Information Technology",
    "informationtechnology": "Information Technology",
    "consumercyclical": "Consumer Discretionary",
    "consumerdiscretionary": "Consumer Discretionary",
    "consumerdefensive": "Consumer Staples",
    "consumerstaples": "Consumer Staples",
    "financialservices": "Financials",
    "financial": "Financials",
    "financials": "Financials",
    "healthcare": "Health Care",
    "basicmaterials": "Materials",
    "materials": "Materials",
    "realestate": "Real Estate",
    "communicationservices": "Communication Services",
    "energy": "Energy",
    "industrials": "Industrials",
    "utilities": "Utilities",
}


def _canonical_sector_key(name: str) -> str:
    return name.lower().replace(" ", "").replace("_", "")


def _map_sector_name(name: str):
    """Returns this tool's GICS sector name for a Yahoo sector name/key (see
    _SECTOR_CANONICAL), or None if it's not a recognized GICS-11 sector
    (e.g. a fund's "cash" slice)."""
    return _SECTOR_CANONICAL.get(_canonical_sector_key(name))


def matches_style_category(raw_category: str) -> bool:
    """True if `raw_category` (Yahoo's funds_data.fund_overview
    ['categoryName'], e.g. "Large Blend" or "High Yield Bond") is an
    EXACT match to one of pt's STYLE_CATEGORIES (see attributes.py) --
    no fuzzy matching, since a near-miss like "Target-Date 2040" vs. the
    "Target-Date 2035" entry is a genuinely different category, not a
    formatting difference the way sector names have."""
    return raw_category is not None and raw_category.strip() in attributes.STYLE_CATEGORIES


def _load_cache() -> dict:
    if CACHE_PATH.exists():
        try:
            return json.loads(CACHE_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_cache(cache: dict):
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, indent=2, sort_keys=True))


def _is_fresh(entry: dict, max_age_days: int) -> bool:
    try:
        fetched = datetime.fromisoformat(entry["fetched"])
    except (KeyError, ValueError):
        return False
    return datetime.now() - fetched < timedelta(days=max_age_days)


def _normalize_sector_weights(raw: dict) -> dict:
    """raw: {yahoo_sector_name_or_key: weight_fraction, ...} (weights need
    not sum to 1.0 -- e.g. an ETF's "cash" slice isn't a GICS sector and is
    simply dropped). Returns {this_tool's_sector_name: weight_fraction},
    merging any two Yahoo keys that map to the same GICS name."""
    out = {}
    for name, weight in raw.items():
        if not weight or weight <= 0:
            continue
        mapped = _map_sector_name(name)
        if mapped is None:
            continue
        out[mapped] = out.get(mapped, 0.0) + weight
    # Yahoo's per-sector floats each carry a little representation error
    # (e.g. 0.0066000004 instead of 0.0066); summed across ~11 sectors this
    # routinely pushes the total a hair over 1.0 (seen up to 1.0001 on real
    # holdings) -- enough to trip attributes.set_sector()'s ">100%" check
    # even though the fund is obviously ~100%, not over-invested. Scale
    # proportionally back to 1.0 whenever the total exceeds it so the write
    # never fails on floating-point noise; relative weights between sectors
    # are unaffected. A no-op when the total is already <= 1.0.
    total = sum(out.values())
    if total > 1.0:
        out = {k: v / total for k, v in out.items()}
    return out


# Every key fetch_symbol() always sets, used both to initialize its result
# dict and to detect a cache entry written by an older version of this
# script (missing any of these) so it gets backfilled -- see main()'s
# needs_backfill check. Whenever a new field is added to fetch_symbol()'s
# result, add its key here too so old cache entries pick it up automatically
# instead of needing a new special-cased backfill condition.
RESULT_KEYS = (
    "sector_weights", "expense_ratio_pct", "quote_type", "style_category",
    "raw_sector_weights", "raw_expense_fields", "raw_fund_overview",
)


def fetch_symbol(yf_module, symbol: str) -> dict:
    """Returns a dict with all of RESULT_KEYS set:
      sector_weights: {gics_name: weight, ...} or None -- pt's own
        categories, mapped/summed from raw_sector_weights (see
        _normalize_sector_weights())
      expense_ratio_pct: float or None -- pt's convention (0.03 means
        0.03%), picked from whichever of raw_expense_fields is usable
      quote_type: "EQUITY" | "ETF" | "MUTUALFUND" | None, straight from
        Yahoo
      style_category: str or None -- the RAW Morningstar category name
        from Yahoo, e.g. "Large Blend" or "Foreign Large Bond"
        (raw_fund_overview['categoryName']); see matches_style_category()
        -- only written to the database (as 100% that category) when it's
        an EXACT match to one of pt's STYLE_CATEGORIES, same as sector/
        expense ratio; a non-matching category is reported but never
        written -- see --show-style-categories
      raw_sector_weights: the UNMAPPED sector dict exactly as Yahoo
        returned it (whatever key format/casing it used), or None
      raw_expense_fields: {"netExpenseRatio": ..., "annualReportExpenseRatio":
        ..., "expenseRatio": ...} -- each value exactly as Yahoo returned
        it (None if that field was absent), so which field had data and
        its original units are visible, not just the converted result
      raw_fund_overview: the full dict from funds_data.fund_overview
        (typically categoryName, family, legalType), or None
    Never raises -- a fetch failure for one symbol shouldn't abort the
    whole run; the caller sees None fields and reports it as "not found"
    rather than crashing."""
    result = {k: None for k in RESULT_KEYS}
    try:
        ticker = yf_module.Ticker(symbol)
        info = ticker.info or {}
    except Exception:
        return result

    quote_type = (info.get("quoteType") or "").upper()
    result["quote_type"] = quote_type or None

    if quote_type == "EQUITY":
        sector = info.get("sector")
        if sector:
            result["raw_sector_weights"] = {sector: 1.0}
            mapped = _map_sector_name(sector)
            if mapped is not None:
                result["sector_weights"] = {mapped: 1.0}
        return result  # individual stocks don't have an expense ratio or style category

    # ETF / mutual fund: try a couple of known field shapes for sector
    # weightings -- yfinance's coverage here has shifted across versions.
    raw_weights = None
    funds_data = None
    try:
        funds_data = getattr(ticker, "funds_data", None)
        if funds_data is not None:
            sw = getattr(funds_data, "sector_weightings", None)
            if sw:
                raw_weights = dict(sw)
    except Exception:
        pass

    if funds_data is not None:
        try:
            overview = getattr(funds_data, "fund_overview", None) or {}
            if overview:
                result["raw_fund_overview"] = dict(overview)
            category = overview.get("categoryName")
            if category:
                result["style_category"] = category
        except Exception:
            pass
    if raw_weights is None:
        sw = info.get("sectorWeightings")
        if sw:
            # Sometimes a list of single-key dicts, sometimes one dict.
            if isinstance(sw, list):
                raw_weights = {}
                for entry in sw:
                    raw_weights.update(entry)
            elif isinstance(sw, dict):
                raw_weights = sw
    if raw_weights:
        result["raw_sector_weights"] = raw_weights
        result["sector_weights"] = _normalize_sector_weights(raw_weights) or None

    # yfinance's expense-ratio fields use TWO DIFFERENT conventions, verified
    # empirically against known real values (VOO=0.03%, ARKK=0.75%, etc.):
    # netExpenseRatio is already a percentage (0.03 means 0.03%); the other
    # two are fractions (0.0003 means 0.03%). netExpenseRatio also turned out
    # to be by far the most consistently populated across ETFs and mutual
    # funds, so it's tried first.
    raw_expense_fields = {
        field: info.get(field) for field in ("netExpenseRatio", "annualReportExpenseRatio", "expenseRatio")
    }
    if any(v is not None for v in raw_expense_fields.values()):
        result["raw_expense_fields"] = raw_expense_fields
    pct = None
    net = raw_expense_fields["netExpenseRatio"]
    if net is not None:
        pct = float(net)
    else:
        for field in ("annualReportExpenseRatio", "expenseRatio"):
            value = raw_expense_fields[field]
            if value is not None:
                pct = value * 100
                break
    # Sanity guard: real expense ratios essentially never exceed ~3% even
    # for the priciest actively-managed funds -- anything past that is far
    # more likely a unit-conversion mismatch (this class of bug is exactly
    # why this guard exists) than a real fund, so it's treated as not found
    # rather than risking writing a garbage value.
    if pct is not None and 0 <= pct <= 3:
        result["expense_ratio_pct"] = pct

    return result


def held_symbols(db_path: Path) -> list:
    conn = db_mod.get_connection(db_path)
    latest = conn.execute("SELECT id FROM snapshots ORDER BY id DESC LIMIT 1").fetchone()
    if not latest:
        return []
    rows = conn.execute(
        "SELECT DISTINCT symbol FROM holdings WHERE snapshot_id = ? ORDER BY symbol", (latest["id"],)
    ).fetchall()
    return [r["symbol"] for r in rows]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--symbol", action="append", dest="symbols",
                         help="Fetch just this symbol (repeatable) instead of the latest snapshot's holdings.")
    parser.add_argument("--apply", action="store_true", help="Write proposed changes to the database (default: dry run).")
    parser.add_argument("--overwrite-existing", action="store_true",
                         help="Also overwrite a symbol that already has sector/expense-ratio/style data (default: leave it alone).")
    parser.add_argument("--force-refresh", action="store_true", help="Ignore the cache and re-fetch every symbol.")
    parser.add_argument("--max-age-days", type=int, default=DEFAULT_MAX_AGE_DAYS,
                         help=f"Cache staleness threshold (default {DEFAULT_MAX_AGE_DAYS}).")
    parser.add_argument("--db", type=Path, default=db_mod.DEFAULT_DB_PATH, help="Path to portfolio.db.")
    parser.add_argument("--show-style-categories", action="store_true",
                         help="Additionally report EVERY Morningstar category Yahoo has for a held fund, "
                              "including ones with no STYLE_CATEGORIES match (those are informational only, "
                              "never written) -- to help decide whether pt's STYLE_CATEGORIES (see "
                              "pt/attributes.py) are worth expanding further. Exact matches are proposed "
                              "as writes either way -- see the 'Style' section above.")
    parser.add_argument("--show-raw-data", action="store_true",
                         help="Also print the raw, unprocessed Yahoo Finance fields for each symbol that has "
                              "any (sector weights before mapping to pt's GICS categories, all three possible "
                              "expense-ratio fields with their original values/units, and the raw fund_overview "
                              "dict) -- INFORMATIONAL ONLY, never written anywhere; useful for sanity-checking "
                              "the automatic mapping/conversion logic against what Yahoo actually returned.")
    args = parser.parse_args()

    try:
        import yfinance as yf
    except ImportError:
        print("This script needs the yfinance package, which isn't installed.\n"
              "Install it with:\n\n    pip3 install yfinance\n\n"
              "It's intentionally NOT in requirements.txt -- the core pt tool never needs it.",
              file=sys.stderr)
        sys.exit(1)

    all_symbols = args.symbols or held_symbols(args.db)
    if not all_symbols:
        print("No symbols to fetch (no snapshots imported yet, or none given via --symbol).")
        return

    symbols = [s.strip().upper() for s in all_symbols if _looks_like_ticker(s.strip().upper())]
    not_ticker_shaped = sorted(set(all_symbols) - set(symbols))
    if not_ticker_shaped:
        print(f"Skipping {len(not_ticker_shaped)} symbol(s) that don't look like real tickers "
              "(CUSIPs, cash-sweep positions, crypto pairs, internal plan codes, etc.) -- "
              "never sent to Yahoo Finance:")
        print(f"  {', '.join(not_ticker_shaped)}\n")
    if not symbols:
        print("Nothing left to fetch after filtering.")
        return
    print(f"Fetching {len(symbols)} symbol(s) -- this can take a while for a large portfolio "
          "(one network call per symbol not already cached); safe to leave running.\n")

    conn = db_mod.get_connection(args.db)
    cache = _load_cache()

    fetched_fresh = 0
    used_cache = 0
    sector_updates = []   # (symbol, {category: weight})
    expense_updates = []  # (symbol, pct)
    style_updates = []    # (symbol, category) -- only categories with an exact STYLE_CATEGORIES match
    no_sector_data = []
    no_expense_data = []
    skipped_existing = []
    needs_style = []
    style_categories = {}  # raw Yahoo category name -> [symbols] (--show-style-categories only)
    raw_data = {}  # symbol -> data (--show-raw-data only)

    for i, symbol in enumerate(symbols, 1):
        cached = cache.get(symbol)
        # A cache entry written before some field in RESULT_KEYS existed is
        # missing that key entirely (as opposed to having fetched it and got
        # back None) -- force a refetch just for THIS run so whichever report
        # needs it isn't silently missing older holdings, without a full
        # cache wipe for everyone else. This is a one-time cost per symbol
        # per newly-added field, self-healing as each symbol gets refetched.
        needs_backfill = cached is not None and any(k not in cached for k in RESULT_KEYS)
        if cached and not needs_backfill and not args.force_refresh and _is_fresh(cached, args.max_age_days):
            data = cached
            used_cache += 1
            print(f"[{i}/{len(symbols)}] {symbol} (cached)")
        else:
            print(f"[{i}/{len(symbols)}] {symbol} ...", end=" ", flush=True)
            data = fetch_symbol(yf, symbol)
            data["fetched"] = datetime.now().isoformat()
            cache[symbol] = data
            fetched_fresh += 1
            found = []
            if data.get("sector_weights"):
                found.append("sector")
            if data.get("expense_ratio_pct") is not None:
                found.append("expense ratio")
            category = data.get("style_category")
            if category:
                found.append(f"style: {category}" + (" (match)" if matches_style_category(category) else " (no match)"))
            print(", ".join(found) if found else "nothing found")
            if fetched_fresh % 25 == 0:
                _save_cache(cache)  # periodic checkpoint -- a long run can be interrupted safely

        existing_sector = attributes.get_sector_weights(conn, symbol)
        existing_expense = attributes.get_expense_ratio(conn, symbol)
        existing_style = attributes.get_style_weights(conn, symbol)

        # Re-derive from the raw weights (rather than trusting the cached
        # "sector_weights" value as-is) whenever raw data is available, so a
        # cache entry written before a _normalize_sector_weights() fix isn't
        # stuck replaying the old (possibly invalid, e.g. >100%) result --
        # no refetch/backfill needed, since the raw data itself didn't change.
        if data.get("raw_sector_weights"):
            sector_weights = _normalize_sector_weights(data["raw_sector_weights"]) or None
        else:
            sector_weights = data.get("sector_weights")
        if sector_weights:
            if existing_sector and not args.overwrite_existing:
                skipped_existing.append((symbol, "sector"))
            else:
                sector_updates.append((symbol, sector_weights))
        else:
            no_sector_data.append(symbol)

        expense_pct = data.get("expense_ratio_pct")
        if expense_pct is not None:
            if existing_expense is not None and not args.overwrite_existing:
                skipped_existing.append((symbol, "expense ratio"))
            else:
                expense_updates.append((symbol, expense_pct))
        elif data.get("quote_type") != "EQUITY":
            no_expense_data.append(symbol)

        # Style: only write when Yahoo's category is an EXACT match to one of
        # pt's STYLE_CATEGORIES (see matches_style_category()) -- Yahoo gives
        # one category per fund rather than a weighted split, so a match is
        # written as 100% that category; a non-matching or missing category
        # is never guessed at, it just leaves the symbol in needs_style below.
        category = data.get("style_category")
        if category and matches_style_category(category):
            if existing_style and not args.overwrite_existing:
                skipped_existing.append((symbol, "style"))
            else:
                style_updates.append((symbol, category))
        elif not existing_style:
            needs_style.append(symbol)

        if args.show_style_categories and category:
            style_categories.setdefault(category, []).append(symbol)

        if args.show_raw_data and any(
            data.get(k) for k in ("raw_sector_weights", "raw_expense_fields", "raw_fund_overview")
        ):
            raw_data[symbol] = data

    _save_cache(cache)

    print(f"Checked {len(symbols)} symbol(s): {fetched_fresh} fetched, {used_cache} from cache "
          f"(cache: {CACHE_PATH}).\n")

    if sector_updates:
        print(f"Sector data {'to write' if args.apply else 'that WOULD be written (dry run)'} "
              f"for {len(sector_updates)} symbol(s):")
        for symbol, weights in sector_updates:
            label = ", ".join(f"{c} {w*100:.0f}%" for c, w in sorted(weights.items(), key=lambda kv: -kv[1]))
            print(f"  {symbol}: {label}")
        print()

    if expense_updates:
        print(f"Expense ratio {'to write' if args.apply else 'that WOULD be written (dry run)'} "
              f"for {len(expense_updates)} symbol(s):")
        for symbol, pct in expense_updates:
            print(f"  {symbol}: {pct:.3f}%")
        print()

    if style_updates:
        print(f"Style {'to write' if args.apply else 'that WOULD be written (dry run)'} "
              f"for {len(style_updates)} symbol(s) (exact match to one of pt's STYLE_CATEGORIES, "
              "written as 100% that category):")
        for symbol, category in style_updates:
            print(f"  {symbol}: {category}")
        print()

    if skipped_existing:
        print(f"Skipped {len(skipped_existing)} symbol/field pair(s) that already have data "
              "(pass --overwrite-existing to replace them):")
        for symbol, field in skipped_existing:
            print(f"  {symbol}: {field}")
        print()

    if no_sector_data or no_expense_data:
        missing = sorted(set(no_sector_data) | set(no_expense_data))
        print(f"No sector/expense data found for {len(missing)} symbol(s) (often actively-managed "
              f"mutual funds with no Yahoo Finance profile) -- set these by hand if you have the data:")
        print(f"  {', '.join(missing)}")
        print()

    if needs_style:
        print(f"{len(needs_style)} symbol(s) still have no style data in the database and no exact "
              "match in pt's STYLE_CATEGORIES (pass --show-style-categories to see what Yahoo has for "
              "them, if anything). Use `seed-styles` with a Fidelity \"Style Diversification\" export, "
              "or `style set` by hand:")
        print(f"  {', '.join(sorted(needs_style))}")
        print()

    if args.show_style_categories:
        matching = {c: syms for c, syms in style_categories.items() if matches_style_category(c)}
        non_matching = {c: syms for c, syms in style_categories.items() if not matches_style_category(c)}
        print("=== Style categories seen (matches are also in the write proposal above) ===\n")
        if matching:
            print(f"Categories that match one of pt's STYLE_CATEGORIES exactly "
                  f"({sum(len(s) for s in matching.values())} symbol(s)):")
            for cat, syms in sorted(matching.items(), key=lambda kv: -len(kv[1])):
                print(f"  {cat} ({len(syms)}): {', '.join(sorted(syms))}")
            print()
        if non_matching:
            print(f"Categories that DON'T match any current style box ({sum(len(s) for s in non_matching.values())} "
                  "symbol(s), INFORMATIONAL ONLY -- never written) -- sorted by how many symbols each would "
                  "cover, to help decide whether any are worth adding to pt/attributes.py's STYLE_CATEGORIES:")
            for cat, syms in sorted(non_matching.items(), key=lambda kv: -len(kv[1])):
                print(f"  {cat} ({len(syms)}): {', '.join(sorted(syms))}")
            print()
        if not matching and not non_matching:
            print("No style category data found for any symbol checked (most likely all individual "
                  "stocks, or funds with no Yahoo Finance profile).\n")

    if args.show_raw_data:
        print("=== Raw yfinance data (INFORMATIONAL ONLY -- nothing written) ===\n")
        if raw_data:
            for symbol in sorted(raw_data):
                data = raw_data[symbol]
                print(f"  {symbol} ({data.get('quote_type') or 'unknown type'}):")
                raw_sw = data.get("raw_sector_weights")
                if raw_sw:
                    mapped = data.get("sector_weights") or {}
                    parts = ", ".join(f"{k}={v}" for k, v in raw_sw.items())
                    print(f"    raw sector weights: {{{parts}}}")
                    if mapped:
                        mapped_str = ", ".join(f"{c} {w*100:.0f}%" for c, w in sorted(mapped.items(), key=lambda kv: -kv[1]))
                        print(f"      -> mapped to pt sectors: {mapped_str}")
                raw_exp = data.get("raw_expense_fields")
                if raw_exp:
                    fields_str = ", ".join(f"{k}={v}" for k, v in raw_exp.items())
                    print(f"    raw expense-ratio fields: {{{fields_str}}}")
                    if data.get("expense_ratio_pct") is not None:
                        print(f"      -> used: {data['expense_ratio_pct']:.3f}%")
                raw_overview = data.get("raw_fund_overview")
                if raw_overview:
                    overview_str = ", ".join(f"{k}={v}" for k, v in raw_overview.items())
                    print(f"    raw fund overview: {{{overview_str}}}")
                print()
        else:
            print("No raw sector/expense/fund-overview fields found for any symbol checked.\n")

    if not args.apply:
        if sector_updates or expense_updates or style_updates:
            print("Dry run -- nothing written. Re-run with --apply to write the changes above.")
        return

    for symbol, weights in sector_updates:
        attributes.set_sector(conn, symbol, list(weights.items()))
    for symbol, pct in expense_updates:
        attributes.set_expense_ratio(conn, symbol, pct)
    for symbol, category in style_updates:
        attributes.set_style(conn, symbol, [(category, 1.0)])
    print(f"Applied: {len(sector_updates)} sector update(s), {len(expense_updates)} expense-ratio "
          f"update(s), {len(style_updates)} style update(s).")


if __name__ == "__main__":
    main()
