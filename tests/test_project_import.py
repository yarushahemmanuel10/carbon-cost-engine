"""Part 3: cost rates and project import. Synthetic data only (no ICE)."""
import csv

import pytest

from carbon_cost.ingest import project as P

HEAD = P.ITEM_COLS + P.COMPONENT_COLS


def write_csv(path, header, rows):
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)
    return path


@pytest.fixture
def aliases(tmp_path):
    return write_csv(tmp_path / "aliases.csv", ["alias", "canonical_name", "note"], [
        ["CONC", "Test concrete", ""], ["REBAR", "Test rebar", ""], ["GLASS", "Test glass", ""],
    ])


def make_project(folder, items, name="Demo", gia="1000"):
    folder.mkdir(exist_ok=True)
    write_csv(folder / "items.csv", HEAD, items)
    write_csv(folder / "versions.csv", P.VERSION_COLS, [[name, "", "Option 1", "", gia, "items.csv"]])
    return folder


# a correct slab: 100 m2 x 0.25 m concrete, rebar 100 kg/m3 inside it, priced on T-SLAB (per m2)
SLAB = [
    ["S1", "2.2", "Slab", "100", "m2", "T-SLAB", "c", "CONC", "thickness_m", "0.25", "", ""],
    ["S1", "2.2", "Slab", "100", "m2", "T-SLAB", "r", "REBAR", "kg_per_m3", "100", "c", ""],
]


def test_correct_project_is_imported_with_its_rebar_host(seeded, tmp_path, aliases):
    folder = make_project(tmp_path / "p", SLAB)
    made = P.import_project(seeded, folder, aliases)
    vid = made["Option 1"]
    comps = seeded.execute(
        "SELECT c.id, m.canonical_name, c.basis, c.host_component_id FROM item_components c "
        "JOIN materials m ON m.id = c.material_id JOIN element_items i ON i.id = c.element_item_id "
        "WHERE i.project_version_id = ? ORDER BY c.id", (vid,)).fetchall()
    assert [r["canonical_name"] for r in comps] == ["Test concrete", "Test rebar"]
    assert comps[1]["host_component_id"] == comps[0]["id"]


def test_rebar_row_listed_before_its_host_still_links(seeded, tmp_path, aliases):
    folder = make_project(tmp_path / "p", [SLAB[1], SLAB[0]])
    P.import_project(seeded, folder, aliases)
    host = seeded.execute("SELECT host_component_id FROM item_components WHERE basis = 'kg_per_m3'").fetchone()[0]
    assert host == seeded.execute("SELECT id FROM item_components WHERE basis = 'thickness_m'").fetchone()[0]


@pytest.mark.parametrize("row, message", [
    (["S1", "9.9", "Slab", "100", "m2", "T-SLAB", "c", "CONC", "thickness_m", "0.25", "", ""], "NRM1 code '9.9'"),
    (["S1", "2.2", "Slab", "100", "m2", "NOPE", "c", "CONC", "thickness_m", "0.25", "", ""], "cost rate 'NOPE'"),
    (["S1", "2.2", "Slab", "100", "m2", "T-FOUND", "c", "CONC", "thickness_m", "0.25", "", ""], "is per m3"),
    (["S1", "2.2", "Slab", "100", "m2", "T-SLAB", "c", "UNKNOWN", "thickness_m", "0.25", "", ""], "alias 'UNKNOWN'"),
    (["S1", "2.2", "Slab", "100", "m2", "T-SLAB", "c", "GLASS", "thickness_m", "0.25", "", ""], "needs a factor in"),
    (["S1", "2.2", "Slab", "100", "m2", "T-SLAB", "c", "CONC", "area_ratio", "1", "", ""], "needs a factor in"),
    (["S1", "2.2", "Slab", "100", "m2", "T-SLAB", "c", "REBAR", "thickness_m", "0.25", "", ""], "no confirmed density"),
    (["S1", "2.2", "Slab", "100", "m2", "T-SLAB", "r", "REBAR", "kg_per_m3", "100", "", ""], "needs a host"),
    (["S1", "2.2", "Slab", "100", "m2", "T-SLAB", "r", "REBAR", "kg_per_m3", "100", "zz", ""], "is not a component"),
    (["S1", "2.2", "Slab", "100", "m2", "T-SLAB", "c", "CONC", "volume_ratio", "1", "", ""], "cannot be used on an item measured in m2"),
])
def test_bad_rows_are_refused_with_a_clear_message(seeded, tmp_path, aliases, row, message):
    folder = make_project(tmp_path / "p", [row])
    with pytest.raises(P.ImportProblems) as exc:
        P.import_project(seeded, folder, aliases)
    assert message in str(exc.value)
    assert seeded.execute("SELECT count(*) FROM projects").fetchone()[0] == 1   # only the fixture's own project


def test_host_on_an_m3_item_is_refused(seeded, tmp_path, aliases):
    rows = [["F1", "1.1", "Pads", "10", "m3", "T-FOUND", "c", "CONC", "volume_ratio", "1", "", ""],
            ["F1", "1.1", "Pads", "10", "m3", "T-FOUND", "r", "REBAR", "kg_per_m3", "80", "c", ""]]
    seeded.execute("INSERT INTO cost_rates (definition_id, rate_gbp, data_source_id, valid_from) VALUES (2, 300, 1, '2020-01-01')")
    with pytest.raises(P.ImportProblems, match="host must be empty"):
        P.import_project(seeded, make_project(tmp_path / "p", rows), aliases)


def test_every_problem_is_reported_at_once_with_line_numbers(seeded, tmp_path, aliases):
    rows = [["S1", "9.9", "Slab", "100", "m2", "T-SLAB", "c", "UNKNOWN", "thickness_m", "0.25", "", ""],
            ["S2", "2.2", "Slab", "-5", "m4", "T-SLAB", "c", "CONC", "sideways", "0", "", ""]]
    with pytest.raises(P.ImportProblems) as exc:
        P.import_project(seeded, make_project(tmp_path / "p", rows), aliases)
    text = "\n".join(exc.value.problems)
    for expected in ("line 3: quantity '-5'", "line 3: unit 'm4'", "line 3: basis 'sideways'", "line 3: basis_value '0'"):
        assert expected in text


def test_item_rows_that_disagree_are_refused(seeded, tmp_path, aliases):
    rows = [SLAB[0], ["S1", "2.2", "Slab", "120", "m2", "T-SLAB", "r", "REBAR", "kg_per_m3", "100", "c", ""]]
    with pytest.raises(P.ImportProblems, match=r"different \['quantity'\]"):
        P.import_project(seeded, make_project(tmp_path / "p", rows), aliases)


def test_importing_the_same_project_twice_is_refused(seeded, tmp_path, aliases):
    folder = make_project(tmp_path / "p", SLAB)
    P.import_project(seeded, folder, aliases)
    with pytest.raises(P.ImportProblems, match="already in the database"):
        P.import_project(seeded, folder, aliases)


def test_cost_rates_are_loaded_as_illustrative_and_only_once(conn, tmp_path):
    path = write_csv(tmp_path / "rates.csv", ["ref", "description", "unit", "rate_gbp", "comment"],
                     [["A", "Thing A", "m2", "10", ""], ["B", "Thing B", "m3", "20.5", "note"]])
    assert P.load_cost_rates(conn, path) == {"cost_rates": 2}
    rows = conn.execute("SELECT d.ref, r.rate_gbp, r.is_illustrative, r.valid_from FROM cost_rates r "
                        "JOIN cost_rate_definitions d ON d.id = r.definition_id ORDER BY d.ref").fetchall()
    assert [tuple(r) for r in rows] == [("A", 10.0, 1, P.COST_VALID_FROM), ("B", 20.5, 1, P.COST_VALID_FROM)]
    with pytest.raises(P.ImportProblems, match="already in this database"):
        P.load_cost_rates(conn, path)


def test_bad_cost_rates_load_nothing(conn, tmp_path):
    path = write_csv(tmp_path / "rates.csv", ["ref", "description", "unit", "rate_gbp"],
                     [["A", "x", "m2", "ten"], ["A", "y", "km", "-1"]])
    with pytest.raises(P.ImportProblems) as exc:
        P.load_cost_rates(conn, path)
    assert len(exc.value.problems) == 4
    assert conn.execute("SELECT count(*) FROM cost_rate_definitions").fetchone()[0] == 0


# ---- the committed example files: checked for consistency without any ICE data
def test_example_project_files_are_consistent(conn):
    versions = P.read_project(P.EXAMPLE_PROJECT)
    aliases = P.load_aliases()
    with open(P.COST_RATES_CSV, newline="") as fh:
        rates = {r["ref"]: r["unit"] for r in csv.DictReader(fh)}
    codes = {r[0] for r in conn.execute("SELECT code FROM elements")}
    assert [v["label"] for v in versions] == ["A - concrete frame", "A2 - concrete, 50% GGBS",
                                             "A3 - concrete, EAF rebar", "B - timber frame"]
    for v in versions:
        assert v["gia_m2"] == 3000
        for it in v["items"].values():
            assert it["head"]["nrm1_code"] in codes
            assert rates[it["head"]["cost_rate_ref"]] == it["head"]["unit"]
            for c in it["components"].values():
                assert c["material"] in aliases
