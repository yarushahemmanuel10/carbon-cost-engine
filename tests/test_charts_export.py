"""Part 6: charts (SVG) and the Power BI export. Synthetic data only."""
import csv
import io
import re

import pytest

from carbon_cost import charts, engine as E, export as X
from test_engine import AS_OF, build


def test_ticks_are_round_numbers():
    assert charts._ticks(0, 237.6) == [0, 50, 100, 150, 200, 250]
    assert charts._ticks(472, 624) == [450, 500, 550, 600, 650]
    assert charts._ticks(0, 0.8)[-1] >= 0.8


def test_element_colours_follow_the_code_not_the_rank():
    a = charts.element_colours(["2.2", "1.1", "2.5"])
    b = charts.element_colours(["2.5", "2.2", "1.1"])
    assert a == b and a["1.1"] == charts.SERIES[0]


def test_bar_chart_sorts_largest_first_and_has_tooltips():
    svg = charts.bar_chart([("small", 1.0, "tip small"), ("big", 9.0, "tip big")], "Title")
    assert svg.index(">big<") < svg.index(">small<")
    assert "<title>tip big</title>" in svg and svg.startswith("<svg")


def test_chart_text_is_escaped():
    svg = charts.bar_chart([("<script>", 1.0, "<b>")], "A & B")
    assert "<script>" not in svg and "&lt;script&gt;" in svg and "A &amp; B" in svg


def test_scatter_labels_never_overlap():
    svg = charts.scatter([("A", 525, 237.6), ("A2", 525, 195.0), ("A3", 525, 197.9), ("B", 578, 126.6)],
                         "t", "x", "y")
    ys = sorted(float(m) for m in re.findall(r'<text x="[\d.]+" y="([\d.]+)" fill="[^"]+" font-size="12">A', svg))
    assert len(ys) == 3 and all(b - a >= 19.9 for a, b in zip(ys, ys[1:]))


def test_stacked_bars_show_each_option_total():
    svg = charts.stacked_bars([("Opt", {"1.1": 30.0, "2.2": 70.0}, 100.0)], {"1.1": "#000", "2.2": "#111"},
                              {"1.1": "Sub", "2.2": "Floors"}, "T")
    assert ">100.0<" in svg and "Opt: 2.2 Floors, 70.0 (70%)" in svg


def test_export_is_tidy_and_sums_without_double_counting(seeded):
    build(seeded, glass=True)
    aid = E.run_assessment(seeded, 1, AS_OF)
    rows = list(csv.DictReader(io.StringIO(X.to_csv(seeded, [aid]))))
    assert set(rows[0]) == set(X.COLUMNS)
    carbon = [r for r in rows if r["line_type"] == "carbon"]
    cost = [r for r in rows if r["line_type"] == "cost"]
    assert len(carbon) == 3 and len(cost) == 2                       # 3 materials, 2 items
    assert sum(float(r["carbon_kgco2e"]) for r in carbon) == pytest.approx(11300)
    assert sum(float(r["cost_gbp"]) for r in cost) == pytest.approx(10000 + 1000)
    assert all(r["cost_gbp"] == "" for r in carbon) and all(r["carbon_kgco2e"] == "" for r in cost)
    assert {r["nrm1_group"] for r in rows} == {"2 Superstructure"}
    assert all(r["source_redistributable"] == "1" for r in rows)    # the synthetic source may be shared


def test_export_refuses_a_draft_or_unknown_assessment(seeded):
    with pytest.raises(LookupError):
        X.rows_for(seeded, 99)


def test_charts_page_and_csv_download(client):
    assert "Run an assessment" in client.get("/projects/1/charts", follow_redirects=True).get_data(as_text=True)
    client.post("/versions/1/assess", data={"as_of": "2023-06-01"})
    html = client.get("/projects/1/charts").get_data(as_text=True)
    assert "<svg" in html and "Cost against carbon" in html and "Option A" in html
    assert "<svg" in client.get("/assessments/1").get_data(as_text=True)
    resp = client.get("/projects/1/export.csv")
    assert resp.mimetype == "text/csv" and "attachment" in resp.headers["Content-Disposition"]
    assert resp.get_data(as_text=True).startswith("project,design_option,assessment_id")
    assert client.get("/assessments/99/export.csv").status_code == 404


def test_sensitivity_table_format():
    from carbon_cost import sensitivity
    md = sensitivity.as_markdown([{"scenario": "Base", "carbon_t": 712.7, "kg_m2": 237.6, "vs_a_pct": 0.0,
                                   "cost_gbp": 1574080.0, "note": ""}])
    assert md.splitlines()[2] == "| Base | 713 | 238 | +0% | £1,574,080 |"


def test_sensitivity_needs_the_example_project(seeded, tmp_path):
    from carbon_cost import db as D, sensitivity
    path = tmp_path / "s.db"
    D.init_db(path).close()
    with pytest.raises(LookupError):
        sensitivity.run(path, "2026-10-01")
