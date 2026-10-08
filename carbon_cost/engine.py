"""The calculation engine: embodied carbon (A1-A3) and cost for a project version.

    run_assessment(conn, version_id, as_of)   calculate, save and finalise an assessment
    results(conn, assessment_id)              totals by element, per m2 GIA, sources used
    compare(conn, assessment_a, assessment_b) element-by-element difference of two assessments
    swap_material(conn, component_id, ...)    what-if: one material changed, nothing saved
    reproduce(conn, assessment_id)            re-derive a saved result from its stored lines

    python -m carbon_cost.engine                       # assess every version of every project
    python -m carbon_cost.engine --as-of 2026-10-01

Rules (agreed in docs/design_note.md, sections 4.3, 4.4 and 7):
  * the factor (and rate) used is the one valid on the as-of date; if there is
    none, the run stops and names the material and the earliest valid date.
    It never falls back to another factor;
  * a basis only goes with its own kind of factor (mass -> per kg, area -> per
    m2, count -> per unit or per kg with a mass per item). Nothing is converted
    to make a pair fit;
  * biogenic carbon is reported separately, and only where a mass is known.
Everything is calculated first. Only if every line can be calculated is
anything written, in one transaction, and the assessment is finalised.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import date
from pathlib import Path

from carbon_cost import db

MODULE = "A1-A3"
MASS_BASES = ("thickness_m", "volume_ratio", "kg_per_m3")
PAIRING = {
    "thickness_m": ("kgCO2e/kg",), "volume_ratio": ("kgCO2e/kg",), "kg_per_m3": ("kgCO2e/kg",),
    "area_ratio": ("kgCO2e/m2",), "count": ("kgCO2e/unit", "kgCO2e/kg"),
}


class EngineError(RuntimeError):
    """A line cannot be calculated honestly. The message names the item and the reason."""


# ---------------------------------------------------------------- lookups
def _valid(table_where: str):
    return (f"{table_where} AND valid_from <= :d AND :d < COALESCE(valid_to, '9999-12-31') "
            "ORDER BY valid_from DESC LIMIT 1")


def find_factor(conn, material_id: int, as_of: str, module: str = MODULE):
    row = conn.execute("SELECT * FROM carbon_factors WHERE material_id = :m AND lifecycle_module = :mod" +
                       _valid(""), {"m": material_id, "mod": module, "d": as_of}).fetchone()
    if row is None:
        name = conn.execute("SELECT canonical_name FROM materials WHERE id = ?", (material_id,)).fetchone()
        first = conn.execute("SELECT min(valid_from) FROM carbon_factors WHERE material_id = ? AND lifecycle_module = ?",
                             (material_id, module)).fetchone()[0]
        when = f"the earliest factor starts on {first}" if first else "it has no factor at all"
        raise EngineError(f"no {module} carbon factor for {name[0] if name else material_id!r} is valid on {as_of}; {when}")
    return row


def find_biogenic(conn, material_id: int, as_of: str):
    return conn.execute("SELECT * FROM biogenic_carbon WHERE material_id = :m" + _valid(""),
                        {"m": material_id, "d": as_of}).fetchone()


def find_rate(conn, definition_id: int, as_of: str):
    row = conn.execute("SELECT * FROM cost_rates WHERE definition_id = :x" + _valid(""),
                       {"x": definition_id, "d": as_of}).fetchone()
    if row is None:
        ref = conn.execute("SELECT ref FROM cost_rate_definitions WHERE id = ?", (definition_id,)).fetchone()
        first = conn.execute("SELECT min(valid_from) FROM cost_rates WHERE definition_id = ?", (definition_id,)).fetchone()[0]
        when = f"the earliest rate starts on {first}" if first else "it has no rate at all"
        raise EngineError(f"no cost rate {ref[0] if ref else definition_id!r} is valid on {as_of}; {when}")
    return row


# ---------------------------------------------------------------- calculation (no writing)
def _component_line(conn, item, comp, components_by_id, as_of, material_id=None):
    """Work out one carbon line for one component. Returns (line dict, warning or None)."""
    mat_id = material_id or comp["material_id"]
    mat = conn.execute("SELECT * FROM materials WHERE id = ?", (mat_id,)).fetchone()
    label = f"item {item['description']!r}, material {mat['canonical_name']!r}"
    factor = find_factor(conn, mat_id, as_of)
    basis, value, qty = comp["basis"], comp["basis_value"], item["quantity"]
    if factor["unit"] not in PAIRING[basis]:
        raise EngineError(f"{label}: basis {basis} cannot use a factor in {factor['unit']} "
                          f"(it needs {' or '.join(PAIRING[basis])}); nothing is converted")
    volume = mass = density = per_item = None
    if basis in ("thickness_m", "volume_ratio"):
        if mat["density_kg_m3"] is None:
            raise EngineError(f"{label}: basis {basis} needs a density and this material has no confirmed density")
        volume = qty * value
        density = mat["density_kg_m3"]
        mass = volume * density
        factor_qty = mass
    elif basis == "kg_per_m3":
        if comp["host_component_id"] is not None:
            host = components_by_id[comp["host_component_id"]]
            volume = qty * host["basis_value"]          # the host is a thickness_m component of the same item
        else:
            volume = qty                                # item measured in m3
        mass = volume * value
        factor_qty = mass
    elif basis == "area_ratio":
        factor_qty = qty * value                        # m2, matches a per-m2 factor
    else:                                               # count
        n = qty * value
        if factor["unit"] == "kgCO2e/kg":
            if mat["mass_per_item_kg"] is None:
                raise EngineError(f"{label}: counting a per-kg material needs a mass per item, which it lacks")
            per_item = mat["mass_per_item_kg"]
            mass = n * per_item
            factor_qty = mass
        else:
            factor_qty = n
    warning = None
    bio = find_biogenic(conn, mat_id, as_of)
    bio_kg = None
    if bio is not None:
        if mass is None:
            warning = (f"{label}: carbon storage is given per kg but this line has no mass "
                       f"(basis {basis}); biogenic carbon not reported for it")
            bio = None
        else:
            bio_kg = mass * bio["value_kgco2e_per_kg"]
    line = {
        "element_id": item["element_id"], "element_item_id": item["id"], "item_component_id": comp["id"],
        "item_description": item["description"], "item_quantity": qty, "item_unit": item["unit"],
        "material_id": mat_id, "basis": basis, "basis_value": value, "volume_m3": volume,
        "density_kg_m3_used": density, "mass_per_item_kg_used": per_item, "mass_kg": mass,
        "factor_quantity": factor_qty, "factor_unit": factor["unit"], "lifecycle_module": MODULE,
        "carbon_factor_id": factor["id"], "carbon_kgco2e": factor_qty * factor["value"],
        "biogenic_carbon_id": bio["id"] if bio is not None else None, "biogenic_kgco2e": bio_kg,
    }
    return line, warning


def calculate(conn, version_id: int, as_of: str, overrides: dict | None = None):
    """Calculate every carbon and cost line of a version. Writes nothing.

    `overrides` maps item_component_id -> material_id (used by swap_material).
    Returns (carbon_lines, cost_lines, warnings). Raises EngineError on the first
    line that cannot be calculated.
    """
    overrides = overrides or {}
    date.fromisoformat(as_of)
    items = conn.execute("SELECT * FROM element_items WHERE project_version_id = ? ORDER BY id", (version_id,)).fetchall()
    if not items:
        raise EngineError(f"project version {version_id} has no items")
    carbon, cost, warnings = [], [], []
    for item in items:
        comps = conn.execute("SELECT * FROM item_components WHERE element_item_id = ? ORDER BY id", (item["id"],)).fetchall()
        if not comps:
            raise EngineError(f"item {item['description']!r} has no components (materials)")
        by_id = {c["id"]: c for c in comps}
        for c in comps:
            line, warn = _component_line(conn, item, c, by_id, as_of, overrides.get(c["id"]))
            carbon.append(line)
            if warn:
                warnings.append(warn)
        rate = find_rate(conn, item["cost_rate_definition_id"], as_of)
        cost.append({"element_id": item["element_id"], "element_item_id": item["id"],
                     "item_description": item["description"], "item_quantity": item["quantity"],
                     "item_unit": item["unit"], "cost_rate_id": rate["id"],
                     "cost_gbp": item["quantity"] * rate["rate_gbp"]})
    return carbon, cost, warnings


# ---------------------------------------------------------------- saving
def run_assessment(conn, version_id: int, as_of: str, notes: str | None = None) -> int:
    """Calculate, save and finalise. Returns the assessment id. All or nothing."""
    version = conn.execute("SELECT * FROM project_versions WHERE id = ?", (version_id,)).fetchone()
    if version is None:
        raise EngineError(f"project version {version_id} does not exist")
    carbon, cost, warnings = calculate(conn, version_id, as_of)
    text = "\n".join(filter(None, [notes] + [f"WARNING: {w}" for w in warnings])) or None
    with db.transaction(conn):
        aid = conn.execute("INSERT INTO assessments (project_version_id, as_of_date, gia_m2, notes) VALUES (?, ?, ?, ?)",
                           (version_id, as_of, version["gia_m2"], text)).lastrowid
        for line in carbon:
            cols = ", ".join(line)
            conn.execute(f"INSERT INTO assessment_lines (assessment_id, {cols}) VALUES (:aid, "
                         + ", ".join(f":{k}" for k in line) + ")", {"aid": aid, **line})
        for line in cost:
            cols = ", ".join(line)
            conn.execute(f"INSERT INTO assessment_cost_lines (assessment_id, {cols}) VALUES (:aid, "
                         + ", ".join(f":{k}" for k in line) + ")", {"aid": aid, **line})
        conn.execute("UPDATE assessments SET finalised_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE id = ?", (aid,))
    return aid


# ---------------------------------------------------------------- reporting
def _element_tree(conn):
    rows = conn.execute("SELECT id, code, name, parent_id FROM elements").fetchall()
    return {r["id"]: dict(r) for r in rows}


def _summarise(conn, carbon_by_el, bio_by_el, cost_by_el, gia):
    """Roll element totals up the NRM1 tree. Returns a list of rows sorted by code."""
    tree = _element_tree(conn)
    totals = defaultdict(lambda: {"carbon_kgco2e": 0.0, "biogenic_kgco2e": 0.0, "cost_gbp": 0.0})
    for source, key in ((carbon_by_el, "carbon_kgco2e"), (bio_by_el, "biogenic_kgco2e"), (cost_by_el, "cost_gbp")):
        for el, val in source.items():
            node = el
            while node is not None:                     # add to the element and every parent above it
                totals[node][key] += val
                node = tree[node]["parent_id"]
    total_carbon = sum(carbon_by_el.values())
    rows = []
    for el, t in totals.items():
        n = tree[el]
        rows.append({"element_id": el, "code": n["code"], "name": n["name"],
                     "level": n["code"].count(".") + 1, **t,
                     "carbon_share": t["carbon_kgco2e"] / total_carbon if total_carbon else 0.0,
                     "carbon_kgco2e_per_m2": t["carbon_kgco2e"] / gia, "cost_gbp_per_m2": t["cost_gbp"] / gia})
    rows.sort(key=lambda r: [int(p) for p in r["code"].split(".")])
    return rows


def results(conn, assessment_id: int) -> dict:
    """Everything a report needs, read only from the saved assessment."""
    a = conn.execute("SELECT a.*, v.label, p.name AS project FROM assessments a JOIN project_versions v ON v.id = "
                     "a.project_version_id JOIN projects p ON p.id = v.project_id WHERE a.id = ?", (assessment_id,)).fetchone()
    if a is None:
        raise EngineError(f"assessment {assessment_id} does not exist")
    carbon, bio, cost = defaultdict(float), defaultdict(float), defaultdict(float)
    for r in conn.execute("SELECT element_id, carbon_kgco2e, biogenic_kgco2e FROM assessment_lines WHERE assessment_id = ?",
                          (assessment_id,)):
        carbon[r["element_id"]] += r["carbon_kgco2e"]
        bio[r["element_id"]] += r["biogenic_kgco2e"] or 0.0
    for r in conn.execute("SELECT element_id, cost_gbp FROM assessment_cost_lines WHERE assessment_id = ?", (assessment_id,)):
        cost[r["element_id"]] += r["cost_gbp"]
    gia = a["gia_m2"]
    total_c, total_b, total_cost = sum(carbon.values()), sum(bio.values()), sum(cost.values())
    sources = [dict(r) for r in conn.execute(
        "SELECT DISTINCT s.name, s.version, s.attribution_text, s.url, s.redistributable FROM data_sources s WHERE s.id IN ("
        " SELECT f.data_source_id FROM assessment_lines l JOIN carbon_factors f ON f.id = l.carbon_factor_id WHERE l.assessment_id = :a"
        " UNION SELECT r.data_source_id FROM assessment_cost_lines c JOIN cost_rates r ON r.id = c.cost_rate_id WHERE c.assessment_id = :a)"
        " ORDER BY s.name", {"a": assessment_id})]
    illustrative = conn.execute(
        "SELECT max(r.is_illustrative) FROM assessment_cost_lines c JOIN cost_rates r ON r.id = c.cost_rate_id "
        "WHERE c.assessment_id = ?", (assessment_id,)).fetchone()[0]
    lines = [dict(r) for r in conn.execute(
        "SELECT l.*, e.code, m.canonical_name AS material, f.value AS factor_value FROM assessment_lines l "
        "JOIN elements e ON e.id = l.element_id JOIN materials m ON m.id = l.material_id "
        "JOIN carbon_factors f ON f.id = l.carbon_factor_id WHERE l.assessment_id = ? ORDER BY l.id", (assessment_id,))]
    return {
        "assessment_id": assessment_id, "project": a["project"], "version": a["label"],
        "version_id": a["project_version_id"], "as_of_date": a["as_of_date"], "finalised_at": a["finalised_at"],
        "gia_m2": gia, "notes": a["notes"],
        "total_carbon_kgco2e": total_c, "total_biogenic_kgco2e": total_b, "total_cost_gbp": total_cost,
        "carbon_kgco2e_per_m2": total_c / gia, "cost_gbp_per_m2": total_cost / gia,
        "by_module": {MODULE: total_c},
        "elements": _summarise(conn, carbon, bio, cost, gia),
        "lines": lines, "sources": sources, "cost_is_illustrative": bool(illustrative),
    }


def compare(conn, assessment_a: int, assessment_b: int) -> dict:
    """B minus A, by element and in total (carbon and cost)."""
    ra, rb = results(conn, assessment_a), results(conn, assessment_b)
    ea = {r["code"]: r for r in ra["elements"]}
    eb = {r["code"]: r for r in rb["elements"]}
    rows = []
    for code in sorted(set(ea) | set(eb), key=lambda c: [int(p) for p in c.split(".")]):
        a, b = ea.get(code), eb.get(code)
        ca, cb = (a or {}).get("carbon_kgco2e", 0.0), (b or {}).get("carbon_kgco2e", 0.0)
        pa, pb = (a or {}).get("cost_gbp", 0.0), (b or {}).get("cost_gbp", 0.0)
        rows.append({"code": code, "name": (a or b)["name"], "level": (a or b)["level"],
                     "carbon_a": ca, "carbon_b": cb, "carbon_delta": cb - ca,
                     "cost_a": pa, "cost_b": pb, "cost_delta": pb - pa})
    def pct(new, old):
        return (new - old) / old if old else None
    return {
        "a": ra, "b": rb, "elements": rows,
        "carbon_delta_kgco2e": rb["total_carbon_kgco2e"] - ra["total_carbon_kgco2e"],
        "carbon_delta_pct": pct(rb["total_carbon_kgco2e"], ra["total_carbon_kgco2e"]),
        "cost_delta_gbp": rb["total_cost_gbp"] - ra["total_cost_gbp"],
        "cost_delta_pct": pct(rb["total_cost_gbp"], ra["total_cost_gbp"]),
    }


def swap_material(conn, item_component_id: int, new_material_id: int, as_of: str) -> dict:
    """What happens to the version's totals if one component uses another material. Saves nothing.

    Cost does not change: a cost rate prices the finished work, not the material,
    so a material swap needs a new rate to change the cost (shown as a note).
    """
    comp = conn.execute("SELECT c.*, i.project_version_id FROM item_components c JOIN element_items i "
                        "ON i.id = c.element_item_id WHERE c.id = ?", (item_component_id,)).fetchone()
    if comp is None:
        raise EngineError(f"component {item_component_id} does not exist")
    vid = comp["project_version_id"]
    before, _, _ = calculate(conn, vid, as_of)
    after, _, warnings = calculate(conn, vid, as_of, {item_component_id: new_material_id})
    old_line = next(l for l in before if l["item_component_id"] == item_component_id)
    new_line = next(l for l in after if l["item_component_id"] == item_component_id)
    total_before = sum(l["carbon_kgco2e"] for l in before)
    total_after = sum(l["carbon_kgco2e"] for l in after)
    return {
        "component_before_kgco2e": old_line["carbon_kgco2e"], "component_after_kgco2e": new_line["carbon_kgco2e"],
        "total_before_kgco2e": total_before, "total_after_kgco2e": total_after,
        "delta_kgco2e": total_after - total_before,
        "delta_pct": (total_after - total_before) / total_before if total_before else None,
        "cost_note": "Cost unchanged: the cost rate prices the finished work. Change the item's rate to price the swap.",
        "warnings": warnings,
    }


def reproduce(conn, assessment_id: int, rel_tol: float = 1e-9) -> dict:
    """Recalculate a saved assessment from its stored inputs and the exact factor and rate rows it cites."""
    stored_c = stored_cost = again_c = again_cost = 0.0
    for r in conn.execute("SELECT l.carbon_kgco2e, l.factor_quantity, f.value FROM assessment_lines l "
                          "JOIN carbon_factors f ON f.id = l.carbon_factor_id WHERE l.assessment_id = ?", (assessment_id,)):
        stored_c += r["carbon_kgco2e"]
        again_c += r["factor_quantity"] * r["value"]
    for r in conn.execute("SELECT c.cost_gbp, c.item_quantity, r.rate_gbp FROM assessment_cost_lines c "
                          "JOIN cost_rates r ON r.id = c.cost_rate_id WHERE c.assessment_id = ?", (assessment_id,)):
        stored_cost += r["cost_gbp"]
        again_cost += r["item_quantity"] * r["rate_gbp"]
    ok = (abs(stored_c - again_c) <= rel_tol * max(1.0, abs(stored_c))
          and abs(stored_cost - again_cost) <= rel_tol * max(1.0, abs(stored_cost)))
    return {"matches": ok, "stored_carbon_kgco2e": stored_c, "recalculated_carbon_kgco2e": again_c,
            "stored_cost_gbp": stored_cost, "recalculated_cost_gbp": again_cost}


# ---------------------------------------------------------------- command line
def format_results(r: dict) -> str:
    out = [f"{r['project']} | {r['version']} | as of {r['as_of_date']} | GIA {r['gia_m2']:,.0f} m2",
           f"{'NRM1':<5} {'Element':<32} {'tCO2e':>9} {'share':>6} {'kgCO2e/m2':>10} {'cost GBP':>12}",
           "-" * 80]
    for e in r["elements"]:
        indent = "  " * (e["level"] - 1)
        out.append(f"{e['code']:<5} {(indent + e['name'])[:32]:<32} {e['carbon_kgco2e'] / 1000:>9.1f} "
                   f"{e['carbon_share']:>6.0%} {e['carbon_kgco2e_per_m2']:>10.1f} {e['cost_gbp']:>12,.0f}")
    out.append("-" * 80)
    out.append(f"{'':<5} {'TOTAL (A1-A3)':<32} {r['total_carbon_kgco2e'] / 1000:>9.1f} {'':>6} "
               f"{r['carbon_kgco2e_per_m2']:>10.1f} {r['total_cost_gbp']:>12,.0f}")
    if r["total_biogenic_kgco2e"]:
        out.append(f"      Biogenic carbon stored (reported separately, NOT subtracted): "
                   f"{r['total_biogenic_kgco2e'] / 1000:,.1f} tCO2e")
    out.append(f"      Cost per m2 GIA: GBP {r['cost_gbp_per_m2']:,.0f}"
               + ("  (illustrative rates)" if r["cost_is_illustrative"] else ""))
    out.append("      Sources: " + "; ".join(s["attribution_text"].split(".")[0] for s in r["sources"]))
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Assess every project version and print the results.")
    ap.add_argument("--db", type=Path, default=db.DEFAULT_DB_PATH)
    ap.add_argument("--as-of", default=date.today().isoformat())
    args = ap.parse_args(argv)
    conn = db.connect(args.db)
    try:
        ids = {}
        for v in conn.execute("SELECT id, label FROM project_versions WHERE archived = 0 ORDER BY id").fetchall():
            ids[v["label"]] = run_assessment(conn, v["id"], args.as_of)
        for label, aid in ids.items():
            print()
            print(format_results(results(conn, aid)))
        base = next(iter(ids.values()), None)
        if base is not None and len(ids) > 1:
            print()
            print(f"Compared with {next(iter(ids))}:")
            for label, aid in list(ids.items())[1:]:
                c = compare(conn, base, aid)
                print(f"   {label:<28} carbon {c['carbon_delta_kgco2e'] / 1000:+8.1f} t ({c['carbon_delta_pct']:+.1%})"
                      f"   cost GBP {c['cost_delta_gbp']:+12,.0f} ({c['cost_delta_pct']:+.1%})")
    except EngineError as exc:
        print(f"ERROR: {exc}")
        return 1
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
