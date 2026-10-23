# Buy Clean materials

The evidence behind the `MATERIALS` map in `scripts/build_lead_list.py`.
The map decides two things: which declarations get the +3 California
rule, and which get the +2 other-states rule. Both are judgements about
what a state programme actually covers, so the evidence is recorded here
rather than left in the code.

## California

The Buy Clean Act (Public Resources Code 3500-3505, amended by SB 1207
in 2024) sets maximum global warming potential values for the materials
Caltrans buys, and the Buy Clean California Act Materials Program
(BCCMP) requires a **facility-specific** Type III EPD to demonstrate
compliance.

The four products with EPD requirements are:

| Product | Where the EPD is |
|---|---|
| Structural steel (plate, hollow structural sections, hot-rolled sections) | Caltrans Authorized Materials List |
| Carbon steel rebar | Caltrans Authorized Materials List |
| Flat glass | not on the list; Caltrans treats it as informational and the EPD goes to the Engineer |
| Mineral wool board insulation | not on the list |

The Caltrans EPD list at
<https://mets.dot.ca.gov/aml/EnvironmentalProductDeclarations.php> is
the state's own list of the declarations it accepts. 59 rows in two
material categories, 4 of them superseded. It carries the facility
address, the declaration number and the exact validity range.

Asphalt and concrete are in the Act's GWP limits but not in the EPD
programme, so they score for other states and not for California.

## Other states

The State Buy Clean Partnership programmes and what each covers, from
the programme pages themselves:

| State | Materials |
|---|---|
| Colorado | Asphalt, concrete, glass, rebar, structural steel, prestressed, wood structural |
| Illinois | Concrete, steel, asphalt |
| Maryland | Concrete, steel, asphalt |
| Massachusetts | Concrete, steel, glass, asphalt, wood |
| Minnesota | Tier 1: concrete, asphalt, structural steel, carbon steel rebar. Tier 2: glass, insulation, aluminium, wood. Tier 3: wood, gypsum, membranes, sealants, plastics |
| New Jersey | Concrete, steel, asphalt |
| New York | Concrete limits, plus disclosure for steel, flat glass and asphalt |
| Oregon | Concrete, steel |
| Washington | Concrete, rebar and prestressed, structural steel, engineered wood |
| Wisconsin | Concrete, steel, asphalt |

Federal programmes were largely rescinded in 2025, so they are not in
the table.

## The material map, and how the code derives a material

`MATERIALS` is keyed by the material the code derives from the
registry's own product category, not by a free-text guess.
`CATEGORY_MATERIAL` maps the published category names to a material:

| Registry category | Material |
|---|---|
| carbon steel rebar | rebar |
| structural steel: plate, hollow structural sections, hot-rolled sections | structural steel |
| thermal insulation, spf insulation, continuous wall insulation, roof insulation | insulation |
| mineral wool, mineral wool insulation, mineral wool board, rockwool | mineral wool |
| concrete, articulating concrete block, interlocking concrete pavers | concrete |
| precast concrete | precast |
| asphalt shingles, asphalt membranes | asphalt |
| steel brackets | steel |
| gypsum, gypsum board products, gypsum panel products, gypsum roof board | gypsum |
| interior, exterior, powder, roof, concrete coatings | coatings |
| single ply roofing | roofing |
| water-resistive and air barriers | barrier |
| resinous flooring | flooring |
| the rest | other |

The insulation split is the one that matters. The Act's insulation
product is mineral wool **board**, and no registry we can read carries a
category for it. The insulation categories we can read are thermal, spray
foam, continuous wall and roof insulation, which are not mineral wool
board, so they score for Minnesota and Texas but not for California.
Mapping every insulation EPD to California would pitch a roof-insulation
renewal as a Buy Clean problem.

## The limits

- **Scope.** The +3 needs a facility-specific EPD. The state listing is
  what makes the Caltrans rows facility-specific, and an unknown scope
  scores 0 rather than +3.
- **The author.** Caltrans carries no author field, so the developer on
  those rows is `unknown`. That is the data, not a parse failure.
- **Facility, not company.** An EPD is accepted for a facility. The
  company is scored once, after the mill name is split off the company
  name, and "Nucor-Yamato Steel" stays separate from "Nucor Steel".
- **The gap.** Flat glass and mineral wool board insulation are Buy
  Clean products no readable registry carries. They live in UL, SCS,
  Smart EPD and EC3. See `sources.md`.
