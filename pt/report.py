"""Produces the two report deliverables: a text rebalancing report and an
Excel workbook with one tab per view of the data."""
import math
import sys
from datetime import datetime
from pathlib import Path

import yaml
from openpyxl import Workbook
from openpyxl.chart import ScatterChart, Series, Reference
from openpyxl.chart.legend import Legend
from openpyxl.chart.layout import Layout, ManualLayout
from openpyxl.chart.shapes import GraphicalProperties
from openpyxl.chart.text import RichText
from openpyxl.drawing.line import LineProperties
from openpyxl.drawing.text import CharacterProperties, Paragraph, ParagraphProperties
from openpyxl.styles import Font, Alignment, PatternFill
from openpyxl.utils import get_column_letter

from . import allocation as alloc
from . import attributes
from . import classifier
from . import ips as ips_mod
from . import planning
from . import projection as projection_mod
from . import scenario as scenario_mod
from .classifier import UNCLASSIFIED

FONT_NAME = "Arial"
HEADER_FILL = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
HEADER_FONT = Font(name=FONT_NAME, bold=True, color="FFFFFF")
BODY_FONT = Font(name=FONT_NAME)
BOLD_FONT = Font(name=FONT_NAME, bold=True)
# Monospace, for the SCENARIO CONFIGURATION YAML dump -- see
# _write_scenario_config() -- so its indentation actually lines up, the
# way it would in a text editor. Not used anywhere else in the workbook.
MONO_FONT = Font(name="Courier New")
CURRENCY_FMT = '$#,##0;($#,##0)'
PCT_FMT = '0.0%'
EXPENSE_FMT = '0.00%'
# For a chart y-axis only (see _add_line_chart()) -- NOT used for any
# worksheet cell, which always shows the full dollar amount via
# CURRENCY_FMT above. The trailing double-comma is a standard Excel
# format trick: each comma divides the displayed value by 1,000, so two
# of them divide by 1,000,000 -- turning "$2,500,000" into "$2.5M", the
# same abbreviation pt/charts.py's matplotlib _currency_formatter() uses.
# Three sections (positive;negative;zero) so the origin reads as a plain
# "$0" instead of "$0.0M", matching that same function's behavior.
MILLIONS_AXIS_FMT = '"$"#,##0.0,,"M";-"$"#,##0.0,,"M";"$"0'

# Width for the asset-class/macro-class name column in the text report.
# Fixed at the longest subclass name ("Intermediate Treasuries (3-7 year)",
# 34 chars) rather than a round number, so a long duration-bucket name can
# never throw off the columns after it.
CLASS_COL_WIDTH = 34


# ---------------------------------------------------------------- text report

def _source_file_display(source_file: str, public: bool) -> str:
    """snapshots.source_file is a "; "-joined list of imported CSV
    basenames (see cmd_import) -- often embedding a household member's
    name (e.g. "Portfolio_Positions_Aug-26-2026-alex.csv"). Redacted to
    just a file count in the public view; shown verbatim in private."""
    if not public:
        return source_file
    n = len([s for s in (source_file or "").split(";") if s.strip()])
    return f"<redacted> ({n} file{'s' if n != 1 else ''})"


def build_text_report(conn, snapshot_id: int, debug: bool = False, public: bool = False) -> str:
    """public=True produces the shareable view (see `report --public`, the
    default): no account-number suffixes, and the IPS-target comparison,
    by-account IPS, unclassified-holdings, and investment-expense sections
    are all dropped -- leaving the allocation-by-account-type and
    allocation-by-account (asset-class breakdown) sections. --private keeps
    everything (public=False)."""
    def acct_ref(num):
        """'#...1234' last-4 account reference, or '' in the public view."""
        return "" if public else f"#...{num[-4:]}"
    snap = conn.execute(
        "SELECT * FROM snapshots WHERE id = ?", (snapshot_id,)
    ).fetchone()

    alloc_rows, total = alloc.allocation_by_asset_class(conn, snapshot_id)
    macro_rows, _ = alloc.allocation_by_macro_class(conn, snapshot_id)
    acct_rows, _ = alloc.allocation_by_account_type(conn, snapshot_id)
    comparison = ips_mod.compare_to_targets(conn, alloc_rows, total)
    macro_comparison = ips_mod.compare_to_macro_targets(conn, macro_rows, total)
    unclassified_value = sum(r["value"] for r in alloc_rows if r["asset_class"] == UNCLASSIFIED)

    lines = []
    lines.append("=" * 64)
    lines.append("HOUSEHOLD REBALANCING REPORT")
    lines.append("=" * 64)
    lines.append(f"Snapshot:      #{snap['id']}  (imported {snap['imported_at']})")
    lines.append(f"Source file:   {_source_file_display(snap['source_file'], public)}")
    if snap["as_of_date"]:
        lines.append(f"As-of date:    {snap['as_of_date']}")
    lines.append(f"Household total value: ${total:,.0f}")
    lines.append("")

    lines.append("-" * 64)
    lines.append("ALLOCATION BY ACCOUNT TYPE")
    lines.append("-" * 64)
    for r in acct_rows:
        lines.append(f"  {r['account_type']:<24} ${r['value']:>14,.0f}   {r['pct']*100:>6.1f}%")
    lines.append("")

    if debug:
        lines.append("-" * 64)
        lines.append("DEBUG: ACCOUNTS CONTRIBUTING TO EACH ACCOUNT TYPE")
        lines.append("-" * 64)
        by_type = alloc.accounts_by_account_type(conn, snapshot_id)
        # Account names are free text from each household's own Fidelity
        # export, unlike the fixed, known-in-advance asset-class/macro-class
        # names elsewhere in this report (see CLASS_COL_WIDTH) -- a real
        # name can run well past a guessed-at fixed width (e.g. "Cash
        # Management (Individual - TOD)"), which would push every column
        # after it out of alignment for just that row. Sized dynamically
        # from the actual names being printed instead, so every row lines
        # up regardless of how long any one account's name is.
        name_width = max(
            (len(a["account_name"]) for accounts in by_type.values() for a in accounts),
            default=0,
        )
        for r in acct_rows:
            lines.append(f"  {r['account_type']}  (total ${r['value']:,.0f})")
            for a in by_type.get(r["account_type"], []):
                owner_label = a["owner"] or "unspecified"
                tag = "  [manual override]" if a["account_type_source"] == "manual" else ""
                ref = acct_ref(a["account_number"])
                lines.append(
                    f"      {a['account_name']:<{name_width}} {ref + '  ' if ref else ''}"
                    f"Owner: {owner_label:<10} ${a['value']:>13,.0f}{tag}"
                )
        lines.append("")

    lines.append("-" * 64)
    lines.append("ALLOCATION BY ACCOUNT  (asset class breakdown within each account)")
    lines.append("-" * 64)
    by_account = alloc.allocation_by_account(conn, snapshot_id)
    current_account_number = None
    current_account_total = None
    for r in by_account:
        if r["account_number"] != current_account_number:
            if current_account_number is not None:
                lines.append(f"      {'Account Total:':<{CLASS_COL_WIDTH}} ${current_account_total:>13,.0f}   {100.0:>6.1f}%")
            current_account_number = r["account_number"]
            current_account_total = r["account_total"]
            owner_label = r["owner"] or "unspecified"
            ref = acct_ref(r["account_number"])
            lines.append(
                f"  {r['account_name']}  ({r['account_type']}, Owner: {owner_label}"
                f"{', ' + ref if ref else ''})"
            )
        lines.append(f"      {r['asset_class']:<{CLASS_COL_WIDTH}} ${r['value']:>13,.0f}   {r['pct_of_account']*100:>6.1f}%")
    if current_account_number is not None:
        lines.append(f"      {'Account Total:':<{CLASS_COL_WIDTH}} ${current_account_total:>13,.0f}   {100.0:>6.1f}%")
    lines.append("")

    # The IPS-target comparison (macro + asset class), the by-account IPS
    # override section, unclassified-holdings, and investment-expense
    # sections below are all internal-planning detail -- dropped from the
    # public/shareable view (see `report --public`, the default).
    if not public:
        lines.append("-" * 64)
        lines.append("ACTUAL vs. IPS TARGET  (by macro class)")
        lines.append("-" * 64)
        has_macro_targets = any(c["target_pct"] for c in macro_comparison)
        if not has_macro_targets:
            lines.append("  No macro-class IPS targets have been set yet. Run:")
            lines.append('    python -m pt.cli targets set "Growth" 0.60')
            lines.append(f"  ...for each macro class ({'/'.join(classifier.MACRO_CLASSES)}), then re-run.")
            lines.append("  (Macro totals below still reflect your actual allocation either way.)")
            header = f"  {'Macro Class':<{CLASS_COL_WIDTH}} {'Actual':>8}"
            lines.append(header)
            for c in macro_comparison:
                lines.append(f"  {c['asset_class']:<{CLASS_COL_WIDTH}} {c['actual_pct']*100:>7.1f}%")
        else:
            header = f"  {'Macro Class':<{CLASS_COL_WIDTH}} {'Actual':>8} {'Target':>8} {'Diff':>8} {'$ to trade':>14}  Action"
            lines.append(header)
            for c in macro_comparison:
                lines.append(
                    f"  {c['asset_class']:<{CLASS_COL_WIDTH}} {c['actual_pct']*100:>7.1f}% {c['target_pct']*100:>7.1f}% "
                    f"{c['diff_pct']*100:>+7.1f}% ${c['diff_dollars']:>+13,.0f}  {c['action']}"
                )
        lines.append("")

        lines.append("-" * 64)
        lines.append("ACTUAL vs. IPS TARGET  (by asset class)")
        lines.append("-" * 64)
        has_targets = any(c["target_pct"] for c in comparison)
        if not has_targets:
            lines.append("  No IPS targets have been set yet. Run:")
            lines.append("    python -m pt.cli targets set \"US Equity\" 0.45")
            lines.append("  ...for each asset class, then re-run this report.")
            lines.append("  (Actual totals below still reflect your current allocation either way.)")
            header = f"  {'Asset Class':<{CLASS_COL_WIDTH}} {'Actual':>8}"
            lines.append(header)
            for c in comparison:
                lines.append(f"  {c['asset_class']:<{CLASS_COL_WIDTH}} {c['actual_pct']*100:>7.1f}%")
        else:
            header = f"  {'Asset Class':<{CLASS_COL_WIDTH}} {'Actual':>8} {'Target':>8} {'Diff':>8} {'$ to trade':>14}  Action"
            lines.append(header)
            for c in comparison:
                lines.append(
                    f"  {c['asset_class']:<{CLASS_COL_WIDTH}} {c['actual_pct']*100:>7.1f}% {c['target_pct']*100:>7.1f}% "
                    f"{c['diff_pct']*100:>+7.1f}% ${c['diff_dollars']:>+13,.0f}  {c['action']}"
                )
            lines.append("")
            lines.append("  Positive $ to trade = overweight (consider trimming).")
            lines.append("  Negative $ to trade = underweight (consider adding).")

        lines.append("")

    by_account_targets = {} if public else ips_mod.compare_to_targets_by_account(conn, by_account)
    if by_account_targets:
        lines.append("-" * 64)
        lines.append("ACTUAL vs. IPS TARGET  (by account, accounts with an override only)")
        lines.append("-" * 64)
        for acct_num, info in by_account_targets.items():
            lines.append(f"  {info['account_name']}  (#...{acct_num[-4:]}, total ${info['account_total']:,.0f})")
            for c in info["rows"]:
                tag = " *" if c["is_override"] else "  "
                lines.append(
                    f"      {c['asset_class']:<{CLASS_COL_WIDTH}}{tag} {c['actual_pct']*100:>7.1f}% {c['target_pct']*100:>7.1f}% "
                    f"{c['diff_pct']*100:>+7.1f}% ${c['diff_dollars']:>+13,.0f}  {c['action']}"
                )
        lines.append("  * = account-specific target override; other rows fall back to the household target")
        lines.append("")
    if not public and unclassified_value > 0:
        lines.append("-" * 64)
        lines.append("ATTENTION: UNCLASSIFIED HOLDINGS")
        lines.append("-" * 64)
        lines.append(
            f"  ${unclassified_value:,.0f} in holdings have no asset-class "
            "classification and are excluded from the comparison above (bucketed"
            " as 'Unclassified'). See the 'Unclassified' tab in the spreadsheet,"
            " then classify with:"
        )
        lines.append('    python -m pt.cli classify SYMBOL "US Equity"')
        lines.append("")

    if not public:
        exp_accounts, exp_totals = alloc.expense_summary(conn, snapshot_id)
        lines.append("-" * 64)
        lines.append("INVESTMENT EXPENSE SUMMARY  (calculated annual cost)")
        lines.append("-" * 64)
        name_width = max((len(a["account_name"]) for a in exp_accounts), default=12)
        for a in exp_accounts:
            lines.append(
                f"  {a['account_name']:<{name_width}} ${a['value']:>13,.0f}   "
                f"{a['blended_ratio']*100:>6.2f}%   ${a['total_expense_dollars']:>10,.0f}/yr"
            )
        lines.append("-" * 64)
        lines.append(
            f"  {'HOUSEHOLD TOTAL':<{name_width}} ${exp_totals['value']:>13,.0f}   "
            f"{exp_totals['blended_ratio']*100:>6.2f}%   ${exp_totals['total_expense_dollars']:>10,.0f}/yr"
        )
        if exp_totals["missing_value"] > 0:
            missing_pct = exp_totals["missing_value"] / exp_totals["value"] * 100 if exp_totals["value"] else 0.0
            lines.append("")
            lines.append(
                f"  ${exp_totals['missing_value']:,.0f} ({missing_pct:.1f}%) in holdings have no expense ratio on "
                "file -- the ratios/costs above understate the true total. Set one with "
                '`python -m pt.cli expense-ratio set SYMBOL PCT`, or for a whole managed account\'s flat fee '
                "(added to its holdings' own ratios, not replacing them), "
                "`python -m pt.cli accounts set-expense-ratio ACCOUNT_NUMBER PCT`."
            )
        lines.append("")

    lines.append("=" * 64)
    lines.append(f"Report generated {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("=" * 64)

    return "\n".join(lines)


# ------------------------------------------------------------- excel workbook

def _style_header_row(ws, row_idx, ncols):
    for col in range(1, ncols + 1):
        cell = ws.cell(row=row_idx, column=col)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(horizontal="center")


def _autosize(ws, widths):
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w


def _redact_account_numbers(obj):
    """A deep copy of `obj` (a scenario dict, or any nested piece of one)
    with every value stored under a key literally named "account_number"
    replaced by "<redacted>" -- for the public report's SCENARIO
    CONFIGURATION dump, where scenario.inherited_accounts[].account_number
    is a real brokerage account number. Everything else is copied
    unchanged."""
    if isinstance(obj, dict):
        return {k: ("<redacted>" if k == "account_number" else _redact_account_numbers(v))
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [_redact_account_numbers(v) for v in obj]
    return obj


def _inherited_date_cells(entry: dict) -> list:
    """[decedent date of birth, decedent date of death, SECURE Act 10-year
    deadline] as display strings for one scenario.inherited_accounts entry
    -- shown on the Accounts tab in both the public and private views. The
    inherited account must be fully distributed ($0) by Dec 31 of the
    deadline year (see rmd.InheritedAccountSchedule.final_distribution_year)."""
    dob = entry.get("decedent_birthdate")
    dod = entry.get("decedent_death_date")
    return [
        str(dob) if dob is not None else "",
        str(dod) if dod is not None else "",
        f"Dec 31, {dod.year + 10}" if dod is not None else "",
    ]


def _write_scenario_config(ws, scenario: dict, start_row: int, public: bool = False) -> int:
    """Writes the raw scenario dict (see pt/scenario.py) back out as YAML
    -- the same format the scenario file itself uses -- starting at
    start_row, one line per row in column A. A single giant blob in one
    cell would need horizontal scrolling and wouldn't show its own
    indentation, so it's split the way a text file's lines are, in a
    monospace font so the indentation still reads as indentation.
    sort_keys=False preserves the scenario dict's own key order rather
    than alphabetizing (see format_scenario_config()'s docstring in
    pt/projection.py, which this mirrors for the text report).
    public=True redacts account_number values first (see
    _redact_account_numbers()). Returns the next free row after this block."""
    header_cell = ws.cell(row=start_row, column=1, value="SCENARIO CONFIGURATION")
    header_cell.font = BOLD_FONT
    if public:
        scenario = _redact_account_numbers(scenario)
    lines = yaml.safe_dump(scenario, default_flow_style=False, sort_keys=False, allow_unicode=True).splitlines()
    for i, line in enumerate(lines, start=start_row + 1):
        cell = ws.cell(row=i, column=1, value=line)
        cell.font = MONO_FONT
    # Column A's width is already set (narrow -- it's the "Year" column) by
    # _autosize()/_projection_col_widths() above this block, and is left
    # alone here -- a long line just visually overflows into the empty
    # cells to its right, which Excel does automatically for text in an
    # otherwise-empty row, without needing the column itself resized
    # (which would also widen the Year column for the whole sheet).
    return start_row + len(lines) + 1


def _write_model_parameters(ws, scenario: dict, blended_rate: float, start_row: int,
                             volatility: float = None, mc: dict = None, seed=None,
                             bucket_rates: dict = None) -> int:
    """A compact "MODEL PARAMETERS" block -- just the numbers that actually
    drove this tab's math: the blended return (and, for Monte Carlo, the
    blended volatility and the trial count / seed), plus every asset
    class's resolved expected-return / volatility assumption. One line per
    row in column A, the same single-column style as _write_scenario_config()
    (which sits just below this with the full scenario YAML for anything
    not distilled here -- both go under the chart(s), so the table and its
    chart stay together at the top of the sheet). volatility/mc/seed are
    for the Monte Carlo tab; omit them on the deterministic Projection
    tab. A class configured with tracks/floor/cap (see
    projection.resolve_asset_class_tracking() -- e.g. "Fixed Indexed
    Annuities" by default) gets an extra indented line naming what it
    tracks and its floor/cap, since that -- not the volatility figure
    right above it -- is what actually drives its Monte Carlo draw.

    bucket_rates (optional, from `report --per-bucket-growth` -- see
    projection.bucket_allocation_rows()): {bucket_key: rate} for every tax
    bucket that grows independently instead of at the household's own
    blended_rate above -- printed as a household-summary-plus-per-bucket
    breakdown, one line per bucket (label via projection.bucket_label()).

    Returns the next free row after the block."""
    lines = [("MODEL PARAMETERS", True)]
    lines.append((f"Blended growth rate (household summary, weighted by current allocation, held "
                  f"constant): {blended_rate * 100:.2f}%", False))
    if bucket_rates:
        lines.append(("Per-bucket growth rate (--per-bucket-growth -- each tax bucket grows at its OWN "
                      "blended rate instead of the household summary above):", False))
        for key in sorted(bucket_rates, key=projection_mod.bucket_label):
            lines.append((f"    {projection_mod.bucket_label(key)}: {bucket_rates[key] * 100:.2f}%", False))
    if volatility is not None:
        lines.append((f"Blended volatility (weighted-average annual stdev): {volatility:.1f} pts", False))
    if mc is not None:
        lines.append((f"Monte Carlo trials: {mc['trials']:,}", False))
        lines.append((f"Random seed: {seed if seed is not None else 'none (result differs every run)'}", False))
        lines.append((f"Monte Carlo success rate: {mc['success_rate'] * 100:.1f}%", False))
    lines.append(("", False))
    lines.append(("Asset-class return / volatility assumptions (scenario.returns):", True))
    try:
        resolved = projection_mod.resolve_asset_class_returns(scenario or {})
    except projection_mod.ProjectionError:
        resolved = {}
    try:
        tracking = projection_mod.resolve_asset_class_tracking(scenario or {})
    except projection_mod.ProjectionError:
        tracking = {}
    for name, (expected_return, vol) in resolved.items():
        vol_label = f"{vol:.1f} pts" if vol is not None else "(no volatility set)"
        lines.append((f"    {name}: {expected_return * 100:.1f}% return, {vol_label}", False))
        track = tracking.get(name)
        if track:
            lines.append((f"        Monte Carlo: tracks '{track['tracks']}', floored/capped to "
                          f"{track['floor'] * 100:.1f}%-{track['cap'] * 100:.1f}% "
                          "(replaces the volatility above for Monte Carlo)", False))
    correlations = (scenario or {}).get("returns", {}).get("correlations")
    if correlations:
        lines.append((f"Correlation overrides: "
                      f"{', '.join(f'{k}={v}' for k, v in correlations.items())}", False))
    else:
        lines.append(("Cross-class correlations: built-in macro-class defaults "
                      "(pt/projection.py DEFAULT_MACRO_CORRELATIONS)", False))

    for i, (text, bold) in enumerate(lines, start=start_row):
        cell = ws.cell(row=i, column=1, value=text)
        cell.font = BOLD_FONT if bold else BODY_FONT
    return start_row + len(lines) + 1


def _write_table(ws, headers, rows, currency_cols=(), pct_cols=(), start_row=1):
    """rows: list of tuples matching headers order."""
    for j, h in enumerate(headers, start=1):
        ws.cell(row=start_row, column=j, value=h)
    _style_header_row(ws, start_row, len(headers))
    for i, row in enumerate(rows, start=start_row + 1):
        for j, val in enumerate(row, start=1):
            cell = ws.cell(row=i, column=j, value=val)
            cell.font = BODY_FONT
            if j in currency_cols and isinstance(val, (int, float)):
                cell.number_format = CURRENCY_FMT
            if j in pct_cols and isinstance(val, (int, float)):
                cell.number_format = PCT_FMT
    return start_row + len(rows) + 1  # next free row


def _nice_axis_step(max_value: float, target_ticks: int = 8) -> float:
    """A "nice" round number (1/2/2.5/5/10 x a power of 10) for an axis
    majorUnit, sized so roughly target_ticks gridlines cover max_value --
    matplotlib's own default tick locator (see pt/charts.py) picks the
    same kind of step for the same data, which is what this is matching:
    a ~$16M max picks a $2.5M step (2.5M, 5.0M, 7.5M, ...), a ~$40M max
    picks $5M. Returns 1.0 for a non-positive max_value (shouldn't happen
    for a real balance/dollar series, but keeps this from ever dividing by
    zero or returning something nonsensical)."""
    if max_value <= 0:
        return 1.0
    raw_step = max_value / target_ticks
    magnitude = 10 ** math.floor(math.log10(raw_step))
    residual = raw_step / magnitude
    for candidate in (1, 2, 2.5, 5, 10):
        if residual <= candidate:
            return candidate * magnitude
    return 10 * magnitude  # unreachable (residual is always <= 10 by construction)


def _sized_text(size_pt: float, bold: bool = False) -> RichText:
    """A RichText with just a font size (and optionally bold) set as the
    paragraph's default run properties -- for axis tick labels/legend
    text (assigned to .txPr), which render at this size with no literal
    text of their own (Excel fills in the actual labels)."""
    return RichText(p=[Paragraph(pPr=ParagraphProperties(defRPr=CharacterProperties(
        sz=int(size_pt * 100), b=bold
    )))])


def _size_title(title_obj, size_pt: float, bold: bool = True) -> None:
    """Sets the font size on a Title object already holding literal text
    (chart.title, x_axis.title, y_axis.title -- each auto-built into a
    Title/RichText/Paragraph/RegularTextRun tree by assigning a plain
    string to it first, see _add_line_chart()) by mutating that run's
    character properties in place."""
    title_obj.tx.rich.p[0].r[0].rPr = CharacterProperties(sz=int(size_pt * 100), b=bold)


def _add_line_chart(ws, headers: list, header_row: int, n_data_rows: int, series_names: list,
                     title: str, anchor: str, data: list) -> None:
    """Adds a native Excel chart to `ws` matching pt/charts.py's matplotlib
    styling as closely as Excel's own chart model allows -- built as a
    ScatterChart (not a LineChart) specifically so BOTH axes are real
    NUMERIC value axes: a LineChart's x-axis is always a category axis in
    openpyxl (evenly spaced by position, not by value), which can only
    show "every Nth data point" -- not "every actual multiple of 5" the
    way a real year axis should read, since the data's own first year
    isn't necessarily a multiple of 5. Line/marker styling on each Series
    makes it look like a plain line chart despite the different
    underlying chart type.

    One series per name in series_names (each must be a header text a
    _write_table() call already wrote at row header_row -- e.g.
    "Taxable", "10th Percentile"), against the "Year" column, reading the
    n_data_rows immediately below header_row. anchor: the cell to place
    the chart's top-left corner at (e.g. "A50") -- callers place charts
    below/beside the data table they read from so nothing overlaps. Looks
    up each series' column by header TEXT (not a hardcoded index), since
    a table's column layout shifts depending on the household (e.g.
    has_spouse) -- see _projection_table_data(). `data` (the same
    row-tuple list passed to _write_table()) sizes both axes' bounds/step
    -- see _nice_axis_step()."""
    year_col_idx = headers.index("Year")
    series_col_idx = [headers.index(name) for name in series_names]
    years = [row[year_col_idx] for row in data]
    max_value = max(
        (row[c] for row in data for c in series_col_idx if isinstance(row[c], (int, float))), default=0
    )

    chart = ScatterChart()
    chart.scatterStyle = "lineMarker"
    chart.title = title
    _size_title(chart.title, 16)
    chart.title.overlay = False  # outside the plot area, above it -- Excel's
    # default, but set explicitly rather than relying on that default holding
    chart.style = 2
    # Excel auto-sizes the plot area by default, but doesn't always leave
    # enough room for 14pt axis titles/tick labels once they're bigger than
    # its own default (~10pt) assumption -- explicitly inset the plot area
    # (NOT chart.plot_area.layout -- ChartBase._write() overwrites that
    # with chart.layout at save time, a real openpyxl quirk) so nothing
    # overlaps regardless of what Excel's own auto-layout would have done.
    chart.layout = Layout(manualLayout=ManualLayout(
        layoutTarget="inner", xMode="edge", yMode="edge",
        x=0.12, y=0.12, w=0.85, h=0.70,
    ))

    # openpyxl leaves axis .delete as None (unset) by default, which some
    # versions of Excel treat as "don't fully render this axis" -- including
    # its title -- so it has to be set to False explicitly for the title to
    # actually show up.
    chart.x_axis.title = "Year"
    _size_title(chart.x_axis.title, 14, bold=False)
    chart.x_axis.delete = False
    chart.x_axis.axPos = "b"
    chart.x_axis.txPr = _sized_text(14)
    chart.x_axis.numFmt = "0"  # a plain year number, no thousands separator/decimal
    # Round to actual multiples of 5 (2025, 2030, ... not "every 5th data
    # point" starting from whatever year the data happens to start on) --
    # only possible because this is a real numeric axis (ScatterChart),
    # unlike a LineChart's category x-axis.
    chart.x_axis.scaling.min = (min(years) // 5) * 5
    chart.x_axis.scaling.max = -(-max(years) // 5) * 5  # ceiling division
    chart.x_axis.majorUnit = 5

    chart.y_axis.title = "Dollars"
    _size_title(chart.y_axis.title, 14, bold=False)
    chart.y_axis.delete = False
    chart.y_axis.axPos = "l"
    chart.y_axis.txPr = _sized_text(14)
    chart.y_axis.numFmt = MILLIONS_AXIS_FMT
    step = _nice_axis_step(max_value)
    chart.y_axis.majorUnit = step
    chart.y_axis.scaling.min = 0  # a balance/dollar series is never negative --
    # don't let Excel's auto-scaling pad the bottom below zero
    chart.y_axis.scaling.max = math.ceil(max_value / step) * step if max_value > 0 else step

    chart.height = 10
    chart.width = 24

    chart.legend = Legend()
    chart.legend.overlay = True
    # A manual (rather than preset) position -- Excel's built-in positions
    # are only b/t/l/r/tr, no plain top-LEFT corner -- overlay=True + a
    # manual box puts it there instead, matplotlib's loc="upper left"
    # equivalent (see pt/charts.py's _style_axis()). A legend's x/y/w/h
    # are always relative to the WHOLE chart area (unlike the plot area's
    # own manual layout above, there's no "target the plot area instead"
    # option for a legend), so this sits just inside the plot area's own
    # inset (x=0.12/y=0.12 above) rather than at the chart's literal
    # corner, which would land under the title/y-axis label instead.
    # The box's width has to scale with the longest series name -- a fixed
    # 0.28 fraction was tuned against short labels like "Total Balance"
    # and silently DROPPED entries (not wrapped -- just not rendered) for
    # longer ones like "Cumulative Healthcare Expense", discovered by
    # actually opening the generated workbook in Excel and screenshotting
    # the legend rather than trusting the openpyxl object model. ~0.125in
    # per character at 14pt plus a fixed margin for the line swatch,
    # converted to a fraction of the chart's own width (chart.width is in
    # cm; legend layout fractions are unitless 0-1 of that same box).
    max_label_chars = max((len(name) for name in series_names), default=1)
    chart_width_in = chart.width / 2.54
    legend_w = min(0.55, max(0.20, (0.35 + 0.125 * max_label_chars) / chart_width_in))
    chart.legend.layout = Layout(manualLayout=ManualLayout(
        x=0.16, y=0.16, w=legend_w, h=0.05 * max(len(series_names), 1) + 0.05,
        xMode="edge", yMode="edge",
    ))
    chart.legend.txPr = _sized_text(14)
    chart.legend.graphicalProperties = GraphicalProperties(
        solidFill="FFFFFF", ln=LineProperties(solidFill="000000", w=9525)  # 9525 EMU = 0.75pt
    )

    x_values = Reference(ws, min_col=year_col_idx + 1, min_row=header_row + 1, max_row=header_row + n_data_rows)
    for name in series_names:
        col = headers.index(name) + 1
        y_values = Reference(ws, min_col=col, min_row=header_row, max_row=header_row + n_data_rows)
        series = Series(y_values, x_values, title_from_data=True)
        series.marker.symbol = "none"
        series.smooth = False
        chart.series.append(series)
    ws.add_chart(chart, anchor)


# A chart from _add_line_chart() is chart.height=10cm tall -- ~19 rows at
# the default row height. Stack the next thing (another chart, or a text
# block) this many rows below the previous chart's anchor so they don't
# visually overlap.
_CHART_ROW_SPAN = 20


def _add_projection_charts(ws, headers, header_row, data, start_row: int) -> int:
    """The two standard projection charts (balance by bucket; cumulative
    tax + healthcare), stacked from start_row down -- shared by
    build_workbook()'s Projection tab and build_projection_workbook() so
    the layout can't drift. Returns the first free row below both charts."""
    _add_line_chart(ws, headers, header_row, len(data),
                     ["Taxable", "Traditional", "Roth", "Inherited", "Total Balance"],
                     "Balance by Bucket", f"A{start_row}", data=data)
    _add_line_chart(ws, headers, header_row, len(data),
                     ["Cumulative Tax (Retirement)", "Cumulative Healthcare Expense"],
                     "Cumulative Tax & Healthcare Expense", f"A{start_row + _CHART_ROW_SPAN}", data=data)
    return start_row + 2 * _CHART_ROW_SPAN


def _find_scatter_chart_ref(scenario: dict):
    """The `scatter_chart` file reference `roth_optimizer optimize` records
    on a saved *_optimized*.yaml's roth_conversions section (see
    roth_optimizer/cli.py's _save_optimizer_outputs()) -- the first entry
    that has one, or None. A path, relative to the scenario file's own
    directory (or absolute)."""
    for entry in (scenario or {}).get("roth_conversions") or []:
        if isinstance(entry, dict) and entry.get("scatter_chart"):
            return str(entry["scatter_chart"])
    return None


def _scatter_marker_series(ws, x_col: int, y_col: int, row: int, title: str, symbol: str, color: str,
                            size: int = 11):
    """A one-point markers-only Series reading (x_col, y_col) at `row`."""
    series = Series(Reference(ws, min_col=y_col, min_row=row, max_row=row), title=title,
                    xvalues=Reference(ws, min_col=x_col, min_row=row, max_row=row))
    series.marker.symbol = symbol
    series.marker.size = size
    series.marker.graphicalProperties = GraphicalProperties(solidFill=color, ln=LineProperties(solidFill=color))
    series.graphicalProperties.line.noFill = True
    return series


def _axis_bounds(values: list):
    """(lo, hi, step) -- "nice" round axis bounds hugging the data's own
    range, unlike the projection charts' zero-based ones: a tradeoff cloud
    between two large dollar totals would otherwise be squashed into one
    corner of an axis that starts at $0."""
    lo, hi = min(values), max(values)
    step = _nice_axis_step(hi - lo if hi > lo else max(abs(hi), 1.0))
    return math.floor(lo / step) * step, math.ceil(hi / step) * step, step


def _add_optimizer_search_sheet(wb, scenario: dict, scenario_path=None) -> None:
    """Adds an "Optimizer Search" tab if `scenario` (an *_optimized*.yaml
    written by `roth_optimizer optimize --out-scenario ... --save-iterations
    ....html`) names its scatter chart file in a roth_conversions entry's
    `scatter_chart` field -- see _find_scatter_chart_ref(). Optional and
    best-effort: no such field means no tab and no message; a field whose
    file is missing/unreadable/not a chart this tool wrote prints a
    warning to stderr and skips the tab, never failing the report.

    Excel can't display an HTML page inside a sheet, so the tab redraws
    the chart as a native Excel scatter from the data embedded in the HTML
    (roth_optimizer.scatter_chart.parse_scatter_html()) -- every candidate
    schedule as a dot, the optimized schedule and the No conversions/
    Current plan reference points marked, plus each schedule's trajectory
    through any --optimizer-projection-ages the chart was made with --
    and links to the original file for the interactive (hover) version."""
    ref = _find_scatter_chart_ref(scenario)
    if not ref:
        return
    base_dir = Path(scenario_path or scenario_mod.DEFAULT_SCENARIO_PATH).expanduser().resolve().parent
    chart_path = (base_dir / Path(ref).expanduser()).resolve()

    from roth_optimizer import scatter_chart as scatter_chart_mod  # lazy: roth_optimizer imports pt
    try:
        data = scatter_chart_mod.parse_scatter_html(chart_path.read_text(encoding="utf-8"))
    except OSError as e:
        print(f"WARNING: report skipped the 'Optimizer Search' tab -- scatter_chart '{ref}' "
              f"(resolved to {chart_path}) couldn't be read: {e.strerror or e}", file=sys.stderr)
        return
    except scatter_chart_mod.ScatterChartParseError as e:
        print(f"WARNING: report skipped the 'Optimizer Search' tab -- scatter_chart '{ref}' "
              f"(resolved to {chart_path}) isn't usable: {e}", file=sys.stderr)
        return

    ws = wb.create_sheet("Optimizer Search")
    ws["A1"] = f"Optimizer Search -- every Roth-conversion schedule tried ({chart_path.name})"
    ws["A1"].font = BOLD_FONT
    link = ws["A2"]
    link.value = "Open the interactive chart (hover for exact numbers)"
    link.hyperlink = chart_path.as_uri()
    link.font = Font(name=FONT_NAME, color="0563C1", underline="single")
    ws["A3"] = ("Each dot is one candidate schedule the search scored: lifetime taxes paid vs. after-tax "
                "estate value (valued at life expectancy, at the heir tax rate the search assumed).")
    ws["A3"].font = BODY_FONT

    ref_rows = [("Optimized (winner)", data["winner"][0], data["winner"][1], data["winner_age"])]
    ref_rows += [(r["label"], r["x"], r["y"], data["winner_age"]) for r in data["refs"]]
    series_labels = {"winner": "Optimized", "zero": "No conversions", "original": "Current plan"}
    trajectory_rows = {}
    for key, label in series_labels.items():
        points = data["age_series"].get(key) or []
        if points:
            trajectory_rows[key] = [(f"{label} @ age {pt['age']}", pt["x"], pt["y"], pt["age"]) for pt in points]

    ref_headers = ["Point", "Lifetime Taxes Paid", "After-Tax Estate Value", "Age"]
    header_row = 5
    all_ref_rows = ref_rows + [row for key in trajectory_rows for row in trajectory_rows[key]]
    next_row = _write_table(ws, ref_headers, all_ref_rows, currency_cols=(2, 3), start_row=header_row)

    cand_header_row = next_row + 2
    ws.cell(row=cand_header_row - 1, column=1, value="Every candidate schedule").font = BOLD_FONT
    cand_rows = [(i, x, y) for i, (x, y) in enumerate(data["points"], start=1)]
    _write_table(ws, ["Iteration", "Lifetime Taxes Paid", "After-Tax Estate Value"], cand_rows,
                 currency_cols=(2, 3), start_row=cand_header_row)
    _autosize(ws, [24, 22, 24, 8])

    chart = ScatterChart()
    chart.scatterStyle = "lineMarker"
    chart.title = "Lifetime taxes paid vs. after-tax estate value"
    # 11pt, not _add_line_chart()'s 16pt -- that chart reserves a 0.12
    # (of chart height) top inset above its plot area for the title,
    # this one only 0.05 (5in chart - 4in plot - 0.75in bottom margin
    # leaves just 0.25in of top margin -- see below), and a 16pt bold
    # title does not fit in 0.25in: it visibly collided with the legend
    # (also anchored at that same y=0.05) when this was first built and
    # actually screenshotted. 11pt (matching the legend's own txPr size
    # below) fits with room to spare.
    _size_title(chart.title, 11)
    chart.title.overlay = False
    # No manual layout here -- left to Excel/LibreOffice's own automatic
    # placement, which centers the title horizontally by default (what
    # was asked for) above the plot area. This only works because the
    # 0.5in top margin below (plot's manualLayout y=0.10) leaves enough
    # room for an 11pt title: an EARLIER version of this code pinned the
    # title to an explicit manualLayout box instead, because auto
    # placement collided with the legend at the original, tighter 0.25in
    # margin -- and a subsequent attempt to center that pinned title with
    # algn="ctr" wrote correct OOXML (confirmed in the raw chart XML) that
    # LibreOffice still rendered flush-left, a rendering gap rather than a
    # file error. Letting Excel/LO auto-place the title, now that there's
    # room, sidesteps both problems at once -- confirmed by actually
    # screenshotting the generated workbook.
    chart.style = 2

    # Chart area 10in x 5in; inner plot area 8.5in wide (1in left margin)
    # x 3.75in tall, leaving a 0.5in top margin (room for the centered
    # title above) and a 0.75in bottom margin -- sized explicitly (same
    # manualLayout/"inner" convention as _add_line_chart() above) rather
    # than trusting Excel's own auto-layout, which left inconsistent
    # margins depending on the viewer/version. chart.height/width are in
    # cm (openpyxl); the layout fractions below are unitless 0-1 of that
    # same chart-area box, so 1in left margin = 1/10 = 0.10, the top
    # inset for a 0.5in top margin is 0.5/5 = 0.10, and plot height
    # 3.75in / 5in chart = 0.75 -- the fractions still sum to 1
    # (0.10 top + 0.75 plot + 0.15 bottom, i.e. 0.75in, unchanged).
    chart.height = 12.70  # 5in
    chart.width = 25.40  # 10in
    chart.layout = Layout(manualLayout=ManualLayout(
        layoutTarget="inner", xMode="edge", yMode="edge",
        x=0.10, y=0.10, w=0.85, h=0.75,
    ))

    xs = [x for x, _ in data["points"]] + [r[1] for r in all_ref_rows]
    ys = [y for _, y in data["points"]] + [r[2] for r in all_ref_rows]
    for axis, values, title, pos in ((chart.x_axis, xs, "Lifetime taxes paid", "b"),
                                      (chart.y_axis, ys, "After-tax estate value", "l")):
        axis.title = title
        _size_title(axis.title, 14, bold=False)
        axis.delete = False
        axis.axPos = pos
        axis.txPr = _sized_text(12)
        axis.numFmt = MILLIONS_AXIS_FMT
        lo, hi, step = _axis_bounds(values)
        axis.scaling.min, axis.scaling.max, axis.majorUnit = lo, hi, step

    n = len(cand_rows)
    if n:
        cloud = Series(Reference(ws, min_col=3, min_row=cand_header_row + 1, max_row=cand_header_row + n),
                       title="Candidate schedules",
                       xvalues=Reference(ws, min_col=2, min_row=cand_header_row + 1, max_row=cand_header_row + n))
        cloud.marker.symbol = "circle"
        cloud.marker.size = 4
        cloud.marker.graphicalProperties = GraphicalProperties(solidFill="5B9BD5", ln=LineProperties(solidFill="5B9BD5"))
        cloud.graphicalProperties.line.noFill = True
        chart.series.append(cloud)

    marker_style = [("star", "C00000"), ("diamond", "7F7F7F"), ("square", "ED7D31")]
    for i, (label, _x, _y, _age) in enumerate(ref_rows):
        symbol, color = marker_style[i % len(marker_style)]
        chart.series.append(_scatter_marker_series(ws, 2, 3, header_row + 1 + i, label, symbol, color, size=12))

    row_cursor = header_row + 1 + len(ref_rows)
    for key, color in (("winner", "C00000"), ("zero", "7F7F7F"), ("original", "ED7D31")):
        rows_for_key = trajectory_rows.get(key)
        if not rows_for_key:
            continue
        line = Series(Reference(ws, min_col=3, min_row=row_cursor, max_row=row_cursor + len(rows_for_key) - 1),
                      title=f"{series_labels[key]} through later ages",
                      xvalues=Reference(ws, min_col=2, min_row=row_cursor,
                                        max_row=row_cursor + len(rows_for_key) - 1))
        line.marker.symbol = "circle"
        line.marker.size = 6
        line.marker.graphicalProperties = GraphicalProperties(noFill=True, ln=LineProperties(solidFill=color))
        line.graphicalProperties.line = LineProperties(solidFill=color, w=12700, prstDash="sysDot")
        line.smooth = False
        chart.series.append(line)
        row_cursor += len(rows_for_key)

    chart.legend = Legend()
    chart.legend.overlay = True  # sit ON TOP of the plot area (like
    # _add_line_chart()'s legend) rather than reserving its own strip
    # and shrinking the plot area the manualLayout above just fixed.
    # Legend box 7in x 1in, positioned so its right edge lines up with
    # the plot area's own right edge (0.10 + 0.85 = 0.95; 0.95 - 0.70
    # legend width = 0.25) and its top edge lines up with the plot
    # area's own top edge (y=0.10, same as the layout above -- moves
    # with it if that top margin ever changes again) -- "upper right of
    # the plot area", not the chart area.
    chart.legend.layout = Layout(manualLayout=ManualLayout(
        xMode="edge", yMode="edge",
        x=0.25, y=0.10, w=0.70, h=0.20,
    ))
    chart.legend.txPr = _sized_text(11)
    chart.legend.graphicalProperties = GraphicalProperties(
        solidFill="FFFFFF", ln=LineProperties(solidFill="000000", w=9525)  # 9525 EMU = 0.75pt
    )
    ws.add_chart(chart, "F5")


def _projection_table_data(rows, profile, account_columns=None):
    """headers/data/currency-columns for a projection table -- shared by
    build_workbook()'s Projection tab and build_projection_workbook() (the
    standalone `project --out *.xlsx` workbook, see pt/cli.py), so the two
    can never drift apart. account_columns, if given (from
    projection.build_account_columns()), appends one column per real
    account -- see that function's docstring for what those values mean.
    Returns (headers, data, currency_cols, has_spouse)."""
    has_spouse = bool(profile.get("people", {}).get("spouse"))
    headers = ["Year", "Primary Age"]
    if has_spouse:
        headers.append("Spouse Age")
    headers += ["Retired", "Filing Status", "Taxable", "Traditional", "Roth", "Inherited", "Total Balance",
                "Social Security", "RMD (Own)", "RMD (Inherited Trad.)", "Inherited Roth Dist.",
                "Rollover", "Roth Conversion", "IRA Distribution",
                "401(k) Contribution (Traditional)", "401(k) Contribution (Roth)",
                "401(k) After-Tax to Roth (Mega Backdoor)", "Employer Match",
                "Health Insurance",
                "Medicare Part B", "Medicare Part D IRMAA", "IRMAA Tier",
                "Cumulative Healthcare Expense", "Advisory Expense", "Liability Payment",
                "Spending", "Pre-Retirement Income", "Taxable Income", "Tax", "Cumulative Tax (Retirement)",
                "Withdrawal", "Shortfall"]
    for col in account_columns or []:
        headers.append(col["label"])

    data = []
    for r in rows:
        row = [r["year"], r["primary_age"]]
        if has_spouse:
            row.append(r["spouse_age"])
        row += ["Yes" if r["retired"] else "", r["filing_status"].upper(),
                r["taxable_balance"], r["traditional_balance"],
                r["roth_balance"], r["inherited_balance"], r["total_balance"], r["ss_income"],
                r["rmd_own"], r["rmd_inherited_traditional"], r["inherited_roth_distribution"],
                r["rollover"], r["roth_conversion"], r["ira_distribution"],
                r["contribution_traditional"], r["contribution_roth"],
                r["after_tax_roth_contribution"], r["employer_match"],
                r["health_insurance"],
                r["medicare_part_b"], r["medicare_part_d_irmaa"], r["irmaa_tier"],
                r["cumulative_healthcare_expense"], r["advisory_expense"], r["liability_payment"],
                r["spending"], r["pre_retirement_income"], r["taxable_income"], r["tax"],
                r["cumulative_tax_in_retirement"], r["withdrawal"], r["shortfall"]]
        for col in account_columns or []:
            row.append((r.get("accounts") or {}).get(col["account_number"], 0.0))
        data.append(tuple(row))

    first_money_col = 5 if has_spouse else 4
    non_currency_cols = {headers.index("IRMAA Tier") + 1, headers.index("Filing Status") + 1}
    money_cols = tuple(c for c in range(first_money_col, len(headers) + 1) if c not in non_currency_cols)
    return headers, data, money_cols, has_spouse


def _projection_col_widths(has_spouse, n_account_columns=0):
    return ([8, 12] + ([12] if has_spouse else [])
            + [10, 12, 14, 14, 14, 14, 16, 14, 12, 18, 16, 14, 16, 16, 18, 16, 24, 16, 16, 14, 18, 10, 20, 16, 16, 14, 18, 16, 12, 22, 14, 14]
            + [16] * n_account_columns)


def build_workbook(conn, snapshot_id: int, profile_path=None, scenario_path=None, verbose: bool = False,
                    monte_carlo: bool = False, trials: int = 1000, seed: int = None, public: bool = False,
                    per_bucket_growth: bool = False):
    """profile_path/scenario_path default to the standard planning-profile
    and retirement-scenario locations (see pt/planning.py, pt/scenario.py).
    The Projection tab is included only if both load successfully -- most
    households won't have a scenario configured, and that's fine; the rest
    of the workbook doesn't depend on it. verbose (see `report -v`/
    `--verbose` in pt/cli.py) adds a column for every account currently
    over VERBOSE_ACCOUNT_THRESHOLD, same as `project --verbose`.

    If the scenario configures scenario.hypothetical_accounts, the
    Projection/Monte Carlo tabs' own math (growth rate, per-bucket
    breakdown, the simulation itself) comes ENTIRELY from that made-up
    household -- NOT from this snapshot -- exactly like `project`/
    `monte-carlo --scenario` do; every other tab (Holdings, Allocation,
    IPS comparisons, ...) still reports on the real snapshot regardless,
    since those have nothing to do with the scenario at all. The profile
    itself follows the same rule (planning.resolve_profile()): if
    profile_path isn't given AND the scenario is a self-contained
    hypothetical one (hypothetical_accounts + its own top-level `people`),
    the profile is built from that instead of needing a --profile file
    that matches the made-up owners.

    monte_carlo (see `report --monte-carlo`): also run run_monte_carlo()
    from the same profile/scenario and add a "Monte Carlo" tab immediately
    after "Projection" -- trials/seed are the same knobs the standalone
    `monte-carlo` command takes. Skipped silently (like the Projection tab)
    if the scenario/profile don't load or don't project cleanly.

    public (see `report --public`, the DEFAULT): the shareable view. Drops
    the Account Number column (Accounts tab), the Holdings / Investment
    Expenses / IPS Comparison (Macro) / IPS Comparison / IPS by Account /
    Unclassified tabs, and the Summary tab's two IPS-target tables; redacts
    account_number values in the Projection/Monte Carlo scenario dump. The
    inherited-account decedent birth/death dates are shown on the Accounts
    tab in BOTH views. --private (public=False) keeps everything.

    per_bucket_growth (see `report --per-bucket-growth`): each tax bucket
    (Taxable, each Traditional owner/type pool, each Roth owner, each
    Inherited account) grows at its OWN blended rate/correlated Monte
    Carlo draw from what THAT bucket actually holds, instead of one
    household-wide rate applied to every bucket -- see
    projection.bucket_allocation_rows(). Adds a household-summary-plus-
    per-bucket-detail breakdown to the MODEL PARAMETERS block on both the
    Projection and Monte Carlo tabs."""
    snap = conn.execute("SELECT * FROM snapshots WHERE id = ?", (snapshot_id,)).fetchone()
    alloc_rows, total = alloc.allocation_by_asset_class(conn, snapshot_id)
    macro_rows, _ = alloc.allocation_by_macro_class(conn, snapshot_id)
    acct_type_rows, _ = alloc.allocation_by_account_type(conn, snapshot_id)
    comparison = ips_mod.compare_to_targets(conn, alloc_rows, total)
    macro_comparison = ips_mod.compare_to_macro_targets(conn, macro_rows, total)
    by_account = alloc.allocation_by_account(conn, snapshot_id)
    by_account_targets = ips_mod.compare_to_targets_by_account(conn, by_account)
    exp_accounts, exp_totals = alloc.expense_summary(conn, snapshot_id)

    holdings_raw = conn.execute(
        """
        SELECT a.account_number, a.account_name, a.account_type, a.owner,
               h.symbol, h.description, h.quantity, h.last_price, h.current_value
        FROM holdings h
        JOIN accounts a ON a.account_number = h.account_number
        WHERE h.snapshot_id = ?
        ORDER BY a.account_name, a.account_number, h.current_value DESC
        """,
        (snapshot_id,),
    ).fetchall()
    # Asset class/style/sector labeling can't be a plain SQL join once a
    # symbol may be split across several categories, so each is resolved
    # per-symbol here.
    holdings = [
        dict(
            h,
            asset_class=classifier.get_classification_label(conn, h["symbol"]),
            style=attributes.get_style_label(conn, h["symbol"]),
            sector=attributes.get_sector_label(conn, h["symbol"]),
            expense_ratio=attributes.get_expense_ratio(conn, h["symbol"]),
        )
        for h in holdings_raw
    ]

    accounts = conn.execute(
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

    unclassified = classifier.list_unclassified(conn, snapshot_id)

    # Load the retirement scenario once, up front -- used both for the
    # inherited-account decedent dates on the Accounts tab (below) and, if
    # it projects, the Projection/Monte Carlo tabs further down. Best-effort:
    # most households have no scenario, and the rest of the workbook doesn't
    # need one.
    try:
        scenario = scenario_mod.load_scenario(scenario_path or scenario_mod.DEFAULT_SCENARIO_PATH)
    except scenario_mod.ScenarioError:
        scenario = None
    # For surfacing each inherited account's decedent date of birth / date
    # of death (and the SECURE Act 10-year deadline) on the Accounts tab, in
    # both views. inherited_by_account keys the entries that name a real
    # account_number (matched against held accounts below); all_inherited is
    # every entry, so a hypothetical_value future-inheritance entry (no
    # account_number) still gets its own row.
    all_inherited = list(scenario.get("inherited_accounts", []) if scenario else [])
    inherited_by_account = {
        str(e["account_number"]): e for e in all_inherited if e.get("account_number") is not None
    }

    wb = Workbook()

    # --- Summary tab
    ws = wb.active
    ws.title = "Summary"
    ws["A1"] = "Household Rebalancing Summary"
    ws["A1"].font = Font(name=FONT_NAME, bold=True, size=14)
    ws["A2"] = (f"Snapshot #{snap['id']}  |  Imported {snap['imported_at']}  |  "
                f"Source: {_source_file_display(snap['source_file'], public)}")
    ws["A2"].font = BODY_FONT
    ws["A3"] = f"Household total value: ${total:,.0f}"
    ws["A3"].font = BOLD_FONT

    row = 5
    # The two IPS-target tables are internal-planning detail -- dropped from
    # the public/shareable view, which keeps only Allocation by Account Type.
    if not public:
        ws.cell(row=row, column=1, value="Actual vs. IPS Target by Macro Class").font = BOLD_FONT
        row += 1
        headers = ["Macro Class", "Actual %", "Target %", "Diff %", "$ to Trade", "Action"]
        data = [(c["asset_class"], c["actual_pct"], c["target_pct"], c["diff_pct"], c["diff_dollars"], c["action"])
                for c in macro_comparison]
        row = _write_table(ws, headers, data, currency_cols=(5,), pct_cols=(2, 3, 4), start_row=row)

        row += 1
        ws.cell(row=row, column=1, value="Actual vs. IPS Target by Asset Class").font = BOLD_FONT
        row += 1
        headers = ["Asset Class", "Actual %", "Target %", "Diff %", "$ to Trade", "Action"]
        data = [(c["asset_class"], c["actual_pct"], c["target_pct"], c["diff_pct"], c["diff_dollars"], c["action"])
                for c in comparison]
        row = _write_table(ws, headers, data, currency_cols=(5,), pct_cols=(2, 3, 4), start_row=row)
        row += 1

    ws.cell(row=row, column=1, value="Allocation by Account Type").font = BOLD_FONT
    row += 1
    headers = ["Account Type", "Value", "% of Household"]
    data = [(r["account_type"], r["value"], r["pct"]) for r in acct_type_rows]
    row = _write_table(ws, headers, data, currency_cols=(2,), pct_cols=(3,), start_row=row)

    _autosize(ws, [24, 14, 14, 12, 16, 20])

    # --- Holdings tab (private only -- per-symbol positions)
    if not public:
        ws = wb.create_sheet("Holdings")
        headers = ["Account", "Owner", "Account Type", "Symbol", "Description", "Quantity",
                   "Last Price", "Current Value", "Asset Class", "Style", "Sector", "Expense Ratio"]
        data = [(h["account_name"], h["owner"] or "", h["account_type"], h["symbol"], h["description"],
                  h["quantity"], h["last_price"], h["current_value"], h["asset_class"], h["style"],
                  h["sector"], h["expense_ratio"])
                 for h in holdings]
        _write_table(ws, headers, data, currency_cols=(7, 8))
        for row in ws.iter_rows(min_row=2, min_col=12, max_col=12):
            if isinstance(row[0].value, (int, float)):
                row[0].number_format = EXPENSE_FMT
        _autosize(ws, [22, 14, 16, 10, 34, 12, 12, 14, 20, 22, 24, 14])
        ws.freeze_panes = "A2"

    # --- Accounts tab. The Account Number column is dropped in the public
    # view; the inherited-account decedent birth/death dates + 10-year
    # deadline (from scenario.inherited_accounts, matched by account number)
    # are shown in BOTH views when there are any.
    ws = wb.create_sheet("Accounts")
    num_cols = [] if public else ["Account Number"]
    inh_cols = ["Decedent Date of Birth", "Decedent Date of Death",
                "Inherited-Account 10-Year Deadline"] if all_inherited else []
    headers = num_cols + ["Account Name", "Owner", "Account Type", "Value"] + inh_cols
    matched_inherited = set()
    data = []
    for a in accounts:
        acct_num = str(a["account_number"])
        row = ([] if public else [a["account_number"]]) + [
            a["account_name"], a["owner"] or "", a["account_type"], a["value"],
        ]
        if all_inherited:
            entry = inherited_by_account.get(acct_num)
            if entry:
                matched_inherited.add(acct_num)
                row += _inherited_date_cells(entry)
            else:
                row += ["", "", ""]
        data.append(tuple(row))
    # Any inherited_accounts entry not tied to a held account in this
    # snapshot (a hypothetical_value future inheritance, or an account not
    # in the import) still gets a row so its decedent dates aren't lost.
    for entry in all_inherited:
        acct_num = entry.get("account_number")
        if acct_num is not None and str(acct_num) in matched_inherited:
            continue
        kind = "Inherited Roth IRA" if entry.get("type") == "roth" else "Inherited IRA"
        label = f"(inherited {entry.get('type') or ''} -- not in current snapshot)".replace("  ", " ")
        row = ([] if public else [acct_num or ""]) + [
            label, (entry.get("beneficiary") or ""), kind, "",
        ] + _inherited_date_cells(entry)
        data.append(tuple(row))
    value_col = len(num_cols) + 4
    _write_table(ws, headers, data, currency_cols=(value_col,))
    _autosize(ws, ([] if public else [18]) + [26, 14, 20, 16] + ([16, 16, 16] if all_inherited else []))

    # --- Expenses tab (private only): each account's calculated blended
    # expense ratio and annual dollar cost (holdings' own per-symbol expense
    # ratios, weighted by value, PLUS the account's own flat fee if set via
    # `accounts set-expense-ratio` -- e.g. a managed account's advisory fee
    # -- see pt/allocation.py's expense_summary()), plus a household total.
    if not public:
        ws = wb.create_sheet("Investment Expenses")
        headers = ["Account Name", "Account Type", "Owner", "Value", "Account Fee %",
                   "Holdings Expense $/yr", "Account Fee $/yr", "Total Expense $/yr", "Blended Ratio"]
        data = [(a["account_name"], a["account_type"], a["owner"] or "", a["value"], a["account_fee_pct"],
                  a["holdings_expense_dollars"], a["account_fee_dollars"], a["total_expense_dollars"],
                  a["blended_ratio"])
                 for a in exp_accounts]
        next_row = _write_table(ws, headers, data, currency_cols=(4, 6, 7, 8), pct_cols=(5, 9))
        total_row = (
            "HOUSEHOLD TOTAL", "", "", exp_totals["value"], None,
            None, None, exp_totals["total_expense_dollars"], exp_totals["blended_ratio"],
        )
        for j, val in enumerate(total_row, start=1):
            cell = ws.cell(row=next_row, column=j, value=val)
            cell.font = BOLD_FONT
            if j in (4, 8) and isinstance(val, (int, float)):
                cell.number_format = CURRENCY_FMT
            if j == 9 and isinstance(val, (int, float)):
                cell.number_format = PCT_FMT
        next_row += 2
        if exp_totals["missing_value"] > 0:
            missing_pct = exp_totals["missing_value"] / exp_totals["value"] if exp_totals["value"] else 0.0
            note = ws.cell(
                row=next_row, column=1,
                value=(
                    f"${exp_totals['missing_value']:,.0f} ({missing_pct*100:.1f}%) in holdings have no expense "
                    "ratio on file -- the ratios/costs above understate the true total. Set one with "
                    "expense-ratio set / accounts set-expense-ratio (see pt/README.md)."
                ),
            )
            note.font = BODY_FONT
        _autosize(ws, [26, 16, 14, 16, 14, 20, 16, 18, 14])
        ws.freeze_panes = "A2"

    # --- Allocation tab (asset class x account, with % of each account) --
    # kept in both views (account names, no numbers).
    ws = wb.create_sheet("Allocation")
    ws["A1"] = "Value by Account and Asset Class"
    ws["A1"].font = BOLD_FONT
    headers = ["Account Name", "Owner", "Account Type", "Macro Class", "Asset Class", "Value", "% of Account"]
    data = [(r["account_name"], r["owner"] or "", r["account_type"],
              classifier.SUBCLASS_MACRO.get(r["asset_class"], ""), r["asset_class"],
              r["value"], r["pct_of_account"])
            for r in by_account]
    _write_table(ws, headers, data, currency_cols=(6,), pct_cols=(7,), start_row=3)
    _autosize(ws, [24, 14, 16, 14, 24, 16, 14])

    # --- IPS Comparison tabs (macro + asset class) and IPS by Account --
    # internal-planning detail, private view only.
    if not public:
        ws = wb.create_sheet("IPS Comparison (Macro)")
        headers = ["Macro Class", "Actual %", "Target %", "Diff %", "$ to Trade", "Action"]
        data = [(c["asset_class"], c["actual_pct"], c["target_pct"], c["diff_pct"], c["diff_dollars"], c["action"])
                for c in macro_comparison]
        _write_table(ws, headers, data, currency_cols=(5,), pct_cols=(2, 3, 4))
        _autosize(ws, [24, 12, 12, 12, 16, 20])

        ws = wb.create_sheet("IPS Comparison")
        headers = ["Asset Class", "Actual %", "Target %", "Diff %", "$ to Trade", "Action"]
        data = [(c["asset_class"], c["actual_pct"], c["target_pct"], c["diff_pct"], c["diff_dollars"], c["action"])
                for c in comparison]
        _write_table(ws, headers, data, currency_cols=(5,), pct_cols=(2, 3, 4))
        _autosize(ws, [24, 12, 12, 12, 16, 20])

        # only accounts with a target override -- see
        # ips.compare_to_targets_by_account for why non-override accounts are omitted
        if by_account_targets:
            ws = wb.create_sheet("IPS by Account")
            headers = ["Account", "Asset Class", "Actual %", "Target %", "Diff %", "$ to Trade", "Action", "Override?"]
            data = []
            for acct_num, info in by_account_targets.items():
                for c in info["rows"]:
                    data.append((
                        info["account_name"], c["asset_class"], c["actual_pct"], c["target_pct"],
                        c["diff_pct"], c["diff_dollars"], c["action"], "Yes" if c["is_override"] else "",
                    ))
            _write_table(ws, headers, data, currency_cols=(6,), pct_cols=(3, 4, 5))
            _autosize(ws, [24, 24, 12, 12, 12, 16, 20, 10])

    # --- Unclassified tab (private only)
    if not public:
        ws = wb.create_sheet("Unclassified")
        if unclassified:
            headers = ["Symbol", "Description", "Total Value"]
            data = [(u["symbol"], u["description"], u["total_value"]) for u in unclassified]
            _write_table(ws, headers, data, currency_cols=(3,))
            _autosize(ws, [12, 40, 16])
        else:
            ws["A1"] = "Nothing unclassified -- every holding has an asset class. Nice."
            ws["A1"].font = BODY_FONT

    # --- Projection tab (only if a planning profile AND a retirement scenario
    # are both configured -- see pt/projection.py for the model itself).
    # `scenario` was already loaded near the top of this function.
    try:
        if scenario is None:
            raise scenario_mod.ScenarioError("no retirement scenario configured")
        profile = planning.resolve_profile(profile_path, scenario)
        inherited_schedules = projection_mod.build_inherited_schedules(scenario, profile)
        if scenario.get("hypothetical_accounts"):
            # A made-up household -- the Projection/Monte Carlo tabs' own
            # math (growth rate, per-bucket breakdown, the simulation
            # itself) comes ENTIRELY from the scenario's own
            # hypothetical_accounts, same as `project`/`monte-carlo
            # --scenario` when this section is configured -- NOT from the
            # database snapshot the rest of this workbook (Holdings,
            # Allocation, IPS comparisons, ...) reports on. See
            # projection.build_hypothetical_accounts().
            proj_account_rows, proj_alloc_rows, proj_total = projection_mod.build_hypothetical_accounts(
                scenario, profile
            )
            proj_class_rows = projection_mod.build_hypothetical_account_class_rows(scenario, profile)
        else:
            proj_account_rows = alloc.account_values(conn, snapshot_id)
            proj_alloc_rows, proj_total = alloc_rows, total
            # allocation.allocation_by_account() (`by_account`, already
            # fetched above for the Allocation tab) is exactly the
            # per-account, per-asset-class breakdown bucket_allocation_
            # rows() needs.
            proj_class_rows = by_account
        account_columns = projection_mod.build_account_columns(
            proj_account_rows, inherited_schedules, profile
        ) if verbose else None
        blended_rate = projection_mod.compute_blended_growth_rate(proj_alloc_rows, proj_total, scenario)
        bucket_allocations = bucket_rates = None
        if per_bucket_growth:
            bucket_allocations = projection_mod.bucket_allocation_rows(proj_class_rows, inherited_schedules, profile)
            bucket_rates = {
                key: projection_mod.compute_blended_growth_rate(bucket_rows, bucket_total, scenario)
                for key, (bucket_rows, bucket_total) in bucket_allocations.items()
            }
        projection_rows = projection_mod.project(profile, scenario, proj_account_rows, blended_rate=blended_rate,
                                                   account_columns=account_columns, bucket_rates=bucket_rates)
        projection_ctx = (profile, scenario, proj_account_rows, blended_rate, bucket_allocations,
                           proj_alloc_rows, proj_total)
    except (planning.ProfileError, scenario_mod.ScenarioError, projection_mod.ProjectionError):
        projection_rows = None
        account_columns = None
        bucket_rates = None
        projection_ctx = None

    if projection_rows:
        ws = wb.create_sheet("Projection")
        ws["A1"] = f"Retirement Growth Projection -- blended growth rate {blended_rate*100:.2f}% (held constant)"
        ws["A1"].font = BOLD_FONT
        headers, data, money_cols, has_spouse = _projection_table_data(projection_rows, profile, account_columns)
        header_row = 3
        next_row = _write_table(ws, headers, data, currency_cols=money_cols, start_row=header_row)
        _autosize(ws, _projection_col_widths(has_spouse, len(account_columns or [])))
        next_row = _add_projection_charts(ws, headers, header_row, data, next_row + 2)
        next_row = _write_model_parameters(ws, scenario, blended_rate, next_row + 1, bucket_rates=bucket_rates)
        next_row = _write_scenario_config(ws, scenario, next_row + 1, public=public)

    # --- Monte Carlo tab (only with report --monte-carlo, and only if the
    # Projection tab's own inputs loaded/projected cleanly -- same scenario
    # and profile). create_sheet() appends, so this lands right after
    # "Projection".
    if monte_carlo and projection_ctx is not None:
        (mc_profile, mc_scenario, mc_account_rows, mc_blended_rate, mc_bucket_allocations,
         mc_alloc_rows, mc_total) = projection_ctx
        try:
            volatility = projection_mod.compute_blended_volatility(mc_alloc_rows, mc_total, mc_scenario)
            mc = projection_mod.run_monte_carlo(
                mc_profile, mc_scenario, mc_account_rows, mc_blended_rate, volatility,
                mc_alloc_rows, mc_total, trials=trials, seed=seed, bucket_allocations=mc_bucket_allocations,
            )
        except projection_mod.ProjectionError:
            mc = None
        if mc is not None:
            _populate_monte_carlo_sheet(wb.create_sheet("Monte Carlo"), mc, mc_blended_rate, mc_scenario,
                                         seed, public=public, bucket_rates=bucket_rates)

    # --- Optimizer Search tab (only when the scenario is an *_optimized*.yaml
    # that names its scatter chart file -- see _add_optimizer_search_sheet()).
    # Independent of whether the scenario projected above: it only reads the
    # chart file. A missing/unreadable file warns and skips, never fails.
    if scenario is not None:
        _add_optimizer_search_sheet(wb, scenario, scenario_path)

    return wb


def build_projection_workbook(rows, profile, blended_rate: float, account_columns=None, scenario: dict = None,
                               bucket_rates: dict = None):
    """A standalone single-sheet workbook for `project --out *.xlsx` (see
    pt/cli.py) -- same table as build_workbook()'s Projection tab, plus
    verbose per-account columns if account_columns is given (from
    projection.build_account_columns(), when --verbose is also passed).
    bucket_rates (optional, `project --per-bucket-growth`): see
    _write_model_parameters()'s own bucket_rates doc.
    Layout: table, then the two charts, then (if scenario is given) a
    MODEL PARAMETERS block and the full SCENARIO CONFIGURATION YAML -- see
    _add_projection_charts()/_write_model_parameters()/_write_scenario_config()."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Projection"
    ws["A1"] = f"Retirement Growth Projection -- blended growth rate {blended_rate*100:.2f}% (held constant)"
    ws["A1"].font = Font(name=FONT_NAME, bold=True, size=14)
    headers, data, money_cols, has_spouse = _projection_table_data(rows, profile, account_columns)
    header_row = 3
    next_row = _write_table(ws, headers, data, currency_cols=money_cols, start_row=header_row)
    _autosize(ws, _projection_col_widths(has_spouse, len(account_columns or [])))
    ws.freeze_panes = "A4"
    next_row = _add_projection_charts(ws, headers, header_row, data, next_row + 2)
    if scenario:
        next_row = _write_model_parameters(ws, scenario, blended_rate, next_row + 1, bucket_rates=bucket_rates)
        next_row = _write_scenario_config(ws, scenario, next_row + 1)
    return wb


def _populate_monte_carlo_sheet(ws, mc: dict, blended_rate: float, scenario: dict = None, seed=None,
                                 public: bool = False, bucket_rates: dict = None):
    """Fills `ws` with the Monte Carlo view: the run's headline verdict, a
    percentile "fan chart" table (10th/50th/90th percentile total balance
    per year), the native Excel fan chart right below it (see
    _add_line_chart()), a shortfall-year distribution table if any trial
    ran short (projection.shortfall_year_distribution() -- how many
    trials' first shortfall fell in each year, so a reader can see
    whether failures cluster instead of just the bare count), then a
    MODEL PARAMETERS block + the full scenario YAML under that. Shared by
    the standalone `monte-carlo --out *.xlsx` workbook and `report
    --monte-carlo`'s "Monte Carlo" tab so the two can't drift. Mirrors
    projection.format_monte_carlo_table() for Excel instead of text.
    bucket_rates (optional, `report --per-bucket-growth`): each bucket's
    own mean blended rate, for the MODEL PARAMETERS block's breakdown --
    the actual per-year Monte Carlo draw for each bucket was already a
    properly correlated one, from its own asset mix (see run_monte_carlo()'s
    bucket_allocations param); this is just the means, for display."""
    ws["A1"] = (f"Monte Carlo Retirement Projection -- {mc['trials']:,} trials, mean return "
                f"{blended_rate*100:.2f}%, volatility {mc['volatility']:.1f} pts")
    ws["A1"].font = Font(name=FONT_NAME, bold=True, size=14)
    successes = mc["outcomes"]["ok"] + mc["outcomes"]["estate"]
    ws["A2"] = (f"Success rate: {mc['success_rate']*100:.1f}% ({successes:,} of {mc['trials']:,} trials)  |  "
                f"Median final balance: ${mc['median_final_balance']:,.0f}  |  "
                f"Median cumulative tax (retired): ${mc['median_cumulative_tax']:,.0f}")
    ws["A2"].font = BOLD_FONT

    headers = ["Year", "10th Percentile", "Median", "90th Percentile"]
    data = [(row["year"], row["p10"], row["p50"], row["p90"]) for row in mc["percentiles_by_year"]]
    header_row = 4
    next_row = _write_table(ws, headers, data, currency_cols=(2, 3, 4), start_row=header_row)
    _autosize(ws, [8, 18, 18, 18])
    ws.freeze_panes = "A5"
    _add_line_chart(ws, headers, header_row, len(data),
                     ["10th Percentile", "Median", "90th Percentile"],
                     "Total Balance (10th/Median/90th Percentile)", f"A{next_row + 2}", data=data)
    next_row = next_row + 2 + _CHART_ROW_SPAN
    dist = projection_mod.shortfall_year_distribution(mc)
    if dist:
        ws.cell(row=next_row, column=1,
                value="Shortfall year distribution (first year each failing trial ran short):").font = BOLD_FONT
        next_row = _write_table(ws, ["Year", "Trials"], dist, start_row=next_row + 1)
    next_row = _write_model_parameters(ws, scenario, blended_rate, next_row + 1,
                                        volatility=mc["volatility"], mc=mc, seed=seed, bucket_rates=bucket_rates)
    if scenario:
        next_row = _write_scenario_config(ws, scenario, next_row + 1, public=public)
    return ws


def build_monte_carlo_workbook(mc: dict, blended_rate: float, scenario: dict = None, seed=None,
                                bucket_rates: dict = None):
    """A standalone single-sheet workbook for `monte-carlo --out *.xlsx`
    (see pt/cli.py), the Monte Carlo counterpart to
    build_projection_workbook(). All the actual layout is in
    _populate_monte_carlo_sheet(), shared with `report --monte-carlo`.
    bucket_rates (optional, `monte-carlo --per-bucket-growth`): see
    _write_model_parameters()'s own bucket_rates doc."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Monte Carlo"
    _populate_monte_carlo_sheet(ws, mc, blended_rate, scenario, seed, bucket_rates=bucket_rates)
    return wb
