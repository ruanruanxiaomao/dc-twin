#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""控制台请求准入：Host / Origin / token。

这个服务能写 `data/config.json`、能触发整条流水线、能生成扩仓变体，而它默认只要
`127.0.0.1` 就能访问——**任何网页都可以向 127.0.0.1 发 POST**（CSRF）。默认端口不是
安全边界，浏览器不会因为"是本机"就拦下来。所以这里补三道闸：

1. **Host 必须是回环**：防 DNS rebinding。攻击者把自己的域名解析到 127.0.0.1，
   浏览器会把请求发到本地服务，Host 头却是 `evil.com`——只查 IP 拦不住。
2. **带 Origin/Referer 的请求必须同源**：正常页面同源，恶意页面跨源会被拒。
   `curl` 不带 Origin，照常放行（否则命令行调试全废）。
3. **可选的 token**：设了 `DC_CONSOLE_TOKEN` 就要求 `X-DC-Token`；绑定非回环地址时
   **强制**要求，否则拒绝启动——宁可起不来，也不要裸奔在局域网上。
"""
from __future__ import annotations

import ipaddress
import os
from urllib.parse import urlparse

LOOPBACK_NAMES = {"localhost", "127.0.0.1", "::1", ""}


def _host_of(host_header: str) -> str:
    """从 Host 头里取主机名（去掉端口、方括号）。"""
    h = (host_header or "").strip().lower()
    if h.startswith("["):                      # [::1]:8765
        return h[1:h.index("]")] if "]" in h else h
    return h.rsplit(":", 1)[0] if ":" in h else h


def host_ok(host_header: str) -> bool:
    h = _host_of(host_header)
    if h in LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return False


def origin_ok(origin: str) -> bool:
    """Origin/Referer 为空时放行（命令行客户端），有值就必须是回环来源。"""
    if not origin:
        return True
    try:
        host = urlparse(origin).hostname or ""
    except ValueError:
        return False
    if host in LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def check_request(headers, token: str = "") -> tuple[bool, str, int]:
    """返回 (放行?, 原因, HTTP 状态码)。headers 用 email.message.Message 或 dict。"""
    get = headers.get if hasattr(headers, "get") else (lambda k, d=None: None)
    host = get("Host") or ""
    if not host_ok(host):
        return False, "Host 不是回环地址（防 DNS rebinding）：%s" % host, 403

    if token:
        got = get("X-DC-Token") or ""
        if got != token:
            return False, "缺少或错误的 X-DC-Token", 401
    return True, "", 200


def check_post(headers, token: str = "") -> tuple[bool, str, int]:
    """写操作额外校验 Origin/Referer。"""
    ok, why, code = check_request(headers, token)
    if not ok:
        return ok, why, code
    get = headers.get if hasattr(headers, "get") else (lambda k, d=None: None)
    origin = get("Origin") or get("Referer") or ""
    if not origin_ok(origin):
        return False, "跨源写入被拒（Origin=%s）" % origin, 403
    return True, "", 200


def bind_allowed(host: str, token: str) -> tuple[bool, str]:
    """绑定非回环地址时必须设 token：局域网/隧道暴露时不能没有凭据。"""
    if host in LOOPBACK_NAMES or host_ok(host):
        return True, ""
    if token:
        return True, ""
    return False, ("拒绝在 %s 上无凭据启动：非回环绑定必须设置 DC_CONSOLE_TOKEN，"
                   "否则同网段任何人都能改配置、跑流水线" % host)


def token_from_env() -> str:
    return os.environ.get("DC_CONSOLE_TOKEN", "").strip()
