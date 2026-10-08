"""Load the illustrative cost rates and import projects from CSV files.

    python -m carbon_cost.ingest.project                       # cost rates + the example project
    python -m carbon_cost.ingest.project --project <folder>    # another project folder

A project is a folder with:
  versions.csv   one row per design option (version): project name, label, GIA,
                 and the items file that describes it
  <items>.csv    one row per COMPONENT (material) of an item. The item columns
                 (item, nrm1_code, description, quantity, unit, cost_rate_ref)
                 repeat on every row of the same item. This is the layout a
                 quantity surveyor can fill in Excel.

Materials are named by a short alias (data/reference/material_aliases.csv),
which maps to the full material name in the database.

Everything is checked BEFORE anything is written. All problems are reported
together, with the file and line number, and then nothing is imported.
"""
from __future__ import annotations

import argparse
import csv
from collections import OrderedDict
from pathlib import Path

from carbon_cost import db

REFERENCE_DIR = db.PROJECT_ROOT / "data" / "reference"
ALIASES_CSV = REFERENCE_DIR / "material_aliases.csv"
COST_RATES_CSV = REFERENCE_DIR / "cost_rates.csv"
EXAMPLE_PROJECT = REFERENCE_DIR / "projects" / "example_office"

# The illustrative rates are dated from the same month as the ICE V5.0
# factors, so one as-of date (on or after 2026-06-01) finds both.
COST_VALID_FROM = "2026-06-01"
COST_SOURCE = {
    "name": "Illustrative cost rates",
    "publisher": "Yarushah Emmanuel Sardar (author of this prototype)",
    "version": "2026-10",
    "licence": "MIT, as part of this repository",
    "attribution_text": "Illustrative rates written for this prototype. Not commercial data and not from a price book.",
    "url": None,
    "redistributable": 1,
}
PRICE_BASE = "Illustrative only: invented ballpark figures, not from a price book"

ITEM_COLS = ["item", "nrm1_code", "description", "quantity", "unit", "cost_rate_ref"]
COMPONENT_COLS = ["component", "material", "basis", "basis_value", "host", "comment"]
VERSION_COLS = ["project_name", "project_description", "version_label", "version_description", "gia_m2", "items_file"]
UNITS = ("m2", "m3", "nr")
BASES = ("thickness_m", "volume_ratio", "kg_per_m3", "area_ratio", "count")

# Which basis may be used on which item unit (the database enforces the same rules)
BASIS_ITEM_UNIT = {
    "thickness_m": ("m2",), "area_ratio": ("m2",), "volume_ratio": ("m3",),
    "kg_per_m3": ("m2", "m3"), "count": ("nr",),
}
# Which basis may be used with which factor unit (design note, section 4.3)
BASIS_FACTOR_UNIT = {
    "thickness_m": ("kgCO2e/kg",), "volume_ratio": ("kgCO2e/kg",), "kg_per_m3": ("kgCO2e/kg",),
    "area_ratio": ("kgCO2e/m2",), "count": ("kgCO2e/unit", "kgCO2e/kg"),
}


class ImportProblems(RuntimeError):
    """Raised with every problem found, so they can all be fixed in one go."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("Nothing was imported. Problems found:\n  " + "\n  ".join(problems))


# ---------------------------------------------------------------- helpers
def _read_csv(path: Path, required: list[str]) -> list[tuple[int, dict]]:
    path = Path(path)
    if not path.exists():
        raise ImportProblems([f"{path}: file not found"])
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        missing = [c for c in required if c not in (reader.fieldnames or [])]
        if missing:
            raise ImportProblems([f"{path.name}: missing column(s) {missing}"])
        return [(line, {k: (v or "").strip() for k, v in row.items() if k}) for line, row in enumerate(reader, start=2)
                if any((v or "").strip() for v in row.values())]


def _positive(text: str):
    try:
        v = float(text)
    except ValueError:
        return None
    return v if v > 0 else None


def load_aliases(path: Path = ALIASES_CSV) -> dict[str, str]:
    rows = _read_csv(path, ["alias", "canonical_name"])
    out, problems = {}, []
    for line, r in rows:
        if r["alias"] in out:
            problems.append(f"{Path(path).name} line {line}: alias {r['alias']!r} appears twice")
        out[r["alias"]] = r["canonical_name"]
    if problems:
        raise ImportProblems(problems)
    return out


# ---------------------------------------------------------------- cost rates
def load_cost_rates(conn, csv_path: Path = COST_RATES_CSV, valid_from: str = COST_VALID_FROM) -> dict:
    """Load the illustrative rates: one definition and one rate per row. All or nothing."""
    rows = _read_csv(csv_path, ["ref", "description", "unit", "rate_gbp"])
    if conn.execute("SELECT 1 FROM data_sources WHERE name = ? AND version = ?",
                    (COST_SOURCE["name"], COST_SOURCE["version"])).fetchone():
        raise ImportProblems([f"{COST_SOURCE['name']} {COST_SOURCE['version']} is already in this database. "
                              "Rates are never overwritten; supersede a rate to change it."])
    problems, seen = [], set()
    for line, r in rows:
        where = f"{Path(csv_path).name} line {line}"
        if not r["ref"]:
            problems.append(f"{where}: ref is empty")
        elif r["ref"] in seen:
            problems.append(f"{where}: ref {r['ref']!r} appears twice")
        seen.add(r["ref"])
        if r["unit"] not in UNITS:
            problems.append(f"{where}: unit {r['unit']!r} is not one of {UNITS}")
        try:
            if float(r["rate_gbp"]) < 0:
                problems.append(f"{where}: rate_gbp must not be negative")
        except ValueError:
            problems.append(f"{where}: rate_gbp {r['rate_gbp']!r} is not a number")
        if conn.execute("SELECT 1 FROM cost_rate_definitions WHERE ref = ?", (r["ref"],)).fetchone():
            problems.append(f"{where}: a cost rate definition {r['ref']!r} already exists")
    if problems:
        raise ImportProblems(problems)
    with db.transaction(conn):
        src = conn.execute(
            "INSERT INTO data_sources (name, publisher, version, licence, attribution_text, url, redistributable) "
            "VALUES (:name, :publisher, :version, :licence, :attribution_text, :url, :redistributable)", COST_SOURCE
        ).lastrowid
        for _, r in rows:
            d = conn.execute("INSERT INTO cost_rate_definitions (ref, description, unit) VALUES (?, ?, ?)",
                             (r["ref"], r["description"], r["unit"])).lastrowid
            conn.execute(
                "INSERT INTO cost_rates (definition_id, rate_gbp, data_source_id, is_illustrative, price_base, comments, valid_from) "
                "VALUES (?, ?, ?, 1, ?, ?, ?)",
                (d, float(r["rate_gbp"]), src, PRICE_BASE, r.get("comment") or None, valid_from))
    return {"cost_rates": len(rows)}


# ---------------------------------------------------------------- reading a project
def read_project(folder: Path) -> list[dict]:
    """Read versions.csv and every items file into plain dicts (no database needed).

    Returns a list of versions; each has its items in file order, each item
    has its components. Problems with the FILES themselves (layout, numbers,
    repeated item columns that disagree) are collected and raised together.
    """
    folder = Path(folder)
    versions, problems = [], []
    vrows = _read_csv(folder / "versions.csv", VERSION_COLS)
    labels = set()
    project_names = {r["project_name"] for _, r in vrows}
    if len(project_names) != 1:
        problems.append(f"versions.csv: every row must have the same project_name (found {sorted(project_names)})")
    for vline, v in vrows:
        where = f"versions.csv line {vline}"
        if not v["version_label"]:
            problems.append(f"{where}: version_label is empty")
        elif v["version_label"] in labels:
            problems.append(f"{where}: version_label {v['version_label']!r} appears twice")
        labels.add(v["version_label"])
        gia = _positive(v["gia_m2"])
        if gia is None:
            problems.append(f"{where}: gia_m2 {v['gia_m2']!r} must be a number greater than 0")
        try:
            rows = _read_csv(folder / v["items_file"], ITEM_COLS + COMPONENT_COLS)
        except ImportProblems as exc:
            problems.extend(exc.problems)
            continue
        items: "OrderedDict[str, dict]" = OrderedDict()
        fname = v["items_file"]
        for line, r in rows:
            at = f"{fname} line {line}"
            key = r["item"]
            if not key:
                problems.append(f"{at}: item is empty")
                continue
            head = {c: r[c] for c in ITEM_COLS}
            if key not in items:
                q = _positive(r["quantity"])
                if q is None:
                    problems.append(f"{at}: quantity {r['quantity']!r} must be a number greater than 0")
                if r["unit"] not in UNITS:
                    problems.append(f"{at}: unit {r['unit']!r} is not one of {UNITS}")
                items[key] = {"head": head, "quantity": q, "line": line, "components": OrderedDict(),
                              "comment": r["comment"]}
            elif head != items[key]["head"]:
                diff = [c for c in ITEM_COLS if head[c] != items[key]["head"][c]]
                problems.append(f"{at}: item {key!r} has different {diff} from its first row (line {items[key]['line']})")
                continue
            cid = r["component"]
            if not cid:
                problems.append(f"{at}: component is empty")
                continue
            if cid in items[key]["components"]:
                problems.append(f"{at}: component {cid!r} appears twice in item {key!r}")
                continue
            if r["basis"] not in BASES:
                problems.append(f"{at}: basis {r['basis']!r} is not one of {BASES}")
            bv = _positive(r["basis_value"])
            if bv is None:
                problems.append(f"{at}: basis_value {r['basis_value']!r} must be a number greater than 0")
            items[key]["components"][cid] = {"material": r["material"], "basis": r["basis"], "basis_value": bv,
                                             "host": r["host"] or None, "comment": r["comment"] or None, "line": line}
        versions.append({"project_name": v["project_name"], "project_description": v["project_description"] or None,
                         "label": v["version_label"], "description": v["version_description"] or None,
                         "gia_m2": gia, "file": fname, "items": items})
    if problems:
        raise ImportProblems(problems)
    return versions


# ---------------------------------------------------------------- checking against the database
def check_project(conn, versions: list[dict], aliases: dict[str, str]) -> list[str]:
    """Return every problem found when the project is compared with the database."""
    problems = []
    name = versions[0]["project_name"] if versions else ""
    if conn.execute("SELECT 1 FROM projects WHERE name = ?", (name,)).fetchone():
        problems.append(f"a project called {name!r} is already in the database")
    for v in versions:
        for key, it in v["items"].items():
            h, at = it["head"], f"{v['file']} line {it['line']}"
            if not conn.execute("SELECT 1 FROM elements WHERE code = ?", (h["nrm1_code"],)).fetchone():
                problems.append(f"{at}: NRM1 code {h['nrm1_code']!r} is not in the element list")
            rate = conn.execute("SELECT unit FROM cost_rate_definitions WHERE ref = ?", (h["cost_rate_ref"],)).fetchone()
            if rate is None:
                problems.append(f"{at}: cost rate {h['cost_rate_ref']!r} does not exist")
            elif rate["unit"] != h["unit"]:
                problems.append(f"{at}: item {key!r} is in {h['unit']} but cost rate {h['cost_rate_ref']!r} is per {rate['unit']}")
            for cid, c in it["components"].items():
                cat = f"{v['file']} line {c['line']}: {key}/{cid}"
                if h["unit"] not in BASIS_ITEM_UNIT.get(c["basis"], ()):
                    problems.append(f"{cat}: basis {c['basis']} cannot be used on an item measured in {h['unit']}")
                # host rules
                if c["host"] is not None:
                    host = it["components"].get(c["host"])
                    if c["basis"] != "kg_per_m3":
                        problems.append(f"{cat}: only a kg_per_m3 component can name a host")
                    elif h["unit"] == "m3":
                        problems.append(f"{cat}: on an m3 item, kg_per_m3 uses the item volume, so host must be empty")
                    elif host is None:
                        problems.append(f"{cat}: host {c['host']!r} is not a component of item {key!r}")
                    elif host["basis"] != "thickness_m":
                        problems.append(f"{cat}: host {c['host']!r} must be a thickness_m component")
                elif c["basis"] == "kg_per_m3" and h["unit"] == "m2":
                    problems.append(f"{cat}: kg_per_m3 on an m2 item needs a host (the thickness_m component it sits in)")
                # material, factor unit and density
                canonical = aliases.get(c["material"])
                if canonical is None:
                    problems.append(f"{cat}: material alias {c['material']!r} is not in material_aliases.csv")
                    continue
                mat = conn.execute("SELECT id, density_kg_m3, mass_per_item_kg FROM materials WHERE canonical_name = ?",
                                   (canonical,)).fetchone()
                if mat is None:
                    problems.append(f"{cat}: material {canonical!r} is not in the database (has the ICE data been loaded?)")
                    continue
                c["material_id"] = mat["id"]
                units = {r[0] for r in conn.execute(
                    "SELECT DISTINCT unit FROM carbon_factors WHERE material_id = ? AND lifecycle_module = 'A1-A3'", (mat["id"],))}
                if not units:
                    problems.append(f"{cat}: material {c['material']!r} has no A1-A3 carbon factor")
                elif not units & set(BASIS_FACTOR_UNIT.get(c["basis"], ())):
                    problems.append(f"{cat}: basis {c['basis']} needs a factor in {BASIS_FACTOR_UNIT.get(c['basis'])}, "
                                    f"but {c['material']!r} has {sorted(units)}")
                if c["basis"] in ("thickness_m", "volume_ratio") and mat["density_kg_m3"] is None:
                    problems.append(f"{cat}: material {c['material']!r} has no confirmed density, which basis {c['basis']} needs")
                if c["basis"] == "count" and units == {"kgCO2e/kg"} and mat["mass_per_item_kg"] is None:
                    problems.append(f"{cat}: a count of a per-kg material needs mass_per_item_kg, which {c['material']!r} lacks")
    return problems


# ---------------------------------------------------------------- import
def import_project(conn, folder: Path = EXAMPLE_PROJECT, aliases_path: Path = ALIASES_CSV) -> dict:
    """Check everything, then write the project in one transaction. Returns {label: version_id}."""
    versions = read_project(folder)
    aliases = load_aliases(aliases_path)
    problems = check_project(conn, versions, aliases)
    if problems:
        raise ImportProblems(problems)
    out = {}
    with db.transaction(conn):
        first = versions[0]
        pid = conn.execute("INSERT INTO projects (name, description) VALUES (?, ?)",
                           (first["project_name"], first["project_description"])).lastrowid
        for v in versions:
            vid = conn.execute("INSERT INTO project_versions (project_id, label, description, gia_m2) VALUES (?, ?, ?, ?)",
                               (pid, v["label"], v["description"], v["gia_m2"])).lastrowid
            out[v["label"]] = vid
            for it in v["items"].values():
                h = it["head"]
                iid = conn.execute(
                    "INSERT INTO element_items (project_version_id, element_id, description, quantity, unit, "
                    "cost_rate_definition_id, comment) VALUES (?, (SELECT id FROM elements WHERE code = ?), ?, ?, ?, "
                    "(SELECT id FROM cost_rate_definitions WHERE ref = ?), ?)",
                    (vid, h["nrm1_code"], h["description"], it["quantity"], h["unit"], h["cost_rate_ref"], it["comment"] or None),
                ).lastrowid
                ids = {}
                # hosts first, so a rebar row can point at its concrete
                ordered = sorted(it["components"].items(), key=lambda kv: kv[1]["host"] is not None)
                for cid, c in ordered:
                    ids[cid] = conn.execute(
                        "INSERT INTO item_components (element_item_id, material_id, basis, basis_value, host_component_id, comment) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (iid, c["material_id"], c["basis"], c["basis_value"], ids.get(c["host"]) if c["host"] else None,
                         c["comment"]),
                    ).lastrowid
    return out


# ---------------------------------------------------------------- command line
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Load the illustrative cost rates and import a project.")
    ap.add_argument("--db", type=Path, default=db.DEFAULT_DB_PATH)
    ap.add_argument("--project", type=Path, default=EXAMPLE_PROJECT)
    ap.add_argument("--skip-rates", action="store_true", help="the cost rates are already loaded")
    args = ap.parse_args(argv)
    if not Path(args.db).exists():
        print(f"ERROR: {args.db} not found. Load the ICE data first: python -m carbon_cost.ingest.ice")
        return 1
    conn = db.connect(args.db)
    try:
        if not args.skip_rates:
            print(f"Cost rates loaded: {load_cost_rates(conn)['cost_rates']} (illustrative, valid from {COST_VALID_FROM})")
        made = import_project(conn, args.project)
    except ImportProblems as exc:
        print(f"ERROR: {exc}")
        return 1
    finally:
        conn.close()
    print(f"Project imported from {args.project.name}:")
    for label, vid in made.items():
        print(f"   version {vid}: {label}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
