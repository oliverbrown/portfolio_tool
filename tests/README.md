# Regression suite

Formalizes the manual checks this project's own README.md/QUICKSTART.md
walkthroughs (and every version's own commit message) already describe --
runs entirely against `test_data/`'s synthetic Jordan/Casey household, in
throwaway temp databases (see `tests/_helpers.py`'s `TempDB`). Never
touches your real `~/.portfolio_tool` database, and leaves nothing behind
in the repo.

Uses Python's built-in `unittest` -- no new dependency, consistent with
this project's existing dependency-light philosophy (`requirements.txt`
is just `openpyxl`/`PyYAML`; everything else is optional).

## Running it

```bash
# The whole suite
python3 -m unittest discover -s tests -t . -v

# One file
python3 -m unittest tests.test_import -v
python3 -m unittest tests.test_projection -v
python3 -m unittest tests.test_roth_optimizer -v
python3 -m unittest tests.test_cli_smoke -v

# One test
python3 -m unittest tests.test_import.SchwabImportTests.test_multi_account_file_splits_correctly -v
```

`-t .` (top-level directory = repo root) is required for `discover` --
without it, `tests/_helpers.py`'s package-relative imports (`from .
import _helpers`) fail with `ImportError: attempted relative import with
no known parent package`, since `discover` otherwise treats `tests/`
itself as the top-level directory rather than a subpackage of the repo.

## What's covered

- **`test_import.py`** -- broker detection (`pt/importer.py`'s
  `detect_broker()`), each broker's own parser (Fidelity/Schwab/E*TRADE)
  in isolation, multi-file merging and Joint-account detection, and that
  a full multi-broker household classifies with zero unclassified symbols
  against the starter seed data.
- **`test_projection.py`** -- `compute_blended_growth_rate()`/
  `compute_blended_volatility()` against both the legacy 2-bucket
  `returns.growth`/`.fixed_income` shape and the newer per-class
  `returns.asset_classes` shape (including the subclass-then-macro
  fallback order and the "unconfigured held class raises a clear error"
  behavior), `project()` against a real imported snapshot (structural
  invariants like total_balance always equaling its own component
  buckets, not just "doesn't crash"), and `run_monte_carlo()` (shape,
  seeded reproducibility, and a direct statistical check that the
  correlated per-asset-class draw actually produces a tighter spread at
  lower correlation -- not just that it runs).
- **`test_roth_optimizer.py`** -- the search against a small self-contained
  hypothetical-account scenario (no database needed): never scores below
  the no-conversions baseline, respects `max_conversion_per_year`, and is
  reproducible given a seed.
- **`test_cli_smoke.py`** -- actually invokes each package's CLI as a
  subprocess (`python3 -m pt.cli ...`, `roth_optimizer.cli`,
  `scenario_engine.cli`) the way a real user would, catching argparse
  wiring bugs (wrong flag name, a positional/optional ordering issue like
  `--account` needing to come before the file list) the internal-function
  tests above can't see, since they never go through argument parsing at
  all.

## What's NOT covered (be aware, don't assume)

This is a regression safety net built up from the checks this project's
own development already relies on -- not exhaustive unit coverage. In
particular: `pt/report.py`'s Excel chart generation (formatting details
were verified by hand this session, unzipping generated `.xlsx` files and
reading the raw chart XML -- see the README's own chart-formatting
history), `pt/tax.py`'s bracket/RMD/IRMAA arithmetic in detail (exercised
indirectly through `project()`, not asserted against by exact figure),
`scenario_engine.cli optimize`'s inner-grid search, and the classification
CRUD commands (`classify`/`style`/`sector`/`expense-ratio` set/show).
Extend this suite as those areas change, rather than assuming they're
covered because something here is green.
