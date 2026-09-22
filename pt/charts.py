"""Matplotlib chart generation for `project`/`monte-carlo --out *.png` (see
pt/cli.py). Entirely optional -- matplotlib is NOT in requirements.txt,
the same treatment as yfinance (see scripts/update_market_data.py): the
core tool never needs it, only these two functions do, and pt/cli.py
gives a clear error (pointing at requirements-optional.txt) if it isn't
installed, rather than importing it at module load time here.

Two chart types, matching pt/report.py's Excel charts (see
_add_line_chart()) so the two delivery mechanisms show the same data:
  save_projection_chart() -- project()'s deterministic per-year rows:
    a balance-by-bucket panel (Taxable/Traditional/Roth/Inherited/Total)
    and a cumulative-cost panel (tax paid while retired, healthcare
    expense), stacked in one PNG.
  save_monte_carlo_chart() -- run_monte_carlo()'s percentiles_by_year:
    total balance as a shaded 10th-90th percentile band with the median
    as a solid line (a "fan chart") -- matplotlib's fill_between makes
    this easy; openpyxl's native charts can't easily do a shaded band, so
    this is the one place the two delivery mechanisms genuinely look
    different, not just differently hosted.
"""


def _currency_formatter(value, _pos=None) -> str:
    """1234567 -> '$1.2M', 45000 -> '$45K', 900 -> '$900' -- for a chart
    axis, where full comma-formatted dollar amounts would be too wide to
    read once total_balance reaches into the tens of millions."""
    sign = "-" if value < 0 else ""
    value = abs(value)
    if value >= 1_000_000:
        return f"{sign}${value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{sign}${value / 1_000:.0f}K"
    return f"{sign}${value:.0f}"


def _style_axis(ax, title: str, ylabel: str = "Dollars"):
    from matplotlib.ticker import FuncFormatter

    ax.set_title(title)
    ax.set_xlabel("Year")
    ax.set_ylabel(ylabel)
    ax.yaxis.set_major_formatter(FuncFormatter(_currency_formatter))
    ax.grid(True, alpha=0.3)
    ax.legend(loc="upper left", fontsize=8)


def save_projection_chart(rows: list, profile: dict, out_path) -> None:
    """Saves a two-panel PNG from project()'s row list (see
    pt/projection.py) to out_path: balance by bucket (Taxable/
    Traditional/Roth/Inherited/Total) on top, cumulative tax paid while
    retired + cumulative healthcare expense on the bottom -- both vs.
    year. Raises ImportError if matplotlib isn't installed (see module
    docstring; pt/cli.py catches this with a clearer message)."""
    import matplotlib
    matplotlib.use("Agg")  # headless -- this tool never opens a GUI window
    import matplotlib.pyplot as plt

    years = [r["year"] for r in rows]
    primary_name = profile["people"]["primary"].get("name", "Primary").capitalize()

    fig, (ax_balance, ax_cost) = plt.subplots(2, 1, figsize=(11, 9))

    ax_balance.plot(years, [r["taxable_balance"] for r in rows], label="Taxable")
    ax_balance.plot(years, [r["traditional_balance"] for r in rows], label="Traditional")
    ax_balance.plot(years, [r["roth_balance"] for r in rows], label="Roth")
    ax_balance.plot(years, [r["inherited_balance"] for r in rows], label="Inherited")
    ax_balance.plot(years, [r["total_balance"] for r in rows], label="Total", linewidth=2.5, color="black")
    _style_axis(ax_balance, f"Balance by Bucket -- {primary_name}'s Household")

    ax_cost.plot(years, [r["cumulative_tax_in_retirement"] for r in rows],
                 label="Cumulative Tax (Retirement)", color="firebrick")
    ax_cost.plot(years, [r["cumulative_healthcare_expense"] for r in rows],
                 label="Cumulative Healthcare Expense", color="darkorange")
    _style_axis(ax_cost, "Cumulative Tax & Healthcare Expense")

    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def save_monte_carlo_chart(mc: dict, out_path) -> None:
    """Saves a "fan chart" PNG from run_monte_carlo()'s result (see
    pt/projection.py) to out_path: total balance as a shaded 10th-90th
    percentile band with the median as a solid line, vs. year. Raises
    ImportError if matplotlib isn't installed (see module docstring)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    rows = mc["percentiles_by_year"]
    years = [r["year"] for r in rows]
    p10 = [r["p10"] for r in rows]
    p50 = [r["p50"] for r in rows]
    p90 = [r["p90"] for r in rows]

    fig, ax = plt.subplots(figsize=(11, 6.5))
    ax.fill_between(years, p10, p90, alpha=0.25, color="steelblue", label="10th-90th percentile")
    ax.plot(years, p50, color="steelblue", linewidth=2, label="Median")
    _style_axis(ax, "Total Balance")

    successes = mc["outcomes"]["ok"] + mc["outcomes"]["estate"]
    fig.suptitle(f"Monte Carlo -- {mc['trials']:,} trials, {mc['success_rate']*100:.1f}% success rate "
                 f"({successes:,}/{mc['trials']:,})")
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
