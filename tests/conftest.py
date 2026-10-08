"""Shared test fixtures.

All data here is SYNTHETIC (made up for testing). No ICE data is used,
so the tests pass for anyone who clones the repository.

Every test gets its own brand-new in-memory database, so test data never
builds up and no test can affect another.
"""
import pytest

from carbon_cost import db

ELEMENT_2_2 = "(SELECT id FROM elements WHERE code = '2.2')"


@pytest.fixture
def conn():
    """A fresh in-memory database with the full schema and NRM1 elements."""
    c = db.init_db(":memory:")
    yield c
    c.close()


@pytest.fixture
def seeded(conn):
    """Made-up reference data:

    materials     1 'Test concrete' (density 2400)   2 'Test rebar'   3 'Test glass' (no density)
    factors       1 -> concrete 0.10 kgCO2e/kg, A1-A3, from 2020-01-01
                  2 -> rebar    2.00 kgCO2e/kg, A1-A3, from 2020-01-01
                  3 -> glass   30.00 kgCO2e/m2, A1-A3, from 2020-01-01
    cost rates    1 -> 'T-SLAB'  100 GBP/m2 from 2020-01-01      (definition 2 'T-FOUND' per m3 has no rate)
    project       1, version 1 (GIA 1000 m2)
    """
    conn.execute(
        "INSERT INTO data_sources (id, name, publisher, version, licence, attribution_text, url, redistributable) "
        "VALUES (1, 'Synthetic test data', 'Test', '1.0', 'none', 'Made-up numbers for tests', NULL, 1)"
    )
    conn.execute(
        "INSERT INTO materials (id, canonical_name, category, density_kg_m3, density_source) "
        "VALUES (1, 'Test concrete', 'Concrete', 2400, 'made up')"
    )
    conn.execute("INSERT INTO materials (id, canonical_name, category) VALUES (2, 'Test rebar', 'Steel')")
    conn.execute("INSERT INTO materials (id, canonical_name, category) VALUES (3, 'Test glass', 'Glass')")
    conn.executemany(
        "INSERT INTO carbon_factors (id, material_id, value, unit, lifecycle_module, data_source_id, valid_from) "
        "VALUES (?, ?, ?, ?, 'A1-A3', 1, '2020-01-01')",
        [(1, 1, 0.10, "kgCO2e/kg"), (2, 2, 2.00, "kgCO2e/kg"), (3, 3, 30.0, "kgCO2e/m2")],
    )
    conn.execute("INSERT INTO cost_rate_definitions (id, ref, description, unit) VALUES (1, 'T-SLAB', 'Test slab', 'm2')")
    conn.execute("INSERT INTO cost_rate_definitions (id, ref, description, unit) VALUES (2, 'T-FOUND', 'Test foundation', 'm3')")
    conn.execute(
        "INSERT INTO cost_rates (id, definition_id, rate_gbp, data_source_id, valid_from) VALUES (1, 1, 100.0, 1, '2020-01-01')"
    )
    conn.execute("INSERT INTO projects (id, name) VALUES (1, 'Test project')")
    conn.execute("INSERT INTO project_versions (id, project_id, label, gia_m2) VALUES (1, 1, 'A', 1000)")
    conn.commit()
    return conn


# ---------------------------------------------------------------- helpers
def add_item(conn, item_id, unit="m2", definition_id=1, quantity=100):
    conn.execute(
        f"INSERT INTO element_items (id, project_version_id, element_id, description, quantity, unit, cost_rate_definition_id) "
        f"VALUES (?, 1, {ELEMENT_2_2}, 'Test item', ?, ?, ?)",
        (item_id, quantity, unit, definition_id),
    )


def new_assessment(conn, as_of="2023-06-01"):
    return conn.execute(
        "INSERT INTO assessments (project_version_id, as_of_date, gia_m2) VALUES (1, ?, 1000)", (as_of,)
    ).lastrowid


def finalise(conn, assessment_id):
    conn.execute(
        "UPDATE assessments SET finalised_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE id = ?", (assessment_id,)
    )


def add_line(conn, assessment_id, **overrides):
    """Insert one carbon line. The defaults are a CORRECT line:

    100 m2 x 0.25 m = 25 m3;  x 2400 kg/m3 = 60,000 kg;  x 0.10 = 6,000 kgCO2e
    Pass keyword arguments to change any column (to build a wrong line).
    """
    row = dict(
        assessment_id=assessment_id, element_code="2.2", element_item_id=None, item_component_id=None,
        item_description="Test item", item_quantity=100, item_unit="m2", material_id=1,
        basis="thickness_m", basis_value=0.25, volume_m3=25, density_kg_m3_used=2400,
        mass_per_item_kg_used=None, mass_kg=60000, factor_quantity=60000, factor_unit="kgCO2e/kg",
        lifecycle_module="A1-A3", carbon_factor_id=1, carbon_kgco2e=6000,
        biogenic_carbon_id=None, biogenic_kgco2e=None,
    )
    row.update(overrides)
    code = row.pop("element_code")
    cols = ", ".join(row)
    marks = ", ".join(f":{k}" for k in row)
    return conn.execute(
        f"INSERT INTO assessment_lines (element_id, {cols}) "
        f"VALUES ((SELECT id FROM elements WHERE code = '{code}'), {marks})",
        row,
    ).lastrowid


def add_cost_line(conn, assessment_id, **overrides):
    """Insert one cost line. Default is CORRECT: 100 m2 x 100 GBP/m2 = 10,000 GBP."""
    row = dict(
        assessment_id=assessment_id, element_item_id=None, item_description="Test item",
        item_quantity=100, item_unit="m2", cost_rate_id=1, cost_gbp=10000,
    )
    row.update(overrides)
    cols = ", ".join(row)
    marks = ", ".join(f":{k}" for k in row)
    return conn.execute(
        f"INSERT INTO assessment_cost_lines (element_id, {cols}) VALUES ({ELEMENT_2_2}, {marks})", row
    ).lastrowid
