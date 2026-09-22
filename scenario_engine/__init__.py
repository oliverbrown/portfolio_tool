"""Companion tool to pt: generates a matrix of retirement scenario YAML
files from a base scenario plus a sweep config, and runs pt's own
projection engine against a directory of scenario files. See README.md
in this directory for the full workflow and file syntax.

Deliberately a separate top-level package from pt (see pt's own
README's "Everything runs locally" / project-layout section) -- pt is
considered stable; this reuses pt's modules rather than duplicating
them, but ships and is invoked independently (`python3 -m
scenario_engine.cli ...`, not `pt.cli`)."""
