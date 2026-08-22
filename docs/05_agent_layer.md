# 05 — The Agent Layer

*What agents can do, how they are authorised, and how we know whether they are any good.*

---

## The operating model in one paragraph

Agents read Tier C (evidence) and Tier B (assertions), write assertions, and **propose** commands.
A policy engine evaluates each proposal against authority grants, tolerance bands, and
segregation-of-duties rules, then auto-commits, queues it for a human, or rejects it. Committed
commands emit flow events attributed to the agent as a Party. Humans work an **exception queue**
ranked by money at risk, not a stack of forms. Every auto-committed action is subject to a
standing random audit that feeds a calibration curve, and that curve is the evidence used to
raise or revoke authority.

---

## The command surface

Agents never see tables. They see a registry of domain commands (`command_definition`, §11), each
declaring: argument schema, preconditions, postconditions, invariants, reversibility class, the
event types it emits, the minimum authority band, and whether it supports dry-run.

An indicative first set — small on purpose:

| Family | Commands |
|---|---|
| Read | `find_entity`, `explain_balance`, `trace_genealogy`, `project_availability`, `summarise_exception` |
| Procurement | `draft_requisition`, `issue_purchase_order`, `expedite_line`, `acknowledge_supplier_confirmation` |
| Receiving | `receive_against`, `record_over_delivery`, `route_to_inspection`, `return_to_supplier` |
| Payables | `match_invoice_to_receipt`, `flag_price_variance`, `hold_invoice`, `release_payment` |
| Inventory | `move_quantum`, `split_lot`, `merge_lot`, `change_condition`, `request_cycle_count`, `post_count_result` |
| Production | `release_job`, `issue_to_job`, `report_production`, `report_scrap`, `reschedule_operation`, `substitute_component` |
| Quality | `record_inspection`, `raise_nonconformance`, `propose_disposition` |
| Customer | `draft_quote`, `promise_date`, `allocate_to_order`, `ship_against` |

**Design rules for commands:**

1. **A command is a business act, not a table operation.** `issue_to_job`, never `insert_flow`.
2. **Invariants live in the command, not the prompt.** An agent cannot construct a movement that
   breaks lot genealogy because no command does that.
3. **Every command dry-runs.** The dry run produces the blast radius the policy engine decides on.
   A command that cannot dry-run cannot be auto-committed, ever.
4. **Arguments are URIs, not names.** `item: "erp:item_definition:BRK-1180"`. Names are resolved
   in a separate, explicit retrieval step that has its own confidence and can fail loudly
   (Pillar 6). This is the structural anti-hallucination boundary: an invented URI is a rejection
   at the door, not a phantom row three tables deep.
5. **Commands are additive.** New capability means a new command with its own authority profile,
   never a new flag on an existing one that quietly widens its blast radius.

---

## Reversibility classes drive everything

| Class | Meaning | Default posture |
|---|---|---|
| `R0_read_only` | no state change | always allowed |
| `R1_reversible` | internal, fully undoable, no external trace | auto-commit freely |
| `R2_compensable` | undoable via a compensating flow; leaves an audit trace | auto-commit within limits |
| `R3_externally_visible` | a third party has seen it — PO transmitted, email sent, payment released | human approval unless explicitly and narrowly granted |
| `R4_physically_irreversible` | the world changed — material cut, part scrapped, destructive test run | human, always |

The important consequence: **R3 is the real line**, not R4. R4 actions mostly happen through
people on the shop floor anyway. R3 is where an agent can do damage at software speed and at
software scale — a hundred wrong POs transmitted before anyone notices. Egress is the control
point.

## Authority bands

Granted per `(agent, command, scope)` with monetary ceilings, a daily aggregate ceiling, a
reversibility cap, an expiry, and a written rationale.

| Band | The agent may… |
|---|---|
| `observe` | read and report only |
| `suggest` | write assertions and proposals; nothing commits |
| `act_within_limits` | auto-commit inside declared tolerance and monetary ceilings; everything else queues |
| `act_with_notice` | auto-commit and notify a named human, who has a defined window to reverse |
| `autonomous` | auto-commit without notice; still subject to random audit and daily ceilings |

Two rules that matter more than the ladder itself:

- **Grants expire.** Every grant is time-boxed with a rationale, so "temporary during the busy
  season" cannot silently become permanent.
- **Agents are bound by segregation of duties.** `duty_separation_rule` applies to
  `software_agent` parties identically to people. An agent that can both create a supplier and
  release payment to it is a fraud path with no human in it — and it will be found by an auditor
  before it is found by us.

## Delegation is explicit, never impersonation

`flow_event.on_behalf_of_id` records delegation. An agent acting for a buyer is recorded as *the
agent, acting on behalf of the buyer* — never as the buyer. Impersonation destroys the audit
trail and makes calibration impossible, because you can no longer separate the agent's error rate
from the human's.

---

## The exception queue is the primary human UI

Humans do not browse the ERP. They work a queue of things that need judgment, ranked by
`estimated_impact` — money at risk — rather than by age or by the order rows were created.

Each item carries: what happened, why it matters, the evidence, the agent's proposed fix with its
rationale, the alternatives it considered and rejected, and one-click accept/modify/reject.

**Design constraints on the queue, learned from how these fail:**

- **A rejected proposal must be cheap to correct.** "Modify and approve" is the common path, and
  the modification is training signal — capture it in `agent_outcome.corrected_value`.
- **Queue depth is a monitored metric with a ceiling.** If it grows faster than it drains, the
  authority bands are set wrong, and the fix is to widen a *narrow, well-calibrated* band — never
  to bulk-approve.
- **Bulk approve is deliberately not offered** for R3+. If a hundred identical proposals are
  pending, that is a signal to grant the agent a narrow scoped autonomy for that exact case, with
  the audit sampling that comes with it. Bulk approval is automation with none of the controls.

---

## Evaluation: the part that determines whether any of this works

Per the house rule (`skills_library/reference/evals_playbook.md`), **verifiability is a design
input, not a post-hoc activity.** A capability that cannot be scored does not get built.

**Every agent capability must declare, before implementation:**

1. **The unit of judgment.** One invoice, one shortage, one disposition — with a ground-truth-able
   answer.
2. **Where ground truth comes from.** Human review, downstream outcome (did the payment get
   disputed?), or reconciliation against a later verified fact (did the cycle count agree?).
3. **The error taxonomy.** Not "accuracy" — the specific ways it goes wrong. For invoice
   matching: wrong PO, wrong line, missed multi-receipt split, quantity from the wrong column,
   right match but wrong confidence. Error analysis first, metrics second.
4. **The cost asymmetry.** A false auto-approve on a $40k invoice and a false escalation on a $40
   one are not the same error, and a single accuracy number hides that. Score with the actual
   cost matrix.

**Calibration is the specific obligation of this architecture.** `assertion.confidence` and
`proposal.confidence` are load-bearing — the policy engine *decides on them*. So they must mean
something:

- Bucket predictions by stated confidence; measure observed agreement per bucket. If the 0.9
  bucket is right 70% of the time, the number is decoration and every threshold built on it is
  wrong.
- **Sample auto-committed actions randomly** (`agent_outcome.review_basis = 'random_audit_sample'`).
  This is the load-bearing sample. Reviewing only what humans already had to approve measures the
  agent on precisely the cases where it was least confident — a biased sample that flatters it.
- Publish the calibration curve alongside the authority grant. Widening authority is a decision
  made against that curve, not against a demo.

**Shadow mode is the default onboarding path.** A new agent runs at `suggest`, writing proposals
nobody commits, scored against what humans actually did. It earns authority; it is not granted it.

**Watch for drift.** Model versions change, suppliers change document layouts, a new customer
arrives with a format nobody has seen. Confidence calibration monitored over time is the early
warning; a silent accuracy collapse behind a stable confidence number is the failure mode that
costs real money.

---

## What agents are actually *for* here

Worth stating plainly, because "AI-first ERP" invites the wrong picture. The agent is not a
chatbot on top of a form. The highest-value work in a job shop is the reconciliation and chasing
that nobody has time to do properly:

- **Document → structure.** Supplier invoices, packing slips, customer POs and prints turned into
  assertions with citations. The perennial data-entry tax.
- **Matching.** Invoice ↔ receipt ↔ PO, including the messy cases — partial receipts, split
  shipments, freight allocated across lines, unit-of-measure mismatches.
- **Exception detection with a proposed fix.** Shortages that will idle a machine, operations
  tracking behind promise dates, price variances outside tolerance, expiring certifications.
- **Reason-code enrichment.** Scrap and downtime reasons proposed from evidence — machine
  telemetry, operator notes, timing — rather than an operator choosing the top dropdown item at
  shift end. These fields are the highest-value and lowest-quality data in the plant.
- **Physical uncertainty triage** (Pillar 8). Deciding *what to go count* based on the expected
  cost of being wrong. This one is only possible because of the belief model, and it is the
  clearest example of a capability a conventional ERP structurally cannot offer.
- **Explanation.** "Why is this job late," "why are we buying this," "what is in this shipment" —
  answerable by traversing stored relations, not by reconstructing a story.

Every one of those is verifiable against a ground truth. That is not a coincidence — it is the
selection criterion.
