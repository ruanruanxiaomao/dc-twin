# -*- coding: utf-8 -*-
"""D10b：面积变化时的功能区重排求解器（纯算术，无 rng、无时钟、不碰 bpy）。

## 它要解的是什么

R8.6 的诚实结论是："填满进深"当场撞出 1022 处 L3/L4/L6，因为 `depth_budget="from_size"`
把预算算成 `厂房进深 - 边距 - 1`，然后按节距一路排下去，**完全不看功能区在哪**。
多出来的行直接压进 z=62..67 的 PACK/SORT，库位访问点（AP）也跟着落进站台里。

所以问题不是"行数算错了"，而是**没人回答"从哪儿开始不能再排"**。

## 解法：按锚定关系切分进深，不做搜索

布局里有三种东西，锚定关系天然不同：

* **月台**锚定**南墙**（`docks.edge="south"`）——卸货口对着场外，挪了就没意义；
* **功能区**（收货暂存 / 包装 / 分拣）锚定**后墙**——它们要靠墙摆，但"哪面墙"随厂房走；
* **货架**吃**中间剩下的**进深。

于是面积一变，正确动作不是"多排几行"，而是：

1. 量出功能带的高度（`厂房进深 - 功能带起点`），这个高度**不随面积变**；
2. 把功能带整体**平移到新的后墙内侧**（`shift = 新进深 - 旧进深`）；
3. 货架组的进深预算 = **功能带新起点 - 货架起点 - 余量**。

第 2 步是关键：功能带平移到 84-8=76 之后，C 组从 41 起就有 76-41-1=34 m 可用，
行数从 4 行变成 7 行——**面积真的变成了库容，而且 0 违规**，因为货架根本没碰到功能区。

这不是"求解"，是一段减法和一次平移。没有搜索、没有迭代、没有阈值调参，
所以给定输入必然得到同一结果，也就可以被门逐项证伪。
"""

from __future__ import annotations

#: 货架与功能区之间留的施工余量（m）。取 1.0 与 R8.6 的 `-1.0` 保持同一口径，
#: 免得"求解器上线后边界悄悄变了 0.2 m"这种没人能解释的漂移。
CLEARANCE_M = 1.0

#: 行距节距的最小可行进深。低于它连一行都放不下，调用方必须报错而不是硬塞。
MIN_ROW_DEPTH_M = 1.0

#: 功能带后沿到后墙的**缺省**余量（m），仅在没有基线时用于自检/示例。
#: 真正可靠的余量来自 `wh["area_baseline"]["depth"]`（见 `shift_band`）。
TAIL_GAP_M = 3.0


class SolveError(RuntimeError):
    """求解器拒绝给出几何。宁可炸，也不许产出一个"更大但越界"的布局。"""


def _rect_z0(r):
    return float(r[1])


def _rect_z1(r):
    return float(r[3])


def functional_band(wh):
    """功能带：所有 areas / stations 的外接进深区间与高度。

    只认 `areas` 与 `stations`。月台（`docks`）**故意不算**——它锚南墙，
    不属于可平移的带；把它算进来会让 shift 把卸货口一起推走。
    """
    rects = []
    for a in wh.get("areas") or []:
        if isinstance(a, dict) and a.get("rect"):
            rects.append(a["rect"])
    for s in wh.get("stations") or []:
        if isinstance(s, dict) and s.get("rect"):
            rects.append(s["rect"])
    # 充电位也算功能带：它的访问点在 start 往场内 ap_dz 处，若货架排到它跟前，
    # AP 会既不可达（L6）又落在带里（L4）。第一版漏了它，放大后正好剩 1 个
    # `W-CHG-06 AP not walkable`——1022 → 4 里的那个 1。
    ch = wh.get("chargers") or {}
    if ch.get("mode") == "line" and ch.get("start"):
        cz = float(ch["start"][1])
        ap_dz = float(ch.get("ap_dz", 0.0))
        lo, hi = min(cz, cz + ap_dz), max(cz, cz + ap_dz)
        rects.append([0.0, lo, 0.0, hi + 0.5])   # z 带；x 不参与进深判断
    if not rects:
        return None
    z0 = min(_rect_z0(r) for r in rects)
    z1 = max(_rect_z1(r) for r in rects)
    return {"z0": z0, "z1": z1, "height": z1 - z0, "rects": rects}


def _baseline_depth(wh):
    """功能区坐标所对应的**原始**厂房进深。

    **为什么必须显式记基线**：调用方拿到的 spec 里 `size.depth` 已经是新值，而
    areas 的坐标还是旧值。这对输入本身是矛盾的，从它反推"原来多深"只能靠猜——
    我第一版用常量 3 m 当后沿余量，在 W 仓算对了，却把 F1/F2/F3 的功能区整体推后
    3 m，直接把前置仓压出 24 处 L4/L6。**猜一个常量，等于给三个仓同时改了布局。**

    所以：没有基线就**不平移**（shift=0）。面积变化是显式动作，基线也该显式写。
    """
    b = (wh.get("area_baseline") or {}).get("depth")
    return float(b) if b is not None else None


def shift_band(wh, new_depth, old_depth=None, band=None, tail_gap=None):
    """把功能带贴到新后墙内侧；返回 (新 wh, 记录)。

    只平移 z，**不动 x**（功能区靠哪一侧是人为决定），也**不动月台**（锚南墙，
    卸货口对着场外）。充电位、待命位跟着功能带走。

    **幂等**靠"目标位置 - 当前位置"，不是"新进深 - 旧进深"：后者跑两次会推两次，
    而门本来就会重跑，一旦翻倍所有断言都失去意义（第一版就是这么错的）。
    后沿余量从基线反推（`基线进深 - 基线带前沿 - 带高`），所以各仓可以不同。
    """
    band = band or functional_band(wh)
    new_depth = float(new_depth)
    base = wh.setdefault("area_baseline", {}) if old_depth is None else {}
    old = float(old_depth) if old_depth is not None else _baseline_depth(wh)
    rec = {"new_depth": new_depth, "old_depth": old, "shift": 0.0,
           "band_before": None, "band_after": None}
    if band is None:
        rec["note"] = "没有功能区，无需平移"
        wh.setdefault("size", {})["depth"] = new_depth
        return wh, rec
    if old is None:
        rec["note"] = "没有 area_baseline.depth，不推断、不平移"
        wh.setdefault("size", {})["depth"] = new_depth
        return wh, rec
    # 第一次调用时把"基线带的原始位置"钉进基线，之后 target 就有了绝对参照
    if "band_z0" not in base:
        base["band_z0"] = band["z0"]
        base["band_h"] = band["height"]
    gap = (old - float(base["band_z0"]) - float(base["band_h"])
           if tail_gap is None else float(tail_gap))
    target_z0 = new_depth - gap - band["height"]
    shift = target_z0 - band["z0"]
    rec.update({"shift": round(shift, 6), "tail_gap": round(gap, 6),
                "band_before": [band["z0"], band["z1"]],
                "band_after": [round(band["z0"] + shift, 6),
                               round(band["z1"] + shift, 6)]})
    if abs(shift) > 1e-9:
        z0 = band["z0"]

        def move(obj):
            r = obj.get("rect")
            if r and float(r[1]) >= z0 - 1e-9:
                obj["rect"] = [r[0], round(float(r[1]) + shift, 6),
                               r[2], round(float(r[3]) + shift, 6)]

        for a in wh.get("areas") or []:
            if isinstance(a, dict):
                move(a)
        for st in wh.get("stations") or []:
            if isinstance(st, dict):
                move(st)
        ch = wh.get("chargers") or {}
        if ch.get("mode") == "line" and ch.get("start"):
            sx, sz = ch["start"]
            if float(sz) >= z0 - 1e-9:
                ch["start"] = [sx, round(float(sz) + shift, 6)]
        for sp in wh.get("spawns") or []:
            if isinstance(sp, dict) and sp.get("position"):
                pos = sp["position"]
                if float(pos[2]) >= z0 - 1e-9:
                    sp["position"] = [pos[0], pos[1], round(float(pos[2]) + shift, 6)]
    wh["size"]["depth"] = new_depth
    wh.setdefault("area_reflow", []).append(rec)
    return wh, rec


def _group_x_span(group, rl, gap):
    """货架组在 x 方向占的区间。行若与另一组 x 重叠，就不能越进它的起点。"""
    gx = float(group["origin"][0])
    bays = int(group.get("bays", 1))
    # orientation='z' 时行沿 x 推进，x 跨度是 RD+aw；这里只处理 'x'（默认布局全是 'x'），
    # 其余排布交给调用方显式给 span，避免猜错方向。
    if group.get("orientation", "x") != "x":
        return None
    return (gx, gx + bays * (float(rl) + float(gap)))


def free_depth_for_group(wh, group, rack_depth, aisle, band=None,
                         siblings=None, rl=1.1, gap=0.6, ap_offset=1.5,
                         rear_lane=True):
    """某货架组能用的进深 = 三个上限取最小。

    1. **功能带前沿**（减施工余量）——排进去就压 PACK/SORT，正是 1022 处越界的来源；
    2. **同 x 列里起点更靠后的货架组**（减余量）——A 组一路排到 61 会吃掉 C 组的地；
    3. 只给 `from_size` 时不再额外看 zone 框：zone 是渲染用的颜色块，不参与碰撞。
       让行数去服从"颜色框"而不服从真实障碍，等于把审美写进约束。

    返回里 `blocked_by` 必须写清"被谁挡住"：R8.6 那次越界之所以难读，就是因为
    门只报"重叠"，不报"你在跟谁抢地方"。
    """
    band = band or functional_band(wh)
    origin_z = float((wh.get("origin") or [0, 0])[1])
    gx, gz = group["origin"]
    mx, mz = group.get("margin", [2, 3])
    start_z = origin_z + float(gz) + float(mz)
    # 余量必须容得下访问点：AP 落在行前 ap_offset 处，只留 1.0 m 的话最后一排
    # 的 AP 会刚好越过界（实测放大后 3 个 SHELF AP 报 L6 就是这个原因）。
    clear = max(CLEARANCE_M, float(ap_offset))
    if rear_lane:
        # 每排货架后侧也有一个访问点（深位两侧各一个）。最后一排若紧贴上限，
        # 它后侧的 AP 就落在通道里没地方站——实测正是 3 个 SHELF AP 报 L6 的原因。
        # 预留一条「行深 + 访问点偏移」的后巷，宽度按真实几何算，不拍脑袋。
        clear += float(rack_depth) + float(ap_offset) - CLEARANCE_M
    limits = []
    if band is not None:
        limits.append((band["z0"] + origin_z - clear,
                       "functional_band@z=%.2f(clear=%.2f)" % (band["z0"] + origin_z, clear)))
    else:
        limits.append((float(wh["size"]["depth"]) - clear, "rear_wall"))
    if siblings:
        span = _group_x_span(group, rl, gap)
        for other in siblings:
            if other is group or other.get("zone") == group.get("zone"):
                continue
            ox, oz = other["origin"]
            omx, omz = other.get("margin", [2, 3])
            o_start = origin_z + float(oz) + float(omz)
            if o_start <= start_z + 1e-9:
                continue
            if span is not None:
                ospan = _group_x_span(other, rl, gap)
                if ospan is not None and (span[1] <= ospan[0] or ospan[1] <= span[0]):
                    continue
            limits.append((o_start - clear, "sibling_zone_%s@z=%.2f"
                           % (other.get("zone"), o_start)))
    limit_z, who = min(limits, key=lambda t: t[0])
    budget = limit_z - start_z
    if budget < MIN_ROW_DEPTH_M:
        raise SolveError(
            "货架组 zone=%s 起点 z=%.2f 已越过 %s（limit z=%.2f），剩余 %.2f m "
            "连一行都放不下——这是布局冲突，不是行数问题"
            % (group.get("zone"), start_z, who, limit_z, budget))
    return {"budget": round(budget, 6), "start_z": round(start_z, 6),
            "limit_z": round(limit_z, 6), "blocked_by": who,
            "rack_depth": float(rack_depth), "aisle": float(aisle)}


def solve_depth_budget(wh, group, rack_depth, aisle):
    """`depth_budget: "from_size"` 的求解入口。

    与 R8.6 的区别只有一句：R8.6 算到**厂房后墙**，这里算到**功能带前沿**。
    """
    return free_depth_for_group(wh, group, rack_depth, aisle)
