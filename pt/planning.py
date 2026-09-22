"""Retirement planning profile: the household's fixed identity facts (who's
in it, and when they were born) that both the retirement scenario
(pt/scenario.py) and later planning features join against by name.

Unlike account/holding data, this is a small, human-edited file -- so it
lives next to the SQLite database (same directory, same privacy treatment:
local-only, never committed to the project's git repo) rather than as rows
in the database. Edit the file directly in a text editor; there's no CLI
'set' command for it yet.

Shape (see README.md "Retirement planning profile" for the full example):
    people.primary/spouse: {name, birthdate} -- name is lowercase and is the
        join key used by pt/scenario.py's social_security section and by
        `import`'s owner names, so a later feature can go from "this
        account's owner" to "their birthdate" without re-deriving who's
        who. birthdate parses as a datetime.date via YAML's native date
        type (write YYYY-MM-DD). spouse is optional -- omit it for a
        single-person household.

Retirement ages, spending target, inflation, expected returns, and Social
Security claim ages -- the assumptions that vary by "what if" scenario --
live in a separate scenario file instead (pt/scenario.py), since a
household has one set of birthdates but may want to compare several
scenarios against them.
"""
from pathlib import Path

import yaml

DEFAULT_PROFILE_PATH = Path.home() / ".portfolio_tool" / "retirement_profile.yaml"


class ProfileError(Exception):
    pass


def load_profile(path: Path = DEFAULT_PROFILE_PATH) -> dict:
    """Loads and parses the retirement planning profile. Raises ProfileError
    with a clear message if the file doesn't exist yet or fails to parse."""
    path = Path(path)
    if not path.exists():
        raise ProfileError(
            f"No planning profile found at {path}. Create one there as YAML with "
            "a top-level 'people' section -- see README.md 'Retirement planning profile'."
        )
    with open(path, encoding="utf-8") as f:
        try:
            profile = yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise ProfileError(f"Couldn't parse {path} as YAML: {e}")
    if not isinstance(profile, dict):
        raise ProfileError(f"{path} doesn't look like a valid profile (expected a YAML mapping).")
    return profile


def resolve_profile(profile_path, scenario: dict) -> dict:
    """The profile to use for `project`/`monte-carlo`/`report`'s Projection
    tab. If profile_path was explicitly given (a user's --profile), always
    uses that file. Otherwise, if the scenario configures BOTH
    hypothetical_accounts and its own top-level `people` section (same
    shape as a real profile.yaml's people section), builds the profile
    directly from that instead of touching any file at all -- lets a
    fully self-contained hypothetical scenario skip needing a separate
    profile file (or one matching its made-up owners) entirely. Otherwise
    falls back to the default profile file, same as always (still
    required in that case). May raise ProfileError."""
    if profile_path:
        return load_profile(profile_path)
    if scenario.get("hypothetical_accounts") and scenario.get("people"):
        return {"people": scenario["people"]}
    return load_profile(DEFAULT_PROFILE_PATH)
