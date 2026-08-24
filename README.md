# agent-erp-core

An **agent-first ERP core for discrete manufacturing** — job shops, make-to-order,
mixed-mode. An event-sourced ledger, a policy engine that bounds what automated actors
may do, and a small set of domain commands with their invariants enforced below the
caller.

Apache-2.0. This is the data core: it runs, and is useful, with no AI layer at all.

---

## Why it exists

A conventional ERP is forms over tables — schema designed backwards from screens, logic
in the UI, state mutated in place, audit bolted on. That shape defeats automated
operators for six specific reasons, and each has a structural answer here:

| The problem | The answer |
|---|---|
| Meaning lives in tribal knowledge (`USERDEF3`, `FLAG7`) | The ontology is runtime data that generates tool schemas, validation and labels |
| Destructive updates erase the trail | An append-only, hash-chained flow ledger; current state is a projection |
| Nowhere to record an actor's *intent* | A proposal layer carrying rationale, evidence and a declared blast radius |
| Every fact asserted with equal authority | Three tiers of truth — signal, assertion, record — with promotion gates |
| Authority is binary | Authority bands per (actor, command, scope), bounded by reversibility and money |
| The API is CRUD | Domain commands with invariants enforced below the caller |

Plus one specific to manufacturing: **physical stock is *believed*, not known.** On-hand
carries a verification age and a confidence, so a caller can reason about the expected
cost of being wrong rather than trusting an integer nobody has checked since March.

## What's here

```
db/schema.sql        44 tables. The ledger, the ontology, commitments, jobs, quality.
db/rls.sql           Row-level tenant isolation: enabled, FORCEd, default-deny.
db/verify.sql        Tests the architecture's claims against whatever data you have.
erp/core/db.py       The Ledger — the only thing that appends to the event log.
erp/core/policy.py   The policy engine: authority, reversibility, ceilings, SoD.
erp/core/registry.py The command registry — the declared write surface.
erp/core/commands.py Domain commands with pre/postconditions and dry-run.
erp/core/ingest/     Adapters for loading an existing ERP's data.
docs/                The architecture: principles, ontology, taxonomies, trade-offs.
```

Start with `docs/00_principles.md`, then `docs/01_ontology.md`. `docs/06_tradeoffs.md`
is 14 decision records with their costs and the conditions that would reverse them —
it is the honest version of the argument, including the three decisions most likely to
be wrong.

## Quick start

```bash
createdb agent_erp
psql -d agent_erp -f db/schema.sql
psql -d agent_erp -f db/rls.sql       # creates the unprivileged app role
psql -d agent_erp -f db/verify.sql    # asserts the invariants hold
```

Every connection must declare its tenant, or it sees nothing:

```sql
SELECT set_config('agent_erp.tenant_id', '<your-tenant-uuid>', false);
```

That default is deliberate: a connection that forgets returns empty results rather than
another tenant's data.

## Running the tests

```bash
python -m tests.test_rls              # tenant isolation, proven behaviourally
python -m tests.test_commands         # command invariants + ledger reconciliation
python -m tests.test_layer_boundary   # the core depends on nothing AI-shaped
```

`test_rls.py` does not merely inspect catalogs — it connects as the unprivileged role,
plants a row under a second tenant and tries to read it. Superusers and `BYPASSRLS`
roles ignore RLS entirely, and a table's owner ignores it unless `FORCE` is set, so a
configuration-only check can pass on a database that leaks.

## Provenance

This work is original. No code, schema definition, table name, column name, or data
model was copied, ported, or derived from any existing ERP — commercial or open source.

Two published, non-proprietary conceptual sources informed the *vocabulary*: **ISA-95 /
IEC 62264** for the equipment-hierarchy naming, and **REA** (McCarthy, 1982 — an
academic accounting model) for the paired-flow duality concept. Neither contributed
code. See `NOTICE`.

## Accounts payable

Matching an invoice says whether it is *correct*. The subledger says what is **owed**:

```sql
SELECT * FROM payable_open WHERE settlement_state <> 'settled';
SELECT * FROM payable_aging(DATE '2026-03-31');   -- as at, not now()
```

`payable_obligation` is a liability accepted; `payable_settlement` is an append-only
event that discharges part of one. There is **no balance column and no status column** —
outstanding is gross minus settlements, computed on read, so there is one definition and
nothing to reconcile. A credit note is a negative obligation rather than a special case,
which keeps every balance query a `SUM`. A reversal is a negative settlement, so a
disputed payment keeps its history instead of being edited away.

Aging takes the date as an argument. An aging report that cannot be run "as at month
end" is not an aging report, and hardcoding `now()` makes the past unreproducible.

Deliberately excluded: general ledger, journals, chart of accounts. This is the
subledger; posting a summary into a GL is an integration.

## Upgrading

`docs/03_schema.sql` is a design artifact: it is written to be read, and it recreates
from nothing. A database with data in it needs a different path, so migrations are
separate:

```bash
bash infra/migrate.sh            # apply anything pending
bash infra/migrate.sh --status   # what is applied, what is not
bash infra/migrate.sh --dry-run  # what would run
```

Forward-only, each migration in a transaction with the row recording it, so a failure
leaves the previous version rather than a half-migrated database. An applied migration
is immutable — its checksum is recorded and `migrate.sh` refuses to continue if the
file changes underneath it. `tests/test_migrations.py` builds a database each way and
fails if the migrations and the reference schema have drifted, so the document keeps
describing the database.

## Status

Early, and specifically:

**Measured** (`infra/loadtest.py`, 500,000 flow events, PostgreSQL 16):

| query | before | after |
|---|---|---|
| ledger head — runs on **every** write | 29.9 ms, seq scan | 0.0 ms, index |
| events by type, recent first | 0.0 ms, index | 0.0 ms, index |
| events by correlation id | 0.0 ms, index | 0.0 ms, index |
| sequence range (audit export) | 13.2 ms, seq scan | 0.2 ms, index |

The head lookup mattered most: it reads `prev_hash` on every append, so scanning meant
write cost grew with history and the system got slower the longer it was used. Migration
002 indexes it. 30 ms was survivable; the *shape* was not.

**Not measured, and you should assume nothing:** concurrent writers, lock contention,
vacuum behaviour under sustained load, anything above 500k events, and every table other
than `flow_event`. There is no benchmark for multi-tenant query interference under RLS.
This has never carried production traffic.

Other known gaps are listed at the end of `docs/12_open_core.md` rather than hidden —
including the missing bitemporal convenience views and the fact that production verbs
(`report_production`, `issue_to_job`) are not yet in the command layer.

Dependencies: `psycopg`. That is the whole list, and it is enforced by an allowlist in
`tests/test_layer_boundary.py` — adding one is a deliberate act, because a public
release inherits its licence and its supply chain.
