# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Applied Industrials
"""The canonical shapes a source system must produce.

THIS IS THE ANTI-CORRUPTION BOUNDARY (ADR-014). Everything upstream of here
speaks the customer's ERP dialect; everything downstream speaks ours. No
vendor field name, table name, or code value is permitted past this module —
that rule is what keeps the clean-room design intact once real integrations
start, and it is the thing that erodes first if nobody is watching.

A new customer should be a MAPPING FILE, not a code change. If onboarding
requires editing Python, the abstraction has failed and the fix belongs here
rather than in a per-customer fork.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal


@dataclass
class SupplierRecord:
    code: str                       # the customer's vendor id, used as our `code`
    name: str
    payment_terms: str | None = None
    lead_time_days: int | None = None
    attributes: dict = field(default_factory=dict)

    def issues(self) -> list[str]:
        p = []
        if not self.code:
            p.append("supplier code is empty")
        if not self.name:
            p.append(f"supplier {self.code} has no name")
        return p


@dataclass
class PurchaseOrderLineRecord:
    order_code: str
    line_number: int
    item_code: str
    item_description: str | None
    quantity: Decimal
    uom: str
    unit_price: Decimal
    promise_date: date | None = None
    attributes: dict = field(default_factory=dict)

    def issues(self) -> list[str]:
        # None means the source value could not be PARSED. It must stay
        # distinguishable from a real zero — coercing an unreadable price to
        # 0.00 would put a zero-value order in front of the matcher and every
        # invoice against it would look like a massive overcharge.
        p = []
        if self.quantity is None:
            p.append(f"{self.order_code} line {self.line_number}: unreadable quantity")
        elif self.quantity <= 0:
            p.append(f"{self.order_code} line {self.line_number}: quantity must be > 0")
        if not self.uom:
            p.append(f"{self.order_code} line {self.line_number}: missing UOM")
        if self.unit_price is None:
            p.append(f"{self.order_code} line {self.line_number}: unreadable unit price")
        elif self.unit_price < 0:
            p.append(f"{self.order_code} line {self.line_number}: negative unit price")
        return p


@dataclass
class PurchaseOrderRecord:
    code: str
    supplier_code: str
    ordered_at: datetime | None
    currency: str = "USD"
    payment_terms: str | None = None
    lines: list[PurchaseOrderLineRecord] = field(default_factory=list)

    def issues(self) -> list[str]:
        p = []
        if not self.code:
            p.append("order code is empty")
        if not self.supplier_code:
            p.append(f"order {self.code} has no supplier")
        if not self.lines:
            p.append(f"order {self.code} has no lines")
        # Required downstream (commitment.created_at is NOT NULL). Catching it
        # here turns a mid-write database error into a clean rejection —
        # without this the ingest crashed partway through with rows already
        # inserted, which is the exact half-loaded state this class exists
        # to prevent.
        if self.ordered_at is None:
            p.append(f"order {self.code}: missing or unreadable order date")
        for line in self.lines:
            p.extend(line.issues())
        return p


@dataclass
class ReceiptRecord:
    """A physical receipt against a purchase order line.

    `external_id` is the customer's own receipt/packing-slip identifier. We
    keep it so a matched invoice can be traced back into THEIR system — the
    pilot is read-only, and an answer they cannot verify in their own ERP is
    worth nothing to them.
    """
    external_id: str
    order_code: str
    line_number: int
    quantity: Decimal
    uom: str
    received_at: datetime
    lot_code: str | None = None
    attributes: dict = field(default_factory=dict)

    def issues(self) -> list[str]:
        p = []
        if self.quantity is None or self.quantity <= 0:
            p.append(f"receipt {self.external_id}: quantity must be > 0")
        if not self.received_at:
            p.append(f"receipt {self.external_id}: missing received date")
        return p


@dataclass
class IngestReport:
    """What came in, and what was wrong with it.

    Every ingest returns one. Silent partial loads are how an integration
    reports 94% accuracy on 60% of the data.
    """
    suppliers: int = 0
    orders: int = 0
    order_lines: int = 0
    receipts: int = 0
    rejected: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    unmatched_receipts: list[str] = field(default_factory=list)

    def summary(self) -> str:
        out = [f"suppliers={self.suppliers} orders={self.orders} "
               f"lines={self.order_lines} receipts={self.receipts}"]
        if self.rejected:
            out.append(f"REJECTED {len(self.rejected)} records:")
            out += [f"    - {r}" for r in self.rejected[:15]]
            if len(self.rejected) > 15:
                out.append(f"    … and {len(self.rejected) - 15} more")
        if self.unmatched_receipts:
            out.append(f"WARNING {len(self.unmatched_receipts)} receipts "
                       f"reference an order line that was not supplied:")
            out += [f"    - {r}" for r in self.unmatched_receipts[:10]]
        if self.warnings:
            out.append(f"{len(self.warnings)} warnings:")
            out += [f"    - {w}" for w in self.warnings[:10]]
        return "\n".join(out)


class SourceAdapter:
    """What every source system must implement.

    Three methods. If a customer's ERP cannot answer these three questions,
    it cannot be onboarded — and knowing that on day one is worth more than
    discovering it in week six.
    """

    name: str = "abstract"

    def suppliers(self) -> list[SupplierRecord]:
        raise NotImplementedError

    def purchase_orders(self) -> list[PurchaseOrderRecord]:
        raise NotImplementedError

    def receipts(self) -> list[ReceiptRecord]:
        raise NotImplementedError
