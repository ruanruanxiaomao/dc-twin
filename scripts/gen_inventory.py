#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scripts/gen_inventory.py —— P6a-0：库存层的数据与配置（**不碰引擎**）。

为什么不新造 SKU 主数据：`data/skus.json`（240 SKU，含 velocity/abc/cube/weight）与
`data/slotting.json`（392 库位分配，240 ASSIGNED + 152 EMPTY）已经存在。库存层要的是
"每库位归哪个 SKU、多少件"，这是既有数据的**派生**，不是第二套主数据——
"一份数据多个消费者"这条铁律在这里的体现就是：SKU 只有 skus.json 一个源头。

三份产出：
  data/sku_master.json        SKU 补 unit_cost / pack_size / storage（派生）
  data/inventory_slotting.json 每库位的 sku + role（reserve / pick_face），空库位补满
  data/inventory_initial.json  每 (sku, location) 期初结存

两条硬要求：
  1. **确定性**：固定 seed、全部按 id 排序、不读系统时钟、输出里没有时间戳。
     两次生成必须逐字节一致（`tests/unit/test_inventory.py` 会跑两次比字节）。
  2. **库位必须能解析到 SKU**：`slotting.json` 有 152 个 EMPTY 库位，若留空，
     P6a-1 里"订单按 pick_loc 反查 SKU"就会 raise。所以这里把空位按速度序
     分配给 A 类 SKU 作溢出储位，让"任何 SHELF 库位都有 SKU"成为不变量。

跑法：python scripts/gen_inventory.py [--quiet]
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG, DATA, OUT = ROOT / "config", ROOT / "data", ROOT / "out"

SEED = 20261106          # 与 data/config.json 同源，标明这些数不是随手编的
SCHEMA_VERSION = "1.0"


def jload(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def jdump(p, obj):
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    Path(p).write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def unit_cost(sku):
    """演示单价（placeholder）：按体积与重量估，不是真实采购价。

    只用于"库存占用资金"这个**展示口径**，不进 P&L（见 ADR-GAME-09 决策一）。
    """
    return round(1.5 + float(sku.get("cube_m3") or 0) * 35.0
                 + float(sku.get("weight_kg") or 0) * 0.25, 2)


def pack_size(abc):
    return {"A": 12, "B": 6, "C": 1}.get(abc, 1)


def lot_split(qty, n_lots):
    """把 qty 拆成 n_lots 份，余数给前几份。**整数除法 + 固定顺序**，保证确定性。"""
    base, rem = divmod(int(qty), int(n_lots))
    return [base + (1 if i < rem else 0) for i in range(n_lots)]


def life_fraction(ranges, lot_i, mix):
    """取第 lot_i 个批次的"剩余寿命占比"。

    用 `mix`（由 SKU/库位/批次序号的整数算术得到）在区间内取点，**不用 rng、
    不用 hash()**：`hash()` 对字符串有进程级随机化，跨进程/跨平台会漂，
    而这份数据必须两次生成逐字节一致（测试会跑两次比字节）。
    """
    lo, hi = ranges[min(lot_i, len(ranges) - 1)]
    p = (mix % 1000) / 1000.0
    return lo + (hi - lo) * p


def build():
    layout = jload(DATA / "layout.json")
    skus = jload(DATA / "skus.json")
    slotting = jload(DATA / "slotting.json")
    spec = jload(CONFIG / "inventory_spec.json")
    # 刻意不读 data/areas.json：拣选面锚点用 layout 里的 OUTBOUND 月台即可，
    # 而 areas.json 由 spec_areas.py 生成、不在洁净室数据链里。读一个用不到的
    # 上游产物，会让"全新空目录复现"在第 5 步就挂（第一版就是这么挂的）。

    base_qty = spec["stock_policy"]["base_qty"]
    overflow_mult = float(spec["stock_policy"]["overflow_multiplier"])
    max_pf = int(spec["pick_face"].get("max_per_warehouse", 8))
    pf_ratio = float(spec["pick_face"].get("fill_ratio_of_reserve", 0.3))

    # ---- 库位基础信息（坐标 / 所属仓）——用于挑拣选面与算距离
    loc_wh, loc_xy = {}, {}
    for w in layout["warehouses"]:
        for loc in w.get("locations", []):
            loc_wh[loc["id"]] = w["id"]
            loc_xy[loc["id"]] = tuple(loc.get("access_point") or (0.0, 0.0))

    # ---- SKU 字典（源 = skus.json，绝不在此新增 SKU）
    sku_by_id = {s["sku"]: s for s in skus["skus"]}
    order_skus = sorted(sku_by_id)          # 排序是确定性的前提

    assigned = [r for r in slotting["assignments"] if r.get("status") == "ASSIGNED"]
    empties = [r for r in slotting["assignments"] if r.get("status") != "ASSIGNED"]
    assigned.sort(key=lambda r: r["location_id"])
    empties.sort(key=lambda r: r["location_id"])

    rows = {}
    sku_wh = {}                              # SKU → 已分配库位所在仓
    for r in assigned:
        rows[r["location_id"]] = {"sku": r["sku"], "role": "reserve", "source": "assigned"}
        sku_wh[r["sku"]] = r["warehouse_id"]

    # ---- 空库位 → 同仓 A 类 SKU 的溢出储位（按速度降序轮转，保证确定性）
    # **必须同仓**：空库位只在 W。若把 W 的空位派给一个"主储位在 F1"的 SKU，
    # 就会出现"储备位 F1、拣选面 W"的跨仓配对，内部补货单要跨仓搬运 —— P6a 明确
    # 不做多仓调拨，这种配对在引擎里会直接抛错（第一版就是这么被抓到的）。
    a_class_all = sorted((s for s in order_skus if sku_by_id[s].get("abc") == "A"),
                         key=lambda s: (-float(sku_by_id[s].get("velocity") or 0), s))
    if not a_class_all:                      # 兜底：没有 A 类就用全量
        a_class_all = list(order_skus)
    cursor = {}
    for r in empties:
        wid = r["warehouse_id"]
        pool = [s for s in a_class_all if sku_wh.get(s) == wid]
        if not pool:
            pool = [s for s in order_skus if sku_wh.get(s) == wid] or a_class_all
        k = cursor.get(wid, 0)
        rows[r["location_id"]] = {"sku": pool[k % len(pool)], "role": "reserve",
                                  "source": "overflow"}
        cursor[wid] = k + 1

    # ---- 拣选面：每仓取"离该仓 OUTBOUND 月台最近"的溢出位，数量受 max_per_warehouse 限制
    outbound_anchor = {}
    for w in layout["warehouses"]:
        cands = [loc for loc in w.get("locations", []) if loc.get("kind") == "OUTBOUND"]
        if cands:
            outbound_anchor[w["id"]] = tuple(cands[0].get("access_point") or (0.0, 0.0))
    pf_by_wh = {}
    for wid in sorted({r["warehouse_id"] for r in empties}):
        pool = [r["location_id"] for r in empties if r["warehouse_id"] == wid]
        anchor = outbound_anchor.get(wid)
        if anchor is None:
            continue
        pool.sort(key=lambda lid: (dist(loc_xy[lid], anchor), lid))
        chosen = pool[:max_pf]
        for lid in chosen:
            rows[lid]["role"] = "pick_face"
        pf_by_wh[wid] = sorted(chosen)

    # ---- 按 SKU 汇总储位
    by_sku = {}
    for lid in sorted(rows):
        info = rows[lid]
        if info["sku"] not in by_sku:
            by_sku[info["sku"]] = {"reserve": [], "pick_face": []}
        by_sku[info["sku"]][info["role"]].append(lid)
    # 拣选面数量只由 `pick_face.max_per_warehouse` 决定。**不做"多储位就自动提为
    # 拣选面"的第二轮提升**：那会让 config 里的上限失效（实测会从 32 涨到 96），
    # 配置与行为对不上，比没有配置更糟。
    for sku in sorted(by_sku):
        v = by_sku[sku]
        v["reserve"].sort()
        v["pick_face"].sort()

    # ---- 期初结存 + 期初批次（P6b）
    exp = spec.get("expiry", {})
    exp_enabled = bool(exp.get("enabled", False))
    shelf_by_class = {k: float(v) for k, v in (exp.get("shelf_life_s_by_class") or {}).items()}
    life_ranges = exp.get("initial_lot_life_fraction") or [[1.0, 1.0]]
    aging_n = int(exp.get("aging_every_n_locations", 1) or 1)
    max_lots = int(exp.get("max_lots_per_location", 1) or 1)
    horizon_s = float(jload(CONFIG / "sim_spec.json").get("horizon_s", 3600))

    stock, lots, total_units, total_value = {}, {}, 0, 0.0
    lot_seq = 0
    expiring_units, expiring_lots, aging_locs = 0, 0, 0
    loc_order = sorted(rows)                     # 库位全局序号：确定性取模用
    loc_idx = {lid: i for i, lid in enumerate(loc_order)}
    sku_idx = {s: i for i, s in enumerate(order_skus)}

    for sku in sorted(by_sku):
        abc = sku_by_id[sku].get("abc", "C")
        base = int(base_qty.get(abc, 8))
        cost = unit_cost(sku_by_id[sku])
        per_loc = {}
        for lid in by_sku[sku]["reserve"]:
            q = base if rows[lid]["source"] == "assigned" else max(1, int(base * overflow_mult))
            per_loc[lid] = q
        for lid in by_sku[sku]["pick_face"]:
            per_loc[lid] = max(1, int(base * pf_ratio))
        stock[sku] = per_loc
        for q in per_loc.values():
            total_units += q
            total_value += q * cost

        shelf = shelf_by_class.get(abc, 0.0)
        lots[sku] = {}
        for lid in sorted(per_loc):
            q = per_loc[lid]
            if not exp_enabled or shelf <= 0:
                continue
            # 批量拆批次：A 类 48 件 → 3 批，B 类 24 → 2 批，C 类 8 → 1 批。
            # 批次数按量而不是"每库位固定"，是为了让"先进先出"在快消品上真有意义。
            n = max(1, min(max_lots, 1 + q // 24))
            # 只让每 N 个库位有一批临期货：整仓同时到期不是效期管理，是一次性事故，
            # 而且会把 baseline 的库存整体打掉，掩盖真正想观测的周转行为。
            aging = (loc_idx[lid] % aging_n == 0)
            if aging:
                aging_locs += 1
            ranges = life_ranges if aging else [[0.80, 1.0]]
            # **日期码倒挂**：一半的临期库位里，"最后到货的那批"日期码反而最短。
            #
            # 这个不是编出来的巧合，而是 FEFO 存在的理由：供应商后送来的货常常
            # 是更早生产的批次（清库存、拼车、调拨）。如果所有库位都让"后到 = 晚到期"，
            # FIFO 与 FEFO 会给出**完全相同**的结果——实测确实如此（expired 1657 对 1657），
            # 那样"批次策略"就是个假开关：玩家切了也不会变。
            reverse_life = aging and (loc_idx[lid] % (2 * aging_n) < aging_n)
            qtys = lot_split(q, n)
            out = []
            for i, lq in enumerate(qtys):
                if lq <= 0:
                    continue
                lot_seq += 1
                mix = (sku_idx[sku] * 131 + loc_idx[lid] * 17 + i * 7) % 1000
                li = (n - 1 - i) if reverse_life else i
                frac = life_fraction(ranges, li, mix)
                expiry = round(frac * shelf, 3)
                # t_in 递减：i=0 是最早入库的批次。同效期时 FIFO 仍有确定次序。
                t_in = round(-60.0 * (n - 1 - i), 3)
                out.append({"lot_id": "LOT-I%06d" % lot_seq, "qty": lq,
                            "t_in": t_in, "expiry_t": expiry,
                            "remaining_frac": round(frac, 4),
                            "date_code_inverted": bool(reverse_life)})
                if expiry <= horizon_s:
                    expiring_units += lq
                    expiring_lots += 1
            if out:
                lots[sku][lid] = out

    # ---- 产出 1：SKU 主数据（派生，不新增 SKU）
    master = {
        "schema_version": SCHEMA_VERSION,
        "kind": "sku_master",
        "generated_from": "data/skus.json + config/inventory_spec.json",
        "seed": SEED,
        "note": "unit_cost / pack_size 为演示 placeholder；只用于库存占用资金展示，不进 P&L。"
                "shelf_life_s 同理：按仿真秒计的演示效期，不是真实保质期",
        "count": len(order_skus),
        "skus": [{
            "id": s,
            "name": sku_by_id[s].get("name", s),
            "abc": sku_by_id[s].get("abc", "C"),
            "velocity": sku_by_id[s].get("velocity", 0.0),
            "cube_m3": sku_by_id[s].get("cube_m3", 0.0),
            "weight_kg": sku_by_id[s].get("weight_kg", 0.0),
            "unit_cost": unit_cost(sku_by_id[s]),
            "pack_size": pack_size(sku_by_id[s].get("abc", "C")),
            "storage": "SHELF",
            "shelf_life_s": shelf_by_class.get(sku_by_id[s].get("abc", "C"), 0.0),
        } for s in order_skus],
    }

    # ---- 产出 2：库位 → SKU + 角色（**每个 SHELF 库位都必须有 SKU**）
    slot_rows = []
    for lid in sorted(rows):
        info = rows[lid]
        slot_rows.append({
            "location_id": lid,
            "warehouse_id": loc_wh[lid],
            "sku": info["sku"],
            "role": info["role"],
            "source": info["source"],
        })
    inventory_slotting = {
        "schema_version": SCHEMA_VERSION,
        "kind": "inventory_slotting",
        "generated_from": "data/slotting.json + data/layout.json + config/inventory_spec.json",
        "notes": [
            "每个 SHELF 库位恰好一个 SKU：P6a-1 用 pick_loc 反查 SKU，查不到就 raise。",
            "role=pick_face 的库位由空库位按'离 OUTBOUND 月台最近'选出，作为前置小批量。",
            "空库位全部补给 A 类 SKU 作溢出储位：不新增 SKU，只让库位不再无主。",
        ],
        "counts": {
            "locations": len(slot_rows),
            "skus": len(by_sku),
            "pick_face": sum(1 for r in slot_rows if r["role"] == "pick_face"),
            "reserve": sum(1 for r in slot_rows if r["role"] == "reserve"),
            "from_overflow": sum(1 for r in slot_rows if r["source"] == "overflow"),
        },
        "pick_face_by_warehouse": {k: v for k, v in sorted(pf_by_wh.items())},
        "by_sku": {s: by_sku[s] for s in sorted(by_sku)},
        "locations": slot_rows,
    }

    # ---- 产出 3：期初结存 + 期初批次
    initial = {
        "schema_version": SCHEMA_VERSION,
        "kind": "inventory_initial",
        "generated_from": "data/inventory_slotting.json + config/inventory_spec.json",
        "seed": SEED,
        "note": "期初结存按 ABC 基准量；拣选面按 fill_ratio_of_reserve 取小批量。"
                "lots 是同一批货的批次拆分：qty 之和必须等于 stock（引擎会断言，对不上即 raise）",
        "summary": {"total_units": total_units, "inventory_value": round(total_value, 2),
                    "skus": len(stock),
                    "lots": sum(len(lst) for per_loc in lots.values()
                                for lst in per_loc.values()),
                    "expiring_lots": expiring_lots,
                    "expiring_units": expiring_units,
                    "aging_locations": aging_locs,
                    "inverted_locations": sum(1 for per in lots.values()
                                              for arr in per.values()
                                              if any(l.get("date_code_inverted") for l in arr)),
                    "expiry_enabled": exp_enabled,
                    "horizon_s": horizon_s},
        "stock": {s: stock[s] for s in sorted(stock)},
        "lots": {s: {l: lots[s][l] for l in sorted(lots[s])} for s in sorted(lots)},
    }
    return master, inventory_slotting, initial


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="生成库存层数据（P6a-0，不碰引擎）")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--out-dir", default=None, help="输出到别处（测试确定性用）")
    a = ap.parse_args(argv)

    master, slotting, initial = build()
    outs = {
        "sku_master.json": master,
        "inventory_slotting.json": slotting,
        "inventory_initial.json": initial,
    }
    target = Path(a.out_dir) if a.out_dir else DATA
    for name, obj in outs.items():
        jdump(target / name, obj)

    # schema 自检：配置本身必须过 schema（数据文件没有 schema，靠下面的不变量）
    import jsonschema
    jsonschema.validate(jload(CONFIG / "inventory_spec.json"),
                        jload(CONFIG / "schemas" / "inventory_spec.schema.json"))

    if not a.quiet:
        c = slotting["counts"]
        print("INVENTORY_OK skus=%d locations=%d pick_face=%d overflow=%d units=%d value=%.2f"
              % (master["count"], c["locations"], c["pick_face"], c["from_overflow"],
                 initial["summary"]["total_units"], initial["summary"]["inventory_value"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
