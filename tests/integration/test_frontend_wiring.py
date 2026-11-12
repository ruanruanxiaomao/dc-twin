"""前端接线（P2-2 LIVE_OK 的静态部分）。

浏览器自动化（tabbit / Playwright）在本机没装，所以这里不做点击流，而是钉"接线是否
接通"这层最容易出错、也最容易被漏掉的东西：
  - 页签 / 面板 / 脚本三者都在 index.html 里（少一个就是"点了没反应"）；
  - app.js 的播放、倍速、采样、时钟四处都接到了 DC_LIVE（少一处就是"画面不动"）；
  - game.js 只通过 HTTP 拿状态，不自己算时间/算钱（ADR-GAME-02）。

行为本身由 tests/integration/test_game_session.py 的真 HTTP 测试覆盖。
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
STATIC = ROOT / "console" / "static"


def read(name):
    return (STATIC / name).read_text(encoding="utf-8")


def test_index_has_game_tab_pane_and_script():
    html = read("index.html")
    assert 'data-tab="game"' in html, "没有游戏页签"
    assert 'id="pane-game"' in html, "没有游戏面板"
    assert '/static/game.js' in html, "game.js 没被引入"
    # game.js 必须在 app.js 之后：它要用 app.js 建立的全局
    assert html.index("/static/app.js") < html.index("/static/game.js")


def test_app_wires_live_mode_in_four_places():
    js = read("app.js")
    assert "DC_LIVE" in js, "app.js 完全没接直播钩子"
    # 1) 采样  2) 播放  3) 倍速  4) 时钟
    assert "DC_LIVE.pos(" in js, "3D 采样没接直播"
    assert "DC_LIVE.setPaused(" in js, "播放键没接服务端暂停"
    assert "DC_LIVE.setSpeed(" in js, "倍速没接服务端"
    assert "DC_LIVE.time()" in js, "时钟没接服务端 t_s"
    assert "DC_LIVE.horizon()" in js, "时间轴没按整局时长铺满"


def test_replay_mode_is_preserved():
    """回放（复盘）必须还在：直播只是分支，不是替换。"""
    js = read("app.js")
    assert "S.t += Math.min(dt, 0.25) * S.speed" in js, "原回放推进逻辑被删了"
    assert "sampleAgent(a, t)" in js, "原关键帧插值被删了"


def test_game_js_does_not_do_its_own_accounting():
    js = read("game.js")
    # 只允许"显示"服务端数字，不允许自己累加成本/收入
    assert "api('/api/game/state')" in js
    for forbidden in ("已收 +=", "cash +=", "revenue +="):
        assert forbidden not in js, "前端在算钱：%s（违反 ADR-GAME-02）" % forbidden


def test_game_js_endpoints_cover_contract():
    js = read("game.js")
    for ep in ("/api/game/start", "/api/game/pause", "/api/game/speed",
               "/api/game/command", "/api/game/state", "/api/game/save",
               "/api/game/load", "/api/game/levels"):
        assert ep in js, "前端没有调用 %s" % ep


def test_game_css_present():
    css = read("style.css")
    for cls in (".g-hud", ".g-card", ".g-clock", ".g-level", ".g-toast"):
        assert cls in css, "缺少样式 %s（会渲染成裸 div）" % cls
