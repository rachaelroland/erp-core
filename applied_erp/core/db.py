# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Applied Industrials
"""Database access and the ledger writer."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone

import psycopg
from psycopg.rows import dict_row

from ..config import ROLE_ADMIN, ROLE_APP, TENANT_ID, dsn


def connect(role: str = ROLE_APP, tenant_id: str = TENANT_ID):
    """Open a connection and DECLARE ITS TENANT.

    The tenant is set here rather than left to callers: the RLS predicate
    reads it, and an unset value matches no row. Forgetting it yields empty
    results rather than a leak — but empty results are still a bug, and
    setting it in one place means it cannot be forgotten in another.
    """
    conn = psycopg.connect(dsn(role), row_factory=dict_row)
    conn.autocommit = False
    with conn.cursor() as cur:
        cur.execute("SELECT set_config('agent_erp.tenant_id', %s, false)",
                    (tenant_id,))
    conn.commit()
    return conn


def connect_admin(tenant_id: str = TENANT_ID):
    """The migration/fixture role: DDL, test-data construction, and the
    private eval harness. Never for serving a request."""
    return connect(ROLE_ADMIN, tenant_id)


def new_id() -> str:
    return str(uuid.uuid4())


class Ledger:
    """The only thing in the system that appends to the flow ledger.

    Centralised on purpose: the hash chain, the actor attribution, and the
    duality pairing are invariants, and invariants enforced in one place are
    invariants. Commands call this; nothing else writes flow_event.
    """

    def __init__(self, conn):
        self.conn = conn
        self._prev = self._current_head()

    def _current_head(self) -> bytes | None:
        with self.conn.cursor() as cur:
            cur.execute("SELECT entry_hash FROM flow_event "
                        "ORDER BY sequence_no DESC LIMIT 1")
            row = cur.fetchone()
        return row["entry_hash"] if row else None

    def append(self, *, event_type: str, actor_id: str, correlation_id: str,
               occurred_at: datetime | None = None, rationale: str,
               proposal_id: str | None = None, reason_code: str | None = None,
               caused_by: str | None = None, payload: dict | None = None,
               on_behalf_of: str | None = None) -> str:
        """Append one event, extending the hash chain."""
        occurred_at = occurred_at or datetime.now(timezone.utc)
        recorded_at = datetime.now(timezone.utc)
        eid = new_id()
        body = {
            "id": eid, "type": event_type,
            "occurred_at": occurred_at.isoformat(),
            "recorded_at": recorded_at.isoformat(),
            "actor": actor_id, "correlation": correlation_id,
            "reason": reason_code, "payload": payload or {},
        }
        canonical = json.dumps(body, sort_keys=True, default=str).encode()
        entry_hash = hashlib.sha256((self._prev or b"") + canonical).digest()

        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO flow_event (id, tenant_id, event_type, occurred_at, "
                " recorded_at, actor_id, on_behalf_of_id, correlation_id, "
                " caused_by_event_id, proposal_id, reason_code, payload, "
                " rationale, prev_hash, entry_hash) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (eid, TENANT_ID, event_type, occurred_at, recorded_at, actor_id,
                 on_behalf_of, correlation_id, caused_by, proposal_id,
                 reason_code, json.dumps(payload or {}), rationale,
                 self._prev, entry_hash),
            )
        self._prev = entry_hash
        return eid

    def money_pair(self, *, event_id: str, amount: float, credit_party: str,
                   debit_account: str, uom_id: str,
                   cost_component: str = "material") -> None:
        """Write the balanced money pair for a liability accrual."""
        pair = new_id()
        rows = [
            (new_id(), TENANT_ID, event_id, pair, "money", -1, amount, uom_id,
             None, amount, cost_component, credit_party, None),
            (new_id(), TENANT_ID, event_id, pair, "money", 1, amount, uom_id,
             None, amount, cost_component, None, debit_account),
        ]
        with self.conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO resource_flow (id, tenant_id, event_id, pair_key, "
                " resource_kind, direction, quantity, quantity_uom_id, unit_cost, "
                " extended_cost, cost_component, party_id, account_code) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", rows)
