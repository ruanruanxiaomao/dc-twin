# -*- coding: utf-8 -*-
"""控制台「项目文档」面板的数据源。

这条守的是"页签长期撒谎"这一类问题：历史方案（`docs/DC_MASTER_PLAN.md`）被删除后，
面板仍指着它，页面变成一句"计划书尚未生成"，而打包自检只报"0 KB"——没有人知道
是文档没了，还是打包坏了。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import console.server as server  # noqa: E402


def test_plan_source_exists():
    src = server.plan_source()
    assert src is not None, "文档面板没有任何可用来源：%s" % (server.PLAN_SOURCES,)
    assert src.exists()


def test_plan_html_is_a_real_document():
    """打包自检要求 >50 KB 且含 h2；这里提前把它钉住，别等 exe 才发现是空壳。"""
    html = server.build_plan_html()
    raw = html.encode("utf-8")
    assert len(raw) > 50_000, "文档面板只有 %d 字节，像是转换失败的残片" % len(raw)
    assert "<h2>" in html
    assert "来源：" in html, "页首必须标出文档来源，否则读者不知道自己在看什么"


def test_plan_sources_order_is_explicit():
    """候选链顺序是人定的，不是随手写的：优先历史方案，其次项目报告。"""
    assert server.PLAN_SOURCES[0].endswith("DC_MASTER_PLAN.md")
    assert server.PLAN_SOURCES[-1].endswith("PROJECT_REPORT.md")
