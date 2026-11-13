# -*- coding: utf-8 -*-
"""R5：库存策略闭式层。

要证的四件事：

1. 每个公式对**手算表**（外部推导值，不复用被测实现）；
2. 定义域边界：H=0 无界、σ<0 不合法、服务水平端点对应 ±∞、v≥p 自相矛盾；
3. 两处开关联锁：只开一个必须报错，不静默降级；
4. 同输入同输出（无隐藏状态）。
"""
import json
import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import inventory_policy as ip   # noqa: E402
import _subproc                 # noqa: E402


def test_eoq_matches_hand_calc():
    assert ip.eoq(12500, 180, 2.16) == pytest.approx(1443.376, abs=5e-4)
    assert ip.eoq(0, 180, 2.16) == 0.0


def test_z_table():
    assert ip.z_from_service_level(0.95) == pytest.approx(1.6449, abs=5e-4)
    assert ip.z_from_service_level(0.975) == pytest.approx(1.9600, abs=5e-4)
    assert ip.z_from_service_level(0.99) == pytest.approx(2.3263, abs=5e-4)
    assert abs(ip.z_from_service_level(0.5)) < 1e-9


def test_rop_uses_sqrt_lead_time():
    """σ_L 必须乘 √L：乘 L 会高估 1/√L 倍（L<1 时低估），这是最常见的安全库存错法。"""
    got = ip.rop_from_policy(12.5, 0.5, 0.95, demand_cv=0.3)
    assert got["mu_lead"] == pytest.approx(6.25)
    assert got["ss"] == pytest.approx(1.6449 * 3.75 * math.sqrt(0.5), abs=2e-3)
    assert got["rop"] == pytest.approx(10.612, abs=5e-4)


def test_newsvendor_matches_hand_calc():
    f = ip.critical_fractile(8, 18, 2)
    assert f == pytest.approx(0.625)
    q = ip.newsvendor_quantity({"kind": "normal", "mu": 100, "sigma": 25}, f)
    assert q == pytest.approx(107.966, abs=5e-4)
    u = ip.newsvendor_quantity({"kind": "uniform", "lo": 0, "hi": 100}, 0.625)
    assert u == pytest.approx(62.5)


def test_gmroi():
    assert ip.gmroi(42, 30) == pytest.approx(1.4)


@pytest.mark.parametrize("fn,args", [
    (ip.eoq, (12500, 180, 0.0)),          # H=0 无界
    (ip.eoq, (12500, 180, -1.0)),
    (ip.safety_stock, (1.6, -0.1)),
    (ip.z_from_service_level, (0.0,)),
    (ip.z_from_service_level, (1.0,)),
    (ip.critical_fractile, (8, 18, 20)),  # 残值 ≥ 售价
    (ip.gmroi, (42, 0.0)),
    (ip.newsvendor_quantity, ({"kind": "poisson"}, 0.5)),
])
def test_domain_violations_raise(fn, args):
    with pytest.raises(ip.PolicyError):
        fn(*args)


def test_rop_switches_are_interlocked():
    """两处开关：只开一个必须报错（否则跑出旧阈值却以为用了新策略）。"""
    assert ip.rop_settings({"policy": "reorder_point"}, {"enabled": True}) is None
    with pytest.raises(ip.PolicyError):
        ip.rop_settings({"policy": "rop_ss"}, {"enabled": False})
    conf = ip.rop_settings({"policy": "rop_ss"},
                           {"enabled": True,
                            "rop": {"lead_time_h": 0.5, "service_level": 0.95}})
    assert conf["service_level"] == 0.95
    with pytest.raises(ip.PolicyError):
        ip.rop_settings({"policy": "rop_ss"}, {"enabled": True, "rop": {"service_level": 0.95}})


def test_default_spec_is_off():
    spec = json.loads((ROOT / "config" / "inventory_policy_spec.json").read_text(encoding="utf-8"))
    inv = json.loads((ROOT / "config" / "inventory_spec.json").read_text(encoding="utf-8"))
    assert spec["enabled"] is False
    assert inv["reorder"].get("policy", "reorder_point") == "reorder_point"
    assert ip.rop_settings(inv["reorder"], spec) is None


def test_gate_and_self_test():
    r = _subproc.run(["scripts/check_inventory_policy.py", "--quiet"], cwd=str(ROOT))
    assert r.returncode == 0, (r.stdout or "") + (r.stderr or "")
    assert "POLICY_CLOSED_FORM_OK" in (r.stdout or "")
    r2 = _subproc.run(["scripts/check_inventory_policy.py", "--self-test"], cwd=str(ROOT))
    assert r2.returncode == 0, (r2.stdout or "") + (r2.stderr or "")
    assert "POLICY_SELF_TEST PASS" in (r2.stdout or "")
