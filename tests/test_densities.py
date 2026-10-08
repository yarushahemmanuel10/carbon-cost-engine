"""Density candidates and the confirmed-densities loader (synthetic data only)."""
import csv

import pytest

import synthetic_ice
from carbon_cost.ingest import densities


@pytest.fixture(scope="module")
def candidates(tmp_path_factory):
    path = tmp_path_factory.mktemp("dens") / "synthetic.xlsx"
    synthetic_ice.build(path)
    return densities.extract_density_candidates(path)


def test_table_is_found_by_its_title_not_by_row_number(candidates):
    assert {c["sheet"] for c in candidates} == {"Timber", "Plaster"}
    assert [c["density_kg_m3"] for c in candidates if c["sheet"] == "Timber"] == [500, 600, 150, 700, 550, 1200]


def test_blank_name_continues_the_row_above(candidates):
    t = [c for c in candidates if c["sheet"] == "Timber"]
    assert (t[1]["material"], t[1]["name_carried_from_row_above"], t[1]["condition"]) == ("softwood", "yes", "Dry")
    assert (t[5]["material"], t[5]["name_carried_from_row_above"]) == ("plywood", "yes")
    assert t[5]["condition"] == ""          # a stored 0 is an empty cell, not a condition


def test_names_do_not_carry_across_a_group_heading(candidates):
    t = [c for c in candidates if c["sheet"] == "Timber"]
    assert (t[3]["group"], t[3]["material"], t[3]["name_carried_from_row_above"]) == ("Wood derivatives", "Wood derivatives", "no")
    assert t[4]["group"] == "Wood derivatives" and t[4]["material"] == "plywood"


def test_values_outside_the_sanity_range_are_flagged_not_removed(candidates):
    checks = {c["density_kg_m3"]: c["range_check"] for c in candidates if c["sheet"] == "Timber"}
    assert checks == {500: "ok", 600: "ok", 150: "OUTSIDE RANGE", 700: "ok", 550: "ok", 1200: "OUTSIDE RANGE"}
    plaster = [c for c in candidates if c["sheet"] == "Plaster"]
    assert plaster[0]["range_check"] == "no range defined"


def test_candidates_file_and_counts(candidates, tmp_path):
    stats = densities.write_candidates(candidates, tmp_path / "d.csv")
    assert stats == {"total": 7, "sheets": 2, "flagged": 2, "ok": 4, "no_range": 1}
    assert len(list(csv.DictReader(open(tmp_path / "d.csv", encoding="utf-8")))) == 7


# ------------------------------------------------------------ loading densities.csv
def write_csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(densities.DENSITY_COLUMNS)
        w.writerows(rows)
    return path


def test_only_confirmed_rows_are_loaded(seeded, tmp_path):
    p = write_csv(tmp_path / "d.csv", [
        ["Test rebar", 7850, "made-up source", "yes", ""],
        ["Test glass", 2500, "made-up source", "no", "not checked yet"],
    ])
    result = densities.load_densities(seeded, p)
    assert result == {"loaded": ["Test rebar"], "skipped_unconfirmed": ["Test glass"]}
    got = dict(seeded.execute("SELECT canonical_name, density_kg_m3 FROM materials").fetchall())
    assert got == {"Test concrete": 2400.0, "Test rebar": 7850.0, "Test glass": None}
    assert seeded.execute("SELECT density_source FROM materials WHERE canonical_name = 'Test rebar'").fetchone()[0] == "made-up source"


@pytest.mark.parametrize("row, message", [
    (["No such material", 100, "src", "yes", ""], "is not in the database"),
    (["Test rebar", 7850, "", "yes", ""], "has no source"),
    (["Test rebar", "heavy", "src", "yes", ""], "is not a number"),
    (["Test rebar", -5, "src", "yes", ""], "must be greater than 0"),
])
def test_a_bad_row_stops_the_whole_load(seeded, tmp_path, row, message):
    p = write_csv(tmp_path / "d.csv", [["Test glass", 2500, "made-up source", "yes", ""], row])
    with pytest.raises(densities.DensityError, match=message):
        densities.load_densities(seeded, p)
    # nothing was changed, not even the good row above the bad one
    assert seeded.execute("SELECT density_kg_m3 FROM materials WHERE canonical_name = 'Test glass'").fetchone()[0] is None


def test_duplicate_material_and_missing_column_are_reported(seeded, tmp_path):
    p = write_csv(tmp_path / "d.csv", [["Test rebar", 7850, "s", "yes", ""], ["Test rebar", 7800, "s", "yes", ""]])
    with pytest.raises(densities.DensityError, match="appears twice"):
        densities.load_densities(seeded, p)
    bad = tmp_path / "bad.csv"
    bad.write_text("material,density_kg_m3\nTest rebar,7850\n", encoding="utf-8")
    with pytest.raises(densities.DensityError, match="missing column"):
        densities.load_densities(seeded, bad)
