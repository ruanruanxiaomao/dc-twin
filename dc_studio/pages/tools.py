# -*- coding: utf-8 -*-
"""Toolbox: run the data chain, open artefacts, stream logs."""

from __future__ import annotations

import os
import platform
import socket
import subprocess
import sys
import threading
import time
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import ttk

from .. import theme as T
from ..data import open_path
from ..widgets import Card, Chip, PillButton

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


class ToolsPage(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=T.BG)
        self.app = app
        self.store = app.store
        self.proc: subprocess.Popen | None = None
        self.busy = False
        self._server_started = False

        head = tk.Frame(self, bg=T.BG)
        head.pack(fill="x", padx=18, pady=(12, 8))
        tk.Label(head, text="常用命令与产物入口", bg=T.BG, fg=T.MUTED, font=T.font(9)).pack(side="left")
        Chip(head, "本地执行", fg=T.GREEN).pack(side="left", padx=10)
        Chip(head, "不联网", fg=T.ACCENT).pack(side="left")
        self.state = tk.Label(head, text="空闲", bg=T.BG, fg=T.DIM, font=T.font(8))
        self.state.pack(side="right")

        body = tk.Frame(self, bg=T.BG)
        body.pack(fill="both", expand=True, padx=18, pady=(0, 14))
        body.grid_columnconfigure(0, weight=5)
        body.grid_columnconfigure(1, weight=6)
        body.grid_rowconfigure(0, weight=1)

        left = tk.Frame(body, bg=T.BG)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))

        actions = Card(left, "流水线", "全部命令在项目根目录执行", accent=T.ACCENT)
        actions.pack(fill="x")
        rows = [
            ("数据链重建", "layout → 库位 → 路径 → 任务（约 10 s，不启动 Blender）",
             [sys.executable, "run.py", "--scope", "data"], T.ACCENT),
            ("场景链", "在数据链基础上跑 8 个场景 + KPI + 账本（约 30 s）",
             [sys.executable, "run.py", "--scope", "scenarios"], T.MAGENTA),
            ("状态报告", "重新生成 out/CURRENT_STATUS.md 与验收汇总",
             [sys.executable, "scripts/report_status.py"], T.GREEN),
            ("文档防漂移", "比对状态文档↔产物↔计划书副本；答辩前必跑（<1 s）",
             [sys.executable, "scripts/check_doc_drift.py"], T.PURPLE),
            ("过程校验", "工位产能/服务时间/流程图连通性（L14–L22，<1 s）",
             [sys.executable, "scripts/check_process.py"], T.MAGENTA),
            ("生成产物包", "跑场景链并把产物装进 artifacts/DC_<时间>_<标签>/（约 1 min）",
             [sys.executable, "scripts/run_bundle.py", "--scope", "scenarios",
              "--label", "from_studio"], T.AMBER),
            ("功能区菜单", "命令行版菜单：14 个模板与预设（图形版见左侧『功能区』页）",
             [sys.executable, "scripts/build_variant.py", "--list"], T.ACCENT),
            ("生成扩仓变体", "按当前勾选生成 100x88 变体并校验（等价于『功能区』页的生成按钮）",
             [sys.executable, "scripts/build_variant.py", "--build"], T.MAGENTA),
            ("用变体跑一遍", "扩仓变体 + 产物包（把结果放进 artifacts/，不动主链）",
             [sys.executable, "scripts/run_bundle.py", "--scope", "scenarios",
              "--spec", "config/layout_spec_areas.json", "--label", "variant_from_studio"],
             T.PURPLE),
            ("驾驶舱守卫", "勾选页 ↔ 生成器 ↔ 变体 spec 三方一致（防界面与产物脱节）",
             [sys.executable, "scripts/check_studio.py"], T.BLUE),
            ("工位排队验证", "并行位 4→1：必须出现排队且吞吐下降（约 15 s）",
             [sys.executable, "scripts/check_station.py"], T.RED),
            ("死锁退让验证", "关闭退让必须更差：吞吐与受阻时长对照（约 40 s）",
             [sys.executable, "scripts/check_deadlock.py"], T.RED),
            ("运行测试", "pytest 快速回归（不含 Blender 用例；需先装 requirements-dev.txt）",
             [sys.executable, "-m", "pytest", "-q", "-m", "not blender"], T.AMBER),
        ]
        for title, desc, cmd, accent in rows:
            self._action(actions.body, title, desc, cmd, accent)

        launch = Card(left, "界面入口", "", accent=T.PURPLE)
        launch.pack(fill="x", pady=(10, 0))
        links = [
            ("3D 网页版（three.js）", "实时转视角 · AGV 回放 · 功能区；打不开时自动退到静态快照",
             self._open_console, T.ACCENT),
            ("2D 布局编辑器", "submission/editor.js 所在作品页", self._open_editor, T.PURPLE),
            ("静态 Demo", "demo/index.html", self._open_demo, T.GREEN),
            ("Blender 场景", "out/distribution_center.blend", self._open_blend, T.AMBER),
            ("GLB 预览", "out/dc_preview.glb", self._open_glb, T.MUTED),
        ]
        for title, desc, cmd, accent in links:
            self._link(launch.body, title, desc, cmd, accent)

        right = tk.Frame(body, bg=T.BG)
        right.grid(row=0, column=1, sticky="nsew")
        right.grid_rowconfigure(1, weight=1)
        right.grid_columnconfigure(0, weight=1)

        info = Card(right, "环境", "", accent=T.BLUE)
        info.grid(row=0, column=0, sticky="ew")
        blender = Path(r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe")
        has_pil = "可用" if _has_pil() else "缺失"
        counts = self.store.counts
        lines = [
            ("Python", sys.version.split()[0], "3.10+"),
            ("平台", platform.system() + " " + platform.release(), platform.machine()),
            ("Pillow", has_pil, "缩略图"),
            ("Blender", "已安装" if blender.exists() else "未找到", "5.2 LTS"),
            ("数据规模", f"{counts.get('racks', 0)} 货架 / {counts.get('locations', 0)} 库位",
             f"{counts.get('robots', 0)} 台 AGV"),
            ("产物时间", self.store.freshness(), "sim_index.json"),
        ]
        grid = tk.Frame(info.body, bg=T.PANEL)
        grid.pack(fill="x")
        for c in range(2):
            grid.grid_columnconfigure(c, weight=1, uniform="e")
        for i, (key, value, note) in enumerate(lines):
            cell = tk.Frame(grid, bg=T.PANEL_ALT, highlightbackground=T.BORDER, highlightthickness=1)
            cell.grid(row=i // 2, column=i % 2, sticky="nsew", padx=3, pady=3)
            tk.Label(cell, text=key, bg=T.PANEL_ALT, fg=T.DIM, font=T.font(8)).pack(anchor="w", padx=8, pady=(5, 0))
            tk.Label(cell, text=value, bg=T.PANEL_ALT, fg=T.TEXT, font=T.font(9, "bold")).pack(anchor="w", padx=8)
            tk.Label(cell, text=note, bg=T.PANEL_ALT, fg=T.DIM, font=T.font(7)).pack(anchor="w", padx=8, pady=(0, 5))

        log_card = Card(right, "输出", "实时 stdout / stderr", accent=T.GREEN)
        log_card.grid(row=1, column=0, sticky="nsew", pady=(10, 0))
        self.log = tk.Text(log_card.body, bg="#05090f", fg="#a9e6cf", insertbackground="#a9e6cf",
                           relief="flat", bd=0, font=T.mono(8), wrap="word", padx=10, pady=8)
        vsb = ttk.Scrollbar(log_card.body, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=vsb.set, state="disabled")
        self.log.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self._append("$ 就绪。选择一个动作开始；输出会实时出现在这里。\n")

    # ------------------------------------------------------------ rows
    def _action(self, parent, title: str, desc: str, cmd: list[str], accent: str):
        row = tk.Frame(parent, bg=T.PANEL_ALT, highlightbackground=T.BORDER, highlightthickness=1)
        row.pack(fill="x", pady=4)
        text = tk.Frame(row, bg=T.PANEL_ALT)
        text.pack(side="left", fill="x", expand=True, padx=10, pady=8)
        tk.Label(text, text=title, bg=T.PANEL_ALT, fg=T.TEXT, font=T.font(10, "bold"), anchor="w").pack(anchor="w")
        tk.Label(text, text=desc, bg=T.PANEL_ALT, fg=T.DIM, font=T.font(8), anchor="w").pack(anchor="w")
        PillButton(row, "运行", lambda: self._run(title, cmd), fg=accent, pad=(14, 6)).pack(side="right", padx=10)

    def _link(self, parent, title: str, desc: str, cmd, accent: str):
        row = tk.Frame(parent, bg=T.PANEL)
        row.pack(fill="x", pady=3)
        dot = tk.Canvas(row, bg=T.PANEL, width=8, height=8, highlightthickness=0)
        dot.pack(side="left", padx=(0, 6))
        dot.create_oval(1, 1, 7, 7, fill=accent, outline="")
        tk.Label(row, text=title, bg=T.PANEL, fg=T.TEXT, font=T.font(9), width=14,
                 anchor="w").pack(side="left")
        tk.Label(row, text=desc, bg=T.PANEL, fg=T.DIM, font=T.font(8), anchor="w").pack(side="left", fill="x", expand=True)
        PillButton(row, "打开", cmd, fg=accent, pad=(10, 3)).pack(side="right")

    # ------------------------------------------------------------ runner
    def _append(self, text: str):
        self.log.configure(state="normal")
        self.log.insert("end", text)
        self.log.see("end")
        self.log.configure(state="disabled")

    def _run(self, title: str, cmd: list[str]):
        if self.busy:
            self._append("$ 已有任务在运行，请稍候。\n")
            return
        self.busy = True
        self.state.configure(text=f"运行中：{title}", fg=T.AMBER)
        self._append(f"\n$ {' '.join(cmd)}\n")
        threading.Thread(target=self._worker, args=(cmd,), daemon=True).start()

    def _worker(self, cmd: list[str]):
        t0 = time.time()
        try:
            self.proc = subprocess.Popen(
                cmd, cwd=str(self.store.root), stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                creationflags=CREATE_NO_WINDOW,
            )
            assert self.proc.stdout is not None
            for line in self.proc.stdout:
                self.after(0, self._append, line)
            code = self.proc.wait()
            elapsed = time.time() - t0
            self.after(0, self._append, f"$ 退出码 {code} · {elapsed:.1f}s\n")
            self.after(0, self._done, code == 0)
        except Exception as exc:  # pragma: no cover - environment dependent
            self.after(0, self._append, f"$ 启动失败：{exc}\n")
            self.after(0, self._done, False)

    def _done(self, ok: bool):
        self.busy = False
        self.proc = None
        self.state.configure(text="空闲" if ok else "上一次任务失败", fg=T.GREEN if ok else T.RED)

    # ------------------------------------------------------------ launchers
    @staticmethod
    def _port_up(port: int, host: str = "127.0.0.1") -> bool:
        with socket.socket() as s:
            s.settimeout(0.2)
            return s.connect_ex((host, port)) == 0

    def _open_console(self):
        """打开 3D 网页版。

        优先完整控制台（8765，能重跑/编辑）；它起不来（缺依赖、端口被占、
        `-m console.server` 导入失败等）就退回静态快照（8899，只读但要什么有什么）。
        以前这里只 Popen 不检查，失败时浏览器打开一片空白，界面上完全看不出原因。
        """
        def open_when_ready(url: str, tries: int = 18):
            if self._port_up(int(url.rsplit(":", 1)[1].split("/")[0])):
                self._append(f"$ 打开 {url}\n")
                webbrowser.open(url)
                return
            if tries <= 0:
                self._append("$ 控制台未就绪，改开静态快照\n")
                self._open_snapshot()
                return
            self.after(250, lambda: open_when_ready(url, tries - 1))

        if self._port_up(8765):
            open_when_ready("http://127.0.0.1:8765/?view=3d", 1)
            return
        if not self._server_started:
            self._server_started = True
            self._append("\n$ 启动本地控制台 http://127.0.0.1:8765/?view=3d\n")
            try:
                subprocess.Popen([sys.executable, "-m", "console.server"], cwd=str(self.store.root),
                                 creationflags=CREATE_NO_WINDOW,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception as exc:
                self._append(f"$ 启动失败：{exc}\n")
        open_when_ready("http://127.0.0.1:8765/?view=3d")

    def _open_snapshot(self):
        """静态快照（docs/app）：不依赖控制台服务，只读但能看 3D。"""
        if not self._port_up(8899):
            try:
                subprocess.Popen([sys.executable, "scripts/serve_webapp.py",
                                  "--no-open", "--port", "8899"],
                                 cwd=str(self.store.root), creationflags=CREATE_NO_WINDOW,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception as exc:
                self._append(f"$ 静态快照启动失败：{exc}\n")
                return
        self.after(1500, lambda: webbrowser.open(
            "http://127.0.0.1:8899/app/index.html?view=3d"))

    def _open_editor(self):
        open_path(self.store.root / "submission" / "index.html")

    def _open_demo(self):
        open_path(self.store.root / "demo" / "index.html")

    def _open_blend(self):
        open_path(self.store.out_dir / "distribution_center.blend")

    def _open_glb(self):
        open_path(self.store.out_dir / "dc_preview.glb")


def _has_pil() -> bool:
    try:
        import PIL  # noqa: F401
        return True
    except Exception:
        return False
