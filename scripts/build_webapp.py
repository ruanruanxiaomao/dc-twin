"""build_webapp.py — 把本地控制台导出成**可静态部署**的 3D 网页版。

为什么要这一步：`console/server.py` 提供的是 `/api/bootstrap`、`/api/paths` 这些
动态接口，GitHub Pages 只会发文件，不会跑 Python。所以把接口的返回值**冻结成
JSON**，再用一个 90 行的 fetch 垫片把前端请求重定向到这些文件上。

产物：`docs/app/`（可直接放 Pages / 任何静态托管）
  ├─ index.html  app.js  ops.js  style.css  viewer3d.js
  ├─ vendor/            three.js r1xx + GLTFLoader + OrbitControls
  ├─ snapshot.js        fetch 垫片 + "静态快照"横幅
  ├─ out/dc_preview.glb 3D 模型（含 21 个功能区色块与中文名称牌）
  ├─ api/*.json         冻结的接口返回值
  └─ manifest.json      每个文件的 sha256（可核对有没有被手改）

用法：
  python scripts/build_webapp.py
  python scripts/build_webapp.py --out docs/app
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "console" / "static"
DEFAULT_OUT = ROOT / "docs" / "app"

# 接口 -> 冻结文件名。顺序无关，但每个都要能被垫片找到。
API_FILES = {
    "/api/bootstrap": "bootstrap.json",
    "/api/paths": "paths.json",
    "/api/status": "status.json",
    "/api/scenarios": "scenarios.json",
    "/api/pnl": "pnl.json",
    "/api/ledger": "ledger.json",
    "/api/schema": "schema.json",
    "/api/cost-spec": "cost-spec.json",
    "/api/scenario/template": "scenario/template.json",
}

SHIM = r"""/* 静态快照垫片：把 /api/* 与 /out/* 重定向到同目录下的冻结文件。
 * 只读——所有 POST（重跑、保存配置、写场景）都会被拒绝并给出提示，
 * 因为这些操作需要真实的 Python 服务端，而不是一份快照。
 */
(() => {
  const API = %(api_map)s;
  const realFetch = window.fetch.bind(window);
  const json = (obj, status = 200) =>
    Promise.resolve(new Response(JSON.stringify(obj), {
      status, headers: { 'Content-Type': 'application/json' },
    }));

  window.DC_SNAPSHOT = true;

  window.fetch = (input, init = {}) => {
    // three 的 FileLoader 会传 Request 对象，String(request) 是 '[object Request]'，
    // 直接截字符串拿不到 pathname —— 必须用 URL 解析（踩过一次）。
    const raw = input && input.url ? input.url : String(input);
    let u;
    try { u = new URL(raw, location.href); } catch (_) { return realFetch(input, init); }
    const path = u.pathname;
    const url = path + u.search;
    const method = (init.method || 'GET').toUpperCase();

    if (path.startsWith('/api/')) {
      if (method !== 'GET') {
        return json({ ok: false, static: true,
                      error: '这是静态快照，重跑/保存类操作不可用。请本地运行 python console/server.py' });
      }
      const file = API[path];
      if (file) return realFetch('./api/' + file, { cache: 'no-store' });
      // 轮询类接口：静态快照里永远"空闲"，让页面别转圈
      if (path === '/api/job') return json({ status: 'idle', lines: [], next: 0 });
      if (path === '/api/scenario/get')
        return json({ ok: false, static: true, error: '静态快照不含可编辑场景' });
      return json({ ok: false, static: true, error: 'no snapshot for ' + path });
    }
    if (path.startsWith('/out/')) return realFetch('.' + url, { cache: 'no-store' });
    if (path.startsWith('/data/')) return realFetch('.' + url, { cache: 'no-store' });
    if (path.startsWith('/static/')) return realFetch(url.replace('/static/', './'), init);
    return realFetch(input, init);
  };

  // 顶部横幅：让人一眼知道"这是快照，不是活的"
  const boot = () => {
    const bar = document.createElement('div');
    bar.textContent = '静态快照 · 只读 —— 3D/图表/KPI 来自一次真实运行；重跑与编辑请本地启动控制台';
    bar.style.cssText = [
      'position:fixed', 'left:0', 'right:0', 'top:0', 'z-index:9999',
      'padding:7px 14px', 'font:13px/1.4 system-ui,"Microsoft YaHei",sans-serif',
      'color:#0b1220', 'background:#facc15', 'text-align:center',
      'box-shadow:0 1px 6px rgba(15,23,42,.18)',
    ].join(';');
    document.body.appendChild(bar);
    document.body.style.paddingTop = '34px';
  };
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
"""

LANDING = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>配送中心数字孪生 · DC Twin</title>
<style>
  :root {{ color-scheme: dark; }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; min-height:100vh; background:#0a0e16; color:#eef3fb;
         font:15px/1.7 system-ui,"Microsoft YaHei",sans-serif; }}
  .wrap {{ max-width: 880px; margin: 0 auto; padding: 64px 24px 80px; }}
  h1 {{ font-size: 30px; margin: 0 0 6px; letter-spacing:.5px; }}
  .sub {{ color:#9babc4; margin-bottom: 34px; }}
  .cards {{ display:grid; gap:14px; grid-template-columns: repeat(auto-fit,minmax(240px,1fr)); }}
  a.card {{ display:block; padding:20px 22px; border-radius:14px; text-decoration:none;
            background:#111823; border:1px solid #202b3b; transition:.16s; }}
  a.card:hover {{ border-color:#4dc9f6; transform: translateY(-2px); }}
  a.card b {{ display:block; color:#eef3fb; font-size:16px; margin-bottom:4px; }}
  a.card span {{ color:#9babc4; font-size:13px; }}
  .meta {{ margin-top:36px; color:#697a93; font-size:12.5px; }}
  .meta code {{ color:#9babc4; }}
</style>
</head>
<body>
<div class="wrap">
  <h1>配送中心数字孪生</h1>
  <div class="sub">4 仓 · 441 库位 · 21 台 AMR · 21 个功能区 · 39 项实测 KPI</div>
  <div class="cards">
    <a class="card" href="./app/index.html">
      <b>3D 控制台 →</b>
      <span>three.js 实时渲染 .glb，可转视角、看 AGV 回放与功能区</span>
    </a>
    <a class="card" href="https://github.com/ruanruanxiaomao/dc-twin">
      <b>源代码 →</b>
      <span>Python 数据链 + Blender 场景 + 8 个可证伪守卫</span>
    </a>
    <a class="card" href="https://github.com/ruanruanxiaomao/dc-twin/blob/master/docs/DC_MASTER_PLAN.md">
      <b>计划书 →</b>
      <span>v3.8：自审核对表、字段字典、28 章实施计划</span>
    </a>
  </div>
  <div class="meta">
    快照构建于 <code>{stamp}</code> · 提交 <code>{commit}</code> · 模型 <code>{glb_kb} KB</code>
  </div>
</div>
</body>
</html>
"""


def write_text_lf(path: Path, text: str):
    """一律写 LF。

    为什么较真：git 提交时会把 CRLF 规范成 LF，如果 manifest 的 sha256 是在
    CRLF 工作副本上算的，克隆/CI 里必然对不上——W1 会全线报错。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def normalize_lf(root: Path):
    """把拷贝过来的前端文本文件统一成 LF（vendor 里的 .js 也要）。"""
    for p in root.rglob("*"):
        if not p.is_file() or p.suffix.lower() in (".glb", ".png", ".jpg", ".ico", ".webp"):
            continue
        b = p.read_bytes()
        if b"\r\n" in b:
            p.write_bytes(b.replace(b"\r\n", b"\n"))


def jdump(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    write_text_lf(path, json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n")
    return path.stat().st_size


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def copy_tree(src: Path, dst: Path):
    for p in src.rglob("*"):
        if p.is_dir():
            continue
        rel = p.relative_to(src)
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, target)


def rewrite_index(p: Path):
    """把绝对路径改成相对路径，并插入垫片脚本。"""
    s = p.read_text(encoding="utf-8")
    s = s.replace('"/static/vendor/', '"./vendor/')
    s = s.replace("'/static/vendor/", "'./vendor/")
    s = s.replace('"/static/', '"./')
    s = s.replace("'/static/", "'./")
    s = s.replace('<script src="./app.js"></script>',
                  '<script src="./snapshot.js"></script>\n<script src="./app.js"></script>')
    p.write_text(s, encoding="utf-8")
    return s


def rewrite_js(out: Path):
    """把 JS 里的 /static/ 绝对路径改成相对路径。

    注意 `await import('/static/viewer3d.js')` 走的是**模块加载器**，不经过 fetch，
    所以垫片拦不到它——必须在文件里改。
    """
    for name in ("app.js", "ops.js"):
        p = out / name
        s = p.read_text(encoding="utf-8")
        before = s
        s = s.replace("'/static/", "'./").replace('"/static/', '"./')
        if s != before:
            p.write_text(s, encoding="utf-8")


def build(out: Path) -> dict:
    sys.path.insert(0, str(ROOT / "console"))
    import server  # noqa: E402  (只在构建时用；不改它的任何状态)

    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    copy_tree(STATIC, out)
    rewrite_index(out / "index.html")
    rewrite_js(out)

    # 3D 模型
    glb_src = ROOT / "out" / "dc_preview.glb"
    if not glb_src.exists():
        raise SystemExit("缺 out/dc_preview.glb —— 先跑 blender -b -P scripts/export_glb.py")
    (out / "out").mkdir(parents=True, exist_ok=True)
    shutil.copy2(glb_src, out / "out" / "dc_preview.glb")

    # 冻结接口
    payloads = {
        "bootstrap": server.build_bootstrap(),
        "paths": server.build_paths_payload(),
        "scenarios": {"ok": True, "scenarios": server.scenario_tools.list_scenarios(),
                      "builtin_frozen": list(server.scenario_tools.BUILTIN)},
        "pnl": server.pnl_payload(None, False),
        "ledger": server.ledger_payload(None, None, 200),
        "schema": {"fields": server.CONFIG_SCHEMA,
                   "scopes": {k: {"label": v, "steps": len(server.SCOPES[k])}
                              for k, v in server.SCOPE_LABEL.items()}},
        "cost-spec": {"ok": True,
                      "spec": server._load(ROOT / "config" / "cost_spec.json"),
                      "editable": [f[0] for f in server.COST_FIELDS] + ["prices_source"]},
        "scenario/template": {"ok": True,
                              "template": server.scenario_tools.default_template("my_scenario")},
        "status": server.build_bootstrap()["reports"],
    }
    sizes = {}
    for key, obj in payloads.items():
        # key 可能是 "scenario/template" 这种带子目录的（接口 /api/scenario/template）
        sizes[key] = jdump(out / "api" / (key + ".json"), obj)

    write_text_lf(out / "snapshot.js", SHIM % {"api_map": json.dumps(API_FILES, indent=2)})

    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                            capture_output=True, text=True).stdout.strip() or "unknown"
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # 落地页（放在 docs/，Pages 的根）
    landing = ROOT / "docs" / "index.html"
    write_text_lf(landing, LANDING.format(stamp=stamp, commit=commit,
                                          glb_kb=round(glb_src.stat().st_size / 1024)))
    (ROOT / "docs" / ".nojekyll").write_text("", encoding="utf-8")

    normalize_lf(out)          # 必须在 sha256 之前
    files = {}
    for p in sorted(out.rglob("*")):
        if p.is_file() and p.name != "manifest.json":
            files[str(p.relative_to(out)).replace("\\", "/")] = {
                "bytes": p.stat().st_size, "sha256": sha256(p)}
    manifest = {
        "schema_version": "1.0",
        "kind": "webapp_snapshot",
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "commit": commit,
        "glb": "out/dc_preview.glb",
        "api": sizes,
        "files": files,
        "counts": {"files": len(files),
                   "bytes": sum(f["bytes"] for f in files.values())},
        "note": "静态快照：/api/* 已冻结为 api/*.json，POST 一律拒绝",
    }
    write_text_lf(out / "manifest.json",
                  json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return manifest


def main(argv=None):
    ap = argparse.ArgumentParser(description="导出可静态部署的 3D 网页版")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    a = ap.parse_args(argv)
    m = build(Path(a.out).resolve())
    print("WEBAPP_OK %s files=%d bytes=%.1fMB commit=%s"
          % (a.out, m["counts"]["files"], m["counts"]["bytes"] / 1048576, m["commit"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
