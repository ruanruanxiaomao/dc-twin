#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R1 · 集装单元层（G3）的测试。

四条主线：

1. **数据**：每个 SKU 都有单元、都不超载、SSCC 位数对；生成器三次一致。
2. **默认关闭必须字节不变**：关闭态事件流与 v3 金标准**逐字节**一致。
   这条是 R1 的全部价值——接进来但不影响既有产物。
3. **开启后有事件**：UNITLOAD_HANDOFF / ID_FIRST_READ / ID_RESCAN 真的出现，
   且观测首读率落在配置门槛附近。
4. **超载真的拦**：B4 不是"字段存着好看"，拒收要留事件、库存不许动。
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import sim_engine as se       # noqa: E402
import step_baseline as sb    # noqa: E402


def jload(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def make_sim(tmp_path, ul_enabled=None):
    """ul_enabled=None 走配置默认（当前 false）；True/False 走覆写钩子。"""
    spec = jload(ROOT / "config" / "unit_load_spec.json")
    if ul_enabled is not None:
        spec = dict(spec, enabled=ul_enabled)
    se.Simulation.unit_load_override = (spec if ul_enabled is not None else None)
    try:
        sim = se.Simulation(
            ROOT / "config" / "scenarios" / "baseline.json",
            jload(ROOT / "config" / "sim_spec.json"),
            jload(ROOT / "config" / "fleet_spec.json"),
            jload(ROOT / "config" / "demand_spec.json"),
            jload(ROOT / "data" / "layout.json"),
            jload(ROOT / "data" / "nav_grid.json"),
            jload(ROOT / "data" / "config.json"),
            out_dir=tmp_path / "run",
        )
    finally:
        se.Simulation.unit_load_override = None
    return sim


def events_of(sim):
    try:
        sim.log.f.flush()
    except Exception:                     # noqa: BLE001
        pass
    return [json.loads(x) for x in (sim.out / "sim_events.jsonl")
            .read_text(encoding="utf-8").splitlines() if x.strip()]


# ---------------------------------------------------------------- 1) 数据

def test_every_sku_has_a_unit_load():
    m = jload(ROOT / "data" / "sku_master.json")
    assert m.get("unit_load_counts"), "sku_master 没有 unit_load 摘要"
    for s in m["skus"]:
        u = s.get("unit_load")
        assert u, "%s 没有 unit_load" % s["id"]
        assert u["kind"] in ("pallet", "tote")
        assert u["units_per_load"] >= 1
        assert u["gross_weight_kg"] > 0
        assert len(u["sscc"]) == 18
        assert u["id_method"] in ("barcode", "rfid")
        # 上架先验货架载：这一条不许有例外
        assert u["gross_weight_kg"] <= u["rating_kg"] + 1e-9, "%s 超载" % s["id"]
        assert u["overload"] is False


def test_cartons_tote_and_pallets_are_both_present():
    """如果全是托盘或全是料箱，说明分档逻辑没生效（A/B 托盘、C 料箱）。"""
    m = jload(ROOT / "data" / "sku_master.json")
    kinds = {s["unit_load"]["kind"] for s in m["skus"]}
    assert kinds == {"pallet", "tote"}


def test_unit_load_generation_is_deterministic(tmp_path):
    import subprocess
    a, b = tmp_path / "a", tmp_path / "b"
    for d in (a, b):
        subprocess.run([sys.executable, "scripts/gen_inventory.py", "--quiet",
                        "--out-dir", str(d)], cwd=ROOT, check=True,
                       capture_output=True)
    for name in ("sku_master.json", "inventory_initial.json", "inventory_slotting.json"):
        assert (a / name).read_bytes() == (b / name).read_bytes(), "%s 两次生成不一致" % name
        assert (a / name).read_bytes() == (ROOT / "data" / name).read_bytes(), \
            "%s 与仓库里的文件不一致（忘了重新生成？）" % name


# ---------------------------------------------------------------- 2) 默认关闭 = 字节不变

def test_disabled_is_byte_identical_to_v3(tmp_path):
    golden = ROOT / "tests" / "golden" / "step_baseline_v3.json"
    if not golden.exists():
        pytest.skip("没有 v3 基准")
    sim = make_sim(tmp_path, ul_enabled=False)
    assert sim.ul_enabled is False
    assert not sim.ul_by_sku
    sim.run()
    import hashlib
    want = jload(golden)["scenarios"]["baseline"]["sim_events.jsonl"]["sha256"]
    raw = (sim.out / "sim_events.jsonl").read_text(encoding="utf-8")
    got = hashlib.sha256(sb.normalise(raw).encode("utf-8")).hexdigest()
    assert got == want, "关闭集装单元层后事件流与 v3 不一致：开关没兜住"


def test_default_config_keeps_the_layer_off():
    spec = jload(ROOT / "config" / "unit_load_spec.json")
    assert spec["enabled"] is False, "默认必须关闭，否则金标准立刻破"


# ---------------------------------------------------------------- 3) 开启后有事件

def test_enabled_emits_handoff_and_identification(tmp_path):
    sim = make_sim(tmp_path, ul_enabled=True)
    assert sim.ul_enabled and sim.ul_by_sku
    sim.run()
    evs = events_of(sim)
    kinds = {e["type"] for e in evs}
    assert "UNITLOAD_HANDOFF" in kinds, "开启后没有任何整托交接事件"
    assert "ID_FIRST_READ" in kinds, "开启后没有任何首读事件"

    handoffs = [e for e in evs if e["type"] == "UNITLOAD_HANDOFF"]
    assert all(e["loads"] >= 1 for e in handoffs)
    assert all(e["units_per_load"] >= 1 for e in handoffs)
    assert all(e["gross_kg"] > 0 for e in handoffs)

    reads = sim.ul_first_read_n + sim.ul_rescan_n
    assert reads > 0
    # 观测首读率应当接近配置门槛（0.995），允许小样本抖动
    assert sim.ul_first_read_n / reads >= 0.90, (
        "首读率 %.4f 明显低于门槛，识别逻辑可能写反了"
        % (sim.ul_first_read_n / reads))
    assert sim.ul_overload_n == 0, "合规数据不该出现超载拒收"


def test_enabled_is_deterministic(tmp_path):
    a = make_sim(tmp_path / "a", ul_enabled=True)
    b = make_sim(tmp_path / "b", ul_enabled=True)
    a.run()
    b.run()

    def digest(sim):
        evs = [e for e in events_of(sim)
               if e["type"].startswith(("UNITLOAD_", "ID_"))]
        return json.dumps(evs, sort_keys=True)

    assert digest(a) == digest(b), "同种子跑出两份不同的集装单元事件流"


# ---------------------------------------------------------------- 4) 超载拦截

def test_overload_is_rejected_with_an_event(tmp_path):
    sim = make_sim(tmp_path, ul_enabled=True)
    sku, loc = None, None
    for s, per in sim.inv.items():
        if per:
            sku, loc = s, sorted(per)[0]
            break
    assert sku and loc
    before = sim.inv[sku][loc]
    # 人为把这一托标成超载（真实数据是合规的，反例必须自己造）
    sim.ul_by_sku[sku] = dict(sim.ul_by_sku[sku], overload=True,
                              gross_weight_kg=1500.0, rating_kg=1000.0)
    wid = sim.inv_loc[loc]["warehouse"]
    docks = [d["id"] for d in sim.world.by_kind.get(wid, {}).get("INBOUND", [])]
    order = {"order_id": "T-UL-OVER", "type": "INBOUND", "warehouse": wid,
             "pick_loc": sorted(docks)[0], "drop_loc": loc, "qty": 3.0,
             "deadline": float("inf"), "reassignments": 0}

    delivered, ok = sim._inv_settle(order)
    assert (delivered, ok) == (0.0, False), "超载单元被放行了"
    assert sim.inv[sku][loc] == pytest.approx(before), "拒收却动了库存"
    assert sim.ul_overload_n == 1
    ev = [e for e in events_of(sim) if e["type"] == "UNITLOAD_OVERLOAD_REJECTED"]
    assert ev and ev[-1]["limit_kg"] == 1000.0 and ev[-1]["gross_kg"] == 1500.0


def test_overload_check_is_off_when_layer_disabled(tmp_path):
    """关闭时连超载都不判——这不是漏洞，是"默认关闭"的定义。"""
    sim = make_sim(tmp_path, ul_enabled=False)
    assert sim.ul_by_sku == {}
    assert sim.ul_enabled is False
