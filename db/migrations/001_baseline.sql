-- SPDX-License-Identifier: Apache-2.0
-- Copyright 2026 Applied Industrials
-- =====================================================================
-- 001 — baseline
--
-- GENERATED from docs/03_schema.sql by infra/build_migrations.py.
-- Do not edit by hand: edit the reference schema and regenerate, or the
-- two drift and the database stops matching the document that explains
-- it. tests/test_migrations.py builds a database each way and fails if
-- the structures differ.
--
-- Any DROP is removed. The reference recreates from nothing because it
-- is a design artifact; a migration runs against a database that has
-- data in it.
-- =====================================================================

-- SPDX-License-Identifier: Apache-2.0
-- Copyright 2026 Applied Industrials
-- =====================================================================
-- agent_erp — reference schema
-- PostgreSQL 16+
--
-- CLEAN ROOM. Designed from the domain model in 01_ontology.md.
-- No structures, names, or patterns taken from any commercial or
-- open-source ERP. Vocabulary alignment with ISA-95 (equipment
-- hierarchy) and REA (event duality) is conceptual only and marked
-- where it occurs.
--
-- THIS IS A DESIGN ARTIFACT, NOT A MIGRATION. It exists to be read and
-- argued with. Indexes are indicative, not tuned. Some constraints are
-- written as comments where expressing them in DDL would obscure the
-- intent.
--
-- Reading order: §0 conventions → §8 flow ledger (the spine) →
-- §5 occurrences → everything else.
-- =====================================================================


-- =====================================================================
-- §0  CONVENTIONS
-- =====================================================================
--
-- * Every table carries tenant_id. Row-level security is the isolation
--   mechanism (ADR-012). Single database, single schema.
-- * Every business entity carries: id (uuid), tenant_id, code (the
--   human-readable natural key, unique per tenant), and participates in
--   the URI convention  erp:<entity_type>:<code>  (Pillar 6).
-- * Bitemporality (Pillar 7): valid_from/valid_to describe when a fact
--   was true in the world; recorded_at/superseded_at describe when we
--   knew it. Tables that need both say so; "as of now" views hide it.
-- * No stored counters (Pillar 1). Anything that could be summed from
--   the flow ledger IS summed from the flow ledger.
-- * Money is numeric(18,6) — six decimals because unit costs on
--   fasteners and per-second machine rates need them. Never float.
-- * Quantities are numeric(18,6) with a mandatory uom_id. There is no
--   such thing as a bare quantity in this schema.

CREATE EXTENSION IF NOT EXISTS "pgcrypto";   -- gen_random_uuid, digest
CREATE EXTENSION IF NOT EXISTS "btree_gist"; -- exclusion constraints on ranges

CREATE DOMAIN qty      AS numeric(18,6);
CREATE DOMAIN money6   AS numeric(18,6);
CREATE DOMAIN conf     AS numeric(4,3) CHECK (VALUE >= 0 AND VALUE <= 1);
CREATE DOMAIN code_key AS text CHECK (VALUE ~ '^[A-Za-z0-9][A-Za-z0-9._/-]{0,63}$');


-- =====================================================================
-- §1  SEMANTIC LAYER  (Pillar 4: the ontology is runtime data)
-- =====================================================================
-- This section is what makes the system self-describing. Agent tool
-- schemas, validation rules, and UI labels are all generated from here.
-- If a term is not registered, agents cannot see it and validators do
-- not accept it.

CREATE TABLE semantic_term (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid,                       -- NULL = global/system term
    vocabulary      text NOT NULL,              -- e.g. 'inventory_condition'
    term            text NOT NULL,              -- e.g. 'quarantined_pending_disposition'
    definition      text NOT NULL,              -- the string agents actually read
    synonyms        text[] NOT NULL DEFAULT '{}',-- shop-floor slang → canonical term
    unit_class      text,                       -- for quantitative terms
    parent_term_id  uuid REFERENCES semantic_term(id),  -- taxonomy is a DAG, not a list
    effective_from  timestamptz NOT NULL DEFAULT now(),
    deprecated_at   timestamptz,                -- deprecate, never repurpose (§02 rule 3)
    replaced_by_id  uuid REFERENCES semantic_term(id),
    UNIQUE (tenant_id, vocabulary, term)
);
COMMENT ON TABLE semantic_term IS
  'Every controlled value in the system. Agents read definition+synonyms to ground language; '
  'validators read the same rows. One source of meaning, no drift between docs and code.';

-- Extension attributes, governed. This is the deliberate alternative to
-- a USERDEF wasteland (00_principles.md, "not doing in v1").
CREATE TABLE attribute_definition (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid NOT NULL,
    applies_to      text NOT NULL,        -- entity type the attribute extends
    attribute_key   code_key NOT NULL,
    data_type       text NOT NULL         -- 'text'|'number'|'boolean'|'date'|'term_ref'|'quantity'
                    CHECK (data_type IN ('text','number','boolean','date','term_ref','quantity')),
    vocabulary      text,                 -- required when data_type='term_ref'
    unit_class      text,                 -- required when data_type='quantity'
    is_required     boolean NOT NULL DEFAULT false,
    definition      text NOT NULL,        -- mandatory. an undocumented field is a future outage
    UNIQUE (tenant_id, applies_to, attribute_key)
);

-- Resolvable address for every entity (Pillar 6). Commands reject URIs
-- that do not resolve here — the structural anti-hallucination boundary.
CREATE TABLE entity_uri (
    uri             text PRIMARY KEY,     -- 'erp:item_definition:BRK-1180'
    tenant_id       uuid NOT NULL,
    entity_type     text NOT NULL,
    entity_id       uuid NOT NULL,
    display_label   text NOT NULL,
    search_text     text,                 -- denormalised for NL→URI resolution
    retired_at      timestamptz,
    UNIQUE (tenant_id, entity_type, entity_id)
);
CREATE INDEX entity_uri_search_idx ON entity_uri USING gin (to_tsvector('english', search_text));


-- =====================================================================
-- §2  PARTIES & AUTHORITY
-- =====================================================================
-- Ontology §1. AI agents are Parties — same identity model, same
-- segregation-of-duties rules as people. Not a flag on a user.

CREATE TABLE party (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid NOT NULL,
    code            code_key NOT NULL,
    actor_kind      text NOT NULL
                    CHECK (actor_kind IN ('person','organization','equipment_actor',
                                          'software_agent','scheduled_process')),
    legal_name      text NOT NULL,
    display_name    text NOT NULL,
    -- software_agent identity, required for reproducibility of past decisions
    agent_model     text,                 -- e.g. 'vendor/model-identifier'
    agent_version   text,
    agent_config_hash text,               -- prompt + tool config digest
    attributes      jsonb NOT NULL DEFAULT '{}',   -- validated against attribute_definition
    created_at      timestamptz NOT NULL DEFAULT now(),
    retired_at      timestamptz,
    UNIQUE (tenant_id, code),
    CHECK (actor_kind <> 'software_agent'
           OR (agent_model IS NOT NULL AND agent_config_hash IS NOT NULL))
);

CREATE TABLE party_role (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid NOT NULL,
    party_id        uuid NOT NULL REFERENCES party(id),
    role            text NOT NULL,        -- semantic_term vocabulary 'party_role'
    valid_from      date NOT NULL DEFAULT CURRENT_DATE,
    valid_to        date,
    attributes      jsonb NOT NULL DEFAULT '{}',   -- payment terms, incoterms, lead times…
    UNIQUE (tenant_id, party_id, role, valid_from)
);
COMMENT ON TABLE party_role IS
  'Roles are held, not inherited. The same org is routinely customer + supplier + carrier; '
  'typing the party by role makes that unrepresentable.';

-- Qualifications: supplier certs, operator weld quals, calibration
-- authority. Expiry drives agent-detectable compliance exceptions.
CREATE TABLE party_qualification (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid NOT NULL,
    party_id        uuid NOT NULL REFERENCES party(id),
    qualification   text NOT NULL,
    scope_uri       text REFERENCES entity_uri(uri),  -- qualified for what
    granted_on      date NOT NULL,
    expires_on      date,
    evidence_id     uuid,                 -- FK added in §10
    verified_by     uuid REFERENCES party(id)
);

-- --- Governance (ontology §9) -----------------------------------------
CREATE TABLE authority_grant (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid NOT NULL,
    grantee_id      uuid NOT NULL REFERENCES party(id),   -- person OR agent
    command_name    text NOT NULL,        -- '*' or a specific command (§11)
    band            text NOT NULL
                    CHECK (band IN ('observe','suggest','act_within_limits',
                                    'act_with_notice','autonomous')),
    scope_filter    jsonb NOT NULL DEFAULT '{}',  -- e.g. {"site":"LOU","supplier_tier":"C"}
    max_monetary    money6,               -- per-action ceiling
    max_daily_monetary money6,            -- aggregate ceiling, resets daily
    max_reversibility text NOT NULL DEFAULT 'R1_reversible'
                    CHECK (max_reversibility IN ('R0_read_only','R1_reversible','R2_compensable',
                                                 'R3_externally_visible','R4_physically_irreversible')),
    granted_by      uuid NOT NULL REFERENCES party(id),
    valid_from      timestamptz NOT NULL DEFAULT now(),
    valid_to        timestamptz,
    revoked_at      timestamptz,
    rationale       text NOT NULL          -- why this grant exists; read at review time
);
COMMENT ON TABLE authority_grant IS
  'Authority is a dial per (grantee, command, scope), bounded by monetary magnitude and '
  'reversibility class. Grants are time-boxed so "temporary" cannot silently become permanent.';

-- Segregation of duties: applies identically to people and agents.
CREATE TABLE duty_separation_rule (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid NOT NULL,
    rule_code       code_key NOT NULL,
    command_a       text NOT NULL,
    command_b       text NOT NULL,
    correlation_key text NOT NULL,   -- the object both must touch, e.g. 'supplier_id'
    severity        text NOT NULL CHECK (severity IN ('block','require_second_approver','warn')),
    rationale       text NOT NULL,
    UNIQUE (tenant_id, rule_code)
);
COMMENT ON TABLE duty_separation_rule IS
  'Example: the actor that ran create_supplier on supplier X may not run release_payment on '
  'supplier X. Enforced against software_agent parties too — an agent that can do both is a '
  'fraud path with no human in it.';

CREATE TABLE policy_rule (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid NOT NULL,
    rule_code       code_key NOT NULL,
    applies_to      text NOT NULL,        -- command name or event family
    predicate       jsonb NOT NULL,       -- declarative condition, versioned & simulatable
    effect          text NOT NULL
                    CHECK (effect IN ('auto_commit','require_human','require_two_humans',
                                      'reject','warn_and_continue')),
    tolerance       jsonb NOT NULL DEFAULT '{}',  -- {"over_receipt_pct":2,"price_var_pct":5}
    priority        int NOT NULL DEFAULT 100,     -- lowest wins; first match decides
    valid_from      timestamptz NOT NULL DEFAULT now(),
    valid_to        timestamptz,
    rationale       text NOT NULL,
    UNIQUE (tenant_id, rule_code, valid_from)
);
COMMENT ON TABLE policy_rule IS
  'Policy is data so it can be versioned, simulated, and replayed. "What would this agent have '
  'been permitted to do last quarter" must be answerable without reading a git history.';


-- =====================================================================
-- §3  SPECIFICATIONS  (ontology §3 — the type level)
-- =====================================================================

CREATE TABLE unit_of_measure (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid NOT NULL,
    code            code_key NOT NULL,          -- 'kg', 'each', 'hour'
    unit_class      text NOT NULL,              -- §02 unit classes
    to_base_factor  numeric(24,12) NOT NULL,    -- against the class base unit
    precision_places int NOT NULL DEFAULT 6,
    UNIQUE (tenant_id, code)
);
-- Cross-class conversion is forbidden globally and permitted only per item:
CREATE TABLE item_uom_conversion (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid NOT NULL,
    item_id         uuid NOT NULL,              -- FK below
    from_uom_id     uuid NOT NULL REFERENCES unit_of_measure(id),
    to_uom_id       uuid NOT NULL REFERENCES unit_of_measure(id),
    factor          numeric(24,12) NOT NULL,    -- e.g. kg → each for a casting
    basis           text NOT NULL,              -- 'nominal_weight' | 'measured_yield' | …
    UNIQUE (tenant_id, item_id, from_uom_id, to_uom_id)
);

CREATE TABLE item_definition (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid NOT NULL,
    code            code_key NOT NULL,
    description     text NOT NULL,
    sourcing_class  text NOT NULL,   -- facet A (§02): purchased | manufactured | …
    function_class  text NOT NULL,   -- facet B (§02): raw_material | component | …
    stock_uom_id    uuid NOT NULL REFERENCES unit_of_measure(id),
    tracking_policy text NOT NULL DEFAULT 'none'
                    CHECK (tracking_policy IN ('none','lot_tracked','serial_tracked',
                                               'lot_and_serial_tracked')),
    shelf_life_days int,
    capability_tags text[] NOT NULL DEFAULT '{}',  -- governed by semantic_term
    attributes      jsonb NOT NULL DEFAULT '{}',
    -- Costing intent, per item: see ADR-010
    costing_method  text NOT NULL DEFAULT 'moving_average'
                    CHECK (costing_method IN ('standard','moving_average','fifo_layered',
                                              'specific_identification')),
    created_at      timestamptz NOT NULL DEFAULT now(),
    retired_at      timestamptz,
    UNIQUE (tenant_id, code)
);
ALTER TABLE item_uom_conversion
    ADD CONSTRAINT item_uom_conversion_item_fk FOREIGN KEY (item_id) REFERENCES item_definition(id);

-- Revisions are first-class: the ontology's `succeeds` relation.
CREATE TABLE item_revision (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid NOT NULL,
    item_id         uuid NOT NULL REFERENCES item_definition(id),
    revision_code   code_key NOT NULL,       -- 'A', 'B', '2026-03'
    supersedes_id   uuid REFERENCES item_revision(id),
    effective_from  date NOT NULL,
    effective_to    date,
    change_reason   text,
    interchangeable_with_prior boolean NOT NULL DEFAULT true,
    UNIQUE (tenant_id, item_id, revision_code)
);
COMMENT ON COLUMN item_revision.interchangeable_with_prior IS
  'The field that decides whether existing stock can be consumed against the new revision. '
  'Getting this wrong is how a shop ships a superseded part into an aerospace order.';

-- --- Structure (bill of material) -------------------------------------
-- Header/component split, effectivity-dated, and OWNABLE BY A COMMITMENT
-- so that ETO/MRO jobs can create their own one-off structure (01 §strategy).
CREATE TABLE structure_definition (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid NOT NULL,
    code            code_key NOT NULL,
    parent_item_id  uuid NOT NULL REFERENCES item_definition(id),
    parent_revision_id uuid REFERENCES item_revision(id),
    structure_kind  text NOT NULL DEFAULT 'production'
                    CHECK (structure_kind IN ('production','engineering','planning',
                                              'costing','as_built','as_maintained')),
    owned_by_commitment_line_id uuid,     -- non-null ⇒ one-off ETO/MRO structure
    output_quantity qty NOT NULL DEFAULT 1,
    output_uom_id   uuid NOT NULL REFERENCES unit_of_measure(id),
    effective_from  date NOT NULL,
    effective_to    date,
    UNIQUE (tenant_id, code, effective_from)
);
COMMENT ON COLUMN structure_definition.structure_kind IS
  'as_built is not a variant of production — it is an OCCURRENCE-side record of what was '
  'actually consumed, materialised here for query convenience and reconciled against flows.';

CREATE TABLE structure_component (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid NOT NULL,
    structure_id    uuid NOT NULL REFERENCES structure_definition(id),
    position        int NOT NULL,
    component_item_id uuid NOT NULL REFERENCES item_definition(id),
    component_revision_id uuid REFERENCES item_revision(id),
    quantity_per    qty NOT NULL,
    quantity_uom_id uuid NOT NULL REFERENCES unit_of_measure(id),
    scrap_factor    numeric(6,5) NOT NULL DEFAULT 0,   -- expected yield loss
    is_phantom      boolean NOT NULL DEFAULT false,
    consumed_at_step_id uuid,             -- FK to process_step; backflush point
    reference_designators text[],         -- 'R12','R13' — needed for real traceability
    effective_from  date NOT NULL,
    effective_to    date,
    UNIQUE (tenant_id, structure_id, position, effective_from)
);

CREATE TABLE component_substitute (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid NOT NULL,
    structure_component_id uuid NOT NULL REFERENCES structure_component(id),
    substitute_item_id uuid NOT NULL REFERENCES item_definition(id),
    priority        int NOT NULL DEFAULT 1,
    conversion_factor qty NOT NULL DEFAULT 1,
    approval_required boolean NOT NULL DEFAULT false,
    approved_by     uuid REFERENCES party(id),
    conditions      jsonb NOT NULL DEFAULT '{}'   -- e.g. {"customers_excluded":["…"]}
);
COMMENT ON TABLE component_substitute IS
  'The ontology `substitutes` relation. High agent value: a shortage agent can propose an '
  'approved alternate instead of escalating, but only where approval_required is false or '
  'the grant covers it.';

-- --- Process (routing) ------------------------------------------------
CREATE TABLE process_definition (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid NOT NULL,
    code            code_key NOT NULL,
    produced_item_id uuid REFERENCES item_definition(id),
    owned_by_commitment_line_id uuid,     -- one-off routing for ETO
    effective_from  date NOT NULL,
    effective_to    date,
    UNIQUE (tenant_id, code, effective_from)
);

CREATE TABLE process_step (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid NOT NULL,
    process_id      uuid NOT NULL REFERENCES process_definition(id),
    step_number     int NOT NULL,
    description     text NOT NULL,
    -- Capability-based, not machine-based: the step needs a capability,
    -- the scheduler picks a resource that has it. Hard-binding a step to
    -- one machine is what makes routings brittle (ADR-011).
    required_capability text NOT NULL,
    preferred_resource_id uuid,           -- FK §4; a hint, not a constraint
    setup_time_minutes numeric(10,3) NOT NULL DEFAULT 0,
    run_time_basis  text NOT NULL DEFAULT 'per_unit'
                    CHECK (run_time_basis IN ('per_unit','per_batch','fixed')),
    run_time_minutes numeric(12,4) NOT NULL DEFAULT 0,
    queue_time_minutes numeric(10,2) NOT NULL DEFAULT 0,
    move_time_minutes  numeric(10,2) NOT NULL DEFAULT 0,
    overlap_allowed boolean NOT NULL DEFAULT false,   -- can the next step start early
    is_subcontracted boolean NOT NULL DEFAULT false,
    subcontract_party_id uuid REFERENCES party(id),
    yield_factor    numeric(6,5) NOT NULL DEFAULT 1,
    work_instruction_evidence_id uuid,    -- FK §10 — the doc the operator sees
    UNIQUE (tenant_id, process_id, step_number)
);

-- --- Quality specification --------------------------------------------
CREATE TABLE quality_specification (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid NOT NULL,
    code            code_key NOT NULL,
    item_id         uuid REFERENCES item_definition(id),
    process_step_id uuid REFERENCES process_step(id),
    effective_from  date NOT NULL,
    effective_to    date,
    UNIQUE (tenant_id, code, effective_from)
);

CREATE TABLE quality_characteristic (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid NOT NULL,
    specification_id uuid NOT NULL REFERENCES quality_specification(id),
    characteristic  text NOT NULL,
    characteristic_type text NOT NULL
                    CHECK (characteristic_type IN ('variable','attribute','visual','functional_test')),
    nominal_value   numeric(18,8),
    lower_limit     numeric(18,8),
    upper_limit     numeric(18,8),
    uom_id          uuid REFERENCES unit_of_measure(id),
    sample_plan     jsonb NOT NULL DEFAULT '{}',
    is_critical     boolean NOT NULL DEFAULT false,
    measurement_method text
);


-- =====================================================================
-- §4  PLACES & USABLE RESOURCES
-- =====================================================================
-- Hierarchy vocabulary aligns with ISA-95 (enterprise/site/area/work
-- center/work unit). Structure is ours: one self-referencing tree with a
-- level type, rather than a table per level.

CREATE TABLE location_node (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid NOT NULL,
    code            code_key NOT NULL,
    parent_id       uuid REFERENCES location_node(id),
    level_type      text NOT NULL
                    CHECK (level_type IN ('enterprise','site','area','work_center',
                                          'work_unit','storage_location','bin')),
    path            text,                     -- materialised path, e.g. 'ACME/LOU/WELD/CELL-3'
    is_stocking     boolean NOT NULL DEFAULT false,
    is_nettable     boolean NOT NULL DEFAULT true,  -- counts toward available-to-promise
    attributes      jsonb NOT NULL DEFAULT '{}',
    UNIQUE (tenant_id, code)
);
-- NOTE: modelled here as a materialised text path for readability. The
-- real choice — ltree, closure table, or recursive CTE — is an
-- implementation decision that depends on how often the hierarchy is
-- reshaped, and is deliberately left open.

CREATE TABLE resource_unit (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid NOT NULL,
    code            code_key NOT NULL,
    resource_kind   text NOT NULL
                    CHECK (resource_kind IN ('equipment','labor_pool','tooling','fixture',
                                             'container','energy_supply')),
    location_id     uuid REFERENCES location_node(id),
    capabilities    text[] NOT NULL DEFAULT '{}',  -- matched against process_step.required_capability
    capacity_uom_id uuid REFERENCES unit_of_measure(id),
    nominal_rate    numeric(18,6),          -- capacity per hour, if applicable
    cost_rate_per_hour money6,
    burden_rate_per_hour money6,
    telemetry_actor_id uuid REFERENCES party(id),  -- the equipment_actor that emits events
    attributes      jsonb NOT NULL DEFAULT '{}',
    retired_at      timestamptz,
    UNIQUE (tenant_id, code)
);
COMMENT ON COLUMN resource_unit.capabilities IS
  'Capability tags are the join between what a step needs and what a machine can do. This is '
  'what lets a scheduling agent find a legal alternate when the preferred machine goes down, '
  'instead of stalling the job.';

CREATE TABLE resource_calendar (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid NOT NULL,
    resource_id     uuid REFERENCES resource_unit(id),   -- NULL = plant default
    time_window     tstzrange NOT NULL,   -- NOT `window`: reserved keyword in SQL
    availability    text NOT NULL
                    CHECK (availability IN ('available','shift_off','holiday',
                                            'planned_maintenance','reserved')),
    capacity_factor numeric(5,4) NOT NULL DEFAULT 1,
    CONSTRAINT resource_calendar_no_overlap
        EXCLUDE USING gist (tenant_id WITH =, resource_id WITH =, time_window WITH &&)
);
COMMENT ON CONSTRAINT resource_calendar_no_overlap ON resource_calendar IS
  'Overlapping calendar windows are the classic silent scheduling bug — two rows both claim '
  'Tuesday and capacity is double-counted. Excluded at the database, not in application code.';


-- =====================================================================
-- §5  OCCURRENCES  (ontology §4 — the instance level)
-- =====================================================================

CREATE TABLE lot (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid NOT NULL,
    code            code_key NOT NULL,
    item_id         uuid NOT NULL REFERENCES item_definition(id),
    revision_id     uuid REFERENCES item_revision(id),
    origin_kind     text NOT NULL
                    CHECK (origin_kind IN ('received','produced','split','merged',
                                           'customer_returned','adjusted_in')),
    supplier_lot_code text,
    manufactured_on date,
    expires_on      date,
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, code)
);

-- Lot genealogy: the ontology `derives_from` relation. This table is the
-- entire recall story — forward (where did lot 88-C go) and backward
-- (what went into this shipment) are the same traversal.
CREATE TABLE lot_genealogy (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid NOT NULL,
    child_lot_id    uuid NOT NULL REFERENCES lot(id),
    parent_lot_id   uuid NOT NULL REFERENCES lot(id),
    relation        text NOT NULL
                    CHECK (relation IN ('consumed_into','split_from','merged_from','reworked_from')),
    quantity        qty,
    quantity_uom_id uuid REFERENCES unit_of_measure(id),
    flow_event_id   uuid,                 -- FK §8 — the event that created the link
    UNIQUE (tenant_id, child_lot_id, parent_lot_id, relation, flow_event_id)
);

CREATE TABLE serial_unit (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid NOT NULL,
    serial_code     code_key NOT NULL,
    item_id         uuid NOT NULL REFERENCES item_definition(id),
    lot_id          uuid REFERENCES lot(id),
    current_parent_serial_id uuid REFERENCES serial_unit(id),  -- as-built nesting
    born_at         timestamptz,
    retired_at      timestamptz,
    UNIQUE (tenant_id, item_id, serial_code)
);

-- --- The inventory quantum -------------------------------------------
-- Ontology axis 6. Five independent dimensions, deliberately NOT collapsed
-- into one status column, plus the belief model from Pillar 8.
CREATE TABLE inventory_quantum (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid NOT NULL,
    -- identity
    item_id         uuid NOT NULL REFERENCES item_definition(id),
    revision_id     uuid REFERENCES item_revision(id),
    lot_id          uuid REFERENCES lot(id),
    serial_id       uuid REFERENCES serial_unit(id),
    -- place
    location_id     uuid NOT NULL REFERENCES location_node(id),
    container_id    uuid REFERENCES resource_unit(id),
    -- condition
    condition       text NOT NULL DEFAULT 'usable',      -- §02 condition vocabulary
    -- ownership
    ownership       text NOT NULL DEFAULT 'owned',       -- §02 ownership vocabulary
    owner_party_id  uuid REFERENCES party(id),           -- required when not 'owned'
    -- encumbrance
    encumbrance     text NOT NULL DEFAULT 'free',        -- §02 encumbrance vocabulary
    encumbered_to_commitment_line_id uuid,               -- FK §6

    -- Quantity is DERIVED from resource_flow, not stored. This column is a
    -- maintained projection with the flow ledger as the authority; a
    -- reconciliation job proves them equal (ADR-001).
    projected_quantity qty NOT NULL DEFAULT 0,
    quantity_uom_id uuid NOT NULL REFERENCES unit_of_measure(id),

    -- Pillar 8: physical state is believed, not known.
    last_verified_at timestamptz,
    last_verified_method text
                    CHECK (last_verified_method IN ('cycle_count','physical_inventory',
                                                    'scan_confirmed','weight_check',
                                                    'vision_check','none')),
    last_verified_variance qty,           -- signed error found at last verification
    belief_confidence conf,               -- decayed since last_verified_at; see note

    created_at      timestamptz NOT NULL DEFAULT now(),
    closed_at       timestamptz,          -- quantum reaches zero and is retired

    CHECK (ownership = 'owned' OR owner_party_id IS NOT NULL),
    CHECK (encumbrance <> 'hard_reserved' OR encumbered_to_commitment_line_id IS NOT NULL)
);
COMMENT ON COLUMN inventory_quantum.belief_confidence IS
  'Modeled probability that projected_quantity matches physical reality. Decays from '
  'last_verified_at as a function of elapsed time, movement velocity, and this item/location''s '
  'historical count variance. Keep the model simple and empirically calibrated — a controller '
  'has to be able to understand it. This column is what lets a replenishment agent reason about '
  'the expected cost of being wrong instead of trusting an integer that nobody has checked '
  'since March.';

CREATE INDEX inventory_quantum_lookup_idx
    ON inventory_quantum (tenant_id, item_id, location_id, condition, ownership, encumbrance)
    WHERE closed_at IS NULL;


-- =====================================================================
-- §6  COMMITMENTS  (ontology §5 — one shape, thirteen document kinds)
-- =====================================================================

CREATE TABLE commitment (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid NOT NULL,
    code            code_key NOT NULL,
    kind            text NOT NULL,        -- §02 commitment kinds — selects VALIDATION, not a table
    direction       text NOT NULL CHECK (direction IN ('inbound','outbound','internal')),
    counterparty_id uuid REFERENCES party(id),           -- NULL for internal
    firmness        text NOT NULL DEFAULT 'speculative', -- §02 firmness ladder
    currency        char(3) NOT NULL DEFAULT 'USD',
    incoterms       text,
    payment_terms   text,
    supersedes_id   uuid REFERENCES commitment(id),      -- revisions of a quote/order
    external_reference text,              -- the customer's PO number, etc.
    attributes      jsonb NOT NULL DEFAULT '{}',
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, code)
);
COMMENT ON TABLE commitment IS
  'Quote, sales order, PO, blanket release, forecast, work-order demand, transfer, RMA and '
  'capacity reservation are all this table. They differ in firmness and in which policy rules '
  'apply. Thirteen document types, one shape — which is why an agent needs to learn one query '
  'to answer "what is committed against this item".';

CREATE TABLE commitment_line (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid NOT NULL,
    commitment_id   uuid NOT NULL REFERENCES commitment(id),
    line_number     int NOT NULL,
    -- what is promised: a specification, or (for reservations) an occurrence
    item_id         uuid REFERENCES item_definition(id),
    revision_id     uuid REFERENCES item_revision(id),
    reserved_quantum_id uuid REFERENCES inventory_quantum(id),
    resource_id     uuid REFERENCES resource_unit(id),   -- capacity reservations
    description     text,                                -- ETO lines may have no item yet
    -- how much, when, at what price
    quantity        qty NOT NULL,
    quantity_uom_id uuid NOT NULL REFERENCES unit_of_measure(id),
    promise_window  tstzrange NOT NULL,
    requested_at    timestamptz,
    unit_price      money6,
    price_basis     text,                 -- 'contract' | 'quoted' | 'list_less_discount' | …
    -- ETO/MRO: this line may OWN its structure and routing (01 §strategy)
    owns_structure_id uuid REFERENCES structure_definition(id),
    owns_process_id   uuid REFERENCES process_definition(id),
    parent_line_id  uuid REFERENCES commitment_line(id), -- pegging: demand → supply
    attributes      jsonb NOT NULL DEFAULT '{}',
    cancelled_at    timestamptz,
    UNIQUE (tenant_id, commitment_id, line_number),
    CHECK (item_id IS NOT NULL OR resource_id IS NOT NULL OR description IS NOT NULL)
);
-- deferred FKs from §3/§5 now resolvable
ALTER TABLE structure_definition ADD CONSTRAINT structure_owned_by_line_fk
    FOREIGN KEY (owned_by_commitment_line_id) REFERENCES commitment_line(id);
ALTER TABLE process_definition ADD CONSTRAINT process_owned_by_line_fk
    FOREIGN KEY (owned_by_commitment_line_id) REFERENCES commitment_line(id);
ALTER TABLE inventory_quantum ADD CONSTRAINT quantum_encumbered_to_fk
    FOREIGN KEY (encumbered_to_commitment_line_id) REFERENCES commitment_line(id);

COMMENT ON COLUMN commitment_line.parent_line_id IS
  'Pegging. A work-order demand line pegged to the sales-order line it exists to satisfy, a '
  'purchase line pegged to the job that needs it. This chain is what makes "why are we buying '
  'this" answerable in one hop — the question an expediting agent asks constantly.';


-- =====================================================================
-- §7  JOBS  (occurrence side of process definitions)
-- =====================================================================

CREATE TABLE job (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid NOT NULL,
    code            code_key NOT NULL,
    produced_item_id uuid REFERENCES item_definition(id),
    revision_id     uuid REFERENCES item_revision(id),
    structure_id    uuid REFERENCES structure_definition(id),   -- snapshot at release
    process_id      uuid REFERENCES process_definition(id),     -- snapshot at release
    demand_line_id  uuid REFERENCES commitment_line(id),        -- what it exists to satisfy
    quantity_ordered qty NOT NULL,
    quantity_uom_id uuid NOT NULL REFERENCES unit_of_measure(id),
    state           text NOT NULL DEFAULT 'draft',   -- §02 job states
    planned_window  tstzrange,
    released_at     timestamptz,
    closed_at       timestamptz,
    UNIQUE (tenant_id, code)
);
COMMENT ON COLUMN job.structure_id IS
  'Snapshotted at release. If the engineering BOM changes mid-job, the job keeps the structure '
  'it was released against unless someone explicitly re-releases it — and both facts are '
  'visible. Silently following the live BOM is how mid-job engineering changes corrupt costing.';

CREATE TABLE job_operation (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid NOT NULL,
    job_id          uuid NOT NULL REFERENCES job(id),
    step_id         uuid REFERENCES process_step(id),   -- planned step; NULL if ad hoc
    sequence        int NOT NULL,
    -- PLANNED (from the specification)
    planned_resource_id uuid REFERENCES resource_unit(id),
    planned_setup_minutes numeric(10,3),
    planned_run_minutes   numeric(12,4),
    planned_window  tstzrange,
    -- ACTUAL (the occurrence) — everything else is summed from flows
    actual_resource_id uuid REFERENCES resource_unit(id),
    state           text NOT NULL DEFAULT 'not_started',  -- §02 operation states
    started_at      timestamptz,
    completed_at    timestamptz,
    UNIQUE (tenant_id, job_id, sequence)
);
COMMENT ON TABLE job_operation IS
  'The planned/actual column pairs are the whole point (ontology axis 1 + 2). Variance analysis '
  'is a subtraction on this table joined to summed flows — not a separate reporting project.';


-- =====================================================================
-- §8  THE FLOW LEDGER  — the spine (Pillar 1, ontology §6)
-- =====================================================================
-- Append-only. Hash-chained. Nothing here is ever UPDATEd or DELETEd.
-- Corrections are compensating events. Every projection in this schema
-- is derivable from these two tables.

CREATE TABLE flow_event (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid NOT NULL,
    sequence_no     bigint GENERATED ALWAYS AS IDENTITY,   -- total order per install
    event_type      text NOT NULL,        -- §02 flow taxonomy

    -- Bitemporality (Pillar 7)
    occurred_at     timestamptz NOT NULL, -- valid time: when it happened in the world
    recorded_at     timestamptz NOT NULL DEFAULT now(),    -- transaction time: when we learned

    -- Attribution: who did it. An agent is a party like any other.
    actor_id        uuid NOT NULL REFERENCES party(id),
    on_behalf_of_id uuid REFERENCES party(id),  -- delegation is explicit, never impersonation
    agent_run_id    uuid,                 -- ties a batch of agent actions together

    -- Causation and correlation: the explanation chain (ontology `causes`)
    caused_by_event_id uuid REFERENCES flow_event(id),
    correlation_id  uuid NOT NULL,        -- the business transaction this belongs to
    proposal_id     uuid,                 -- FK §10 — non-null if an agent proposed it

    -- Reversal (never delete)
    compensates_event_id uuid REFERENCES flow_event(id),
    reason_code     text,                 -- governed vocabulary per event family

    payload         jsonb NOT NULL DEFAULT '{}',
    rationale       text,                 -- natural-language why; agents MUST populate

    -- Tamper evidence
    prev_hash       bytea,
    entry_hash      bytea NOT NULL,       -- digest(prev_hash || canonical(row))

    CHECK (occurred_at <= recorded_at + interval '1 day')  -- guard against clock nonsense
);
CREATE INDEX flow_event_correlation_idx ON flow_event (tenant_id, correlation_id);
CREATE INDEX flow_event_actor_time_idx  ON flow_event (tenant_id, actor_id, recorded_at DESC);
CREATE INDEX flow_event_type_time_idx   ON flow_event (tenant_id, event_type, occurred_at DESC);

COMMENT ON COLUMN flow_event.rationale IS
  'For human actors this is optional context. For software_agent actors it is mandatory and is '
  'the text a reviewer reads in the exception queue. An agent action with no stated reasoning '
  'is not auditable and should not be committable.';

-- Immutability enforced at the database, not by convention.
-- Shown as rules for brevity; in a real build these must be BEFORE
-- triggers that RAISE EXCEPTION — a rule DO INSTEAD NOTHING silently
-- swallows the write, and a silent no-op is worse than a hard error.
CREATE RULE flow_event_no_update AS ON UPDATE TO flow_event DO INSTEAD NOTHING;
CREATE RULE flow_event_no_delete AS ON DELETE TO flow_event DO INSTEAD NOTHING;


-- The signed resource movements. REA-aligned duality: rows come in
-- balanced pairs sharing an event, one decrement and one increment.
CREATE TABLE resource_flow (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid NOT NULL,
    event_id        uuid NOT NULL REFERENCES flow_event(id),
    pair_key        uuid NOT NULL,        -- links the two halves of a duality pair

    resource_kind   text NOT NULL
                    CHECK (resource_kind IN ('material','equipment','labor','energy',
                                             'information','money')),
    direction       smallint NOT NULL CHECK (direction IN (-1, 1)),  -- decrement / increment

    -- material half
    quantum_id      uuid REFERENCES inventory_quantum(id),
    item_id         uuid REFERENCES item_definition(id),
    lot_id          uuid REFERENCES lot(id),
    serial_id       uuid REFERENCES serial_unit(id),
    location_id     uuid REFERENCES location_node(id),
    -- capacity/labor half
    resource_id     uuid REFERENCES resource_unit(id),
    job_operation_id uuid REFERENCES job_operation(id),
    -- money half
    account_code    text,

    quantity        qty NOT NULL,
    quantity_uom_id uuid NOT NULL REFERENCES unit_of_measure(id),

    -- Cost rides the flow (Pillar 1 / ontology §6). This is why costing is
    -- not a separate subsystem that has to be reconciled.
    unit_cost       money6,
    extended_cost   money6,
    cost_component  text                  -- 'material'|'labor'|'burden'|'outside'|'freight'
                    CHECK (cost_component IN ('material','labor','burden','outside','freight',
                                              'variance','revaluation')),

    -- ontology `fulfills`
    fulfills_line_id uuid REFERENCES commitment_line(id),

    party_id        uuid REFERENCES party(id),   -- the counterparty of this half
    CHECK (quantity >= 0)                        -- sign lives in `direction`, not the number
);
CREATE INDEX resource_flow_quantum_idx ON resource_flow (tenant_id, quantum_id);
CREATE INDEX resource_flow_pair_idx    ON resource_flow (tenant_id, pair_key);
CREATE INDEX resource_flow_fulfils_idx ON resource_flow (tenant_id, fulfills_line_id);

CREATE RULE resource_flow_no_update AS ON UPDATE TO resource_flow DO INSTEAD NOTHING;
CREATE RULE resource_flow_no_delete AS ON DELETE TO resource_flow DO INSTEAD NOTHING;

COMMENT ON TABLE resource_flow IS
  'Every physical and financial fact in the business is rows in this table. Inventory balance, '
  'WIP value, job cost, supplier liability, capacity consumed, and operation completion are all '
  'aggregations over it. Invariant (enforced in the command layer, asserted by a reconciliation '
  'job): for any pair_key, SUM(direction * extended_cost) = 0 for transfers, and net non-zero '
  'only where value is genuinely created or destroyed (production, scrap, revaluation).';


-- --- Projections ------------------------------------------------------
-- Read models. Rebuildable from the ledger at any time; that property is
-- the entire safety argument for letting agents operate on this system.

CREATE VIEW quantum_balance AS
SELECT  q.tenant_id, q.id AS quantum_id, q.item_id, q.location_id,
        q.condition, q.ownership, q.encumbrance,
        COALESCE(SUM(f.direction * f.quantity), 0) AS quantity_on_hand,
        MAX(e.occurred_at)                          AS last_movement_at,
        q.belief_confidence
FROM    inventory_quantum q
LEFT JOIN resource_flow f ON f.quantum_id = q.id
LEFT JOIN flow_event    e ON e.id = f.event_id
GROUP BY q.tenant_id, q.id;

CREATE VIEW commitment_line_fulfilment AS
SELECT  cl.tenant_id, cl.id AS commitment_line_id, cl.quantity AS quantity_committed,
        COALESCE(SUM(f.quantity), 0)                       AS quantity_fulfilled,
        cl.quantity - COALESCE(SUM(f.quantity), 0)         AS quantity_open
FROM    commitment_line cl
LEFT JOIN resource_flow f ON f.fulfills_line_id = cl.id
GROUP BY cl.tenant_id, cl.id, cl.quantity;

-- Materialised in a real build, with the reconciliation job proving it
-- against the ledger. Kept as a view here to make the derivation explicit.
CREATE VIEW job_cost_actual AS
SELECT  j.tenant_id, j.id AS job_id, f.cost_component,
        SUM(f.direction * f.extended_cost) AS cost_to_date
FROM    job j
JOIN    job_operation o ON o.job_id = j.id
JOIN    resource_flow f ON f.job_operation_id = o.id
GROUP BY j.tenant_id, j.id, f.cost_component;


-- =====================================================================
-- §9  QUALITY OCCURRENCES
-- =====================================================================

CREATE TABLE inspection (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid NOT NULL,
    specification_id uuid REFERENCES quality_specification(id),
    subject_uri     text NOT NULL REFERENCES entity_uri(uri),  -- lot, serial, quantum, operation
    inspected_by    uuid NOT NULL REFERENCES party(id),        -- may be an agent (vision check)
    inspected_at    timestamptz NOT NULL DEFAULT now(),
    disposition     text NOT NULL DEFAULT 'pending',           -- §02 disposition vocabulary
    flow_event_id   uuid REFERENCES flow_event(id)
);

CREATE TABLE inspection_result (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid NOT NULL,
    inspection_id   uuid NOT NULL REFERENCES inspection(id),
    characteristic_id uuid NOT NULL REFERENCES quality_characteristic(id),
    measured_value  numeric(18,8),
    attribute_pass  boolean,
    uom_id          uuid REFERENCES unit_of_measure(id),
    is_conforming   boolean NOT NULL,
    measured_by     uuid REFERENCES party(id),
    evidence_id     uuid                  -- FK §10 — the gauge trace or the photo
);

CREATE TABLE nonconformance (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid NOT NULL,
    code            code_key NOT NULL,
    subject_uri     text NOT NULL REFERENCES entity_uri(uri),
    severity        text NOT NULL CHECK (severity IN ('critical','major','minor','cosmetic')),
    detected_at     timestamptz NOT NULL DEFAULT now(),
    detected_by     uuid NOT NULL REFERENCES party(id),
    root_cause_term text,                 -- governed vocabulary; often an agent assertion first
    disposition     text,
    corrective_action_state text,         -- §02 corrective action states
    closed_at       timestamptz,
    UNIQUE (tenant_id, code)
);


-- =====================================================================
-- §10  EPISTEMICS — Tiers C and B  (Pillar 2)
-- =====================================================================
-- Where agents do their reading and most of their writing.

-- Tier C: raw immutable observation. Content-addressed.
CREATE TABLE evidence (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid NOT NULL,
    content_hash    bytea NOT NULL,       -- sha256 of the bytes
    media_type      text NOT NULL,
    source_kind     text NOT NULL
                    CHECK (source_kind IN ('supplier_document','customer_document','email',
                                           'scan','sensor_sample','photograph','transcript',
                                           'external_system_payload','human_note')),
    received_at     timestamptz NOT NULL DEFAULT now(),
    received_from   uuid REFERENCES party(id),
    origin_detail   jsonb NOT NULL DEFAULT '{}',   -- sender address, device id, endpoint
    storage_uri     text NOT NULL,
    -- SECURITY: content from here is DATA, never instructions. See 07_threat_model.md.
    trust_tier      text NOT NULL DEFAULT 'untrusted'
                    CHECK (trust_tier IN ('untrusted','semi_trusted','trusted')),
    UNIQUE (tenant_id, content_hash)
);
COMMENT ON COLUMN evidence.trust_tier IS
  'A vendor PDF is untrusted input that may contain adversarial text. trust_tier gates which '
  'downstream automation may act on assertions derived from it — untrusted evidence can never '
  'auto-commit an R3 command regardless of extraction confidence.';

CREATE TABLE evidence_region (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid NOT NULL,
    evidence_id     uuid NOT NULL REFERENCES evidence(id),
    page_number     int,
    bounding_box    numeric(9,6)[],       -- normalised [x0,y0,x1,y1]
    extracted_text  text,
    time_offset_ms  int                   -- for transcripts / sensor streams
);
COMMENT ON TABLE evidence_region IS
  'The citation target. "Why does the system think the PO number is 4471" resolves to a '
  'rectangle on a page, not to a model''s recollection.';

-- Tier B: interpreted claims. Append-only; supersede, never overwrite.
CREATE TABLE assertion (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid NOT NULL,
    subject_uri     text NOT NULL REFERENCES entity_uri(uri),
    predicate       text NOT NULL,        -- a registered semantic_term
    value_json      jsonb NOT NULL,
    value_uom_id    uuid REFERENCES unit_of_measure(id),

    asserted_by     uuid NOT NULL REFERENCES party(id),
    method          text NOT NULL,        -- §02 assertion methods
    confidence      conf NOT NULL,
    evidence_region_id uuid REFERENCES evidence_region(id),
    evidence_id     uuid REFERENCES evidence(id),
    model_reference text,                 -- model id + config hash, for agent assertions

    -- bitemporal
    valid_from      timestamptz NOT NULL DEFAULT now(),
    valid_to        timestamptz,
    recorded_at     timestamptz NOT NULL DEFAULT now(),

    resolution_status text NOT NULL DEFAULT 'current',   -- §02 resolution statuses
    superseded_by_id uuid REFERENCES assertion(id),
    promoted_event_id uuid REFERENCES flow_event(id),    -- set when promoted into Tier A

    CHECK (method NOT IN ('document_extraction_model','classification_model','inference_model')
           OR model_reference IS NOT NULL)
);
CREATE INDEX assertion_subject_idx ON assertion (tenant_id, subject_uri, predicate)
    WHERE resolution_status = 'current';
CREATE INDEX assertion_low_conf_idx ON assertion (tenant_id, confidence)
    WHERE resolution_status = 'current' AND confidence < 0.90;

COMMENT ON TABLE assertion IS
  'An assertion is not a fact — it is a claim, by a named actor, with a stated confidence and a '
  'pointer to what it rests on. Conflicting assertions coexist; a resolution policy picks the '
  'current belief and the losers stay visible. This table is the reason the system can be '
  'usefully uncertain instead of confidently wrong.';

-- Resolution policy: how competing assertions about the same subject
-- and predicate are arbitrated. Data, not code — so it can be replayed.
CREATE TABLE belief_resolution_policy (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid NOT NULL,
    predicate       text NOT NULL,
    strategy        text NOT NULL
                    CHECK (strategy IN ('highest_confidence','most_recent','method_precedence',
                                        'require_agreement','human_wins')),
    method_precedence text[],             -- ordered, for 'method_precedence'
    min_confidence_to_hold conf NOT NULL DEFAULT 0.500,
    UNIQUE (tenant_id, predicate)
);

-- --- The proposal protocol (Pillar 3) ---------------------------------
CREATE TABLE proposal (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid NOT NULL,
    proposed_by     uuid NOT NULL REFERENCES party(id),
    agent_run_id    uuid,
    command_name    text NOT NULL,
    arguments       jsonb NOT NULL,
    rationale       text NOT NULL,        -- mandatory; this is what a human reads
    confidence      conf NOT NULL,

    -- Declared before execution; the policy engine decides on THIS.
    reversibility   text NOT NULL
                    CHECK (reversibility IN ('R0_read_only','R1_reversible','R2_compensable',
                                             'R3_externally_visible','R4_physically_irreversible')),
    blast_radius    jsonb NOT NULL,       -- {records, monetary_magnitude, downstream_lines,
                                          --  external_parties_notified}
    dry_run_result  jsonb,                -- what the command says it would do

    supporting_assertion_ids uuid[] NOT NULL DEFAULT '{}',
    alternatives_considered  jsonb NOT NULL DEFAULT '[]',

    status          text NOT NULL DEFAULT 'pending_policy',   -- §02 proposal statuses
    matched_policy_id uuid REFERENCES policy_rule(id),
    decided_by      uuid REFERENCES party(id),
    decided_at      timestamptz,
    decision_note   text,
    committed_event_id uuid REFERENCES flow_event(id),
    expires_at      timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX proposal_queue_idx ON proposal (tenant_id, status, created_at)
    WHERE status = 'awaiting_human';

ALTER TABLE flow_event ADD CONSTRAINT flow_event_proposal_fk
    FOREIGN KEY (proposal_id) REFERENCES proposal(id);

COMMENT ON TABLE proposal IS
  'The intent layer that conventional ERPs lack entirely. Every agent action exists here first, '
  'with its reasoning, its evidence, its declared blast radius, and the alternatives it rejected. '
  'A new agent can run in shadow mode writing proposals nobody commits, and be scored against '
  'what humans actually did — which is how you earn authority rather than being granted it.';

-- Deferred FKs from earlier sections
ALTER TABLE party_qualification   ADD CONSTRAINT pq_evidence_fk    FOREIGN KEY (evidence_id) REFERENCES evidence(id);
ALTER TABLE process_step          ADD CONSTRAINT ps_evidence_fk    FOREIGN KEY (work_instruction_evidence_id) REFERENCES evidence(id);
ALTER TABLE inspection_result     ADD CONSTRAINT ir_evidence_fk    FOREIGN KEY (evidence_id) REFERENCES evidence(id);
ALTER TABLE lot_genealogy         ADD CONSTRAINT lg_event_fk       FOREIGN KEY (flow_event_id) REFERENCES flow_event(id);
ALTER TABLE structure_component   ADD CONSTRAINT sc_step_fk        FOREIGN KEY (consumed_at_step_id) REFERENCES process_step(id);
ALTER TABLE process_step          ADD CONSTRAINT ps_pref_res_fk    FOREIGN KEY (preferred_resource_id) REFERENCES resource_unit(id);


-- =====================================================================
-- §11  AGENT OPERATIONS
-- =====================================================================

-- The command registry. Generated from the ontology; this IS the agent
-- tool surface (Pillar 5). If a command is not here, it cannot be called.
CREATE TABLE command_definition (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid,                 -- NULL = system-defined
    command_name    code_key NOT NULL,
    summary         text NOT NULL,        -- the tool description agents read
    argument_schema jsonb NOT NULL,       -- JSON Schema
    preconditions   jsonb NOT NULL DEFAULT '[]',
    postconditions  jsonb NOT NULL DEFAULT '[]',
    invariants      jsonb NOT NULL DEFAULT '[]',
    reversibility   text NOT NULL,
    emits_event_types text[] NOT NULL DEFAULT '{}',
    minimum_band    text NOT NULL DEFAULT 'act_within_limits',
    supports_dry_run boolean NOT NULL DEFAULT true,
    UNIQUE (tenant_id, command_name)
);

-- The exception queue: the primary human interface (05_agent_layer.md).
CREATE TABLE exception_item (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid NOT NULL,
    exception_type  text NOT NULL,        -- 'unmatched_invoice','shortage_risk','late_operation'…
    subject_uri     text REFERENCES entity_uri(uri),
    detected_by     uuid NOT NULL REFERENCES party(id),
    detected_at     timestamptz NOT NULL DEFAULT now(),
    severity        text NOT NULL CHECK (severity IN ('critical','high','medium','low')),
    estimated_impact money6,              -- ranking the queue by money, not by age
    summary         text NOT NULL,
    proposal_id     uuid REFERENCES proposal(id),   -- the suggested fix, if any
    state           text NOT NULL DEFAULT 'open'
                    CHECK (state IN ('open','acknowledged','in_progress','resolved',
                                     'dismissed','auto_resolved')),
    resolved_at     timestamptz,
    resolved_by     uuid REFERENCES party(id)
);
CREATE INDEX exception_queue_idx ON exception_item (tenant_id, state, severity, estimated_impact DESC)
    WHERE state IN ('open','acknowledged');

-- Calibration: is the confidence number meaningful? (05_agent_layer.md)
CREATE TABLE agent_outcome (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       uuid NOT NULL,
    agent_id        uuid NOT NULL REFERENCES party(id),
    proposal_id     uuid REFERENCES proposal(id),
    assertion_id    uuid REFERENCES assertion(id),
    stated_confidence conf NOT NULL,
    human_verdict   text NOT NULL
                    CHECK (human_verdict IN ('agreed','corrected','rejected','not_reviewed')),
    corrected_value jsonb,
    reviewed_by     uuid REFERENCES party(id),
    reviewed_at     timestamptz,
    review_basis    text NOT NULL
                    CHECK (review_basis IN ('mandatory_approval','random_audit_sample',
                                            'escalation','downstream_failure'))
);
COMMENT ON TABLE agent_outcome IS
  'Random-audit rows are the load-bearing ones. Reviewing only what humans already had to '
  'approve produces a biased sample and a confidence curve that flatters the agent. A standing '
  'random sample of AUTO-COMMITTED actions is the only honest calibration signal — and it is '
  'the evidence used to raise or revoke an authority_grant.';


-- =====================================================================
-- §12  WHAT IS DELIBERATELY ABSENT
-- =====================================================================
-- * General ledger / chart of accounts — projected from resource_flow,
--   posted to the incumbent finance system in v1 (ADR-014).
-- * Payroll, HR, CRM — out of scope (00_principles.md).
-- * Scheduling solver state — the schema supports finite capacity
--   (resource_calendar, capabilities, job_operation windows) but the
--   solver and its scenario tables are a later module (ADR-011).
-- * Any user-defined column. Extensions go through attribute_definition.
-- * Stored quantity counters anywhere except inventory_quantum's
--   explicitly-labelled projection column.
