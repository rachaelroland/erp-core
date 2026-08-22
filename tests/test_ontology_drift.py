# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Applied Industrials
"""Detect ontology drift (ADR-008).

Pillar 4 says the ontology is a runtime artefact, not documentation: agents
read `semantic_term` to ground language, and the same rows drive validation.
ADR-008 names the failure mode that idea invites —

    "a real risk of ontology drift, where the registry and the schema
     disagree, which is worse than having no registry at all because it is
     trusted."

— and promises the mitigation: "drift is a CI check; the registry and the
schema are compared on every build, and disagreement fails the build."

This is that check. Three directions, because drift has three shapes:

  1. SCHEMA -> REGISTRY.  A value a CHECK constraint permits but the registry
     has never heard of. An agent reading the vocabulary would not know the
     value exists; a validator would happily accept it.
  2. DATA -> REGISTRY.  A value actually stored in a column that the registry
     does not define. Worse than (1): it is already in the database.
  3. REGISTRY -> USE.  A registered term that nothing can ever hold, usually
     a typo or a rename that only got done on one side.

Run:  uv run python -m tests.test_ontology_drift
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from applied_erp.core.db import connect_admin  # noqa: E402

# Which (table, column) each vocabulary governs. This mapping is the thing
# being asserted: it is the claim that the registry and the schema describe
# the same values, and every entry is a promise the check enforces.
GOVERNED: dict[str, list[tuple[str, str]]] = {
    "inventory_condition": [("inventory_quantum", "condition")],
    "inventory_ownership": [("inventory_quantum", "ownership")],
    "inventory_encumbrance": [("inventory_quantum", "encumbrance")],
    "assertion_method": [("assertion", "method")],
    "scrap_reason": [],          # reason codes are free-text by design; see note
}

# Vocabularies whose values are reason codes rather than a closed enum. They
# are registered for agents to READ, but no CHECK constrains them, so
# direction (1) does not apply. Stated explicitly rather than silently
# skipped — an unexplained exemption is how a check rots.
ADVISORY_ONLY = {"scrap_reason"}

ARRAY_LITERAL = re.compile(r"'((?:[^']|'')*)'::text")

results: list[tuple[bool, str]] = []


def check(ok: bool, label: str) -> None:
    results.append((bool(ok), label))


def check_constraint_values(conn) -> dict[tuple[str, str], set[str]]:
    """The value set each CHECK constraint permits, per (table, column)."""
    out: dict[tuple[str, str], set[str]] = {}
    with conn.cursor() as cur:
        cur.execute("""
            SELECT rel.relname AS table_name,
                   pg_get_constraintdef(con.oid) AS definition
            FROM pg_constraint con
            JOIN pg_class rel ON rel.oid = con.conrelid
            JOIN pg_namespace n ON n.oid = rel.relnamespace
            WHERE n.nspname = 'public' AND con.contype = 'c'
              AND pg_get_constraintdef(con.oid) LIKE '%ANY (ARRAY%'""")
        for row in cur.fetchall():
            defn = row["definition"]
            m = re.search(r"\(\(?(\w+)\s*=\s*ANY", defn)
            if not m:
                continue
            column = m.group(1)
            values = {v.replace("''", "'") for v in ARRAY_LITERAL.findall(defn)}
            out.setdefault((row["table_name"], column), set()).update(values)
    return out


def registry(conn) -> dict[str, set[str]]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT vocabulary, term FROM semantic_term "
            "WHERE deprecated_at IS NULL")
        out: dict[str, set[str]] = {}
        for r in cur.fetchall():
            out.setdefault(r["vocabulary"], set()).add(r["term"])
    return out


def distinct_values(conn, table: str, column: str) -> set[str]:
    with conn.cursor() as cur:
        cur.execute(f"SELECT DISTINCT {column} AS v FROM {table} "
                    f"WHERE {column} IS NOT NULL")
        return {r["v"] for r in cur.fetchall()}


def main() -> int:
    conn = connect_admin()
    checks = check_constraint_values(conn)
    reg = registry(conn)

    print(f"ontology drift: {len(reg)} vocabularies, "
          f"{sum(len(v) for v in reg.values())} registered terms, "
          f"{len(checks)} constrained columns")
    print()

    for vocabulary, columns in GOVERNED.items():
        registered = reg.get(vocabulary, set())
        check(bool(registered), f"vocabulary '{vocabulary}' exists in the registry")
        if not registered:
            continue

        for table, column in columns:
            # (1) schema -> registry
            if vocabulary not in ADVISORY_ONLY:
                permitted = checks.get((table, column), set())
                # An UNCONSTRAINED column makes this direction vacuous: an
                # empty permitted-set trivially contains nothing unregistered,
                # so the check passes while enforcing nothing. Report it as a
                # finding — the vocabulary claims to govern a column that the
                # database does not actually constrain, which is exactly the
                # gap that let 'banana_hold' be written by direct SQL.
                if not permitted:
                    check(False,
                          f"{table}.{column}: governed by '{vocabulary}' but has "
                          f"NO enum CHECK — the registry constrains nothing here, "
                          f"only the command layer does")
                else:
                    unregistered = permitted - registered
                    check(not unregistered,
                          f"{table}.{column}: every CHECK value is registered"
                          + (f" — MISSING {sorted(unregistered)}" if unregistered else ""))

            # (2) data -> registry. The serious one: already stored.
            try:
                in_use = distinct_values(conn, table, column)
            except Exception as exc:
                check(False, f"{table}.{column}: could not read values ({exc})")
                conn.rollback()
                continue
            undefined = in_use - registered
            check(not undefined,
                  f"{table}.{column}: every STORED value is defined"
                  + (f" — UNDEFINED {sorted(undefined)}" if undefined else ""))

    # (3) registry -> use. A term nothing can hold is usually a rename that
    # only happened on one side.
    for vocabulary, columns in GOVERNED.items():
        if vocabulary in ADVISORY_ONLY or not columns:
            continue
        registered = reg.get(vocabulary, set())
        permitted: set[str] = set()
        for table, column in columns:
            permitted |= checks.get((table, column), set())
        if not permitted:
            continue
        orphaned = registered - permitted
        check(not orphaned,
              f"'{vocabulary}': every registered term is actually permitted"
              + (f" — ORPHANED {sorted(orphaned)}" if orphaned else ""))

    # Every registered term must carry a definition: it is the string agents
    # read to ground language, and a blank one silently degrades them.
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM semantic_term "
                    "WHERE definition IS NULL OR btrim(definition) = ''")
        check(cur.fetchone()["n"] == 0, "every registered term has a definition")

    # Commands in the registry must exist in code, and vice versa. Same class
    # of drift, different artefact.
    from applied_erp.core.registry import COMMANDS
    declared = {c["command_name"] for c in COMMANDS}
    with conn.cursor() as cur:
        cur.execute("SELECT command_name FROM command_definition")
        stored = {r["command_name"] for r in cur.fetchall()}
    check(declared == stored,
          "command registry in the database matches the code"
          + (f" — code-only {sorted(declared - stored)}, "
             f"db-only {sorted(stored - declared)}"
             if declared != stored else ""))

    conn.close()

    passed = sum(1 for ok, _ in results if ok)
    for ok, label in results:
        print(f"  {'ok  ' if ok else 'FAIL'}  {label}")
    print()
    print(f"{passed}/{len(results)} checks passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
