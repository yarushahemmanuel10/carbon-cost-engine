"""db.supersede_* : replacing a versioned row in ONE transaction (synthetic data only)."""
import sqlite3

import pytest

from carbon_cost import db

PICK = (
    "SELECT id, value FROM carbon_factors WHERE material_id = 1 AND lifecycle_module = 'A1-A3' "
    "AND valid_from <= :d AND :d < COALESCE(valid_to, '9999-12-31')"
)


def test_supersede_keeps_the_old_factor_and_dates_pick_the_right_one(seeded):
    new_id = db.supersede_carbon_factor(seeded, 1, value=0.08, valid_from="2024-01-01", data_source_id=1)
    old = seeded.execute("SELECT * FROM carbon_factors WHERE id = 1").fetchone()
    new = seeded.execute("SELECT * FROM carbon_factors WHERE id = ?", (new_id,)).fetchone()
    assert (old["value"], old["valid_to"], old["superseded_by"]) == (0.10, "2024-01-01", new_id)
    assert (new["value"], new["valid_from"], new["valid_to"]) == (0.08, "2024-01-01", None)
    assert (new["material_id"], new["unit"], new["lifecycle_module"]) == (1, "kgCO2e/kg", "A1-A3")
    # the day before the change -> old factor; the day of the change -> new factor
    assert tuple(seeded.execute(PICK, {"d": "2023-12-31"}).fetchone()) == (1, 0.10)
    assert tuple(seeded.execute(PICK, {"d": "2024-01-01"}).fetchone()) == (new_id, 0.08)
    # exactly one answer for any date
    assert len(seeded.execute(PICK, {"d": "2030-01-01"}).fetchall()) == 1


def test_supersede_is_saved_without_a_separate_commit(tmp_path):
    path = tmp_path / "t.db"
    c = db.init_db(path)
    c.execute("INSERT INTO data_sources VALUES (1, 'S', 'P', '1', 'none', 'made up', NULL, 1)")
    c.execute("INSERT INTO materials (id, canonical_name, category) VALUES (1, 'M', 'C')")
    c.execute("INSERT INTO carbon_factors (id, material_id, value, unit, lifecycle_module, data_source_id, valid_from) "
              "VALUES (1, 1, 0.10, 'kgCO2e/kg', 'A1-A3', 1, '2020-01-01')")
    c.commit()
    db.supersede_carbon_factor(c, 1, value=0.08, valid_from="2024-01-01", data_source_id=1)
    c.close()  # no commit() call here
    c2 = db.connect(path)
    assert c2.execute("SELECT count(*) FROM carbon_factors").fetchone()[0] == 2
    c2.close()


def test_failed_supersede_changes_nothing(seeded):
    # a negative A1-A3 value is refused by the database at step 2 of 3 ...
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        db.supersede_carbon_factor(seeded, 1, value=-5.0, valid_from="2024-01-01", data_source_id=1)
    # ... so step 1 (closing the old row) must have been undone as well
    old = seeded.execute("SELECT valid_to, superseded_by FROM carbon_factors WHERE id = 1").fetchone()
    assert (old["valid_to"], old["superseded_by"]) == (None, None)
    assert seeded.execute("SELECT count(*) FROM carbon_factors WHERE material_id = 1").fetchone()[0] == 1
    assert not seeded.in_transaction


def test_supersede_inside_a_bigger_transaction_rolls_back_with_it(seeded):
    with pytest.raises(RuntimeError):
        with db.transaction(seeded):
            db.supersede_carbon_factor(seeded, 1, value=0.08, valid_from="2024-01-01", data_source_id=1)
            raise RuntimeError("something else went wrong")
    assert seeded.execute("SELECT count(*) FROM carbon_factors WHERE material_id = 1").fetchone()[0] == 1
    assert seeded.execute("SELECT valid_to FROM carbon_factors WHERE id = 1").fetchone()[0] is None


def test_only_the_current_row_can_be_superseded(seeded):
    db.supersede_carbon_factor(seeded, 1, value=0.08, valid_from="2024-01-01", data_source_id=1)
    with pytest.raises(ValueError, match="already closed"):
        db.supersede_carbon_factor(seeded, 1, value=0.07, valid_from="2025-01-01", data_source_id=1)


def test_new_date_must_be_later_than_the_old_one(seeded):
    with pytest.raises(ValueError, match="must be later"):
        db.supersede_carbon_factor(seeded, 1, value=0.08, valid_from="2020-01-01", data_source_id=1)
    with pytest.raises(ValueError, match="must be later"):
        db.supersede_carbon_factor(seeded, 1, value=0.08, valid_from="2019-01-01", data_source_id=1)


def test_unknown_id_is_reported(seeded):
    with pytest.raises(LookupError, match="no row with id 999"):
        db.supersede_carbon_factor(seeded, 999, value=0.08, valid_from="2024-01-01", data_source_id=1)


def test_supersede_cost_rate(seeded):
    new_id = db.supersede_cost_rate(seeded, 1, rate_gbp=120.0, valid_from="2025-01-01", data_source_id=1)
    rows = seeded.execute("SELECT id, rate_gbp, valid_from, valid_to, superseded_by FROM cost_rates ORDER BY id").fetchall()
    assert [tuple(r) for r in rows] == [
        (1, 100.0, "2020-01-01", "2025-01-01", new_id),
        (new_id, 120.0, "2025-01-01", None, None),
    ]


def test_supersede_biogenic_carbon(seeded):
    seeded.execute(
        "INSERT INTO biogenic_carbon (id, material_id, value_kgco2e_per_kg, data_source_id, valid_from) "
        "VALUES (1, 1, -2.0, 1, '2020-01-01')"
    )
    new_id = db.supersede_biogenic_carbon(seeded, 1, value_kgco2e_per_kg=-1.5, valid_from="2024-01-01", data_source_id=1)
    rows = seeded.execute("SELECT id, value_kgco2e_per_kg, valid_to, superseded_by FROM biogenic_carbon ORDER BY id").fetchall()
    assert [tuple(r) for r in rows] == [(1, -2.0, "2024-01-01", new_id), (new_id, -1.5, None, None)]
