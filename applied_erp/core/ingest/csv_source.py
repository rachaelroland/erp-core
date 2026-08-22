# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Applied Industrials
"""CSV adapter — the vendor-neutral onboarding path.

Almost every ERP can export CSV, including ones with no usable API, which
makes this the fastest route from "we have a prospect" to "we have their
data". A new customer is a MAPPING FILE: which file holds what, which column
means what, and how their dates and numbers are formatted.

    uv run python -m erp.cli ingest --dir ./customer_export \\
                                    --mapping mappings/generic.json

The mapping is deliberately dumb — column names and formats only. Anything
that needs logic belongs in a subclass, so the difference between customers
stays visible instead of accumulating as branches in here.
"""

from __future__ import annotations

import csv
import json
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .schema import (PurchaseOrderLineRecord, PurchaseOrderRecord,
                     ReceiptRecord, SourceAdapter, SupplierRecord)

DEFAULT_MAPPING = {
    "files": {
        "suppliers": "suppliers.csv",
        "purchase_orders": "purchase_orders.csv",
        "receipts": "receipts.csv",
    },
    "date_formats": ["%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%Y-%m-%d %H:%M:%S",
                     "%m/%d/%Y %H:%M", "%d-%b-%Y"],
    "decimal_separator": ".",
    "thousands_separator": ",",
    "columns": {
        "suppliers": {
            "code": "supplier_code", "name": "supplier_name",
            "payment_terms": "terms", "lead_time_days": "lead_days",
        },
        # One row per ORDER LINE; the header fields repeat. This is how ERP
        # exports usually arrive, so it is the default rather than a variant.
        "purchase_orders": {
            "order_code": "po_number", "supplier_code": "supplier_code",
            "ordered_at": "order_date", "currency": "currency",
            "payment_terms": "terms", "line_number": "line_no",
            "item_code": "item_number", "item_description": "description",
            "quantity": "qty_ordered", "uom": "uom",
            "unit_price": "unit_price", "promise_date": "due_date",
        },
        "receipts": {
            "external_id": "receipt_id", "order_code": "po_number",
            "line_number": "line_no", "quantity": "qty_received",
            "uom": "uom", "received_at": "receipt_date", "lot_code": "lot",
        },
    },
}


class CsvSource(SourceAdapter):
    name = "csv"

    def __init__(self, directory: str | Path, mapping: dict | None = None):
        self.dir = Path(directory)
        self.map = mapping or DEFAULT_MAPPING
        self.problems: list[str] = []
        if not self.dir.is_dir():
            raise SystemExit(f"not a directory: {self.dir}")

    # -- parsing helpers ------------------------------------------------

    def _rows(self, kind: str) -> list[dict]:
        fname = self.map["files"].get(kind)
        if not fname:
            return []
        path = self.dir / fname
        if not path.exists():
            self.problems.append(f"missing file for {kind}: {path.name}")
            return []
        with path.open(newline="", encoding="utf-8-sig") as fh:
            return list(csv.DictReader(fh))

    def _col(self, kind: str, logical: str) -> str | None:
        return self.map["columns"].get(kind, {}).get(logical)

    def _get(self, row: dict, kind: str, logical: str) -> str | None:
        col = self._col(kind, logical)
        if col is None:
            return None
        value = row.get(col)
        return value.strip() if isinstance(value, str) else value

    def _decimal(self, raw, where: str) -> Decimal | None:
        if raw in (None, ""):
            return None
        text = str(raw)
        thou = self.map.get("thousands_separator", ",")
        dec = self.map.get("decimal_separator", ".")
        if thou:
            text = text.replace(thou, "")
        if dec != ".":
            text = text.replace(dec, ".")
        text = text.replace("$", "").replace("(", "-").replace(")", "").strip()
        try:
            return Decimal(text)
        except (InvalidOperation, ValueError):
            self.problems.append(f"{where}: cannot read number {raw!r}")
            return None

    def _date(self, raw, where: str) -> datetime | None:
        if raw in (None, ""):
            return None
        text = str(raw).strip()
        for fmt in self.map.get("date_formats", []):
            try:
                return datetime.strptime(text, fmt)
            except ValueError:
                continue
        self.problems.append(f"{where}: unrecognised date {raw!r}")
        return None

    # -- the three questions -------------------------------------------

    def suppliers(self) -> list[SupplierRecord]:
        out = []
        for row in self._rows("suppliers"):
            lead = self._get(row, "suppliers", "lead_time_days")
            out.append(SupplierRecord(
                code=self._get(row, "suppliers", "code") or "",
                name=self._get(row, "suppliers", "name") or "",
                payment_terms=self._get(row, "suppliers", "payment_terms"),
                lead_time_days=int(lead) if (lead or "").isdigit() else None,
            ))
        return out

    def purchase_orders(self) -> list[PurchaseOrderRecord]:
        orders: dict[str, PurchaseOrderRecord] = {}
        for i, row in enumerate(self._rows("purchase_orders"), start=2):
            code = self._get(row, "purchase_orders", "order_code") or ""
            where = f"purchase_orders.csv row {i} ({code})"
            order = orders.get(code)
            if order is None:
                order = PurchaseOrderRecord(
                    code=code,
                    supplier_code=self._get(row, "purchase_orders", "supplier_code") or "",
                    ordered_at=self._date(
                        self._get(row, "purchase_orders", "ordered_at"), where),
                    currency=self._get(row, "purchase_orders", "currency") or "USD",
                    payment_terms=self._get(row, "purchase_orders", "payment_terms"),
                )
                orders[code] = order

            line_no = self._get(row, "purchase_orders", "line_number")
            promise = self._date(
                self._get(row, "purchase_orders", "promise_date"), where)
            order.lines.append(PurchaseOrderLineRecord(
                order_code=code,
                line_number=int(line_no) if (line_no or "").lstrip("-").isdigit()
                            else len(order.lines) + 1,
                item_code=self._get(row, "purchase_orders", "item_code") or "",
                item_description=self._get(row, "purchase_orders", "item_description"),
                # No `or Decimal(0)`: an unparseable number must stay None
                # so validation can reject it rather than silently reading
                # a bad price as free.
                quantity=self._decimal(
                    self._get(row, "purchase_orders", "quantity"), where),
                uom=self._get(row, "purchase_orders", "uom") or "",
                unit_price=self._decimal(
                    self._get(row, "purchase_orders", "unit_price"), where),
                promise_date=promise.date() if promise else None,
            ))
        return list(orders.values())

    def receipts(self) -> list[ReceiptRecord]:
        out = []
        for i, row in enumerate(self._rows("receipts"), start=2):
            ext = self._get(row, "receipts", "external_id") or f"row{i}"
            where = f"receipts.csv row {i} ({ext})"
            received = self._date(self._get(row, "receipts", "received_at"), where)
            line_no = self._get(row, "receipts", "line_number")
            out.append(ReceiptRecord(
                external_id=ext,
                order_code=self._get(row, "receipts", "order_code") or "",
                line_number=int(line_no) if (line_no or "").lstrip("-").isdigit() else 0,
                quantity=self._decimal(
                    self._get(row, "receipts", "quantity"), where),
                uom=self._get(row, "receipts", "uom") or "",
                received_at=received,
                lot_code=self._get(row, "receipts", "lot_code"),
            ))
        return out


def load_mapping(path: str | Path | None) -> dict:
    if not path:
        return DEFAULT_MAPPING
    data = json.loads(Path(path).read_text())
    # Shallow-merge onto the default so a customer file only states its
    # differences, and a new logical column does not silently vanish.
    merged = json.loads(json.dumps(DEFAULT_MAPPING))
    for key, value in data.items():
        if isinstance(value, dict) and key in merged:
            for k2, v2 in value.items():
                if isinstance(v2, dict) and k2 in merged[key]:
                    merged[key][k2].update(v2)
                else:
                    merged[key][k2] = v2
        else:
            merged[key] = value
    return merged
