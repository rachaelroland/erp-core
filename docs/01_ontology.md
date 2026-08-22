# 01 — Ontology

*The upper model: nine concepts, the relations between them, and the six axes that generate
most of the design.*

Provenance note: the **duality of economic events** below is a deliberate alignment with REA
(McCarthy, 1982 — an accounting research model, public scholarship, not a product). The
equipment hierarchy vocabulary aligns with **ISA-95 / IEC 62264**. Both are used as vocabulary,
not as implementations. Everything else here is designed from the domain.

---

## Why an upper model at all

The failure mode of ERP data models is *proliferation by document type*: a table for quotes, a
near-identical one for sales orders, another for purchase orders, another for blanket releases,
another for forecasts — each with its own status enum, its own line table, its own fulfilment
logic. Forty document types, forty special cases, and an agent has to learn all forty.

The claim of this ontology is that a manufacturing business has **nine kinds of thing**, and every
document type is a projection of a handful of them. Learn nine, generate forty. That is the
whole reason to do ontology work before schema work.

---

## The nine concepts

### 1. Party — anything that can act or be transacted with

Subtypes: `organization`, `person`, `equipment_actor` (a machine that emits events),
`software_agent` (an AI agent or an integration).

**Load-bearing decision: AI agents are Parties.** Not a flag on a user, not a service account
borrowing someone's credentials. An agent has its own identity, its own authority grants, and its
own place in segregation-of-duties rules. If `agent:ap_matcher` created a vendor record, it must
not also be able to release payment to it — the same control we apply to people, applied for the
same reason.

A Party holds **roles** (customer, supplier, employee, carrier, subcontractor, regulator) rather
than being typed by them, because the same organization is routinely three of those at once.

### 2. Resource — anything scarce that gets produced, consumed, or used

Subtypes: `material`, `equipment`, `labor`, `energy`, `information`, `money`.

Unifying these is what makes costing and capacity fall out of one mechanism instead of four.
A press-hour, a kilogram of 304 stainless, a machinist-hour, and a dollar are all resources whose
flow is tracked the same way; they differ in their units, their divisibility, and whether they are
*consumed* (material) or *used and returned* (equipment, labor).

That last distinction matters and is worth naming explicitly:

- **Consumable resources** decrement permanently. Steel becomes a bracket.
- **Usable resources** are occupied for an interval and released. A CNC cell is not consumed by
  a job; it is *reserved* for 4.2 hours. Capacity is the integral of usable-resource availability.

### 3. Specification — the type-level description of a resource or a process

`item_definition`, `structure_definition` (bill of material), `process_definition` (routing),
`quality_specification`, `configuration_rule`.

### 4. Occurrence — the instance-level embodiment of a specification

`lot`, `serial_unit`, `inventory_quantum`, `equipment_unit`, `process_execution`.

**The type/instance split is the single most important modeling axis in manufacturing**, and it
is the one most often blurred. A BOM is a specification; the material actually consumed by job
J-1183 is a set of occurrences that may or may not match it. A routing says the part visits
OP-20 for 12 minutes; the process execution says it took 31 minutes on a substitute machine
because the primary was down.

Blur this and you lose the ability to ask the only question that matters for continuous
improvement: **where does actual diverge from planned, and why?** Keep it clean and variance
analysis is a `JOIN`, not a project.

### 5. Commitment — a promise about future resource flow

A commitment is: *a party, a specification (or an occurrence), a quantity, a time window, a price
or cost basis, and a direction (in or out).*

That shape covers a quote line, a sales-order line, a purchase-order line, a blanket release, a
forecast bucket, a work-order demand line, a subcontract instruction, and a capacity reservation.
They differ in **firmness** (speculative → offered → firm → released) and in whether they bind
the counterparty. They do not differ in structure.

**What this buys an agent:** one shape to reason over. "What is committed out of this item over
the next 30 days" is one query across all demand types, rather than a union of six tables that
someone forgot to update when a seventh was added.

### 6. Flow — an actual increment or decrement of a resource, attributed to a party, at a time

The atom of the ledger. Every physical and financial fact reduces to signed resource flows.

**The duality rule (REA-aligned):** flows come in balanced pairs. A material issue is a decrement
at the stockroom paired with an increment at the job. A shipment is a decrement of finished goods
paired with an increment of a receivable. A production report is a set of decrements (components,
labor, machine time) paired with an increment (the produced quantity, carrying the summed cost).

**Why this is worth the abstraction:** cost accounting stops being a separate subsystem. Cost
rides the flow. Because every increment names the decrements it came from, you get actual costing,
lot genealogy, and "which customer shipments contain material from vendor lot 88-C" from the same
structure — which is exactly what you need at 2am when a recall lands.

### 7. Assertion — a claim about any of the above, with an author and a confidence

Subject (a URI), predicate (an ontology term), value, actor, method, confidence, evidence,
validity window, superseded-by.

This is Tier B from Pillar 2, and it is where agents do most of their writing. An assertion is
explicitly *not* a fact: it is someone's claim, held with a degree of belief, traceable to what it
was based on. Multiple conflicting assertions can coexist; a resolution policy chooses the current
belief, and the losers stay visible.

### 8. Evidence — an immutable observation that assertions point at

Documents, document pages, email messages, scans, sensor samples, photographs, transcripts.
Content-addressed by hash. Never edited, only superseded.

Evidence is the anchor for the only defensible answer to "why does the system think that": *this
region of this page of this PDF, received from this address at this time.*

### 9. Policy — a rule that constrains who may do what, when, and within what tolerance

Authority grants, approval thresholds, segregation-of-duties constraints, tolerance bands
(price variance, receipt over-delivery, scrap allowance), calendars, and shift patterns.

Policy is data, not code, for the same reason the ontology is: it has to be inspectable,
versioned, and simulatable. "What would this agent have been allowed to do last quarter" is a
question you will be asked.

---

## The relations (the verbs of the ontology)

| Relation | From → To | Meaning |
|---|---|---|
| `realizes` | Occurrence → Specification | this lot is an instance of that item definition |
| `specifies` | Specification → Specification | BOM component, routing step, alternate |
| `fulfills` | Flow → Commitment | this receipt satisfies that PO line |
| `reserves` | Commitment → Occurrence | this order line is allocated to that lot |
| `consumes` / `produces` | Flow → Resource | signed direction of the flow |
| `derives_from` | Occurrence → Occurrence | lot genealogy; the produced lot came from these input lots |
| `pairs_with` | Flow → Flow | the duality link; every decrement has its increment |
| `compensates` | Flow → Flow | reversal — never a delete |
| `causes` | Event → Event | causation chain, for explanation and replay |
| `asserts` | Party → Assertion | who claimed it |
| `evidences` | Evidence → Assertion | what it was based on |
| `governs` | Policy → anything | which rule applied |
| `succeeds` | Revision → Revision | engineering change; effectivity-dated |
| `substitutes` | Specification ↔ Specification | approved alternate material or process |

An agent that knows these fourteen verbs and nine nouns can navigate the whole system. That is
the design target: **a small, learnable, closed vocabulary** — because a vocabulary that fits in
context is a vocabulary the agent gets right.

---

## The six taxonomy axes

Most modeling arguments in this domain resolve to one of six orthogonal axes. Keeping them
orthogonal — rather than collapsing them into one status enum, which is the standard mistake —
is most of the schema design.

| # | Axis | Poles | Why it must stay separate |
|---|---|---|---|
| 1 | **Type ↔ Instance** | specification / occurrence | Plan-vs-actual variance is impossible without it |
| 2 | **Plan ↔ Actual** | commitment / flow | Backlog, capacity, and MRP are all commitment-side; costing is flow-side |
| 3 | **Physical ↔ Informational** | material movement / document state | A PO can be "approved" while nothing has physically moved. Conflating these is why ERP status fields lie |
| 4 | **Believed ↔ Verified** | asserted / confirmed | Pillar 8. Confidence must be a property of the record, not of the reader's cynicism |
| 5 | **Valid time ↔ Transaction time** | when true / when known | Backdating, retroactive ECOs, period close |
| 6 | **Aggregate ↔ Quantum** | 400 units / this lot in this bin in this condition owned by this party | Traceability, consignment, and quality holds all need the quantum |

### Axis 6 deserves an expansion, because it is where most ERPs go wrong

"On hand" is not one number with one status. It is a set of **quanta**, each of which is the
intersection of *five independent dimensions*:

- **identity** — item, revision, lot, serial
- **place** — site → area → location → bin
- **condition** — usable, in-inspection, quarantined, nonconforming, reworkable, obsolete
- **ownership** — ours, customer-supplied, vendor-consigned, in-transit-to-us (title passed?)
- **encumbrance** — free, allocated to a commitment, hard-reserved to a lot, staged

Conventional systems collapse condition/ownership/encumbrance into a single `status` column and
then discover they cannot represent "customer-supplied material, quarantined pending our
inspection, already allocated to their order." Keeping the dimensions independent costs a wider
table and buys correctness in exactly the cases that generate disputes.

---

## Manufacturing strategy: what the ontology must *not* hard-code

Discrete SMB manufacturers are rarely one thing. The same shop runs:

- **make-to-stock** — produce to forecast, ship from inventory
- **make-to-order** — produce against a firm order, standard design
- **configure-to-order** — standard modules, rules-driven combination
- **engineer-to-order** — design work is part of the job; the BOM is created *during* the job
- **repair/refurb (MRO)** — the incoming article is customer property with unknown condition

These differ in *when the specification comes into existence relative to the commitment*, which is
the only thing the schema needs to accommodate:

| Strategy | Specification exists… | Consequence for the model |
|---|---|---|
| MTS | long before the commitment | commitments reference a stable `item_definition` |
| MTO | before, but demand-triggered | same, plus commitment→job linkage |
| CTO | as rules; the instance spec is generated at order time | need a *derived* specification bound to a commitment line |
| ETO | after the commitment; evolves during the job | specification must be creatable mid-job and versioned against actuals |
| MRO | never fully; discovered by inspection | the "BOM" is an assertion set built from evidence |

ETO and MRO are the ones that break rigid schemas — and they are common in the job-shop segment.
The design answer: **a specification may be owned by a commitment** (a one-off BOM belonging to
job J-1183), and specifications are versioned with effectivity, so a spec that changes three times
mid-job still costs correctly. This is also where the assertion tier earns its keep: on an MRO
teardown, "this unit contains a worn part 88-C" starts life as an inspector's assertion with a
photo behind it, and gets promoted to a demand commitment once approved.

---

## Worked trace: one purchase, end to end, in ontology terms

To show the vocabulary closing:

1. A **Party** (supplier) is on a **Commitment** (PO line, direction=in, firm) for an
   **Item Definition** (Specification), 500 kg, window Aug 12–14, at $4.10/kg.
2. A truck arrives. A scan produces **Evidence** (packing slip image, weight-ticket reading).
3. An agent reads the evidence and writes **Assertions**: `po_number = 4471` (conf 0.98),
   `quantity = 502 kg` (conf 0.93), `vendor_lot = 88-C` (conf 0.88).
4. The agent emits a **Proposal**: `receive_against(commitment_line, qty=502, lot=88-C)`.
   Over-delivery is 0.4%, inside the tolerance **Policy**; blast radius is low; the command's
   reversibility class is compensable. Policy auto-commits.
5. Commit produces paired **Flows**: decrement supplier-owed, increment
   `inventory_quantum(item, lot 88-C, location RCV-01, condition=in-inspection, owner=ours,
   encumbrance=free)`. The flow `fulfills` the commitment; the quantum `realizes` the item def.
6. Inspection produces another **Occurrence** and a condition change to `usable`. Failure would
   instead produce a nonconformance and a `derives_from` split of the lot.
7. Months later, a customer complaint traces backwards: shipment → produced lot →
   `derives_from` → lot 88-C → this receipt → this **Evidence** image → this supplier.
   Every hop is a stored relation, not a reconstruction.

Nine nouns, fourteen verbs, no document-type special cases. That is the test the ontology has to
keep passing as scope grows.
