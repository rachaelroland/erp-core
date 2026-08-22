# 02 — Controlled Vocabularies

*The enumerations. These are the values agents will see, so they are designed to be
self-explanatory rather than terse — an agent reading `condition = 'quarantined_pending_disposition'`
needs no lookup table; one reading `st = 4` needs tribal knowledge.*

**Naming rules for every vocabulary here:**
1. `snake_case`, spelled out, no abbreviations, no numeric codes.
2. Every term carries a definition in `semantic_term` (see schema §1) — the definition is what the
   agent reads, and it is the same string the validator and the UI use.
3. Terms are **added, never repurposed.** Retiring a term deprecates it; it never gets a new
   meaning. Repurposed enum values are how historical data quietly becomes wrong.
4. Vocabularies are versioned with effectivity dates, because "what did `on_hold` mean in 2027"
   is an audit question.

---

## Party roles

`customer`, `prospect`, `supplier`, `subcontractor`, `carrier`, `employee`, `contractor`,
`regulator`, `certifying_body`, `internal_organization`, `software_agent_operator`

A Party holds many roles simultaneously; roles carry their own effectivity and their own
qualification records (a supplier's ISO cert, an employee's weld qualification).

## Actor kinds

`person`, `organization`, `equipment_actor`, `software_agent`, `scheduled_process`

Every event names an actor of one of these kinds. `software_agent` actors additionally record
model identifier, version, prompt/config hash, and the run identifier — without those, an agent's
past decisions are not reproducible and therefore not auditable.

---

## Item classification

Two independent facets. Conflating them (the common shortcut) makes "purchased tooling that we
also sell" unrepresentable.

**Facet A — sourcing (how it comes to exist):**
`purchased`, `manufactured`, `subcontracted`, `phantom` (structural only, never stocked),
`service`, `co_product`, `by_product`, `customer_supplied`

**Facet B — function (what role it plays):**
`raw_material`, `component`, `subassembly`, `finished_good`, `consumable`, `tooling`,
`fixture`, `gauge`, `packaging`, `spare_part`, `scrap_material`

Plus free-form **capability tags** for anything else (`rohs`, `itar_controlled`, `serialized`,
`shelf_life_limited`, `hazmat_class_3`). Tags are governed by the ontology registry so they cannot
proliferate silently — a new tag is a definition, not a typo.

## Unit-of-measure classes

`count`, `mass`, `length`, `area`, `volume`, `time`, `currency`, `energy`, `dimensionless`

Conversions are only permitted within a class, except for item-specific density/yield conversions
(kg ↔ each for a casting), which are explicit rows on the item, not global constants. This is a
deliberate strictness: silent cross-class conversion is a classic source of six-figure errors.

---

## Inventory quantum dimensions

Five orthogonal axes (ontology axis 6). **Never collapse these into one status field.**

**Condition:**
`usable`, `awaiting_inspection`, `in_inspection`, `quarantined_pending_disposition`,
`nonconforming_reject`, `nonconforming_use_as_is`, `awaiting_rework`, `in_rework`,
`expired_shelf_life`, `obsolete`, `sample_destroyed`

**Ownership:**
`owned`, `customer_supplied`, `vendor_consigned`, `vendor_owned_at_our_site`,
`ours_at_third_party`, `in_transit_title_ours`, `in_transit_title_theirs`

**Encumbrance:**
`free`, `soft_allocated` (planning intent), `hard_reserved` (this exact quantum, this commitment),
`staged_for_issue`, `staged_for_shipment`, `pledged_as_collateral`

**Place:** hierarchical path — `enterprise / site / area / location / bin`
(ISA-95-aligned vocabulary; our own structure).

**Identity:** item + revision + lot + serial, at whatever granularity the item's tracking policy
requires.

**Item tracking policy:** `none`, `lot_tracked`, `serial_tracked`, `lot_and_serial_tracked`.
Set on the item definition; determines the mandatory identity fields on every flow touching it.
Changing an item's tracking policy is effectivity-dated, never retroactive — pre-existing quanta
keep the granularity they were created with.

---

## Commitment taxonomy

**Direction:** `inbound` (we receive), `outbound` (we deliver), `internal` (we move or transform)

**Firmness ladder** — deliberately a ladder, since planning logic keys on ordering:
`speculative` → `forecast` → `quoted` → `offered` → `firm` → `released` → `in_fulfilment` →
`fulfilled` | `cancelled` | `expired`

**Kind** (the "document type" that a conventional ERP would give its own table):
`sales_quote`, `sales_order`, `blanket_release`, `demand_forecast`, `purchase_requisition`,
`purchase_order`, `blanket_purchase_release`, `subcontract_instruction`, `work_order_demand`,
`capacity_reservation`, `return_authorization_inbound`, `return_authorization_outbound`,
`transfer_order`

All thirteen share one structure. Kind selects which validations and which default policies apply
— it does not select a table.

---

## Flow (event) taxonomy

Physical and financial facts. Each is a signed pair (Pillar/duality rule); the name describes the
*business* act, not the table operation.

**Material:**
`receive`, `receive_over_delivery`, `return_to_supplier`, `issue_to_job`, `return_from_job`,
`report_production`, `report_scrap`, `report_rework`, `move`, `transfer_between_sites`,
`ship`, `receive_customer_return`, `cycle_count_adjust`, `physical_inventory_adjust`,
`split_lot`, `merge_lot`, `change_condition`, `write_off`, `sample_consume`

**Capacity / labor:**
`clock_on_operation`, `clock_off_operation`, `record_setup_time`, `record_run_time`,
`record_downtime`, `record_indirect_time`, `machine_state_change`

**Financial:**
`accrue_receipt_liability`, `record_supplier_invoice`, `match_invoice_to_receipt`,
`release_payment`, `recognize_revenue`, `issue_customer_invoice`, `apply_cash_receipt`,
`post_variance`, `revalue_inventory`

**Correction (never deletes):**
`compensate` — carries `compensates_flow_id` and a required reason code.

**Reason codes** are their own governed vocabulary per event family — scrap reasons
(`tooling_wear`, `operator_error`, `material_defect`, `setup_scrap`, `machine_malfunction`,
`engineering_change`, `handling_damage`), downtime reasons, adjustment reasons. These are the
highest-value fields in the entire system for improvement work and the ones most reliably
garbage in practice, which is a strong argument for agents proposing them from evidence rather
than an operator picking the first item in a dropdown at shift end.

---

## Job / process execution states

**Job:** `draft`, `planned`, `firm_planned`, `released`, `in_process`, `on_hold`,
`awaiting_material`, `awaiting_inspection`, `complete_pending_close`, `closed`, `cancelled`

**Operation:** `not_started`, `setup`, `running`, `paused`, `awaiting_move`,
`awaiting_inspection`, `complete`, `skipped`, `outsourced_in_transit`, `outsourced_at_vendor`

**Machine state** (for OEE, aligned in spirit with standard availability/performance/quality
decomposition; our own terms): `producing`, `setup`, `idle_no_work`, `idle_no_operator`,
`idle_no_material`, `unplanned_down`, `planned_maintenance`, `changeover`, `offline`

**Job quantity vocabulary** — all *derived* from flows, never stored as counters (Pillar 1):
`quantity_ordered`, `quantity_released`, `quantity_started`, `quantity_completed`,
`quantity_scrapped`, `quantity_reworked`, `quantity_in_inspection`, `quantity_shipped`,
`quantity_invoiced`

---

## Quality taxonomy

**Characteristic type:** `variable` (measured), `attribute` (pass/fail), `visual`, `functional_test`
**Disposition:** `accept`, `accept_with_deviation`, `rework`, `repair`, `scrap`,
`return_to_supplier`, `use_as_is_with_concession`, `pending`
**Nonconformance severity:** `critical`, `major`, `minor`, `cosmetic`
**Corrective action state:** `raised`, `contained`, `root_cause_analysis`, `action_planned`,
`action_implemented`, `effectiveness_verified`, `closed`

---

## Assertion taxonomy (Tier B)

**Method** — how the claim was produced. Drives default trust:
`human_keyed`, `human_reviewed`, `barcode_scan`, `rfid_read`, `sensor_reading`,
`document_extraction_model`, `classification_model`, `deterministic_rule`, `system_calculation`,
`external_system_sync`, `inference_model`

**Resolution status:** `current`, `superseded`, `contradicted`, `withdrawn`, `promoted_to_record`

**Confidence** is a `numeric(4,3)` in [0,1] with a *stated meaning*: the modeled probability that
a competent human reviewer would agree with the claim given the same evidence. It must be
calibrated against sampled human review (see `05_agent_layer.md`), or it is decoration.

---

## Proposal / governance taxonomy

**Reversibility class** — the primary input to how much authority a command needs:

| Class | Meaning | Example |
|---|---|---|
| `R0_read_only` | no state change | run an availability query |
| `R1_reversible` | internal state, fully undoable, no external trace | soft-allocate stock, reschedule an unreleased job |
| `R2_compensable` | undoable via a compensating flow, leaves an audit trace | post a receipt, issue material to a job |
| `R3_externally_visible` | third party has seen it; retraction is a business act | transmit a PO, email a customer, release a payment, ship |
| `R4_physically_irreversible` | the world changed | cut material, scrap a part, run a destructive test |

**Proposal status:** `draft`, `pending_policy`, `auto_committed`, `awaiting_human`,
`approved`, `rejected`, `expired`, `superseded`, `failed_precondition`

**Authority band** granted per (agent, command, scope):
`observe` → `suggest` → `act_within_limits` → `act_with_notice` → `autonomous`

**Blast radius** — declared estimate a command must produce *before* execution:
records touched, monetary magnitude, downstream commitments affected, whether any external party
is notified. This is what the policy engine actually decides on, and it is why commands must be
able to dry-run.
