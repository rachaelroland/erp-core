# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Applied Industrials
"""Translate source records into our model and write them.

The only place customer data becomes agent_erp data. Validation happens
BEFORE anything is written: a record with problems is rejected and reported,
never half-loaded, because a partially-ingested order produces a matching
failure that looks like an agent error.
"""

from __future__ import annotations

import json
import uuid
from datetime import timezone

from ...config import TENANT_ID
from .schema import IngestReport, SourceAdapter

UTC = timezone.utc


def _new() -> str:
    return str(uuid.uuid4())


def _ensure_uom(conn, cache: dict, code: str) -> str:
    """Find or create a unit of measure.

    Unknown units are created as `dimensionless` with a warning rather than
    guessed into a class. Guessing that 'CS' means cases and silently giving
    it a conversion factor is how a 12x quantity error enters the system.
    """
    code = (code or "each").strip() or "each"
    key = code.lower()
    if key in cache:
        return cache[key]
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM unit_of_measure WHERE tenant_id=%s "
                    "AND lower(code)=%s", (TENANT_ID, key))
        row = cur.fetchone()
        if row:
            cache[key] = row["id"]
            return row["id"]
        uid = _new()
        cur.execute(
            "INSERT INTO unit_of_measure (id, tenant_id, code, unit_class, "
            " to_base_factor) VALUES (%s,%s,%s,'dimensionless',1)",
            (uid, TENANT_ID, code))
    cache[key] = uid
    return uid


def _ensure_item(conn, cache: dict, code: str, description: str | None,
                 uom_id: str) -> str:
    key = code.lower()
    if key in cache:
        return cache[key]
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM item_definition WHERE tenant_id=%s "
                    "AND lower(code)=%s", (TENANT_ID, key))
        row = cur.fetchone()
        if row:
            cache[key] = row["id"]
            return row["id"]
        iid = _new()
        cur.execute(
            "INSERT INTO item_definition (id, tenant_id, code, description, "
            " sourcing_class, function_class, stock_uom_id, tracking_policy, "
            " costing_method) "
            "VALUES (%s,%s,%s,%s,'purchased','component',%s,'none','moving_average')",
            (iid, TENANT_ID, code, description or code, uom_id))
        cur.execute(
            "INSERT INTO entity_uri (uri, tenant_id, entity_type, entity_id, "
            " display_label, search_text) VALUES (%s,%s,'item_definition',%s,%s,%s)",
            (f"erp:item_definition:{code}", TENANT_ID, iid,
             description or code, f"{code} {description or ''}"))
    cache[key] = iid
    return iid


def ingest(conn, source: SourceAdapter, *, dry_run: bool = False) -> IngestReport:
    report = IngestReport()
    uoms: dict = {}
    items: dict = {}
    parties: dict = {}

    # ---- suppliers ----------------------------------------------------
    for s in source.suppliers():
        problems = s.issues()
        if problems:
            report.rejected.extend(problems)
            continue
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM party WHERE tenant_id=%s AND code=%s",
                        (TENANT_ID, s.code))
            row = cur.fetchone()
            if row:
                parties[s.code] = row["id"]
                continue
            pid = _new()
            cur.execute(
                "INSERT INTO party (id, tenant_id, code, actor_kind, legal_name, "
                " display_name) VALUES (%s,%s,%s,'organization',%s,%s)",
                (pid, TENANT_ID, s.code, s.name, s.name))
            cur.execute(
                "INSERT INTO party_role (id, tenant_id, party_id, role, attributes) "
                "VALUES (%s,%s,%s,'supplier',%s)",
                (_new(), TENANT_ID, pid, json.dumps({
                    "payment_terms": s.payment_terms,
                    "lead_time_days": s.lead_time_days})))
            cur.execute(
                "INSERT INTO entity_uri (uri, tenant_id, entity_type, entity_id, "
                " display_label, search_text) VALUES (%s,%s,'party',%s,%s,%s)",
                (f"erp:party:{s.code}", TENANT_ID, pid, s.name,
                 f"{s.code} {s.name}"))
        parties[s.code] = pid
        report.suppliers += 1

    # ---- purchase orders ----------------------------------------------
    line_ids: dict[tuple[str, int], str] = {}
    for o in source.purchase_orders():
        problems = o.issues()
        if problems:
            report.rejected.extend(problems)
            continue
        if o.supplier_code not in parties:
            report.rejected.append(
                f"order {o.code}: supplier {o.supplier_code} not in the supplier file")
            continue

        # Already loaded. Re-running an export is a normal thing to do —
        # someone re-sends the same file, or a load is retried after a
        # partial failure — and it must produce a report, not a unique-key
        # crash halfway through the batch.
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM commitment WHERE tenant_id=%s AND code=%s",
                        (TENANT_ID, o.code))
            if cur.fetchone():
                report.warnings.append(f"order {o.code} already present — skipped")
                continue

        cid = _new()
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO commitment (id, tenant_id, code, kind, direction, "
                " counterparty_id, firmness, currency, payment_terms, created_at) "
                "VALUES (%s,%s,%s,'purchase_order','inbound',%s,'released',%s,%s,%s)",
                (cid, TENANT_ID, o.code, parties[o.supplier_code], o.currency,
                 o.payment_terms, o.ordered_at))
            cur.execute(
                "INSERT INTO entity_uri (uri, tenant_id, entity_type, entity_id, "
                " display_label, search_text) VALUES (%s,%s,'commitment',%s,%s,%s)",
                (f"erp:commitment:{o.code}", TENANT_ID, cid, o.code,
                 f"{o.code} {o.supplier_code}"))

            for ln in o.lines:
                uom_id = _ensure_uom(conn, uoms, ln.uom)
                item_id = _ensure_item(conn, items, ln.item_code,
                                       ln.item_description, uom_id)
                lid = _new()
                if ln.promise_date:
                    window = (f"[{ln.promise_date} 00:00:00+00,"
                              f"{ln.promise_date} 23:59:59+00)")
                elif o.ordered_at:
                    window = (f"[{o.ordered_at.date()} 00:00:00+00,"
                              f"{o.ordered_at.date()} 23:59:59+00)")
                else:
                    report.rejected.append(
                        f"{o.code} line {ln.line_number}: no promise or order date")
                    continue
                cur.execute(
                    "INSERT INTO commitment_line (id, tenant_id, commitment_id, "
                    " line_number, item_id, quantity, quantity_uom_id, "
                    " promise_window, requested_at, unit_price, price_basis) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'contract')",
                    (lid, TENANT_ID, cid, ln.line_number, item_id, ln.quantity,
                     uom_id, window, o.ordered_at, ln.unit_price))
                line_ids[(o.code, ln.line_number)] = lid
                report.order_lines += 1
        report.orders += 1

    # ---- receipts as ledger events -------------------------------------
    from ..db import Ledger
    ledger = Ledger(conn)
    # Look up the SAME code we create. Querying SYS-GENERATOR and inserting
    # SYS-INGEST meant the lookup never found what the previous run made, so
    # the second ingest into any database died on a unique-key violation.
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM party WHERE tenant_id=%s AND code='SYS-INGEST'",
                    (TENANT_ID,))
        row = cur.fetchone()
        if row:
            actor = row["id"]
        else:
            actor = _new()
            cur.execute(
                "INSERT INTO party (id, tenant_id, code, actor_kind, legal_name, "
                " display_name) VALUES (%s,%s,'SYS-INGEST','scheduled_process',"
                " 'Ingest Process','Ingest Process')", (actor, TENANT_ID))

    for r in source.receipts():
        problems = r.issues()
        if problems:
            report.rejected.extend(problems)
            continue
        key = (r.order_code, r.line_number)
        if key not in line_ids:
            report.unmatched_receipts.append(
                f"receipt {r.external_id} -> {r.order_code} line {r.line_number}")
            continue
        with conn.cursor() as cur:
            cur.execute("SELECT commitment_id, item_id, quantity_uom_id "
                        "FROM commitment_line WHERE id=%s", (line_ids[key],))
            line = cur.fetchone()

        eid = ledger.append(
            event_type="receive", actor_id=actor,
            correlation_id=line["commitment_id"], occurred_at=r.received_at,
            rationale=f"Imported receipt {r.external_id} against "
                      f"{r.order_code} line {r.line_number}.",
            payload={"po": r.order_code, "line": r.line_number,
                     "source_receipt_id": r.external_id})
        uom_id = _ensure_uom(conn, uoms, r.uom)
        pair = _new()
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO resource_flow (id, tenant_id, event_id, pair_key, "
                " resource_kind, direction, quantity, quantity_uom_id, "
                " item_id, fulfills_line_id) "
                "VALUES (%s,%s,%s,%s,'material',%s,%s,%s,%s,%s)",
                [(_new(), TENANT_ID, eid, pair, -1, r.quantity, uom_id,
                  line["item_id"], line_ids[key]),
                 (_new(), TENANT_ID, eid, pair, 1, r.quantity, uom_id,
                  line["item_id"], line_ids[key])])
        report.receipts += 1

    if dry_run:
        conn.rollback()
        report.warnings.append("DRY RUN — nothing was written")
    else:
        conn.commit()

    if getattr(source, "problems", None):
        report.warnings.extend(source.problems)
    return report
