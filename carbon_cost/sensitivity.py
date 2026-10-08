"""Sensitivity tests for the example office: how much do single choices move the answer?

    python -m carbon_cost.sensitivity --as-of 2026-10-01

Works on a TEMPORARY COPY of the database, so nothing in the real database
changes and no assessment is saved. Each scenario changes one thing (a
material, or a density) and recalculates with the engine.
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import tempfile
from pathlib import Path

from carbon_cost import db, engine as E
from carbon_cost.ingest import project as P

CONCRETE = "CONC-32/40-UKAVG"
GGBS = "CONC-32/40-GGBS50"
REBAR = "REBAR-WORLD"
EAF = "REBAR-EAF"


def _ids(conn, aliases):
    return {a: conn.execute("SELECT id FROM materials WHERE canonical_name = ?", (n,)).fetchone()[0]
            for a, n in aliases.items()
            if conn.execute("SELECT 1 FROM materials WHERE canonical_name = ?", (n,)).fetchone()}


def _version(conn, label):
    row = conn.execute("SELECT id, gia_m2 FROM project_versions WHERE label = ?", (label,)).fetchone()
    if row is None:
        raise LookupError(f"design option {label!r} not found; import the example project first")
    return row


def _swaps(conn, version_id, mapping, elements=None):
    """item_component_id -> new material id, for every component using a material in `mapping`."""
    q = ("SELECT c.id, c.material_id FROM item_components c JOIN element_items i ON i.id = c.element_item_id "
         "JOIN elements e ON e.id = i.element_id WHERE i.project_version_id = ?")
    out = {}
    for r in conn.execute(q + (" AND e.code IN (%s)" % ",".join("?" * len(elements)) if elements else ""),
                          (version_id, *(elements or []))):
        if r["material_id"] in mapping:
            out[r["id"]] = mapping[r["material_id"]]
    return out


def _total(conn, version_id, as_of, overrides=None):
    carbon, cost, _ = E.calculate(conn, version_id, as_of, overrides)
    return sum(l["carbon_kgco2e"] for l in carbon), sum(l["cost_gbp"] for l in cost)


def run(db_path, as_of: str) -> list[dict]:
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "copy.db"
        shutil.copyfile(db_path, copy)
        conn = db.connect(copy)
        try:
            m = _ids(conn, P.load_aliases())
            a, b = _version(conn, "A - concrete frame"), _version(conn, "B - timber frame")
            gia = a["gia_m2"]
            base_a, cost_a = _total(conn, a["id"], as_of)
            rows = []

            def add(name, version, overrides=None, note=""):
                c, k = _total(conn, version["id"], as_of, overrides)
                rows.append({"scenario": name, "carbon_t": c / 1000, "kg_m2": c / gia,
                             "vs_a_pct": (c - base_a) / base_a, "cost_gbp": k, "note": note})

            add("A - concrete frame (base)", a)
            add("A + 50% GGBS in all concrete", a, _swaps(conn, a["id"], {m[CONCRETE]: m[GGBS]}))
            add("A + EAF-route rebar", a, _swaps(conn, a["id"], {m[REBAR]: m[EAF]}))
            add("A + GGBS + EAF rebar", a, _swaps(conn, a["id"], {m[CONCRETE]: m[GGBS], m[REBAR]: m[EAF]}),
                "both material choices, no design change")
            add("B - timber frame (base)", b)
            add("B + GGBS and EAF rebar in substructure", b,
                _swaps(conn, b["id"], {m[CONCRETE]: m[GGBS], m[REBAR]: m[EAF]}, elements=["1.1"]),
                "the remaining hotspot in B is the substructure")
            pir = m["PIR"]
            for density in (30.0, 60.0):
                with db.transaction(conn):
                    conn.execute("UPDATE materials SET density_kg_m3 = ? WHERE id = ?", (density, pir))
                add(f"A with PIR at {density:g} kg/m3 (base 45)", a, note="PIR density is the midpoint of a 30-60 range")
                add(f"B with PIR at {density:g} kg/m3 (base 45)", b)
            return rows
        finally:
            conn.close()


def as_markdown(rows) -> str:
    out = ["| Scenario | tCO2e | kgCO2e/m2 | vs A | Cost (illustrative) |", "|---|---:|---:|---:|---:|"]
    for r in rows:
        out.append(f"| {r['scenario']} | {r['carbon_t']:,.0f} | {r['kg_m2']:,.0f} | {r['vs_a_pct']:+.0%} | £{r['cost_gbp']:,.0f} |")
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Sensitivity tests on the example office (nothing is saved).")
    ap.add_argument("--db", type=Path, default=db.DEFAULT_DB_PATH)
    ap.add_argument("--as-of", default="2026-10-01")
    args = ap.parse_args(argv)
    try:
        print(as_markdown(run(args.db, args.as_of)))
    except (LookupError, E.EngineError, sqlite3.Error) as exc:
        print(f"ERROR: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
