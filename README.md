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

## Status

Early. The schema runs and its invariants are tested, but it has not carried production
load and the indexes are untuned. Known gaps are listed at the end of
`docs/12_open_core.md` rather than hidden — including the absent ontology-drift check,
the missing bitemporal convenience views, and the fact that production verbs
(`report_production`, `issue_to_job`) are not yet in the command layer.

Dependencies: `psycopg`. That is the whole list, and it is enforced by an allowlist in
`tests/test_layer_boundary.py` — adding one is a deliberate act, because a public
release inherits its licence and its supply chain.
