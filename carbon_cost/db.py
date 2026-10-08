"""Database connection helper.

Every part of the project opens the database through connect(), so that
foreign keys are switched on every single time. (SQLite leaves them OFF
by default, and the setting is per connection, not saved in the file.)
"""
from __future__ import annotations

import itertools
import sqlite3
from contextlib import contextmanager
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SRC_DIR.parent
SCHEMA_PATH = SRC_DIR / "schema.sql"
SEED_NRM1_PATH = SRC_DIR / "seed_nrm1.sql"
DEFAULT_DB_PATH = PROJECT_ROOT / "db" / "carbon_cost.db"

# STRICT tables need SQLite 3.37 (released 2021).
MIN_SQLITE = (3, 37, 0)


def connect(db_path: str | Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Open the database with foreign keys ON. Use ':memory:' for tests."""
    if sqlite3.sqlite_version_info < MIN_SQLITE:
        raise RuntimeError(
            f"SQLite {sqlite3.sqlite_version} is too old: this project needs "
            f"{'.'.join(map(str, MIN_SQLITE))} or newer (STRICT tables)."
        )
    if str(db_path) != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # Check it really is on, and fail loudly if not.
    if conn.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        conn.close()
        raise RuntimeError("Could not switch on foreign keys for this connection.")
    return conn


def init_db(db_path: str | Path = DEFAULT_DB_PATH, *, seed_elements: bool = True) -> sqlite3.Connection:
    """Create all tables in an EMPTY database and load the NRM1 elements.

    Raises an error if the database already has tables, so an existing
    database is never silently overwritten.
    """
    conn = connect(db_path)
    existing = conn.execute(
        "SELECT count(*) FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchone()[0]
    if existing:
        conn.close()
        raise RuntimeError(
            f"{db_path} already contains {existing} tables. "
            "Delete the file first if you really want to rebuild it."
        )
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    if seed_elements:
        conn.executescript(SEED_NRM1_PATH.read_text(encoding="utf-8"))
    conn.commit()
    return conn


# ---------------------------------------------------------------------
# Transactions
# ---------------------------------------------------------------------
_savepoint_ids = itertools.count(1)


@contextmanager
def transaction(conn: sqlite3.Connection):
    """Run several statements as ONE unit: all of them happen, or none.

        with transaction(conn):
            conn.execute(...)
            conn.execute(...)

    If any statement fails, everything done inside the block is undone
    and the error is raised again. If the block is the outermost one, the
    work is saved (committed) when it ends. It is safe to nest blocks.
    """
    outermost = not conn.in_transaction
    name = f"sp_{next(_savepoint_ids)}"
    conn.execute(f"SAVEPOINT {name}")
    try:
        yield conn
    except BaseException:
        conn.execute(f"ROLLBACK TO {name}")
        conn.execute(f"RELEASE {name}")
        raise
    else:
        conn.execute(f"RELEASE {name}")
        if outermost and conn.in_transaction:
            conn.commit()


# ---------------------------------------------------------------------
# Superseding a versioned row
# ---------------------------------------------------------------------
# Replacing a factor takes three statements:
#   1. close the old row      (set valid_to)
#   2. insert the new row     (valid_from = the same date)
#   3. link old -> new        (set superseded_by)
# If only some of them ran, a material would be left with no current
# factor, or with a closed row that points nowhere. So they are ONLY
# ever run through the functions below, inside one transaction.

def _supersede(conn, table, parent_cols, old_id, valid_from, new_values):
    old = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (old_id,)).fetchone()
    if old is None:
        raise LookupError(f"{table}: there is no row with id {old_id}")
    if old["valid_to"] is not None:
        raise ValueError(
            f"{table} id {old_id} was already closed on {old['valid_to']}; "
            "only the current row can be superseded"
        )
    if not valid_from > old["valid_from"]:
        raise ValueError(
            f"{table} id {old_id}: the new valid_from ({valid_from}) must be later "
            f"than the old valid_from ({old['valid_from']})"
        )
    row = {col: old[col] for col in parent_cols}   # same material / module / unit
    row.update(new_values)
    row["valid_from"] = valid_from
    cols = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    with transaction(conn):
        conn.execute(f"UPDATE {table} SET valid_to = ? WHERE id = ?", (valid_from, old_id))
        new_id = conn.execute(
            f"INSERT INTO {table} ({cols}) VALUES ({marks})", tuple(row.values())
        ).lastrowid
        conn.execute(f"UPDATE {table} SET superseded_by = ? WHERE id = ?", (new_id, old_id))
    return new_id


def supersede_carbon_factor(conn, old_id, *, value, valid_from, data_source_id,
                            comments=None, dqi_score=None, dqi_version=None,
                            source_section_version=None) -> int:
    """Replace the current carbon factor `old_id` with a new value from `valid_from`.

    The material, lifecycle module and unit stay the same (a different
    unit means a different material). Returns the id of the new row.
    The old row is kept, closed on `valid_from`, and points at the new one.
    """
    return _supersede(
        conn, "carbon_factors",
        ("material_id", "unit", "declared_unit", "lifecycle_module"),
        old_id, valid_from,
        {"value": value, "data_source_id": data_source_id, "comments": comments,
         "dqi_score": dqi_score, "dqi_version": dqi_version,
         "source_section_version": source_section_version},
    )


def supersede_cost_rate(conn, old_id, *, rate_gbp, valid_from, data_source_id,
                        is_illustrative=True, price_base=None, comments=None) -> int:
    """Replace the current cost rate `old_id` with a new price from `valid_from`."""
    return _supersede(
        conn, "cost_rates", ("definition_id",), old_id, valid_from,
        {"rate_gbp": rate_gbp, "data_source_id": data_source_id,
         "is_illustrative": 1 if is_illustrative else 0,
         "price_base": price_base, "comments": comments},
    )


def supersede_biogenic_carbon(conn, old_id, *, value_kgco2e_per_kg, valid_from,
                              data_source_id, comments=None) -> int:
    """Replace the current biogenic carbon row `old_id` from `valid_from`."""
    return _supersede(
        conn, "biogenic_carbon", ("material_id",), old_id, valid_from,
        {"value_kgco2e_per_kg": value_kgco2e_per_kg,
         "data_source_id": data_source_id, "comments": comments},
    )


if __name__ == "__main__":
    import sys

    target = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DB_PATH
    c = init_db(target)
    tables = [r[0] for r in c.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")]
    triggers = c.execute("SELECT count(*) FROM sqlite_master WHERE type = 'trigger'").fetchone()[0]
    elements = c.execute("SELECT count(*) FROM elements").fetchone()[0]
    print(f"Created {target}")
    print(f"  {len(tables)} tables: {', '.join(tables)}")
    print(f"  {triggers} triggers")
    print(f"  {elements} NRM1 elements loaded")
    print(f"  foreign_keys = {c.execute('PRAGMA foreign_keys').fetchone()[0]}")
    c.close()
