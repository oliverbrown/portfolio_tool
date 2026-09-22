"""Companion tool to pt: a true multi-year Roth conversion optimizer.

pt's own `project()` treats scenario.roth_conversions as a GREEDY per-year
heuristic -- each year, convert as much as that year's constraints allow,
without looking ahead to whether converting more (or less) that year would
produce a better outcome later. This package instead searches across the
WHOLE conversion schedule at once -- a separate dollar amount per owner per
year -- to maximize a defined "after-tax total estate value" objective
(see optimizer.py), using pt/projection.py's own roth_conversions.
annual_amount-as-a-list support (see build_roth_conversions()) to hand
project() a specific per-year schedule instead of a single flat number.

Deliberately a separate top-level package from pt, same reasoning as
scenario_engine (see that package's __init__.py) -- pt is considered
stable; this reuses pt's projection engine rather than duplicating it,
but ships and is invoked independently (`python3 -m
roth_optimizer.cli ...`, not `pt.cli`). Also deliberately dependency-free
(no numpy/scipy) -- see optimizer.py's search algorithm, a from-scratch
coordinate-ascent/pattern search, not a call into an external solver."""
