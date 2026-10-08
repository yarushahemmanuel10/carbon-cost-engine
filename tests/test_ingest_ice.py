"""The ICE parser, tested on a small MADE-UP workbook (tests/synthetic_ice.py).

No ICE data is used here, so these tests run for anyone who clones the
repository without the licensed file.
"""
import csv

import pytest

from carbon_cost import db
import synthetic_ice
from carbon_cost.ingest import ice


@pytest.fixture(scope="module")
def parsed(tmp_path_factory):
    path = tmp_path_factory.mktemp("ice") / "synthetic.xlsx"
    tags = synthetic_ice.build(path)
    rows = ice.read_summary_sheet(path)
    res = ice.parse_summary(rows)
    return {"path": path, "tags": tags, "rows": rows, "res": res,
            "by_name": {f.canonical_name: f for f in res.factors},
            "rejected": {r.row: r for r in res.rejected}}


def reason_for(parsed, tag):
    return parsed["rejected"][parsed["tags"][tag]].reason


# ------------------------------------------------------------ reading
def test_formatting_is_read_from_the_sheet(parsed):
    by_text = {r.f: r for r in parsed["rows"]}
    assert by_text["Concrete"].size == 20.0
    assert by_text["By Strength Class"].fill == "theme9:0.60"
    assert by_text["With CEM I"].fill == "theme9:0.80"
    assert by_text["Materials"].fill == "theme9:0.80"


def test_raw_extract_is_written(parsed, tmp_path):
    out = tmp_path / "raw.csv"
    ice.write_raw_csv(parsed["rows"], out)
    lines = list(csv.DictReader(open(out, encoding="utf-8")))
    assert len(lines) == len(parsed["rows"])
    assert {"row", "f_bold", "f_size", "f_fill", "F", "G", "T"} <= set(lines[0])


# ------------------------------------------------------------ headers and units
def test_header_layouts_are_detected_not_hard_coded(parsed):
    res = parsed["res"]
    assert sum(res.header_variants.values()) == 16      # every table in the test sheet was found
    assert len(res.header_variants) >= 8          # several different header layouts in the test sheet


def test_per_kg_row(parsed):
    f = parsed["by_name"]["Aggregates > gravel"]
    assert (f.value, f.unit, f.name_in_source, f.comments, f.dqi_score, f.dqi_version) == (
        0.5, "kgCO2e/kg", "Aggregate, gravel", "a comment", 0.6, "V1.0")


def test_non_numeric_dqi_becomes_empty(parsed):
    f = parsed["by_name"]["Aggregates > sand"]
    assert f.dqi_score is None and f.dqi_version is None


def test_per_tonne_is_converted_to_per_kg(parsed):
    f = parsed["by_name"]["Tarmac > Tarmac, mixed at plant > Tarmac, 5% binder"]
    assert f.unit == "kgCO2e/kg"
    assert f.value == pytest.approx(0.08)
    assert f.source_value == 80.0 and "per tonne" in f.source_unit_text


def test_per_unit_keeps_its_unit_and_says_what_a_unit_is(parsed):
    a = parsed["by_name"]["Tarmac > Road surfaces > Road, 5% binder"]
    b = parsed["by_name"]["Tarmac > Road surfaces > Road, 6% binder"]
    assert (a.value, a.unit, a.declared_unit) == (21.0, "kgCO2e/unit", "1 m2, per 100mm depth")
    # the Units cell is '#N/A' on the second row: the description comes from the header row instead
    assert (b.value, b.unit, b.declared_unit) == (23.0, "kgCO2e/unit", "1 m2, per 100mm depth")


def test_per_m2_is_never_turned_into_per_kg(parsed):
    f = parsed["by_name"]["Glass > Glazing by thickness - Per m2 > Double glazing - no frame > 8 mm of glass"]
    assert (f.value, f.unit) == (88.0, "kgCO2e/m2")


def test_declared_units_table(parsed):
    n = parsed["by_name"]
    base = "Concrete > Precast Concrete > Precast concrete products > "
    pipe, beam, floor, block = (n[base + x] for x in ("precast pipe", "precast beam", "precast floor", "concrete block 100mm"))
    assert (pipe.unit, pipe.declared_unit, pipe.mass_per_item_kg, pipe.value) == ("kgCO2e/unit", "m", 300.0, 12.0)
    assert (beam.unit, beam.declared_unit, beam.mass_per_item_kg, beam.value) == ("kgCO2e/kg", None, None, 0.55)
    assert (floor.unit, floor.mass_per_item_kg, floor.value) == ("kgCO2e/m2", 333.0, 77.0)
    assert (block.unit, block.declared_unit, block.mass_per_item_kg) == ("kgCO2e/unit", "each", 20.5)


def test_declared_unit_split_over_two_columns(parsed):
    # "1" under Declared Units and "m2" under Mass means "1 m2"
    f = parsed["by_name"]["Bricks > Brick walls - per m2 of wall > Single skin walls > with mortar 1:3"]
    assert (f.value, f.unit, f.mass_per_item_kg) == (60.0, "kgCO2e/m2", None)
    # "2 m2" is not understood, so it is rejected and not guessed
    assert reason_for(parsed, "brick wall 1:4") == ice.R_UNIT


def test_single_item_named_in_the_ice_db_name_is_stored_per_unit(parsed):
    f = parsed["by_name"]["Bricks > A Single Brick"]
    assert (f.value, f.unit, f.declared_unit) == (1.9, "kgCO2e/unit", "Clay brick, one brick, 215 x 65 x 102.5mm")
    assert f.mass_per_item_kg is None                          # not guessed from the comment
    assert f.comments.startswith("a brick weighs about 2 kg ")  # the source comment is kept ...
    assert ice.UNIT_FROM_NAME_NOTE in f.comments                # ... and the conflict is noted after it
    assert parsed["res"].unit_from_name == [(f.row, "A Single Brick", "Clay brick, one brick, 215 x 65 x 102.5mm")]


# ------------------------------------------------------------ headings
def test_full_name_is_built_from_the_headings(parsed):
    n = parsed["by_name"]
    assert "Concrete > In-Situ Concrete > By Strength Class > With CEM I > 32/40 MPa" in n
    assert "Concrete > In-Situ Concrete > By Strength Class > 32/40 MPa" in n
    f = n["Concrete > In-Situ Concrete > By Strength Class > With CEM I > 32/40 MPa"]
    assert (f.category, f.subcategory_path, f.label) == (
        "Concrete", "Concrete > In-Situ Concrete > By Strength Class > With CEM I", "32/40 MPa")
    assert f.name_in_source == "Concrete 32/40 with CEM I"
    assert "Cement and Mortar > Cement > CEM II > CEM II/A" in n


def test_title_row_above_a_header_lends_its_labels(parsed):
    # the header row itself has no Comments / DQI labels; the band row above it does
    f = parsed["by_name"]["Concrete > In-Situ Concrete > By Strength Class > General"]
    assert (f.comments, f.dqi_score, f.dqi_version) == ("avoid general values", 0.71, "V1.0")


def test_title_style_header_and_numeric_labels(parsed):
    base = "Concrete > In-Situ Concrete > By Volume and Cementitious Content > By cement content with CEM I > "
    assert parsed["by_name"][base + "100 kg CEM I per m3"].value == 0.15
    assert parsed["by_name"][base + "120"].value == 0.16


def test_notes_are_not_headings(parsed):
    assert not any("Important Note" in f.canonical_name or "boundaries" in f.canonical_name for f in parsed["res"].factors)
    kinds = {text[:14]: kind for _, kind, text in parsed["res"].structure}
    assert kinds["Important Note"] == "note"
    assert kinds["The data above"] == "note"


def test_version_heading_is_recorded_for_its_section_only(parsed):
    assert all(f.section_version is None for f in parsed["res"].factors)   # the only versioned section was withdrawn
    assert ("version", "Version: ICE V 2.0 2011") in {(k, t) for _, k, t in parsed["res"].structure}


# ------------------------------------------------------------ several values per row
def test_multi_value_columns_become_separate_materials(parsed):
    base = "Concrete > In-Situ Concrete > By Strength Class > % Cement Replacement - Slag > "
    a, b = parsed["by_name"][base + "25% > 32/40 MPa"], parsed["by_name"][base + "50% > 32/40 MPa"]
    assert (a.value, a.name_in_source, a.dqi_score, a.dqi_version) == (0.27, "Concrete 32/40 with 25% slag", 0.69, "V1.0")
    assert (b.value, b.name_in_source, b.dqi_score, b.dqi_version) == (0.21, "Concrete 32/40 with 50% slag", 0.68, "V1.0")
    assert a.comments == "estimated"


def test_percentage_row_is_not_loaded_as_a_material(parsed):
    assert not any(f.label.startswith("% Cement Replacement") for f in parsed["res"].factors)


def test_value_columns_nest_under_the_band_directly_above(parsed):
    name = ("Concrete > In-Situ Concrete > By Volume and Cementitious Content > By cement content with slag > "
            "% Cement Replacement - Slag > 50% > 120")
    assert parsed["by_name"][name].value == 0.12


# ------------------------------------------------------------ rejects
def test_rejects_carry_a_reason(parsed):
    r = {x.name: x.reason for x in parsed["res"].rejected}
    assert r["removed stuff"] == ice.R_TEXT
    assert r["no value given"] == ice.R_BLANK
    assert r["Felt underlay"] == ice.R_NO_VALUE_COL
    assert r["Nylon carpet"] == ice.R_NO_VALUE_COL
    assert r["Mortar (1:3)"] == ice.R_NO_HEADER
    assert r["Mortar (1:4)"] == ice.R_NO_HEADER
    assert r["Concrete - model row"] == ice.R_PRECAST
    assert r["A single slab"] == ice.R_UNIT_CONFLICT
    assert "A Single Brick" not in r


def test_precast_model_block_ends_at_the_next_section(parsed):
    assert "Concrete > Precast Concrete > Precast concrete products > precast pipe" in parsed["by_name"]
    assert sum(1 for x in parsed["res"].rejected if x.reason == ice.R_PRECAST) == 3


def test_every_row_is_accounted_for(parsed):
    res = parsed["res"]
    seen = {f.row for f in res.factors} | {r.row for r in res.rejected} | {row for row, _, _ in res.structure}
    seen |= {f.biogenic_row for f in res.factors if f.biogenic_row}
    unaccounted = {r.row for r in parsed["rows"]} - seen
    # the only rows left are timber 'including' rows whose storage figure was refused (warned about)
    texts = {r.f for r in parsed["rows"] if r.row in unaccounted}
    assert texts == {"Timber, Odd - Including Carbon Storage"}


# ------------------------------------------------------------ timber
def test_timber_is_one_material_with_storage_kept_apart(parsed):
    n = parsed["by_name"]
    clt = n["Timber > Timber, CLT"]
    assert (clt.value, clt.unit, clt.biogenic_kgco2e_per_kg, clt.name_in_source) == (0.60, "kgCO2e/kg", -2.00, "Timber, CLT")
    assert n["Timber > Timber, Glulam"].biogenic_kgco2e_per_kg == -1.90
    assert not any("Including" in name or "No Carbon Storage" in name for name in n)
    assert all(f.value >= 0 for f in parsed["res"].factors)
    assert parsed["res"].timber_pairs == 2


def test_timber_pair_that_does_not_add_up_gets_no_storage_and_a_warning(parsed):
    odd = parsed["by_name"]["Timber > Timber, Odd"]
    assert odd.value == 0.80 and odd.biogenic_kgco2e_per_kg is None
    assert any("Timber, Odd" in w and "NOT stored" in w for w in parsed["res"].warnings)


def test_unpaired_including_row_is_rejected(parsed):
    r = {x.name: x.reason for x in parsed["res"].rejected}
    assert r["Timber, Orphan - Including Carbon Storage"] == ice.R_TIMBER_UNPAIRED


# ------------------------------------------------------------ duplicates
def test_rule_a_wrong_label_next_to_the_right_one(parsed):
    assert reason_for(parsed, "mix wrong label") == ice.R_LABEL_MISMATCH
    base = "Concrete > In-Situ Concrete > By Volume and Cementitious Content > Nominal proportions - with average cement > "
    assert base + "1:3:6" in parsed["by_name"] and base + "1:2.5:5" not in parsed["by_name"]


def test_rule_b_same_datapoint_shown_twice(parsed):
    assert reason_for(parsed, "rebar repeat") == ice.R_DUPLICATE
    assert "Steel > World data - see the profile > Steel, Rebar" in parsed["by_name"]
    # the 0% column repeats the CEM I row with the same ICE DB name and value
    assert reason_for(parsed, "cc slag 120") == ice.R_DUPLICATE
    assert sum(1 for f in parsed["res"].factors if f.name_in_source == "Concrete - CEM I - 120 kg") == 1


def test_rule_c_same_label_different_values_are_both_kept(parsed):
    base = "Concrete > Precast Concrete > Precast concrete products > As above but with recycled rebar "
    a = parsed["by_name"][base + "[precast beam, recycled rebar]"]
    b = parsed["by_name"][base + "[precast floor, recycled rebar]"]
    assert (a.value, a.unit, b.value, b.unit) == (0.50, "kgCO2e/kg", 70.0, "kgCO2e/m2")


def test_no_two_materials_share_a_name(parsed):
    names = [f.canonical_name.lower() for f in parsed["res"].factors]
    assert len(names) == len(set(names))


# ------------------------------------------------------------ the declared-unit selector
def test_selector_must_read_kg(parsed, tmp_path):
    assert parsed["res"].declared_unit_selector == "kg"
    path = tmp_path / "m3.xlsx"
    synthetic_ice.build(path, select_unit="m3")
    with pytest.raises(ice.IngestError, match="reads 'm3', not 'kg'"):
        ice.parse_summary(ice.read_summary_sheet(path))


def test_missing_selector_fails_loudly(tmp_path):
    path = tmp_path / "none.xlsx"
    synthetic_ice.build(path, include_selector=False)
    with pytest.raises(ice.IngestError, match="Could not find the 'SELECT declared unit' cell"):
        ice.parse_summary(ice.read_summary_sheet(path))


def test_missing_file_and_missing_sheet(tmp_path):
    with pytest.raises(ice.IngestError, match="ICE file not found"):
        ice.read_summary_sheet(tmp_path / "nope.xlsx")
    from openpyxl import Workbook
    wb = Workbook()
    wb.save(tmp_path / "empty.xlsx")
    with pytest.raises(ice.IngestError, match="Sheet 'ICE Summary' not found"):
        ice.read_summary_sheet(tmp_path / "empty.xlsx")


# ------------------------------------------------------------ loading
def test_load_writes_materials_factors_and_biogenic(parsed, conn):
    counts = ice.load_to_db(conn, parsed["res"])
    n = len(parsed["res"].factors)
    assert counts == {"materials": n, "carbon_factors": n, "biogenic_carbon": 2}
    row = conn.execute(
        "SELECT m.category, m.subcategory_path, m.name_in_source, f.value, f.unit, f.lifecycle_module, f.valid_from, "
        "f.valid_to, s.redistributable, s.attribution_text, b.value_kgco2e_per_kg AS bio "
        "FROM materials m JOIN carbon_factors f ON f.material_id = m.id JOIN data_sources s ON s.id = f.data_source_id "
        "LEFT JOIN biogenic_carbon b ON b.material_id = m.id WHERE m.canonical_name = 'Timber > Timber, CLT'"
    ).fetchone()
    assert dict(row) == {
        "category": "Timber", "subcategory_path": "Timber", "name_in_source": "Timber, CLT", "value": 0.60,
        "unit": "kgCO2e/kg", "lifecycle_module": "A1-A3", "valid_from": "2026-06-01", "valid_to": None,
        "redistributable": 0, "attribution_text": "ICE Database Educational V5.0, Circular Ecology Ltd", "bio": -2.00,
    }
    assert conn.execute("SELECT count(*) FROM carbon_factors WHERE value < 0").fetchone()[0] == 0
    assert conn.execute("SELECT mass_per_item_kg FROM materials WHERE canonical_name LIKE '%concrete block 100mm'").fetchone()[0] == 20.5


def test_a_file_of_another_version_is_not_loaded(tmp_path, conn):
    path = tmp_path / "v6.xlsx"
    synthetic_ice.build(path, version_text="Educational (V6.0) - Jan 2030")
    res = ice.parse_summary(ice.read_summary_sheet(path))
    assert res.sheet_version == "Educational (V6.0) - Jan 2030"
    with pytest.raises(ice.IngestError, match="loads ICE Educational V5.0"):
        ice.load_to_db(conn, res)
    assert conn.execute("SELECT count(*) FROM materials").fetchone()[0] == 0


def test_loading_twice_is_refused(parsed, conn):
    ice.load_to_db(conn, parsed["res"])
    with pytest.raises(ice.IngestError, match="already in this database"):
        ice.load_to_db(conn, parsed["res"])


def test_a_failed_load_leaves_the_database_empty(parsed, conn):
    import copy
    broken = copy.deepcopy(parsed["res"])
    broken.factors[-1].unit = "kgCO2e/furlong"          # the database refuses this unit on the last row
    with pytest.raises(Exception):
        ice.load_to_db(conn, broken)
    assert conn.execute("SELECT count(*) FROM materials").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM data_sources").fetchone()[0] == 0


def test_command_line_end_to_end(parsed, tmp_path, capsys):
    code = ice.main(["--xlsx", str(parsed["path"]), "--db", str(tmp_path / "t.db"), "--staging", str(tmp_path / "staging")])
    out = capsys.readouterr().out
    assert code == 0
    for name in ("ice_summary_raw.csv", "ice_parsed.csv", "ice_rejected.csv", "ice_structure.csv", "density_candidates.csv"):
        assert (tmp_path / "staging" / name).exists(), name
    assert "Rows rejected:" in out and "Rows loaded into the database:" in out and "valid_from 2026-06-01" in out
    c = db.connect(tmp_path / "t.db")
    assert c.execute("SELECT count(*) FROM carbon_factors").fetchone()[0] == len(parsed["res"].factors)
    c.close()
    # running it again on the same database stops with a clear message and exit code 1
    assert ice.main(["--xlsx", str(parsed["path"]), "--db", str(tmp_path / "t.db"), "--staging", str(tmp_path / "staging")]) == 1
