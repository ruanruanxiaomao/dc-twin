# -*- coding: utf-8 -*-
"""测试里跑子进程的统一入口。

## 为什么需要它

`subprocess.run(..., text=True)` 不带 `encoding` 时，父进程用**本机 locale** 解码；
子进程的 stdout 编码又由它自己的环境决定。两者不一致就会炸——而且炸法很隐蔽：
pytest 的 reader 线程里抛 `UnicodeDecodeError: 'gbk' codec can't decode ...`，
`stdout` 变成 `None`，最后报出来是 `json.loads(None)` 的 TypeError，
看起来像"脚本挂了"，其实脚本 40/40 全过。

这类 bug 只在特定机器（非 UTF-8 locale）上出现，属于最恶心的一类：
**本机能过、CI 能过、别人机器上红**。所以两边都钉死：

- 子进程：`PYTHONUTF8=1` + `PYTHONIOENCODING=utf-8`，不管宿主 locale；
- 父进程：`encoding="utf-8"` + `errors="replace"`，解码失败也不许把 stdout 变成 None。

`errors="replace"` 是刻意的：宁可看见一个乱码字符，也不要因为一个字节让整个
测试抛出与真实原因无关的异常。
"""
from __future__ import annotations

import os
import subprocess
import sys


def utf8_env(extra=None):
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    if extra:
        env.update(extra)
    return env


def run(args, **kw):
    """跑一个子进程，两边都按 UTF-8 对齐。"""
    kw.setdefault("capture_output", True)
    kw.setdefault("encoding", "utf-8")
    kw.setdefault("errors", "replace")
    kw.pop("text", None)          # encoding 已经隐含 text=True
    kw.setdefault("env", utf8_env())
    return subprocess.run([sys.executable] + list(args), **kw)
