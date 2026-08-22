# 00 — Principles

*What an agent-first ERP is, and why the incumbent shape defeats agents.*

---

## The diagnosis: why agents fail on a conventional ERP

A conventional manufacturing ERP is **forms over tables**. The schema was designed backwards from
the screens, the business logic lives in the UI layer and in stored procedures, and state is
mutated in place. That shape has six specific properties that make agents unreliable on it — and
each one points at a design pillar.

| Property of a conventional ERP | Why an agent breaks on it |
|---|---|
| **Semantics live in tribal knowledge.** Columns named `USERDEF3`, `FLAG7`, `MISC_CODE`; meaning is what the plant manager remembers. | The agent has no grounding. It guesses at meaning, and guesses confidently. |
| **Destructive updates.** `UPDATE job SET qty_complete = 40` overwrites 38. | No reasoning trail, no reversal, no "why did this change." You cannot audit an agent you cannot replay. |
| **No intent layer.** The database records what happened, never what was *proposed*, by whom, on what basis. | There is nowhere to put an agent's suggestion. It either writes to production or it does nothing. |
| **All facts are asserted equally.** An OCR'd invoice number and a human-keyed one land in the same `varchar` with the same authority. | The agent cannot express or propagate uncertainty, so the system cannot triage its own weak spots. |
| **Authority is binary.** A user can edit a table or cannot. | Agents need graduated authority. Binary permission forces the choice between "useless" and "terrifying." |
| **The API surface is CRUD.** Expose the tables and let the caller figure it out. | Given raw CRUD, an agent produces structurally valid garbage — a receipt with no matching commitment, a lot split that breaks traceability. Invariants must live below the agent, not in its prompt. |

The rest of this document is the eight pillars that answer these.

---

## Pillar 1 — An append-only flow ledger is the record of truth; current state is a projection

Nothing is ever updated in place at the core. Every physical or financial change is an **event**
appended to a hash-chained log, and every balance (on-hand, WIP value, order backlog, operation
completion) is *derived* from that log.

**What it buys us:** time travel and replay come free, which means (a) an agent's action can
always be explained by walking the causation chain, (b) a bad agent run can be reversed by
compensating events rather than by a DBA with a backup, (c) "what did we believe on the 3rd" is
answerable, which is what auditors and cost accountants actually ask.

**What it costs:** read complexity, projection lag, and storage growth. See ADR-001 in
`06_tradeoffs.md` for how far we take it and where we stop.

**The rule that follows:** counters are never stored. `qty_completed` is a `SUM` over flows, not
a column someone increments. Stored counters drift; projections cannot.

## Pillar 2 — Three tiers of truth, with promotion gates

Not all data deserves the same ceremony. We separate:

- **Tier C — Signals.** Raw immutable observation: the vendor PDF, the email, the scale reading,
  the machine's MTConnect sample, the barcode scan. Content-addressed, never edited.
- **Tier B — Assertions.** Interpreted claims *about* entities: "this invoice's PO number is
  4471," "this scrap event's root cause is tooling," "this receipt matches that PO line."
  Every assertion carries actor, method, confidence, evidence pointer, and validity window.
  Append-only; a new assertion supersedes rather than overwrites.
- **Tier A — Records of truth.** Money, inventory quantity, commitments to customers and
  vendors. Strongly typed, invariant-enforced, event-sourced.

Agents live natively in Tier B. They read C, assert into B, and **propose** into A. Promotion
from B to A is a gated act with a policy behind it.

**What it buys us:** the system can hold a belief without acting on it. That single capability is
the difference between an agent that is useful at 85% accuracy and one that is dangerous at 85%
accuracy.

## Pillar 3 — Agents propose; the policy engine commits

No agent has write access to Tier A. An agent emits a **Proposal**: a target command, its
arguments, a rationale in natural language, the evidence it rests on, a calibrated confidence, and
an estimated blast radius. A policy engine then decides — auto-commit, queue for a human,
or reject.

**What it buys us:** authority becomes a tunable dial per capability, per dollar magnitude, per
reversibility class. You can run a new agent in shadow mode against the same queue and score it
before granting it any authority at all.

**What it costs:** latency, and a human review queue that can become the bottleneck. The mitigation
is authority bands tied to reversibility and magnitude (see `05_agent_layer.md`), not "approve
everything."

## Pillar 4 — The ontology is a runtime artifact, not documentation

The semantic layer is stored as data, and it is the single source that generates: the tool schemas
agents see, the validation rules, the API contracts, and the UI labels. One definition of what
"lot" means, consumed by every layer.

**What it buys us:** the grounding problem largely disappears. Agents don't infer meaning from
column names — they read definitions, units, allowed values, and relations from the same registry
the validator uses. When a term changes, everything changes together.

**What it costs:** a layer of indirection and real discipline. Ontology drift — where the registry
and the tables disagree — is the specific failure mode to guard against.

## Pillar 5 — Agents get domain verbs, never CRUD

The agent-facing surface is a bounded set of **commands** drawn from the domain — `receive_against`,
`issue_to_job`, `split_lot`, `reschedule_operation`, `flag_nonconformance` — each with a declared
argument schema, preconditions, postconditions, invariants, a reversibility class, and a required
authority band.

**What it buys us:** invariants are enforced below the agent. The agent cannot invent an
inventory movement that breaks lot genealogy, because there is no command that does that.
It also collapses the action space from "any SQL" to a few dozen well-specified verbs, which is
the difference between an evaluable system and an unevaluable one.

## Pillar 6 — Everything is addressable, and agents may only cite real addresses

Every entity has a stable URI, a human-readable code, and a resolvable natural-language handle.
Commands reject URIs that do not resolve.

**What it buys us:** a hard structural boundary against hallucination. An agent that invents part
number `ACME-4471-B` gets a rejection at the command boundary, not a phantom row. Resolution
(natural language → URI) is its own retrieval step with its own confidence, which is where the
ambiguity belongs.

## Pillar 7 — Bitemporal by default on anything that can be backdated

Every record of consequence carries both **valid time** (when the fact was true in the world) and
**transaction time** (when we learned it). A receipt posted Tuesday for material that arrived
Friday is two different dates, and cost accounting, inventory valuation, and any retroactive
engineering change depend on telling them apart.

**What it buys us:** correct answers to "what was the on-hand on the 3rd" *and* "what did we
think the on-hand was on the 3rd" — different questions with different answers, both of which get
asked in an audit. Also makes retroactive BOM and routing changes tractable instead of
catastrophic.

**What it costs:** every query gets two more predicates, and developers get it wrong. Mitigation:
bitemporality is hidden behind "as of now" views by default and only surfaced when asked for.

## Pillar 8 — Physical state is *believed*, not known

This is the pillar most specific to manufacturing, and the one most at odds with how ERPs are
built. An ERP asserts that there are 412 units in Bin A-3. There are not. There are 412 units
*as of the last transaction that claimed to touch them*, degraded by every unrecorded pick,
miscount, and shop-floor shortcut since.

We model on-hand as a **believed quantity** with `last_verified_at`, a verification method, and a
confidence that decays with time and movement velocity.

**What it buys us:** agents can reason about physical uncertainty instead of pretending it away —
"we believe 400 ± drift; a stockout here idles the press for 6 hours; the expected cost of a cycle
count is lower than the expected cost of being wrong; go count it." That is a genuinely new class
of decision, and it is unavailable to any system that stores on-hand as an integer.

**What it costs:** more state per quantum, and a real risk of over-engineering. The confidence
model must stay simple and empirical (decay calibrated against actual cycle-count variance), not
a Bayesian edifice nobody can explain to a controller.

---

## What we are deliberately *not* doing in v1

Stated up front so scope creep has to argue with something:

- **No general ledger of record.** We project postings, but the GL stays in the incumbent system
  until everything else is proven. Displacing the GL first is how greenfield ERP projects die.
- **No payroll, no HR, no CRM.** Adjacent, well-served, and not where the agent leverage is.
- **No finite-capacity scheduling solver in v1.** The schema supports one (see ADR-011); the
  solver is a later module, and a bad one is worse than none.
- **No multi-currency consolidation or transfer pricing.** SMB job shops rarely need it, and it
  contaminates the costing model early.
- **No custom-field-anything.** The escape hatch is typed extension attributes governed by the
  ontology (Pillar 4), not a `USERDEF` wasteland.

---

## The honest framing

Building a full ERP is a multi-year effort with a high mortality rate, and the reason is never
the schema — it is the ten thousand edge cases in someone's actual shop. This architecture is
worth pursuing because the agent layer, not the transaction layer, is where the new value is;
the transaction layer just has to be shaped so agents can operate on it safely.

The realistic path (ADR-014) is that this core runs *alongside* an incumbent ERP first, owning
Tiers B and C and the proposal protocol, and grows into the system of record one module at a
time — starting where the incumbent is weakest and the traceability requirements are lightest
(estimating/quoting, scheduling, AP), never with the GL.
