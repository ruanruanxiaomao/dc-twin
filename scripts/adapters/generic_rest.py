"""Stage C3 - generic REST WMS adapter (pure standard library).

Auth secrets are read from environment variables named in config/integrations.json;
they are never stored in config, logs, or reports. GET is the default; POST for
ack requires an explicit dry_run=False and is off by default (G3: no production
writes). Contract tests run against a local mock HTTP server.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request

try:
    from .base import WmsAdapter  # package import
except ImportError:  # script-dir / flat import
    from base import WmsAdapter  # type: ignore


class GenericRestAdapter(WmsAdapter):
    name = "generic_rest"

    def __init__(self, cfg, environ=None):
        env = environ if environ is not None else os.environ
        endpoint = str(cfg.get("endpoint", ""))
        if not endpoint.startswith(("http://", "https://")):
            raise ValueError("endpoint must be http(s), got %r" % endpoint)
        if ".." in endpoint or endpoint.startswith("file:"):
            raise ValueError("unsafe endpoint")
        self.endpoint = endpoint.rstrip("/")
        self.orders_path = cfg.get("orders_path", "/api/orders")
        self.locations_path = cfg.get("locations_path", "/api/locations")
        self.field_map = cfg.get("field_map", {})
        self.timeout_s = float(cfg.get("timeout_s", 5))
        self.dry_run = bool(cfg.get("dry_run", True))
        auth = cfg.get("auth", {}) or {}
        self.auth_type = auth.get("type", "none")
        token_env = auth.get("token_env")
        self.token = env.get(token_env) if token_env else None
        self.user = env.get(auth.get("user_env", "")) if auth.get("user_env") else None
        self.read_only = self.dry_run

    def _headers(self):
        h = {"Accept": "application/json"}
        if self.auth_type == "bearer" and self.token:
            h["Authorization"] = "Bearer " + self.token
        elif self.auth_type == "basic" and self.token:
            import base64
            raw = (self.user or "") + ":" + self.token
            h["Authorization"] = "Basic " + base64.b64encode(raw.encode()).decode()
        return h

    def _get(self, path, params=None):
        url = self.endpoint + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers=self._headers(), method="GET")
        with urllib.request.urlopen(req, timeout=self.timeout_s) as r:
            return json.loads(r.read().decode("utf-8"))

    @staticmethod
    def _rows(payload):
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            for k in ("orders", "data", "results", "items"):
                if isinstance(payload.get(k), list):
                    return payload[k]
        return []

    def fetch_orders(self, since_s=0.0):
        payload = self._get(self.orders_path, {"since": since_s})
        out = []
        for row in self._rows(payload):
            mapped = {}
            for target, source in (self.field_map or {}).items():
                if source in row:
                    mapped[target] = row[source]
            mapped.setdefault("order_type", "OUTBOUND")
            mapped.setdefault("qty", 1)
            mapped.setdefault("priority", "P2")
            mapped.setdefault("warehouse", "W")
            mapped.setdefault("sku", "UNKNOWN")
            out.append(mapped)
        return out

    def ack_order(self, order_id, status, dry_run=None):
        write = not (self.dry_run if dry_run is None else dry_run)
        if not write:
            return {"ok": True, "dry_run": True, "order_id": order_id,
                    "status": status, "written": False}
        body = json.dumps({"order_id": order_id, "status": status}).encode("utf-8")
        req = urllib.request.Request(self.endpoint + self.orders_path + "/ack",
                                     data=body, headers=dict(self._headers(),
                                     **{"Content-Type": "application/json"}),
                                     method="POST")
        with urllib.request.urlopen(req, timeout=self.timeout_s) as r:
            return {"ok": True, "dry_run": False, "written": True,
                    "response": json.loads(r.read().decode("utf-8"))}

    def sync_locations(self):
        try:
            return self._rows(self._get(self.locations_path))
        except (urllib.error.URLError, ValueError):
            return []
