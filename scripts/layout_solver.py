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

#: 至少一跨（bay）。连一跨都放不下说明组起点已经越过上限，是布局冲突不是参数问题。
MIN_BAYS = 1

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

    R8.11（WP1）：取数走 `solid_registry.spec_band`，不再在这里就地拼。
    这条函数曾经自己列表推导，于是"areas 归 spec_areas 管、充电位归深度轴管"
    这类分工只存在于代码形态里；现在成员（`members`）带出处，门能验"深度轴
    的输入真的来自登记册"。返回结构与历史口径一致，`area_reflow` 数字不变。
    """
    import solid_registry
    return solid_registry.spec_band(wh)


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


def shift_spawns_x(wh, new_width, old_width=None, anchor_ratio=0.5):
    """宽度变化时，**贴着东墙的待命位**跟着东墙走；返回 (新 wh, 记录)。

    与 `shift_band` 同一条纪律：锚定关系决定谁动。
    月台锚南墙、功能带锚后墙、**待命位锚东墙**（默认布局 6 个 x=98.25，正好留
    1.75 m 贴墙），所以宽度一变，只有东侧那排该平移，西侧那排（x=1.75）不动。
    判据是"离哪面墙近"，不是拍一个阈值常量：`x >= 基线宽 * 0.5` 归东墙。

    **幂等**同样靠基线绝对参照：第一次调用把原始点钉进 `area_baseline.spawn_x0`，
    之后一律用 `x0 + (新宽 - 基线宽)` 重算，而不是在当前值上累加 dx——后者跑两次
    会推两次，门本来就会重跑。

    没有 `area_baseline.width` 就**不平移**（同 `shift_band`：不猜常量）。这时货架
    组的上限会由待命位（blockers）自己挡住，所以最坏结果是"少了宽度红利"，
    不是"待命位被货架吃掉"。
    """
    base = wh.setdefault("area_baseline", {})
    old = (float(old_width) if old_width is not None
           else (float(base["width"]) if base.get("width") is not None else None))
    rec = {"new_width": float(new_width), "old_width": old, "dx": 0.0,
           "moved": 0, "anchor": "east_wall"}
    wh.setdefault("size", {})["width"] = float(new_width)
    if old is None:
        rec["note"] = "没有 area_baseline.width，不推断、不平移"
        return wh, rec
    pts = ((wh.get("spawns") or {}).get("points") or [])
    if not pts:
        rec["note"] = "没有待命位，无需平移"
        return wh, rec
    if "spawn_x0" not in base:
        base["spawn_x0"] = [list(p) for p in pts]
        base["width"] = old
    base_w = float(base["width"])
    dx = float(new_width) - base_w
    rec["dx"] = round(dx, 6)
    # **无条件按基线重算**，不是"dx≠0 才推"：后者在"改大再改回来"时 dx=0，
    # 点会留在放大后的位置（我的第一版就是这样，复位静默失败）。
    for i, p0 in enumerate(base["spawn_x0"]):
        if i >= len(pts):
            break
        if float(p0[0]) < base_w * float(anchor_ratio):
            continue                          # 西侧那排贴西墙，不随东墙动
        new_x = round(float(p0[0]) + dx, 6)
        if abs(new_x - float(pts[i][0])) > 1e-9:
            rec["moved"] += 1
        pts[i][0] = new_x
    if rec["moved"]:
        wh.setdefault("area_reflow_x", []).append(rec)
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


def _group_z_span(group, rows, rack_depth, aisle):
    """货架组在 z 方向占的区间——宽度轴判断"两组是否抢同一条 x 走廊"用。

    只有 `orientation='x'`（行沿 z 推进、bay 沿 x 推进）才有意义；
    其余排布返回 None，由调用方显式给 span，不猜方向。
    """
    if group.get("orientation", "x") != "x":
        return None
    gz = float(group["origin"][1])
    return (gz, gz + int(rows) * (float(rack_depth) + float(aisle)))


def free_bays_for_group(wh, group, rack_length, gap, siblings=None,
                        rows=1, rack_depth=1.1, aisle=3.0, clearance=None,
                        blockers=None):
    """某货架组能排几跨（bay）= **三个上限取最小**，与深度轴同一套纯算术。

    1. **右墙**（`size.width`）减施工余量；
    2. **同 z 走廊里起点更靠右的货架组**（减余量）——右列组被左列组挤，
       左列组又被右列组挡，谁先占住 x 谁定边界；
    3. **同走廊里的待命位**（`blockers`，减余量）——待命位锚东墙、贴墙 1.75 m，
       宽度放大后它们跟着东墙走；货架若排到它们身上，AGV 连发车点都没了
       （第一版只算前两个上限，宽 +20 就炸出 6 条 `HOME-W-R07..R12 AP not walkable`）。

    宽度方向**不存在"功能带前沿"这种上限**：功能带是横跨整个 x 的一条 z 带，
    它约束的是进深，不是宽度。硬把它算进来等于给宽度轴凭空加一条假约束。

    宽度方向也**不需要**深度轴那条"后巷"余量：货位访问点（AP）落在行的前后
    （z 方向），不落在 bay 的左右侧，所以最右一跨靠墙 1 m 仍然取得到货。
    这不是省事，是 AP 几何决定的——写成 rack_length + ap_offset 会把宽度白扔掉。

    放不下一跨就抛 `SolveError`（布局冲突），不静默回到声明值。
    """
    if group.get("orientation", "x") != "x":
        raise SolveError(
            "货架组 zone=%s 的 orientation=%r：宽度轴只实现 'x'（bay 沿 x 推进）。"
            "其余排布请显式给 bays，别让求解器猜方向。"
            % (group.get("zone"), group.get("orientation")))

    origin_x = float((wh.get("origin") or [0, 0])[0])
    gx, _gz = group["origin"]
    mx, _mz = group.get("margin", [2, 3])
    start_x = origin_x + float(gx) + float(mx)
    clear = float(clearance) if clearance is not None else CLEARANCE_M

    width = float(wh["size"]["width"])
    limits = [(origin_x + width - clear, "east_wall@x=%.2f" % (origin_x + width))]

    zspan = _group_z_span(group, rows, rack_depth, aisle)
    for other in siblings or []:
        if other is group or other.get("zone") == group.get("zone"):
            continue
        ox, _oz = other["origin"]
        omx, _omz = other.get("margin", [2, 3])
        o_start = origin_x + float(ox) + float(omx)
        if o_start <= start_x + 1e-9:
            continue
        if zspan is not None:
            ozspan = _group_z_span(other, other.get("rows", 1), rack_depth, aisle)
            if ozspan is not None and (zspan[1] <= ozspan[0] or ozspan[1] <= zspan[0]):
                continue                      # 不抢同一条 x 走廊，不构成上限
        limits.append((o_start - clear, "sibling_zone_%s@x=%.2f"
                       % (other.get("zone"), o_start)))

    for b in blockers or []:
        bx = float(b["x"])
        if bx - clear <= start_x + 1e-9:
            continue                          # 在组起点左侧，不构成右边界
        limits.append((bx - clear, "%s@x=%.2f" % (b.get("id", "blocker"), bx)))

    limit_x, who = min(limits, key=lambda t: t[0])
    budget = limit_x - start_x
    pitch = float(rack_length) + float(gap)
    bays = int((budget + float(gap)) // pitch) if budget > 0 else 0
    if bays < MIN_BAYS:
        raise SolveError(
            "货架组 zone=%s 起点 x=%.2f 已越过 %s（limit x=%.2f），剩余 %.2f m "
            "连一跨（%.2f m）都放不下——这是布局冲突，不是跨数问题"
            % (group.get("zone"), start_x, who, limit_x, budget, pitch))
    return {"bays": bays, "budget": round(budget, 6),
            "start_x": round(start_x, 6), "limit_x": round(limit_x, 6),
            "blocked_by": who, "rack_length": float(rack_length),
            "gap": float(gap), "pitch": round(pitch, 6)}


def solve_bay_budget(wh, group, rack_length, gap, **kw):
    """`width_budget: "from_size"` 的求解入口（与深度轴对称）。"""
    return free_bays_for_group(wh, group, rack_length, gap, **kw)
