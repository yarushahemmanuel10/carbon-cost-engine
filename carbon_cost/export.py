"""Export assessments as a flat CSV for Power BI (or Excel).

    python -m carbon_cost.export                     # latest assessment of every option -> exports/
    python -m carbon_cost.export --assessment 3

One row per LINE, in long ("tidy") form, so totals can be summed in Power BI
without double counting:
  line_type = carbon : one row per material of an item (carbon_kgco2e filled, cost_gbp empty)
  line_type = cost   : one row per item              (cost_gbp filled, carbon_kgco2e empty)

The export contains values derived from the carbon data source. When that
source may not be redistributed (ICE Educational), the column
source_redistributable says 0: keep the file for your own use. exports/ is
git-ignored for that reason.
"""
from __future__ import annotations

import argparse
import csv
import io
from pathlib import Path

from carbon_cost import db

EXPORT_DIR = db.PROJECT_ROOT / "exports"
COLUMNS = [
    "project", "design_option", "assessment_id", "as_of_date", "gia_m2", "line_type",
    "nrm1_code", "nrm1_group", "element", "item_description", "item_quantity", "item_unit",
    "material", "basis", "basis_value", "mass_kg", "factor_quantity", "factor_value", "factor_unit",
    "lifecycle_module", "carbon_kgco2e", "biogenic_kgco2e", "cost_rate_ref", "rate_gbp", "cost_gbp",
    "rate_is_illustrative", "source", "source_version", "source_redistributable",
]


def rows_for(conn, assessment_id: int) -> list[dict]:
    head = conn.execute(
        "SELECT a.id, a.as_of_date, a.gia_m2, v.label, p.name FROM assessments a JOIN project_versions v "
        "ON v.id = a.project_version_id JOIN projects p ON p.id = v.project_id WHERE a.id = ? AND a.finalised_at IS NOT NULL",
        (assessment_id,)).fetchone()
    if head is None:
        raise LookupError(f"assessment {assessment_id} does not exist or is not finalised")
    base = {"project": head["name"], "design_option": head["label"], "assessment_id": head["id"],
            "as_of_date": head["as_of_date"], "gia_m2": head["gia_m2"]}
    group = ("(SELECT p.code || ' ' || p.name FROM elements p WHERE p.id = e.parent_id)")
    out = []
    for r in conn.execute(
            f"SELECT l.*, e.code, e.name AS element, {group} AS grp, m.canonical_name, f.value AS fv, "
            "s.name AS src, s.version AS sv, s.redistributable AS sr FROM assessment_lines l "
            "JOIN elements e ON e.id = l.element_id JOIN materials m ON m.id = l.material_id "
            "JOIN carbon_factors f ON f.id = l.carbon_factor_id JOIN data_sources s ON s.id = f.data_source_id "
            "WHERE l.assessment_id = ? ORDER BY e.code, l.id", (assessment_id,)):
        out.append({**base, "line_type": "carbon", "nrm1_code": r["code"], "nrm1_group": r["grp"], "element": r["element"],
                    "item_description": r["item_description"], "item_quantity": r["item_quantity"], "item_unit": r["item_unit"],
                    "material": r["canonical_name"], "basis": r["basis"], "basis_value": r["basis_value"],
                    "mass_kg": r["mass_kg"], "factor_quantity": r["factor_quantity"], "factor_value": r["fv"],
                    "factor_unit": r["factor_unit"], "lifecycle_module": r["lifecycle_module"],
                    "carbon_kgco2e": r["carbon_kgco2e"], "biogenic_kgco2e": r["biogenic_kgco2e"],
                    "source": r["src"], "source_version": r["sv"], "source_redistributable": r["sr"]})
    for r in conn.execute(
            f"SELECT c.*, e.code, e.name AS element, {group} AS grp, d.ref, rt.rate_gbp, rt.is_illustrative, "
            "s.name AS src, s.version AS sv, s.redistributable AS sr FROM assessment_cost_lines c "
            "JOIN elements e ON e.id = c.element_id JOIN cost_rates rt ON rt.id = c.cost_rate_id "
            "JOIN cost_rate_definitions d ON d.id = rt.definition_id JOIN data_sources s ON s.id = rt.data_source_id "
            "WHERE c.assessment_id = ? ORDER BY e.code, c.id", (assessment_id,)):
        out.append({**base, "line_type": "cost", "nrm1_code": r["code"], "nrm1_group": r["grp"], "element": r["element"],
                    "item_description": r["item_description"], "item_quantity": r["item_quantity"], "item_unit": r["item_unit"],
                    "cost_rate_ref": r["ref"], "rate_gbp": r["rate_gbp"], "cost_gbp": r["cost_gbp"],
                    "rate_is_illustrative": r["is_illustrative"],
                    "source": r["src"], "source_version": r["sv"], "source_redistributable": r["sr"]})
    return out


def latest_ids(conn) -> list[int]:
    return [r[0] for r in conn.execute(
        "SELECT max(a.id) FROM assessments a JOIN project_versions v ON v.id = a.project_version_id "
        "WHERE a.finalised_at IS NOT NULL AND v.archived = 0 GROUP BY a.project_version_id ORDER BY a.project_version_id")]


def to_csv(conn, assessment_ids) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=COLUMNS, extrasaction="raise")
    w.writeheader()
    for aid in assessment_ids:
        for row in rows_for(conn, aid):
            w.writerow({k: row.get(k, "") if row.get(k) is not None else "" for k in COLUMNS})
    return buf.getvalue()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Export assessments as a flat CSV for Power BI.")
    ap.add_argument("--db", type=Path, default=db.DEFAULT_DB_PATH)
    ap.add_argument("--assessment", type=int, action="append", help="assessment id (repeatable); default: latest of each option")
    ap.add_argument("--out", type=Path, default=EXPORT_DIR / "carbon_cost_export.csv")
    args = ap.parse_args(argv)
    conn = db.connect(args.db)
    try:
        ids = args.assessment or latest_ids(conn)
        if not ids:
            print("No finalised assessments to export. Run python -m carbon_cost.engine first.")
            return 1
        text = to_csv(conn, ids)
    finally:
        conn.close()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    print(f"Wrote {len(text.splitlines()) - 1} rows for assessments {ids} to {args.out}")
    print("It contains values derived from the carbon data source: keep it for your own use if that source "
          "may not be redistributed (see the source_redistributable column).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
