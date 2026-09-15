-- SPDX-License-Identifier: Apache-2.0
-- Copyright 2026 Applied Industrials
-- =====================================================================
-- vocabulary_constraints.sql — generate CHECK constraints FROM the registry
--
-- Pillar 4 says the ontology is the single source of meaning and that
-- validation is generated from it. docs/02 states the intent plainly:
-- "the CHECK lists in the DDL duplicate semantic_term as a readability aid
--  — in a build, the registry is authoritative and the checks are generated
--  from it."
--
-- Until this file existed, that was aspiration. Three columns the ontology
-- claims to govern (inventory_quantum.condition / ownership / encumbrance)
-- had no enum CHECK at all: the command layer validated them, but any direct
-- SQL write could store anything. tests/test_ontology_drift.py demonstrated
-- it by writing 'banana_hold' straight into a condition column.
--
-- Running this AFTER seeding semantic_term makes the registry authoritative
-- in the database itself. Re-run whenever the vocabulary changes; adding a
-- term is then a registry insert plus this script, never hand-edited DDL.
-- =====================================================================

DO $$
DECLARE
    spec record;
    terms text;
    constraint_name text;
BEGIN
    FOR spec IN
        SELECT * FROM (VALUES
            ('inventory_quantum', 'condition',   'inventory_condition'),
            ('inventory_quantum', 'ownership',   'inventory_ownership'),
            ('inventory_quantum', 'encumbrance', 'inventory_encumbrance'),
            ('assertion',         'method',      'assertion_method'),
            -- Added with migration 004. Both columns carried a comment
            -- naming the §02 vocabulary and no constraint at all.
            ('inspection',        'disposition', 'quality_disposition'),
            ('nonconformance',    'disposition', 'quality_disposition'),
            ('nonconformance',    'corrective_action_state', 'corrective_action_state')
        ) AS t(table_name, column_name, vocabulary)
    LOOP
        SELECT string_agg(quote_literal(term), ', ' ORDER BY term)
          INTO terms
          FROM semantic_term
         WHERE vocabulary = spec.vocabulary AND deprecated_at IS NULL;

        -- No terms registered means the vocabulary is not seeded yet.
        -- Skip rather than write a CHECK that forbids every value —
        -- an empty allowlist would lock the table entirely.
        IF terms IS NULL THEN
            RAISE NOTICE 'vocabulary % has no terms; skipping %.%',
                spec.vocabulary, spec.table_name, spec.column_name;
            CONTINUE;
        END IF;

        constraint_name := format('%s_%s_vocab', spec.table_name, spec.column_name);

        EXECUTE format('ALTER TABLE public.%I DROP CONSTRAINT IF EXISTS %I',
                       spec.table_name, constraint_name);
        EXECUTE format(
            'ALTER TABLE public.%I ADD CONSTRAINT %I CHECK (%I = ANY (ARRAY[%s]))',
            spec.table_name, constraint_name, spec.column_name, terms);

        RAISE NOTICE 'constrained %.% to % registered terms',
            spec.table_name, spec.column_name,
            (SELECT count(*) FROM semantic_term
              WHERE vocabulary = spec.vocabulary AND deprecated_at IS NULL);
    END LOOP;
END $$;
