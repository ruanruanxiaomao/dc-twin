# -*- coding: utf-8 -*-
"""D10a / D12：功能区重摆的「草稿 → 校验 → 显式提交」。

## 为什么单独一个模块

摆弄功能区的位置是**交互**，写进磁盘是**事务**。这两件事混在一起，就会变成
"拖一下、存一下"——用户拖动过程中每一个中间状态都会落盘，仓库配置被一次误拖毁掉。
所以这里把三段切开，每段都能独立被门调用：

1. `plan()`   纯计算：给一组勾选 + 一组位移，算出最终矩形（**不碰磁盘**）；
2. `validate()` 把草稿写成 spec，跑 `spec_layout` 的 L2/L3/L4/L6 全套（**写临时文件**）；
3. `commit()` 只有拿着**校验时发的那张令牌**、且重算令牌一致、且几何 0 issue，才落盘。

令牌是防"校验的是 A、提交的是 B"：拖完再拖一格，令牌就变了，旧的校验结果作废。

## 与 `build_variant.py` 的关系

排布算法（`pack`）不在这里重写——直接复用它的，否则两边迟早不一致。
这一层只负责"用户的位移覆盖算法给出的位置"和"事务边界"。
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config"
OUT = ROOT / "out"
sys.path.insert(0, str(ROOT / "scripts"))

import build_variant as bv  # noqa: E402  (pack / load_catalog 的唯一定义处)

#: 草稿里单个功能区的位移上限（m）。超过这个数基本是手滑或坐标系搞错了，
#: 与其让用户拖出 1000 m 再报一堆 L2，不如在入口就拒绝并说明。
MAX_SHIFT_M = 60.0


class CommitRejected(RuntimeError):
    """提交被拒。`code` 给前端做分支，`detail` 给用户看原因。"""

    def __init__(self, code, detail, issues=None):
        super().__init__("%s: %s" % (code, detail))
        self.code = code
        self.detail = detail
        self.issues = issues or []


def _jdump(p, obj):
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    Path(p).write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def plan(enabled, moves=None, target_depth=None, base_spec=None):
    """算出功能区矩形。`moves` 是 ``{template_id: [dx, dz]}`` 的**位移**。

    用位移而不是绝对坐标，是因为前端拖拽天然产出位移；绝对坐标还得知道原位置，
    而原位置随勾选集合变化，容易错位。位移是幂等的：同一个 moves 反复应用结果相同。
    """
    catalog, _raw = bv.load_catalog()
    base = json.loads(Path(base_spec or bv.BASE_SPEC).read_text(encoding="utf-8"))
    wh = next((w for w in base["warehouses"] if w["id"] == "W"), None)
    if wh is None:
        raise CommitRejected("NO_WAREHOUSE", "基础 spec 里没有 W 仓")
    place = bv.jload(bv.SELECTION).get("placement") or {} if Path(bv.SELECTION).exists() else {}
    x_margin = float(place.get("x_margin_m", 4.0))
    gap = float(place.get("gap_m", 2.0))
    offset = float(place.get("band_start_offset_m", 2.0))
    band_z0 = float(wh["size"]["depth"]) + offset

    picked = [catalog[k] for k in enabled if k in catalog]
    items = sorted(picked, key=lambda t: -t["priority"])
    placed, depth_used = bv.pack(items, (x_margin, band_z0, wh["size"]["width"] - x_margin),
                                 x_margin, gap)
    moves = moves or {}
    unknown = [k for k in moves if k not in {p["template"] for p in placed}]
    if unknown:
        raise CommitRejected("UNKNOWN_MOVE", "位移指向未勾选的模板：%s" % ",".join(unknown))
    for p in placed:
        dx, dz = (moves.get(p["template"]) or [0.0, 0.0])
        dx, dz = float(dx), float(dz)
        if abs(dx) > MAX_SHIFT_M or abs(dz) > MAX_SHIFT_M:
            raise CommitRejected("MOVE_TOO_FAR",
                                 "%s 的位移 (%.1f, %.1f) 超过 %.0f m，拒绝"
                                 % (p["template"], dx, dz, MAX_SHIFT_M))
        if dx or dz:
            r = p["rect"]
            p["rect"] = [round(r[0] + dx, 3), round(r[1] + dz, 3),
                         round(r[2] + dx, 3), round(r[3] + dz, 3)]
    return {"warehouse": "W", "base_depth": float(wh["size"]["depth"]),
            "band_z0": band_z0, "placed": placed, "depth_used": depth_used,
            "moves": {k: [float(v[0]), float(v[1])] for k, v in moves.items()}}


def token(plan_doc):
    """校验令牌：勾选集合 + 位移 + 每个矩形的最终位置，全部进摘要。

    只对"用户意图"取摘要（moves），不对结果取——否则算法改一点点，所有旧令牌
    都失效，用户刚校验完就被告知过期，体验更糟且没有额外安全性。
    """
    payload = {"moves": plan_doc.get("moves") or {},
               "rects": {p["template"]: p["rect"] for p in plan_doc["placed"]}}
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def to_spec(plan_doc, base_spec=None):
    """把草稿写进一份完整 spec（W 的 areas 被替换，depth 覆盖到最后一个区的北沿）。"""
    base = json.loads(Path(base_spec or bv.BASE_SPEC).read_text(encoding="utf-8"))
    wh = next(w for w in base["warehouses"] if w["id"] == "W")
    far = max((p["rect"][3] for p in plan_doc["placed"]), default=0.0)
    wh["size"]["depth"] = round(max(plan_doc["base_depth"], far + 2.0), 2)
    wh["areas"] = []
    for p in plan_doc["placed"]:
        wh["areas"].append({
            "id": "W-%s" % p["template"], "kind": p["kind"], "name": p["name"],
            "group": p.get("group", ""), "rect": p["rect"],
            "capacity": p["capacity"], "process": p["process"],
            "constraints": p["constraints"],
            "access_point": [round((p["rect"][0] + p["rect"][2]) / 2, 2),
                             round(p["rect"][1] - 1.5, 2)],
        })
    return base


def check(spec, tag="draft"):
    """跑两个校验器：`spec_layout`（L2–L6 布局）+ `spec_areas`（L23–L26 功能区）。

    **为什么必须两个**：`spec_layout` 的 L2/L3 只把 racks/docks/stations 放进 rects，
    **不含 areas**——所以"把收货区拖进货架带"在那边是 0 issue。我第一版只跑了一个，
    越界草稿直接通过、还写了盘；门当场抓到（`invalid_geometry.ok = true` 但它明明该红）。
    这正是"校验器覆盖范围不等于语义"的经典坑：校验没报，不代表东西没错。
    """
    tmp = Path(tempfile.mkdtemp(prefix="assembly_"))
    sp = tmp / ("%s_spec.json" % tag)
    _jdump(sp, spec)
    out = tmp / ("%s_layout.json" % tag)
    rep = tmp / ("%s_report.json" % tag)
    p1 = subprocess.run([sys.executable, str(ROOT / "scripts" / "spec_layout.py"),
                         "--spec", str(sp), "--out", str(out), "--report", str(rep),
                         "--quiet"],
                        cwd=str(ROOT), capture_output=True, encoding="utf-8", errors="replace")
    issues = []
    if rep.exists():
        issues = list(json.loads(rep.read_text(encoding="utf-8")).get("issues") or [])
    if p1.returncode != 0 and not issues:
        raise CommitRejected("VALIDATOR_ERROR",
                             "布局校验器异常退出（%s）：%s"
                             % (p1.returncode, ((p1.stdout or "") + (p1.stderr or ""))[-300:]))
    # 功能区 L23–L26：不跑它，areas 的越界完全没有防线
    arep = tmp / ("%s_areas.json" % tag)
    p2 = subprocess.run([sys.executable, str(ROOT / "scripts" / "spec_areas.py"),
                         "--spec", str(sp), "--layout", str(out),
                         "--nav", str(tmp / "no_such_nav.json"),
                         "--out", str(tmp / "areas_out.json"),
                         "--report", str(arep), "--quiet"],
                        cwd=str(ROOT), capture_output=True, encoding="utf-8", errors="replace")
    if arep.exists():
        for i in (json.loads(arep.read_text(encoding="utf-8")).get("issues") or []):
            if isinstance(i, dict):
                issues.append({"rule": i.get("rule"),
                               "message": "%s %s" % (i.get("area") or i.get("id") or "?",
                                                     i.get("message") or i.get("detail") or "")})
    if p2.returncode != 0 and not arep.exists():
        raise CommitRejected("VALIDATOR_ERROR",
                             "功能区校验器异常退出（%s）：%s"
                             % (p2.returncode, ((p2.stdout or "") + (p2.stderr or ""))[-300:]))
    return issues, {"returncode": p1.returncode, "areas_rc": p2.returncode,
                    "tmp": str(tmp)}


def validate(enabled, moves=None, base_spec=None):
    """草稿 → 校验结果 + 令牌。前端"校验"按钮就是调它。"""
    doc = plan(enabled, moves, base_spec=base_spec)
    spec = to_spec(doc, base_spec=base_spec)
    issues, meta = check(spec)
    return {"ok": not issues, "token": token(doc), "issues": issues,
            "plan": doc, "returncode": meta["returncode"],
            "summary": {"count": len(doc["placed"]),
                        "depth": spec["warehouses"][0]["size"]["depth"]}}


def commit(enabled, moves, tok, base_spec=None, out_spec=None, allow_write=True):
    """D12 显式提交。

    四道闸，任何一道不过都不落盘：

    1. **必须带令牌**——没校验过就想存，是"拖完直接落盘"的别名；
    2. **令牌必须重算一致**——校验的是 A、提交的是 B 就拒绝（拖完再拖一格也算）；
    3. **几何必须 0 issue**——L2/L3/L4/L6 一条都不许留；
    4. 通过后才写 `out_spec`（默认 `config/layout_spec_assembly.json`）。
    """
    if not tok:
        raise CommitRejected("NEED_VALIDATE", "没有校验令牌：先点「校验」再看结果")
    doc = plan(enabled, moves, base_spec=base_spec)
    if token(doc) != tok:
        raise CommitRejected("STALE_PLAN", "草稿在校验之后又变了，请重新校验")
    spec = to_spec(doc, base_spec=base_spec)
    issues, meta = check(spec)
    if issues:
        raise CommitRejected("GEOMETRY_INVALID",
                             "布局校验未通过（%d 条 issue），拒绝写盘" % len(issues),
                             issues=issues)
    target = Path(out_spec or (CONFIG / "layout_spec_assembly.json"))
    if allow_write:
        _jdump(target, spec)
    return {"ok": True, "spec": str(target.relative_to(ROOT)) if target.is_relative_to(ROOT)
            else str(target), "token": tok, "issues": 0,
            "locations_plan": {"count": len(doc["placed"]),
                               "depth": spec["warehouses"][0]["size"]["depth"]}}


def main(argv=None):
    """命令行只做一件事：把当前 selection 跑一遍校验，打印结果（不写盘）。

    写盘只发生在 commit，而 commit 只能由 API/交互触发——命令行不给"绕过校验"的入口。
    """
    import argparse
    ap = argparse.ArgumentParser(description="功能区草稿校验（只读，不落盘）")
    ap.add_argument("--enabled", default="", help="逗号分隔的模板 id，缺省用 selection")
    ap.add_argument("--moves", default="{}", help='JSON：{"RCV-01":[dx,dz]}')
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)
    if a.enabled:
        enabled = [x for x in a.enabled.split(",") if x]
    else:
        enabled = (bv.jload(bv.SELECTION).get("enabled") or []) if Path(bv.SELECTION).exists() else []
    try:
        r = validate(enabled, json.loads(a.moves))
    except CommitRejected as e:
        print("ASSEMBLY_REJECT %s %s" % (e.code, e.detail))
        return 2
    if a.quiet:
        print("ASSEMBLY_%s issues=%d token=%s"
              % ("OK" if r["ok"] else "FAIL", len(r["issues"]), r["token"]))
    else:
        print("ASSEMBLY_%s count=%d depth=%.1f token=%s"
              % ("OK" if r["ok"] else "FAIL", r["summary"]["count"],
                 r["summary"]["depth"], r["token"]))
        for i in r["issues"][:8]:
            print("  %s %s" % (i.get("rule"), i.get("message")))
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
