"""状态表重生成必须幂等（`report_status.write_if_changed`）。

为什么值得单独测：这份文件带"生成时间"却被提交进版本库，只要重生成就变脏。
表面上只是 `git status` 多一行，实际后果是**diff 里永远只有时间戳**——真正的口径
变化（"31 步"改"45 步"）被噪音淹没，审查的人扫过去就跳过了。

我第一版修复写了两个 bug，都在这条测试覆盖范围内：
  1. 用列表比较，而 `lines` 末尾的空串会被 `splitlines()` 吃掉 → 恒不等，幂等从未生效；
  2. 只把时间戳行 filter 掉、忘了末尾空行 → 同一个恒不等。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import report_status as rs  # noqa: E402


def build(ts, extra=None):
    """造一份最小状态表：结构对齐真实输出（含末尾空串、含时间戳行）。"""
    lines = [
        '# 当前状态（自动生成，请勿手改）',
        '',
        '| 指标 | 当前值 |',
        '|---|---|',
        '%s %s |' % (rs.TS_PREFIX, ts),
        '| Blender | 5.2.3 LTS |',
    ]
    if extra:
        lines.append(extra)
    lines += ['', '## 口径纪律', '', '1. 场景数只认 config/scenarios/*.json。', '']
    return lines


def test_second_write_is_a_noop(tmp_path):
    p = tmp_path / "CURRENT_STATUS.md"
    assert rs.write_if_changed(p, build("2026-11-12 10:00:00")) is True
    first = p.read_text(encoding="utf-8")
    # 时间戳变了、内容没变 → 不该重写
    assert rs.write_if_changed(p, build("2026-11-12 23:59:59")) is False
    assert p.read_text(encoding="utf-8") == first, "只差时间戳却重写了文件"


def test_real_content_change_does_rewrite(tmp_path):
    p = tmp_path / "CURRENT_STATUS.md"
    rs.write_if_changed(p, build("2026-11-12 10:00:00"))
    assert rs.write_if_changed(p, build("2026-11-12 10:00:01", extra="| 游戏层 | 4 关 |")) is True
    text = p.read_text(encoding="utf-8")
    assert "| 游戏层 | 4 关 |" in text
    assert "10:00:01" in text, "内容变了却没刷新时间戳"


def test_trailing_blank_line_does_not_defeat_idempotency(tmp_path):
    """专门钉住那个"末尾空串"的坑：两次都传以空串结尾的 lines。"""
    p = tmp_path / "CURRENT_STATUS.md"
    assert rs.write_if_changed(p, build("t1")) is True
    assert rs.write_if_changed(p, build("t2")) is False
    assert rs.write_if_changed(p, build("t3")) is False


def test_body_lines_strips_timestamp_and_trailing_blanks():
    got = rs._body_lines("| 生成时间 | x |\n| A | 1 |\n\n")
    assert got == ['| A | 1 |']


def test_generated_file_is_idempotent_on_disk():
    """真实产物的回归：连跑两次，第二次必须报告"未变"。"""
    target = ROOT / "out" / "CURRENT_STATUS.md"
    if not target.exists():
        import pytest
        pytest.skip("还没有生成过 CURRENT_STATUS.md")
    import hashlib

    def sha():
        return hashlib.sha256(target.read_bytes()).hexdigest()

    before = sha()
    assert rs.write_if_changed(str(target), target.read_text(encoding="utf-8").splitlines()) is False
    assert sha() == before
