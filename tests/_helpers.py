"""Shared plumbing for this regression suite -- not itself a test module
(no test_*.py name, unittest discovery skips it).

Every test that needs a database gets a fresh, throwaway SQLite file in a
temp directory (see TempDB), seeded from test_data/'s synthetic Jordan/
Casey household (see test_data/README.md) -- never the user's real
~/.portfolio_tool database. Nothing here writes anywhere outside a temp
directory.
"""
import shutil
import tempfile
from pathlib import Path

from pt import allocation as alloc
from pt import classifier
from pt import db as db_mod
from pt import importer
from pt import store

REPO_ROOT = Path(__file__).resolve().parent.parent
TEST_DATA = REPO_ROOT / "test_data"

TEST_SCENARIO = TEST_DATA / "retirement_scenario.yaml"
TEST_PROFILE = TEST_DATA / "retirement_profile.yaml"

FIDELITY_JORDAN = TEST_DATA / "Portfolio_Positions_Aug-15-2026-jordan.csv"
FIDELITY_CASEY = TEST_DATA / "Portfolio_Positions_Aug-15-2026-casey.csv"
SCHWAB_JORDAN = TEST_DATA / "Schwab_Positions_Aug-15-2026-jordan.csv"
ETRADE_CASEY = TEST_DATA / "ETRADE_Positions_Aug-15-2026-casey.csv"
ETRADE_CASEY_ACCOUNT = "X9999"  # arbitrary -- the file itself has no account number, see pt/importer_etrade.py


class TempDB:
    """Context manager: a fresh SQLite DB path in its own temp directory,
    deleted on exit (success or failure). Usage:

        with TempDB() as db_path:
            conn = db_mod.get_connection(db_path)
            ...
    """

    def __enter__(self):
        self._dir = tempfile.mkdtemp(prefix="pt_regression_")
        self.path = Path(self._dir) / "test.db"
        return self.path

    def __exit__(self, exc_type, exc, tb):
        shutil.rmtree(self._dir, ignore_errors=True)
        return False


def seeded_connection(db_path):
    """A connection to db_path with the starter symbol classifications
    loaded (data/classifications_seed.csv) -- every test_data/ fixture
    symbol, including the CASH pseudo-symbol both Schwab/E*TRADE cash
    rows normalize to, is in that seed list, so a snapshot built purely
    from test_data/ fixtures should always come back with nothing
    unclassified."""
    conn = db_mod.get_connection(db_path)
    classifier.seed_classifications(conn)
    return conn


def import_fidelity_household(conn):
    """Imports test_data/'s two synthetic Fidelity CSVs (Jordan + Casey) --
    the same pair test_data/README.md's own documented example uses.
    Returns snapshot_id."""
    pairs = [(FIDELITY_JORDAN, "Jordan"), (FIDELITY_CASEY, "Casey")]
    as_of_date, accounts, holdings, _skipped = importer.parse_multiple_csvs(pairs)
    return store.save_snapshot(conn, source_file="test-fidelity", as_of_date=as_of_date,
                                accounts=accounts, holdings=holdings)


def import_full_household(conn):
    """Imports all four test_data/ fixtures at once -- both Fidelity files
    plus the Schwab and E*TRADE ones -- mirroring test_data/README.md's
    combined "Try it" example (multi-broker, mixed owners, one account
    deliberately duplicated across two Fidelity files to exercise Joint
    detection). Returns (snapshot_id, accounts, holdings, skipped)."""
    pairs = [
        (FIDELITY_JORDAN, "Jordan"),
        (FIDELITY_CASEY, "Casey"),
        (SCHWAB_JORDAN, "Jordan"),
        (ETRADE_CASEY, "Casey"),
    ]
    as_of_date, accounts, holdings, skipped = importer.parse_multiple_csvs(
        pairs, account_numbers={ETRADE_CASEY.name: ETRADE_CASEY_ACCOUNT},
    )
    snapshot_id = store.save_snapshot(conn, source_file="test-full", as_of_date=as_of_date,
                                       accounts=accounts, holdings=holdings)
    return snapshot_id, accounts, holdings, skipped


def allocation_rows_and_total(conn, snapshot_id):
    return alloc.allocation_by_asset_class(conn, snapshot_id)
