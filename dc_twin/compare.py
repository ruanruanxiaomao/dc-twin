"""compare.py - diff two scenario runs (KPI deltas + assertions + config)."""
from __future__ import annotations

import csv
import json
from pathlib import Path

from . import paths


def _resolve(s: str) -> Path:
    p = Path(s)
    return p if p.is_absolute() else paths.ROOT / p


def load(run: str) -> dict:
    p = _resolve(run)
    if not p.is_dir():
        raise SystemExit("DC-E5001 run directory not found: %s" % p)
    kpi = json.loads((p / "kpi.json").read_text(encoding="utf-8")) if (p / "kpi.json").exists() else {}
    sc = json.loads((p / "scenario.json").read_text(encoding="utf-8")) if (p / "scenario.json").exists() else {}
    asr = json.loads((p / "assertions.json").read_text(encoding="utf-8")) if (p / "assertions.json").exists() else {}
    return {"path": p, "id": sc.get("id", p.parent.name), "seed": sc.get("seed"),
            "metrics": kpi.get("metrics", {}), "assertions": asr, "scenario": sc}


def compare(run_a: str, run_b: str, fmt: str = "md", out: str | None = None) -> int:
    a, b = load(run_a), load(run_b)
    keys = sorted(set(a["metrics"]) | set(b["metrics"]))
    rows = []
    for k in keys:
        ma, mb = a["metrics"].get(k, {}), b["metrics"].get(k, {})
        va, vb = ma.get("value"), mb.get("value")
        delta = None
        if isinstance(va, (int, float)) and isinstance(vb, (int, float)):
            delta = round(vb - va, 4)
        rows.append({"metric": k, "unit": ma.get("unit") or mb.get("unit") or "",
                     "a": va, "b": vb, "delta": delta,
                     "source": ma.get("source") or mb.get("source") or ""})
    payload = {"schema_version": "1.0", "kind": "run_comparison",
               "run_a": str(a["path"]), "run_b": str(b["path"]),
               "id_a": a["id"], "id_b": b["id"], "seed_a": a["seed"], "seed_b": b["seed"],
               "assertions": {"a": a["assertions"].get("verdict"),
                              "b": b["assertions"].get("verdict")},
               "deltas": rows}
    if fmt == "json":
        text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    elif fmt == "csv":
        import io
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=["metric", "unit", "a", "b", "delta", "source"])
        w.writeheader()
        w.writerows(rows)
        text = buf.getvalue()
    else:
        lines = ["# Run comparison", "", "A = `%s` (%s, seed %s) · B = `%s` (%s, seed %s)" %
                 (a["id"], a["path"].name, a["seed"], b["id"], b["path"].name, b["seed"]),
                 "", "Assertion verdicts: A=%s / B=%s" %
                 (payload["assertions"]["a"], payload["assertions"]["b"]), "",
                 "| Metric | Unit | A | B | Δ (B−A) |", "|---|---|---:|---:|---:|"]
        for r in rows:
            lines.append("| %s | %s | %s | %s | %s |" %
                         (r["metric"], r["unit"], r["a"], r["b"], r["delta"]))
        lines += ["", "KPI provenance is per metric (`source`); analytic what-if values "
                  "are not full per-vehicle A* results.", ""]
        text = "\n".join(lines)
    if out:
        Path(out).write_text(text, encoding="utf-8")
        print("COMPARE_OK output=%s metrics=%d" % (out, len(rows)))
    else:
        print(text)
    return 0
