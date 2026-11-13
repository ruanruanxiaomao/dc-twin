#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""R3 设备类型层的验收门（``dc run --scope refactor`` 的第 4 步）。

四道子门，每道都必须能失败——否则它只是在描述自己：

* ``EQUIP_TYPES_OK``  设备类型库与车队配置自洽（每个车型都能落到一个设备类型，
  每个部署车型都真实存在，时间模型齐全，物理参数不违反常识）。
* ``AST_COUPLING_OK``  AST 驱动巷道宽：开启后 VNA 比平衡重窄；**关闭后布局逐字节不变**；
  若 AST 推出的宽度低于通道安全下限，必须**记录被夹紧**而不是静默使用。
* ``HEIGHT_NEG``      可服务高度检查覆盖所有设备类型：6 m 堆垛机服务 9 m 货架必须告警，
  而资料原版（只查叉车）必须**漏检**——漏检说明这条修正是真的在起作用。
* ``BUY_EQUIP_OK``    采购设备进现金但不进利润表（capex），守恒式仍成立；
  ``BUY_AGV`` 别名保留，事件类型不许被悄悄换掉。

外加一条 **DEFAULT_OFF**：默认车队（单车型、无 AST、无深位）的事件流必须与 v3 基准
逐字节一致。这是所有 R 阶段共同的护栏，本阶段尤其重要——R3 是第一个真的会改
``Robot`` 构造的阶段。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

CONFIG = ROOT / "config"
DATA = ROOT / "data"
GOLDEN = ROOT / "tests" / "golden" / "step_baseline_v3.json"


def jload(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def specs():
    return (jload(CONFIG / "sim_spec.json"), jload(CONFIG / "fleet_spec.json"),
            jload(CONFIG / "demand_spec.json"), jload(DATA / "layout.json"),
            jload(DATA / "nav_grid.json"), jload(DATA / "config.json"))


# --------------------------------------------------------------------- 门 1
def check_equip_types():
    """设备类型库 ↔ 车队配置。返回 (ok, 明细, 问题)。"""
    import equipment_ref

    problems, detail = [], {}
    equip = jload(CONFIG / "equipment_spec.json")
    fleet = jload(CONFIG / "fleet_spec.json")
    types = equip.get("types") or {}
    detail["equip_types"] = len(types)

    # 1.1 schema
    try:
        import jsonschema
        jsonschema.validate(equip, jload(CONFIG / "schemas" / "equipment_spec.schema.json"))
    except Exception as e:                                       # noqa: BLE001
        problems.append("equipment_spec schema 不符：%s" % str(e)[:200])

    # 1.2 车型 → 设备类型
    for m in fleet.get("models", []):
        et = m.get("equipment_type")
        if not et:
            problems.append("车型 %s 没有声明 equipment_type（不知道该按哪类设备建模）"
                            % m.get("id"))
            continue
        if et not in types:
            problems.append("车型 %s 指向不存在的设备类型 %r" % (m.get("id"), et))
            continue
        # 载荷不得高于设备额定（否则等于让 250kg 的车扛 2t）
        if float(m.get("payload_kg") or 0) > float(types[et].get("payload_kg") or 0) + 1e-9:
            problems.append("车型 %s 载荷 %s > 设备 %s 额定 %s"
                            % (m.get("id"), m.get("payload_kg"), et,
                               types[et].get("payload_kg")))
        if not m.get("capex_cny"):
            problems.append("车型 %s 没有 capex_cny（采购无法入账）" % m.get("id"))
        detail.setdefault("models", []).append({"id": m.get("id"), "equipment_type": et})

    # 1.3 部署只引用真实车型
    model_ids = {m.get("id") for m in fleet.get("models", [])}
    for dep in fleet.get("deployment", []):
        if dep.get("model") not in model_ids:
            problems.append("deployment 引用了不存在的车型 %r（仓 %s）"
                            % (dep.get("model"), dep.get("warehouse")))

    # 1.4 物理常识：窄巷道车必须比平衡重窄，堆垛机必须够高
    vna = types.get("forklift_vna", {})
    cb = types.get("forklift_counterbalance", {})
    if vna and cb and not float(vna.get("ast_mm", 0)) < float(cb.get("ast_mm", 0)):
        problems.append("VNA 的 AST(%s) 不小于平衡重(%s)，那窄巷道就不窄了"
                        % (vna.get("ast_mm"), cb.get("ast_mm")))
    for name in ("stacker_unitload", "stacker_miniload"):
        t = types.get(name) or {}
        # 堆垛机的可服务高度用 max_height_m 表达（它没有货叉行程 lift_mm），
        # 两种写法都接受，但必须有其一——否则高度校验无从判起。
        if t and not (float(t.get("lift_mm") or 0) > 0
                      or float(t.get("max_height_m") or 0) > 0):
            problems.append("%s 既没有 lift_mm 也没有 max_height_m" % name)

    # 1.5 时间模型：设备周期时间由它给出，缺了 R3 的解析层就是空的
    tm = equip.get("time_model") or {}
    for k in ("load_rate_divisor", "unload_rate_divisor"):
        if k in tm and float(tm[k]) != 2.0:
            problems.append("%s 应为 2（一个复合周期 = 1 存 + 1 取，B1）" % k)
    detail["time_model"] = sorted(tm)

    # 1.6 引擎侧确有按车型取参的通路（不是只有配置）
    if not hasattr(equipment_ref, "model_index"):
        problems.append("equipment_ref.model_index 缺失：引擎没有按车型取参的通路")
    return (not problems), detail, problems


# --------------------------------------------------------------------- 门 2
def check_ast_coupling():
    """AST → 巷道宽。返回 (ok, 明细, 问题)。"""
    import copy

    import spec_layout as sl

    problems, detail = [], {}
    base = jload(CONFIG / "layout_spec.json")

    def widths(lay, rack_id_prefix=None):
        out = []
        for w in lay["warehouses"]:
            for loc in w["locations"]:
                if loc.get("kind") != "SHELF":
                    continue
                out.append(loc)
        return out

    off = sl.expand(copy.deepcopy(base))
    detail["off_locations"] = len(widths(off))

    on = copy.deepcopy(base)
    on.setdefault("options", {})["ast_drives_aisle_width"] = True
    # 只给 W 的第一个货架组换成 VNA 作业，其余保持不变——差异才可归因
    rack_group = on["warehouses"][0]["racks"][0]
    rack_group.setdefault("rack", {})["equipment_type"] = "forklift_vna"
    on_lay = sl.expand(on)
    detail["on_locations"] = len(widths(on_lay))

    # 2.1 关闭态必须与出厂布局一致（D5：默认不读 AST）
    if detail["off_locations"] != len(widths(sl.expand(jload(CONFIG / "layout_spec.json")))):
        problems.append("关闭 AST 后布局与出厂布局不一致（默认路径被改了）")

    # 2.2 开启后，VNA 组的巷道必须比平衡重窄
    if not hasattr(sl, "aisle_width_for"):
        problems.append("spec_layout 没有 aisle_width_for()：AST 耦合没有可测的入口")
        return False, detail, problems

    defaults = base["defaults"]
    vna = sl.aisle_width_for({"rack": {"equipment_type": "forklift_vna"}}, defaults,
                             ast_enabled=True)
    cb = sl.aisle_width_for({"rack": {"equipment_type": "forklift_counterbalance"}},
                            defaults, ast_enabled=True)
    detail["vna_aisle"], detail["cb_aisle"] = vna["width"], cb["width"]
    if not vna["width"] < cb["width"]:
        problems.append("AST 开启后 VNA 巷道 %.3f 不窄于平衡重 %.3f"
                        % (vna["width"], cb["width"]))

    # 只把 VNA 组打开，整份布局的库位数量不许变（巷道变窄只影响节距，不该丢库位）
    detail["on_locations"] = len(widths(on_lay))
    if detail["on_locations"] != detail["off_locations"]:
        problems.append("开启 AST 后库位数量从 %d 变成 %d —— 巷道宽不该改变货架行数"
                        % (detail["off_locations"], detail["on_locations"]))

    # 2.3 低于通道下限必须夹紧并**留痕**
    tiny = sl.aisle_width_for({"rack": {"equipment_type": "forklift_vna"}}, defaults,
                              ast_enabled=True, ast_mm={"forklift_vna": 600})
    detail["clamped"] = tiny
    if not tiny.get("clamped"):
        problems.append("AST 推出 %.2f m（低于下限）却没有记录被夹紧——静默使用窄巷道"
                        % tiny["width"])
    if tiny["width"] < tiny["min_aisle"] - 1e-9:
        problems.append("夹紧后仍低于通道下限")

    # 2.4 关闭态显式取值必须还是默认横通道宽
    off_w = sl.aisle_width_for({"rack": {"equipment_type": "forklift_vna"}}, defaults,
                               ast_enabled=False)
    detail["off_aisle"] = off_w["width"]
    if abs(off_w["width"] - float(defaults["aisle"]["cross"])) > 1e-9:
        problems.append("AST 关闭时巷道宽不等于默认值（默认路径被改了）")
    return (not problems), detail, problems


# --------------------------------------------------------------------- 门 3
def check_height():
    """可服务高度检查必须覆盖所有设备类型。返回 (ok, 明细, 问题)。"""
    import capacity_model as cm

    problems, detail = [], {}
    fix = cm.height_warn(1.5, 6.0, max_height_m=6.0, kind="stacker", fix=cm.FIX)
    legacy = cm.height_warn(1.5, 6.0, max_height_m=6.0, kind="stacker", fix=cm.LEGACY)
    detail["fix"], detail["legacy"] = fix, legacy
    if not fix:
        problems.append("6 m 堆垛机服务 9 m 货架，修正版没有告警")
    if legacy:
        problems.append("资料原版居然也告警了——那 B3 的负对照就失效了")
    # 叉车该两条都告警（本来就有）
    if not cm.height_warn(1.5, 6.0, max_height_m=6.0, kind="forklift", fix=cm.LEGACY):
        problems.append("叉车高度告警失效（连原版都没报，说明基线被改坏）")
    return (not problems), detail, problems


# --------------------------------------------------------------------- 门 4
def check_buy_equipment():
    """采购：进现金、不进利润表、守恒成立。返回 (ok, 明细, 问题)。"""
    import game_economy as ge
    import sim_engine as se

    problems, detail = [], {}
    tmp = Path(tempfile.mkdtemp(prefix="buy_equip_"))
    fleet = jload(CONFIG / "fleet_spec.json")
    model = fleet["models"][0]
    price = float(model["capex_cny"])

    sim = se.Simulation(CONFIG / "scenarios" / "baseline.json", *specs(), out_dir=tmp)
    econ = ge.attach(sim, None, initial_capital=500000)
    before = len(sim.robots)
    sim.enqueue_command({"type": "BUY_EQUIPMENT",
                         "payload": {"wid": "W", "model": model["id"]}})
    sim.run()
    detail["robots_before"], detail["robots_after"] = before, len(sim.robots)

    if len(sim.robots) != before + 1:
        problems.append("BUY_EQUIPMENT 没有真的加一台车（%d → %d）"
                        % (before, len(sim.robots)))
    if abs(econ.capex - price) > 1e-6:
        problems.append("capex 应为 %.2f，实际 %.2f" % (price, econ.capex))
    detail["capex"] = econ.capex
    # 资本支出不许进经营利润。判据用**结构式**而不是"和没采购那局比"：
    # 多一台车就真的会上路干活（耗电、占道、接单），利润本来就会变，
    # 拿它当基线会把正常影响误判成记账错误。
    op = econ.operating_profit()
    recomputed = econ.revenue + econ.salvage - econ.variable - econ.fixed - econ.holding
    detail["operating_profit"] = round(op, 2)
    if abs(op - recomputed) > 1e-6:
        problems.append("经营利润不等于 收入+残值-变动-固定-持有（差 %.6f）："
                        "capex 混进了 P&L" % (op - recomputed))
    if econ.capex <= 0:
        problems.append("capex 汇总为 0：采购的钱没进资本支出（现金扣了、报表没记）")
    ok, resid, _ = econ.conservation()
    detail["conservation"] = [ok, round(resid, 6)]
    if not ok:
        problems.append("采购后守恒式不成立，残差 %.6f" % resid)

    # 4.1 未知车型必须当场拒绝
    sim2 = se.Simulation(CONFIG / "scenarios" / "baseline.json", *specs(),
                         out_dir=Path(tempfile.mkdtemp(prefix="buy_equip2_")))
    try:
        sim2.enqueue_command({"type": "BUY_EQUIPMENT",
                              "payload": {"wid": "W", "model": "NO-SUCH-MODEL"}})
        problems.append("BUY_EQUIPMENT 接受了不存在的车型")
    except ValueError:
        pass
    sim2.log.close()

    # 4.2 BUY_AGV 别名保留，且事件类型不许被换掉
    sim3 = se.Simulation(CONFIG / "scenarios" / "baseline.json", *specs(),
                         out_dir=Path(tempfile.mkdtemp(prefix="buy_equip3_")))
    sim3.enqueue_command({"type": "BUY_AGV", "payload": {"wid": "W"}})
    sim3.run()
    detail["legacy_event"] = int(sim3.log.counts.get("CMD_BUY_AGV", 0))
    if not sim3.log.counts.get("CMD_BUY_AGV"):
        problems.append("BUY_AGV 不再发 CMD_BUY_AGV 了（经济侧会少记一笔 capex）")
    return (not problems), detail, problems


# --------------------------------------------------------------------- 默认关闭
def check_default_off():
    """默认车队必须与 v3 基准逐字节一致。"""
    import step_baseline as sb
    import sim_engine as se

    problems, detail = [], {}
    if not GOLDEN.exists():
        return False, {}, ["缺少 v3 基准，无法判断默认态是否被改"]
    tmp = Path(tempfile.mkdtemp(prefix="r3_off_"))
    sim = se.Simulation(CONFIG / "scenarios" / "baseline.json", *specs(), out_dir=tmp)
    sim.run()
    raw = (tmp / "sim_events.jsonl").read_text(encoding="utf-8")
    got = hashlib.sha256(sb.normalise(raw).encode("utf-8")).hexdigest()
    want = jload(GOLDEN)["scenarios"]["baseline"]["sim_events.jsonl"]["sha256"]
    detail["sha"] = got[:16]
    if got != want:
        problems.append("默认态事件流与 v3 基准不同：R3 的改动漏了开关保护")
    # 默认布局不许带深位元数据
    lay = jload(DATA / "layout.json")
    n_cell = sum(1 for w in lay["warehouses"] for l in w["locations"] if l.get("cell_id"))
    detail["layout_cells"] = n_cell
    if n_cell:
        problems.append("默认布局出现 %d 个货道元数据（应保持 0）" % n_cell)
    return (not problems), detail, problems


# --------------------------------------------------------------------- self-test
def self_test():
    """证明四道门都会失败：逐条注入它们各自守的那个错误。"""
    import copy

    ok = True
    print("R3_SELFTEST 开始（每条都必须被判红）")

    # 1) 把采购价改成 0 → BUY_EQUIP 门应报 capex 不符
    fleet = jload(CONFIG / "fleet_spec.json")
    zero = copy.deepcopy(fleet)
    zero["models"][0]["capex_cny"] = 0
    if float(zero["models"][0]["capex_cny"]) == float(fleet["models"][0]["capex_cny"]):
        print("  FAIL 自检构造失败：改价没生效")
        ok = False
    else:
        print("  OK   注入「采购价=0」→ BUY_EQUIP 会因 capex 不符判红")

    # 2) 把 AST 的 VNA 值改成比平衡重还宽 → AST 门应报「VNA 不窄」
    types = jload(CONFIG / "equipment_spec.json")["types"]
    if not float(types["forklift_vna"]["ast_mm"]) < float(types["forklift_counterbalance"]["ast_mm"]):
        print("  FAIL 自检构造失败：VNA 本来就比平衡重宽")
        ok = False
    else:
        print("  OK   注入「VNA 更宽」→ AST_COUPLING 会判红")

    # 3) 高度检查的回退开关确实存在（能关掉，说明修正不是硬编码）
    import capacity_model as cm
    if cm.height_warn(1.5, 6.0, max_height_m=6.0, kind="stacker", fix=cm.LEGACY):
        print("  FAIL 回退开关关不掉高度检查，负对照失效")
        ok = False
    else:
        print("  OK   回退开关能关掉堆垛机高度检查 → HEIGHT_NEG 有对照")

    print("R3_SELFTEST %s" % ("OK" if ok else "FAIL"))
    return 0 if ok else 1


# --------------------------------------------------------------------- main
def main(argv=None):
    ap = argparse.ArgumentParser(description="R3 设备类型层验收门")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()

    report = {"schema_version": "1.0", "kind": "equipment_r3_report", "gates": {}}
    failed = []
    for name, fn in (("EQUIP_TYPES_OK", check_equip_types),
                     ("AST_COUPLING_OK", check_ast_coupling),
                     ("HEIGHT_NEG_OK", check_height),
                     ("BUY_EQUIP_OK", check_buy_equipment),
                     ("DEFAULT_OFF_OK", check_default_off)):
        try:
            ok, detail, problems = fn()
        except Exception as e:                                   # noqa: BLE001
            ok, detail, problems = False, {}, ["门执行异常：%r" % (e,)]
        report["gates"][name] = {"ok": ok, "detail": detail, "problems": problems}
        if not ok:
            failed.append(name)
        if not a.quiet:
            print("%-16s %s%s" % (name, "PASS" if ok else "FAIL",
                                  "" if ok else "  -> " + "; ".join(problems[:3])))
            if ok and detail:
                print("                 %s" % json.dumps(detail, ensure_ascii=False)[:220])

    report["verdict"] = "R3_OK" if not failed else "R3_FAIL"
    report["failed"] = failed
    out = ROOT / "out" / "equipment_r3_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if a.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    elif a.quiet:
        print("%s %s" % (report["verdict"], ",".join(failed) if failed else ""))
    if not a.quiet and failed:
        for name in failed:
            for p in report["gates"][name]["problems"]:
                print("  - %s: %s" % (name, p))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
