# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Applied Industrials
"""The command registry — the agent's entire write surface (Pillar 5).

Agents never see tables. They see these commands. Each declares its argument
schema, preconditions, invariants, reversibility class, and whether it can be
dry-run — and the policy engine decides on the dry run's blast radius, never
on the agent's say-so.

Adding capability means adding a command with its own authority profile. It
must never mean adding a flag to an existing command that quietly widens what
that command can do.
"""

from __future__ import annotations

import json
import uuid

from ..config import TENANT_ID

COMMANDS = [
    {
        "command_name": "match_invoice_to_receipt",
        "summary": (
            "Match a supplier invoice document to the purchase-order lines and "
            "receipts it pays for, and record the resulting liability. Does NOT "
            "release payment."
        ),
        "argument_schema": {
            "type": "object",
            "required": ["document_uri", "commitment_code", "line_numbers",
                         "receipt_event_ids"],
            "properties": {
                "document_uri": {"type": "string",
                                 "description": "erp:evidence:<document_code>"},
                "commitment_code": {"type": "string",
                                    "description": "The purchase order this invoice pays"},
                "line_numbers": {"type": "array", "items": {"type": "integer"}},
                "receipt_event_ids": {"type": "array", "items": {"type": "string"}},
            },
        },
        "preconditions": [
            "document_uri resolves in entity_uri",
            "commitment_code resolves to a commitment of kind purchase_order",
            "every receipt_event_id is a 'receive' event against that commitment",
            "no prior committed match exists for this document",
        ],
        "postconditions": [
            "a match_invoice_to_receipt event exists citing the document",
            "the open exception_item for this document is resolved",
        ],
        "invariants": [
            "invoiced quantity may not exceed received quantity beyond tolerance",
            "invoiced unit price may not exceed PO price beyond tolerance",
            "an invoice number already matched may not be matched again",
        ],
        "reversibility": "R2_compensable",
        "emits_event_types": ["match_invoice_to_receipt", "accrue_receipt_liability"],
        "minimum_band": "act_within_limits",
        "supports_dry_run": True,
    },
    {
        "command_name": "raise_invoice_exception",
        "summary": (
            "Record that an invoice cannot be matched automatically and state why, "
            "routing it to a human with the evidence attached."
        ),
        "argument_schema": {
            "type": "object",
            "required": ["document_uri", "exception_type", "rationale"],
            "properties": {
                "document_uri": {"type": "string"},
                "exception_type": {"type": "string"},
                "rationale": {"type": "string"},
                "suspected_commitment_code": {"type": "string"},
            },
        },
        "preconditions": ["document_uri resolves in entity_uri"],
        "postconditions": ["an exception_item exists in state awaiting_human"],
        "invariants": [],
        "reversibility": "R1_reversible",
        "emits_event_types": [],
        "minimum_band": "suggest",
        "supports_dry_run": True,
    },
    {
        "command_name": "release_payment",
        "summary": (
            "Release payment against a matched supplier invoice. Externally "
            "visible and effectively irreversible once the transfer leaves."
        ),
        "argument_schema": {
            "type": "object",
            "required": ["document_uri", "amount", "remit_to_party_code"],
            "properties": {
                "document_uri": {"type": "string"},
                "amount": {"type": "number"},
                "remit_to_party_code": {"type": "string"},
            },
        },
        "preconditions": [
            "a committed match exists for this document",
            "remit_to_party_code has verified banking details on file",
        ],
        "postconditions": ["a release_payment event exists"],
        "invariants": [
            "the actor that matched the invoice may not also release it "
            "(duty_separation_rule SOD-MATCH-PAY)",
        ],
        # R3: a third party has seen it. NO agent holds a grant for this
        # command — deliberately, per 07_threat_model.md's single most
        # important control.
        "reversibility": "R3_externally_visible",
        "emits_event_types": ["release_payment"],
        "minimum_band": "act_with_notice",
        "supports_dry_run": True,
    },
    {
        "command_name": "request_cycle_count",
        "summary": "Ask the floor to physically count a quantum whose believed "
                   "quantity has decayed below useful confidence.",
        "argument_schema": {
            "type": "object",
            "required": ["quantum_id", "rationale"],
            "properties": {
                "quantum_id": {"type": "string"},
                "rationale": {"type": "string"},
                "priority": {"type": "string", "enum": ["routine", "urgent"]},
            },
        },
        "preconditions": ["quantum_id resolves and is not closed"],
        "postconditions": ["a count request exception_item exists"],
        "invariants": [],
        "reversibility": "R1_reversible",
        "emits_event_types": [],
        "minimum_band": "act_with_notice",
        "supports_dry_run": True,
    },
    {
        "command_name": "move_quantum",
        "summary": "Move stock between locations. Condition, ownership and "
                   "encumbrance travel with it unchanged.",
        "argument_schema": {"type": "object",
            "required": ["quantum_id", "to_location_code", "quantity", "reason"],
            "properties": {"quantum_id": {"type": "string"},
                           "to_location_code": {"type": "string"},
                           "quantity": {"type": "number"},
                           "reason": {"type": "string"}}},
        "preconditions": ["quantum resolves and is open",
                          "destination location resolves",
                          "quantity does not exceed quantity on hand",
                          "stock is not quarantined or rejected"],
        "postconditions": ["a move event exists", "both quanta reconcile to the ledger"],
        "invariants": ["quarantined stock cannot be moved without a disposition first",
                       "a move never changes total quantity on hand"],
        "reversibility": "R1_reversible",
        "emits_event_types": ["move"],
        "minimum_band": "act_within_limits",
        "supports_dry_run": True,
    },
    {
        "command_name": "change_condition",
        "summary": "Re-disposition stock — inspection outcome, quarantine, "
                   "release, or scrap classification.",
        "argument_schema": {"type": "object",
            "required": ["quantum_id", "to_condition", "reason"],
            "properties": {"quantum_id": {"type": "string"},
                           "to_condition": {"type": "string"},
                           "reason": {"type": "string"}}},
        "preconditions": ["quantum resolves and holds stock",
                          "to_condition is a registered inventory_condition term",
                          "a reason is supplied"],
        "postconditions": ["a change_condition event exists"],
        "invariants": ["the target condition must exist in the vocabulary — "
                       "a caller may not invent a status"],
        "reversibility": "R2_compensable",
        "emits_event_types": ["change_condition"],
        "minimum_band": "act_within_limits",
        "supports_dry_run": True,
    },
    {
        "command_name": "split_lot",
        "summary": "Split part of a lot into a new lot, preserving genealogy.",
        "argument_schema": {"type": "object",
            "required": ["quantum_id", "quantity", "new_lot_code", "reason"],
            "properties": {"quantum_id": {"type": "string"},
                           "quantity": {"type": "number"},
                           "new_lot_code": {"type": "string"},
                           "reason": {"type": "string"}}},
        "preconditions": ["quantum is lot-tracked",
                          "split quantity is less than quantity on hand",
                          "the new lot code is unused"],
        "postconditions": ["a lot_genealogy row links child to parent"],
        "invariants": ["a split MUST record the parent link — losing it "
                       "destroys traceability silently, and nothing notices "
                       "until a recall"],
        "reversibility": "R2_compensable",
        "emits_event_types": ["split_lot"],
        "minimum_band": "act_within_limits",
        "supports_dry_run": True,
    },
    {
        "command_name": "post_count_result",
        "summary": "Record a physical count and reconcile the book to it. The "
                   "only command that advances last_verified_at.",
        "argument_schema": {"type": "object",
            "required": ["quantum_id", "counted_quantity"],
            "properties": {"quantum_id": {"type": "string"},
                           "counted_quantity": {"type": "number"},
                           "reason": {"type": "string"}}},
        "preconditions": ["quantum resolves", "counted quantity is not negative"],
        "postconditions": ["last_verified_at advances and belief confidence resets",
                           "any variance is posted as a compensating flow"],
        "invariants": ["the book is reconciled TO the count, never the reverse"],
        "reversibility": "R2_compensable",
        "emits_event_types": ["cycle_count_adjust"],
        "minimum_band": "act_with_notice",
        "supports_dry_run": True,
    },
]


def seed_registry(conn) -> int:
    """Write the command registry into the database.

    The registry is data, not code, so that agents can read their own tool
    surface from the same place the validator reads it (Pillar 4).
    """
    with conn.cursor() as cur:
        cur.execute("DELETE FROM command_definition")
        for c in COMMANDS:
            cur.execute(
                "INSERT INTO command_definition "
                "(id, tenant_id, command_name, summary, argument_schema, "
                " preconditions, postconditions, invariants, reversibility, "
                " emits_event_types, minimum_band, supports_dry_run) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (str(uuid.uuid4()), TENANT_ID, c["command_name"], c["summary"],
                 json.dumps(c["argument_schema"]), json.dumps(c["preconditions"]),
                 json.dumps(c["postconditions"]), json.dumps(c["invariants"]),
                 c["reversibility"], c["emits_event_types"],
                 c["minimum_band"], c["supports_dry_run"]),
            )
    conn.commit()
    return len(COMMANDS)


def tool_surface_for(band: str) -> list[dict]:
    """The commands an agent at this authority band may even see."""
    order = ["observe", "suggest", "act_within_limits", "act_with_notice", "autonomous"]
    limit = order.index(band)
    return [c for c in COMMANDS if order.index(c["minimum_band"]) <= limit]