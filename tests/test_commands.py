# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Applied Industrials
"""Exercise the core command surface and its invariants.

Each command is tested twice: that it REFUSES what it should, and that when
it acts the ledger still reconciles. The second half is the important one —
a command that writes flows without keeping the projection in step silently
invalidates verify.sql §2, which is the load-bearing claim of the whole
architecture.

Run:  uv run python -m tests.test_commands
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from applied_erp.config import TENANT_ID                          # noqa: E402
from applied_erp.core.db import connect                           # noqa: E402
from applied_erp.core.commands import Commands                   # noqa: E402

results: list[tuple[bool, str]] = []


def check(condition: bool, label: str) -> None:
    results.append((bool(condition), label))


def reconciles(conn) -> tuple[int, int]:
    with conn.cursor() as cur:
        cur.execute("""
            WITH ledger AS (
                SELECT q.id,
                       COALESCE(SUM(f.direction * f.quantity), 0) AS from_ledger,
                       q.projected_quantity AS projection
                FROM inventory_quantum q
                LEFT JOIN resource_flow f ON f.quantum_id = q.id
                GROUP BY q.id, q.projected_quantity)
            SELECT count(*) AS total,
                   count(*) FILTER (WHERE abs(from_ledger - projection) < 0.000001) AS agree
            FROM ledger""")
        r = cur.fetchone()
    return r["total"], r["agree"]


def pick_quantum(conn, *, condition="usable", lot_tracked=True, min_qty=10):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT q.id, q.projected_quantity, l.code AS lot_code "
            "FROM inventory_quantum q LEFT JOIN lot l ON l.id = q.lot_id "
            "WHERE q.tenant_id=%s AND q.condition=%s AND q.projected_quantity > %s "
            f"  AND q.lot_id IS {'NOT NULL' if lot_tracked else 'NULL'} "
            "ORDER BY q.projected_quantity DESC LIMIT 1",
            (TENANT_ID, condition, min_qty))
        return cur.fetchone()


def main() -> int:
    # Use the application's own connect(): unprivileged, tenant
    # declared. A raw psycopg.connect() here sees nothing under RLS,
    # which is the database behaving correctly and the test being wrong.
    conn = connect()
    cmd = Commands(conn, actor_code="EMP-MOKAFOR")

    before_total, before_agree = reconciles(conn)
    check(before_total == before_agree,
          f"ledger reconciles before commands ({before_agree}/{before_total})")

    # --- refusals ----------------------------------------------------
    q = pick_quantum(conn)
    check(q is not None, "found a lot-tracked quantum to work with")
    if q is None:
        conn.close()
        return report()

    r = cmd.move_quantum(quantum_id=str(uuid.uuid4()), to_location_code="WIP",
                         quantity=1, reason="test", dry_run=True)
    check(not r.ok and "does not resolve" in " ".join(r.refusals),
          "move_quantum refuses an unresolvable quantum (Pillar 6)")

    r = cmd.move_quantum(quantum_id=q["id"], to_location_code="NOWHERE-99",
                         quantity=1, reason="test", dry_run=True)
    check(not r.ok, "move_quantum refuses an unknown location")

    r = cmd.move_quantum(quantum_id=q["id"], to_location_code="WIP",
                         quantity=q["projected_quantity"] + 1000,
                         reason="test", dry_run=True)
    check(not r.ok and any("only" in x for x in r.refusals),
          "move_quantum refuses to move more than is on hand")

    r = cmd.change_condition(quantum_id=q["id"], to_condition="banana",
                             reason="test", dry_run=True)
    check(not r.ok and any("registered" in x for x in r.refusals),
          "change_condition refuses a term not in the vocabulary (Pillar 4)")

    r = cmd.change_condition(quantum_id=q["id"], to_condition="quarantined_pending_disposition",
                             reason="", dry_run=True)
    check(not r.ok and any("reason" in x for x in r.refusals),
          "change_condition requires a reason")

    r = cmd.split_lot(quantum_id=q["id"], quantity=q["projected_quantity"],
                      new_lot_code=f"SPLIT-{uuid.uuid4().hex[:6]}",
                      reason="test", dry_run=True)
    check(not r.ok and any("rename" in x for x in r.refusals),
          "split_lot refuses a whole-lot split")

    untracked = pick_quantum(conn, lot_tracked=False)
    if untracked:
        r = cmd.split_lot(quantum_id=untracked["id"], quantity=1,
                          new_lot_code=f"SPLIT-{uuid.uuid4().hex[:6]}",
                          reason="test", dry_run=True)
        check(not r.ok and any("not lot-tracked" in x for x in r.refusals),
              "split_lot refuses an untracked quantum")

    held = pick_quantum(conn, condition="quarantined_pending_disposition", min_qty=0)
    if held:
        r = cmd.move_quantum(quantum_id=held["id"], to_location_code="WIP",
                             quantity=1, reason="test", dry_run=True)
        check(not r.ok and any("change_condition" in x for x in r.refusals),
              "move_quantum refuses to move quarantined stock")

    # --- dry run writes nothing --------------------------------------
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM flow_event")
        events_before = cur.fetchone()["n"]
    cmd.move_quantum(quantum_id=q["id"], to_location_code="WIP",
                     quantity=1, reason="test", dry_run=True)
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM flow_event")
        check(cur.fetchone()["n"] == events_before,
              "dry run writes no events")

    # --- real execution ----------------------------------------------
    start_qty = q["projected_quantity"]
    r = cmd.move_quantum(quantum_id=q["id"], to_location_code="WIP",
                         quantity=5, reason="staging", dry_run=False)
    check(r.ok and r.events, "move_quantum executes")

    child_code = f"SPLIT-{uuid.uuid4().hex[:6]}"
    r = cmd.split_lot(quantum_id=q["id"], quantity=3, new_lot_code=child_code,
                      reason="quality_hold", dry_run=False)
    check(r.ok, "split_lot executes")
    if r.ok:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) AS n FROM lot_genealogy g JOIN lot c "
                " ON c.id = g.child_lot_id WHERE c.code=%s AND g.relation='split_from'",
                (child_code,))
            check(cur.fetchone()["n"] == 1,
                  "split_lot records genealogy back to the parent lot")

    r = cmd.post_count_result(quantum_id=q["id"],
                              counted_quantity=float(start_qty) - 9.0,
                              dry_run=False)
    check(r.ok, "post_count_result executes")
    with conn.cursor() as cur:
        cur.execute("SELECT last_verified_at, last_verified_method, "
                    " belief_confidence FROM inventory_quantum WHERE id=%s",
                    (q["id"],))
        row = cur.fetchone()
    check(row["last_verified_at"] is not None
          and row["last_verified_method"] == "cycle_count",
          "post_count_result advances last_verified_at (Pillar 8)")
    check(float(row["belief_confidence"]) > 0.9,
          "counting restores belief confidence")

    conn.commit()

    after_total, after_agree = reconciles(conn)
    check(after_total == after_agree,
          f"ledger STILL reconciles after commands ({after_agree}/{after_total})")

    with conn.cursor() as cur:
        cur.execute("""
            WITH chain AS (SELECT sequence_no, entry_hash, prev_hash,
                   LAG(entry_hash) OVER (ORDER BY sequence_no) AS expected
                   FROM flow_event)
            SELECT count(*) FILTER (WHERE prev_hash IS DISTINCT FROM expected) AS broken
            FROM chain""")
        check(cur.fetchone()["broken"] == 0,
              "hash chain intact after commands wrote through Ledger")

    with cur_pairs(conn) as bad:
        check(bad == 0, "every flow pair written by commands is balanced")

    conn.close()
    return report()


class cur_pairs:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        with self.conn.cursor() as cur:
            cur.execute("SELECT count(*) AS bad FROM ("
                        " SELECT pair_key, count(*) h, SUM(direction*quantity) n"
                        " FROM resource_flow GROUP BY pair_key) p "
                        "WHERE h <> 2 OR abs(n) > 0.000001")
            return cur.fetchone()["bad"]

    def __exit__(self, *a):
        return False


def report() -> int:
    passed = sum(1 for ok, _ in results if ok)
    print(f"core commands: {passed}/{len(results)} checks passed")
    for ok, label in results:
        print(f"  {'ok  ' if ok else 'FAIL'}  {label}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
