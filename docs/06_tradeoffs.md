# 06 — Trade-offs

*Decision records. Each one states what we chose, what it costs, and the condition that would
flip it. A decision with no stated cost is a slogan, and a decision with no flip condition is
dogma.*

---

## ADR-001 — Event-sourced ledger vs. mutable current-state rows

**Decision.** Append-only `flow_event` + `resource_flow` as the record of truth; all balances are
projections. Projections are materialised for read performance, with a reconciliation job that
proves them equal to the ledger.

**Why.** Agents need three things a mutable schema cannot give: replay (to explain a decision),
reversal (compensating events instead of restore-from-backup), and point-in-time truth (for audit
and costing). It also removes the single worst class of ERP bug — the drifting stored counter.

**Cost.** Reads get harder and slower. Storage grows without bound. Every developer must
internalise "never update," and some will not. Projection lag creates a window where the read
model and the ledger disagree, which surfaces as "the screen says 400 but the report says 398."
There is real operational burden in the reconciliation job.

**Mitigation.** Synchronous projection for hot aggregates (quantum balance, commitment
fulfilment) inside the same transaction as the ledger write — accepting the write cost to
eliminate the lag where it would be user-visible. Asynchronous for everything analytical.

**What would flip it.** If the reconciliation burden exceeded the audit value — plausible for a
single-site shop with no traceability requirements. A hybrid (event-sourced inventory and
finance, mutable master data) is the fallback, and honestly is where most of the value already
is. We are *not* event-sourcing master data; item and supplier records are ordinary versioned
rows. Full event sourcing of everything is the mistake here.

---

## ADR-002 — Bitemporal by default on transactional records

**Decision.** `occurred_at` (valid time) and `recorded_at` (transaction time) on every flow event
and assertion; effectivity dating on specifications.

**Why.** Backdated receipts, retroactive engineering changes, period-close adjustments, and
"what did we believe on the 3rd" are not edge cases in manufacturing — they are Tuesday. Costing
correctness depends on it.

**Cost.** Every query gains predicates that developers will forget. Bugs from missing temporal
filters are subtle and produce plausible-looking wrong numbers, which is the worst kind.

**Mitigation.** Bitemporality is hidden by default. The standard access path is an "as of now"
view; the raw tables are reachable but not the default. Temporal queries are a deliberate act.

**What would flip it.** Nothing at the transaction layer — this is close to non-negotiable.
We *did* decline full bitemporality on master data, where simple effectivity dating is enough
and much cheaper to reason about.

---

## ADR-003 — One commitment shape vs. a table per document type

**Decision.** `commitment` + `commitment_line` for all thirteen document kinds; kind selects
validation and policy, not a table.

**Why.** It collapses the agent's vocabulary. "What is committed against this item over 30 days"
is one query, and it stays one query when a fourteenth document type arrives. It also makes
pegging (demand → supply) uniform, which is what makes "why are we buying this" answerable in one
hop.

**Cost.** Genericity taxes clarity. A sales order and a capacity reservation share columns they
do not both use, so nullable columns proliferate and type-level constraints weaken — a purchase
line and a quote line are enforceable-different only in the validation layer, not the DDL.
Reporting queries need a `kind` filter that people will forget, producing totals that quietly
double-count.

**Mitigation.** Per-kind views with the right columns and the filter baked in, used as the
default access path. Per-kind JSON Schema validation on `attributes`.

**What would flip it.** If two kinds diverged enough that the shared table became mostly nulls —
most likely candidate is capacity reservation, which is arguably not a commitment about a
*resource quantity* at all. If it splits, it splits alone.

---

## ADR-004 — Three-tier truth (signal / assertion / record) vs. writing extracted data straight into fields

**Decision.** Agents write assertions; promotion to the record of truth is a gated act.

**Why.** It lets the system hold a belief without acting on it. That is the difference between an
85%-accurate agent being useful and being dangerous. It also gives every field a provenance
story, which is what makes the system auditable when a regulator or a customer asks.

**Cost.** Write amplification, more tables, and a genuinely harder mental model. Two places to
look for "the value" — and the risk of them disagreeing. Query complexity increases everywhere
that needs the *current belief* rather than the record.

**Mitigation.** Only *interpreted* attributes get the assertion treatment. Anything keyed by a
human through a validating UI goes straight to the record with `method = 'human_keyed'` recorded
on the event. We are not asserting our way to a supplier's name.

**What would flip it.** If extraction accuracy for a document class became high enough that the
assertion layer was pure overhead — but even then the audit trail argument holds independently,
so this would narrow the tier rather than remove it.

---

## ADR-005 — PostgreSQL, single database, single schema

**Decision.** One Postgres instance. `jsonb` for governed extensions, full-text search in-database,
vectors via pgvector when needed, time-series data *out* of it.

**Why.** At SMB manufacturing scale — tens of millions of flow rows a year, not billions — one
database that can do transactions, JSON, search, and vectors removes an entire category of
distributed-systems problems. Every cross-store consistency bug is a bug we do not have. This is
the highest-leverage boring decision in the document.

**Cost.** A ceiling exists. High-frequency machine telemetry would swamp it; heavy analytical
queries will contend with transactional load. Vector search at scale is not Postgres's strength.

**Mitigation.** Telemetry goes to a purpose-built time-series store from day one, with only
*summarised* assertions landing in Postgres (see open question 3 in `04_schema_notes.md`).
Read replicas for analytics.

**What would flip it.** Multi-site deployments with tens of millions of events *per site per
month*, or a customer with a real data-lake requirement. Neither is the target segment.

---

## ADR-006 — Domain-verb command API vs. exposing CRUD/GraphQL to agents

**Decision.** A registry of ~40 commands with declared pre/postconditions, invariants,
reversibility, and dry-run.

**Why.** Invariants below the agent instead of in the prompt. A bounded, evaluable action space.
And a natural authority-granting unit — you grant authority *per command*, which is impossible if
the surface is "any query."

**Cost.** Real engineering effort per command, and a permanent coverage gap: any operation not
yet expressed as a command is unavailable to agents, so there is standing pressure to add a
generic escape hatch. That pressure must be refused; the escape hatch is the whole design
undone.

**Mitigation.** Read-side is more permissive than write-side — agents get flexible query
capability against read models, because reads are R0. Only writes are narrow.

**What would flip it.** Nothing. If this flips, the architecture is no longer agent-first in any
meaningful sense.

---

## ADR-007 — Agents propose; policy commits — vs. agents writing directly with rollback

**Decision.** No agent has direct write access to the record of truth.

**Why.** Graduated authority, shadow-mode onboarding, and a human queue that shows intent and
reasoning rather than a diff. It also means a misbehaving agent is contained by policy rather than
by a hope that someone notices.

**Cost.** Latency, and a human bottleneck that can make the system *slower* than the manual
process it replaced — the failure mode that kills adoption. It also adds an entire subsystem
(policy engine) that must itself be correct.

**Mitigation.** Aggressive auto-commit for R1/R2 inside calibrated limits; `act_with_notice` with
a reversal window for the middle band; queue depth monitored as a first-class operational metric.

**What would flip it.** Nothing at the boundary — but the *bands* should move steadily toward
autonomy per capability as calibration data accumulates. A system where authority never widens is
one that never pays back.

---

## ADR-008 — Ontology as runtime data vs. hand-written models

**Decision.** `semantic_term`, `attribute_definition`, `command_definition` are data; tool
schemas, validation, and labels are generated from them.

**Why.** One source of meaning. Agent grounding stops being a prompt-engineering problem and
becomes a data problem — the agent reads the same definitions the validator enforces.

**Cost.** Indirection. Harder to read the code and know what a field means without querying.
Slower to change in the small — adding a field means a registry entry, not a migration line.
And a real risk of **ontology drift**, where the registry and the actual tables disagree, which
is worse than having no registry at all because it is trusted.

**Mitigation.** Drift is a CI check: the registry and the schema are compared on every build, and
disagreement fails the build.

**What would flip it.** If the registry became a bottleneck on iteration speed during early
development. Reasonable interim: code-first with generated registry export, flipping to
registry-first once the model stabilises.

---

## ADR-009 — Quantum-level inventory granularity vs. aggregate quantity per item/location

**Decision.** Inventory is a set of quanta across five orthogonal dimensions.

**Why.** Traceability, consignment, quality holds, and customer-supplied material are all
unrepresentable without it — and they are exactly the situations that generate disputes and
recalls. The recall traversal in `01_ontology.md` only works because of it.

**Cost.** Row count multiplies. Quantum proliferation is real: naive splitting creates thousands
of tiny quanta, and both queries and the UI degrade. Simple questions ("how many do we have")
become aggregations.

**Mitigation.** Merge policy — quanta identical on all five dimensions are merged unless the item
is lot-tracked and the lots differ. Aggressive closing of zero-quantity quanta. The default UI
shows aggregates and expands to quanta on demand.

**What would flip it.** For a shop with no lot tracking, no consignment, and no customer-supplied
material, this is over-modelled. But those shops grow into needing it, and retrofitting
traceability onto aggregate history is impossible — the data was never captured. Build it in.

---

## ADR-010 — Costing: actual layered cost carried on flows vs. standard cost with variance accounts

**Decision.** Cost rides `resource_flow`; costing method is per item
(`standard | moving_average | fifo_layered | specific_identification`), defaulting to moving
average.

**Why.** Because cost is attached to the same rows that move material, actual job cost, inventory
valuation, and margin are all derivable from one structure with no reconciliation step. Job shops
genuinely need actual costing — every job is different, and standard cost on a one-off is
fiction.

**Cost.** Actual costing is computationally heavier and gives less stable margins period to
period, which controllers dislike. Standard costing exists partly *because* variance accounts are
a useful management tool, and pure actual costing loses that lens. Retroactive cost corrections
(a supplier invoice arriving three weeks after the receipt) require revaluation flows that ripple
through consumed material — a genuinely hard problem.

**Mitigation.** Support standard costing per item for stable high-volume parts, and generate
variance flows against it. `revaluation` is a first-class `cost_component` so the ripple is
explicit and traceable rather than hidden in a period-end adjustment.

**What would flip it.** A customer base dominated by repetitive high-volume manufacturing would
favour standard-first. Our segment does not look like that.

---

## ADR-011 — Capability-based routing with no solver in v1 vs. finite-capacity scheduling from the start

**Decision.** The schema supports finite capacity (capabilities, calendars, operation windows),
but v1 ships infinite-capacity planning with a good exception detector. The solver is a later
module.

**Why.** Scheduling is where ERP projects go to die. A bad finite scheduler is worse than none —
it produces plans nobody believes, and then the shop runs on a whiteboard and the ERP data goes
stale, which corrupts everything downstream. Meanwhile an agent that *detects* schedule risk and
proposes targeted fixes captures a large share of the value at a fraction of the risk.

**Cost.** No true promise dates. Available-to-promise stays approximate. Competitors will demo a
Gantt chart we do not have.

**Mitigation.** Capability tags on steps rather than hard machine binding, so when the solver
arrives it has a legal move set to work with. Calendar overlap already excluded at the database.

**What would flip it.** A customer whose binding constraint is genuinely scheduling — a single
bottleneck cell with high changeover cost. That is a real segment, and it would justify pulling
the solver forward.

---

## ADR-012 — Row-level multi-tenancy vs. schema- or database-per-tenant

**Decision.** `tenant_id` on every table with Postgres row-level security.

**Why.** One migration, one connection pool, one operational surface. Cross-tenant benchmarking
(anonymised) becomes possible, which is a genuine product differentiator in this segment — a job
shop cannot otherwise know whether its scrap rate is good.

**Cost.** A single missed RLS policy is a cross-tenant data leak, which for an ERP is an
extinction-level incident. Noisy-neighbour effects. Per-tenant restore is painful — restoring one
customer's data from a shared table is not a simple operation.

**Mitigation.** RLS enforced by default-deny; a CI test that fails the build if any table lacks a
policy; per-tenant logical backup streams.

**What would flip it.** A customer with a contractual or regulatory isolation requirement —
common in defence-adjacent work, which is common in job shops. The design should keep
database-per-tenant viable as a deployment option, which row-level tenancy with a consistent
`tenant_id` does.

---

## ADR-013 — Evidence stored as immutable content-addressed blobs vs. parsed-and-discarded

**Decision.** Keep the original bytes forever, hashed; extraction produces assertions pointing at
regions.

**Why.** Reprocessing when models improve, citation for audit, and dispute resolution. The
original document *is* the evidence in a commercial disagreement.

**Cost.** Storage growth, and a data-retention/GDPR surface — immutable storage and deletion
requests are in direct tension.

**Mitigation.** Tiered storage with lifecycle rules. Crypto-shredding for erasure requests: the
blob stays, the key is destroyed, the hash and metadata survive so the audit chain does not break.

**What would flip it.** Nothing at the design level; retention *periods* are a per-tenant policy.

---

## ADR-014 — Build alongside an incumbent ERP first, displace module by module

**Decision.** v1 is not a replacement. It runs beside an existing ERP (M1 in the near-term
context), owns Tiers B and C and the proposal protocol, reads the incumbent's data, and writes
back through the incumbent's API. It grows into the system of record one module at a time.

**Why.** This is the honest one. A greenfield ERP is a multi-year effort with a high mortality
rate, and the killer is never the schema — it is ten thousand edge cases in someone's actual shop
plus the fact that no manufacturer will bet the business on an unproven system of record. The
agent layer, meanwhile, delivers value on day one *without* owning the record of truth. Building
the core in the shape described here means the transition is possible later; building it as a
bolt-on means it never is.

**Cost.** Dual-system complexity, synchronisation lag, and a permanent temptation to let the
incumbent's model leak into ours — which is the specific thing the clean-room rule exists to
prevent. Integration work is unglamorous and never finished. And there is real risk of getting
stuck as a permanent bolt-on: comfortable, but not the thing we set out to build.

**Displacement order, weakest-first and lowest-traceability-first:**
1. Estimating and quoting — usually the weakest module, lowest integration surface, immediate
   value.
2. Scheduling and shop-floor execution — high pain, and the data already flows one way.
3. Procurement and AP — where the agent value is proven (matching, variance, expediting).
4. Inventory and traceability — the hard one; requires cutover, not coexistence.
5. Order management.
6. **The general ledger, last and only if ever.** Displacing the GL first is the single most
   reliable way to kill the project.

**Mitigation of the leak risk.** All incumbent data enters through an explicit anti-corruption
translation into our ontology. Their field names never appear in our schema. This is a
maintained boundary, not a one-time import — and it is enforced by the clean-room rule in
`../CLAUDE.md`.

**What would flip it.** A greenfield customer with no incumbent — a new plant, or a shop running
on spreadsheets, which is a genuinely large population in this segment. That is the ideal design
partner and worth actively seeking, because they let the full architecture be exercised end to
end without an anti-corruption layer in the way.

---

## The three decisions most likely to be wrong

Stated plainly, since a document of confident ADRs is a document that has stopped thinking:

1. **ADR-001 (event sourcing everywhere in the transaction layer).** The reconciliation and
   projection burden is easy to underestimate and it is paid continuously, by the team, forever.
   If this project stalls on operational complexity, this is the cause.
2. **ADR-004 + Pillar 8 (assertions and belief modelling).** Genuinely novel, genuinely
   unproven at this scale, and the `belief_confidence` model in particular is currently an
   intention rather than a calibrated function. It could turn out that shops want a number, not a
   distribution, and that the reasoning it enables is worth less than the confusion it causes.
3. **ADR-003 (one commitment shape).** Elegant, and elegance is exactly what over-generalises. If
   the nullable columns and kind-filters start dominating the code, split it — and do not wait
   long to admit it.
