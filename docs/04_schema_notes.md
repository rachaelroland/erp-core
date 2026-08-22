# 04 — How to read the schema

`03_schema.sql` is a design artifact. It has not been run, and running it is not the point.
This note says which parts are load-bearing (the argument would collapse without them) and which
are illustrative (reasonable choices that could be made differently without harm).

---

## The five load-bearing structures

If you change these, you are designing a different system.

**1. `flow_event` + `resource_flow` (§8).** The append-only, hash-chained spine with signed,
paired resource movements. Everything else is derived. This is what makes agent actions
replayable, explainable, and reversible without a restore. Cost rides on `resource_flow`, which
is why costing is not a second system that has to be reconciled with the first.

**2. `inventory_quantum` (§5) with five independent dimensions plus belief.** Condition,
ownership, and encumbrance as separate columns — not one `status` — is what makes
"customer-supplied material, quarantined, already allocated to their order" representable.
`belief_confidence` + `last_verified_at` is Pillar 8, and it is the field that no conventional
ERP has.

**3. `commitment` / `commitment_line` (§6) as one shape for thirteen document kinds.** Collapsing
quotes, orders, POs, forecasts, releases, transfers and reservations into one structure is what
keeps the agent's vocabulary small enough to be reliable.

**4. `assertion` + `evidence` + `evidence_region` (§10).** The epistemic tier. Claims with
authors, confidences, and citations to a rectangle on a page. Remove this and agents are forced
to write directly into the record of truth, which is the failure this whole architecture exists
to avoid.

**5. `proposal` + `authority_grant` + `policy_rule` (§10, §2).** The intent layer and the dial
that governs it. Without these, "agent-first" means "agent writes to prod."

## What is illustrative

- **Every index.** Indicative only; nothing here has seen a query plan.
- **`numeric` precisions.** `(18,6)` for money and quantity is a considered default, not a
  measured one. Fastener unit costs and per-second machine rates are the cases that drove it.
- **The `location_node` path representation.** Deliberately open (see the note in §4).
- **The `jsonb` escape hatches** (`attributes`, `payload`, `blast_radius`, `predicate`). In a
  real build, each needs a JSON Schema registered in `attribute_definition` and validated on
  write. Untyped jsonb is how a governed schema quietly becomes a document store.
- **Enum-as-`text`-plus-`CHECK`.** Chosen over Postgres `ENUM` types because vocabularies must be
  addable at runtime by tenants (§1) and `ALTER TYPE` is a poor mechanism for that. The `CHECK`
  lists in the DDL duplicate `semantic_term` on purpose, as a readability aid — in a build, the
  registry is authoritative and the checks are generated from it.
- **The views in §8.** They show the derivation. In production they are materialised, with a
  reconciliation job that proves the projection equals the ledger.

## Conventions worth internalising

- **No stored counters.** If you find yourself adding `quantity_completed` to `job`, stop. It is
  a `SUM` over `resource_flow`. Stored counters drift; the drift is silent; the drift is what
  makes people stop trusting the ERP.
- **Sign lives in `direction`, never in the number.** `quantity >= 0` is a `CHECK` for a reason —
  negative quantities in a ledger make every aggregate ambiguous.
- **Corrections are compensating events.** There is no `UPDATE` path and no `DELETE` path.
  `compensates_event_id` plus a mandatory reason code.
- **Agents are parties.** Any table that references an actor takes a `party_id`. There is no
  separate `agent_actions` table, and there should never be one — the moment agent activity lives
  somewhere else, it stops being subject to the same controls.
- **`rationale` is mandatory for agent-authored events.** Enforced in the command layer, not the
  DDL. An unexplained agent action is unreviewable.

## Open questions the schema does not yet answer

Written down so they don't get lost:

1. **Partitioning strategy for `flow_event` / `resource_flow`.** Time-based is obvious; the
   interaction with bitemporal queries (which cross partitions freely) is not.
2. **Belief decay function.** `belief_confidence` needs a concrete, defensible model calibrated
   against real cycle-count variance. Until we have a dataset, it is a column with an intention.
3. **Assertion volume.** High-frequency sensor-derived assertions could dwarf everything else.
   Likely answer: sensor data stays in a time-series store and only *summarised* assertions land
   here — but that boundary is undrawn.
4. **Structure/routing snapshotting granularity.** `job.structure_id` snapshots at release. Does
   an approved mid-job substitution create a new structure version, or an assertion against the
   job? Currently ambiguous.
5. **Multi-site transfer ownership.** `in_transit_title_ours` vs `in_transit_title_theirs` is
   modelled, but which site's balance the in-transit quantum counts toward is not decided.
6. **Unit-of-measure conversion at flow time.** A flow records one uom; aggregating across
   different uoms for the same item needs a defined conversion point. Probably at write, using
   the item's stock uom — but that loses information about how it was actually transacted.
