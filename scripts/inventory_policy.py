# -*- coding: utf-8 -*-
"""R5：库存策略的纯函数层（闭式，无搜索、无 rng、不碰 bpy）。

## 为什么单独一层

库存策略的错法几乎都不是"算不出来"，而是**算错了还没人看得见**：
z 取错（0.95 当成 1.0）、fractile 取成 c/p、安全库存在 σ=0 时不为 0、
EOQ 在 H=0 时除零返回 inf。这些在仿真里表现为"缺货多了一点"，
跟"需求涨了一点"分不开——所以必须先在闭式层把每条公式钉死，再谈接线。

四个公式，全部有闭式解，全部可被一个手算值证伪：

* EOQ  `Q* = sqrt(2DS/H)`
* SS   `SS = z(服务水平) · σ_L`
* ROP  `ROP = μ_L + SS`
* 报童 `Q* = F⁻¹((p−c)/(p−v))`
* GMROI `毛利 / 平均库存成本`

## 边界不是"异常处理"，是定义域

* `H ≤ 0`：持有成本为零时 EOQ 无界（数学上是无穷大），必须炸而不是返回 inf；
* `σ_L < 0`：标准差没有负数，炸；
* `0 < 服务水平 < 1`：端点 0/1 对应 z=−∞/+∞，炸；
* `p ≤ c`：卖一件亏一件，报童的临界分位 ≤0，正确动作是"不备货"（返回 0）；
* `v ≥ p`：残值不低于售价，分母 ≤0，参数自相矛盾，炸。
"""
from __future__ import annotations

import math


class PolicyError(ValueError):
    """参数落在定义域外。宁可炸，也不返回一个"看起来能用"的数。"""


def _f(name, v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise PolicyError("%s 必须是数值，收到 %r" % (name, v))
    if not math.isfinite(x):
        raise PolicyError("%s 必须是有限值，收到 %r" % (name, x))
    return x


def eoq(demand_per_year, order_cost, holding_cost_per_unit_year):
    """经济订货批量 `sqrt(2DS/H)`。

    D 或 S 为 0 → 0（不下单是对的）；H ≤ 0 → 炸（无界，不是 0）。
    """
    D = _f("年需求 D", demand_per_year)
    S = _f("单次订货成本 S", order_cost)
    H = _f("单位年持有成本 H", holding_cost_per_unit_year)
    if D < 0 or S < 0:
        raise PolicyError("D 与 S 必须 ≥ 0（D=%r, S=%r）" % (D, S))
    if H <= 0:
        raise PolicyError("H 必须 > 0（收到 %r）：H=0 时 EOQ 无界" % (H,))
    if D == 0 or S == 0:
        return 0.0
    return math.sqrt(2.0 * D * S / H)


def _norm_ppf(p):
    """标准正态分位数（Acklam 有理逼近，误差 < 1.2e-9）。

    不引 scipy：这一层要能在没有科学栈的机器上跑门，而且"用了哪个近似"
    本身要能被门用几个已知点（0.95→1.6449）钉住。
    """
    if not (0.0 < p < 1.0):
        raise PolicyError("分位必须在 (0,1) 内（收到 %r）" % (p,))
    a = (-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00)
    b = (-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01)
    c = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00)
    d = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00)
    plow = 0.02425
    if p < plow:
        q = math.sqrt(-2.0 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)
    if p > 1.0 - plow:
        q = math.sqrt(-2.0 * math.log(1.0 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
           (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1.0)


def z_from_service_level(service_level):
    """服务水平 → 安全系数 z。0.5 → 0，0.95 → 1.6449。"""
    sl = _f("服务水平", service_level)
    if not (0.0 < sl < 1.0):
        raise PolicyError("服务水平必须在 (0,1) 内（收到 %r）：端点对应 z=±∞" % (sl,))
    return _norm_ppf(sl)


def safety_stock(z, sigma_lead_time):
    """安全库存 `z · σ_L`。σ=0 → 0（需求确定就不需要缓冲）。"""
    zz = _f("z", z)
    s = _f("σ_L", sigma_lead_time)
    if s < 0:
        raise PolicyError("σ_L 必须 ≥ 0（收到 %r）：标准差没有负数" % (s,))
    return zz * s


def reorder_point(demand_mean_lead_time, safety):
    """补货点 `μ_L + SS`。"""
    mu = _f("μ_L", demand_mean_lead_time)
    ss = _f("SS", safety)
    if mu < 0:
        raise PolicyError("μ_L 必须 ≥ 0（收到 %r）" % (mu,))
    return mu + ss


def rop_from_policy(demand_per_hour, lead_time_h, service_level,
                    sigma_per_hour=None, demand_cv=None, floor=1.0):
    """把"每小时需求 + 提前期 + 服务水平"一步算成补货点。

    `sigma_per_hour` 不给时按 `demand_cv · μ` 估（缺省 cv=0，即 σ=0）。
    `floor` 是业务下界：仓库不会因为算出来 0.2 件就不设补货点。
    """
    mu_h = _f("demand_per_hour", demand_per_hour)
    if mu_h < 0:
        raise PolicyError("每小时需求必须 ≥ 0（收到 %r）" % (mu_h,))
    lt = _f("lead_time_h", lead_time_h)
    if lt < 0:
        raise PolicyError("提前期必须 ≥ 0（收到 %r）" % (lt,))
    if sigma_per_hour is None:
        cv = 0.0 if demand_cv is None else _f("demand_cv", demand_cv)
        if cv < 0:
            raise PolicyError("需求变异系数必须 ≥ 0（收到 %r）" % (cv,))
        sigma_h = cv * mu_h
    else:
        sigma_h = _f("sigma_per_hour", sigma_per_hour)
        if sigma_h < 0:
            raise PolicyError("每小时标准差必须 ≥ 0（收到 %r）" % (sigma_h,))
    z = z_from_service_level(service_level)
    mu_l = mu_h * lt
    # 独立同分布假设下 σ_L = σ_h · sqrt(L)——写成 σ_h · L 会高估 1/sqrt(L) 倍，
    # 这是最常见的"安全库存偏大"来源，所以在这里写死公式并在门上钉住。
    ss = safety_stock(z, sigma_h * math.sqrt(lt))
    return {"z": round(z, 6), "mu_lead": round(mu_l, 6), "ss": round(ss, 6),
            "rop": round(max(float(floor), reorder_point(mu_l, ss)), 6)}


def rop_settings(reorder_section, policy_spec):
    """两处开关的联锁：返回 ROP 配置；未启用返回 None；半开则抛错。

    为什么要有这个函数：`reorder.policy=rop_ss` 与
    `inventory_policy_spec.enabled=true` 是两个文件里的两个开关，只开一个的结果是
    **跑出旧阈值却以为用了新策略**——这种"看起来生效"比直接报错危险得多。
    联锁规则写在一处，仿真与门调的都是它。
    """
    policy = str((reorder_section or {}).get("policy", "reorder_point"))
    if policy != "rop_ss":
        return None
    if (policy_spec or {}).get("enabled") is not True:
        raise PolicyError(
            "reorder.policy=rop_ss 但 inventory_policy_spec.enabled != true："
            "两处开关必须同时打开，不做静默降级")
    conf = (policy_spec or {}).get("rop") or {}
    for key in ("lead_time_h", "service_level"):
        if conf.get(key) is None:
            raise PolicyError("inventory_policy_spec.rop 缺 %s" % key)
    return conf


def critical_fractile(cost, price, salvage):
    """报童临界分位 `(p−c)/(p−v)`。

    `p ≤ c` → 0.0（卖一件亏一件，最优动作是不备货），不抛错：
    这是**合法输入下的合理解**，不是参数错误。
    `v ≥ p` 才是真矛盾（残值不低于售价，分母 ≤0），抛错。
    """
    c = _f("成本 c", cost)
    p = _f("售价 p", price)
    v = _f("残值 v", salvage)
    if c < 0 or p < 0:
        raise PolicyError("成本与售价必须 ≥ 0")
    if p <= c:
        return 0.0
    if v >= p:
        raise PolicyError("残值 v=%r ≥ 售价 p=%r：分母 ≤0，参数自相矛盾" % (v, p))
    return (p - c) / (p - v)


def newsvendor_quantity(dist, fractile=None, cost=None, price=None, salvage=None):
    """报童最优订货量 = 需求分布的 `fractile` 分位。

    `dist` 支持 `normal{mu,sigma}` 与 `uniform{lo,hi}`：
    前者用 `mu + sigma·z`，后者用 `lo + f·(hi−lo)`——两条都是闭式，
    不迭代、不采样，所以给定输入必然同一结果。
    """
    d = dist or {}
    kind = d.get("kind")
    if fractile is None:
        if cost is None or price is None or salvage is None:
            raise PolicyError("要么给 fractile，要么给 cost/price/salvage")
        fractile = critical_fractile(cost, price, salvage)
    f = _f("fractile", fractile)
    if not (0.0 <= f <= 1.0):
        raise PolicyError("临界分位必须在 [0,1]（收到 %r）" % (f,))
    if kind == "normal":
        mu = _f("mu", d.get("mu"))
        sigma = _f("sigma", d.get("sigma"))
        if sigma < 0:
            raise PolicyError("正态标准差必须 ≥ 0")
        if f == 0.0:
            return mu
        if f == 1.0:
            raise PolicyError("f=1 对应 z=+∞，订货量无界")
        return mu + sigma * _norm_ppf(f)
    if kind == "uniform":
        lo = _f("lo", d.get("lo"))
        hi = _f("hi", d.get("hi"))
        if hi < lo:
            raise PolicyError("uniform 的 hi 必须 ≥ lo")
        return lo + f * (hi - lo)
    raise PolicyError("未知需求分布 %r（可选 normal / uniform）" % (kind,))


def gmroi(gross_margin, avg_inventory_cost):
    """GMROI = 毛利 / 平均库存成本（>1 才算赚回库存占用的钱）。"""
    gm = _f("毛利", gross_margin)
    c = _f("平均库存成本", avg_inventory_cost)
    if c <= 0:
        raise PolicyError("平均库存成本必须 > 0（收到 %r）：分母为 0 时 GMROI 无界" % (c,))
    return gm / c


def plan_from_spec(spec):
    """把 `config/inventory_policy_spec.json` 算成一组可读的方案数字。

    门用它对账，报告用它出数——**不允许别处再手算一遍**（那会变成两份真相）。
    """
    s = spec or {}
    out = {}
    if s.get("eoq") is not None:
        e = s["eoq"]
        out["eoq"] = round(eoq(e.get("annual_demand"), e.get("order_cost"),
                               e.get("holding_cost_per_unit_year")), 6)
    if s.get("rop") is not None:
        r = s["rop"]
        out["rop"] = rop_from_policy(r.get("demand_per_hour"), r.get("lead_time_h"),
                                     r.get("service_level"),
                                     sigma_per_hour=r.get("sigma_per_hour"),
                                     demand_cv=r.get("demand_cv"))
    if s.get("newsvendor") is not None:
        n = s["newsvendor"]
        f = critical_fractile(n.get("cost"), n.get("price"), n.get("salvage"))
        out["newsvendor"] = {"fractile": round(f, 6),
                             "qty": round(newsvendor_quantity(n.get("dist"), f), 6)}
    if s.get("gmroi") is not None:
        g = s["gmroi"]
        out["gmroi"] = round(gmroi(g.get("gross_margin"),
                                   g.get("avg_inventory_cost")), 6)
    return out
