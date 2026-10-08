"""Part 4: the calculation engine. Synthetic data only (no ICE).

The fixture's numbers (see conftest.py): concrete 0.10 kgCO2e/kg at 2400 kg/m3,
rebar 2.00 kgCO2e/kg, glass 30 kgCO2e/m2, slab rate 100 GBP/m2, GIA 1000 m2.
"""
import pytest

from carbon_cost import db, engine as E


def build(conn, rebar=True, glass=False):
    """One slab item: 100 m2, concrete 0.25 m (+ rebar 100 kg/m3 hosted in it); optional glass item."""
    conn.execute("INSERT INTO element_items (id, project_version_id, element_id, description, quantity, unit, "
                 "cost_rate_definition_id) VALUES (1, 1, (SELECT id FROM elements WHERE code = '2.2'), 'Slab', 100, 'm2', 1)")
    conn.execute("INSERT INTO item_components (id, element_item_id, material_id, basis, basis_value) "
                 "VALUES (1, 1, 1, 'thickness_m', 0.25)")
    if rebar:
        conn.execute("INSERT INTO item_components (id, element_item_id, material_id, basis, basis_value, host_component_id) "
                     "VALUES (2, 1, 2, 'kg_per_m3', 100, 1)")
    if glass:
        conn.execute("INSERT INTO element_items (id, project_version_id, element_id, description, quantity, unit, "
                     "cost_rate_definition_id) VALUES (2, 1, (SELECT id FROM elements WHERE code = '2.6'), 'Glazing', 10, 'm2', 1)")
        conn.execute("INSERT INTO item_components (id, element_item_id, material_id, basis, basis_value) "
                     "VALUES (3, 2, 3, 'area_ratio', 1.0)")
    conn.commit()


AS_OF = "2023-06-01"


def test_slab_with_rebar_by_hand(seeded):
    build(seeded)
    aid = E.run_assessment(seeded, 1, AS_OF)
    r = E.results(seeded, aid)
    # concrete: 100 x 0.25 = 25 m3; x 2400 = 60,000 kg; x 0.10 = 6,000 kgCO2e
    # rebar:    25 m3 x 100 kg/m3 = 2,500 kg; x 2.00 = 5,000 kgCO2e
    assert r["total_carbon_kgco2e"] == pytest.approx(11000)
    assert r["total_cost_gbp"] == pytest.approx(10000)            # 100 m2 x 100 GBP
    assert r["carbon_kgco2e_per_m2"] == pytest.approx(11.0)       # GIA 1000
    rebar = [l for l in r["lines"] if l["material_id"] == 2][0]
    assert rebar["volume_m3"] == pytest.approx(25) and rebar["mass_kg"] == pytest.approx(2500)


def test_area_factor_is_applied_to_area_without_a_mass(seeded):
    build(seeded, rebar=False, glass=True)
    r = E.results(seeded, E.run_assessment(seeded, 1, AS_OF))
    glass = [l for l in r["lines"] if l["material_id"] == 3][0]
    assert glass["factor_quantity"] == pytest.approx(10) and glass["mass_kg"] is None
    assert glass["carbon_kgco2e"] == pytest.approx(300)           # 10 m2 x 30


def test_results_roll_up_the_nrm1_tree(seeded):
    build(seeded, glass=True)
    r = E.results(seeded, E.run_assessment(seeded, 1, AS_OF))
    by = {e["code"]: e for e in r["elements"]}
    assert by["2.2"]["carbon_kgco2e"] == pytest.approx(11000)
    assert by["2.6"]["carbon_kgco2e"] == pytest.approx(300)
    assert by["2"]["carbon_kgco2e"] == pytest.approx(11300)       # parent = sum of children
    assert by["2.2"]["carbon_share"] == pytest.approx(11000 / 11300)
    assert "1" not in by                                          # elements with nothing are not listed


def test_the_assessment_is_saved_finalised_and_reproducible(seeded):
    build(seeded)
    aid = E.run_assessment(seeded, 1, AS_OF)
    assert seeded.execute("SELECT finalised_at FROM assessments WHERE id = ?", (aid,)).fetchone()[0] is not None
    rep = E.reproduce(seeded, aid)
    assert rep["matches"] and rep["recalculated_carbon_kgco2e"] == pytest.approx(11000)


def test_superseded_factor_changes_new_runs_but_not_old_ones(seeded):
    build(seeded)
    old = E.run_assessment(seeded, 1, AS_OF)
    db.supersede_carbon_factor(seeded, 1, value=0.05, valid_from="2024-01-01", data_source_id=1)
    new = E.run_assessment(seeded, 1, "2024-06-01")
    assert E.results(seeded, old)["total_carbon_kgco2e"] == pytest.approx(11000)
    assert E.results(seeded, new)["total_carbon_kgco2e"] == pytest.approx(3000 + 5000)
    assert E.reproduce(seeded, old)["matches"]


def test_no_factor_on_the_as_of_date_stops_and_names_the_earliest_date(seeded):
    build(seeded)
    with pytest.raises(E.EngineError, match=r"Test concrete.*valid on 2019-01-01.*earliest factor starts on 2020-01-01"):
        E.run_assessment(seeded, 1, "2019-01-01")
    assert seeded.execute("SELECT count(*) FROM assessments").fetchone()[0] == 0     # nothing written


def test_missing_density_stops_the_run_and_writes_nothing(seeded):
    build(seeded, rebar=False)
    seeded.execute("UPDATE materials SET density_kg_m3 = NULL, density_source = NULL WHERE id = 1")
    with pytest.raises(E.EngineError, match="needs a density"):
        E.run_assessment(seeded, 1, AS_OF)
    assert seeded.execute("SELECT count(*) FROM assessments").fetchone()[0] == 0


def test_basis_and_factor_unit_must_pair(seeded):
    build(seeded, rebar=False)
    seeded.execute("UPDATE item_components SET material_id = 3 WHERE id = 1")      # thickness with a per-m2 factor
    with pytest.raises(E.EngineError, match="cannot use a factor in kgCO2e/m2"):
        E.calculate(seeded, 1, AS_OF)


def test_biogenic_is_reported_apart_and_needs_a_mass(seeded):
    build(seeded, rebar=False, glass=True)
    seeded.execute("INSERT INTO biogenic_carbon (material_id, value_kgco2e_per_kg, data_source_id, valid_from) "
                   "VALUES (1, -0.5, 1, '2020-01-01')")
    seeded.execute("INSERT INTO biogenic_carbon (material_id, value_kgco2e_per_kg, data_source_id, valid_from) "
                   "VALUES (3, -1.0, 1, '2020-01-01')")
    aid = E.run_assessment(seeded, 1, AS_OF)
    r = E.results(seeded, aid)
    assert r["total_carbon_kgco2e"] == pytest.approx(6000 + 300)                # storage NOT subtracted
    assert r["total_biogenic_kgco2e"] == pytest.approx(60000 * -0.5)           # only the line with a mass
    assert "has no mass" in r["notes"]                                         # the glass line is explained


def test_count_basis_per_unit_and_per_kg(seeded):
    seeded.execute("INSERT INTO cost_rate_definitions (id, ref, description, unit) VALUES (3, 'T-DOOR', 'Door', 'nr')")
    seeded.execute("INSERT INTO cost_rates (definition_id, rate_gbp, data_source_id, valid_from) VALUES (3, 500, 1, '2020-01-01')")
    seeded.execute("INSERT INTO materials (id, canonical_name, category, mass_per_item_kg) VALUES (4, 'Test door', 'Timber', 30)")
    seeded.execute("INSERT INTO carbon_factors (material_id, value, unit, lifecycle_module, data_source_id, valid_from) "
                   "VALUES (4, 0.5, 'kgCO2e/kg', 'A1-A3', 1, '2020-01-01')")
    seeded.execute("INSERT INTO element_items (id, project_version_id, element_id, description, quantity, unit, "
                   "cost_rate_definition_id) VALUES (1, 1, (SELECT id FROM elements WHERE code = '2.8'), 'Doors', 4, 'nr', 3)")
    seeded.execute("INSERT INTO item_components (element_item_id, material_id, basis, basis_value) VALUES (1, 4, 'count', 2)")
    r = E.results(seeded, E.run_assessment(seeded, 1, AS_OF))
    assert r["total_carbon_kgco2e"] == pytest.approx(4 * 2 * 30 * 0.5)          # 8 items x 30 kg x 0.5
    assert r["total_cost_gbp"] == pytest.approx(2000)


def test_compare_gives_b_minus_a(seeded):
    build(seeded)
    a = E.run_assessment(seeded, 1, AS_OF)
    seeded.execute("UPDATE item_components SET basis_value = 50 WHERE id = 2")    # halve the rebar
    b = E.run_assessment(seeded, 1, AS_OF)
    c = E.compare(seeded, a, b)
    assert c["carbon_delta_kgco2e"] == pytest.approx(-2500)
    assert c["carbon_delta_pct"] == pytest.approx(-2500 / 11000)
    assert c["cost_delta_gbp"] == 0
    assert {r["code"]: r["carbon_delta"] for r in c["elements"]}["2.2"] == pytest.approx(-2500)


def test_swap_material_is_a_what_if_that_saves_nothing(seeded):
    build(seeded)
    seeded.execute("INSERT INTO materials (id, canonical_name, category) VALUES (5, 'Test green rebar', 'Steel')")
    seeded.execute("INSERT INTO carbon_factors (material_id, value, unit, lifecycle_module, data_source_id, valid_from) "
                   "VALUES (5, 0.5, 'kgCO2e/kg', 'A1-A3', 1, '2020-01-01')")
    s = E.swap_material(seeded, 2, 5, AS_OF)
    assert s["component_before_kgco2e"] == pytest.approx(5000)
    assert s["component_after_kgco2e"] == pytest.approx(1250)                  # 2500 kg x 0.5
    assert s["delta_kgco2e"] == pytest.approx(-3750)
    assert seeded.execute("SELECT count(*) FROM assessments").fetchone()[0] == 0
    assert seeded.execute("SELECT material_id FROM item_components WHERE id = 2").fetchone()[0] == 2


def test_a_version_with_no_items_is_refused(seeded):
    with pytest.raises(E.EngineError, match="has no items"):
        E.run_assessment(seeded, 1, AS_OF)
