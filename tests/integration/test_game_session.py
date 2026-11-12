"""实时会话与游戏 API（P2 GAME_OK / P3 / P4 / P5 的接口层）。

分两段测：
  - **会话层**：墙钟循环真的推进、暂停真的停、倍速真的快、并发命令不丢不重。
  - **HTTP 层**：`/api/game/*` 契约按方案书 §2.4.4 跑一遍（真起一个服务，不 mock）。

为什么要真起服务：前端只认 HTTP 契约。只测 Python 函数会漏掉路由拼错、
JSON 形状不对、错误码不对这类"函数对了接口还是不通"的问题。
"""
from __future__ import annotations

import json
import sys
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

import console.game as game_mod  # noqa: E402
import console.server as server_mod  # noqa: E402
import game_save as game_save_mod  # noqa: E402


# ------------------------------------------------------------------ 会话层
@pytest.fixture()
def session(tmp_path, monkeypatch):
    monkeypatch.setattr(game_mod, "SESSIONS", tmp_path / "sessions")
    sess = game_mod.MANAGER.start("baseline", mode="live")
    yield sess
    sess.stop()
    game_mod.MANAGER.stop()


def wait_until(pred, timeout=6.0, interval=0.02):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if pred():
            return True
        time.sleep(interval)
    return False


def test_live_session_advances(session):
    assert wait_until(lambda: session.sim.tick_i > 0), "实时会话起来后 tick 没有推进"
    t1 = session.sim.tick_i
    assert wait_until(lambda: session.sim.tick_i > t1), "会话停住了"


def test_pause_really_stops_the_clock(session):
    assert wait_until(lambda: session.sim.tick_i > 0)
    session.pause(True)
    time.sleep(0.15)                    # 让循环走到暂停分支
    t = session.sim.tick_i
    time.sleep(0.4)
    assert session.sim.tick_i == t, "暂停后 tick 还在涨（暂停只停了前端动画？）"
    session.pause(False)
    assert wait_until(lambda: session.sim.tick_i > t), "恢复后没有继续推进"


def test_speed_changes_step_rate(session):
    session.pause(True)
    session.set_speed(8)
    assert session.speed == 8
    assert not session.paused, "设倍速应同时解除暂停"
    base = session.sim.tick_i
    assert wait_until(lambda: session.sim.tick_i > base + 10, timeout=5)


def test_bad_speed_rejected(session):
    with pytest.raises(game_mod.GameError):
        session.set_speed(3)


def test_concurrent_commands_are_not_lost(session):
    """并发发命令：不丢、不重、不炸。锁是唯一的保护，这条就是锁的证伪。"""
    n_threads, per_thread = 6, 20
    errors = []

    def worker(i):
        try:
            for k in range(per_thread):
                session.command({"type": "SET_POLICY",
                                 "payload": {"dispatch.battery_reserve_pct": 20 + (k % 5)}})
        except Exception as exc:                       # noqa: BLE001
            errors.append(repr(exc))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors
    want = n_threads * per_thread
    assert wait_until(lambda: session.sim.log.counts.get("CMD_SET_POLICY", 0) >= want,
                      timeout=8), (
        "并发命令只有 %d/%d 条被执行" % (session.sim.log.counts.get("CMD_SET_POLICY", 0), want))


def test_fast_forward_to_settle(session):
    session.pause(True)
    out = session.fast_forward("settle", max_s=120)
    assert out["ok"]
    assert session.sim.t >= 120 or session.finished


def test_load_keeps_progress_and_can_resume(tmp_path, monkeypatch):
    """读档后必须①保住进度②paused=false 时真的继续走。

    这两个都是浏览器点击流抓出来的真 bug：
      - `GameSession.start()` 无条件 `_init_run()` → 读档后 t 归零（看起来读成功，
        其实从头再开一局）；
      - `/api/game/load` 只在 paused=false 时起线程 → 点了"继续"时钟不动。
    单测只测"读档后 t 对不对"是抓不到第二个的，所以这里必须等真实线程推进。
    """
    monkeypatch.setattr(game_mod, "SESSIONS", tmp_path / "sessions")
    monkeypatch.setattr(game_mod, "MANAGER", game_mod.GameManager())
    monkeypatch.setattr(game_save_mod, "SLOTS", tmp_path / "slots")
    monkeypatch.setattr(game_save_mod, "SAVES", tmp_path)

    sess = game_mod.MANAGER.start("baseline", mode="live")
    assert wait_until(lambda: sess.sim.t > 0)
    sess.pause(True)
    time.sleep(0.2)
    saved_t = sess.sim.t
    sess.save("resume")
    sess.stop()

    loaded = game_mod.GameSession.load("resume")
    assert loaded.sim.t == pytest.approx(saved_t, abs=1e-9), \
        "读档后 t 变了（进度丢失）：%s != %s" % (loaded.sim.t, saved_t)
    assert loaded.paused is True, "读档应停在暂停"

    loaded.start()                      # 线程起来，但仍是暂停
    time.sleep(0.3)
    assert loaded.sim.t == pytest.approx(saved_t, abs=1e-9), "暂停状态下不该推进"

    loaded.pause(False)
    assert wait_until(lambda: loaded.sim.t > saved_t + 0.05, timeout=5), \
        "点了继续但时钟没动（读档会话没有 tick 线程）"
    loaded.stop()


# ------------------------------------------------------------------ HTTP 层
@pytest.fixture()
def http_server(tmp_path, monkeypatch):
    monkeypatch.setattr(game_mod, "SESSIONS", tmp_path / "sessions")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server_mod.Handler)
    port = httpd.server_address[1]
    th = threading.Thread(target=httpd.serve_forever, daemon=True)
    th.start()
    try:
        yield "http://127.0.0.1:%d" % port
    finally:
        httpd.shutdown()
        game_mod.MANAGER.stop()


def get_json(base, path):
    with urllib.request.urlopen(base + path, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def post_json(base, path, body):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(base + path, data=data,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8") or "{}")


def test_http_start_state_pause_speed(http_server):
    code, started = post_json(http_server, "/api/game/start", {"scenario_id": "baseline"})
    assert code == 200 and started["ok"]
    assert started.get("session_id")

    st = get_json(http_server, "/api/game/state")
    assert st["ok"] and "robots" in st and "economy" in st
    assert wait_until(lambda: get_json(http_server, "/api/game/state")["tick_i"] > 0)

    code, paused = post_json(http_server, "/api/game/pause", {"paused": True})
    assert code == 200 and paused["paused"] is True
    time.sleep(0.15)
    t = get_json(http_server, "/api/game/state")["tick_i"]
    time.sleep(0.3)
    assert get_json(http_server, "/api/game/state")["tick_i"] == t

    code, sp = post_json(http_server, "/api/game/speed", {"speed": 4})
    assert code == 200 and sp["speed"] == 4

    code, bad = post_json(http_server, "/api/game/speed", {"speed": 3})
    assert code == 400 and not bad["ok"]


def test_http_command_and_errors(http_server):
    post_json(http_server, "/api/game/start", {"scenario_id": "baseline"})
    code, out = post_json(http_server, "/api/game/command",
                          {"type": "SET_POLICY", "payload": {"dispatch.policy": "highest_soc"}})
    assert code == 200 and out["ok"]

    code, out = post_json(http_server, "/api/game/command",
                          {"type": "TELEPORT", "payload": {}})
    assert code == 400 and "unknown command" in out["error"]

    code, out = post_json(http_server, "/api/game/command",
                          {"type": "BUILD_STATION", "payload": {}})
    assert code == 400 and "尚未实现" in out["error"]

    code, out = post_json(http_server, "/api/game/command", {"type": "BUY_AGV",
                                                             "payload": {"wid": "W"}})
    assert code == 200


def test_http_save_and_load(http_server):
    post_json(http_server, "/api/game/start", {"scenario_id": "baseline"})
    wait_until(lambda: get_json(http_server, "/api/game/state")["tick_i"] > 5)
    post_json(http_server, "/api/game/pause", {"paused": True})

    code, saved = post_json(http_server, "/api/game/save", {"slot": "pytest_slot"})
    assert code == 200 and saved["ok"], saved
    tick = saved["tick_i"]

    code, loaded = post_json(http_server, "/api/game/load", {"slot": "pytest_slot"})
    assert code == 200 and loaded["ok"], loaded
    assert abs(loaded["tick_i"] - tick) <= 1, "读档后 tick 与存档点不符"

    code, slots = post_json(http_server, "/api/game/save/delete", {"slot": "pytest_slot"})
    assert code == 200


def test_http_levels_endpoint(http_server):
    d = get_json(http_server, "/api/game/levels")
    assert d["ok"] and len(d["levels"]) >= 4
    ids = [lv["id"] for lv in d["levels"]]
    assert ids[0] == "level_01"
    assert d["unlocked"]["level_01"] is True, "第一关必须默认解锁"
    assert d["unlocked"].get("level_04") in (True, False)


def test_http_state_without_session(http_server):
    st = get_json(http_server, "/api/game/state")
    assert st["ok"] is False and st["error"] == "no_session"
    code, out = post_json(http_server, "/api/game/pause", {"paused": True})
    assert code == 409
