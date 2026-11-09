"""Stage C3 - WMS adapter framework (COMPLETION_PLAN 5.4).

Self-designed interface; the decomposition idea (transport order / adapter /
resource boundary) is a concept reference to openTCS only, with no code derived
from it. Read-only by default: ack_order never writes unless dry_run=False is
passed explicitly. No credentials are stored in config/integrations.json.

Not validated against a real OpenBoxes / Odoo instance (degradation G3);
contract tests use a local mock HTTP server instead.
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod

ORDER_FIELDS = ("order_id", "order_type", "warehouse", "sku", "qty", "priority")
ORDER_TYPES = ("INBOUND", "OUTBOUND", "PICK", "REPLENISH", "TRANSFER")


def validate_order(o):
    issues = []
    if not isinstance(o, dict):
        return ["order is not an object"]
    for k in ORDER_FIELDS:
        if k not in o:
            issues.append("missing %s" % k)
    if o.get("order_type") not in ORDER_TYPES:
        issues.append("unknown order_type: %r" % o.get("order_type"))
    if not isinstance(o.get("qty"), (int, float)) or o.get("qty", 0) <= 0:
        issues.append("qty must be positive")
    if not isinstance(o.get("order_id"), str) or not o["order_id"]:
        issues.append("order_id must be a non-empty string")
    return issues


def dedupe(orders):
    """Idempotency key = order_id; repeated pulls must not create duplicates."""
    seen, unique, dups = set(), [], []
    for o in orders:
        key = o.get("order_id")
        if key in seen:
            dups.append(key)
        else:
            seen.add(key)
            unique.append(o)
    return unique, dups


def retry(fn, times=3, base_delay_s=0.05):
    """Exponential backoff x3. Raises the last exception on exhaustion."""
    last = None
    for i in range(times):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            last = exc
            if i < times - 1:
                time.sleep(base_delay_s * (2 ** i))
    raise last


class WmsAdapter(ABC):
    name = "abstract"
    read_only = True

    @abstractmethod
    def fetch_orders(self, since_s=0.0):
        """Return list[dict]; caller validates with validate_order()."""

    def ack_order(self, order_id, status, dry_run=True):
        """Default dry-run: never writes to a production system."""
        return {"ok": True, "dry_run": True, "order_id": order_id,
                "status": status, "written": False}

    def sync_locations(self):
        return []

    def pull(self, since_s=0.0):
        """Pull + dedupe + validate; invalid rows are reported, not dropped silently."""
        raw = retry(lambda: self.fetch_orders(since_s))
        unique, dups = dedupe(raw)
        valid, invalid = [], []
        for o in unique:
            issues = validate_order(o)
            (invalid if issues else valid).append({"order": o, "issues": issues} if issues else o)
        return {"adapter": self.name, "orders": valid, "duplicates": dups,
                "invalid": invalid, "count": len(valid)}


class StaticAdapter(WmsAdapter):
    """Contract-test adapter over an in-memory list."""
    name = "static"

    def __init__(self, orders):
        self.orders = list(orders)

    def fetch_orders(self, since_s=0.0):
        return [dict(o) for o in self.orders if o.get("t_arrive", 0) >= since_s]

    def sync_locations(self):
        return [{"id": "LOC-1", "kind": "SHELF"}]
