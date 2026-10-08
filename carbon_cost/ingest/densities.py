"""Densities: candidates from the ICE profile sheets, and loading the confirmed ones.

Two separate jobs live here.

1. extract_density_candidates()  reads the "Material Properties (CIBSE Data)"
   table found on each ICE material profile sheet and writes every row to
   data/staging/density_candidates.csv. These are CANDIDATES only. The
   data is old (the ICE sheets date it to 2011) and generic, and nothing
   from this file is ever loaded automatically.

2. load_densities()  reads data/reference/densities.csv, a small file
   written and checked by a person, and puts the densities on the
   materials. A row is only loaded if its `confirmed` column says "yes".
"""
from __future__ import annotations

import csv
import re
from pathlib import Path

from carbon_cost import db

TABLE_TITLE = "material properties (cibse data)"
MAX_SCAN_ROWS = 3000            # the tables sit around row 530; never walk the ~1M empty rows
MAX_COLS = 16

# Sanity ranges in kg/m3, by profile sheet. A value outside its range is
# FLAGGED, not removed: the sheet may hold a different product (foam glass
# on the Glass sheet, aerated blocks on the Concrete sheet).
SANITY_RANGES = {
    "Timber": (300, 1000),
    "Concrete": (600, 2600),
    "Steel": (7700, 8100),
    "Glass": (2400, 2600),
    "Clay_Bricks": (1500, 2200),
}

DENSITIES_CSV = db.PROJECT_ROOT / "data" / "reference" / "densities.csv"
DENSITY_COLUMNS = ["material", "density_kg_m3", "source", "confirmed", "note"]


class DensityError(RuntimeError):
    pass


def _clean(v):
    if v is None:
        return None
    s = re.sub(r"\s+", " ", str(v)).strip()
    return s or None


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def extract_density_candidates(xlsx_path: Path) -> list[dict]:
    """Return one dict per density row found in any '(CIBSE Data)' table of the workbook."""
    import warnings

    import openpyxl

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        wb = openpyxl.load_workbook(xlsx_path, read_only=True, data_only=True)
        out: list[dict] = []
        try:
            for ws in wb.worksheets:
                out.extend(_read_sheet(ws))
        finally:
            wb.close()
    return out


def _read_sheet(ws) -> list[dict]:
    rows_out: list[dict] = []
    state = "search"            # search -> header -> data
    mat_col = cond_col = dens_col = None
    group = None
    current = None              # material name carried down to rows with a blank name
    empty_run = 0
    for number, row in enumerate(ws.iter_rows(min_row=1, max_row=MAX_SCAN_ROWS, max_col=MAX_COLS, values_only=True), start=1):
        if state == "search":
            if any(isinstance(v, str) and v.strip().lower() == TABLE_TITLE for v in row):
                state = "header"
            continue
        if state == "header":
            texts = {i: _clean(v).lower() for i, v in enumerate(row) if isinstance(v, str) and _clean(v)}
            for i, t in texts.items():
                if t == "material":
                    mat_col = i
                elif t == "condition":
                    cond_col = i
                elif t.startswith("density"):
                    dens_col = i
            if mat_col is not None and dens_col is not None:
                state = "data"
            elif texts:
                # a title with no usable header underneath: give up on this sheet
                return rows_out
            continue
        # ---- data rows
        material = _clean(row[mat_col]) if mat_col < len(row) else None
        density = row[dens_col] if dens_col < len(row) else None
        condition = _clean(row[cond_col]) if cond_col is not None and cond_col < len(row) else None
        if condition in ("0", "0.0"):
            condition = None    # empty cells that Excel stored as 0
        if material is None and not _is_number(density):
            empty_run += 1
            if empty_run >= 3:
                break
            continue
        empty_run = 0
        note = ""
        name_carried = False
        if material is not None and material.endswith(":"):
            new_group = material.rstrip(":").strip()
            if _is_number(density):
                # a heading row that also holds a density: the density belongs to the material above it
                note = f"density sits on the heading row '{new_group}'; attached to the material above"
                if current is not None:
                    rows_out.append(_candidate(ws.title, number, group, current, True, condition, density, note))
            group, current = new_group, None        # names never carry across a group heading
            continue
        if material is None:
            if current is None:
                # blank name straight after a group heading: the row describes the group itself
                material, name_carried = group, False
                note = "no material name on this row; it is listed directly under the group heading"
            else:
                material, name_carried = current, True
        else:
            current = material
        if not _is_number(density):
            continue
        rows_out.append(_candidate(ws.title, number, group, material, name_carried, condition, density, note))
    return rows_out


def _candidate(sheet, number, group, material, name_carried, condition, density, note) -> dict:
    low_high = SANITY_RANGES.get(sheet)
    if low_high is None:
        check = "no range defined"
    elif low_high[0] <= density <= low_high[1]:
        check = "ok"
    else:
        check = "OUTSIDE RANGE"
    return {
        "sheet": sheet, "row": number, "group": group or "", "material": material or "",
        "name_carried_from_row_above": "yes" if name_carried else "no",
        "condition": condition or "", "density_kg_m3": float(density),
        "range_low": low_high[0] if low_high else "", "range_high": low_high[1] if low_high else "",
        "range_check": check, "note": note,
    }


def write_candidates(cands: list[dict], path: Path) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    cols = ["sheet", "row", "group", "material", "name_carried_from_row_above", "condition",
            "density_kg_m3", "range_low", "range_high", "range_check", "note"]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(cands)
    return {
        "total": len(cands),
        "sheets": len({c["sheet"] for c in cands}),
        "flagged": sum(c["range_check"] == "OUTSIDE RANGE" for c in cands),
        "ok": sum(c["range_check"] == "ok" for c in cands),
        "no_range": sum(c["range_check"] == "no range defined" for c in cands),
    }


# ---------------------------------------------------------------- loading confirmed densities
def load_densities(conn, csv_path: Path = DENSITIES_CSV) -> dict:
    """Put the confirmed densities from densities.csv onto the materials.

    * Only rows with confirmed = yes are loaded. Others are reported and skipped.
    * An unknown material, a missing source or a density that is not a
      positive number stops the whole load: nothing is changed.
    Returns {'loaded': [...], 'skipped_unconfirmed': [...]}.
    """
    csv_path = Path(csv_path)
    if not csv_path.exists():
        raise DensityError(f"{csv_path} not found")
    with open(csv_path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        missing = [c for c in DENSITY_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise DensityError(f"{csv_path.name}: missing column(s) {missing}; expected {DENSITY_COLUMNS}")
        rows = list(reader)

    to_load, skipped, problems, seen = [], [], [], set()
    for line, r in enumerate(rows, start=2):
        name = (r["material"] or "").strip()
        if not name:
            continue
        if name.lower() in seen:
            problems.append(f"line {line}: material {name!r} appears twice")
            continue
        seen.add(name.lower())
        # checked for every row, confirmed or not, so a mistyped name is caught early
        mat = conn.execute("SELECT id FROM materials WHERE canonical_name = ?", (name,)).fetchone()
        if mat is None:
            problems.append(f"line {line}: material {name!r} is not in the database (names must match exactly)")
            continue
        if (r["confirmed"] or "").strip().lower() != "yes":
            skipped.append(name)
            continue
        try:
            density = float(r["density_kg_m3"])
        except (TypeError, ValueError):
            problems.append(f"line {line}: density {r['density_kg_m3']!r} for {name!r} is not a number")
            continue
        if not density > 0:
            problems.append(f"line {line}: density for {name!r} must be greater than 0")
            continue
        source = (r["source"] or "").strip()
        if not source:
            problems.append(f"line {line}: {name!r} has no source; every density must say where it came from")
            continue
        to_load.append((density, source, mat["id"], name))
    if problems:
        raise DensityError("densities.csv was not loaded:\n  " + "\n  ".join(problems))
    with db.transaction(conn):
        for density, source, mat_id, _ in to_load:
            conn.execute("UPDATE materials SET density_kg_m3 = ?, density_source = ? WHERE id = ?",
                         (density, source, mat_id))
    return {"loaded": [n for *_, n in to_load], "skipped_unconfirmed": skipped}


def main() -> int:
    """python -m carbon_cost.ingest.densities : load the confirmed rows of densities.csv."""
    c = db.connect()
    try:
        result = load_densities(c)
    except DensityError as exc:
        print(f"ERROR: {exc}")
        return 1
    finally:
        c.close()
    print(f"Densities loaded: {len(result['loaded'])}")
    for n in result["loaded"]:
        print(f"   {n}")
    print(f"Skipped (not confirmed yet): {len(result['skipped_unconfirmed'])}")
    for n in result["skipped_unconfirmed"]:
        print(f"   {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
