# Embodied carbon alongside the cost plan: a prototype and what it shows

*Yarushah Emmanuel Sardar · October 2026 · personal learning project*

## Summary

A cost plan and an embodied carbon assessment start from the same list of
quantities, but they are usually produced separately and at different
times, often after the design is fixed. This project is a small working
tool that produces both from one list, so that a design option can be
judged on cost and carbon together.

Applied to an illustrative 3,000 m2 office (product stage A1-A3, structure
and envelope only), it shows:

- **Concrete floors, roof and foundations dominate.** In the concrete
  option they are 83% of the embodied carbon; the frame itself is 5%.
- **Two material choices with no design change cut carbon by 35%**:
  50% GGBS cement replacement and electric arc furnace (EAF) rebar. That is
  about three quarters of the saving from switching to a timber frame.
- **A timber frame cuts carbon by 47%** at an illustrative 10% higher cost.
  Its remaining hotspot is the substructure (45%); using GGBS and EAF rebar
  there as well brings the total saving to 56%.
- **Data choices matter as much as design choices.** Which rebar figure is
  used moves the concrete option by 17%. The uncertain PIR insulation
  density, in contrast, moves it by about 1%.

## 1. The problem

Most embodied carbon is locked in by early design decisions, when the
structure and main materials are chosen. A quantity surveyor's cost plan
exists at that stage; a carbon assessment often does not. If both came from
the same quantities, a designer could see what each option costs in money
and in carbon before deciding.

## 2. What was built

A Python application (SQLite database, Flask web interface, 194 automated
tests) that:

1. **loads embodied carbon factors** from the ICE Database Educational V5.0
   (Circular Ecology Ltd), a report-style spreadsheet, with every row either
   loaded or rejected with a stated reason;
2. **imports a project** as CSV files laid out like a cost plan: items by
   NRM1 element, each made of one or more materials (for example a slab of
   concrete plus rebar inside it);
3. **calculates** carbon per material and cost per item, using only the
   factors and rates valid on a chosen date, and **saves each run as a
   permanent record** of exactly which factor and rate rows it used;
4. **shows** cost and carbon side by side by element, compares design
   options, tries "what if" material swaps without saving, draws charts,
   and exports a flat CSV for Power BI.

Three design decisions carry most of the weight:

- **Nothing is overwritten.** Factors and rates are replaced with a new,
  dated row; the old row stays. A result from last year can always be
  re-derived from the rows it cites, and the database itself refuses an
  assessment line that cites the wrong material's factor, a factor not
  valid on the assessment date, or a carbon figure that does not equal
  quantity x factor.
- **Nothing is converted to fit.** A per-m2 factor is never turned into a
  per-kg one, and a volume never becomes a mass without a sourced density.
  If a line cannot be calculated honestly, the run stops and names the
  item.
- **Biogenic carbon is shown, never subtracted.** Carbon stored in timber is
  reported next to the A1-A3 total. Subtracting it without the end-of-life
  release (module C, not modelled here) would flatter timber in exactly the
  comparison the tool exists to make.

## 3. The data

**Carbon factors.** The ICE summary sheet has no machine-readable table.
It is 64 small tables in 14 different layouts, with units given only in
header text (per kg, per tonne, per m2, per item) and heading levels shown
only by cell formatting. The parser reads the layout of each table rather
than fixed row numbers. Of 829 rows: 502 factors were loaded; 228 rows were
rejected (108 blank or withdrawn, 44 repeats of another row, 23 in withdrawn
sections, 22 text instead of numbers, 21 with no stated unit, 9 in an
interactive calculator block, 1 contradictory label). Timber appears twice
(with and without carbon storage); the 20 pairs were checked against each
other and stored as one material plus a separate biogenic figure. One ICE
comment matters for interpretation: the EAF rebar figure "should not be
taken as representative of European rebar", so it is used here as an
*EAF-route* alternative, not as "UK rebar".

**Licence.** ICE Educational is for educational use only and may not be
redistributed. The repository therefore contains no ICE file, no extract
and no factor values; users load their own copy. The database records
whether each source may be redistributed, and every export row carries that
flag.

**Densities** (to turn volumes into masses) come from standards and
manufacturers' data sheets, each cited: concrete from BS EN 1991-1-1,
glulam from BS EN 14080, CLT, plasterboard, mineral wool and PIR from
manufacturer data. None comes from the ICE file.

**Cost rates are illustrative**: invented ballpark figures, flagged as such
in the database and on every screen. They price finished work (a m2 of
slab), as a cost plan does, not a kg of material.

## 4. The example and results

An illustrative office: 3 storeys, 40 x 25 m footprint, 3,000 m2 GIA,
40% glazed facade. Quantities are estimates for a typical building, not
from a real project (full assumptions in `example_assumptions.md`).

| Design option | tCO2e | kgCO2e/m2 | vs A | Cost (illustrative) | £/m2 |
|---|---:|---:|---:|---:|---:|
| A - concrete flat slab | 713 | 238 | | £1,574,080 | 525 |
| A2 - 50% GGBS concrete | 585 | 195 | -18% | £1,574,080 | 525 |
| A3 - EAF-route rebar | 594 | 198 | -17% | £1,574,080 | 525 |
| B - glulam and CLT | 380 | 127 | -47% | £1,733,080 | 578 |

Option B also stores 504 tCO2e of biogenic carbon in its timber, reported
separately and not subtracted.

Where the carbon sits (tCO2e):

| NRM1 element | A - concrete | B - timber |
|---|---:|---:|
| 1.1 Substructure | 171 (24%) | 171 (45%) |
| 2.1 Frame | 34 (5%) | 11 (3%) |
| 2.2 Upper floors | 281 (39%) | 75 (20%) |
| 2.3 Roof | 144 (20%) | 40 (11%) |
| 2.5 External walls | 50 (7%) | 50 (13%) |
| 2.6 Windows | 18 (2%) | 18 (5%) |
| 2.7 Partitions | 15 (2%) | 15 (4%) |

The main lines were checked by hand. For example, option A's upper floors:
2,000 m2 x 0.275 m = 550 m3 of concrete, x 2,400 kg/m3 = 1,320 t, giving
177 tCO2e; plus 60.5 t of rebar giving 104 tCO2e; 281 tCO2e in total, as
the tool reports.

## 5. Sensitivity: which choices move the answer

Each line changes one thing and recalculates (`python -m carbon_cost.sensitivity`).

| Scenario | tCO2e | kgCO2e/m2 | vs A |
|---|---:|---:|---:|
| A - concrete (base) | 713 | 238 | |
| A + 50% GGBS in all concrete | 585 | 195 | -18% |
| A + EAF-route rebar | 594 | 198 | -17% |
| **A + GGBS + EAF rebar** | **466** | **155** | **-35%** |
| B - timber (base) | 380 | 127 | -47% |
| **B + GGBS and EAF rebar in the substructure** | **315** | **105** | **-56%** |
| A with PIR at 30 / 60 kg/m3 (base 45) | 706 / 720 | 235 / 240 | -1% / +1% |
| B with PIR at 30 / 60 kg/m3 (base 45) | 373 / 387 | 124 / 129 | -48% / -46% |

## 6. What this suggests for design decisions

1. **Look at the slabs first.** In a concrete-framed office the floor and
   roof slabs and the foundations are where the carbon is. Effort spent on
   the frame or the envelope moves the total much less.
2. **Specification is a cheap lever.** GGBS concrete and EAF rebar change
   no drawings, and together they deliver most of what a change of frame
   delivers. Whether they change cost depends on the market; the
   illustrative rates here were not adjusted, so this prototype cannot say.
3. **After a frame change, the hotspot moves.** In the timber option, the
   foundations become the largest element, so the next decision is about
   the substructure.
4. **Record the data choice, not just the result.** The same building
   differs by 17% depending on which rebar figure is used. A result that
   does not say which factors it used cannot be checked or compared. That
   is why each assessment here stores its exact factor rows.

## 7. Limitations

- **Scope**: product stage (A1-A3) only, for structure, envelope and
  partitions. No transport (A4), construction (A5), use (B), or end of
  life (C). No services, finishes, stairs or external works. The totals are
  therefore lower than a whole-life assessment and must not be compared
  with whole-life benchmarks such as RIBA or LETI targets.
- **Generic factors**: ICE averages, not product-specific Environmental
  Product Declarations (EPDs).
- **No wastage** allowance; window frames, partition studs and roof
  membranes are excluded.
- **Illustrative quantities and cost rates**; not a real project or price
  book.
- **One current factor per material**: two sources for the same material
  are modelled as two materials.

## 8. What a real tool would need

- a commercially licensed carbon dataset (ICE's commercial successor is
  IC+) and links to product EPDs;
- project-specific data for transport and construction (A4-A5), and the
  later life-cycle stages, reported in the RICS Whole Life Carbon
  Assessment structure;
- real cost data, and cost rates that respond to specification changes;
- import from the cost-planning tools quantity surveyors already use, and
  user accounts with an audit trail for project edits.

## Sources

- ICE Database Educational V5.0 (June 2026), Circular Ecology Ltd,
  <https://circularecology.com/embodied-carbon-footprint-database.html>.
  Licensed for educational use; not redistributed here.
- BS EN 1991-1-1 (densities of construction materials); BS EN 14080 (glulam);
  manufacturers' data sheets as cited in `data/reference/densities.csv`.
- RICS New Rules of Measurement (NRM1) for the element structure.
