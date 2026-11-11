"""故障改派：确定性验证"打断在途任务 → 订单退回 → 另一台车接手"。

`validate_sim.py` 的 S4 原来硬要求 `reassignments > 0`。接上月台门之后派单变保守
（没门先等），故障时刻该仓可能一台车都没在执行任务，于是三条故障砸中 0 条是正常的
观测结果——把"必须改派"写成断言会变成靠运气。所以那部分改成"确实打断了在途任务
才要求改派"，而改派机制本身在这里用构造好的状态确定性地验证。
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


def busy_robot(sim, n=1, rid=None):
    """把一台车放进"正在取货途中"的状态，模拟任务被打断。"""
    r = sim.robots[rid or sorted(sim.robots)[0]]
    r.order = {"order_id": "F-%02d" % n, "type": "OUTBOUND", "warehouse": r.wid,
               "pick_loc": "W-SHELF-001", "drop_loc": "W-OU-02",
               "pick_xy": (0.0, 0.0), "drop_xy": (0.0, 0.0),
               "t_arrive": 0.0, "priority": "P2", "deadline": 1e6,
               "reassignments": 0}
    r.state = "MOVING_PICK"
    return r


def test_fault_on_a_busy_robot_returns_the_order_and_reassigns(tmp_path):
    sim = make_sim(tmp_path)
    r = busy_robot(sim)
    sim.faults = [{"type": "robot_down", "agent": r.id,
                   "start": 100.0, "end": 700.0, "was_active": False}]

    sim._apply_faults(100.0)

    assert r.state == "FAULT" and r.order is None, "故障车不能继续持有任务"
    assert any(o["order_id"] == "F-01" for o in sim.queue), "订单要退回派单队列"
    assert sim.queue[-1]["reassignments"] == 1
    assert sim.log.counts.get("TASK_REASSIGNED") == 1
    assert sim.log.counts.get("FAULT_START") == 1


def test_fault_on_an_idle_robot_does_not_invent_a_reassignment(tmp_path):
    """没在干活的车坏了，就不该凭空产生改派事件（这正是 S4 放宽的依据）。"""
    sim = make_sim(tmp_path)
    r = sim.robots[sorted(sim.robots)[0]]
    r.order = None
    r.state = "IDLE"
    sim.faults = [{"type": "robot_down", "agent": r.id,
                   "start": 100.0, "end": 700.0, "was_active": False}]

    sim._apply_faults(100.0)

    assert r.state == "FAULT"
    assert sim.log.counts.get("TASK_REASSIGNED") is None
    assert sim.queue == []


def test_preempt_busy_policy_moves_the_fault_onto_a_working_robot(tmp_path):
    """场景声明 preempt_busy 时，故障要落到同仓正在执行任务的车身上。"""
    sim = make_sim(tmp_path)
    sim.sc["fault_target_policy"] = "preempt_busy"
    ids = sorted(sim.robots)
    idle = sim.robots[ids[0]]
    idle.order = None
    idle.state = "IDLE"
    busy = busy_robot(sim, 2, rid=ids[1])
    sim.faults = [{"type": "robot_down", "agent": idle.id,
                   "start": 200.0, "end": 800.0, "was_active": False}]

    sim._apply_faults(200.0)

    assert busy.state == "FAULT", "故障应改到在途车辆上"
    assert idle.state == "IDLE", "原本空闲的目标车不该被牵连"
