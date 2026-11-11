"""月台占用与排队：直接对调度/状态机做断言，不靠"跑一遍看起来像"。

模型（v4.7）：
  · **门是时间窗资源**。一次装卸占用 150 s（出库）/180 s（入库），期间这扇门
    不能再接一单；占用从派单时起算（含预计行驶时间），到装卸完成结束。
  · **AGV 只在门口做短交接**（`dwell_pick_s`）。之前的做法是让车在门口干等
    整个装卸时长，而月台走廊只有一条车道——实测 peak 从 59 单掉到 35 单，
    相当于把"没建门"换成了"建了一堵墙"。
  · **没门就排队等派单**，不先开到门口堵着：队列是"等门的订单"，
    指标 `dock_queue_max` / `dock_queue_wait_avg_s` 统计的就是它。

基线（45 单/h、38 扇门）下门远未饱和，队列为 0——所以"会排队"这件事必须由
单元测试和 `scripts/check_dock.py` 的容量对照来证明，而不是靠看 KPI。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import sim_engine as se  # noqa: E402


def jload(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def make_sim(tmp_path):
    """按 main() 的方式装载，但只构造不运行（秒级）。"""
    tmp_path.mkdir(parents=True, exist_ok=True)
    return se.Simulation(
        ROOT / "config" / "scenarios" / "baseline.json",
        jload(ROOT / "config" / "sim_spec.json"),
        jload(ROOT / "config" / "fleet_spec.json"),
        jload(ROOT / "config" / "demand_spec.json"),
        jload(ROOT / "data" / "layout.json"),
        jload(ROOT / "data" / "nav_grid.json"),
        jload(ROOT / "data" / "config.json"),
        out_dir=tmp_path,
    )


def dock_ids(sim):
    return [k for k, v in sim.stations.items() if v.get("kind") == "DOCK"]


def an_order(n=1, dock="W-OU-02", shelf="W-SHELF-001"):
    return {"order_id": "T-%02d" % n, "type": "OUTBOUND", "warehouse": "W",
            "pick_loc": shelf, "drop_loc": dock,
            "pick_xy": (0.0, 0.0), "drop_xy": (0.0, 0.0),
            "t_arrive": 0.0, "priority": "P2", "deadline": 1e6,
            "reassignments": 0}


def order_waiting(sim, oid):
    return any(o["order_id"] == oid for o in sim.queue)


# ------------------------------------------------------------------ 静态结构
def test_docks_are_loaded_as_door_resources(tmp_path):
    sim = make_sim(tmp_path)
    ids = dock_ids(sim)
    assert len(ids) == 10, ids                              # W 4 区 + F1/F2/F3 各 2 区
    assert sum(sim.stations[i]["slots"] for i in ids) == 38  # 与实际门数一致

    w_out, w_in = sim.stations["W-OU-02"], sim.stations["W-IN-01"]
    assert w_out["slots"] == 5 and w_in["slots"] == 5
    assert len(w_out["door_free_at"]) == 5                  # 每扇门一个时间窗
    # 装卸工时来自 process_spec 的 DOCK_OUT / DOCK_IN 段，不另抄一份
    assert w_in["mean"] == 180.0 and w_out["mean"] == 150.0


def test_slot_overrides_are_isolated_and_resize_doors(tmp_path):
    """工位对照实验不该顺手改月台；改门数时时间窗数组必须一起改。"""
    saved = se.Simulation.station_slots_override, se.Simulation.dock_slots_override
    try:
        se.Simulation.station_slots_override, se.Simulation.dock_slots_override = None, None
        base = make_sim(tmp_path / "base")

        se.Simulation.station_slots_override, se.Simulation.dock_slots_override = 1, None
        sim = make_sim(tmp_path / "a")
        assert all(v["slots"] == 1 for v in sim.stations.values()
                   if v.get("kind") != "DOCK")
        assert sim.stations["W-OU-02"]["slots"] == 5

        se.Simulation.station_slots_override, se.Simulation.dock_slots_override = None, 2
        sim = make_sim(tmp_path / "b")
        assert sim.stations["W-OU-02"]["slots"] == 2
        assert len(sim.stations["W-OU-02"]["door_free_at"]) == 2
        assert (sim.stations["W-PACK-01"]["slots"]
                == base.stations["W-PACK-01"]["slots"])
    finally:
        se.Simulation.station_slots_override, se.Simulation.dock_slots_override = saved


def test_dock_slot_finds_the_first_free_door(tmp_path):
    sim = make_sim(tmp_path)
    st = sim.stations["W-OU-02"]
    assert sim._dock_slot(st, 0.0) == 0
    st["door_free_at"] = [50.0, 10.0, 0.0, 0.0, 0.0]
    assert sim._dock_slot(st, 5.0) == 2        # 前两扇还占着，第三扇空
    assert sim._dock_slot(st, 15.0) == 1       # 第二扇 10 s 后空出，先占它
    st["door_free_at"] = [50.0] * 5
    assert sim._dock_slot(st, 20.0) is None    # 全忙


# ------------------------------------------------------------------ 派单占门
def test_dispatch_reserves_a_door_and_blocks_the_second_order(tmp_path):
    """一扇门、两单：第一单占门并记账，第二单留在派单队列里等。"""
    sim = make_sim(tmp_path)
    st = sim.stations["W-OU-02"]
    st["slots"] = 1
    st["door_free_at"] = [0.0]

    sim.queue = [an_order(1), an_order(2)]
    sim._assign(0.0)

    assert st["served"] == 1, "只该有一单占到门"
    assert st["door_free_at"][0] > 0.0
    assert st["busy_s"] > 100.0                       # 记的是 150 s 量级的装卸时长
    assert order_waiting(sim, "T-02"), "没门的那单必须留在队列里等"
    assert "T-02" in st["queue"] and st["qmax"] >= 1
    assert sim.log.counts.get("DOCK_RESERVED") == 1


def test_waiting_order_counts_its_wait_when_a_door_frees(tmp_path):
    sim = make_sim(tmp_path)
    st = sim.stations["W-OU-02"]
    st["slots"] = 1
    st["door_free_at"] = [0.0]
    sim.queue = [an_order(1), an_order(2)]

    sim._assign(0.0)
    assert order_waiting(sim, "T-02")

    # 门在 200 s 后空出来，第二单这一轮才被派出去
    st["door_free_at"] = [200.0]
    sim._assign(260.0)
    assert not order_waiting(sim, "T-02")
    assert st["wait_n"] == 1 and st["wait_sum"] >= 200.0
    assert st["served"] == 2


# ------------------------------------------------------------------ 门口交接
def test_agv_handoff_at_the_door_is_short_not_the_whole_operation(tmp_path):
    sim = make_sim(tmp_path)
    st = sim.stations["W-OU-02"]
    r = sim.robots[sorted(sim.robots)[0]]
    r.order = an_order(1)
    r.state = "IDLE"
    r.dwell_until = 0.0

    sim._enter_station(r, 100.0, "DROPPING")

    assert r.state == "DROPPING"
    assert r.station is None, "门是时间窗资源，不在到达时占用服务台"
    assert r.dwell_until - 100.0 == sim.dwell, "交接应是短暂停，不是 150 s"
    assert st["busy"] == 0 and st["queue"] == []
    assert sim.log.counts.get("DOCK_HANDOFF") == 1


def test_arrival_at_a_closed_dock_is_refused_and_the_order_goes_back(tmp_path):
    sim = make_sim(tmp_path)
    st = sim.stations["W-OU-02"]
    st["closed"] = True
    r = sim.robots[sorted(sim.robots)[0]]
    r.order = an_order(1)
    r.state = "IDLE"
    before = len(sim.queue)

    sim._enter_station(r, 50.0, "DROPPING")

    assert r.order is None and r.state == "IDLE"
    assert len(sim.queue) == before + 1
    assert sim.queue[-1]["reassignments"] == 1
    assert sim.log.counts.get("DOCK_REFUSED") == 1


def test_closed_state_follows_the_block_and_reopen_events(tmp_path):
    """封锁事件必须真的关/开这门，而不是只往 blocked_docks 里记一笔。"""
    sim = make_sim(tmp_path)
    sim.faults = [
        {"type": "dock_blocked", "dock": "W-OU-02", "start": 0.0, "end": 600.0,
         "was_active": False},
    ]

    sim._apply_faults(10.0)
    assert sim.stations["W-OU-02"]["closed"] is True
    assert "W-OU-02" in sim.blocked_docks

    sim._apply_faults(700.0)
    assert sim.stations["W-OU-02"]["closed"] is False
    assert "W-OU-02" not in sim.blocked_docks
