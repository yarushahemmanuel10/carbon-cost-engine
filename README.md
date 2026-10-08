# carbon-cost-engine

A small learning prototype that links **embodied carbon** to a **cost plan**,
so that one list of quantities gives both a cost and a carbon figure.

This is an independent personal project. It is not affiliated with any
company, and it is not a commercial tool.

> **Status: work in progress.** Built so far: the database (Part 1), the
> ICE ingestion (Part 2) and the project import with an example building
> (Part 3). Not built yet: the calculation engine, the web pages, charts
> and the write-up. This README will be completed at the end; for now it
> covers what exists.

## What is here

| Path | What it is |
|---|---|
| `carbon_cost/schema.sql` | The database: tables and the rules the database enforces itself |
| `carbon_cost/db.py` | Opens the database (foreign keys always on), transactions, superseding a factor |
| `carbon_cost/ingest/ice.py` | Reads the ICE summary sheet and loads the carbon factors |
| `carbon_cost/ingest/densities.py` | Lists density candidates; loads the densities a person has confirmed |
| `carbon_cost/ingest/project.py` | Loads the illustrative cost rates; imports a project from CSV files, checking everything first |
| `data/reference/` | Small files written by hand for this project (committed) |
| `docs/design_note.md` | Why the database is built the way it is |
| `tests/` | Automated tests. They use made-up data only, never ICE data |

## Set up

Needs Python 3.11 or newer (with SQLite 3.37 or newer, which Python 3.11 normally includes).

```
python -m venv .venv
source .venv/bin/activate          # on Windows: .venv\Scripts\activate
pip install -e ".[dev]"
python -m pytest
```

The tests pass without the ICE file. Install with `-e` (editable): the
tool keeps its database in `db/` and reads data from `data/` inside this
folder.

## Get the ICE data (you must do this yourself)

The carbon factors come from the **ICE Database Educational V5.0 (Jun 2026)**
by Circular Ecology Ltd. It is licensed for **educational use only** and
**may not be redistributed, in whole or in part**. For that reason:

- the ICE file is **not** in this repository;
- nothing derived from it is in this repository either (no extracts, no database);
- `.gitignore` blocks `data/raw/`, `data/staging/`, `db/`, and all `.xlsx` and `.db` files.

To use the tool:

1. Download the file from <https://circularecology.com/embodied-carbon-footprint-database.html>.
2. Put it in `data/raw/` with its original name, `ICE_DB_Educational_V5.0_-_Jun_2026.xlsx`.
3. Load the carbon factors, then the confirmed densities:

   ```
   python -m carbon_cost.ingest.ice
   python -m carbon_cost.ingest.densities
   python -m carbon_cost.ingest.project
   ```

   The last step loads the illustrative cost rates and imports the example
   building (four design options). Its assumptions are in
   [`docs/example_assumptions.md`](docs/example_assumptions.md).

   (After `pip install`, the same two steps are also available as
   `carbon-cost-ingest-ice` and `carbon-cost-load-densities`.)

The ingestion prints what it read, what it loaded and what it rejected,
and writes these files to `data/staging/` so every decision can be checked
by eye:

| File | Contents |
|---|---|
| `ice_summary_raw.csv` | The summary sheet exactly as read |
| `ice_parsed.csv` | Every factor that was accepted |
| `ice_rejected.csv` | Every row that was not loaded, with the reason |
| `ice_structure.csv` | Every heading and note, and how it was classified |
| `density_candidates.csv` | Densities listed in the ICE profile sheets, for reference only. Never loaded, never committed. |

Run it once per database. Factors are never overwritten, so a second run
on the same database stops with a message. To reload, delete the file in
`db/` and run it again.

## Data sources

| Data | Source | In this repository? |
|---|---|---|
| Embodied carbon factors (A1-A3) | ICE Database Educational V5.0, Circular Ecology Ltd | No. Licence forbids it. |
| Densities | `data/reference/densities.csv`: standards and manufacturers' data sheets, cited row by row | Yes |
| Cost rates | `data/reference/cost_rates.csv`: illustrative only, invented for this prototype, not commercial data | Yes |

Credit: **ICE Database Educational V5.0, Circular Ecology Ltd**,
<https://circularecology.com/embodied-carbon-footprint-database.html>

### Is V5.0 still the current version?

The summary sheet contains a cell that reads "This version has expired.
Please download a new version". That cell is a date formula, not news of a
newer release: on **1 October 2026** the ICE download page still listed
V5.0 (June 2026) as the latest version (checked by the author).

### Convention: start date of the ICE V5.0 factors

Every factor loaded from ICE V5.0 is given `valid_from = 2026-06-01`, the
month the database was released. An assessment with an earlier "as of"
date will stop with an error that names the material and this date. It
will not fall back to any other factor.

### Densities

ICE gives most factors per kg, so a volume needs a density to become a
mass. Each density in `data/reference/densities.csv` cites its own source
(a standard or a manufacturer's data sheet). None is taken from the ICE
file. A row is only loaded when its `confirmed` column says `yes`.

## Limitations (so far)

- **21 mortar, screed and admixture rows excluded because their unit is not stated in the source.**
- One concrete row ("1:2.5:5" with average UK cement) is excluded because
  its label and its ICE name disagree, and it is not possible to tell
  which is wrong.
- "A Single Brick" sits in a per-kg table but its ICE name describes one
  brick. It is stored per brick (the unit named in its ICE name), and the
  conflict is noted in the factor's comments.
- The full list of limitations will be added when the tool is complete.

## Licence

The code is released under the MIT licence (see `LICENSE`). The licence
covers this repository only. It does not cover the ICE Database, which
has its own terms (see above).
