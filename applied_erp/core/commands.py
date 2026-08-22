# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Applied Industrials
"""Core domain commands — the inventory and production verbs.

Pillar 5: agents get domain verbs, never CRUD. But these are not "agent
commands" — they are the ERP's write surface, and a human clicking a button
goes through exactly the same path. That is the point. If there were a
separate human path, the invariants below would exist twice and diverge.

Every command:
  * validates preconditions and returns a structured refusal, never a
    half-applied write,
  * declares a blast radius from a DRY RUN, which the policy engine decides on,
  * writes through Ledger, so the hash chain and the duality pairing are
    maintained in one place.

No LLM dependency lives here or may ever be added — this module is part of
the open-source core (docs/12).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

from ..config import TENANT_ID
from .db import Ledger

UTC = timezone.utc


def _new() -> str:
    return str(uuid.uuid4())


@dataclass
class CommandResult:
    ok: bool
    command: str
    blast_radius: dict = field(default_factory=dict)
    events: list[str] = field(default_factory=list)
    refusals: list[str] = field(default_factory=list)
    detail: dict = field(default_factory=dict)

    def __str__(self) -> str:
        if self.ok:
            return (f"{self.command}: ok, {len(self.events)} event(s), "
                    f"blast={self.blast_radius}")
        return f"{self.command}: REFUSED — " + "; ".join(self.refusals)


class Commands:
    """The core write surface.

    Constructed with a connection; each method takes URIs or codes, never
    raw ids, so a caller cannot smuggle in an unresolved reference
    (Pillar 6).
    """

    def __init__(self, conn, actor_code: str):
        self.conn = conn
        self.ledger = Ledger(conn)
        self.actor_code = actor_code
        self._actor_id = self._lookup("party", actor_code)

    # -- helpers -------------------------------------------------------

    def _lookup(self, entity_type: str, code: str) -> str | None:
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT entity_id FROM entity_uri WHERE tenant_id=%s "
                "AND entity_type=%s AND uri=%s",
                (TENANT_ID, entity_type, f"erp:{entity_type}:{code}"))
            row = cur.fetchone()
        if row:
            return row["entity_id"]
        # Fall back to the table's natural key: not every entity is
        # registered, but a caller should still not be able to invent one.
        table = {"party": "party", "item_definition": "item_definition",
                 "location_node": "location_node", "lot": "lot",
                 "job": "job", "commitment": "commitment"}.get(entity_type)
        if not table:
            return None
        with self.conn.cursor() as cur:
            cur.execute(f"SELECT id FROM {table} WHERE tenant_id=%s AND code=%s",
                        (TENANT_ID, code))
            row = cur.fetchone()
        return row["id"] if row else None

    def _quantum(self, quantum_id: str) -> dict | None:
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT q.*, i.code AS item_code, l.code AS location_code, "
                "       lo.code AS lot_code "
                "FROM inventory_quantum q "
                "JOIN item_definition i ON i.id = q.item_id "
                "JOIN location_node l ON l.id = q.location_id "
                "LEFT JOIN lot lo ON lo.id = q.lot_id "
                "WHERE q.id = %s AND q.tenant_id = %s", (quantum_id, TENANT_ID))
            return cur.fetchone()

    def _write_pair(self, event_id: str, *, quantity, uom_id, credit: dict,
                    debit: dict, unit_cost=None) -> None:
        pair = _new()
        ext = (Decimal(str(quantity)) * Decimal(str(unit_cost))
               if unit_cost is not None else None)
        rows = []
        for direction, side in ((-1, credit), (1, debit)):
            row = {"id": _new(), "tenant_id": TENANT_ID, "event_id": event_id,
                   "pair_key": pair, "resource_kind": "material",
                   "direction": direction, "quantity": quantity,
                   "quantity_uom_id": uom_id, "unit_cost": unit_cost,
                   "extended_cost": ext, "cost_component": "material"}
            row.update(side)
            rows.append(row)
        # The two halves need not carry identical keys — a count adjustment
        # names a quantum on one side and only an item/location on the other.
        # Take the union and fill the gaps, rather than assuming they match.
        cols = list({k for row in rows for k in row})
        with self.conn.cursor() as cur:
            cur.executemany(
                f"INSERT INTO resource_flow ({', '.join(cols)}) "
                f"VALUES ({', '.join(['%s'] * len(cols))})",
                [tuple(r.get(c) for c in cols) for r in rows])

    def _adjust(self, quantum_id: str, delta) -> None:
        """Keep the maintained projection in step with the ledger.

        `projected_quantity` is a read model, not the truth — verify.sql §2
        proves the two agree, and that proof is void if a command writes
        flows without updating it.
        """
        with self.conn.cursor() as cur:
            cur.execute(
                "UPDATE inventory_quantum SET projected_quantity = "
                " projected_quantity + %s WHERE id = %s", (delta, quantum_id))

    def _find_or_create_quantum(self, *, item_id, location_id, uom_id,
                                lot_id=None, condition="usable",
                                ownership="owned", encumbrance="free") -> str:
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM inventory_quantum WHERE tenant_id=%s "
                " AND item_id=%s AND location_id=%s "
                " AND lot_id IS NOT DISTINCT FROM %s AND condition=%s "
                " AND ownership=%s AND encumbrance=%s AND closed_at IS NULL",
                (TENANT_ID, item_id, location_id, lot_id, condition,
                 ownership, encumbrance))
            row = cur.fetchone()
            if row:
                return row["id"]
            qid = _new()
            cur.execute(
                "INSERT INTO inventory_quantum (id, tenant_id, item_id, lot_id, "
                " location_id, condition, ownership, encumbrance, "
                " projected_quantity, quantity_uom_id) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,0,%s)",
                (qid, TENANT_ID, item_id, lot_id, location_id, condition,
                 ownership, encumbrance, uom_id))
        return qid

    # -- move_quantum ---------------------------------------------------

    def move_quantum(self, *, quantum_id: str, to_location_code: str,
                     quantity, reason: str, dry_run: bool = False) -> CommandResult:
        """Move stock between locations. R1: fully reversible."""
        r = CommandResult(ok=False, command="move_quantum")
        q = self._quantum(quantum_id)
        to_id = self._lookup("location_node", to_location_code)
        qty = Decimal(str(quantity))

        if q is None:
            r.refusals.append(f"quantum {quantum_id} does not resolve")
        if to_id is None:
            r.refusals.append(f"location {to_location_code} does not resolve")
        if qty <= 0:
            r.refusals.append("quantity must be greater than zero")
        if q and qty > q["projected_quantity"]:
            r.refusals.append(
                f"cannot move {qty}: only {q['projected_quantity']} on hand "
                f"at {q['location_code']}")
        if q and q["condition"] in ("nonconforming_reject", "quarantined_pending_disposition"):
            # Moving held stock is how quarantine gets defeated in practice.
            r.refusals.append(
                f"stock is {q['condition']}; use change_condition to release it "
                f"before moving")
        if r.refusals:
            return r

        r.blast_radius = {"records_touched": 2, "monetary_magnitude": 0,
                          "downstream_lines": 0, "external_parties_notified": 0,
                          "correlation_value": q["item_code"]}
        if dry_run:
            r.ok = True
            r.detail = {"from": q["location_code"], "to": to_location_code,
                        "quantity": float(qty), "item": q["item_code"]}
            return r

        eid = self.ledger.append(
            event_type="move", actor_id=self._actor_id, correlation_id=_new(),
            reason_code=reason,
            rationale=f"Move {qty} {q['item_code']} from {q['location_code']} "
                      f"to {to_location_code}.",
            payload={"item": q["item_code"], "quantity": float(qty),
                     "from": q["location_code"], "to": to_location_code})
        dest = self._find_or_create_quantum(
            item_id=q["item_id"], location_id=to_id, uom_id=q["quantity_uom_id"],
            lot_id=q["lot_id"], condition=q["condition"],
            ownership=q["ownership"], encumbrance=q["encumbrance"])
        self._write_pair(
            eid, quantity=qty, uom_id=q["quantity_uom_id"],
            credit={"quantum_id": quantum_id, "item_id": q["item_id"],
                    "lot_id": q["lot_id"], "location_id": q["location_id"]},
            debit={"quantum_id": dest, "item_id": q["item_id"],
                   "lot_id": q["lot_id"], "location_id": to_id})
        self._adjust(quantum_id, -qty)
        self._adjust(dest, qty)
        r.ok, r.events = True, [eid]
        return r

    # -- change_condition -----------------------------------------------

    def change_condition(self, *, quantum_id: str, to_condition: str,
                         reason: str, dry_run: bool = False) -> CommandResult:
        """Re-disposition stock (inspection outcome, quarantine, release)."""
        r = CommandResult(ok=False, command="change_condition")
        q = self._quantum(quantum_id)

        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM semantic_term WHERE vocabulary='inventory_condition' "
                " AND term=%s AND deprecated_at IS NULL", (to_condition,))
            known = cur.fetchone() is not None

        if q is None:
            r.refusals.append(f"quantum {quantum_id} does not resolve")
        if not known:
            # Pillar 4: the vocabulary is data, and a value not in it is not
            # a value. This is what stops a caller inventing a status.
            r.refusals.append(
                f"'{to_condition}' is not a registered inventory_condition")
        if q and q["condition"] == to_condition:
            r.refusals.append(f"already {to_condition}")
        if q and q["projected_quantity"] <= 0:
            r.refusals.append("quantum holds no stock")
        if not reason:
            r.refusals.append("a reason is required to change condition")
        if r.refusals:
            return r

        qty = q["projected_quantity"]
        r.blast_radius = {"records_touched": 2, "monetary_magnitude": 0,
                          "downstream_lines": 0, "external_parties_notified": 0,
                          "correlation_value": q["item_code"]}
        if dry_run:
            r.ok = True
            r.detail = {"item": q["item_code"], "from": q["condition"],
                        "to": to_condition, "quantity": float(qty)}
            return r

        eid = self.ledger.append(
            event_type="change_condition", actor_id=self._actor_id,
            correlation_id=_new(), reason_code=reason,
            rationale=f"{q['item_code']} at {q['location_code']}: "
                      f"{q['condition']} -> {to_condition}.",
            payload={"item": q["item_code"], "from": q["condition"],
                     "to": to_condition, "quantity": float(qty)})
        dest = self._find_or_create_quantum(
            item_id=q["item_id"], location_id=q["location_id"],
            uom_id=q["quantity_uom_id"], lot_id=q["lot_id"],
            condition=to_condition, ownership=q["ownership"],
            encumbrance=q["encumbrance"])
        self._write_pair(
            eid, quantity=qty, uom_id=q["quantity_uom_id"],
            credit={"quantum_id": quantum_id, "item_id": q["item_id"],
                    "lot_id": q["lot_id"], "location_id": q["location_id"]},
            debit={"quantum_id": dest, "item_id": q["item_id"],
                   "lot_id": q["lot_id"], "location_id": q["location_id"]})
        self._adjust(quantum_id, -qty)
        self._adjust(dest, qty)
        r.ok, r.events = True, [eid]
        return r

    # -- split_lot -------------------------------------------------------

    def split_lot(self, *, quantum_id: str, quantity, new_lot_code: str,
                  reason: str, dry_run: bool = False) -> CommandResult:
        """Split part of a lot into a new one, preserving genealogy.

        The invariant that matters: the child lot must record that it came
        from the parent. A split that loses the link silently destroys
        traceability, and nothing downstream would notice until a recall.
        """
        r = CommandResult(ok=False, command="split_lot")
        q = self._quantum(quantum_id)
        qty = Decimal(str(quantity))

        if q is None:
            r.refusals.append(f"quantum {quantum_id} does not resolve")
        elif q["lot_id"] is None:
            r.refusals.append("quantum is not lot-tracked; nothing to split")
        elif qty >= q["projected_quantity"]:
            r.refusals.append(
                f"split quantity {qty} must be less than the {q['projected_quantity']} "
                f"on hand — a whole-lot 'split' is a rename, not a split")
        if qty <= 0:
            r.refusals.append("quantity must be greater than zero")

        with self.conn.cursor() as cur:
            cur.execute("SELECT 1 FROM lot WHERE tenant_id=%s AND code=%s",
                        (TENANT_ID, new_lot_code))
            if cur.fetchone():
                r.refusals.append(f"lot code {new_lot_code} already exists")
        if r.refusals:
            return r

        r.blast_radius = {"records_touched": 3, "monetary_magnitude": 0,
                          "downstream_lines": 0, "external_parties_notified": 0,
                          "correlation_value": q["lot_code"]}
        if dry_run:
            r.ok = True
            r.detail = {"parent_lot": q["lot_code"], "child_lot": new_lot_code,
                        "quantity": float(qty)}
            return r

        child = _new()
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO lot (id, tenant_id, code, item_id, origin_kind) "
                "VALUES (%s,%s,%s,%s,'split')",
                (child, TENANT_ID, new_lot_code, q["item_id"]))
        eid = self.ledger.append(
            event_type="split_lot", actor_id=self._actor_id,
            correlation_id=_new(), reason_code=reason,
            rationale=f"Split {qty} of lot {q['lot_code']} into {new_lot_code}.",
            payload={"parent_lot": q["lot_code"], "child_lot": new_lot_code,
                     "quantity": float(qty)})
        dest = self._find_or_create_quantum(
            item_id=q["item_id"], location_id=q["location_id"],
            uom_id=q["quantity_uom_id"], lot_id=child,
            condition=q["condition"], ownership=q["ownership"],
            encumbrance=q["encumbrance"])
        self._write_pair(
            eid, quantity=qty, uom_id=q["quantity_uom_id"],
            credit={"quantum_id": quantum_id, "item_id": q["item_id"],
                    "lot_id": q["lot_id"], "location_id": q["location_id"]},
            debit={"quantum_id": dest, "item_id": q["item_id"],
                   "lot_id": child, "location_id": q["location_id"]})
        self._adjust(quantum_id, -qty)
        self._adjust(dest, qty)
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO lot_genealogy (id, tenant_id, child_lot_id, "
                " parent_lot_id, relation, quantity, quantity_uom_id, "
                " flow_event_id) VALUES (%s,%s,%s,%s,'split_from',%s,%s,%s)",
                (_new(), TENANT_ID, child, q["lot_id"], qty,
                 q["quantity_uom_id"], eid))
        r.ok, r.events = True, [eid]
        r.detail = {"child_lot_id": child}
        return r

    # -- post_count_result ------------------------------------------------

    def post_count_result(self, *, quantum_id: str, counted_quantity,
                          reason: str = "cycle_count_variance",
                          dry_run: bool = False) -> CommandResult:
        """Record a physical count and reconcile the book to it.

        This is the command that makes Pillar 8 actionable: it is the only
        way `last_verified_at` advances, and therefore the only way belief
        confidence recovers.
        """
        r = CommandResult(ok=False, command="post_count_result")
        q = self._quantum(quantum_id)
        counted = Decimal(str(counted_quantity))

        if q is None:
            r.refusals.append(f"quantum {quantum_id} does not resolve")
        if counted < 0:
            r.refusals.append("counted quantity cannot be negative")
        if r.refusals:
            return r

        book = Decimal(str(q["projected_quantity"]))
        variance = counted - book
        r.blast_radius = {"records_touched": 1, "monetary_magnitude": 0,
                          "downstream_lines": 0, "external_parties_notified": 0,
                          "correlation_value": q["item_code"]}
        if dry_run:
            r.ok = True
            r.detail = {"book": float(book), "counted": float(counted),
                        "variance": float(variance)}
            return r

        now = datetime.now(UTC)
        if variance != 0:
            eid = self.ledger.append(
                event_type="cycle_count_adjust", actor_id=self._actor_id,
                correlation_id=_new(), reason_code=reason,
                rationale=f"Count of {q['item_code']} at {q['location_code']}: "
                          f"book {book}, counted {counted}.",
                payload={"item": q["item_code"], "book": float(book),
                         "counted": float(counted), "variance": float(variance)})
            side = {"quantum_id": quantum_id, "item_id": q["item_id"],
                    "lot_id": q["lot_id"], "location_id": q["location_id"]}
            other = {"item_id": q["item_id"], "location_id": q["location_id"]}
            credit, debit = (other, side) if variance > 0 else (side, other)
            self._write_pair(eid, quantity=abs(variance),
                             uom_id=q["quantity_uom_id"],
                             credit=credit, debit=debit)
            self._adjust(quantum_id, variance)
            r.events = [eid]

        with self.conn.cursor() as cur:
            cur.execute(
                "UPDATE inventory_quantum SET last_verified_at=%s, "
                " last_verified_method='cycle_count', last_verified_variance=%s, "
                " belief_confidence=0.990 WHERE id=%s",
                (now, variance, quantum_id))
        r.ok = True
        r.detail = {"book": float(book), "counted": float(counted),
                    "variance": float(variance)}
        return r
