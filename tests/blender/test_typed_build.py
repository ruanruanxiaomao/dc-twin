# -*- coding: utf-8 -*-
"""R8.3：带 rack_type 的整场构建与锚点比对（需要 Blender，约 60 s）。

这个用例跑的就是 `check_asset_dispatch.py --typed-build`：生成带机理的布局 →
Blender 真实构建 → 与 `tests/golden/typed_3d.json` 双锚比对。

为什么值得占一分钟：纯 Python 的门只能证明"数据不同"，探针只能证明"mesh 不同"，
只有真的把整场建出来，才能证明**派发在场景里真的生效了**（196 架分到 7 类机理、
fallback=0、托盘数对得上）。锚点用的是 counts + GLB 结构指纹，不是字节哈希——
字节不可复现的原因见 ADR-REFACTOR-12。
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import check_asset_dispatch as cad   # noqa: E402
import _subproc                      # noqa: E402


@pytest.mark.blender
@pytest.mark.slow
def test_typed_build_matches_anchor():
    if not cad.find_blender():
        pytest.skip("没有本机 Blender（设 BLENDER_EXE）")
    golden = json.loads(cad.TYPED_GOLDEN.read_text(encoding="utf-8"))
    assert golden["anchors"]["typed_racks"] > 0
    r = _subproc.run(["scripts/check_asset_dispatch.py", "--typed-build"], cwd=str(ROOT))
    out = (r.stdout or "") + (r.stderr or "")
    assert r.returncode == 0, out[-1500:]
    assert "TYPED_BUILD_OK             PASS" in out
