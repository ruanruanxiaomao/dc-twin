# -*- coding: utf-8 -*-
"""Interactive 2D layout sandbox with AGV playback."""

from __future__ import annotations

import bisect
import math
import tkinter as tk
from tkinter import ttk

from .. import theme as T
from ..data import open_path
from ..widgets import PillButton

LAYERS = [
    ("zones", "区域", T.BLUE),
    ("racks", "货架", T.PURPLE),
    ("locations", "库位", T.ACCENT),
    ("docks", "月台", T.AMBER),
    ("stations", "工位", T.MAGENTA),
    ("chargers", "充电", T.GREEN),
    ("conveyors", "输送", "#67e8f9"),
    ("obstacles", "障碍", T.DIM),
    ("trails", "轨迹", T.ACCENT),
    ("labels", "标签", T.MUTED),
]


class LayoutPage(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=T.BG)
        self.app = app
        self.store = app.store
        self.floor = "W"
        self.scale = 8.0
        self.ox = 0.0
        self.oy = 0.0
        self.layers = {key: True for key, _, _ in LAYERS}
        self.layers["labels"] = True
        self.t = 0.0
        self.playing = False
        self.speed = 2.0
        self._auto_fit = True
        self._drag = None
        self._meta: dict[int, str] = {}
        self._series = self.store.paths()
        self._times = {a: [p[0] for p in pts] for a, pts in self._series.items()}
        self._t_end = self.store.path_t_end
        self._phase = {}

        self._toolbar()
        self.canvas = tk.Canvas(self, bg=T.CANVAS_BG, highlightthickness=1,
                                highlightbackground=T.BORDER, bd=0, cursor="crosshair")
        self.canvas.pack(fill="both", expand=True, padx=16, pady=(0, 6))
        self._transport()
        self._bind_canvas()
        self.tip = tk.Label(self.canvas, text="", bg=T.PANEL_HI, fg=T.TEXT,
                            font=T.font(8), bd=1, relief="solid", padx=6, pady=3)

        self.canvas.bind("<Configure>", self._on_resize)
        self.after(45, self._tick)
        self.after(120, self._first_fit)

    # ------------------------------------------------------------ chrome
    def _toolbar(self):
        bar = tk.Frame(self, bg=T.BG)
        bar.pack(fill="x", padx=16, pady=(10, 8))
        left = tk.Frame(bar, bg=T.BG)
        left.pack(side="left")
        tk.Label(left, text="楼层", bg=T.BG, fg=T.DIM, font=T.font(8, "bold")).pack(side="left", padx=(0, 6))
        self._floor_btns = {}
        options = [("W", "主仓 W"), ("F1", "前置 F1"), ("F2", "前置 F2"),
                   ("F3", "前置 F3"), ("ALL", "全部")]
        for key, label in options:
            btn = PillButton(left, label, lambda k=key: self.set_floor(k), pad=(10, 5))
            btn.pack(side="left", padx=2)
            self._floor_btns[key] = btn
        self._floor_btns["W"].set_active(True)

        right = tk.Frame(bar, bg=T.BG)
        right.pack(side="right")
        PillButton(right, "适应窗口", self.fit_view, fg=T.ACCENT).pack(side="left", padx=4)
        PillButton(right, "打开全图", lambda: open_path(self.store.out_dir / "renders" / "01_overview.png"),
                   fg=T.MUTED).pack(side="left", padx=4)

        layers = tk.Frame(self, bg=T.BG)
        layers.pack(fill="x", padx=16, pady=(0, 8))
        tk.Label(layers, text="图层", bg=T.BG, fg=T.DIM, font=T.font(8, "bold")).pack(side="left", padx=(0, 6))
        self._layer_btns = {}
        for key, label, _color in LAYERS:
            btn = PillButton(layers, label, lambda k=key: self.toggle_layer(k), pad=(8, 3))
            btn.pack(side="left", padx=2)
            self._layer_btns[key] = btn

    def _transport(self):
        bar = tk.Frame(self, bg=T.PANEL, highlightbackground=T.BORDER, highlightthickness=1)
        bar.pack(fill="x", padx=16, pady=(0, 12))
        inner = tk.Frame(bar, bg=T.PANEL)
        inner.pack(fill="x", padx=12, pady=8)
        self.play_btn = PillButton(inner, "▶  播放", self.toggle_play, fg=T.GREEN, pad=(14, 6))
        self.play_btn.pack(side="left")
        self.time_lbl = tk.Label(inner, text="t = 0.0 s", bg=T.PANEL, fg=T.TEXT,
                                 font=(T.MONO_FAMILY, 9, "bold"), width=13)
        self.time_lbl.pack(side="left", padx=(12, 8))
        self.slider = ttk.Scale(inner, from_=0, to=max(self._t_end, 1), orient="horizontal",
                                command=self._on_slider)
        self.slider.pack(side="left", fill="x", expand=True, padx=6)
        self.speed_btns = {}
        for sp in (1.0, 2.0, 5.0):
            btn = PillButton(inner, f"{sp:.0f}×", lambda s=sp: self.set_speed(s), pad=(8, 4))
            btn.pack(side="left", padx=2)
            self.speed_btns[sp] = btn
        self.speed_btns[2.0].set_active(True)
        self.status_lbl = tk.Label(inner, text="21 台 AGV · 5140 帧", bg=T.PANEL, fg=T.DIM,
                                   font=T.font(8))
        self.status_lbl.pack(side="left", padx=(12, 0))

    # ------------------------------------------------------------ state
    def set_floor(self, key: str):
        if key == self.floor:
            return
        self.floor = key
        for k, btn in self._floor_btns.items():
            btn.set_active(k == key)
        self._auto_fit = True
        self.fit_view()

    def toggle_layer(self, key: str):
        self.layers[key] = not self.layers[key]
        self._layer_btns[key].set_active(self.layers[key])
        self.redraw_static()
        self._draw_agents()

    def toggle_play(self):
        self.playing = not self.playing
        self.play_btn.configure(text="⏸  暂停" if self.playing else "▶  播放",
                                fg=T.AMBER if self.playing else T.GREEN)

    def set_speed(self, value: float):
        self.speed = value
        for k, btn in self.speed_btns.items():
            btn.set_active(abs(k - value) < 1e-6)

    # ------------------------------------------------------------ view
    def _first_fit(self):
        self.fit_view()

    def _visible_warehouses(self) -> list[dict]:
        if self.floor == "ALL":
            return self.store.warehouses
        wh = self.store.warehouse(self.floor)
        return [wh] if wh else []

    def fit_view(self):
        whs = self._visible_warehouses()
        if not whs:
            return
        xs, ys = [], []
        for wh in whs:
            ox, oy = wh.get("origin", [0, 0])
            size = wh.get("size", {})
            xs += [ox, ox + size.get("width", 10)]
            ys += [oy, oy + size.get("depth", 10)]
        x0, x1 = min(xs) - 1.5, max(xs) + 1.5
        y0, y1 = min(ys) - 1.5, max(ys) + 1.5
        w = max(self.canvas.winfo_width(), 200)
        h = max(self.canvas.winfo_height(), 200)
        self.scale = min((w - 24) / max(x1 - x0, 1e-6), (h - 24) / max(y1 - y0, 1e-6))
        self.ox = (w - (x1 - x0) * self.scale) / 2 - x0 * self.scale
        self.oy = (h - (y1 - y0) * self.scale) / 2 - y0 * self.scale
        self._auto_fit = False
        self.redraw_static()
        self._draw_agents()

    def _on_resize(self, _event):
        if self._auto_fit:
            self.fit_view()

    def w2s(self, x: float, y: float) -> tuple[float, float]:
        return x * self.scale + self.ox, y * self.scale + self.oy

    # ------------------------------------------------------------ static drawing
    def redraw_static(self):
        c = self.canvas
        c.delete("static")
        self._meta = {}
        for wh in self._visible_warehouses():
            self._draw_warehouse(wh)
        c.tag_lower("static")

    def _tag(self, item: int, layer: str, meta: str | None = None):
        self.canvas.itemconfigure(item, tags=("static", f"layer:{layer}"))
        if meta:
            self._meta[item] = meta

    def _rect(self, x0, z0, x1, z1, **kw):
        a = self.w2s(x0, z0)
        b = self.w2s(x1, z1)
        return self.canvas.create_rectangle(a[0], a[1], b[0], b[1], **kw)

    def _draw_warehouse(self, wh: dict):
        c = self.canvas
        ox, oy = wh.get("origin", [0, 0])
        size = wh.get("size", {})
        x1, z1 = ox + size.get("width", 10), oy + size.get("depth", 10)
        base = self._rect(ox, oy, x1, z1, fill="#0a1320", outline=T.BORDER_HI, width=1)
        self._tag(base, "zones")

        # 10 m grid
        step = 10
        x = ox + step
        while x < x1:
            a = self.w2s(x, oy)
            b = self.w2s(x, z1)
            if self.layers["zones"]:
                self._tag(c.create_line(a[0], a[1], b[0], b[1], fill=T.GRID), "zones")
            x += step
        z = oy + step
        while z < z1:
            a = self.w2s(ox, z)
            b = self.w2s(x1, z)
            if self.layers["zones"]:
                self._tag(c.create_line(a[0], a[1], b[0], b[1], fill=T.GRID), "zones")
            z += step

        if self.layers["zones"]:
            for zone in wh.get("zones", []):
                pts = []
                for px, pz in zone.get("polygon", []):
                    sx, sy = self.w2s(px, pz)
                    pts += [sx, sy]
                if len(pts) >= 6:
                    color = zone.get("color") or T.BLUE
                    item = c.create_polygon(pts, fill=T.mix(color, T.BG, 0.82),
                                            outline=T.mix(color, T.BG, 0.25), width=1)
                    self._tag(item, "zones", f"区域 {zone.get('id')} · {zone.get('name', '')}")
                    if self.layers["labels"]:
                        cx = sum(pts[0::2]) / max(len(pts) // 2, 1)
                        cy = sum(pts[1::2]) / max(len(pts) // 2, 1)
                        self._tag(c.create_text(cx, cy, text=zone.get("id", ""),
                                                fill=T.mix(color, "#ffffff", 0.35),
                                                font=(T.FONT_FAMILY, 11, "bold")), "labels")

        if self.layers["conveyors"]:
            for cv in wh.get("conveyors", []):
                path = cv.get("path", [])
                if len(path) < 2:
                    continue
                pts = []
                for px, pz in path:
                    sx, sy = self.w2s(px, pz)
                    pts += [sx, sy]
                width = max(2.0, cv.get("width", 1.0) * self.scale * 0.7)
                item = c.create_line(pts, fill="#2c5a7a", width=width, capstyle="round")
                self._tag(item, "conveyors", f"输送线 {cv.get('id')} · {cv.get('speed_mps', 0)} m/s")

        if self.layers["racks"]:
            for rk in wh.get("racks", []):
                px, _py, pz = rk.get("position", [0, 0, 0])
                sx, sz = rk.get("size", [3, 6, 1])[0], rk.get("size", [3, 6, 1])[2]
                item = self._rect(px, pz, px + sx, pz + sz,
                                  fill="#17253c", outline="#26405f", width=1)
                self._tag(item, "racks",
                          f"货架 {rk.get('id')} · {rk.get('levels', 4)} 层 · 区域 {rk.get('zone')}")

        if self.layers["locations"] and self.scale >= 5.0:
            for loc in wh.get("locations", []):
                ap = loc.get("access_point")
                if not ap:
                    continue
                sx, sy = self.w2s(ap[0], ap[1])
                kind = loc.get("kind", "SHELF")
                color = {"SHELF": "#33587f", "CHARGING": T.GREEN, "HOME": T.DIM,
                         "INBOUND": T.BLUE, "OUTBOUND": T.AMBER}.get(kind, T.MUTED)
                r = 1.6 if kind == "SHELF" else 2.4
                self._tag(c.create_oval(sx - r, sy - r, sx + r, sy + r, fill=color, outline=""),
                          "locations", f"库位 {loc.get('id')} · {kind}")

        if self.layers["docks"]:
            for dk in wh.get("docks", []):
                rect = dk.get("rect")
                if not rect:
                    continue
                item = self._rect(rect[0], rect[1], rect[2], rect[3],
                                  fill="#10233c", outline=T.BLUE, width=1)
                self._tag(item, "docks",
                          f"月台 {dk.get('id')} · {dk.get('kind')} · {len(dk.get('doors', []))} 扇门")
                for door in dk.get("doors", []):
                    cxp = door.get("center", [0, 0])[0]
                    wdt = door.get("width", 3.0)
                    a = self.w2s(cxp - wdt / 2, rect[1])
                    b = self.w2s(cxp + wdt / 2, rect[1])
                    self._tag(c.create_line(a[0], a[1], b[0], b[1], fill=T.AMBER, width=2), "docks")

        if self.layers["stations"]:
            for st in wh.get("stations", []):
                rect = st.get("rect")
                if not rect:
                    continue
                item = self._rect(rect[0], rect[1], rect[2], rect[3],
                                  fill="#2a1c33", outline=T.MAGENTA, width=1)
                self._tag(item, "stations", f"工位 {st.get('id')} · {st.get('kind')}")
                if self.layers["labels"] and self.scale > 3:
                    a = self.w2s((rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2)
                    self._tag(c.create_text(a[0], a[1], text=st.get("kind", ""), fill=T.MAGENTA,
                                            font=T.font(7, "bold")), "labels")

        if self.layers["chargers"]:
            for ch in wh.get("charging_stations", []):
                px, _py, pz = ch.get("position", [0, 0, 0])
                item = self._rect(px - 0.8, pz - 0.8, px + 0.8, pz + 0.8,
                                  fill="#0e2b24", outline=T.GREEN, width=1)
                self._tag(item, "chargers", f"充电桩 {ch.get('id')} · {ch.get('power_kw', 0)} kW")

        if self.layers["obstacles"]:
            for ob in wh.get("obstacles", []):
                rect = ob.get("rect")
                if not rect:
                    continue
                item = self._rect(rect[0], rect[1], rect[2], rect[3],
                                  fill="#151c2b", outline=T.BORDER, width=1)
                self._tag(item, "obstacles", f"障碍 {ob.get('id')} · {ob.get('kind')}")

        if self.layers["labels"] and self.scale > 2:
            a = self.w2s(ox + 1.2, oy + 1.8)
            self._tag(c.create_text(a[0], a[1], text=wh.get("id", ""), anchor="nw",
                                    fill=T.DIM, font=(T.MONO_FAMILY, 10, "bold")), "labels")

    # ------------------------------------------------------------ agents
    def _agent_at(self, agent: str, t: float):
        pts = self._series.get(agent)
        if not pts:
            return None
        times = self._times[agent]
        idx = bisect.bisect_right(times, t) - 1
        if idx < 0:
            idx = 0
        return pts[idx]

    def _floor_of(self, agent: str) -> str:
        if agent.startswith("W-"):
            return "W"
        if agent.startswith("F1-"):
            return "F1"
        if agent.startswith("F2-"):
            return "F2"
        if agent.startswith("F3-"):
            return "F3"
        return "W"

    def _draw_agents(self):
        c = self.canvas
        c.delete("dyn")
        if not self.layers.get("robots", True):
            return
        for agent, pts in self._series.items():
            floor = self._floor_of(agent)
            if self.floor not in ("ALL", floor):
                continue
            cur = self._agent_at(agent, self.t)
            if not cur:
                continue
            _t, x, y, phase = cur
            sx, sy = self.w2s(x, y)
            color = T.PHASE_COLORS.get(phase, T.ACCENT)
            if self.layers["trails"]:
                start = max(0, bisect.bisect_right([p[0] for p in pts], self.t) - 70)
                trail = pts[start:bisect.bisect_right([p[0] for p in pts], self.t)]
                if len(trail) > 1:
                    coords = []
                    for _tt, tx, ty, _ph in trail:
                        a = self.w2s(tx, ty)
                        coords += [a[0], a[1]]
                    c.create_line(coords, fill=T.mix(color, T.CANVAS_BG, 0.55),
                                  width=1.2, tags=("dyn",))
            r = max(3.0, 0.55 * self.scale)
            item = c.create_oval(sx - r, sy - r, sx + r, sy + r, fill=color,
                                 outline="#ffffff", width=1, tags=("dyn", "agent"))
            c.create_text(sx, sy, text=agent.split("-")[-1].replace("R", ""),
                          fill="#04121c", font=T.font(6, "bold"), tags=("dyn",))
            if self.layers["labels"] and self.scale > 6:
                c.create_text(sx, sy - r - 7, text=agent, fill=color,
                              font=T.font(6, "bold"), tags=("dyn",))
            self._phase[agent] = phase

    def _tick(self):
        if self.playing and self._t_end > 0:
            self.t += 0.045 * self.speed
            if self.t > self._t_end:
                self.t = 0.0
            self.slider.set(self.t)
            self.time_lbl.configure(text=f"t = {self.t:6.1f} s")
            self._draw_agents()
        self.after(45, self._tick)

    def _on_slider(self, value):
        try:
            self.t = float(value)
        except Exception:
            return
        self.time_lbl.configure(text=f"t = {self.t:6.1f} s")
        self._draw_agents()

    # ------------------------------------------------------------ interaction
    def _bind_canvas(self):
        c = self.canvas
        c.bind("<MouseWheel>", self._zoom)
        c.bind("<ButtonPress-1>", self._pan_start)
        c.bind("<B1-Motion>", self._pan_move)
        c.bind("<ButtonRelease-1>", self._pan_end)
        c.bind("<Motion>", self._hover)
        c.bind("<Leave>", lambda _e: self.tip.place_forget())
        c.bind("<Double-Button-1>", lambda _e: self.fit_view())

    def _zoom(self, event):
        k = 1.15 if event.delta > 0 else 1 / 1.15
        if not (1.2 < self.scale * k < 160):
            return
        cx, cy = event.x, event.y
        self.canvas.scale("static", cx, cy, k, k)
        self.scale *= k
        self.ox = cx + (self.ox - cx) * k
        self.oy = cy + (self.oy - cy) * k
        self._draw_agents()

    def _pan_start(self, event):
        self._drag = (event.x, event.y)
        self.canvas.configure(cursor="fleur")
        self.tip.place_forget()

    def _pan_move(self, event):
        if not self._drag:
            return
        dx, dy = event.x - self._drag[0], event.y - self._drag[1]
        self._drag = (event.x, event.y)
        self.canvas.move("static", dx, dy)
        self.ox += dx
        self.oy += dy
        self._draw_agents()

    def _pan_end(self, _event):
        self._drag = None
        self.canvas.configure(cursor="crosshair")

    def _hover(self, event):
        if self._drag:
            return
        item = self.canvas.find_closest(event.x, event.y, halo=4)
        text = self._meta.get(item[0]) if item else None
        if not text:
            self.tip.place_forget()
            return
        self.tip.configure(text=text)
        self.tip.place(x=event.x + 14, y=event.y + 12)
        self.tip.lift()
