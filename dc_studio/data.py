# -*- coding: utf-8 -*-
"""Read-only access layer over the project artefacts.

Every loader degrades gracefully: a missing or malformed file never crashes the
UI, it only disables the widget that depends on it.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = ROOT / "out"
DOCS = ROOT / "docs"
RUNS = ROOT / "runs"


def _load_json(path: Path, default: Any = None) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return default


class Store:
    def __init__(self, root: Path = ROOT) -> None:
        self.root = root
        self.data_dir = root / "data"
        self.out_dir = root / "out"
        self.docs_dir = root / "docs"
        self.runs_dir = root / "runs"

        self.layout: dict = _load_json(self.data_dir / "layout.json", {}) or {}
        self.sim_index: dict = _load_json(self.out_dir / "sim_index.json", {}) or {}
        self.pnl_index: dict = _load_json(self.out_dir / "pnl_index.json", {}) or {}
        self.validate: list = _load_json(self.out_dir / "validate_report.json", []) or []
        self.artifact: dict = _load_json(self.out_dir / "artifact_report.json", {}) or {}
        self.build: dict = _load_json(self.out_dir / "build_report.json", {}) or {}
        self.traffic: dict = _load_json(self.out_dir / "traffic_report.json", {}) or {}
        self.tasks: dict = _load_json(self.data_dir / "tasks.json", {}) or {}
        self.scenarios_cfg: dict = _load_json(self.root / "config" / "scenarios" / "baseline.json", {}) or {}

        self._paths: dict[str, list[tuple[float, float, float, str]]] | None = None
        self._kpi: dict | None = None
        self._scenarios: list[dict] | None = None

    # ------------------------------------------------------------ layout
    @property
    def warehouses(self) -> list[dict]:
        return list(self.layout.get("warehouses", []))

    def warehouse(self, wid: str) -> dict | None:
        for wh in self.warehouses:
            if wh.get("id") == wid:
                return wh
        return None

    @property
    def counts(self) -> dict:
        return self.layout.get("summary", {}) or {}

    def global_bounds(self) -> tuple[float, float, float, float]:
        xs, ys = [], []
        for wh in self.warehouses:
            ox, oy = wh.get("origin", [0, 0])
            size = wh.get("size", {})
            xs += [ox, ox + size.get("width", 1)]
            ys += [oy, oy + size.get("depth", 1)]
        if not xs:
            return 0.0, 0.0, 100.0, 70.0
        return min(xs), min(ys), max(xs), max(ys)

    # ------------------------------------------------------------ scenarios
    @property
    def scenarios(self) -> list[dict]:
        if self._scenarios is not None:
            return self._scenarios
        pnl_map = {}
        for row in self.pnl_index.get("runs", []) if isinstance(self.pnl_index, dict) else []:
            pnl_map[row.get("run_id", "")] = row
        rows: list[dict] = []
        for run in self.sim_index.get("runs", []) if isinstance(self.sim_index, dict) else []:
            m = run.get("metrics", {}) or {}
            rid = run.get("run_id", "")
            pnl = pnl_map.get(rid, {})
            rows.append({
                "scenario": run.get("scenario", "?"),
                "run_id": rid,
                "run_dir": run.get("run_dir", ""),
                "seed": run.get("seed"),
                "orders": m.get("orders_completed", 0),
                "generated": m.get("orders_generated", 0),
                "throughput": m.get("throughput_orders_per_h", 0.0),
                "on_time": m.get("on_time_rate", 0.0),
                "tardiness": m.get("avg_tardiness_s", 0.0),
                "idle": m.get("idle_rate", 0.0),
                "distance": m.get("distance_total_m", 0.0),
                "energy": m.get("energy_kwh_total", 0.0),
                "conflicts": m.get("wait_conflicts", 0),
                "faults": m.get("fault_downtime_s", 0.0),
                "revenue": pnl.get("revenue", 0.0),
                "profit": pnl.get("operating_profit", 0.0),
                "breakeven": pnl.get("breakeven_orders", 0),
                "metrics": m,
            })
        order = ["baseline", "peak", "holiday", "night", "robot_failure", "dock_blocked", "layout_variant_f4", "scale_50"]
        rows.sort(key=lambda r: order.index(r["scenario"]) if r["scenario"] in order else 99)
        self._scenarios = rows
        return rows

    def scenario(self, name: str) -> dict | None:
        for row in self.scenarios:
            if row["scenario"] == name:
                return row
        return None

    # ------------------------------------------------------------ KPI
    @property
    def kpi_metrics(self) -> dict:
        """Metric definitions from the first run that ships a sim_kpi.json."""
        if self._kpi is not None:
            return self._kpi
        loaded: dict = {}
        runs = self.scenarios
        candidates = [r.get("run_dir") for r in runs] + [str(self.runs_dir / "baseline")]
        for run_dir in candidates:
            if not run_dir:
                continue
            p = Path(run_dir) / "sim_kpi.json"
            data = _load_json(p, None)
            if isinstance(data, dict) and data.get("metrics"):
                loaded = data["metrics"]
                break
        self._kpi = loaded
        return loaded

    def kpi_rows(self) -> list[dict]:
        out = []
        for name, item in self.kpi_metrics.items():
            if not isinstance(item, dict):
                continue
            out.append({
                "name": name,
                "value": item.get("value"),
                "unit": item.get("unit", ""),
                "definition": item.get("definition", ""),
                "formula": item.get("formula", ""),
                "source": item.get("source", ""),
            })
        return out

    # ------------------------------------------------------------ health
    def health(self) -> dict:
        items = [x for x in self.validate if isinstance(x, dict)]
        passed = sum(1 for x in items if str(x.get("status", "")).upper() == "PASS")
        failed = [x for x in items if str(x.get("status", "")).upper() not in ("PASS", "SKIP")]
        return {"total": len(items), "passed": passed, "failed": failed, "items": items}

    # ------------------------------------------------------------ paths
    def paths(self) -> dict[str, list[tuple[float, float, float, str]]]:
        if self._paths is not None:
            return self._paths
        series: dict[str, list[tuple[float, float, float, str]]] = {}
        p = self.data_dir / "paths.jsonl"
        try:
            with open(p, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    rec = json.loads(line)
                    series.setdefault(rec["agent_id"], []).append(
                        (float(rec["t"]), float(rec["x"]), float(rec["y"]), rec.get("phase", "MOVE"))
                    )
        except Exception:
            series = {}
        for key in series:
            series[key].sort(key=lambda r: r[0])
        self._paths = series
        return series

    @property
    def path_t_end(self) -> float:
        series = self.paths()
        return max((s[-1][0] for s in series.values() if s), default=0.0)

    # ------------------------------------------------------------ docs
    def doc_list(self) -> list[Path]:
        try:
            files = [p for p in self.docs_dir.glob("*.md") if p.is_file()]
        except Exception:
            return []
        priority = ["DC_MASTER_PLAN.md", "DC_EXPANSION_PLAN.md", "COMPLETION_STATUS.md",
                    "COMPLETION_PLAN.md", "PARAMETER_GUIDE.md"]
        files.sort(key=lambda p: (priority.index(p.name) if p.name in priority else 99, p.name))
        return files

    @staticmethod
    def read_text(path: Path) -> str:
        for enc in ("utf-8", "utf-8-sig", "gbk"):
            try:
                return path.read_text(encoding=enc)
            except Exception:
                continue
        return ""

    # ------------------------------------------------------------ gallery
    def gallery(self) -> list[tuple[Path, str]]:
        titles = {
            "01_overview.png": "整体鸟瞰",
            "02_interior.png": "仓内货架",
            "03_dock.png": "月台装卸",
            "04_agv_route.png": "AGV 路径",
            "05_slotting_heatmap.png": "ABC 热力",
            "06_corridor_check.png": "通道校核",
        }
        folder = self.out_dir / "renders"
        out = []
        for name, title in titles.items():
            p = folder / name
            if p.exists():
                out.append((p, title))
        return out

    # ------------------------------------------------------------ meta
    def freshness(self) -> str:
        stamps = []
        for p in (self.out_dir / "sim_index.json", self.data_dir / "layout.json"):
            try:
                stamps.append(datetime.fromtimestamp(p.stat().st_mtime))
            except Exception:
                pass
        if not stamps:
            return "未找到产物"
        latest = max(stamps)
        return latest.strftime("%Y-%m-%d %H:%M")

    def task_summary(self) -> dict:
        return self.tasks.get("summary", {}) if isinstance(self.tasks, dict) else {}

    def relative(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.root))
        except Exception:
            return str(path)


def open_path(path: Path | str) -> None:
    """Open a file or folder with the OS default handler."""
    target = str(path)
    try:
        if os.name == "nt":
            os.startfile(target)  # type: ignore[attr-defined]
        else:
            import subprocess
            subprocess.Popen(["open" if os.uname().sysname == "Darwin" else "xdg-open", target])
    except Exception:
        pass
