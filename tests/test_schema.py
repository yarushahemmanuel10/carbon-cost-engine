"""Rules on the reference and project tables (synthetic data only)."""
import sqlite3

import pytest

from carbon_cost import db
from conftest import add_item


# ------------------------------------------------------------ basics
def test_foreign_keys_are_on(conn):
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_foreign_keys_are_enforced(seeded):
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        seeded.execute(
            "INSERT INTO carbon_factors (material_id, value, unit, lifecycle_module, data_source_id, valid_from) "
            "VALUES (999, 1.0, 'kgCO2e/kg', 'A1-A3', 1, '2020-01-01')"
        )


def test_text_in_a_number_column_is_rejected(seeded):
    with pytest.raises(sqlite3.IntegrityError):
        seeded.execute(
            "INSERT INTO carbon_factors (material_id, value, unit, lifecycle_module, data_source_id, valid_from) "
            "VALUES (2, 'Data has been removed', 'kgCO2e/kg', 'A4', 1, '2020-01-01')"
        )


def test_init_db_refuses_to_overwrite(tmp_path):
    path = tmp_path / "x.db"
    db.init_db(path).close()
    with pytest.raises(RuntimeError, match="already contains"):
        db.init_db(path)


def test_nrm1_tree_is_loaded(conn):
    row = conn.execute(
        "SELECT p.code FROM elements e JOIN elements p ON p.id = e.parent_id WHERE e.code = '2.2'"
    ).fetchone()
    assert row["code"] == "2"


# ------------------------------------------------------------ carbon factors
def test_unknown_unit_is_rejected(seeded):
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        seeded.execute(
            "INSERT INTO carbon_factors (material_id, value, unit, lifecycle_module, data_source_id, valid_from) "
            "VALUES (2, 1.0, 'kgCO2e/tonne', 'A4', 1, '2020-01-01')"
        )


@pytest.mark.parametrize(
    "module",
    ["A4", "A5", "B1", "B2", "B3", "B4", "B5", "B6", "B7", "C1", "C2", "C3", "C4", "D"],
)
def test_later_lifecycle_modules_are_accepted(seeded, module):
    # A1-A3 is already loaded by the fixture; every other module must be accepted too
    seeded.execute(
        "INSERT INTO carbon_factors (material_id, value, unit, lifecycle_module, data_source_id, valid_from) "
        "VALUES (2, 1.0, 'kgCO2e/kg', ?, 1, '2020-01-01')",
        (module,),
    )


def test_unknown_lifecycle_module_is_rejected(seeded):
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        seeded.execute(
            "INSERT INTO carbon_factors (material_id, value, unit, lifecycle_module, data_source_id, valid_from) "
            "VALUES (2, 1.0, 'kgCO2e/kg', 'A6', 1, '2020-01-01')"
        )


def test_negative_a1a3_factor_is_rejected(seeded):
    seeded.execute("INSERT INTO materials (id, canonical_name, category) VALUES (9, 'Test timber', 'Timber')")
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        seeded.execute(
            "INSERT INTO carbon_factors (material_id, value, unit, lifecycle_module, data_source_id, valid_from) "
            "VALUES (9, -1.4, 'kgCO2e/kg', 'A1-A3', 1, '2020-01-01')"
        )


def test_bad_date_is_rejected(seeded):
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        seeded.execute(
            "INSERT INTO carbon_factors (material_id, value, unit, lifecycle_module, data_source_id, valid_from) "
            "VALUES (2, 1.0, 'kgCO2e/kg', 'A4', 1, '01/06/2026')"
        )


def test_factor_cannot_be_deleted(seeded):
    with pytest.raises(sqlite3.IntegrityError, match="never deleted"):
        seeded.execute("DELETE FROM carbon_factors WHERE id = 1")
    assert seeded.execute("SELECT count(*) FROM carbon_factors WHERE id = 1").fetchone()[0] == 1


def test_factor_value_cannot_be_edited(seeded):
    with pytest.raises(sqlite3.IntegrityError, match="never edited"):
        seeded.execute("UPDATE carbon_factors SET value = 0.05 WHERE id = 1")
    assert seeded.execute("SELECT value FROM carbon_factors WHERE id = 1").fetchone()[0] == 0.10


def test_two_current_factors_for_one_material_are_rejected(seeded):
    with pytest.raises(sqlite3.IntegrityError, match="overlaps"):
        seeded.execute(
            "INSERT INTO carbon_factors (material_id, value, unit, lifecycle_module, data_source_id, valid_from) "
            "VALUES (1, 0.08, 'kgCO2e/kg', 'A1-A3', 1, '2024-01-01')"
        )


def test_closed_factor_cannot_be_reopened_or_moved(seeded):
    db.supersede_carbon_factor(seeded, 1, value=0.08, valid_from="2024-01-01", data_source_id=1)
    with pytest.raises(sqlite3.IntegrityError, match="already set"):
        seeded.execute("UPDATE carbon_factors SET valid_to = '2030-01-01' WHERE id = 1")
    with pytest.raises(sqlite3.IntegrityError, match="already set"):
        seeded.execute("UPDATE carbon_factors SET superseded_by = NULL WHERE id = 1")


def test_superseding_factor_must_be_same_material(seeded):
    # raw SQL on purpose: this checks the database rule itself, behind the Python function.
    # Factor 2 belongs to material 2, so it cannot replace factor 1 (material 1).
    with pytest.raises(sqlite3.IntegrityError, match="same material"):
        seeded.execute("UPDATE carbon_factors SET valid_to = '2021-01-01', superseded_by = 2 WHERE id = 1")


def test_backdated_overlapping_factor_is_rejected(seeded):
    db.supersede_carbon_factor(seeded, 1, value=0.08, valid_from="2024-01-01", data_source_id=1)
    with pytest.raises(sqlite3.IntegrityError, match="overlaps"):
        seeded.execute(
            "INSERT INTO carbon_factors (material_id, value, unit, lifecycle_module, data_source_id, valid_from, valid_to) "
            "VALUES (1, 0.5, 'kgCO2e/kg', 'A1-A3', 1, '2022-01-01', '2023-01-01')"
        )


# ------------------------------------------------------------ biogenic, materials, cost
def test_biogenic_storage_must_not_be_positive(seeded):
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        seeded.execute(
            "INSERT INTO biogenic_carbon (material_id, value_kgco2e_per_kg, data_source_id, valid_from) "
            "VALUES (2, 2.0, 1, '2020-01-01')"
        )
    seeded.execute(
        "INSERT INTO biogenic_carbon (material_id, value_kgco2e_per_kg, data_source_id, valid_from) "
        "VALUES (2, -2.0, 1, '2020-01-01')"
    )


def test_density_needs_a_source(seeded):
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        seeded.execute("INSERT INTO materials (canonical_name, category, density_kg_m3) VALUES ('X', 'Y', 2400)")


def test_cost_rate_cannot_be_deleted_or_edited(seeded):
    with pytest.raises(sqlite3.IntegrityError, match="never deleted"):
        seeded.execute("DELETE FROM cost_rates WHERE id = 1")
    with pytest.raises(sqlite3.IntegrityError, match="never edited"):
        seeded.execute("UPDATE cost_rates SET rate_gbp = 1 WHERE id = 1")


# ------------------------------------------------------------ project versions
def test_project_version_archived_flag(seeded):
    assert seeded.execute("SELECT archived FROM project_versions WHERE id = 1").fetchone()[0] == 0
    seeded.execute("UPDATE project_versions SET archived = 1 WHERE id = 1")
    assert seeded.execute("SELECT archived FROM project_versions WHERE id = 1").fetchone()[0] == 1
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        seeded.execute("UPDATE project_versions SET archived = 2 WHERE id = 1")


# ------------------------------------------------------------ items and components
def test_item_unit_must_match_cost_rate_unit(seeded):
    with pytest.raises(sqlite3.IntegrityError, match="different from the unit"):
        add_item(seeded, 1, unit="m3", definition_id=1)  # definition 1 is priced per m2


def test_thickness_needs_an_m2_item(seeded):
    add_item(seeded, 1, unit="m3", definition_id=2)
    with pytest.raises(sqlite3.IntegrityError, match="need an item measured in m2"):
        seeded.execute(
            "INSERT INTO item_components (element_item_id, material_id, basis, basis_value) VALUES (1, 1, 'thickness_m', 0.25)"
        )


def test_rebar_on_m2_item_needs_a_host(seeded):
    add_item(seeded, 1)
    seeded.execute(
        "INSERT INTO item_components (id, element_item_id, material_id, basis, basis_value) VALUES (1, 1, 1, 'thickness_m', 0.25)"
    )
    with pytest.raises(sqlite3.IntegrityError, match="needs host_component_id"):
        seeded.execute(
            "INSERT INTO item_components (element_item_id, material_id, basis, basis_value) VALUES (1, 2, 'kg_per_m3', 110)"
        )
    seeded.execute(
        "INSERT INTO item_components (element_item_id, material_id, basis, basis_value, host_component_id) "
        "VALUES (1, 2, 'kg_per_m3', 110, 1)"
    )
    assert seeded.execute("SELECT count(*) FROM item_components WHERE element_item_id = 1").fetchone()[0] == 2


def test_host_must_be_in_the_same_item(seeded):
    add_item(seeded, 1)
    add_item(seeded, 2)
    seeded.execute(
        "INSERT INTO item_components (id, element_item_id, material_id, basis, basis_value) VALUES (1, 1, 1, 'thickness_m', 0.25)"
    )
    with pytest.raises(sqlite3.IntegrityError, match="same item"):
        seeded.execute(
            "INSERT INTO item_components (element_item_id, material_id, basis, basis_value, host_component_id) "
            "VALUES (2, 2, 'kg_per_m3', 110, 1)"
        )


def test_rebar_on_m3_item_uses_item_volume(seeded):
    add_item(seeded, 1, unit="m3", definition_id=2)
    seeded.execute(
        "INSERT INTO item_components (element_item_id, material_id, basis, basis_value) VALUES (1, 1, 'volume_ratio', 1.0)"
    )
    seeded.execute(
        "INSERT INTO item_components (element_item_id, material_id, basis, basis_value) VALUES (1, 2, 'kg_per_m3', 90)"
    )


def test_deleting_an_item_removes_its_components(seeded):
    add_item(seeded, 1)
    seeded.execute(
        "INSERT INTO item_components (id, element_item_id, material_id, basis, basis_value) VALUES (1, 1, 1, 'thickness_m', 0.25)"
    )
    seeded.execute(
        "INSERT INTO item_components (id, element_item_id, material_id, basis, basis_value, host_component_id) "
        "VALUES (2, 1, 2, 'kg_per_m3', 110, 1)"
    )
    seeded.execute("DELETE FROM element_items WHERE id = 1")
    assert seeded.execute("SELECT count(*) FROM item_components").fetchone()[0] == 0
