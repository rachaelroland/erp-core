-- SPDX-License-Identifier: Apache-2.0
-- Copyright 2026 Applied Industrials
-- =====================================================================
-- 004 — register the quality vocabularies, and the thirteen registered
--       vocabularies' terms that §02 defines and the registry never received
--
-- docs/02_taxonomies.md §"Quality taxonomy" has defined a disposition
-- vocabulary since the first commit. Nothing carried it: it was named in
-- a DDL comment on inspection.disposition ("§02 disposition vocabulary")
-- and nowhere else. Not in semantic_term, so an agent reading the
-- registry to ground the word "disposition" found nothing. Not in a
-- CHECK, so any value at all could be written by direct SQL.
--
-- Corrective action state had the same shape, one column away.
--
-- Found by an outside reviewer (Chip Lynch, 2026-09-02) who noticed that
-- `accept_with_deviation` appears in the markdown and in no SQL artefact.
-- The check that should have found it first is extended in the same
-- change: see tests/test_ontology_drift.py, which now parses §02 and
-- fails when a documented vocabulary has no declared home.
--
-- Extending that check turned up the quieter half of the same drift: the
-- four vocabularies the registry DID carry were subsets of what §02
-- defines. Thirteen documented terms were never seeded, so an agent
-- reading semantic_term to ground "what condition can this inventory be
-- in" got seven of the eleven states the document promises — missing
-- in_rework and nonconforming_use_as_is, both of which a shop floor uses
-- daily. Every check passed, because the vocabulary existed. Nothing
-- compared its contents to the document. Those terms are added here too.
--
-- Definitions are written for an agent to read, per the §02 naming rule
-- that the definition is the string the agent, the validator and the UI
-- all share. The CHECK constraints are NOT written here — they are
-- generated from these rows by db/vocabulary_constraints.sql, which is
-- the only way the registry stays authoritative. Re-run that file after
-- this migration; widening a vocabulary never rejects existing data.
-- =====================================================================

-- This migration is a BACKFILL. It writes one row per term per tenant
-- already present in semantic_term, which means it does nothing at all on
-- a database that has not been seeded yet — and that is correct: on the
-- fresh path the generator seeds the full vocabulary, this file's terms
-- included, so there is nothing to backfill.
--
-- Doing nothing quietly is still the shape of a silent failure, so say so.
-- The first version of this guard raised instead, and test_migrations.py
-- caught it within the minute: that test applies migrations to an empty
-- scratch database to prove they reproduce the reference schema, which is
-- exactly the case where an empty registry is expected.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM semantic_term) THEN
        RAISE NOTICE
            '004: semantic_term is empty, so nothing to backfill. Correct on '
            'a fresh database; on an existing one it means the seeder never ran.';
    END IF;
END $$;

INSERT INTO semantic_term (id, tenant_id, vocabulary, term, definition)
SELECT gen_random_uuid(), t.tenant_id, v.vocabulary, v.term, v.definition
FROM (SELECT DISTINCT tenant_id FROM semantic_term) t
CROSS JOIN (VALUES
    ('quality_disposition', 'pending',
     'Inspected or raised, no disposition decision made yet. The default; material stays unavailable.'),
    ('quality_disposition', 'accept',
     'Conforms to specification. No deviation recorded and no further quality action.'),
    ('quality_disposition', 'accept_with_deviation',
     'Does not conform, accepted anyway against an approved, recorded deviation. The deviation is the authority; without one this is not a valid disposition.'),
    ('quality_disposition', 'rework',
     'Nonconforming and repairable to full specification. Returns to a routing operation.'),
    ('quality_disposition', 'repair',
     'Nonconforming, restored to a usable but not fully conforming state. Distinct from rework because the result still deviates.'),
    ('quality_disposition', 'scrap',
     'Nonconforming and not recoverable. Consumed out of inventory against a scrap reason code.'),
    ('quality_disposition', 'return_to_supplier',
     'Nonconforming supplier material sent back. Title and any debit follow the return.'),
    ('quality_disposition', 'use_as_is_with_concession',
     'Does not conform, used without correction under a customer-granted concession. The concession is external authority, unlike a deviation, which is ours.'),

    ('corrective_action_state', 'raised',
     'A corrective action has been opened against a nonconformance. No containment yet.'),
    ('corrective_action_state', 'contained',
     'Immediate spread stopped — suspect material quarantined, process halted. Says nothing about cause.'),
    ('corrective_action_state', 'root_cause_analysis',
     'Investigation in progress to establish cause. Containment is assumed already done.'),
    ('corrective_action_state', 'action_planned',
     'A cause is established and a corrective action is defined but not yet carried out.'),
    ('corrective_action_state', 'action_implemented',
     'The corrective action has been carried out. Effectiveness is not yet demonstrated.'),
    ('corrective_action_state', 'effectiveness_verified',
     'Evidence shows the action worked — the failure mode has not recurred over a stated window.'),
    ('corrective_action_state', 'closed',
     'Verified and formally closed. Reopening requires a new corrective action, never an edit to this one.'),

    -- Documented in §02 since the first commit, never seeded.
    ('inventory_condition', 'nonconforming_use_as_is',
     'Failed inspection, dispositioned for use without correction. Available, and the deviation stays attached to the quantum.'),
    ('inventory_condition', 'in_rework',
     'Physically in a rework operation now. Distinct from awaiting_rework, which is queued and not yet touched.'),
    ('inventory_condition', 'expired_shelf_life',
     'Past its shelf-life date. Not issuable without a documented re-test or an approved extension.'),
    ('inventory_condition', 'sample_destroyed',
     'Consumed by destructive test or sampling. Carried so the quantity reconciles; never available.'),

    ('inventory_ownership', 'vendor_owned_at_our_site',
     'Vendor holds title to material sitting on our floor, and it is not consignment — no consumption trigger transfers title.'),
    ('inventory_ownership', 'in_transit_title_theirs',
     'Physically in transit and title has not passed to us. Not our inventory value, but we track it because it is inbound supply.'),

    ('inventory_encumbrance', 'pledged_as_collateral',
     'Pledged against a financing arrangement. Physically available, legally not free to dispose of.'),

    ('assertion_method', 'human_reviewed',
     'A person examined a claim produced by something else and accepted it. Higher trust than the underlying method, and the reviewer is recorded.'),
    ('assertion_method', 'rfid_read',
     'Read from an RFID tag. Machine-read like a barcode, with a weaker guarantee of line of sight to the right item.'),
    ('assertion_method', 'sensor_reading',
     'Taken from an instrument — scale, gauge, probe, meter. Carries the device identity so a drifting instrument is traceable.'),
    ('assertion_method', 'classification_model',
     'Produced by a model assigning a category rather than extracting a value; requires model_reference.'),
    ('assertion_method', 'inference_model',
     'Produced by a model reasoning beyond what any source states outright. The weakest method, and never eligible for auto-commit.'),
    ('assertion_method', 'external_system_sync',
     'Received from another system of record. Trust is the trust of that system, which is stated at the integration, not here.')
) AS v(vocabulary, term, definition)
WHERE NOT EXISTS (
    SELECT 1 FROM semantic_term s
     WHERE s.tenant_id = t.tenant_id
       AND s.vocabulary = v.vocabulary
       AND s.term = v.term
);
