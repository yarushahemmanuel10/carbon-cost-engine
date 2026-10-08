"""Web interface (Flask). Start it with:

    python -m carbon_cost.app                 # http://127.0.0.1:5000
    python -m carbon_cost.app --db path/to/carbon_cost.db --port 5050

Pages
  /                       projects, design options, latest results; upload a project; run; compare
  /versions/<id>          the items and materials of one option; edit quantities and materials
  /assessments/<id>       cost and carbon by element, side by side; what-if material swap
  /compare?a=&b=          two assessments, element by element
The app never writes carbon factors or rates. It reads them, imports projects,
edits project items and runs assessments (which are then permanent records).
"""
from __future__ import annotations

import secrets
import tempfile
from datetime import date
from pathlib import Path

from flask import Flask, abort, flash, g, redirect, render_template, request, url_for
from werkzeug.utils import secure_filename

from carbon_cost import db, engine as E
from carbon_cost.ingest import project as P


def create_app(db_path: str | Path = db.DEFAULT_DB_PATH) -> Flask:
    app = Flask(__name__)
    app.config.update(DB_PATH=str(db_path), SECRET_KEY=secrets.token_hex(16), MAX_CONTENT_LENGTH=5 * 1024 * 1024)

    # ------------------------------------------------------------ database per request
    def conn():
        if "conn" not in g:
            if not Path(app.config["DB_PATH"]).exists():
                abort(500, f"Database {app.config['DB_PATH']} not found. Run the ingestion steps in the README first.")
            g.conn = db.connect(app.config["DB_PATH"])
        return g.conn

    @app.teardown_appcontext
    def _close(_exc):
        c = g.pop("conn", None)
        if c is not None:
            c.close()

    # ------------------------------------------------------------ number formats
    @app.template_filter("t")
    def tonnes(kg):
        return "" if kg is None else f"{kg / 1000:,.1f}"

    @app.template_filter("num")
    def num(x, places=1):
        return "" if x is None else f"{x:,.{places}f}"

    @app.template_filter("gbp")
    def gbp(x):
        return "" if x is None else f"£{x:,.0f}"

    @app.template_filter("pct")
    def pct(x, signed=False):
        if x is None:
            return "n/a"
        return f"{x:+.1%}" if signed else f"{x:.0%}"

    @app.template_filter("signed_t")
    def signed_t(kg):
        return f"{kg / 1000:+,.1f}"

    @app.template_filter("signed_gbp")
    def signed_gbp(x):
        return f"{'+' if x >= 0 else '−'}£{abs(x):,.0f}"

    @app.template_filter("short")
    def short(name):
        """A readable name for a long material path. Parts are taken from the end
        until the label says enough (about 30 characters); very long headings are
        shortened. The full name is always shown on hover (title attribute)."""
        parts = [p.strip() for p in (name or "").split(">") if p.strip()]
        if not parts:
            return name
        picked = [parts[-1]]
        for p in reversed(parts[:-1]):
            if len(" › ".join(picked)) >= 30:
                break
            picked.insert(0, p if len(p) <= 32 else p[:30].rstrip() + "…")
        return " › ".join(picked)

    @app.context_processor
    def _today():
        return {"today": date.today().isoformat()}

    # ------------------------------------------------------------ helpers
    def latest_assessments(c):
        rows = c.execute(
            "SELECT a.id, a.project_version_id, a.as_of_date, a.run_at FROM assessments a WHERE a.finalised_at IS NOT NULL "
            "AND a.id = (SELECT max(id) FROM assessments b WHERE b.project_version_id = a.project_version_id "
            "AND b.finalised_at IS NOT NULL)").fetchall()
        out = {}
        for r in rows:
            res = E.results(c, r["id"])
            out[r["project_version_id"]] = res
        return out

    def swap_candidates(c, component_id):
        """Materials that can honestly replace this component: same category, a factor of the same
        kind of unit, and a density where the basis needs one."""
        comp = c.execute("SELECT c.*, m.category FROM item_components c JOIN materials m ON m.id = c.material_id "
                         "WHERE c.id = ?", (component_id,)).fetchone()
        if comp is None:
            return []
        units = E.PAIRING[comp["basis"]]
        needs_density = comp["basis"] in ("thickness_m", "volume_ratio")
        q = ("SELECT DISTINCT m.id, m.canonical_name FROM materials m JOIN carbon_factors f ON f.material_id = m.id "
             "WHERE m.category = ? AND f.valid_to IS NULL AND f.lifecycle_module = 'A1-A3' AND f.unit IN (%s) AND m.id <> ?"
             % ",".join("?" * len(units)))
        if needs_density:
            q += " AND m.density_kg_m3 IS NOT NULL"
        return c.execute(q + " ORDER BY m.canonical_name", (comp["category"], *units, comp["material_id"])).fetchall()

    # ------------------------------------------------------------ pages
    @app.get("/")
    def index():
        c = conn()
        projects = []
        for p in c.execute("SELECT * FROM projects ORDER BY id").fetchall():
            versions = c.execute(
                "SELECT v.*, (SELECT count(*) FROM element_items i WHERE i.project_version_id = v.id) AS n_items "
                "FROM project_versions v WHERE v.project_id = ? ORDER BY v.archived, v.id", (p["id"],)).fetchall()
            projects.append({"p": p, "versions": versions})
        assessments = c.execute(
            "SELECT a.id, a.as_of_date, a.run_at, v.label FROM assessments a JOIN project_versions v "
            "ON v.id = a.project_version_id WHERE a.finalised_at IS NOT NULL ORDER BY a.id DESC").fetchall()
        return render_template("index.html", projects=projects, latest=latest_assessments(c), assessments=assessments)

    @app.post("/upload")
    def upload():
        files = [f for f in request.files.getlist("files") if f and f.filename]
        if not files:
            flash("Choose versions.csv and its items files first.", "error")
            return redirect(url_for("index"))
        with tempfile.TemporaryDirectory() as tmp:
            for f in files:
                name = secure_filename(Path(f.filename).name)
                if not name.lower().endswith(".csv"):
                    flash(f"{f.filename}: only .csv files are accepted.", "error")
                    return redirect(url_for("index"))
                f.save(Path(tmp) / name)
            try:
                made = P.import_project(conn(), Path(tmp))
            except P.ImportProblems as exc:
                for p in exc.problems:
                    flash(p, "error")
                flash("Nothing was imported.", "error")
                return redirect(url_for("index"))
        flash(f"Imported {len(made)} design option(s): {', '.join(made)}.", "ok")
        return redirect(url_for("index"))

    @app.post("/versions/<int:vid>/assess")
    def assess(vid):
        as_of = request.form.get("as_of") or date.today().isoformat()
        try:
            date.fromisoformat(as_of)
            aid = E.run_assessment(conn(), vid, as_of)
        except ValueError:
            flash(f"{as_of!r} is not a date (use YYYY-MM-DD).", "error")
            return redirect(request.referrer or url_for("index"))
        except E.EngineError as exc:
            flash(f"Not calculated: {exc}", "error")
            return redirect(request.referrer or url_for("index"))
        flash("Assessment saved and finalised. It records the exact factors and rates used.", "ok")
        return redirect(url_for("assessment", aid=aid))

    @app.post("/versions/<int:vid>/archive")
    def archive(vid):
        c = conn()
        with db.transaction(c):
            c.execute("UPDATE project_versions SET archived = 1 - archived WHERE id = ?", (vid,))
        return redirect(url_for("index"))

    @app.get("/versions/<int:vid>")
    def version(vid):
        c = conn()
        v = c.execute("SELECT v.*, p.name AS project FROM project_versions v JOIN projects p ON p.id = v.project_id "
                      "WHERE v.id = ?", (vid,)).fetchone()
        if v is None:
            abort(404)
        items = []
        for it in c.execute("SELECT i.*, e.code, e.name AS element, d.ref, d.description AS rate_desc FROM element_items i "
                            "JOIN elements e ON e.id = i.element_id JOIN cost_rate_definitions d ON d.id = i.cost_rate_definition_id "
                            "WHERE i.project_version_id = ? ORDER BY e.code, i.id", (vid,)).fetchall():
            comps = c.execute("SELECT c.*, m.canonical_name, m.density_kg_m3 FROM item_components c JOIN materials m "
                              "ON m.id = c.material_id WHERE c.element_item_id = ? ORDER BY c.id", (it["id"],)).fetchall()
            items.append({"it": it, "comps": [{"c": x, "options": swap_candidates(c, x["id"])} for x in comps]})
        return render_template("version.html", v=v, items=items)

    @app.post("/items/<int:iid>")
    def edit_item(iid):
        c = conn()
        it = c.execute("SELECT * FROM element_items WHERE id = ?", (iid,)).fetchone()
        if it is None:
            abort(404)
        try:
            q = float(request.form["quantity"])
            if q <= 0:
                raise ValueError
        except (KeyError, ValueError):
            flash("Quantity must be a number greater than 0.", "error")
            return redirect(url_for("version", vid=it["project_version_id"]))
        with db.transaction(c):
            c.execute("UPDATE element_items SET quantity = ? WHERE id = ?", (q, iid))
        flash(f"{it['description']}: quantity changed from {it['quantity']:g} to {q:g} {it['unit']}. "
              "Saved assessments are not affected; run a new one to see the effect.", "ok")
        return redirect(url_for("version", vid=it["project_version_id"]))

    @app.post("/components/<int:cid>")
    def edit_component(cid):
        c = conn()
        comp = c.execute("SELECT c.*, i.project_version_id FROM item_components c JOIN element_items i "
                         "ON i.id = c.element_item_id WHERE c.id = ?", (cid,)).fetchone()
        if comp is None:
            abort(404)
        vid = comp["project_version_id"]
        try:
            value = float(request.form["basis_value"])
            material = int(request.form["material_id"])
            if value <= 0:
                raise ValueError
        except (KeyError, ValueError):
            flash("The value must be a number greater than 0.", "error")
            return redirect(url_for("version", vid=vid))
        allowed = {comp["material_id"]} | {r["id"] for r in swap_candidates(c, cid)}
        if material not in allowed:
            flash("That material cannot replace this one (different kind of factor, or no confirmed density).", "error")
            return redirect(url_for("version", vid=vid))
        try:
            with db.transaction(c):
                c.execute("UPDATE item_components SET basis_value = ?, material_id = ? WHERE id = ?", (value, material, cid))
                E.calculate(c, vid, date.today().isoformat())        # the edit must still calculate; else undone
        except E.EngineError as exc:
            flash(f"Not changed: {exc}", "error")
            return redirect(url_for("version", vid=vid))
        flash("Component updated. Saved assessments are not affected; run a new one to see the effect.", "ok")
        return redirect(url_for("version", vid=vid))

    @app.get("/assessments/<int:aid>")
    def assessment(aid):
        c = conn()
        try:
            r = E.results(c, aid)
        except E.EngineError:
            abort(404)
        swap = None
        comp_id = request.args.get("swap_component", type=int)
        mat_id = request.args.get("swap_material", type=int)
        if comp_id and mat_id:
            if mat_id not in {m["id"] for m in swap_candidates(c, comp_id)}:
                flash("That material cannot replace this one.", "error")
            else:
                try:
                    swap = E.swap_material(c, comp_id, mat_id, r["as_of_date"])
                    swap["component_id"] = comp_id
                    swap["from"] = c.execute("SELECT m.canonical_name FROM item_components x JOIN materials m "
                                             "ON m.id = x.material_id WHERE x.id = ?", (comp_id,)).fetchone()[0]
                    swap["to"] = c.execute("SELECT canonical_name FROM materials WHERE id = ?", (mat_id,)).fetchone()[0]
                except E.EngineError as exc:
                    flash(f"What-if not possible: {exc}", "error")
        options = {l["item_component_id"]: swap_candidates(c, l["item_component_id"])
                   for l in r["lines"] if l["item_component_id"] is not None}
        others = c.execute("SELECT a.id, v.label, a.as_of_date FROM assessments a JOIN project_versions v "
                           "ON v.id = a.project_version_id WHERE a.finalised_at IS NOT NULL AND a.id <> ? "
                           "ORDER BY a.id DESC", (aid,)).fetchall()
        return render_template("assessment.html", r=r, swap=swap, options=options, others=others,
                               reproduced=E.reproduce(c, aid))

    @app.get("/compare")
    def compare():
        a, b = request.args.get("a", type=int), request.args.get("b", type=int)
        if not a or not b:
            flash("Choose two assessments to compare.", "error")
            return redirect(url_for("index"))
        try:
            cmp = E.compare(conn(), a, b)
        except E.EngineError:
            abort(404)
        return render_template("compare.html", c=cmp)

    return app
