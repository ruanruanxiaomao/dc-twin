"""P6a-0：库存层的数据与配置（引擎未动）。

这一阶段要证的只有四件事，任何一件崩了都不该往 P6a-1 走：
  1. `inventory_spec.json` 过 schema（配置是行为的唯一来源，schema 是它的护栏）；
  2. 生成**确定性**：两次生成逐字节一致，且与仓库里已提交的文件一致
     （否则"数据被手改过"或"生成依赖了机器/时钟"都会悄悄发生）；
  3. **每个 SHELF 库位都有 SKU**：P6a-1 要用 pick_loc 反查 SKU，留空位就会 raise；
  4. 期初结存与库位一一对应、全为正整数，汇总数能由明细复算。

P6a-1 之后的守恒/缺货/补货门（STOCK_OK / STOCKOUT_NEG / REORDER_OK）不在这里，
它们要等引擎接线。
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

DATA = ROOT / "data"
CONFIG = ROOT / "config"
OUTPUTS = ("sku_master.json", "inventory_slotting.json", "inventory_initial.json")


def jload(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def spec():
    return jload(CONFIG / "inventory_spec.json")


@pytest.fixture(scope="module")
def slotting():
    return jload(DATA / "inventory_slotting.json")


@pytest.fixture(scope="module")
def initial():
    return jload(DATA / "inventory_initial.json")


@pytest.fixture(scope="module")
def master():
    return jload(DATA / "sku_master.json")


# ------------------------------------------------------------------ 配置
def test_spec_passes_schema(spec):
    import jsonschema
    jsonschema.validate(spec, jload(CONFIG / "schemas" / "inventory_spec.schema.json"))


def test_spec_rejects_unknown_key():
    """schema 必须真的会拒绝：多一个键就报，否则"改 JSON 不改 Python"会变成乱加键。"""
    import jsonschema
    bad = jload(CONFIG / "inventory_spec.json")
    bad["holding"]["accrual_everything"] = True
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, jload(CONFIG / "schemas" / "inventory_spec.schema.json"))


def test_spec_bad_range_rejected():
    import jsonschema
    bad = jload(CONFIG / "inventory_spec.json")
    bad["stockout"]["partial_fill"] = "yes"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, jload(CONFIG / "schemas" / "inventory_spec.schema.json"))


# ------------------------------------------------------------------ 确定性
def _gen(out_dir):
    subprocess.run([sys.executable, str(ROOT / "scripts" / "gen_inventory.py"),
                    "--quiet", "--out-dir", str(out_dir)],
                   cwd=str(ROOT), check=True)


def _sha(d):
    return {n: hashlib.sha256((Path(d) / n).read_bytes()).hexdigest() for n in OUTPUTS}


def test_generation_is_deterministic(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    _gen(a)
    _gen(b)
    assert _sha(a) == _sha(b), "两次生成不一致 → 存在时钟/集合顺序/随机源"


def test_committed_data_matches_generator(tmp_path):
    """仓库里的 data/*.json 必须是生成器的产物，而不是手改过的。"""
    _gen(tmp_path)
    for n in OUTPUTS:
        assert _sha(tmp_path)[n] == hashlib.sha256((DATA / n).read_bytes()).hexdigest(), \
            "%s 与生成器输出不一致（被手改过，或生成逻辑变了没重跑）" % n


def test_outputs_have_no_timestamp(master, slotting, initial):
    """带时间戳就必然不确定。三份产物里不许出现"生成时间"类字段。"""
    for obj in (master, slotting, initial):
        text = json.dumps(obj, ensure_ascii=False)
        for bad in ("generated_at", "timestamp", "时间"):
            assert bad not in text, "产物里出现 %r，确定性会被破坏" % bad


# ------------------------------------------------------------------ 库位映射
def test_every_shelf_location_has_a_sku(slotting):
    layout = jload(DATA / "layout.json")
    shelf_ids = {loc["id"] for w in layout["warehouses"]
                 for loc in w.get("locations", []) if loc.get("kind") == "SHELF"}
    mapped = {r["location_id"]: r for r in slotting["locations"]}
    missing = sorted(shelf_ids - set(mapped))
    assert not missing, "这些 SHELF 库位没有 SKU（P6a-1 反查会 raise）：%s" % missing[:5]
    extra = sorted(set(mapped) - shelf_ids)
    assert not extra, "映射里出现了非 SHELF 库位：%s" % extra[:5]
    assert all(mapped[i]["sku"] for i in shelf_ids), "存在 sku=null 的库位"


def test_sku_master_covers_all_slotted_skus(master, slotting):
    ids = {s["id"] for s in master["skus"]}
    slotted = {r["sku"] for r in slotting["locations"]}
    assert slotted <= ids, "库位映射用到了主数据里没有的 SKU：%s" % sorted(slotted - ids)[:5]
    assert master["count"] == len(master["skus"])


def test_pick_face_respects_config_max(spec, slotting):
    cap = int(spec["pick_face"]["max_per_warehouse"])
    for wid, locs in slotting["pick_face_by_warehouse"].items():
        assert len(locs) <= cap, "%s 拣选面 %d 个，超过上限 %d" % (wid, len(locs), cap)


def test_pick_face_skus_have_reserve(slotting):
    """拣选面的意义是"从储备位补货"；没有储备位的拣选面是死路。"""
    bad = [s for s, v in slotting["by_sku"].items() if v["pick_face"] and not v["reserve"]]
    assert not bad, "这些 SKU 有拣选面却没有储备位：%s" % bad[:5]


def test_roles_partition_locations(slotting):
    rows = slotting["locations"]
    assert all(r["role"] in ("reserve", "pick_face") for r in rows), "出现未定义角色"
    by_sku = slotting["by_sku"]
    total = sum(len(v["reserve"]) + len(v["pick_face"]) for v in by_sku.values())
    assert total == len(rows), "by_sku 汇总与 locations 行数不一致（重复或遗漏）"


# ------------------------------------------------------------------ 期初结存
def test_initial_covers_every_location(initial, slotting):
    locs = {r["location_id"] for r in slotting["locations"]}
    got = {lid for sku in initial["stock"] for lid in initial["stock"][sku]}
    assert got == locs, "期初结存与库位不一一对应：缺 %s / 多 %s" % (
        sorted(locs - got)[:5], sorted(got - locs)[:5])


def test_initial_qty_positive_integers(initial, spec):
    if not spec["stock_policy"].get("qty_is_integer", True):
        pytest.skip("配置允许小数库存")
    bad = [(s, loc_, q) for s, m in initial["stock"].items() for loc_, q in m.items()
           if not isinstance(q, int) or q <= 0]
    assert not bad, "非正整数库存：%s" % bad[:5]


def test_initial_summary_recomputable(initial, master):
    """汇总数必须能从明细复算——手写汇总是最容易和明细脱节的数字。"""
    cost = {s["id"]: s["unit_cost"] for s in master["skus"]}
    units = sum(q for m in initial["stock"].values() for q in m.values())
    value = sum(q * cost[s] for s, m in initial["stock"].items() for q in m.values())
    assert initial["summary"]["total_units"] == units
    assert abs(initial["summary"]["inventory_value"] - value) < 1e-6


def test_pick_face_stock_is_smaller_than_reserve(initial, slotting, spec):
    """拣选面是小批量前置：它的结存应低于同 SKU 的储备位，否则"补货"没有意义。"""
    ratio = float(spec["pick_face"].get("fill_ratio_of_reserve", 0.3))
    bad = []
    for sku, v in slotting["by_sku"].items():
        if not v["pick_face"]:
            continue
        pf = max(initial["stock"].get(sku, {}).get(loc_, 0) for loc_ in v["pick_face"])
        rs = min(initial["stock"].get(sku, {}).get(loc_, 0) for loc_ in v["reserve"])
        if pf >= rs:
            bad.append((sku, pf, rs))
    assert not bad, "拣选面结存不小于储备位（ratio=%.2f）：%s" % (ratio, bad[:3])
