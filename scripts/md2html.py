"""md2html.py — 把计划书 Markdown 转成可嵌进控制台的 HTML。

为什么不装 `markdown` 库：这个项目一直坚持"零第三方依赖也能跑"，而计划书用到的
语法只有六种（标题 / 表格 / 围栏代码 / 列表 / 引用 / 行内粗体与代码）。自己写 120 行
比多一个依赖更容易长期维护，也避免版本升级改渲染。

用法：
  python scripts/md2html.py                       # docs/DC_MASTER_PLAN.md -> docs/app/api/plan.html
  python scripts/md2html.py --src X.md --out Y.html
  python scripts/md2html.py --stdout              # 打到标准输出（调试用）
"""
from __future__ import annotations

import argparse
import html
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SRC = ROOT / "docs" / "DC_MASTER_PLAN.md"
DEFAULT_OUT = ROOT / "docs" / "app" / "api" / "plan.html"

CSS = """
:root { color-scheme: dark; }
* { box-sizing: border-box; }
body { margin:0; padding:28px 34px 80px; background:#0d1219; color:#e6edf7;
       font:14.5px/1.75 system-ui,"Microsoft YaHei",sans-serif; }
.doc { max-width: 980px; margin: 0 auto; }
h1,h2,h3,h4 { line-height:1.35; margin:1.9em 0 .55em; color:#f4f8ff; }
h1 { font-size:26px; border-bottom:1px solid #24314a; padding-bottom:.35em; }
h2 { font-size:21px; border-bottom:1px solid #1c2739; padding-bottom:.25em; }
h3 { font-size:17px; color:#9fd8f5; }
h4 { font-size:15px; color:#b9c8de; }
p { margin:.7em 0; }
a { color:#4dc9f6; text-decoration:none; }
a:hover { text-decoration:underline; }
code { background:#182233; border:1px solid #24314a; border-radius:5px;
       padding:1px 5px; font-family:ui-monospace,Consolas,monospace; font-size:.92em; }
pre { background:#0b1119; border:1px solid #223047; border-radius:9px;
      padding:12px 14px; overflow:auto; }
pre code { background:none; border:0; padding:0; }
table { border-collapse:collapse; width:100%; margin:1em 0; font-size:13.5px; }
th,td { border:1px solid #223047; padding:7px 9px; vertical-align:top; text-align:left; }
th { background:#152032; color:#dfe9f7; font-weight:600; }
tr:nth-child(even) td { background:#101825; }
blockquote { margin:1em 0; padding:.4em 1em; border-left:3px solid #4dc9f6;
             background:#101b28; color:#b9c8de; border-radius:0 8px 8px 0; }
blockquote p { margin:.3em 0; }
hr { border:0; border-top:1px solid #1e2a3d; margin:2em 0; }
ul,ol { padding-left:1.4em; margin:.6em 0; }
li { margin:.25em 0; }
"""


def _inline(text: str) -> str:
    """行内语法：先转义，再处理代码/粗体/链接。顺序很重要——否则 `a*b*c` 会被啃。"""
    out = html.escape(text, quote=False)
    out = re.sub(r"`([^`]+)`", r"<code>\1</code>", out)
    out = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", out)
    out = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", r'<a href="\2" target="_blank" rel="noopener">\1</a>', out)
    return out


def _table(rows: list[str]) -> str:
    body = []
    header = [c.strip() for c in rows[0].strip().strip("|").split("|")]
    body.append("<thead><tr>" + "".join("<th>%s</th>" % _inline(c) for c in header) + "</tr></thead>")
    trs = []
    for line in rows[2:]:
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        trs.append("<tr>" + "".join("<td>%s</td>" % _inline(c) for c in cells) + "</tr>")
    body.append("<tbody>" + "".join(trs) + "</tbody>")
    return "<table>" + "".join(body) + "</table>"


def convert(md: str) -> str:
    lines = md.split("\n")
    out: list[str] = []
    i, n = 0, len(lines)
    para: list[str] = []
    in_code = False
    code: list[str] = []
    list_kind = None          # 'ul' / 'ol'

    def flush_para():
        if para:
            out.append("<p>" + _inline(" ".join(x.strip() for x in para)) + "</p>")
            para.clear()

    def close_list():
        nonlocal list_kind
        if list_kind:
            out.append("</%s>" % list_kind)
            list_kind = None

    while i < n:
        line = lines[i]
        stripped = line.strip()

        if stripped.startswith("```"):
            if in_code:
                out.append("<pre><code>" + html.escape("\n".join(code)) + "</code></pre>")
                code.clear()
                in_code = False
            else:
                flush_para()
                close_list()
                in_code = True
            i += 1
            continue
        if in_code:
            code.append(line)
            i += 1
            continue

        if not stripped:
            flush_para()
            close_list()
            i += 1
            continue

        m = re.match(r"^(#{1,4})\s+(.*)$", stripped)
        if m:
            flush_para()
            close_list()
            lvl = len(m.group(1))
            text = _inline(m.group(2).strip())
            out.append("<h%d>%s</h%d>" % (lvl, text, lvl))
            i += 1
            continue

        if stripped.startswith("|") and i + 1 < n and re.match(r"^\|[\s:\-|]+\|?$", lines[i + 1].strip()):
            flush_para()
            close_list()
            rows = []
            while i < n and lines[i].strip().startswith("|"):
                rows.append(lines[i])
                i += 1
            if len(rows) >= 2:
                out.append(_table(rows))
            continue

        if stripped.startswith(">"):
            flush_para()
            close_list()
            block = []
            while i < n and lines[i].strip().startswith(">"):
                block.append(lines[i].strip().lstrip(">").strip())
                i += 1
            out.append("<blockquote>" + "".join("<p>%s</p>" % _inline(b) for b in block if b) + "</blockquote>")
            continue

        if re.match(r"^[-*]\s+", stripped):
            flush_para()
            if list_kind != "ul":
                close_list()
                out.append("<ul>")
                list_kind = "ul"
            out.append("<li>" + _inline(re.sub(r"^[-*]\s+", "", stripped)) + "</li>")
            i += 1
            continue

        if re.match(r"^\d+\.\s+", stripped):
            flush_para()
            if list_kind != "ol":
                close_list()
                out.append("<ol>")
                list_kind = "ol"
            out.append("<li>" + _inline(re.sub(r"^\d+\.\s+", "", stripped)) + "</li>")
            i += 1
            continue

        if re.match(r"^---+$", stripped):
            flush_para()
            close_list()
            out.append("<hr>")
            i += 1
            continue

        para.append(line)
        i += 1

    if in_code and code:
        out.append("<pre><code>" + html.escape("\n".join(code)) + "</code></pre>")
    flush_para()
    close_list()
    return ('<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">'
            "<title>配送中心数字孪生 · 计划书</title><style>%s</style></head>"
            '<body><div class="doc">%s</div></body></html>' % (CSS, "\n".join(out)))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="计划书 Markdown → HTML")
    ap.add_argument("--src", default=str(DEFAULT_SRC))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--stdout", action="store_true")
    a = ap.parse_args(argv)
    src = Path(a.src)
    if not src.exists():
        print("缺 %s" % src, file=sys.stderr)
        return 2
    text = convert(src.read_text(encoding="utf-8"))
    if a.stdout:
        sys.stdout.write(text)
        return 0
    outp = Path(a.out)
    outp.parent.mkdir(parents=True, exist_ok=True)
    with open(outp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    print("PLAN_HTML_OK %s %.0fKB" % (outp, len(text) / 1024))
    return 0


if __name__ == "__main__":
    sys.exit(main())
