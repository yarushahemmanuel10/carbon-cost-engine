"""The link between an assessment and the data it used (synthetic data only).

Every line that goes into an assessment is checked by the database:
it must cite the right factor, the factor must have been valid on the
as-of date, and the arithmetic must agree. Once an assessment is
finalised nothing can be added, changed or removed.
"""
import sqlite3

import pytest

from carbon_cost import db
from conftest import add_cost_line, add_item, add_line, finalise, new_assessment


# =================================================================== carbon lines
def test_a_correct_line_is_accepted(seeded):
    a = new_assessment(seeded)
    add_line(seeded, a)
    assert seeded.execute("SELECT carbon_kgco2e FROM assessment_lines").fetchone()[0] == 6000


# ---- case 1 of 4 from the review: another material's factor
def test_factor_of_a_different_material_is_refused(seeded):
    a = new_assessment(seeded)
    # factor 2 is the rebar factor (2.00); the line says material 1 (concrete)
    with pytest.raises(sqlite3.IntegrityError, match="belongs to a different material"):
        add_line(seeded, a, carbon_factor_id=2, carbon_kgco2e=60000 * 2.00)


def test_factor_of_a_different_lifecycle_module_is_refused(seeded):
    seeded.execute(
        "INSERT INTO carbon_factors (id, material_id, value, unit, lifecycle_module, data_source_id, valid_from) "
        "VALUES (10, 1, 0.01, 'kgCO2e/kg', 'A4', 1, '2020-01-01')"
    )
    a = new_assessment(seeded)
    with pytest.raises(sqlite3.IntegrityError, match="different lifecycle module"):
        add_line(seeded, a, carbon_factor_id=10, carbon_kgco2e=600)  # line still says A1-A3
    add_line(seeded, a, carbon_factor_id=10, carbon_kgco2e=600, lifecycle_module="A4")


def test_factor_unit_must_equal_the_factors_unit(seeded):
    a = new_assessment(seeded)
    # factor 3 (glass) is per m2; the line claims it is per unit
    with pytest.raises(sqlite3.IntegrityError, match="factor_unit is different"):
        add_line(seeded, a, material_id=3, carbon_factor_id=3, basis="count", basis_value=1, item_unit="nr",
                 volume_m3=None, density_kg_m3_used=None, mass_kg=None,
                 factor_quantity=100, factor_unit="kgCO2e/unit", carbon_kgco2e=3000)


def test_unknown_factor_and_unknown_assessment_are_refused(seeded):
    a = new_assessment(seeded)
    with pytest.raises(sqlite3.IntegrityError, match="carbon factor does not exist"):
        add_line(seeded, a, carbon_factor_id=999)
    with pytest.raises(sqlite3.IntegrityError, match="assessment does not exist"):
        add_line(seeded, 999)


# ---- case 2 of 4: factor not valid on the as-of date
def test_factor_not_yet_valid_on_the_as_of_date_is_refused(seeded):
    a = new_assessment(seeded, as_of="2019-12-31")  # factor 1 starts on 2020-01-01
    with pytest.raises(sqlite3.IntegrityError, match="not valid on the as-of date"):
        add_line(seeded, a)


def test_factor_is_valid_from_its_first_day(seeded):
    a = new_assessment(seeded, as_of="2020-01-01")
    add_line(seeded, a)


def test_superseded_factor_is_refused_for_a_later_as_of_date(seeded):
    new_id = db.supersede_carbon_factor(seeded, 1, value=0.08, valid_from="2024-01-01", data_source_id=1)
    late = new_assessment(seeded, as_of="2024-01-01")  # the first day of the NEW factor
    with pytest.raises(sqlite3.IntegrityError, match="not valid on the as-of date"):
        add_line(seeded, late, carbon_factor_id=1, carbon_kgco2e=6000)
    add_line(seeded, late, carbon_factor_id=new_id, carbon_kgco2e=60000 * 0.08)
    # ... and the new factor cannot be used for a date before it started
    early = new_assessment(seeded, as_of="2023-12-31")
    with pytest.raises(sqlite3.IntegrityError, match="not valid on the as-of date"):
        add_line(seeded, early, carbon_factor_id=new_id, carbon_kgco2e=60000 * 0.08)
    add_line(seeded, early, carbon_factor_id=1, carbon_kgco2e=6000)


# ---- case 4 of 4: carbon that is not quantity x factor
def test_carbon_that_does_not_match_quantity_x_factor_is_refused(seeded):
    a = new_assessment(seeded)
    with pytest.raises(sqlite3.IntegrityError, match="not factor_quantity x the carbon factor value"):
        add_line(seeded, a, carbon_kgco2e=5999)
    with pytest.raises(sqlite3.IntegrityError, match="not factor_quantity x the carbon factor value"):
        add_line(seeded, a, carbon_kgco2e=6000 * (1 + 1e-6))  # one part in a million is too far out
    with pytest.raises(sqlite3.IntegrityError, match="not factor_quantity x the carbon factor value"):
        add_line(seeded, a, carbon_kgco2e=0)


def test_rounding_in_the_last_digits_is_tolerated(seeded):
    a = new_assessment(seeded)
    add_line(seeded, a, carbon_kgco2e=6000 * (1 + 1e-12))  # far inside the 1e-9 tolerance


def test_zero_quantity_gives_zero_carbon(seeded):
    a = new_assessment(seeded)
    add_line(seeded, a, mass_kg=0, factor_quantity=0, carbon_kgco2e=0)


# ---- which basis goes with which kind of factor
def test_area_ratio_only_goes_with_a_per_m2_factor(seeded):
    a = new_assessment(seeded)
    # correct: glass, 100 m2 x 30 kgCO2e/m2
    glass = dict(material_id=3, carbon_factor_id=3, basis="area_ratio", basis_value=1.0, volume_m3=None,
                 density_kg_m3_used=None, mass_kg=None, factor_quantity=100, factor_unit="kgCO2e/m2", carbon_kgco2e=3000)
    add_line(seeded, a, **glass)
    # wrong: area_ratio with the per-kg concrete factor
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        add_line(seeded, a, basis="area_ratio", basis_value=1.0)


@pytest.mark.parametrize("basis", ["thickness_m", "volume_ratio", "kg_per_m3"])
def test_mass_bases_only_go_with_a_per_kg_factor(seeded, basis):
    a = new_assessment(seeded)
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        add_line(seeded, a, material_id=3, carbon_factor_id=3, basis=basis, mass_kg=None,
                 factor_quantity=100, factor_unit="kgCO2e/m2", carbon_kgco2e=3000)


def test_per_kg_line_must_have_a_mass_equal_to_the_factor_quantity(seeded):
    a = new_assessment(seeded)
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        add_line(seeded, a, mass_kg=None)
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        add_line(seeded, a, mass_kg=50000)  # factor_quantity is 60000


# ---- biogenic carbon on a line
@pytest.fixture
def with_biogenic(seeded):
    # made-up storage figures: concrete is used as the "timber" here only to keep the fixture small
    seeded.execute("INSERT INTO biogenic_carbon (id, material_id, value_kgco2e_per_kg, data_source_id, valid_from) "
                   "VALUES (1, 1, -2.0, 1, '2020-01-01')")
    seeded.execute("INSERT INTO biogenic_carbon (id, material_id, value_kgco2e_per_kg, data_source_id, valid_from) "
                   "VALUES (2, 2, -1.0, 1, '2020-01-01')")
    return seeded


def test_correct_biogenic_line_is_accepted_and_kept_out_of_carbon(with_biogenic):
    a = new_assessment(with_biogenic)
    add_line(with_biogenic, a, biogenic_carbon_id=1, biogenic_kgco2e=60000 * -2.0)
    row = with_biogenic.execute("SELECT sum(carbon_kgco2e) AS c, sum(biogenic_kgco2e) AS b FROM assessment_lines").fetchone()
    assert (row["c"], row["b"]) == (6000, -120000)  # the A1-A3 figure is untouched by storage


def test_biogenic_row_of_a_different_material_is_refused(with_biogenic):
    a = new_assessment(with_biogenic)
    with pytest.raises(sqlite3.IntegrityError, match="biogenic carbon row belongs to a different material"):
        add_line(with_biogenic, a, biogenic_carbon_id=2, biogenic_kgco2e=60000 * -1.0)


def test_biogenic_row_not_valid_on_the_as_of_date_is_refused(with_biogenic):
    new_id = db.supersede_biogenic_carbon(with_biogenic, 1, value_kgco2e_per_kg=-1.5, valid_from="2024-01-01", data_source_id=1)
    a = new_assessment(with_biogenic, as_of="2023-06-01")
    with pytest.raises(sqlite3.IntegrityError, match="biogenic carbon row was not valid"):
        add_line(with_biogenic, a, biogenic_carbon_id=new_id, biogenic_kgco2e=60000 * -1.5)
    add_line(with_biogenic, a, biogenic_carbon_id=1, biogenic_kgco2e=60000 * -2.0)


def test_biogenic_that_does_not_match_mass_x_storage_is_refused(with_biogenic):
    a = new_assessment(with_biogenic)
    with pytest.raises(sqlite3.IntegrityError, match="not mass_kg x the biogenic carbon value"):
        add_line(with_biogenic, a, biogenic_carbon_id=1, biogenic_kgco2e=-1)


def test_biogenic_needs_a_mass_so_it_cannot_sit_on_an_area_line(with_biogenic):
    with_biogenic.execute("INSERT INTO biogenic_carbon (id, material_id, value_kgco2e_per_kg, data_source_id, valid_from) "
                          "VALUES (3, 3, -1.0, 1, '2020-01-01')")
    a = new_assessment(with_biogenic)
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        add_line(with_biogenic, a, material_id=3, carbon_factor_id=3, basis="area_ratio", basis_value=1.0, volume_m3=None,
                 density_kg_m3_used=None, mass_kg=None, factor_quantity=100, factor_unit="kgCO2e/m2", carbon_kgco2e=3000,
                 biogenic_carbon_id=3, biogenic_kgco2e=-100)


def test_biogenic_is_only_allowed_on_the_a1a3_line(with_biogenic):
    with_biogenic.execute(
        "INSERT INTO carbon_factors (id, material_id, value, unit, lifecycle_module, data_source_id, valid_from) "
        "VALUES (10, 1, 0.01, 'kgCO2e/kg', 'A4', 1, '2020-01-01')"
    )
    a = new_assessment(with_biogenic)
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        add_line(with_biogenic, a, carbon_factor_id=10, carbon_kgco2e=600, lifecycle_module="A4",
                 biogenic_carbon_id=1, biogenic_kgco2e=-120000)


# =================================================================== cost lines
def test_a_correct_cost_line_is_accepted(seeded):
    a = new_assessment(seeded)
    add_cost_line(seeded, a)
    assert seeded.execute("SELECT cost_gbp FROM assessment_cost_lines").fetchone()[0] == 10000


def test_cost_rate_not_valid_on_the_as_of_date_is_refused(seeded):
    new_id = db.supersede_cost_rate(seeded, 1, rate_gbp=120.0, valid_from="2025-01-01", data_source_id=1)
    before_any = new_assessment(seeded, as_of="2019-06-01")
    with pytest.raises(sqlite3.IntegrityError, match="cost rate was not valid on the as-of date"):
        add_cost_line(seeded, before_any)
    late = new_assessment(seeded, as_of="2025-01-01")
    with pytest.raises(sqlite3.IntegrityError, match="cost rate was not valid on the as-of date"):
        add_cost_line(seeded, late, cost_rate_id=1)
    add_cost_line(seeded, late, cost_rate_id=new_id, cost_gbp=12000)


def test_cost_that_does_not_match_quantity_x_rate_is_refused(seeded):
    a = new_assessment(seeded)
    with pytest.raises(sqlite3.IntegrityError, match="not item_quantity x the cost rate"):
        add_cost_line(seeded, a, cost_gbp=9999)
    add_cost_line(seeded, a, cost_gbp=10000 * (1 + 1e-12))


def test_cost_line_unit_must_match_the_rate(seeded):
    a = new_assessment(seeded)
    with pytest.raises(sqlite3.IntegrityError, match="different from the unit the cost rate is priced in"):
        add_cost_line(seeded, a, item_unit="m3")


# =================================================================== finalising
def test_new_assessment_starts_as_a_draft(seeded):
    with pytest.raises(sqlite3.IntegrityError, match="must start with finalised_at empty"):
        seeded.execute("INSERT INTO assessments (project_version_id, as_of_date, gia_m2, finalised_at) "
                       "VALUES (1, '2023-06-01', 1000, '2023-06-01T10:00:00Z')")
    a = new_assessment(seeded)
    assert seeded.execute("SELECT finalised_at FROM assessments WHERE id = ?", (a,)).fetchone()[0] is None


def test_an_empty_assessment_cannot_be_finalised(seeded):
    a = new_assessment(seeded)
    with pytest.raises(sqlite3.IntegrityError, match="no lines"):
        finalise(seeded, a)


# ---- case 3 of 4: appending to a finalised assessment
def test_no_lines_can_be_added_to_a_finalised_assessment(seeded):
    a = new_assessment(seeded)
    add_line(seeded, a)
    add_cost_line(seeded, a)
    finalise(seeded, a)
    with pytest.raises(sqlite3.IntegrityError, match="finalised, no more lines"):
        add_line(seeded, a)
    with pytest.raises(sqlite3.IntegrityError, match="finalised, no more lines"):
        add_cost_line(seeded, a)
    assert seeded.execute("SELECT count(*) FROM assessment_lines").fetchone()[0] == 1
    assert seeded.execute("SELECT count(*) FROM assessment_cost_lines").fetchone()[0] == 1


def test_finalised_at_can_be_set_only_once(seeded):
    a = new_assessment(seeded)
    add_line(seeded, a)
    finalise(seeded, a)
    first = seeded.execute("SELECT finalised_at FROM assessments WHERE id = ?", (a,)).fetchone()[0]
    assert first is not None
    with pytest.raises(sqlite3.IntegrityError, match="already set"):
        seeded.execute("UPDATE assessments SET finalised_at = '2030-01-01T00:00:00Z' WHERE id = ?", (a,))
    with pytest.raises(sqlite3.IntegrityError, match="already set"):
        seeded.execute("UPDATE assessments SET finalised_at = NULL WHERE id = ?", (a,))
    assert seeded.execute("SELECT finalised_at FROM assessments WHERE id = ?", (a,)).fetchone()[0] == first


def test_finalised_at_must_be_a_real_timestamp(seeded):
    a = new_assessment(seeded)
    add_line(seeded, a)
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        seeded.execute("UPDATE assessments SET finalised_at = 'yes' WHERE id = ?", (a,))


def test_a_finalised_assessment_cannot_be_deleted_or_changed(seeded):
    a = new_assessment(seeded)
    add_line(seeded, a)
    add_cost_line(seeded, a)
    finalise(seeded, a)
    with pytest.raises(sqlite3.IntegrityError, match="cannot be deleted"):
        seeded.execute("DELETE FROM assessments WHERE id = ?", (a,))
    with pytest.raises(sqlite3.IntegrityError, match="cannot be deleted"):
        seeded.execute("DELETE FROM assessment_lines WHERE assessment_id = ?", (a,))
    with pytest.raises(sqlite3.IntegrityError, match="cannot be deleted"):
        seeded.execute("DELETE FROM assessment_cost_lines WHERE assessment_id = ?", (a,))
    with pytest.raises(sqlite3.IntegrityError, match="cannot be changed"):
        seeded.execute("UPDATE assessments SET notes = 'edited later' WHERE id = ?", (a,))
    with pytest.raises(sqlite3.IntegrityError, match="cannot be edited"):
        seeded.execute("UPDATE assessment_lines SET carbon_kgco2e = 1 WHERE assessment_id = ?", (a,))
    with pytest.raises(sqlite3.IntegrityError, match="cannot be edited"):
        seeded.execute("UPDATE assessment_cost_lines SET cost_gbp = 1 WHERE assessment_id = ?", (a,))
    assert seeded.execute("SELECT count(*) FROM assessment_lines").fetchone()[0] == 1


def test_a_draft_assessment_can_be_deleted_with_its_lines(seeded):
    # e.g. a run that failed halfway
    a = new_assessment(seeded)
    add_line(seeded, a)
    add_cost_line(seeded, a)
    seeded.execute("DELETE FROM assessments WHERE id = ?", (a,))
    assert seeded.execute("SELECT count(*) FROM assessments").fetchone()[0] == 0
    assert seeded.execute("SELECT count(*) FROM assessment_lines").fetchone()[0] == 0
    assert seeded.execute("SELECT count(*) FROM assessment_cost_lines").fetchone()[0] == 0


def test_deleting_a_draft_leaves_finalised_assessments_alone(seeded):
    done = new_assessment(seeded)
    add_line(seeded, done)
    finalise(seeded, done)
    draft = new_assessment(seeded)
    add_line(seeded, draft)
    seeded.execute("DELETE FROM assessments WHERE id = ?", (draft,))
    assert [r[0] for r in seeded.execute("SELECT assessment_id FROM assessment_lines")] == [done]


def test_draft_notes_can_change_but_the_as_of_date_never_can(seeded):
    a = new_assessment(seeded)
    seeded.execute("UPDATE assessments SET notes = 'warning: something' WHERE id = ?", (a,))
    with pytest.raises(sqlite3.IntegrityError, match="cannot be changed after the assessment is created"):
        seeded.execute("UPDATE assessments SET as_of_date = '2019-01-01' WHERE id = ?", (a,))
    with pytest.raises(sqlite3.IntegrityError, match="cannot be changed after the assessment is created"):
        seeded.execute("UPDATE assessments SET gia_m2 = 5 WHERE id = ?", (a,))


def test_draft_lines_cannot_be_edited_either(seeded):
    a = new_assessment(seeded)
    add_line(seeded, a)
    with pytest.raises(sqlite3.IntegrityError, match="cannot be edited"):
        seeded.execute("UPDATE assessment_lines SET carbon_kgco2e = 1")


# =================================================================== the record survives later changes
def test_finalised_assessment_survives_deleting_the_project_item(seeded):
    add_item(seeded, 1)
    seeded.execute(
        "INSERT INTO item_components (id, element_item_id, material_id, basis, basis_value) VALUES (1, 1, 1, 'thickness_m', 0.25)"
    )
    a = new_assessment(seeded)
    add_line(seeded, a, element_item_id=1, item_component_id=1)
    add_cost_line(seeded, a, element_item_id=1)
    finalise(seeded, a)
    seeded.execute("DELETE FROM element_items WHERE id = 1")
    line = seeded.execute("SELECT * FROM assessment_lines").fetchone()
    assert line["element_item_id"] is None and line["item_component_id"] is None
    assert (line["item_description"], line["carbon_kgco2e"]) == ("Test item", 6000)
    cost = seeded.execute("SELECT * FROM assessment_cost_lines").fetchone()
    assert cost["element_item_id"] is None and cost["cost_gbp"] == 10000


def test_finalised_assessment_still_gives_the_same_answer_after_supersede(seeded):
    a = new_assessment(seeded, as_of="2023-06-01")
    add_line(seeded, a)
    finalise(seeded, a)
    new_id = db.supersede_carbon_factor(seeded, 1, value=0.08, valid_from="2024-01-01", data_source_id=1)
    # the old assessment recomputed from its stored factor id: unchanged
    old = seeded.execute(
        "SELECT l.factor_quantity * f.value AS recomputed, l.carbon_kgco2e FROM assessment_lines l "
        "JOIN carbon_factors f ON f.id = l.carbon_factor_id WHERE l.assessment_id = ?", (a,)
    ).fetchone()
    assert old["recomputed"] == pytest.approx(6000) and old["carbon_kgco2e"] == 6000
    # a new assessment dated after the change uses the new factor
    b = new_assessment(seeded, as_of="2024-06-01")
    add_line(seeded, b, carbon_factor_id=new_id, carbon_kgco2e=60000 * 0.08)
    finalise(seeded, b)
    totals = dict(seeded.execute("SELECT assessment_id, sum(carbon_kgco2e) FROM assessment_lines GROUP BY assessment_id").fetchall())
    assert totals == {a: 6000, b: pytest.approx(4800)}
