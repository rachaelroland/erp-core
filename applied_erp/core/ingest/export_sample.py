# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Applied Industrials
"""Export the synthetic shop as a customer-shaped CSV set.

Two jobs:
  1. Round-trip test — export, wipe, re-ingest, compare. That is how we know
     the adapter works before pointing it at a customer, and it exercises the
     boundary in both directions.
  2. A concrete example of the file format to hand a prospect's IT contact,
     so the ask is "produce these three files" rather than "give us access".

Column names here deliberately do NOT match our internal names — they are
plausible generic ERP export headers, so the mapping layer is genuinely
exercised rather than being an identity function.
"""

from __future__ import annotations

import csv
from pathlib import Path

from ...config import TENANT_ID


def export(conn, out_dir: str | Path) -> dict:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    counts = {}

    with conn.cursor() as cur:
        cur.execute(
            "SELECT p.code, p.legal_name, r.attributes "
            "FROM party p JOIN party_role r ON r.party_id = p.id "
            "WHERE r.role='supplier' AND p.tenant_id=%s ORDER BY p.code",
            (TENANT_ID,))
        rows = cur.fetchall()
    with (out / "suppliers.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["supplier_code", "supplier_name", "terms", "lead_days"])
        for r in rows:
            a = r["attributes"] or {}
            w.writerow([r["code"], r["legal_name"],
                        a.get("payment_terms", ""), a.get("lead_time_days", "")])
    counts["suppliers"] = len(rows)

    with conn.cursor() as cur:
        cur.execute(
            "SELECT c.code AS po, sp.code AS supplier, c.created_at, c.currency, "
            "       c.payment_terms, cl.line_number, i.code AS item, "
            "       i.description, cl.quantity, u.code AS uom, cl.unit_price, "
            "       lower(cl.promise_window) AS due "
            "FROM commitment c "
            "JOIN party sp ON sp.id = c.counterparty_id "
            "JOIN commitment_line cl ON cl.commitment_id = c.id "
            "JOIN item_definition i ON i.id = cl.item_id "
            "JOIN unit_of_measure u ON u.id = cl.quantity_uom_id "
            "WHERE c.kind='purchase_order' AND c.tenant_id=%s "
            "ORDER BY c.code, cl.line_number", (TENANT_ID,))
        rows = cur.fetchall()
    with (out / "purchase_orders.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["po_number", "supplier_code", "order_date", "currency",
                    "terms", "line_no", "item_number", "description",
                    "qty_ordered", "uom", "unit_price", "due_date"])
        for r in rows:
            # US date order and thousands separators on purpose: the mapping
            # layer has to cope with formats we did not choose.
            w.writerow([
                r["po"], r["supplier"],
                r["created_at"].strftime("%m/%d/%Y") if r["created_at"] else "",
                r["currency"], r["payment_terms"] or "", r["line_number"],
                r["item"], r["description"], f"{r['quantity']:,.2f}",
                r["uom"], f"{r['unit_price']:.4f}",
                r["due"].strftime("%m/%d/%Y") if r["due"] else "",
            ])
    counts["order_lines"] = len(rows)

    with conn.cursor() as cur:
        cur.execute(
            "SELECT e.id, c.code AS po, (e.payload->>'line')::int AS line_no, "
            "       rf.quantity, u.code AS uom, e.occurred_at, l.code AS lot "
            "FROM flow_event e "
            "JOIN resource_flow rf ON rf.event_id = e.id AND rf.direction = 1 "
            "JOIN unit_of_measure u ON u.id = rf.quantity_uom_id "
            "JOIN commitment c ON c.id = e.correlation_id "
            "LEFT JOIN lot l ON l.id = rf.lot_id "
            "WHERE e.event_type='receive' AND e.tenant_id=%s "
            "ORDER BY e.occurred_at", (TENANT_ID,))
        rows = cur.fetchall()
    with (out / "receipts.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["receipt_id", "po_number", "line_no", "qty_received",
                    "uom", "receipt_date", "lot"])
        for r in rows:
            w.writerow([f"RCP-{str(r['id'])[:8]}", r["po"], r["line_no"],
                        f"{r['quantity']:,.2f}", r["uom"],
                        r["occurred_at"].strftime("%m/%d/%Y"), r["lot"] or ""])
    counts["receipts"] = len(rows)
    return counts
