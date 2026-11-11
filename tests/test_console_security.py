# -*- coding: utf-8 -*-
"""控制台准入测试：证明"本机端口"不是安全边界。

背景：这个服务能改配置、跑流水线。任何网页都能向 127.0.0.1 发 POST，浏览器不会拦，
所以必须自己校验 Host / Origin / token。下面三条是回归底线：

  1. 跨源 POST 必须被拒（CSRF）
  2. 设了 token 就必须带（无凭据写入）
  3. 没有 Origin 的命令行请求照常可用（否则 check_exe.py 全废）
"""
import http.client
import threading
from http.server import ThreadingHTTPServer

import pytest

from console import security
from console import server as srv


def call(port, method, path, headers=None, body=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request(method, path, body=body, headers=headers or {})
    r = conn.getresponse()
    data = r.read()
    conn.close()
    return r.status, data


@pytest.fixture()
def live(monkeypatch):
    monkeypatch.setattr(srv, "TOKEN", "")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), srv.Handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield httpd.server_address[1]
    finally:
        httpd.shutdown()
        httpd.server_close()


@pytest.fixture()
def live_token(monkeypatch):
    monkeypatch.setattr(srv, "TOKEN", "s3cret")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), srv.Handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield httpd.server_address[1]
    finally:
        httpd.shutdown()
        httpd.server_close()


# ---------------------------------------------------------------- 纯函数

def test_host_must_be_loopback():
    assert security.host_ok("127.0.0.1:8765")
    assert security.host_ok("localhost:8765")
    assert security.host_ok("[::1]:8765")
    # DNS rebinding：域名解析到 127.0.0.1，Host 头会是攻击者的域名
    assert not security.host_ok("evil.example.com:8765")
    assert not security.host_ok("192.168.1.9:8765")


def test_origin_rules():
    assert security.origin_ok("http://127.0.0.1:8765")
    assert security.origin_ok("http://localhost:8765/")
    assert security.origin_ok("")                      # 命令行不带 Origin
    assert not security.origin_ok("http://evil.example.com")


def test_non_loopback_bind_requires_token():
    ok, why = security.bind_allowed("0.0.0.0", "")
    assert not ok and "DC_CONSOLE_TOKEN" in why
    assert security.bind_allowed("0.0.0.0", "t")[0]
    assert security.bind_allowed("127.0.0.1", "")[0]


# ---------------------------------------------------------------- 端到端

def test_post_without_origin_still_allowed(live):
    """curl / check_exe.py 不带 Origin，不能被误伤。"""
    st, _ = call(live, "POST", "/api/areas/plan", {"Content-Type": "application/json"},
                 b'{"enabled": []}')
    assert st == 200


def test_cross_origin_post_rejected(live):
    st, body = call(live, "POST", "/api/areas/plan",
                    {"Content-Type": "application/json",
                     "Origin": "http://evil.example.com"},
                    b'{"enabled": []}')
    assert st == 403
    assert "跨源" in body.decode("utf-8", "replace")


def test_same_origin_post_allowed(live):
    st, _ = call(live, "POST", "/api/areas/plan",
                 {"Content-Type": "application/json",
                  "Origin": "http://127.0.0.1:%d" % live},
                 b'{"enabled": []}')
    assert st == 200


def test_token_required_when_configured(live_token):
    st, _ = call(live_token, "GET", "/api/status")
    assert st == 401
    st, _ = call(live_token, "GET", "/api/status", {"X-DC-Token": "wrong"})
    assert st == 401
    st, _ = call(live_token, "GET", "/api/status", {"X-DC-Token": "s3cret"})
    assert st == 200


def test_token_blocks_write(live_token):
    st, _ = call(live_token, "POST", "/api/areas/plan",
                 {"Content-Type": "application/json"}, b'{"enabled": []}')
    assert st == 401
