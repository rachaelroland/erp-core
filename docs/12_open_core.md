# 12 — Open core

*Phase 6. The core is separated from the AI layer, the separation is enforced
by a test, and the ontology's headline claim finally executes.*

---

## The split

Rachael may open-source the data core and keep the AI layer private. That is
a sound open-core shape — the core drives adoption, the AI layer is the paid
part — but it only stays possible if the boundary is **mechanical**. The
moment core imports the AI layer, separating them becomes a refactor instead
of a `cp -r`.

| Open candidate — `erp/core/` | Private — `erp/ai/` |
|---|---|
| Ledger + hash chain (`db.py`) | Extraction (`extract.py`) |
| Policy engine + authority model (`policy.py`) | Matchers and routing (`matchers.py`) |
| Command registry (`registry.py`) | Agent runner (`run_matcher.py`) |
| Ingestion adapters (`ingest/`) | Scoring + eval (`score*.py`) |
| Schema, ontology, taxonomies, `verify.sql` | Ground truth, prompts |

**The policy engine belongs in the core.** It works on human proposals with no
model present, it is what makes the ledger trustworthy, and publishing "here
is how authority is bounded" is a credibility asset rather than a giveaway.
The moat is the eval harness and the matchers, not the guardrail.

`erp/config.py` now carries **database access only**. Model routing and API
keys moved to `erp/ai/config.py`, so a reader of the public repo finds
nothing about which LLM we use or how it is prompted.

## The boundary is tested, not asserted

`runtime/tests/test_layer_boundary.py` parses every core module and fails if:

- core imports `applied_erp.ai` in any form,
- core imports any model-client or document-AI dependency — a model
  dependency in the system of record blocks the split outright,
- core imports any third party not on an explicit allowlist (currently just
  `psycopg`) — every entry is a licence and a supply chain the public release
  inherits,
- *no* AI module imports core, which would mean the layering is decorative.

```
layer boundary: 9 core modules, 8 ai modules
PASS — core does not depend on the AI layer
```

Run it before any release, and before merging anything that adds an import.

## Why the clean-room rule now has teeth

It was internal discipline; if the core ships publicly it becomes a claim
somebody may test. `CLAUDE.md` rule 1 has always been "no code, schema, table
names, or column names from any existing ERP, including reconstruction from
model training knowledge," with standards (ISA-95, REA, GS1) used only as
labelled vocabulary. That provenance line is the thing that makes an
open-source release safe to publish, and it must not slip.

The one dependency worth watching: core currently needs only `psycopg`
(LGPL-compatible, permissive). Keep it that way — the allowlist in the
boundary test is the enforcement point.

---

## Lot genealogy — the ontology's headline claim now executes

`01_ontology.md` closes on a recall traversal: shipment → produced lot →
`derives_from` → supplier lot → receipt → the evidence image, "every hop a
stored relation, not a reconstruction." That was the longest-standing
unproven claim in the repo — `lot_genealogy` was empty for four phases.

**Two real modelling bugs were behind it**, both the same shape:

1. **Issues drew from a lot-less quantum.** `_issue` looked up
   `(item, location, condition)` with `lot_id=None`, creating a *different*
   quantum from the lot-tracked stock receipts had made. Material never
   carried a lot into production. Now stock is consumed FIFO across the
   actual lot-tracked quanta, and what was consumed is remembered so the
   produced lot can be linked to its inputs.
2. **Shipments had the identical bug.** `_produce` created a lot-tracked
   finished quantum; `_ship` drew from a lot-less one. So even once genealogy
   existed, no shipment referenced a lot and the *backward* direction
   returned nothing.

Both are now lot-aware, with an explicit fallback: if tracked stock is short,
the balance issues from the untracked pool and goes negative — which is what
a shop floor actually does when someone takes material the system does not
know about, and a later cycle count corrects it.

### It traverses, both ways

`verify.sql` §13, against generated data:

```
genealogy_links | produced_lots_linked | supplier_lots_referenced
            183 |                  148 |                       26

BACKWARD — a shipped lot to its source
 FG-GUARD-STD | L2604-0007 | supplier lot WRKS-3607 | depth 1

FORWARD — a suspect supplier lot, what it reached
 L2604-0006 | WRKS-3191 | 84 affected lots | 84 affected shipments
```

The 2am recall question — *which customer shipments contain material from
vendor lot 88-C* — is now a recursive CTE over stored relations, in both
directions.

**Honest caveat:** depth is currently 1. The generator creates one job per
finished good and issues components directly, so there is no intermediate
subassembly lot to traverse through. A multi-level chain (raw → subassembly
lot → finished lot) needs jobs per subassembly, which the schema supports and
the generator does not yet produce. The traversal is proven; its depth is not.

## Core invariants after all of it

| Claim | Result |
|---|---|
| Ledger reproduces the projection | **654/654 quanta agree**, worst delta 0.000000 |
| Duality holds | **2,000 pairs, 0 malformed, 0 unbalanced** |
| Hash chain continuous | **2,323 events, 0 broken links** |
| Ledger immutable | `UPDATE` has no effect |

---

## Tenant isolation (ADR-012) — now implemented and proven

`db/rls.sql` enables row-level security, **FORCEs** it, and attaches a
`tenant_isolation` policy to all 44 tenant-scoped tables. The predicate reads
a session GUC via `current_tenant_id()`; unset means NULL, which matches no
row, so a connection that forgets to set its tenant sees **nothing** rather
than everything.

**The trap this had to avoid.** Superusers and `BYPASSRLS` roles ignore RLS
completely, and a table's *owner* ignores it unless `FORCE` is set. Our
migration role is both. A configuration-only check would therefore report
"RLS enabled" on a database where every query still returns every tenant's
rows. So there is a separate unprivileged `agent_erp_app` role, and
`tests/test_rls.py` proves isolation **behaviourally** — it plants a row under
a second tenant and tries to read it.

What the test asserts:

- every tenant-scoped table has RLS enabled, FORCEd, and a policy;
- `agent_erp_app` is not a superuser and does not hold `BYPASSRLS`;
- with no tenant set, the app role reads **zero** rows (default deny);
- with our tenant set, it reads its own rows and **not** the planted one;
- a cross-tenant `INSERT` is refused (`WITH CHECK` deliberately omits the
  NULL escape, so shared vocabulary is readable but never writable);
- `ground_truth` is unreachable from the application at any tenant.

**The test was verified to fail.** Un-FORCEing `party` and dropping the policy
on `lot` produced exactly two findings and a non-zero exit. A guard that has
never failed is not evidence.

---

## The core command surface

`erp/core/commands.py` adds the inventory verbs, so the core is an ERP core
rather than an AP core. These are **not agent commands** — a human clicking a
button goes through the same path. If there were a separate human path the
invariants would exist twice and diverge.

| Command | Reversibility | The invariant that matters |
|---|---|---|
| `move_quantum` | R1 | quarantined stock cannot be moved without a disposition first |
| `change_condition` | R2 | the target condition must exist in `semantic_term` — a caller may not invent a status (Pillar 4) |
| `split_lot` | R2 | a split MUST record the parent link; losing it destroys traceability silently |
| `post_count_result` | R2 | the book is reconciled TO the count, never the reverse (Pillar 8) |

All four dry-run, declare a blast radius for the policy engine, and write
through `Ledger` so the hash chain and duality pairing stay in one place. The
registry now declares **8 commands**.

`tests/test_commands.py` — 20 checks, all passing — tests each command twice:
that it refuses what it should, and that after it acts **the ledger still
reconciles** (654/654 before, 656/656 after), the hash chain is unbroken, and
every pair it wrote is balanced. That second half is the important one: a
command that writes flows without keeping the projection in step silently
invalidates `verify.sql` §2, which is the load-bearing claim of the whole
architecture.

Run everything with `bash infra/test.sh`.

---

## The licence, and an extractable release

**Apache-2.0**, copyright Applied Industrials. Chosen over AGPL because the core exists
to drive adoption — the AI layer is the paid part — and because a permissive licence
with an explicit patent grant is what a manufacturer's legal team can approve without a
conversation.

`bash infra/build_core_release.sh` extracts the publishable tree and **audits it**,
refusing to produce a release if:

- the copyright holder is still a placeholder,
- any shipped `.py` or `.sql` lacks an SPDX header,
- a private reference leaked (an `applied_erp.ai` import, a named model provider or
  gateway, an API-key-shaped string, or a query against the `ground_truth` fixtures),
- any private module ships (`matchers.py`, `extract.py`, `score.py`, `run_matcher.py`,
  `context.py`, `ground_truth.sql`),
- the extracted tree imports anything AI-shaped,
- `schema.sql` or `rls.sql` fails to apply to an empty database.

That last check is the one that makes this real rather than aspirational: the release is
verified to stand up on a database that has never seen our generator.

**The audit found five genuine problems on its first run**, which is the argument for
having written it:

1. A model provider name had leaked into a public schema comment: an
   `agent_model` column was documented with a live vendor identifier as its
   example value.
2. `verify.sql` queried `ground_truth.*` — the private evaluation fixtures — so the
   shipped verification would not run for anyone else. Split into `db/verify_core.sql`.
3. `rls.sql` did an unguarded `REVOKE ON SCHEMA ground_truth`, which fails on a fresh
   database — precisely where it matters most. Now guarded.
4. Five shipped files had no SPDX header.
5. `__pycache__` artefacts were being packaged.

The release is now 28 files: schema, RLS, core verification, the ledger, policy engine,
command registry, commands, ingestion adapters, three test suites, eight architecture
documents, LICENSE, NOTICE and a public README.

---

## RLS is now in force, not merely proven

The previous state was honest but incomplete: isolation was demonstrated by a test while
the runtime still connected as the migration superuser. Two roles now exist in
`erp/config.py`:

- **`app`** — `agent_erp_app`, unprivileged, subject to RLS, no access to
  `ground_truth`. **The default**, because if the application can run as the migration
  role then RLS is decorative.
- **`admin`** — DDL, fixture construction, and the private evaluation harness (which
  reads the answer key by design). Never for serving a request.

`connect()` also *declares the tenant* rather than leaving it to callers — the predicate
reads a session GUC, and an unset value matches no row. `tests/test_rls.py` now asserts
the runtime's own `connect()` lands on a non-superuser with a tenant set, so a future
change that quietly points it back at the migration role fails the build.

Three commands stay on the admin role, deliberately: `score` and `score-extraction` read
the private fixtures, and `seed-registry` writes system-wide rows (`tenant_id IS NULL`)
that the RLS `WITH CHECK` forbids the app role from authoring — correctly, since a
tenant must never be able to write shared vocabulary.

### Switching over surfaced three real defects

Exactly what it was supposed to do. All three were invisible while everything ran as a
superuser:

1. **The extraction pipeline was reading the answer key.** It sourced PDF paths from
   `ground_truth.document_content` — production behaviour depending on a private test
   fixture. The document's location belongs in `evidence.storage_uri`, and extraction
   now reads from the ERP's own records.
2. **Re-ingesting an export crashed** with a unique-key violation part-way through the
   batch. Re-running a load is a normal thing to do — a file gets re-sent, a partial
   failure gets retried — and it must produce a report, not a crash. Existing orders are
   now skipped with a warning.
3. **`test_commands.py` opened a raw connection** with no tenant declared and so saw an
   empty database. The database was right and the test was wrong; it now uses the
   application's own `connect()`.

---

## Still open in the core

1. **The ontology-drift check from ADR-008** — comparing `semantic_term` to the
   actual schema on every build — is specified and unbuilt.
2. **Bitemporal "as of now" views** (Pillar 7) are described as the default
   access path and do not exist; callers still hit raw tables.
3. **Production verbs.** `report_production` and `issue_to_job` are still only
   in the generator, not the command layer.
4. **The generator still runs as the admin role.** That is correct — it is
   fixture construction, not application behaviour — but it means the
   generator's writes are not themselves RLS-exercised.
5. **No incremental ingest** — every run is a full load.
6. **No licence chosen.** Still a deliberate decision to make, and it
   interacts with the `psycopg` dependency (core's only third party).
