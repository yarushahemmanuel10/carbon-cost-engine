# Example building: assumptions

The example project (`data/reference/projects/example_office/`) is an
**illustrative** building used to show what the tool does. The quantities
are reasonable estimates for a typical building of this type. They are
**not** taken from a real project, and the cost rates are invented
ballpark figures, not commercial data.

## The building

| | |
|---|---|
| Use | Office, 3 storeys |
| Footprint | 40 m x 25 m = 1,000 m2 |
| Gross internal area (GIA) | 3,000 m2 |
| Storey height | 3.5 m (facade height 10.5 m) |
| Facade | 130 m perimeter x 10.5 m = 1,365 m2, of which 40% glazed (546 m2) and 60% solid wall (819 m2) |
| Quantities | Net, as built. No wastage allowance (see Limits) |

## Design options (project versions)

| Version | What changes |
|---|---|
| **A - concrete frame** | Base case: reinforced concrete flat slab and columns. Concrete C32/40 with average UK cement; rebar = world average |
| **A2 - concrete, 50% GGBS** | As A, but every structural concrete uses 50% GGBS cement replacement |
| **A3 - concrete, EAF rebar** | As A, but rebar is EAF-route (recycled scrap) instead of world average |
| **B - timber frame** | Glulam columns and beams, CLT floors with a concrete topping, CLT roof. Substructure, walls, windows and partitions as A |

## Items

### Option A (A2 and A3 differ only in the concrete or rebar named above)

| NRM1 | Item | Quantity | Materials |
|---|---|---|---|
| 1.1 | Ground-bearing slab | 1,000 m2 | 250 mm concrete; rebar 50 kg per m3 of slab |
| 1.1 | Pad foundations | 150 m3 | 30 pads of 2.5 x 2.5 x 0.8 m; rebar 80 kg/m3 |
| 2.1 | Columns, 400 x 400 mm | 50.4 m3 | 30 columns x 3 storeys x 3.5 m; rebar 200 kg/m3 |
| 2.2 | Upper floors, flat slab | 2,000 m2 | 275 mm concrete; rebar 110 kg/m3 |
| 2.3 | Roof | 1,000 m2 | 250 mm concrete; rebar 100 kg/m3; 150 mm PIR insulation |
| 2.5 | External cavity wall | 819 m2 | facing brick (ICE, per m2 of wall incl. mortar); 150 mm mineral wool; 100 mm medium density block (ICE, per m2 incl. mortar); 12.5 mm plasterboard |
| 2.6 | Windows | 546 m2 | double glazing, 4 + 4 mm glass (ICE, per m2) |
| 2.7 | Partitions | 1,500 m2 | 4 x 12.5 mm plasterboard; 50 mm mineral wool. 0.5 m2 of partition per m2 GIA |

### Option B (items that differ from A)

| NRM1 | Item | Quantity | Materials |
|---|---|---|---|
| 2.1 | Glulam columns and beams | 90 m3 | GL24h, about 0.03 m3 per m2 GIA |
| 2.2 | Upper floors | 2,000 m2 | 200 mm CLT + **50 mm unreinforced concrete topping** (C20/25) |
| 2.3 | Roof | 1,000 m2 | 160 mm CLT + 150 mm PIR |

The pad foundations are kept the same as in option A. A lighter timber
frame could need smaller foundations, so this is conservative (it does
not flatter timber).

The concrete topping on the CLT floors is included on purpose. Real CLT
office floors usually need one for acoustics and mass. Leaving it out
would make the timber option look better than it is.

## Key data choices

| Choice | Value | Why |
|---|---|---|
| Base concrete | ICE "32/40 MPa" (average UK cement mix) | A typical UK structural concrete |
| Base rebar | ICE "Steel, Rebar", worldsteel world average | ICE's general rebar figure |
| Alternative rebar | ICE "Steel rebar, recycled, Europe EAF mid range" (well under half the world-average factor) | Shows how much the production route matters |
| Biogenic carbon | Reported separately, never subtracted | See design note 4.4 |

**About the EAF rebar.** ICE's own comment on this row reads: *"This
entry is an average of 29 datapoints. It should not be taken as
representative of European rebar, the datapoints were all for EAF steel.
Steel rebar is also made in the primary production route in Europe."* So
version A3 shows the effect of choosing **EAF-route** rebar. It is not a
claim about UK or European rebar in general.

## Densities

Every density cites its own source in `data/reference/densities.csv`.
None comes from the ICE file.

| Material | kg/m3 | Source |
|---|---|---|
| Concrete (all mixes) | 2,400 | BS EN 1991-1-1 Annex A (plain concrete; rebar is separate) |
| Glulam GL24h | 420 | BS EN 14080, mean density |
| CLT | 490 | Stora Enso CLT technical brochure |
| Plasterboard | 616 | British Gypsum WallBoard 12.5 mm data sheet (from minimum weight) |
| Mineral wool | 45 | Rockwool RWA45 slab |
| PIR | 45 | Kingspan Therma range: "typically 30-60 kg/m3"; midpoint used |

The PIR value is the midpoint of a range, so the write-up tests 30 and
60 kg/m3 as a sensitivity.

## Cost rates

`data/reference/cost_rates.csv`. All rates are **illustrative**: invented
ballpark figures written for this prototype, not from a price book. Each
rate prices a finished piece of work (for example one m2 of slab,
including formwork and labour), the way a cost plan does. They are
flagged as illustrative in the database and on screen.

## What is left out (limits)

- **Wastage**: no allowance for material that is wasted on site. Real
  assessments add this (reported under A5), so real carbon is higher.
- **Window frames, spacers and seals**: the ICE glazing factor is glass
  only. The cost rate includes frames.
- **Steel studs** in partitions, **roof membranes** and vapour control
  layers, **fixings**.
- **Stairs, lift shafts and cores, building services, finishes and
  fit-out, external works**.
- **Lifecycle stages after A3**: transport to site, construction, use and
  end of life are not included.

So the totals cover the main structure and envelope, product stage
(A1-A3) only. They are lower than a full whole-life carbon assessment of
the same building would be, and must not be compared directly with
whole-life benchmarks.
