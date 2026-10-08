-- =====================================================================
-- carbon-cost-engine : database schema (SQLite 3.37 or newer)
-- =====================================================================
-- How to read this file
--   * Every table is STRICT: SQLite will refuse text in a number column.
--   * Foreign keys only work if the connection runs
--         PRAGMA foreign_keys = ON;
--     carbon_cost/db.py does this for every connection. Do not open the database
--     any other way.
--   * Dates are text in ISO form 'YYYY-MM-DD'.
--   * "Versioned" tables (carbon_factors, biogenic_carbon, cost_rates)
--     are never edited and never deleted. A new row replaces an old one
--     and the old one is kept. Triggers below enforce this.
--   * valid_from is the first day a row applies. valid_to is the first
--     day it NO LONGER applies (so the period is valid_from <= d < valid_to).
--     valid_to = NULL means "still current".
-- =====================================================================

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------
-- 1. data_sources : where every number came from
-- ---------------------------------------------------------------------
CREATE TABLE data_sources (
    id               INTEGER PRIMARY KEY,
    name             TEXT    NOT NULL,
    publisher        TEXT    NOT NULL,
    version          TEXT    NOT NULL,
    licence          TEXT    NOT NULL,
    attribution_text TEXT    NOT NULL,
    url              TEXT,
    redistributable  INTEGER NOT NULL CHECK (redistributable IN (0, 1)),
    UNIQUE (name, version)
) STRICT;

-- ---------------------------------------------------------------------
-- 2. materials : one row per material, whatever its factor is
-- ---------------------------------------------------------------------
CREATE TABLE materials (
    id                INTEGER PRIMARY KEY,
    canonical_name    TEXT NOT NULL UNIQUE,   -- full, self-explaining name
    category          TEXT NOT NULL,          -- e.g. 'Concrete'
    subcategory_path  TEXT,                   -- e.g. 'Concrete > In-Situ > By Strength Class > With CEM I'
    name_in_source    TEXT,                   -- name exactly as written in the source
    data_source_id    INTEGER REFERENCES data_sources (id),
    density_kg_m3     REAL CHECK (density_kg_m3 IS NULL OR density_kg_m3 > 0),
    density_source    TEXT,
    -- mass of ONE declared unit (one item, or one m2 for per-m2 materials)
    mass_per_item_kg  REAL CHECK (mass_per_item_kg IS NULL OR mass_per_item_kg > 0),
    -- a density must always say where it came from
    CHECK (density_kg_m3 IS NULL OR density_source IS NOT NULL)
) STRICT;

CREATE INDEX idx_materials_category ON materials (category);

-- ---------------------------------------------------------------------
-- 3. carbon_factors : versioned embodied carbon factors
-- ---------------------------------------------------------------------
CREATE TABLE carbon_factors (
    id                     INTEGER PRIMARY KEY,
    material_id            INTEGER NOT NULL REFERENCES materials (id),
    value                  REAL    NOT NULL,
    unit                   TEXT    NOT NULL
                           CHECK (unit IN ('kgCO2e/kg', 'kgCO2e/m2', 'kgCO2e/unit')),
    -- for 'kgCO2e/unit' only: what one "unit" is, in the source's words
    -- (e.g. 'm' for a pipe, '1 m2, per 100mm depth')
    declared_unit          TEXT,
    lifecycle_module       TEXT    NOT NULL
                           CHECK (lifecycle_module IN (
                               'A1-A3', 'A4', 'A5',
                               'B1', 'B2', 'B3', 'B4', 'B5', 'B6', 'B7',
                               'C1', 'C2', 'C3', 'C4', 'D')),
    data_source_id         INTEGER NOT NULL REFERENCES data_sources (id),
    -- some ICE V5 sections are carried over from older releases
    -- (heading 'Version: ICE V 2.0 2011'); recorded here when present
    source_section_version TEXT,
    dqi_score              REAL CHECK (dqi_score IS NULL OR (dqi_score >= 0 AND dqi_score <= 1)),
    dqi_version            TEXT,
    comments               TEXT,
    valid_from             TEXT    NOT NULL CHECK (valid_from IS date(valid_from)),
    valid_to               TEXT    CHECK (valid_to IS NULL OR valid_to IS date(valid_to)),
    superseded_by          INTEGER REFERENCES carbon_factors (id),
    CHECK (valid_to IS NULL OR valid_to > valid_from),
    CHECK (superseded_by IS NULL OR valid_to IS NOT NULL),
    CHECK (superseded_by IS NULL OR superseded_by <> id),
    -- only module D (benefits beyond the life cycle) may be negative.
    -- Timber carbon storage is NOT a negative factor here: it lives in
    -- biogenic_carbon.
    CHECK (value >= 0 OR lifecycle_module = 'D')
) STRICT;

CREATE INDEX idx_carbon_factors_lookup
    ON carbon_factors (material_id, lifecycle_module, valid_from);

-- at most one "still current" factor per material and module
CREATE UNIQUE INDEX uq_carbon_factors_current
    ON carbon_factors (material_id, lifecycle_module)
    WHERE valid_to IS NULL;

-- ---------------------------------------------------------------------
-- 4. biogenic_carbon : carbon stored in timber, kept apart on purpose
-- ---------------------------------------------------------------------
-- Sign: same as the source. ICE reports storage as a NEGATIVE number
-- (carbon taken out of the air), so value must be <= 0.
CREATE TABLE biogenic_carbon (
    id                INTEGER PRIMARY KEY,
    material_id       INTEGER NOT NULL REFERENCES materials (id),
    value_kgco2e_per_kg REAL  NOT NULL CHECK (value_kgco2e_per_kg <= 0),
    data_source_id    INTEGER NOT NULL REFERENCES data_sources (id),
    comments          TEXT,
    valid_from        TEXT    NOT NULL CHECK (valid_from IS date(valid_from)),
    valid_to          TEXT    CHECK (valid_to IS NULL OR valid_to IS date(valid_to)),
    superseded_by     INTEGER REFERENCES biogenic_carbon (id),
    CHECK (valid_to IS NULL OR valid_to > valid_from),
    CHECK (superseded_by IS NULL OR valid_to IS NOT NULL),
    CHECK (superseded_by IS NULL OR superseded_by <> id)
) STRICT;

CREATE UNIQUE INDEX uq_biogenic_current
    ON biogenic_carbon (material_id)
    WHERE valid_to IS NULL;

-- ---------------------------------------------------------------------
-- 5. cost_rate_definitions + cost_rates : what is priced, and its price
-- ---------------------------------------------------------------------
-- A definition says WHAT is priced and in WHAT unit. It never changes.
-- cost_rates holds the price over time (same versioning as carbon_factors).
-- This mirrors materials -> carbon_factors.
CREATE TABLE cost_rate_definitions (
    id          INTEGER PRIMARY KEY,
    ref         TEXT NOT NULL UNIQUE,     -- short code used in the project CSV
    description TEXT NOT NULL,            -- e.g. 'Upper floor slab, 250mm RC, incl. formwork and rebar'
    unit        TEXT NOT NULL CHECK (unit IN ('m2', 'm3', 'nr'))
) STRICT;

CREATE TABLE cost_rates (
    id                 INTEGER PRIMARY KEY,
    definition_id      INTEGER NOT NULL REFERENCES cost_rate_definitions (id),
    rate_gbp           REAL    NOT NULL CHECK (rate_gbp >= 0),   -- GBP per unit of the definition
    data_source_id     INTEGER NOT NULL REFERENCES data_sources (id),
    is_illustrative    INTEGER NOT NULL DEFAULT 1 CHECK (is_illustrative IN (0, 1)),
    price_base         TEXT,              -- e.g. '2026 Q2, UK average' (free text)
    comments           TEXT,
    valid_from         TEXT    NOT NULL CHECK (valid_from IS date(valid_from)),
    valid_to           TEXT    CHECK (valid_to IS NULL OR valid_to IS date(valid_to)),
    superseded_by      INTEGER REFERENCES cost_rates (id),
    CHECK (valid_to IS NULL OR valid_to > valid_from),
    CHECK (superseded_by IS NULL OR valid_to IS NOT NULL),
    CHECK (superseded_by IS NULL OR superseded_by <> id)
) STRICT;

CREATE INDEX idx_cost_rates_lookup ON cost_rates (definition_id, valid_from);

CREATE UNIQUE INDEX uq_cost_rates_current
    ON cost_rates (definition_id)
    WHERE valid_to IS NULL;

-- ---------------------------------------------------------------------
-- 6. elements : NRM1 element hierarchy (a tree)
-- ---------------------------------------------------------------------
CREATE TABLE elements (
    id        INTEGER PRIMARY KEY,
    code      TEXT NOT NULL UNIQUE,       -- '2', '2.1', '2.2' ...
    name      TEXT NOT NULL,
    parent_id INTEGER REFERENCES elements (id),
    CHECK (parent_id IS NULL OR parent_id <> id)
) STRICT;

CREATE INDEX idx_elements_parent ON elements (parent_id);

-- ---------------------------------------------------------------------
-- 7. projects and project_versions (design options)
-- ---------------------------------------------------------------------
CREATE TABLE projects (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    description TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
) STRICT;

CREATE TABLE project_versions (
    id          INTEGER PRIMARY KEY,
    project_id  INTEGER NOT NULL REFERENCES projects (id),
    label       TEXT    NOT NULL,         -- e.g. 'Option A - concrete frame'
    description TEXT,
    gia_m2      REAL    NOT NULL CHECK (gia_m2 > 0),   -- gross internal area
    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    -- versions are archived (hidden in the app), not deleted
    archived    INTEGER NOT NULL DEFAULT 0 CHECK (archived IN (0, 1)),
    UNIQUE (project_id, label)
) STRICT;

-- ---------------------------------------------------------------------
-- 8. element_items : a measured quantity on an element
-- ---------------------------------------------------------------------
CREATE TABLE element_items (
    id                      INTEGER PRIMARY KEY,
    project_version_id      INTEGER NOT NULL
                            REFERENCES project_versions (id) ON DELETE CASCADE,
    element_id              INTEGER NOT NULL REFERENCES elements (id),
    description             TEXT    NOT NULL,   -- e.g. 'Upper floor slab, 250mm RC'
    quantity                REAL    NOT NULL CHECK (quantity > 0),
    unit                    TEXT    NOT NULL CHECK (unit IN ('m2', 'm3', 'nr')),
    cost_rate_definition_id INTEGER NOT NULL REFERENCES cost_rate_definitions (id),
    comment                 TEXT                -- assumptions go here
) STRICT;

CREATE INDEX idx_element_items_version ON element_items (project_version_id);
CREATE INDEX idx_element_items_element ON element_items (element_id);

-- ---------------------------------------------------------------------
-- 9. item_components : the materials inside an item
-- ---------------------------------------------------------------------
-- basis says how basis_value turns the item quantity into an amount of
-- this material:
--   thickness_m  : item in m2. volume = quantity x thickness. Needs density.
--   volume_ratio : item in m3. volume = quantity x ratio (normally 1.0). Needs density.
--   kg_per_m3    : mass = volume of concrete x kg per m3 (rebar).
--                  item in m3 -> the item's own volume (host must be empty)
--                  item in m2 -> the volume of the host component, which
--                                must be a thickness_m component of the same item
--   area_ratio   : item in m2. area = quantity x ratio (normally 1.0).
--                  For factors given per m2 (glazing, brick walls).
--   count        : item in nr. number = quantity x count per item.
CREATE TABLE item_components (
    id                INTEGER PRIMARY KEY,
    element_item_id   INTEGER NOT NULL
                      REFERENCES element_items (id) ON DELETE CASCADE,
    material_id       INTEGER NOT NULL REFERENCES materials (id),
    basis             TEXT    NOT NULL
                      CHECK (basis IN ('thickness_m', 'volume_ratio', 'kg_per_m3',
                                       'area_ratio', 'count')),
    basis_value       REAL    NOT NULL CHECK (basis_value > 0),
    host_component_id INTEGER REFERENCES item_components (id),
    comment           TEXT,
    CHECK (host_component_id IS NULL OR basis = 'kg_per_m3'),
    CHECK (host_component_id IS NULL OR host_component_id <> id)
) STRICT;

CREATE INDEX idx_item_components_item ON item_components (element_item_id);

-- ---------------------------------------------------------------------
-- 10. assessments : one row per calculation run, plus its lines
-- ---------------------------------------------------------------------
CREATE TABLE assessments (
    id                 INTEGER PRIMARY KEY,
    project_version_id INTEGER NOT NULL REFERENCES project_versions (id),
    as_of_date         TEXT    NOT NULL CHECK (as_of_date IS date(as_of_date)),
    run_at             TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    gia_m2             REAL    NOT NULL CHECK (gia_m2 > 0),   -- copied from the version at run time
    notes              TEXT,
    -- empty while lines are being written. Set once, when every line is
    -- in. After that the assessment and its lines can never change.
    finalised_at       TEXT    CHECK (finalised_at IS NULL
                                      OR finalised_at IS strftime('%Y-%m-%dT%H:%M:%SZ', finalised_at))
) STRICT;

CREATE INDEX idx_assessments_version ON assessments (project_version_id);

-- One line per component per lifecycle module (carbon side).
-- Each line stores the inputs that were used AND the id of the exact
-- carbon_factors row, so the sum can be redone later.
CREATE TABLE assessment_lines (
    id                    INTEGER PRIMARY KEY,
    -- CASCADE only ever removes lines of an assessment that is NOT finalised
    -- (a finalised assessment cannot be deleted at all)
    assessment_id         INTEGER NOT NULL REFERENCES assessments (id) ON DELETE CASCADE,
    element_id            INTEGER NOT NULL REFERENCES elements (id),
    -- links back to the live project rows; set to empty if those rows
    -- are later deleted. The copies below keep the line readable.
    element_item_id       INTEGER REFERENCES element_items (id) ON DELETE SET NULL,
    item_component_id     INTEGER REFERENCES item_components (id) ON DELETE SET NULL,
    -- copies of the inputs at run time
    item_description      TEXT    NOT NULL,
    item_quantity         REAL    NOT NULL,
    item_unit             TEXT    NOT NULL,
    material_id           INTEGER NOT NULL REFERENCES materials (id),
    basis                 TEXT    NOT NULL
                          CHECK (basis IN ('thickness_m', 'volume_ratio', 'kg_per_m3',
                                           'area_ratio', 'count')),
    basis_value           REAL    NOT NULL,
    volume_m3             REAL,              -- volume used, when the basis has one
    density_kg_m3_used    REAL,
    mass_per_item_kg_used REAL,
    mass_kg               REAL,              -- empty if mass cannot be known
    -- the calculation: carbon = factor_quantity x factor value
    factor_quantity       REAL    NOT NULL,  -- kg, m2 or units, to match the factor
    factor_unit           TEXT    NOT NULL
                          CHECK (factor_unit IN ('kgCO2e/kg', 'kgCO2e/m2', 'kgCO2e/unit')),
    lifecycle_module      TEXT    NOT NULL,
    carbon_factor_id      INTEGER NOT NULL REFERENCES carbon_factors (id),
    carbon_kgco2e         REAL    NOT NULL,
    -- carbon stored in timber: its own columns, never inside carbon_kgco2e
    biogenic_carbon_id    INTEGER REFERENCES biogenic_carbon (id),
    biogenic_kgco2e       REAL,
    CHECK ((biogenic_carbon_id IS NULL) = (biogenic_kgco2e IS NULL)),
    -- storage is reported once, next to the A1-A3 line
    CHECK (biogenic_carbon_id IS NULL OR lifecycle_module = 'A1-A3'),
    -- storage is per kg, so it can only be worked out when a mass is known
    CHECK (biogenic_carbon_id IS NULL OR mass_kg IS NOT NULL),
    -- a per-kg factor multiplies a mass: the two columns must agree
    CHECK (factor_unit <> 'kgCO2e/kg' OR (mass_kg IS NOT NULL AND factor_quantity = mass_kg)),
    -- which basis may be used with which kind of factor:
    --   area_ratio                            -> factor per m2 only
    --   thickness_m, volume_ratio, kg_per_m3  -> factor per kg only
    --   count                                 -> factor per unit, or per kg
    --                                            (per kg needs mass_per_item_kg)
    CHECK (
        (basis = 'area_ratio' AND factor_unit = 'kgCO2e/m2')
        OR (basis IN ('thickness_m', 'volume_ratio', 'kg_per_m3') AND factor_unit = 'kgCO2e/kg')
        OR (basis = 'count' AND factor_unit IN ('kgCO2e/unit', 'kgCO2e/kg'))
    )
) STRICT;

CREATE INDEX idx_assessment_lines_assessment ON assessment_lines (assessment_id);

-- One line per item (cost side). Cost is priced per item, not per material.
CREATE TABLE assessment_cost_lines (
    id               INTEGER PRIMARY KEY,
    assessment_id    INTEGER NOT NULL REFERENCES assessments (id) ON DELETE CASCADE,
    element_id       INTEGER NOT NULL REFERENCES elements (id),
    element_item_id  INTEGER REFERENCES element_items (id) ON DELETE SET NULL,
    item_description TEXT    NOT NULL,
    item_quantity    REAL    NOT NULL,
    item_unit        TEXT    NOT NULL,
    cost_rate_id     INTEGER NOT NULL REFERENCES cost_rates (id),
    cost_gbp         REAL    NOT NULL
) STRICT;

CREATE INDEX idx_assessment_cost_lines_assessment ON assessment_cost_lines (assessment_id);

-- ---------------------------------------------------------------------
-- 11. benchmarks : published targets to compare against (optional)
-- ---------------------------------------------------------------------
CREATE TABLE benchmarks (
    id              INTEGER PRIMARY KEY,
    name            TEXT    NOT NULL,
    value_kgco2e_m2 REAL    NOT NULL,
    building_type   TEXT    NOT NULL,
    lifecycle_scope TEXT    NOT NULL,     -- e.g. 'A1-A5', 'A-C'
    source          TEXT    NOT NULL,
    year            INTEGER NOT NULL,
    UNIQUE (name, building_type, lifecycle_scope, year)
) STRICT;


-- =====================================================================
-- TRIGGERS
-- =====================================================================
-- A trigger is a small rule the database runs by itself when a row is
-- added, changed or removed. These protect the audit trail even if the
-- Python code has a bug.

-- ---------- carbon_factors: superseded, never deleted or edited -------
CREATE TRIGGER carbon_factors_no_delete
BEFORE DELETE ON carbon_factors
BEGIN
    SELECT RAISE(ABORT, 'carbon_factors rows are never deleted: supersede the factor instead');
END;

CREATE TRIGGER carbon_factors_immutable
BEFORE UPDATE OF id, material_id, value, unit, declared_unit, lifecycle_module,
                 data_source_id, source_section_version, dqi_score, dqi_version,
                 comments, valid_from
ON carbon_factors
BEGIN
    SELECT RAISE(ABORT, 'carbon_factors rows are never edited: add a new factor and supersede the old one');
END;

CREATE TRIGGER carbon_factors_close_once
BEFORE UPDATE OF valid_to ON carbon_factors
WHEN OLD.valid_to IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'carbon_factors.valid_to is already set and cannot be changed');
END;

CREATE TRIGGER carbon_factors_supersede_check
BEFORE UPDATE OF superseded_by ON carbon_factors
BEGIN
    SELECT RAISE(ABORT, 'carbon_factors.superseded_by is already set and cannot be changed')
    WHERE OLD.superseded_by IS NOT NULL;
    SELECT RAISE(ABORT, 'carbon_factors: the replacing factor must be for the same material and lifecycle module, and start on or after the old valid_to')
    WHERE NEW.superseded_by IS NOT NULL
      AND NOT EXISTS (
          SELECT 1 FROM carbon_factors n
          WHERE n.id = NEW.superseded_by
            AND n.material_id = OLD.material_id
            AND n.lifecycle_module = OLD.lifecycle_module
            AND NEW.valid_to IS NOT NULL
            AND n.valid_from >= NEW.valid_to);
END;

CREATE TRIGGER carbon_factors_no_overlap
BEFORE INSERT ON carbon_factors
BEGIN
    SELECT RAISE(ABORT, 'carbon_factors: validity period overlaps an existing factor for this material and lifecycle module (close the old factor first)')
    WHERE EXISTS (
        SELECT 1 FROM carbon_factors f
        WHERE f.material_id = NEW.material_id
          AND f.lifecycle_module = NEW.lifecycle_module
          AND f.valid_from < COALESCE(NEW.valid_to, '9999-12-31')
          AND COALESCE(f.valid_to, '9999-12-31') > NEW.valid_from);
END;

-- ---------- biogenic_carbon: same rules --------------------------------
CREATE TRIGGER biogenic_carbon_no_delete
BEFORE DELETE ON biogenic_carbon
BEGIN
    SELECT RAISE(ABORT, 'biogenic_carbon rows are never deleted: supersede the row instead');
END;

CREATE TRIGGER biogenic_carbon_immutable
BEFORE UPDATE OF id, material_id, value_kgco2e_per_kg, data_source_id, comments, valid_from
ON biogenic_carbon
BEGIN
    SELECT RAISE(ABORT, 'biogenic_carbon rows are never edited: add a new row and supersede the old one');
END;

CREATE TRIGGER biogenic_carbon_close_once
BEFORE UPDATE OF valid_to ON biogenic_carbon
WHEN OLD.valid_to IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'biogenic_carbon.valid_to is already set and cannot be changed');
END;

CREATE TRIGGER biogenic_carbon_supersede_check
BEFORE UPDATE OF superseded_by ON biogenic_carbon
BEGIN
    SELECT RAISE(ABORT, 'biogenic_carbon.superseded_by is already set and cannot be changed')
    WHERE OLD.superseded_by IS NOT NULL;
    SELECT RAISE(ABORT, 'biogenic_carbon: the replacing row must be for the same material and start on or after the old valid_to')
    WHERE NEW.superseded_by IS NOT NULL
      AND NOT EXISTS (
          SELECT 1 FROM biogenic_carbon n
          WHERE n.id = NEW.superseded_by
            AND n.material_id = OLD.material_id
            AND NEW.valid_to IS NOT NULL
            AND n.valid_from >= NEW.valid_to);
END;

CREATE TRIGGER biogenic_carbon_no_overlap
BEFORE INSERT ON biogenic_carbon
BEGIN
    SELECT RAISE(ABORT, 'biogenic_carbon: validity period overlaps an existing row for this material (close the old row first)')
    WHERE EXISTS (
        SELECT 1 FROM biogenic_carbon b
        WHERE b.material_id = NEW.material_id
          AND b.valid_from < COALESCE(NEW.valid_to, '9999-12-31')
          AND COALESCE(b.valid_to, '9999-12-31') > NEW.valid_from);
END;

-- ---------- cost_rates: same rules -------------------------------------
CREATE TRIGGER cost_rates_no_delete
BEFORE DELETE ON cost_rates
BEGIN
    SELECT RAISE(ABORT, 'cost_rates rows are never deleted: supersede the rate instead');
END;

CREATE TRIGGER cost_rates_immutable
BEFORE UPDATE OF id, definition_id, rate_gbp, data_source_id, is_illustrative,
                 price_base, comments, valid_from
ON cost_rates
BEGIN
    SELECT RAISE(ABORT, 'cost_rates rows are never edited: add a new rate and supersede the old one');
END;

CREATE TRIGGER cost_rates_close_once
BEFORE UPDATE OF valid_to ON cost_rates
WHEN OLD.valid_to IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'cost_rates.valid_to is already set and cannot be changed');
END;

CREATE TRIGGER cost_rates_supersede_check
BEFORE UPDATE OF superseded_by ON cost_rates
BEGIN
    SELECT RAISE(ABORT, 'cost_rates.superseded_by is already set and cannot be changed')
    WHERE OLD.superseded_by IS NOT NULL;
    SELECT RAISE(ABORT, 'cost_rates: the replacing rate must be for the same definition and start on or after the old valid_to')
    WHERE NEW.superseded_by IS NOT NULL
      AND NOT EXISTS (
          SELECT 1 FROM cost_rates n
          WHERE n.id = NEW.superseded_by
            AND n.definition_id = OLD.definition_id
            AND NEW.valid_to IS NOT NULL
            AND n.valid_from >= NEW.valid_to);
END;

CREATE TRIGGER cost_rates_no_overlap
BEFORE INSERT ON cost_rates
BEGIN
    SELECT RAISE(ABORT, 'cost_rates: validity period overlaps an existing rate for this definition (close the old rate first)')
    WHERE EXISTS (
        SELECT 1 FROM cost_rates r
        WHERE r.definition_id = NEW.definition_id
          AND r.valid_from < COALESCE(NEW.valid_to, '9999-12-31')
          AND COALESCE(r.valid_to, '9999-12-31') > NEW.valid_from);
END;

-- A definition's unit and ref are fixed once a rate or an item uses them.
CREATE TRIGGER cost_rate_definitions_fixed
BEFORE UPDATE OF id, ref, unit ON cost_rate_definitions
BEGIN
    SELECT RAISE(ABORT, 'cost_rate_definitions: ref and unit cannot be changed; create a new definition');
END;

-- ---------- element_items: the item unit must match its cost rate ------
CREATE TRIGGER element_items_unit_matches_rate_ins
BEFORE INSERT ON element_items
BEGIN
    SELECT RAISE(ABORT, 'element_items: the item unit is different from the unit of its cost rate')
    WHERE NEW.unit <> (SELECT unit FROM cost_rate_definitions WHERE id = NEW.cost_rate_definition_id);
END;

CREATE TRIGGER element_items_unit_matches_rate_upd
BEFORE UPDATE OF unit, cost_rate_definition_id ON element_items
BEGIN
    SELECT RAISE(ABORT, 'element_items: the item unit is different from the unit of its cost rate')
    WHERE NEW.unit <> (SELECT unit FROM cost_rate_definitions WHERE id = NEW.cost_rate_definition_id);
    SELECT RAISE(ABORT, 'element_items: cannot change the unit while the item has components; remove the components first')
    WHERE NEW.unit <> OLD.unit
      AND EXISTS (SELECT 1 FROM item_components WHERE element_item_id = OLD.id);
END;

-- ---------- item_components: the basis must suit the item unit ---------
CREATE TRIGGER item_components_basis_check_ins
BEFORE INSERT ON item_components
BEGIN
    SELECT RAISE(ABORT, 'item_components: thickness_m and area_ratio need an item measured in m2')
    WHERE NEW.basis IN ('thickness_m', 'area_ratio')
      AND (SELECT unit FROM element_items WHERE id = NEW.element_item_id) <> 'm2';
    SELECT RAISE(ABORT, 'item_components: volume_ratio needs an item measured in m3')
    WHERE NEW.basis = 'volume_ratio'
      AND (SELECT unit FROM element_items WHERE id = NEW.element_item_id) <> 'm3';
    SELECT RAISE(ABORT, 'item_components: count needs an item measured in nr')
    WHERE NEW.basis = 'count'
      AND (SELECT unit FROM element_items WHERE id = NEW.element_item_id) <> 'nr';
    SELECT RAISE(ABORT, 'item_components: kg_per_m3 needs a volume, so the item must be in m3 or m2')
    WHERE NEW.basis = 'kg_per_m3'
      AND (SELECT unit FROM element_items WHERE id = NEW.element_item_id) = 'nr';
    SELECT RAISE(ABORT, 'item_components: kg_per_m3 on an m3 item uses the item volume, so host_component_id must be empty')
    WHERE NEW.basis = 'kg_per_m3'
      AND NEW.host_component_id IS NOT NULL
      AND (SELECT unit FROM element_items WHERE id = NEW.element_item_id) = 'm3';
    SELECT RAISE(ABORT, 'item_components: kg_per_m3 on an m2 item needs host_component_id (the thickness_m component it sits inside)')
    WHERE NEW.basis = 'kg_per_m3'
      AND NEW.host_component_id IS NULL
      AND (SELECT unit FROM element_items WHERE id = NEW.element_item_id) = 'm2';
    SELECT RAISE(ABORT, 'item_components: the host must be a thickness_m component of the same item')
    WHERE NEW.host_component_id IS NOT NULL
      AND NOT EXISTS (
          SELECT 1 FROM item_components h
          WHERE h.id = NEW.host_component_id
            AND h.element_item_id = NEW.element_item_id
            AND h.basis = 'thickness_m');
END;

CREATE TRIGGER item_components_basis_check_upd
BEFORE UPDATE OF element_item_id, basis, host_component_id ON item_components
BEGIN
    SELECT RAISE(ABORT, 'item_components: thickness_m and area_ratio need an item measured in m2')
    WHERE NEW.basis IN ('thickness_m', 'area_ratio')
      AND (SELECT unit FROM element_items WHERE id = NEW.element_item_id) <> 'm2';
    SELECT RAISE(ABORT, 'item_components: volume_ratio needs an item measured in m3')
    WHERE NEW.basis = 'volume_ratio'
      AND (SELECT unit FROM element_items WHERE id = NEW.element_item_id) <> 'm3';
    SELECT RAISE(ABORT, 'item_components: count needs an item measured in nr')
    WHERE NEW.basis = 'count'
      AND (SELECT unit FROM element_items WHERE id = NEW.element_item_id) <> 'nr';
    SELECT RAISE(ABORT, 'item_components: kg_per_m3 needs a volume, so the item must be in m3 or m2')
    WHERE NEW.basis = 'kg_per_m3'
      AND (SELECT unit FROM element_items WHERE id = NEW.element_item_id) = 'nr';
    SELECT RAISE(ABORT, 'item_components: kg_per_m3 on an m3 item uses the item volume, so host_component_id must be empty')
    WHERE NEW.basis = 'kg_per_m3'
      AND NEW.host_component_id IS NOT NULL
      AND (SELECT unit FROM element_items WHERE id = NEW.element_item_id) = 'm3';
    SELECT RAISE(ABORT, 'item_components: kg_per_m3 on an m2 item needs host_component_id (the thickness_m component it sits inside)')
    WHERE NEW.basis = 'kg_per_m3'
      AND NEW.host_component_id IS NULL
      AND (SELECT unit FROM element_items WHERE id = NEW.element_item_id) = 'm2';
    SELECT RAISE(ABORT, 'item_components: the host must be a thickness_m component of the same item')
    WHERE NEW.host_component_id IS NOT NULL
      AND NOT EXISTS (
          SELECT 1 FROM item_components h
          WHERE h.id = NEW.host_component_id
            AND h.element_item_id = NEW.element_item_id
            AND h.basis = 'thickness_m');
    SELECT RAISE(ABORT, 'item_components: cannot change the basis of a component that other components use as their host')
    WHERE NEW.basis <> 'thickness_m'
      AND EXISTS (SELECT 1 FROM item_components c WHERE c.host_component_id = OLD.id);
END;

-- ---------- assessments: draft first, then a permanent record ---------
-- Life of an assessment:
--   1. a row is inserted with finalised_at empty (a draft);
--   2. lines are inserted, each one checked by the triggers below;
--   3. finalised_at is set, once. From then on nothing can be added,
--      changed or removed.
-- A draft (for example a run that failed halfway) may be deleted,
-- together with its lines.

CREATE TRIGGER assessments_start_as_draft
BEFORE INSERT ON assessments
WHEN NEW.finalised_at IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'assessments: a new assessment must start with finalised_at empty');
END;

CREATE TRIGGER assessments_no_delete_finalised
BEFORE DELETE ON assessments
WHEN OLD.finalised_at IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'assessments: a finalised assessment is a permanent record and cannot be deleted');
END;

-- these columns decide which factors were valid, so they are fixed from the start
CREATE TRIGGER assessments_fixed_columns
BEFORE UPDATE OF id, project_version_id, as_of_date, run_at, gia_m2 ON assessments
BEGIN
    SELECT RAISE(ABORT, 'assessments: project version, as-of date, run time and GIA cannot be changed after the assessment is created');
END;

CREATE TRIGGER assessments_notes_locked
BEFORE UPDATE OF notes ON assessments
WHEN OLD.finalised_at IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'assessments: a finalised assessment cannot be changed');
END;

CREATE TRIGGER assessments_finalise_once
BEFORE UPDATE OF finalised_at ON assessments
BEGIN
    SELECT RAISE(ABORT, 'assessments: finalised_at is already set and cannot be changed')
    WHERE OLD.finalised_at IS NOT NULL;
    SELECT RAISE(ABORT, 'assessments: cannot finalise an assessment that has no lines')
    WHERE NEW.finalised_at IS NOT NULL
      AND NOT EXISTS (SELECT 1 FROM assessment_lines WHERE assessment_id = OLD.id)
      AND NOT EXISTS (SELECT 1 FROM assessment_cost_lines WHERE assessment_id = OLD.id);
END;

-- ---------- assessment_lines: every line must agree with the data it cites
CREATE TRIGGER assessment_lines_check_insert
BEFORE INSERT ON assessment_lines
BEGIN
    SELECT RAISE(ABORT, 'assessment_lines: the assessment does not exist')
    WHERE NOT EXISTS (SELECT 1 FROM assessments WHERE id = NEW.assessment_id);

    SELECT RAISE(ABORT, 'assessment_lines: this assessment is finalised, no more lines can be added')
    WHERE (SELECT finalised_at FROM assessments WHERE id = NEW.assessment_id) IS NOT NULL;

    SELECT RAISE(ABORT, 'assessment_lines: the carbon factor does not exist')
    WHERE NOT EXISTS (SELECT 1 FROM carbon_factors WHERE id = NEW.carbon_factor_id);

    SELECT RAISE(ABORT, 'assessment_lines: the carbon factor belongs to a different material')
    WHERE (SELECT material_id FROM carbon_factors WHERE id = NEW.carbon_factor_id) <> NEW.material_id;

    SELECT RAISE(ABORT, 'assessment_lines: the carbon factor is for a different lifecycle module')
    WHERE (SELECT lifecycle_module FROM carbon_factors WHERE id = NEW.carbon_factor_id) <> NEW.lifecycle_module;

    SELECT RAISE(ABORT, 'assessment_lines: factor_unit is different from the unit of the carbon factor')
    WHERE (SELECT unit FROM carbon_factors WHERE id = NEW.carbon_factor_id) <> NEW.factor_unit;

    SELECT RAISE(ABORT, 'assessment_lines: the carbon factor was not valid on the as-of date of the assessment')
    WHERE NOT EXISTS (
        SELECT 1
        FROM carbon_factors f
        JOIN assessments a ON a.id = NEW.assessment_id
        WHERE f.id = NEW.carbon_factor_id
          AND f.valid_from <= a.as_of_date
          AND a.as_of_date < COALESCE(f.valid_to, '9999-12-31'));

    -- relative tolerance 1e-9: allows for rounding in the last digits only
    SELECT RAISE(ABORT, 'assessment_lines: carbon_kgco2e is not factor_quantity x the carbon factor value')
    WHERE (SELECT abs(NEW.carbon_kgco2e - NEW.factor_quantity * f.value)
                  > 1e-9 * max(abs(NEW.carbon_kgco2e), abs(NEW.factor_quantity * f.value))
           FROM carbon_factors f WHERE f.id = NEW.carbon_factor_id);

    SELECT RAISE(ABORT, 'assessment_lines: the biogenic carbon row does not exist')
    WHERE NEW.biogenic_carbon_id IS NOT NULL
      AND NOT EXISTS (SELECT 1 FROM biogenic_carbon WHERE id = NEW.biogenic_carbon_id);

    SELECT RAISE(ABORT, 'assessment_lines: the biogenic carbon row belongs to a different material')
    WHERE NEW.biogenic_carbon_id IS NOT NULL
      AND (SELECT material_id FROM biogenic_carbon WHERE id = NEW.biogenic_carbon_id) <> NEW.material_id;

    SELECT RAISE(ABORT, 'assessment_lines: the biogenic carbon row was not valid on the as-of date of the assessment')
    WHERE NEW.biogenic_carbon_id IS NOT NULL
      AND NOT EXISTS (
        SELECT 1
        FROM biogenic_carbon b
        JOIN assessments a ON a.id = NEW.assessment_id
        WHERE b.id = NEW.biogenic_carbon_id
          AND b.valid_from <= a.as_of_date
          AND a.as_of_date < COALESCE(b.valid_to, '9999-12-31'));

    SELECT RAISE(ABORT, 'assessment_lines: biogenic_kgco2e is not mass_kg x the biogenic carbon value')
    WHERE NEW.biogenic_carbon_id IS NOT NULL
      AND (SELECT abs(NEW.biogenic_kgco2e - NEW.mass_kg * b.value_kgco2e_per_kg)
                  > 1e-9 * max(abs(NEW.biogenic_kgco2e), abs(NEW.mass_kg * b.value_kgco2e_per_kg))
           FROM biogenic_carbon b WHERE b.id = NEW.biogenic_carbon_id);
END;

CREATE TRIGGER assessment_lines_no_delete_finalised
BEFORE DELETE ON assessment_lines
WHEN (SELECT finalised_at FROM assessments WHERE id = OLD.assessment_id) IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'assessment_lines: lines of a finalised assessment cannot be deleted');
END;

-- A line is never edited, draft or not (a wrong draft line is deleted and
-- written again, so it passes the insert checks again).
-- element_item_id and item_component_id are left out of this list on
-- purpose: the database itself blanks them (ON DELETE SET NULL) when a
-- project row is removed.
CREATE TRIGGER assessment_lines_immutable
BEFORE UPDATE OF id, assessment_id, element_id, item_description, item_quantity,
                 item_unit, material_id, basis, basis_value, volume_m3,
                 density_kg_m3_used, mass_per_item_kg_used, mass_kg,
                 factor_quantity, factor_unit, lifecycle_module,
                 carbon_factor_id, carbon_kgco2e, biogenic_carbon_id, biogenic_kgco2e
ON assessment_lines
BEGIN
    SELECT RAISE(ABORT, 'assessment_lines cannot be edited');
END;

-- ---------- assessment_cost_lines: the same idea for cost ---------------
CREATE TRIGGER assessment_cost_lines_check_insert
BEFORE INSERT ON assessment_cost_lines
BEGIN
    SELECT RAISE(ABORT, 'assessment_cost_lines: the assessment does not exist')
    WHERE NOT EXISTS (SELECT 1 FROM assessments WHERE id = NEW.assessment_id);

    SELECT RAISE(ABORT, 'assessment_cost_lines: this assessment is finalised, no more lines can be added')
    WHERE (SELECT finalised_at FROM assessments WHERE id = NEW.assessment_id) IS NOT NULL;

    SELECT RAISE(ABORT, 'assessment_cost_lines: the cost rate does not exist')
    WHERE NOT EXISTS (SELECT 1 FROM cost_rates WHERE id = NEW.cost_rate_id);

    SELECT RAISE(ABORT, 'assessment_cost_lines: item_unit is different from the unit the cost rate is priced in')
    WHERE (SELECT d.unit
           FROM cost_rates r JOIN cost_rate_definitions d ON d.id = r.definition_id
           WHERE r.id = NEW.cost_rate_id) <> NEW.item_unit;

    SELECT RAISE(ABORT, 'assessment_cost_lines: the cost rate was not valid on the as-of date of the assessment')
    WHERE NOT EXISTS (
        SELECT 1
        FROM cost_rates r
        JOIN assessments a ON a.id = NEW.assessment_id
        WHERE r.id = NEW.cost_rate_id
          AND r.valid_from <= a.as_of_date
          AND a.as_of_date < COALESCE(r.valid_to, '9999-12-31'));

    SELECT RAISE(ABORT, 'assessment_cost_lines: cost_gbp is not item_quantity x the cost rate')
    WHERE (SELECT abs(NEW.cost_gbp - NEW.item_quantity * r.rate_gbp)
                  > 1e-9 * max(abs(NEW.cost_gbp), abs(NEW.item_quantity * r.rate_gbp))
           FROM cost_rates r WHERE r.id = NEW.cost_rate_id);
END;

CREATE TRIGGER assessment_cost_lines_no_delete_finalised
BEFORE DELETE ON assessment_cost_lines
WHEN (SELECT finalised_at FROM assessments WHERE id = OLD.assessment_id) IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'assessment_cost_lines: lines of a finalised assessment cannot be deleted');
END;

CREATE TRIGGER assessment_cost_lines_immutable
BEFORE UPDATE OF id, assessment_id, element_id, item_description, item_quantity,
                 item_unit, cost_rate_id, cost_gbp
ON assessment_cost_lines
BEGIN
    SELECT RAISE(ABORT, 'assessment_cost_lines cannot be edited');
END;
