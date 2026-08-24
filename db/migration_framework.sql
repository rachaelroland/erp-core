-- SPDX-License-Identifier: Apache-2.0
-- Copyright 2026 Applied Industrials
-- =====================================================================
-- Migration tracking.
--
-- docs/03_schema.sql is a design artifact — it is written to be read and
-- argued with, and it drops and recreates. That is the right shape for a
-- reference, and the wrong shape for a database with data in it. Anyone
-- adopting this needs to know how to get from version N to N+1 without
-- losing what they have, and "re-run the reference schema" is not an
-- answer.
--
-- Forward-only. No down-migrations: reversing a schema change on a
-- ledger whose whole premise is that history is append-only would mean
-- destroying records to satisfy a rollback. Recovery is restore-from-
-- backup plus a new forward migration, which is what people actually do
-- under pressure anyway.
--
-- Checksums are recorded so an edited-after-the-fact migration is
-- detectable. A migration file is immutable once applied anywhere.
-- =====================================================================

CREATE TABLE IF NOT EXISTS schema_migration (
    version      integer     PRIMARY KEY,
    name         text        NOT NULL,
    checksum     text        NOT NULL,
    applied_at   timestamptz NOT NULL DEFAULT now(),
    applied_by   text        NOT NULL DEFAULT current_user
);

COMMENT ON TABLE schema_migration IS
  'Which migrations this database has had applied. Forward-only. checksum is '
  'of the file as applied, so a migration edited after the fact is detectable '
  'rather than silently divergent.';

-- Deliberately NOT tenant-scoped and NOT under RLS: the schema is a
-- property of the database, not of a tenant. Every other table in this
-- system carries tenant_id; this one is the exception and says so.
