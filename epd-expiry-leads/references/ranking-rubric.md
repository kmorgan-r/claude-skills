# Ranking rubric

Applied once per manufacturer, not once per EPD. A rule fires for the
company however many of its EPDs are in the window, because the pitch is
to the company.

The rubric comes from the original request. Where this implementation
departs from a literal reading, the reason is given.

## +3 California Buy Clean material with a facility-specific EPD

California's Buy Clean Act sets maximum global warming potential for
asphalt, concrete, steel, insulation and flat glass, and the rules require
a facility-specific Type III EPD to demonstrate compliance. A
manufacturer whose EPD is about to lapse is a manufacturer whose ability
to bid California work is about to lapse with it.

The +3 only fires when the EPD is facility-specific. California rejects
industry-wide EPDs and multi-plant averages, so an EPD of unknown scope
cannot be assumed to qualify, and an unknown scope scores 0 rather than
+3.

**The gap.** NSF's registry carries no flat glass, rebar or structural
steel product category. Its only California material is thermal
insulation, and those EPDs expire 2028 to 2031, not in the current
window. So in the 2026-11 to 2027-10 window this rule fires for nothing,
and the "California watch" sheet in the workbook lists every California
material EPD in the registry with its expiry date.

The reason is terms, not effort. Flat glass, rebar and structural steel
EPDs live in the registries we cannot read programmatically. See
`sources.md`.

## +2 Material covered by another state's Buy Clean programme

The same argument applies with less force outside California. If a
material is on another state's authorised list, an expiring EPD costs
that manufacturer work in that state too.

States and the materials they cover, from the programme pages:

| State | Materials |
|---|---|
| Colorado | Asphalt, concrete, glass, rebar, structural steel, prestressed, wood structural |
| Massachusetts | Concrete, steel, glass, asphalt, wood |
| Maryland | Concrete, steel, asphalt |
| Minnesota | Concrete, asphalt, structural steel, carbon steel rebar; tier 2 glass, insulation, aluminium, wood; tier 3 wood, gypsum, membranes, sealants, plastics |
| New Jersey | Concrete, steel, asphalt |
| New York | Concrete limits, plus disclosure for steel, flat glass and asphalt |
| Oregon | Concrete, steel |
| Washington | Concrete, rebar and prestressed, structural steel, engineered wood |
| Wisconsin | Concrete, steel, asphalt |
| Illinois | Concrete, steel, asphalt |

Federal programmes were largely rescinded in 2025, so they are not in the
table.

The `MATERIALS` map in `scripts/build_lead_list.py` carries the same
list, keyed by the material the code derives from NSF's own product
category. The map is a judgement about which states matter for a given
material, not a legal opinion, and it is worth revising when a state
changes its limits.

## +2 Five or more EPDs expiring

A company with five EPDs expiring has a programme to run, not a form to
fill. It also usually has a team, which is what makes the pitch worth
making.

The count excludes industry-wide EPDs, because an industry-wide EPD is
not the company's to renew.

## +2 Prepared in-house

An EPD prepared by the manufacturer's own staff means there is an LCA
team to pitch. If the declaration was written by a consultancy, the
consultancy is the one ClimatePoint would be competing with for the
renewal, and the manufacturer is not the decision maker.

**In-house means the preparer's email domain is the manufacturer's own
domain.** It is not enough for the preparer to be a first-party name on
the PDF. Master Builders Solutions writes the concrete EPDs for several
registrants in this list while selling them admixtures, so a "David Green
at Master Builders Solutions" preparer is a supplier, not the customer.
This is why the code compares domains rather than names.

## -2 Developed by a known consultant

Consultancies write a large share of EPDs: EcoForm, Industrial Ecology
Consultants, Athena Sustainable Materials Institute, WAP Sustainability
Consulting, Sphera, Ramboll, Sustainable Solutions Corporation, Intertek
and others. They usually manage the renewal, which means the
manufacturer has less reason to change supplier.

The list is in `CONSULTANTS` and `CONSULTANT_ORGS` in the script. It
covers both email domains and organisation names, because the PDFs name
the consultancy in the text more often than in an address.

## -2 Written by a third party that also supplies the manufacturer

A distinct case from a consultancy, and a worse one. When the author
sells the manufacturer admixtures or other inputs, the EPD supports the
supplier's product as much as the customer's, and the renewal runs
through the supplier. Master Builders Solutions is the clearest example
in this list.

This rule is an extension of the original rubric, which named only
consultants and automated tools. It earns its place because the original
rubric's intent is "do not pitch a manufacturer whose EPD is not really
theirs to renew", and a supplier-author is exactly that case.

## -2 Developed by an automated tool

An EPD produced by a tool with no human author has nobody to pitch.

**In practice this rarely fires from NSF**, because the PDFs that name an
automated tool also name a consultant who used it. One Click LCA is
listed as the software for the Oldcastle and Legacy Precast EPDs, and
EcoForm or Master Builders Solutions as the preparer, so the consultant
rule fires instead. The rule is kept because it is in the original
rubric and because the tool is visible in the PDFs.

## Excluded: industry-wide EPDs

An industry-wide EPD is issued by a trade association and is not the
manufacturer's to renew. Industry-wide EPDs are dropped from the count
and from the score, and the `reasons` column says how many were excluded.

## What the score does not capture

- **Whether the EPD is already superseded.** A listing can point at a
  document the manufacturer has already replaced. Check for a newer EPD
  from the same company before contacting anyone.
- **Whether the renewal is a formality.** Some renewals are light
  extensions of a light model.
- **Listing staleness.** The listing is the programme operator's record
  and can lag reality.
- **Canadian and non-US registrants.** NSF lists them. The `us_based`
  column separates them, and CASL applies to the Canadian ones.
