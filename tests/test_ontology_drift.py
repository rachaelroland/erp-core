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

This is that check. Four directions, because drift has four shapes:

  1. SCHEMA -> REGISTRY.  A value a CHECK constraint permits but the registry
     has never heard of. An agent reading the vocabulary would not know the
     value exists; a validator would happily accept it.
  2. DATA -> REGISTRY.  A value actually stored in a column that the registry
     does not define. Worse than (1): it is already in the database.
  3. REGISTRY -> USE.  A registered term that nothing can ever hold, usually
     a typo or a rename that only got done on one side.
  4. DOCUMENT -> REGISTRY.  A vocabulary docs/02_taxonomies.md defines that
     the registry never received.

Direction (4) was added on 2026-09-15 and it is the one that made this file
honest. Until then GOVERNED below was the whole of the check's world: it
listed five vocabularies, nothing compared that list to anything, and the
suite reported 19/19 green while §02 defined thirty-one blocks. The guard
against a hand-maintained vocabulary was itself a hand-maintained list, so it
could only find drift in the columns somebody had remembered to add to it.

Chip Lynch found the gap from outside by reading the repository: he noticed
that `accept_with_deviation` appears in §02 and in no SQL artefact anywhere.
It was one of twenty-two documented vocabularies this check could not see.

So §02 is now parsed, and every block it defines must be declared in
DOCUMENTED below — bound to a registry vocabulary, or explicitly recorded as
not yet registered, or marked as prose. An undeclared block fails the build,
which is the property the old list did not have. The declaration is still
written by hand; the difference is that it is now compared against the
document on every run, and a hand-written list that is checked against
another artefact is a different thing from one that is not.

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
    "quality_disposition": [("inspection", "disposition"),
                            ("nonconformance", "disposition")],
    "corrective_action_state": [("nonconformance", "corrective_action_state")],
}

# Vocabularies whose values are reason codes rather than a closed enum. They
# are registered for agents to READ, but no CHECK constrains them, so
# direction (1) does not apply. Stated explicitly rather than silently
# skipped — an unexplained exemption is how a check rots.
ADVISORY_ONLY = {"scrap_reason"}

ARRAY_LITERAL = re.compile(r"'((?:[^']|'')*)'::text")

# --------------------------------------------------------------- direction 4
# docs/02_taxonomies.md, parsed rather than trusted.

PROSE = "prose"   # a block the parser picks up that defines no vocabulary


def taxonomy_doc() -> Path:
    """Find §02 from either layout: the working tree or the published core."""
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "docs" / "02_taxonomies.md"
        if candidate.exists():
            return candidate
    raise FileNotFoundError("docs/02_taxonomies.md not found above this file")


DOC_TERM = re.compile(r"`([A-Za-z][A-Za-z0-9_]*)`")


def doc_blocks(text: str) -> dict[str, set[str]]:
    """Every block of §02 that names at least one backticked term.

    A vocabulary is introduced either by a `## heading` or by a line-initial
    `**Label**`. Bold in the middle of a sentence is prose and is not a
    position a vocabulary may be defined in — stated here because the parser
    genuinely cannot see one there, and a limit nobody wrote down is how a
    check quietly stops covering things.
    """
    blocks: dict[str, set[str]] = {}
    section: str | None = None
    label: str | None = None
    buf: list[str] = []

    def flush() -> None:
        if label and buf:
            terms = set(DOC_TERM.findall("\n".join(buf)))
            if terms:
                blocks[label] = terms

    for raw in text.split("\n"):
        s = raw.strip()
        if s.startswith("## "):
            flush()
            section = s[3:].strip()
            label, buf = section, []
            continue
        m = re.match(r"^\*\*([^*]+?)\*\*(.*)$", s)
        if m:
            flush()
            name = m.group(1).rstrip(":").strip()
            label = f"{section} / {name}" if section else name
            buf = [m.group(2)]
            continue
        if s:
            buf.append(s)
    flush()
    return blocks


# Every block doc_blocks() finds must appear here exactly once. The value is
# the registry vocabulary that carries it, None if §02 defines it and the
# registry does not yet, or PROSE if the block is explanatory text that
# happens to quote identifiers.
#
# The None entries are the honest state of this system, not an oversight
# being hidden: twenty-two vocabularies are written down and unenforced. The
# list may only shrink. Registering one without moving it here fails the
# "every registered vocabulary is declared" check below, and deleting one
# from §02 without removing it here fails the staleness check.
DOCUMENTED: dict[str, str | None] = {
    "Naming rules for every vocabulary here": PROSE,
    "Party roles": None,
    "Actor kinds": None,
    "Item classification / Facet A — sourcing (how it comes to exist)": None,
    "Item classification / Facet B — function (what role it plays)": None,
    "Unit-of-measure classes": None,
    "Inventory quantum dimensions / Condition": "inventory_condition",
    "Inventory quantum dimensions / Ownership": "inventory_ownership",
    "Inventory quantum dimensions / Encumbrance": "inventory_encumbrance",
    "Inventory quantum dimensions / Item tracking policy": None,
    "Commitment taxonomy / Direction": None,
    "Commitment taxonomy / Firmness ladder": None,
    "Commitment taxonomy / Kind": None,
    "Flow (event) taxonomy / Material": None,
    "Flow (event) taxonomy / Capacity / labor": None,
    "Flow (event) taxonomy / Financial": None,
    "Flow (event) taxonomy / Correction (never deletes)": None,
    "Flow (event) taxonomy / Reason codes": "scrap_reason",
    "Job / process execution states / Job": None,
    "Job / process execution states / Operation": None,
    "Job / process execution states / Machine state": None,
    "Job / process execution states / Job quantity vocabulary": PROSE,
    "Quality taxonomy / Characteristic type": None,
    "Quality taxonomy / Disposition": "quality_disposition",
    "Quality taxonomy / Nonconformance severity": None,
    "Quality taxonomy / Corrective action state": "corrective_action_state",
    "Assertion taxonomy (Tier B) / Method": "assertion_method",
    "Assertion taxonomy (Tier B) / Resolution status": None,
    "Proposal / governance taxonomy / Reversibility class": None,
    "Proposal / governance taxonomy / Proposal status": None,
    "Proposal / governance taxonomy / Authority band": None,
}

# Blocks whose backticked terms are not the vocabulary itself, so comparing
# the document's terms against the registry would be meaningless. Each one
# says why, because an unexplained exemption is how a check rots.
TERMS_NOT_COMPARABLE = {
    # §02 lists only the scrap-reason family inline as an illustration; the
    # registered set comes from shop.SCRAP_REASONS and is longer.
    "Flow (event) taxonomy / Reason codes":
        "document shows an illustrative subset of one reason family",
}

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

    documented = doc_blocks(taxonomy_doc().read_text())

    print(f"ontology drift: {len(documented)} documented blocks, "
          f"{len(reg)} registered vocabularies, "
          f"{sum(len(v) for v in reg.values())} registered terms, "
          f"{len(checks)} constrained columns")
    print()

    # (4) DOCUMENT -> REGISTRY. Run first: if the declaration and the document
    # disagree, everything below is checking a world that does not exist.
    undeclared = sorted(set(documented) - set(DOCUMENTED))
    check(not undeclared,
          "every vocabulary block in §02 is declared"
          + (f" — UNDECLARED {undeclared}" if undeclared else ""))

    stale = sorted(set(DOCUMENTED) - set(documented))
    check(not stale,
          "every declaration still exists in §02"
          + (f" — STALE {stale}" if stale else ""))

    for label, vocabulary in sorted(DOCUMENTED.items()):
        if vocabulary in (None, PROSE) or label not in documented:
            continue
        registered = reg.get(vocabulary, set())
        check(bool(registered),
              f"§02 '{label}' is registered as '{vocabulary}'")
        if not registered or label in TERMS_NOT_COMPARABLE:
            continue
        # The registry being a SUBSET of the document is the quiet half of
        # this drift: the vocabulary exists, so every other check passes,
        # while the terms an agent can actually read are fewer than the ones
        # the document promises.
        missing = documented[label] - registered
        check(not missing,
              f"'{vocabulary}': every term in §02 is registered"
              + (f" — MISSING {sorted(missing)}" if missing else ""))

    declared_vocabularies = {v for v in DOCUMENTED.values()
                             if v not in (None, PROSE)}
    undocumented = sorted(set(reg) - declared_vocabularies)
    check(not undocumented,
          "every registered vocabulary is declared against §02"
          + (f" — UNDOCUMENTED {undocumented}" if undocumented else ""))

    unenforced = sorted(k for k, v in DOCUMENTED.items() if v is None)
    print(f"  note  {len(unenforced)} documented vocabularies are not "
          f"registered and not enforced; this list may only shrink")
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
