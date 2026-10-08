"""Part 5: the web interface, driven through Flask's test client. Synthetic data only."""
import pytest

from carbon_cost import db
from carbon_cost.app import create_app


@pytest.fixture
def client(tmp_path):
    """A file database (the app opens its own connections) with one slab item, like test_engine.build()."""
    path = tmp_path / "app.db"
    c = db.init_db(path)
    c.executescript("""
        INSERT INTO data_sources (id, name, publisher, version, licence, attribution_text, url, redistributable)
            VALUES (1, 'Synthetic test data', 'Test', '1.0', 'none', 'Made-up numbers for tests', 'https://example.org', 1);
        INSERT INTO materials (id, canonical_name, category, density_kg_m3, density_source)
            VALUES (1, 'Test > Concrete > 32/40', 'Concrete', 2400, 'made up');
        INSERT INTO materials (id, canonical_name, category) VALUES (2, 'Test rebar', 'Steel'), (5, 'Test green rebar', 'Steel'),
            (6, 'Test aluminium', 'Aluminium');
        INSERT INTO carbon_factors (material_id, value, unit, lifecycle_module, data_source_id, valid_from) VALUES
            (1, 0.10, 'kgCO2e/kg', 'A1-A3', 1, '2020-01-01'), (2, 2.00, 'kgCO2e/kg', 'A1-A3', 1, '2020-01-01'),
            (5, 0.50, 'kgCO2e/kg', 'A1-A3', 1, '2020-01-01'), (6, 9.00, 'kgCO2e/kg', 'A1-A3', 1, '2020-01-01');
        INSERT INTO cost_rate_definitions (id, ref, description, unit) VALUES (1, 'T-SLAB', 'Test slab', 'm2');
        INSERT INTO cost_rates (definition_id, rate_gbp, data_source_id, is_illustrative, valid_from) VALUES (1, 100, 1, 1, '2020-01-01');
        INSERT INTO projects (id, name) VALUES (1, 'Test project');
        INSERT INTO project_versions (id, project_id, label, gia_m2) VALUES (1, 1, 'Option A', 1000);
        INSERT INTO element_items (id, project_version_id, element_id, description, quantity, unit, cost_rate_definition_id)
            VALUES (1, 1, (SELECT id FROM elements WHERE code = '2.2'), 'Slab', 100, 'm2', 1);
        INSERT INTO item_components (id, element_item_id, material_id, basis, basis_value) VALUES (1, 1, 1, 'thickness_m', 0.25);
        INSERT INTO item_components (id, element_item_id, material_id, basis, basis_value, host_component_id)
            VALUES (2, 1, 2, 'kg_per_m3', 100, 1);
    """)
    c.commit()
    c.close()
    app = create_app(path)
    app.config["TESTING"] = True
    with app.test_client() as cl:
        cl.db_path = path
        yield cl


def page(resp):
    return resp.get_data(as_text=True)


def count(cl, sql):
    c = db.connect(cl.db_path)
    try:
        return c.execute(sql).fetchone()[0]
    finally:
        c.close()


def test_home_lists_the_option_before_any_run(client):
    html = page(client.get("/"))
    assert "Option A" in html and "not assessed yet" in html


def test_running_an_assessment_shows_cost_and_carbon_side_by_side(client):
    resp = client.post("/versions/1/assess", data={"as_of": "2023-06-01"}, follow_redirects=True)
    html = page(resp)
    assert resp.status_code == 200
    assert "11.0 tCO₂e" in html                 # 6,000 + 5,000 kg
    assert "£10,000" in html and "illustrative" in html
    assert "Made-up numbers for tests" in html   # source attribution is always shown
    assert "matches" in html                     # reproduced from stored rows
    assert count(client, "SELECT count(*) FROM assessments WHERE finalised_at IS NOT NULL") == 1


def test_a_date_before_the_factors_is_refused_and_nothing_is_saved(client):
    html = page(client.post("/versions/1/assess", data={"as_of": "2019-01-01"}, follow_redirects=True))
    assert "Not calculated" in html and "earliest factor starts on 2020-01-01" in html
    assert count(client, "SELECT count(*) FROM assessments") == 0


def test_what_if_swap_shows_the_delta_and_saves_nothing(client):
    client.post("/versions/1/assess", data={"as_of": "2023-06-01"})
    html = page(client.get("/assessments/1?swap_component=2&swap_material=5"))
    assert "What-if (not saved)" in html
    assert "−3.8" in html or "-3.8" in html       # rebar 5.0 t -> 1.25 t
    assert count(client, "SELECT material_id FROM item_components WHERE id = 2") == 2
    assert count(client, "SELECT count(*) FROM assessments") == 1


def test_swap_to_an_unrelated_material_is_refused(client):
    client.post("/versions/1/assess", data={"as_of": "2023-06-01"})
    html = page(client.get("/assessments/1?swap_component=2&swap_material=6", follow_redirects=True))
    assert "cannot replace" in html and "What-if (not saved)" not in html


def test_editing_a_quantity_affects_new_runs_only(client):
    client.post("/versions/1/assess", data={"as_of": "2023-06-01"})
    client.post("/items/1", data={"quantity": "200"})
    client.post("/versions/1/assess", data={"as_of": "2023-06-01"})
    assert "22.0 tCO₂e" in page(client.get("/assessments/2"))
    assert "11.0 tCO₂e" in page(client.get("/assessments/1"))   # the first result never changes


def test_bad_edits_are_refused(client):
    assert "greater than 0" in page(client.post("/items/1", data={"quantity": "-1"}, follow_redirects=True))
    html = page(client.post("/components/2", data={"basis_value": "100", "material_id": "6"}, follow_redirects=True))
    assert "cannot replace" in html
    assert count(client, "SELECT material_id FROM item_components WHERE id = 2") == 2


def test_compare_page(client):
    client.post("/versions/1/assess", data={"as_of": "2023-06-01"})
    client.post("/components/2", data={"basis_value": "50", "material_id": "2"})
    client.post("/versions/1/assess", data={"as_of": "2023-06-01"})
    html = page(client.get("/compare?a=1&b=2"))
    assert "-2.5 tCO₂e" in html and "-22.7%" in html


def test_upload_reports_every_problem_and_imports_nothing(client):
    import io
    versions = b"project_name,project_description,version_label,version_description,gia_m2,items_file\nP2,,X,,0,items.csv\n"
    items = (b"item,nrm1_code,description,quantity,unit,cost_rate_ref,component,material,basis,basis_value,host,comment\n"
             b"S1,9.9,Slab,100,m2,T-SLAB,c,CONC,thickness_m,0.25,,\n")
    resp = client.post("/upload", data={"files": [(io.BytesIO(versions), "versions.csv"), (io.BytesIO(items), "items.csv")]},
                       content_type="multipart/form-data", follow_redirects=True)
    html = page(resp)
    assert "gia_m2" in html and "Nothing was imported" in html
    assert count(client, "SELECT count(*) FROM projects") == 1


def test_upload_accepts_only_csv(client):
    import io
    resp = client.post("/upload", data={"files": [(io.BytesIO(b"x"), "evil.exe")]},
                       content_type="multipart/form-data", follow_redirects=True)
    assert "only .csv" in page(resp)


def test_archive_hides_but_keeps_the_option(client):
    client.post("/versions/1/archive")
    assert "archived" in page(client.get("/"))
    assert count(client, "SELECT archived FROM project_versions WHERE id = 1") == 1


def test_unknown_pages_are_404(client):
    assert client.get("/assessments/99").status_code == 404
    assert client.get("/versions/99").status_code == 404
