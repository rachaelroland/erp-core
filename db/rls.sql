-- SPDX-License-Identifier: Apache-2.0
-- Copyright 2026 Applied Industrials
-- =====================================================================
-- rls.sql — row-level tenant isolation (ADR-012)
--
-- ADR-012 chose row-level multi-tenancy over a database per tenant, and
-- named the cost honestly: "a single missed RLS policy is a cross-tenant
-- data leak, which for an ERP is an extinction-level incident." This file
-- is the mitigation the ADR promised, plus the CI test that keeps it true.
--
-- THE TRAP THIS AVOIDS: superusers and roles with BYPASSRLS ignore RLS
-- entirely, and a table's OWNER ignores it unless FORCE is set. Our
-- migration role is both. So a config-only check would happily report
-- "RLS enabled" on a database where every query still returns every
-- tenant's rows. Hence: FORCE on every table, a separate non-superuser
-- application role, and a test that proves isolation by actually reading
-- across a tenant boundary rather than by inspecting catalogs.
--
-- Apply after 03_schema.sql. Re-run after any schema change: the DO block
-- picks up new tables automatically, and tests/test_rls.py fails the build
-- if one was missed.
-- =====================================================================

-- The tenant in scope for this session. Unset means NULL, which matches no
-- row — default deny. A connection that forgets to set it sees nothing
-- rather than seeing everything.
CREATE OR REPLACE FUNCTION current_tenant_id() RETURNS uuid
LANGUAGE sql STABLE AS $$
    SELECT NULLIF(current_setting('agent_erp.tenant_id', true), '')::uuid
$$;

COMMENT ON FUNCTION current_tenant_id() IS
  'Reads the agent_erp.tenant_id GUC. Returns NULL when unset, so the RLS '
  'predicate matches nothing and the failure mode is an empty result rather '
  'than a cross-tenant leak.';

-- ---------------------------------------------------------------------
-- The application role. Deliberately NOT the migration role: it must not
-- be superuser and must not hold BYPASSRLS, or none of this applies.
-- ---------------------------------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agent_erp_app') THEN
        CREATE ROLE agent_erp_app LOGIN PASSWORD 'agenterp_app'
            NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS INHERIT;
    ELSE
        ALTER ROLE agent_erp_app NOSUPERUSER NOBYPASSRLS;
    END IF;
END $$;

GRANT USAGE ON SCHEMA public TO agent_erp_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO agent_erp_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO agent_erp_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO agent_erp_app;

-- The answer key is never readable by the application, at any tenant.
-- Guarded: ground_truth is a TEST fixture and does not exist in a real
-- deployment, so an unguarded REVOKE makes this file fail on a fresh
-- database — which is exactly where it matters most.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.schemata
               WHERE schema_name = 'ground_truth') THEN
        EXECUTE 'REVOKE ALL ON SCHEMA ground_truth FROM agent_erp_app';
        EXECUTE 'REVOKE ALL ON ALL TABLES IN SCHEMA ground_truth FROM agent_erp_app';
    END IF;
END $$;

-- ---------------------------------------------------------------------
-- Enable + FORCE + policy on every tenant-scoped table.
-- ---------------------------------------------------------------------
DO $$
DECLARE
    t record;
    nullable boolean;
    predicate text;
BEGIN
    FOR t IN
        SELECT c.relname AS table_name
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind = 'r'
          AND EXISTS (SELECT 1 FROM pg_attribute a
                      WHERE a.attrelid = c.oid AND a.attname = 'tenant_id'
                        AND NOT a.attisdropped)
        ORDER BY c.relname
    LOOP
        SELECT a.attnotnull = false INTO nullable
        FROM pg_attribute a
        WHERE a.attrelid = format('public.%I', t.table_name)::regclass
          AND a.attname = 'tenant_id';

        -- Tables with a nullable tenant_id (semantic_term,
        -- command_definition) use NULL to mean "system-wide" — shared
        -- vocabulary every tenant must be able to read. Those rows stay
        -- visible; everything else is scoped.
        predicate := CASE WHEN nullable
            THEN 'tenant_id IS NULL OR tenant_id = current_tenant_id()'
            ELSE 'tenant_id = current_tenant_id()'
        END;

        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t.table_name);
        -- FORCE so the table OWNER is subject to the policy too. Without
        -- it, anything running as the owner silently sees every tenant.
        EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY', t.table_name);
        EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON public.%I', t.table_name);
        EXECUTE format(
            'CREATE POLICY tenant_isolation ON public.%I '
            'USING (%s) WITH CHECK (%s)',
            t.table_name, predicate,
            -- WITH CHECK omits the NULL escape: an application may READ
            -- system vocabulary but may never WRITE a row belonging to
            -- another tenant, or a global row.
            'tenant_id = current_tenant_id()');
    END LOOP;
END $$;

\echo 'RLS applied. Verify with: uv run python -m tests.test_rls'
