"""Backs up the local SQLite database (and the retirement planning profile
and any retirement scenario files alongside it) to a timestamped copy.

Uses sqlite3's own online backup API (Connection.backup()) rather than a
plain file copy, so it produces a consistent snapshot even if something else
has the database open at the same time -- a raw `cp` of a SQLite file can
capture a half-written page if a write is in progress.
"""
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from . import db as db_mod
from . import planning

DEFAULT_BACKUP_DIR = Path.home() / ".portfolio_tool" / "backups"

# Scenario filenames are deliberately flexible (see pt/scenario.py) -- there's
# no registry of them, so backup() finds them by pattern in the profile's
# directory rather than needing an explicit list of paths.
SCENARIO_GLOB_PATTERNS = ["retirement_scenario*.yaml", "retirement_scenario*.yml"]


def backup(
    db_path: Path = db_mod.DEFAULT_DB_PATH,
    profile_path: Path = planning.DEFAULT_PROFILE_PATH,
    backup_dir: Path = DEFAULT_BACKUP_DIR,
    include_profile: bool = True,
) -> dict:
    """Copies the database (and, if present and include_profile, the
    planning profile and any retirement_scenario*.yaml/.yml files next to
    it) into backup_dir with a shared timestamp suffix.
    Returns {"database": path, "profile": path_or_None, "scenarios": [paths]}."""
    db_path = Path(db_path)
    if not db_path.exists():
        raise FileNotFoundError(f"No database found at {db_path}.")

    backup_dir = Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")

    db_backup_path = backup_dir / f"{db_path.stem}-{stamp}{db_path.suffix}"
    src = sqlite3.connect(str(db_path))
    dst = sqlite3.connect(str(db_backup_path))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()

    profile_backup_path = None
    scenario_backup_paths = []
    profile_path = Path(profile_path)
    if include_profile:
        if profile_path.exists():
            profile_backup_path = backup_dir / f"{profile_path.stem}-{stamp}{profile_path.suffix}"
            shutil.copy2(profile_path, profile_backup_path)

        seen = set()
        for pattern in SCENARIO_GLOB_PATTERNS:
            for scenario_path in sorted(profile_path.parent.glob(pattern)):
                if scenario_path in seen:
                    continue
                seen.add(scenario_path)
                dest = backup_dir / f"{scenario_path.stem}-{stamp}{scenario_path.suffix}"
                shutil.copy2(scenario_path, dest)
                scenario_backup_paths.append(dest)

    return {
        "database": db_backup_path,
        "profile": profile_backup_path,
        "scenarios": scenario_backup_paths,
    }
