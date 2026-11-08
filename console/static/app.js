/* 配送中心数字孪生 · 控制台前端
 * 数据源：/api/bootstrap（布局/任务/报告/产物）、/api/paths（按车关键帧）
 */

'use strict';

// ------------------------------------------------------------------ 常量

const PH_COLOR = {
  MOVE: '#2563eb', ROTATE: '#7c3aed', FACE: '#0891b2',
  PICK: '#ea580c', DROP: '#16a34a', PARK: '#94a3b8',
};
const PH_CN = {
  MOVE: '行驶', ROTATE: '原地旋转', FACE: '转向作业面',
  PICK: '取货', DROP: '交付', PARK: '待命',
};
const PH_ORDER = ['MOVE', 'ROTATE', 'FACE', 'PICK', 'DROP', 'PARK'];

const C = { cv: null, ctx: null, w: 0, h: 0, dpr: 1 };

const S = {
  boot: null,
  paths: null,
  schema: null,
  byId: {},            // agent_id -> path series
  t: 0,
  tEnd: 0,
  playing: false,
  speed: 1,
  sel: null,
  dirty: true,
  mode3d: false,
  view: { scale: 1, ox: 0, oy: 0 },
  show: { racks: true, zones: true, heat: false, trail: true, route: true },
  drag: null,
  job: null,
  jobTimer: null,
  jobNext: 0,
  runScope: 'data',
  heat: {},
};

// ------------------------------------------------------------------ 工具

const $ = (id) => document.getElementById(id);
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const fmt = (v, n = 2) => (v === null || v === undefined || Number.isNaN(v)) ? '—' : Number(v).toFixed(n);
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

function el(tag, cls, html) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (html !== undefined) n.innerHTML = html;
  return n;
}

async function apiGet(path) {
  const r = await fetch(path, { cache: 'no-store' });
  if (!r.ok) throw new Error('HTTP ' + r.status + ' ' + path);
  return r.json();
}

async function apiPost(path, body) {
  const r = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  });
  let j = {};
  try { j = await r.json(); } catch (_) { /* ignore */ }
  if (!r.ok || j.ok === false) throw new Error(j.error || ('HTTP ' + r.status));
  return j;
}

// ------------------------------------------------------------------ 加载

async function loadAll() {
  const [boot, paths, schema] = await Promise.all([
    apiGet('/api/bootstrap'),
    apiGet('/api/paths'),
    apiGet('/api/schema').catch(() => ({ fields: [], scopes: {} })),
  ]);

  S.boot = boot;
  S.paths = paths;
  S.schema = schema;
  S.byId = {};
  for (const a of paths.agents) S.byId[a.id] = a;
  S.tEnd = paths.t_end || 0;
  S.t = clamp(S.t, 0, S.tEnd);
  S.heat = (boot.slotting && boot.slotting.heat_by_rack) || {};

  $('brandSub').textContent = boot.project;
  $('timeEnd').textContent = fmt(S.tEnd);

  renderChips();
  renderAgents();
  renderOrders();
  renderAudit();
  renderParams();
  renderArtifacts();
  renderLegend();
  renderMarks();
  buildScopeUI();
  fitView();
  if (!S.sel) S.sel = (boot.agents[0] && boot.agents[0].id) || null;
  renderDetail();
  syncTimeline();
  S.dirty = true;
}

async function reloadData(silent) {
  try {
    await loadAll();
    if (!silent) toast('已重新载入 data/ 与 out/ 的最新产物');
  } catch (e) {
    toast('重新载入失败：' + e.message, true);
  }
}

// ------------------------------------------------------------------ 顶栏

function renderChips() {
  const b = S.boot;
  const box = $('chips');
  box.innerHTML = '';

  const val = b.reports.validate && b.reports.validate.data;
  const items = Array.isArray(val) ? val : [];
  const pass = items.filter((x) => x.status === 'PASS').length;
  const allPass = items.length > 0 && pass === items.length;

  const tr = b.reports.traffic && b.reports.traffic.data;
  const m = b.metrics || {};

  const chips = [
    {
      k: '验收', v: items.length ? `${pass}/${items.length}` : '—',
      cls: items.length ? (allPass ? 'pass' : 'fail') : '',
    },
    { k: 'AGV', v: (b.agents || []).length },
    {
      k: '最小车距', v: tr && tr.min_pair_distance_m != null ? fmt(tr.min_pair_distance_m) + ' m' : '—',
      cls: tr && tr.min_pair_distance_m >= (tr.min_sep_threshold_m || 0) ? 'pass' : '',
    },
    { k: '仿真', v: fmt(m.duration_s) + ' s' },
    { k: '路径总长', v: fmt(m.total_path_m, 1) + ' m' },
    { k: '关键帧', v: m.samples ?? '—' },
    { k: '订单', v: `${m.tasks ?? 0} 张` },
  ];

  for (const c of chips) {
    box.appendChild(el('span', 'chip ' + (c.cls || ''),
      `<span class="k">${esc(c.k)}</span><b>${esc(c.v)}</b>`));
  }
}

function renderLegend() {
  const box = $('legend');
  box.innerHTML = '';
  for (const p of PH_ORDER) {
    box.appendChild(el('span', '', `<i style="background:${PH_COLOR[p]}"></i>${PH_CN[p]}`));
  }
}

function toast(msg, bad) {
  const n = el('div', 'toast' + (bad ? ' bad' : ''), esc(msg));
  document.body.appendChild(n);
  requestAnimationFrame(() => n.classList.add('on'));
  setTimeout(() => {
    n.classList.remove('on');
    setTimeout(() => n.remove(), 260);
  }, 2600);
}

// ------------------------------------------------------------------ 时间插值

function sampleAgent(a, t) {
  const ts = a.t;
  const n = ts.length;
  if (!n) return null;
  if (t <= ts[0]) return { x: a.x[0], y: a.y[0], th: a.th[0], ph: a.ph[0] };
  if (t >= ts[n - 1]) return { x: a.x[n - 1], y: a.y[n - 1], th: a.th[n - 1], ph: a.ph[n - 1] };

  let lo = 0, hi = n - 1;
  while (lo + 1 < hi) {
    const mid = (lo + hi) >> 1;
    if (ts[mid] <= t) lo = mid; else hi = mid;
  }
  const t0 = ts[lo], t1 = ts[hi];
  const u = t1 > t0 ? (t - t0) / (t1 - t0) : 0;

  let d = a.th[hi] - a.th[lo];
  while (d > Math.PI) d -= Math.PI * 2;
  while (d < -Math.PI) d += Math.PI * 2;

  return {
    x: a.x[lo] + (a.x[hi] - a.x[lo]) * u,
    y: a.y[lo] + (a.y[hi] - a.y[lo]) * u,
    th: a.th[lo] + d * u,
    ph: a.ph[lo],
  };
}

// ------------------------------------------------------------------ 画布

function X(x) { return S.view.ox + x * S.view.scale; }
function Y(y) { return S.view.oy + y * S.view.scale; }
function L(w) { return w * S.view.scale; }

function resizeCanvas() {
  const wrap = $('canvasWrap');
  C.cv = $('cv');
  C.ctx = C.cv.getContext('2d');
  C.dpr = window.devicePixelRatio || 1;
  C.w = wrap.clientWidth;
  C.h = wrap.clientHeight;
  C.cv.width = Math.round(C.w * C.dpr);
  C.cv.height = Math.round(C.h * C.dpr);
  C.cv.style.width = C.w + 'px';
  C.cv.style.height = C.h + 'px';
  S.dirty = true;
}

function fitView() {
  const size = (S.boot && S.boot.layout && S.boot.layout.size) || { width: 112, depth: 114 };
  const pad = 16;
  const sc = Math.min((C.w - pad * 2) / size.width, (C.h - pad * 2) / size.depth);
  S.view.scale = sc > 0 ? sc : 1;
  S.view.ox = (C.w - size.width * S.view.scale) / 2;
  S.view.oy = (C.h - size.depth * S.view.scale) / 2;
  S.dirty = true;
}

function roundRect(ctx, x, y, w, h, r) {
  const rr = Math.min(r, Math.abs(w) / 2, Math.abs(h) / 2);
  ctx.beginPath();
  ctx.moveTo(x + rr, y);
  ctx.arcTo(x + w, y, x + w, y + h, rr);
  ctx.arcTo(x + w, y + h, x, y + h, rr);
  ctx.arcTo(x, y + h, x, y, rr);
  ctx.arcTo(x, y, x + w, y, rr);
  ctx.closePath();
}

function heatColor(v) {
  // 0 → 冷蓝, 0.5 → 黄, 1 → 红
  const t = clamp(v, 0, 1);
  const stops = [[59, 130, 246], [34, 197, 94], [234, 179, 8], [249, 115, 22], [239, 68, 68]];
  const f = t * (stops.length - 1);
  const i = Math.min(stops.length - 2, Math.floor(f));
  const u = f - i;
  const a = stops[i], b = stops[i + 1];
  const c = a.map((x, k) => Math.round(x + (b[k] - x) * u));
  return `rgb(${c[0]},${c[1]},${c[2]})`;
}

function draw() {
  if (!C.ctx || !S.boot) return;
  const ctx = C.ctx;
  const lay = S.boot.layout;

  ctx.setTransform(C.dpr, 0, 0, C.dpr, 0, 0);
  ctx.clearRect(0, 0, C.w, C.h);

  // 地面网格（放大后才显示）
  if (S.view.scale > 3.2) {
    ctx.strokeStyle = '#e9edf3';
    ctx.lineWidth = 1;
    const step = 10;
    const x0 = Math.floor(((0 - S.view.ox) / S.view.scale) / step) * step;
    const y0 = Math.floor(((0 - S.view.oy) / S.view.scale) / step) * step;
    for (let x = x0; x <= 200; x += step) {
      const px = Math.round(X(x)) + 0.5;
      if (px < 0 || px > C.w) continue;
      ctx.beginPath(); ctx.moveTo(px, 0); ctx.lineTo(px, C.h); ctx.stroke();
    }
    for (let y = y0; y <= 200; y += step) {
      const py = Math.round(Y(y)) + 0.5;
      if (py < 0 || py > C.h) continue;
      ctx.beginPath(); ctx.moveTo(0, py); ctx.lineTo(C.w, py); ctx.stroke();
    }
  }

  const warehouses = lay.warehouses || [];

  // 1. 仓库底板
  for (const wh of warehouses) {
    const [ox, oz] = wh.origin;
    const w = wh.size.width, d = wh.size.depth;
    ctx.save();
    roundRect(ctx, X(ox), Y(oz), L(w), L(d), Math.max(3, L(1)));
    ctx.fillStyle = '#f8fafc';
    ctx.fill();
    ctx.strokeStyle = '#c3ccda';
    ctx.lineWidth = 1.4;
    ctx.stroke();
    ctx.restore();
  }

  // 2. 分区
  if (S.show.zones) {
    for (const wh of warehouses) {
      for (const z of wh.zones || []) {
        const poly = z.polygon;
        if (!poly || poly.length < 3) continue;
        ctx.beginPath();
        poly.forEach((p, i) => (i ? ctx.lineTo(X(p[0]), Y(p[1])) : ctx.moveTo(X(p[0]), Y(p[1]))));
        ctx.closePath();
        ctx.fillStyle = (z.color || '#64748b') + '12';
        ctx.fill();
      }
    }
  }

  // 3. 传送带
  for (const wh of warehouses) {
    for (const cv of wh.conveyors || []) {
      const p = cv.path;
      if (!p || p.length < 2) continue;
      ctx.beginPath();
      p.forEach((q, i) => (i ? ctx.lineTo(X(q[0]), Y(q[1])) : ctx.moveTo(X(q[0]), Y(q[1]))));
      ctx.strokeStyle = '#b6c0cf';
      ctx.lineWidth = Math.max(1.5, L(cv.width || 1));
      ctx.lineCap = 'round';
      ctx.stroke();
    }
  }

  // 4. 货架
  if (S.show.racks) {
    for (const wh of warehouses) {
      for (const rk of wh.racks || []) {
        const [rx, , rz] = rk.position;
        const [rl, rd] = rk.size;
        const x = X(rx), y = Y(rz), w = L(rl), h = L(rd);
        if (x + w < -20 || x > C.w + 20 || y + h < -20 || y > C.h + 20) continue;

        if (S.show.heat) {
          const hv = S.heat[rk.id];
          ctx.fillStyle = hv === undefined ? '#e8edf4' : heatColor(hv);
          ctx.globalAlpha = 0.72;
          ctx.fillRect(x, y, w, h);
          ctx.globalAlpha = 1;
          ctx.strokeStyle = 'rgba(0,0,0,.16)';
          ctx.lineWidth = 0.7;
          ctx.strokeRect(x, y, w, h);
        } else {
          ctx.fillStyle = '#fdeade';
          ctx.fillRect(x, y, w, h);
          ctx.strokeStyle = '#e2601f';
          ctx.lineWidth = Math.min(1.2, Math.max(0.5, L(0.06)));
          ctx.strokeRect(x, y, w, h);
        }
      }
    }
  }

  // 5. 障碍物
  for (const wh of warehouses) {
    for (const ob of wh.obstacles || []) {
      const [x0, z0, x1, z1] = ob.rect;
      ctx.fillStyle = '#cbd5e1';
      ctx.fillRect(X(x0), Y(z0), L(x1 - x0), L(z1 - z0));
    }
  }

  // 6. 月台
  for (const wh of warehouses) {
    for (const dk of wh.docks || []) {
      const [x0, z0, x1, z1] = dk.rect;
      const inbound = dk.kind === 'INBOUND';
      ctx.fillStyle = inbound ? '#dbeafe' : '#dcfce7';
      ctx.fillRect(X(x0), Y(z0), L(x1 - x0), L(z1 - z0));
      ctx.strokeStyle = inbound ? '#3b82f6' : '#16a34a';
      ctx.lineWidth = 0.9;
      ctx.strokeRect(X(x0), Y(z0), L(x1 - x0), L(z1 - z0));
    }
  }

  // 7. 作业站
  for (const wh of warehouses) {
    for (const st of wh.stations || []) {
      const [x0, z0, x1, z1] = st.rect;
      ctx.fillStyle = '#ede9fe';
      ctx.fillRect(X(x0), Y(z0), L(x1 - x0), L(z1 - z0));
      ctx.strokeStyle = '#8b5cf6';
      ctx.lineWidth = 0.9;
      ctx.strokeRect(X(x0), Y(z0), L(x1 - x0), L(z1 - z0));
    }
  }

  // 8. 充电桩
  for (const wh of warehouses) {
    for (const cs of wh.charging_stations || []) {
      const [cx, , cz] = cs.position;
      ctx.fillStyle = '#22c55e';
      ctx.beginPath();
      ctx.arc(X(cx), Y(cz), Math.max(1.8, L(0.35)), 0, Math.PI * 2);
      ctx.fill();
    }
  }

  // 9. 选中车的闭环路线
  if (S.show.route && S.sel) {
    drawRoute(ctx, S.sel);
  }

  // 10. 尾迹（仅选中车，避免 21 条尾迹糊成一片）
  if (S.show.trail && S.sel) {
    const a = S.byId[S.sel];
    if (a) drawTrail(ctx, a);
  }

  // 11. 车辆
  for (const a of S.paths.agents) {
    const p = sampleAgent(a, S.t);
    if (!p) continue;
    drawAgent(ctx, a.id, p, a.id === S.sel);
  }
}

function drawRoute(ctx, id) {
  const routes = S.boot.routes && S.boot.routes.routes;
  const bounds = S.boot.routes && S.boot.routes.bounds;
  const pts = routes && routes[id];
  if (!pts || pts.length < 2) return;
  const bd = (bounds && bounds[id]) || { pick: Math.floor(pts.length / 3), drop: Math.floor(pts.length * 2 / 3), end: pts.length };

  const segs = [
    [0, bd.pick, '#94a3b8', '空驶去程'],
    [bd.pick, bd.drop, '#ea580c', '载货'],
    [bd.drop, pts.length, '#0891b2', '空驶回程'],
  ];

  ctx.save();
  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';
  for (const [s, e, color] of segs) {
    if (e - s < 2) continue;
    ctx.beginPath();
    for (let i = s; i < e; i++) {
      const p = pts[i];
      if (i === s) ctx.moveTo(X(p[0]), Y(p[1]));
      else ctx.lineTo(X(p[0]), Y(p[1]));
    }
    ctx.strokeStyle = color;
    ctx.globalAlpha = 0.5;
    ctx.lineWidth = 2.2;
    ctx.stroke();
    ctx.globalAlpha = 1;
  }

  // 取货/交付点
  const mk = (pt, color, label) => {
    if (!pt) return;
    const px = X(pt[0]), py = Y(pt[1]);
    ctx.beginPath();
    ctx.arc(px, py, 4.5, 0, Math.PI * 2);
    ctx.fillStyle = '#fff';
    ctx.fill();
    ctx.lineWidth = 2;
    ctx.strokeStyle = color;
    ctx.stroke();
    ctx.font = '600 10px -apple-system, "Microsoft YaHei", sans-serif';
    ctx.fillStyle = color;
    ctx.textAlign = 'center';
    ctx.fillText(label, px, py - 8);
  };
  mk(pts[bd.pick], '#ea580c', 'PICK');
  mk(pts[bd.drop], '#16a34a', 'DROP');
  ctx.restore();
}

function drawTrail(ctx, a) {
  const win = 7;
  const t0 = Math.max(a.t[0], S.t - win);
  if (S.t - t0 < 0.25) return;
  const steps = 26;
  ctx.beginPath();
  for (let i = 0; i <= steps; i++) {
    const t = t0 + (S.t - t0) * (i / steps);
    const p = sampleAgent(a, t);
    if (!p) continue;
    if (i === 0) ctx.moveTo(X(p.x), Y(p.y));
    else ctx.lineTo(X(p.x), Y(p.y));
  }
  const grad = ctx.createLinearGradient(0, 0, C.w, C.h);
  grad.addColorStop(0, 'rgba(37,99,235,0)');
  grad.addColorStop(1, 'rgba(37,99,235,.45)');
  ctx.strokeStyle = S.sel ? 'rgba(234,88,12,.55)' : grad;
  ctx.lineWidth = S.sel ? 2 : 1.2;
  ctx.lineCap = 'round';
  ctx.stroke();
}

function drawAgent(ctx, id, p, sel) {
  const px = X(p.x), py = Y(p.y);
  if (px < -30 || px > C.w + 30 || py < -30 || py > C.h + 30) return;
  const color = PH_COLOR[p.ph] || '#64748b';
  const size = sel ? 1.35 : 1;
  const ang = -p.th;

  ctx.save();
  ctx.translate(px, py);
  ctx.rotate(ang);

  if (sel) {
    ctx.beginPath();
    ctx.arc(0, 0, 11, 0, Math.PI * 2);
    ctx.strokeStyle = 'rgba(37,99,235,.85)';
    ctx.lineWidth = 2;
    ctx.stroke();
    ctx.beginPath();
    ctx.arc(0, 0, 15, 0, Math.PI * 2);
    ctx.strokeStyle = 'rgba(37,99,235,.2)';
    ctx.lineWidth = 4;
    ctx.stroke();
  }

  ctx.beginPath();
  ctx.moveTo(8.5 * size, 0);
  ctx.lineTo(-5 * size, 5 * size);
  ctx.lineTo(-2.2 * size, 0);
  ctx.lineTo(-5 * size, -5 * size);
  ctx.closePath();
  ctx.fillStyle = color;
  ctx.fill();
  ctx.strokeStyle = 'rgba(255,255,255,.95)';
  ctx.lineWidth = sel ? 1.6 : 1;
  ctx.stroke();
  ctx.restore();

  if (S.view.scale > 5.5 || sel) {
    ctx.font = (sel ? '700 ' : '') + '9.5px -apple-system, "Microsoft YaHei", sans-serif';
    ctx.textAlign = 'center';
    ctx.fillStyle = sel ? '#0f172a' : 'rgba(30,41,59,.75)';
    ctx.strokeStyle = 'rgba(255,255,255,.9)';
    ctx.lineWidth = 2.4;
    ctx.strokeText(id, px, py - 11);
    ctx.fillText(id, px, py - 11);
  }
}

function renderHud() {
  if (!S.paths) return;
  const sel = S.sel && S.byId[S.sel];
  const p = sel ? sampleAgent(sel, S.t) : null;
  const total = S.paths.agents.length;

  let moving = 0;
  for (const a of S.paths.agents) {
    const q = sampleAgent(a, S.t);
    if (q && q.ph === 'MOVE') moving++;
  }
  let carrying = 0;
  const cargos = (S.boot && S.boot.cargo && S.boot.cargo.cargos) || [];
  for (const c of cargos) {
    if (S.t >= c.t_pick_s && S.t < c.t_drop_s) carrying++;
  }

  let html = `
    <div>t = <b>${fmt(S.t)}</b> s / ${fmt(S.tEnd)} s</div>
    <div>在场 <b>${total}</b> 台 · 行驶 <b>${moving}</b> · 载货 <b>${carrying}</b></div>`;
  if (p) {
    html += `<div>${esc(S.sel)} · <b style="color:${PH_COLOR[p.ph] || '#334155'}">${PH_CN[p.ph] || p.ph}</b> · (${fmt(p.x, 1)}, ${fmt(p.y, 1)})</div>`;
  }
  $('hud').innerHTML = html;
}

// ------------------------------------------------------------------ 交互

function bindCanvas() {
  const cv = $('cv');

  cv.addEventListener('wheel', (e) => {
    e.preventDefault();
    const rect = cv.getBoundingClientRect();
    const mx = e.clientX - rect.left, my = e.clientY - rect.top;
    const wx = (mx - S.view.ox) / S.view.scale;
    const wy = (my - S.view.oy) / S.view.scale;
    const f = e.deltaY < 0 ? 1.12 : 1 / 1.12;
    const ns = clamp(S.view.scale * f, 1.2, 90);
    S.view.scale = ns;
    S.view.ox = mx - wx * ns;
    S.view.oy = my - wy * ns;
    S.dirty = true;
  }, { passive: false });

  cv.addEventListener('mousedown', (e) => {
    S.drag = { x: e.clientX, y: e.clientY, ox: S.view.ox, oy: S.view.oy, moved: 0 };
    cv.classList.add('dragging');
  });

  window.addEventListener('mousemove', (e) => {
    if (!S.drag) return;
    const dx = e.clientX - S.drag.x, dy = e.clientY - S.drag.y;
    S.drag.moved = Math.max(S.drag.moved, Math.abs(dx) + Math.abs(dy));
    S.view.ox = S.drag.ox + dx;
    S.view.oy = S.drag.oy + dy;
    S.dirty = true;
  });

  window.addEventListener('mouseup', (e) => {
    if (!S.drag) return;
    const wasClick = S.drag.moved < 4;
    S.drag = null;
    cv.classList.remove('dragging');
    if (!wasClick) return;

    const rect = cv.getBoundingClientRect();
    const mx = e.clientX - rect.left, my = e.clientY - rect.top;
    if (mx < 0 || my < 0 || mx > rect.width || my > rect.height) return;

    let best = null, bestD = 14;
    for (const a of S.paths.agents) {
      const p = sampleAgent(a, S.t);
      if (!p) continue;
      const d = Math.hypot(X(p.x) - mx, Y(p.y) - my);
      if (d < bestD) { bestD = d; best = a.id; }
    }
    if (best) selectAgent(best);
  });

  cv.addEventListener('dblclick', () => fitView());

  $('btnFit').addEventListener('click', fitView);
  $('btnReload').addEventListener('click', () => reloadData(false));

  const toggles = [
    ['chkRacks', 'racks'], ['chkZones', 'zones'], ['chkHeat', 'heat'],
    ['chkTrail', 'trail'], ['chkRoute', 'route'],
  ];
  for (const [id, key] of toggles) {
    $(id).addEventListener('change', (e) => {
      S.show[key] = e.target.checked;
      S.dirty = true;
    });
  }

  window.addEventListener('resize', () => {
    if (S.mode3d) {
      if (viewer) viewer.resize();
    } else {
      resizeCanvas();
    }
    S.dirty = true;
  });

  window.addEventListener('keydown', (e) => {
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
    if (e.code === 'Space') { e.preventDefault(); setPlaying(!S.playing); }
    else if (e.code === 'ArrowRight') { e.preventDefault(); step(0.5); }
    else if (e.code === 'ArrowLeft') { e.preventDefault(); step(-0.5); }
    else if (e.code === 'Home') { S.t = 0; syncTimeline(); S.dirty = true; }
    else if (e.code === 'End') { S.t = S.tEnd; syncTimeline(); S.dirty = true; }
  });
}

function step(dt) {
  S.t = clamp(S.t + dt, 0, S.tEnd);
  syncTimeline();
  S.dirty = true;
}

function selectAgent(id) {
  S.sel = id;
  renderDetail();
  updateAgentsLive();
  highlightOrder(id);
  S.dirty = true;
}

// ------------------------------------------------------------------ 3D 视图

let viewer = null;
let viewerTask = null;

const sampler = (id, t) => {
  const a = S.byId[id];
  return a ? sampleAgent(a, t) : null;
};

async function ensureViewer() {
  if (viewer) return viewer;
  if (viewerTask) return viewerTask;

  viewerTask = (async () => {
    const stat = $('glbStat');
    stat.textContent = '加载 dc_preview.glb…';
    try {
      const mod = await import('/static/viewer3d.js');
      const lay = (S.boot && S.boot.layout) || {};
      const ls = lay.size || { width: 112, depth: 114 };
      viewer = await mod.create($('cv3d'), {
        url: '/out/dc_preview.glb',
        bounds: { x0: 0, z0: 0, x1: ls.width, z1: ls.depth },
        onProgress: (r) => { stat.textContent = `加载中 ${Math.round(r * 100)}%`; },
        onPick: (id) => selectAgent(id),
      });
      const st = viewer.stats();
      stat.textContent = `${st.agv} 台 AGV · ${st.nodes} 个节点`;
      viewer.update(S.t, S.sel, sampler);
    } catch (e) {
      stat.textContent = '3D 加载失败：' + e.message;
      throw e;
    } finally {
      viewerTask = null;
    }
    return viewer;
  })();

  return viewerTask;
}

async function switchView(mode) {
  const is3d = mode === '3d';
  if (!!S.mode3d === is3d) return;
  S.mode3d = is3d;

  $('cv').hidden = is3d;
  $('cv3d').hidden = !is3d;
  $('tools2d').hidden = is3d;
  $('bar3d').hidden = !is3d;
  $('hint2d').hidden = is3d;
  $('hint3d').hidden = !is3d;
  [...$('viewSwitch').children].forEach((b) => b.classList.toggle('on', b.dataset.view === mode));

  if (is3d) {
    try {
      await ensureViewer();
      const cam = new URLSearchParams(location.search).get('cam');
      if (cam) viewer.preset(cam);
      viewer.setVisible(true);
      viewer.update(S.t, S.sel, sampler);
    } catch (_) { /* 错误信息已写到 glbStat */ }
  } else {
    if (viewer) viewer.setVisible(false);
    resizeCanvas();
    S.dirty = true;
  }
}

function bindView3d() {
  $('viewSwitch').addEventListener('click', (e) => {
    const b = e.target.closest('button[data-view]');
    if (b) switchView(b.dataset.view);
  });
  $('bar3d').addEventListener('click', (e) => {
    const b = e.target.closest('button[data-cam]');
    if (b && viewer) viewer.preset(b.dataset.cam);
  });
  $('chkFollow').addEventListener('change', (e) => {
    if (viewer) viewer.setFollow(e.target.checked);
  });
  $('btnFit3d').addEventListener('click', () => { if (viewer) viewer.preset('iso'); });
}

// ------------------------------------------------------------------ 时间轴

function setPlaying(on) {
  S.playing = on;
  $('btnPlay').textContent = on ? '❚❚' : '▶';
  if (on && S.t >= S.tEnd - 1e-6) { S.t = 0; syncTimeline(); }
}

function syncTimeline() {
  const v = S.tEnd > 0 ? Math.round((S.t / S.tEnd) * 1000) : 0;
  $('scrub').value = String(v);
  $('timeNow').textContent = fmt(S.t);
}

function bindTimeline() {
  $('btnPlay').addEventListener('click', () => setPlaying(!S.playing));

  $('scrub').addEventListener('input', (e) => {
    const v = Number(e.target.value) / 1000;
    S.t = clamp(v * S.tEnd, 0, S.tEnd);
    $('timeNow').textContent = fmt(S.t);
    S.dirty = true;
  });

  const speeds = [0.25, 0.5, 1, 2, 4, 8];
  const box = $('speeds');
  box.innerHTML = '';
  for (const sp of speeds) {
    const b = el('button', sp === 1 ? 'on' : '', sp + '×');
    b.addEventListener('click', () => {
      S.speed = sp;
      [...box.children].forEach((c) => c.classList.remove('on'));
      b.classList.add('on');
    });
    box.appendChild(b);
  }
}

function renderMarks() {
  const box = $('tlMarks');
  box.innerHTML = '';
  const tasks = (S.boot.tasks && S.boot.tasks.tasks) || [];
  if (!S.tEnd) return;
  for (const t of tasks) {
    const r = clamp((t.t_request || 0) / S.tEnd, 0, 1);
    const i = el('i', '');
    i.style.left = (r * 100) + '%';
    i.title = `${t.task_id} ${t.agent_id} @ ${fmt(t.t_request)} s`;
    box.appendChild(i);
  }
}

// ------------------------------------------------------------------ 侧栏：车辆

function agentMeta(id) {
  return (S.boot.agents || []).find((a) => a.id === id);
}

function taskOf(id) {
  const ts = (S.boot.tasks && S.boot.tasks.tasks) || [];
  return ts.find((t) => t.agent_id === id);
}

function cargoOf(id) {
  const cs = (S.boot.cargo && S.boot.cargo.cargos) || [];
  return cs.find((c) => c.agent_id === id);
}

function cargoStateAt(c, t) {
  if (!c || !c.states) return null;
  let cur = c.states[0];
  for (const s of c.states) {
    if (s.t <= t + 1e-9) cur = s; else break;
  }
  return cur;
}

function renderDetail() {
  const box = $('agvDetail');
  if (!S.sel) {
    box.innerHTML = '<div class="empty">点击画布上的车辆，或从下方列表选择</div>';
    return;
  }
  const a = S.byId[S.sel];
  const meta = agentMeta(S.sel);
  const task = taskOf(S.sel);
  const cargo = cargoOf(S.sel);
  const p = a ? sampleAgent(a, S.t) : null;
  const cs = cargoStateAt(cargo, S.t);
  const legs = S.boot.reports.route && S.boot.reports.route.data &&
    S.boot.reports.route.data.loop_leg_lengths_m && S.boot.reports.route.data.loop_leg_lengths_m[S.sel];

  const ph = p ? p.ph : '—';
  const color = PH_COLOR[ph] || '#64748b';

  box.innerHTML = `
    <div class="dcard">
      <div class="dcard-h">
        <span class="aid">${esc(S.sel)}</span>
        <span class="pill" style="background:${color}">${esc(PH_CN[ph] || ph)}</span>
        <span class="muted" style="margin-left:auto;font-size:10.5px">${esc(meta ? meta.warehouse : '')}</span>
      </div>
      <dl class="kv">
        <dt>坐标</dt><dd>${p ? fmt(p.x, 2) + ', ' + fmt(p.y, 2) + ' m' : '—'}</dd>
        <dt>朝向</dt><dd>${p ? fmt((p.th * 180 / Math.PI) % 360, 1) + '°' : '—'}</dd>
        <dt>电量</dt><dd>${meta && meta.battery != null ? meta.battery + '%' : '—'}</dd>
        <dt>待命位</dt><dd>${esc(meta ? meta.home_id : '—')}</dd>
        <dt>任务</dt><dd>${esc(task ? task.task_id + ' · ' + task.event_id : '—')}</dd>
        <dt>订单类型</dt><dd>${esc(cargo ? cargo.order_type : '—')}</dd>
        <dt>取货点</dt><dd>${esc(task ? task.pick_location : '—')}</dd>
        <dt>交付点</dt><dd>${esc(task ? task.drop_location : '—')}</dd>
        <dt>货物</dt><dd>${esc(cargo ? cargo.sku + ' × ' + cargo.qty : '—')}</dd>
        <dt>货物状态</dt><dd>${esc(cs ? cs.state : '—')}${cs ? ' <span class="muted">@ ' + fmt(cs.t, 1) + 's</span>' : ''}</dd>
        <dt>三段里程</dt><dd>${legs ? legs.map((v) => fmt(v, 1)).join(' / ') + ' m' : '—'}</dd>
      </dl>
    </div>`;
}

function renderAgents() {
  const box = $('agvList');
  box.innerHTML = '';
  S.agentNodes = {};
  const metas = S.boot.agents || [];
  const groups = {};
  for (const m of metas) (groups[m.warehouse] = groups[m.warehouse] || []).push(m);

  // 按 layout 里的仓库顺序（W 主仓在前），而不是字母序
  const order = ((S.boot.layout && S.boot.layout.warehouses) || [])
    .map((w) => w.id)
    .filter((id) => groups[id]);
  for (const id of Object.keys(groups)) if (!order.includes(id)) order.push(id);

  for (const wh of order) {
    box.appendChild(el('div', 'pane-sub', `${wh} 仓 · ${groups[wh].length} 台`));
    for (const m of groups[wh]) {
      const btn = el('button', 'item' + (m.id === S.sel ? ' sel' : ''));
      btn.innerHTML = `
        <span class="dot"></span>
        <span>
          <span class="t1">${esc(m.id)}</span>
          <span class="t2"></span>
        </span>
        <span class="rt"></span>`;
      btn.addEventListener('click', () => selectAgent(m.id));
      box.appendChild(btn);
      S.agentNodes[m.id] = {
        root: btn,
        dot: btn.querySelector('.dot'),
        t2: btn.querySelector('.t2'),
        rt: btn.querySelector('.rt'),
      };
    }
  }
  updateAgentsLive();
}

function updateAgentsLive() {
  if (!S.agentNodes) return;
  for (const id in S.agentNodes) {
    const n = S.agentNodes[id];
    const a = S.byId[id];
    const p = a ? sampleAgent(a, S.t) : null;
    const ph = p ? p.ph : 'PARK';
    const task = taskOf(id);
    n.dot.style.background = PH_COLOR[ph] || '#94a3b8';
    n.rt.textContent = PH_CN[ph] || ph;
    n.rt.style.color = ph === 'PARK' ? 'var(--muted)' : 'var(--text-2)';
    n.t2.textContent = task
      ? task.task_id + ' · ' + (task.legs && task.legs[0] ? task.legs[0].from + ' → ' + task.legs[0].to : '')
      : '无任务';
    n.root.classList.toggle('sel', id === S.sel);
  }
}

// ------------------------------------------------------------------ 侧栏：订单

function renderOrders() {
  const box = $('orderList');
  box.innerHTML = '';
  const cs = (S.boot.cargo && S.boot.cargo.cargos) || [];
  const sum = S.boot.cargo && S.boot.cargo.summary;
  if (sum) {
    $('ordersSub').textContent =
      `${sum.orders} 张订单 · LOADED ${sum.loaded} · DELIVERED ${sum.delivered} · 迁移 ${sum.state_transitions} 次`;
  }
  for (const c of cs) {
    const btn = el('button', 'item');
    btn.dataset.agent = c.agent_id;
    btn.innerHTML = `
      <span class="dot" style="background:#64748b"></span>
      <span>
        <span class="t1">${esc(c.task_id)} · ${esc(c.order_type)}</span>
        <span class="t2">${esc(c.agent_id)} · ${esc(c.sku)} · ${esc(c.pickup_location)} → ${esc(c.drop_location)}</span>
      </span>
      <span class="rt">${fmt(c.t_pick_s, 1)}→${fmt(c.t_drop_s, 1)}s</span>`;
    btn.addEventListener('click', () => {
      selectAgent(c.agent_id);
      S.t = clamp(c.t_pick_s, 0, S.tEnd);
      syncTimeline();
      S.dirty = true;
      switchTab('agv');
    });
    box.appendChild(btn);
  }
}

function highlightOrder(agentId) {
  [...$('orderList').children].forEach((n) => {
    if (n.dataset && n.dataset.agent) n.classList.toggle('sel', n.dataset.agent === agentId);
  });
}

// ------------------------------------------------------------------ 侧栏：验收

function renderAudit() {
  const rep = S.boot.reports.validate && S.boot.reports.validate.data;
  const items = Array.isArray(rep) ? rep : [];
  const box = $('auditList');
  const sum = $('auditSum');
  box.innerHTML = '';

  const pass = items.filter((x) => x.status === 'PASS').length;
  const allPass = items.length && pass === items.length;
  const neg = S.boot.reports.negative && S.boot.reports.negative.data;
  const negTxt = neg && neg.total ? ` · 负测试 ${neg.passed}/${neg.total}` : '';

  sum.className = 'audit-sum' + (allPass ? '' : ' bad');
  sum.innerHTML = items.length
    ? `<b>${pass}/${items.length}</b> PASS${esc(negTxt)}`
    : '<span class="muted">未找到 out/validate_report.json</span>';

  for (const it of items) {
    const m = /^A(\d+)/.exec(it.item || '');
    const no = m ? m[1] : '·';
    const name = (it.item || '').replace(/^A\d+\s*/, '');
    const node = el('div', 'aitem');
    node.innerHTML = `
      <span class="no">${esc(no)}</span>
      <span>
        <span class="st ${esc(it.status)}">${esc(it.status)}</span>
        <span> ${esc(name)}</span>
        <div class="dt">${esc(it.detail || '')}</div>
      </span>`;
    box.appendChild(node);
  }

  // 负测试：验证器必须能失败，否则验收本身不可信
  if (neg && neg.cases && neg.cases.length) {
    box.appendChild(el('div', 'pane-sub', `验证器负测试 ${neg.passed}/${neg.total} 全部按预期失败`));
    for (const c of neg.cases) {
      const node = el('div', 'aitem');
      node.innerHTML = `
        <span class="no">${c.ok ? '✓' : '✗'}</span>
        <span>
          <span class="st ${c.ok ? 'PASS' : 'FAIL'}">${c.ok ? '按预期失败' : '未失败'}</span>
          <div class="dt">${esc(c.name)}</div>
          <div class="dt">${esc(c.check)} · exit ${esc(c.exit_code)}（期望 ${esc(c.expected)}）</div>
        </span>`;
      box.appendChild(node);
    }
  }
}

// ------------------------------------------------------------------ 侧栏：参数

function renderParams() {
  const form = $('paramForm');
  form.innerHTML = '';
  const cfg = S.boot.config || {};
  const fields = (S.schema && S.schema.fields) || [];

  const get = (path) => path.split('.').reduce((o, k) => (o && o[k] !== undefined ? o[k] : undefined), cfg);

  for (const f of fields) {
    const v = get(f.path);
    const row = el('div', 'field');
    const id = 'f_' + f.path.replace(/\./g, '_');
    row.innerHTML = `
      <label for="${id}">${esc(f.label)}${f.hint ? '<small>' + esc(f.hint) + '</small>' : ''}</label>
      <input id="${id}" data-path="${esc(f.path)}" type="number"
             value="${v === undefined ? '' : v}"
             ${f.min !== undefined ? 'min="' + f.min + '"' : ''}
             ${f.max !== undefined ? 'max="' + f.max + '"' : ''}
             ${f.step !== undefined ? 'step="' + f.step + '"' : (f.type === 'int' ? 'step="1"' : 'step="any"')}>`;
    form.appendChild(row);
  }
}

async function saveConfig() {
  const msg = $('cfgMsg');
  const inputs = [...$('paramForm').querySelectorAll('input[data-path]')];
  const patch = {};
  for (const inp of inputs) {
    if (inp.value === '') continue;
    patch[inp.dataset.path] = inp.value;
  }
  msg.textContent = '保存中…';
  msg.style.color = '';
  try {
    const r = await apiPost('/api/config', { patch });
    const n = Object.keys(r.applied || {}).length;
    msg.textContent = `已写入 ${n} 项（备份 data/config.bak.json）`;
    msg.style.color = 'var(--ok)';
    if (r.warnings && r.warnings.length) {
      toast(r.warnings.join('；'), true);
    }
    S.boot.config = r.config;
  } catch (e) {
    msg.textContent = '失败：' + e.message;
    msg.style.color = 'var(--bad)';
  }
}

function buildScopeUI() {
  const box = $('runScopes');
  box.innerHTML = '';
  const scopes = (S.schema && S.schema.scopes) || {};
  const defs = [
    ['data', '数据层重跑', '纯 Python 全链路（约 10 秒）：布局 → 栅格 → 派单 → 路径 → 校验 → 路线图'],
    ['validate', '仅复验', '负测试 + A1–A21 + 状态表，不重算数据'],
    ['full', '全链路重跑', '含 Blender 建模、动画、渲染 942 帧，耗时最长'],
  ];
  for (const [key, title, desc] of defs) {
    const n = scopes[key] ? scopes[key].steps : '';
    const lab = el('label', 'scope' + (key === S.runScope ? ' on' : ''));
    lab.innerHTML = `
      <input type="radio" name="scope" value="${key}" ${key === S.runScope ? 'checked' : ''}>
      <span><strong>${esc(title)}</strong>${n ? ` <span class="muted">(${n} 步)</span>` : ''}
        <small>${esc(desc)}</small></span>`;
    lab.querySelector('input').addEventListener('change', () => {
      S.runScope = key;
      [...box.children].forEach((c) => c.classList.remove('on'));
      lab.classList.add('on');
    });
    box.appendChild(lab);
  }
}

// ------------------------------------------------------------------ 重跑

async function startRun() {
  $('logBox').textContent = '';
  $('progLabel').textContent = '启动中…';
  $('progFill').style.width = '0%';
  $('progFill').className = '';
  $('btnCancel').disabled = false;
  S.jobNext = 0;
  S.jobHandled = false;

  try {
    const r = await apiPost('/api/run', { scope: S.runScope });
    toast(r.label + ' 已启动');
  } catch (e) {
    toast('启动失败：' + e.message, true);
    $('progLabel').textContent = '启动失败';
    $('btnCancel').disabled = true;
    return;
  }
  pollJob();
}

async function pollJob() {
  let j;
  try {
    j = await apiGet('/api/job?since=' + S.jobNext);
  } catch (e) {
    return;
  }
  if (!j || j.status === 'idle') return;

  S.job = j;
  if (j.lines && j.lines.length) {
    const box = $('logBox');
    const nearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 60;
    for (const l of j.lines) {
      const span = el('span', l.level ? 'l-' + l.level : '', esc(l.text) + '\n');
      box.appendChild(span);
    }
    if (nearBottom) box.scrollTop = box.scrollHeight;
    S.jobNext = j.next;
  }

  const pct = j.total ? Math.round((j.done / j.total) * 100) : 0;
  const fill = $('progFill');
  fill.style.width = pct + '%';
  fill.className = j.status === 'ok' ? 'ok' : (j.status === 'failed' || j.status === 'cancelled' ? 'bad' : '');
  $('progLabel').textContent =
    j.status === 'running'
      ? `${j.done}/${j.total} · ${j.current_label || '准备中'} · ${j.elapsed}s`
      : `${j.status === 'ok' ? '完成' : (j.status === 'cancelled' ? '已取消' : '失败')} · ${j.elapsed}s`;

  if (j.status === 'running') {
    $('btnCancel').disabled = false;
    if (!S.jobTimer) S.jobTimer = setInterval(pollJob, 600);
    return;
  }

  if (S.jobTimer) {
    clearInterval(S.jobTimer);
    S.jobTimer = null;
  }
  $('btnCancel').disabled = true;
  if (j.status === 'ok') fill.style.width = '100%';
  if (j.status === 'ok' && !S.jobHandled) {
    S.jobHandled = true;
    await reloadData(true);
    toast('重跑完成，数据已刷新');
  } else if (j.status === 'failed') {
    toast('重跑失败，见运行日志', true);
  }
}

async function cancelRun() {
  try {
    await apiPost('/api/cancel', {});
    toast('已请求取消');
  } catch (e) {
    toast('取消失败：' + e.message, true);
  }
}

// ------------------------------------------------------------------ 侧栏：产物

function renderArtifacts() {
  const g = $('gallery');
  g.innerHTML = '';
  for (const r of S.boot.renders || []) {
    const t = el('div', 'thumb');
    t.innerHTML = `<img src="${r.url}" alt="${esc(r.name)}" loading="lazy"><span>${esc(r.name.replace(/\.png$/, ''))}</span>`;
    t.addEventListener('click', () => openLightbox(r.url, r.name));
    g.appendChild(t);
  }

  const vb = $('videoBox');
  vb.innerHTML = '';
  if (S.boot.video) {
    vb.innerHTML = `<video controls preload="metadata" src="${S.boot.video.url}"></video>
      <div class="muted" style="font-size:10.5px;margin-top:4px">${esc(S.boot.video.mtime)} · ${(S.boot.video.size / 1048576).toFixed(2)} MB</div>`;
  } else {
    vb.innerHTML = '<div class="muted" style="font-size:11px">未找到 out/anim_dc.mp4</div>';
  }

  $('statusMd').innerHTML = mdToHtml(S.boot.status_md || '');
}

function mdToHtml(md) {
  const lines = md.split(/\r?\n/);
  let out = '';
  let inTable = false;
  for (const raw of lines) {
    const line = raw.trimEnd();
    if (/^\|/.test(line)) {
      const cells = line.split('|').slice(1, -1).map((c) => c.trim());
      if (/^[-: ]+$/.test(cells.join(''))) continue;
      if (!inTable) { out += '<table>'; inTable = true; }
      out += '<tr>' + cells.map((c) => `<td>${inline(c)}</td>`).join('') + '</tr>';
      continue;
    }
    if (inTable) { out += '</table>'; inTable = false; }
    if (/^###\s/.test(line)) out += `<h2>${inline(line.replace(/^###\s*/, ''))}</h2>`;
    else if (/^##\s/.test(line)) out += `<h2>${inline(line.replace(/^##\s*/, ''))}</h2>`;
    else if (/^#\s/.test(line)) out += `<h2>${inline(line.replace(/^#\s*/, ''))}</h2>`;
    else if (/^>\s?/.test(line)) out += `<blockquote>${inline(line.replace(/^>\s?/, ''))}</blockquote>`;
    else if (/^[-*]\s/.test(line)) out += `<ul><li>${inline(line.replace(/^[-*]\s/, ''))}</li></ul>`;
    else if (line === '') continue;
    else out += `<div>${inline(line)}</div>`;
  }
  if (inTable) out += '</table>';
  return out;
}

function inline(s) {
  return esc(s)
    .replace(/\*\*(.+?)\*\*/g, '<b>$1</b>')
    .replace(/`([^`]+)`/g, '<code>$1</code>');
}

function openLightbox(url, name) {
  $('lightboxImg').src = url;
  $('lightboxCap').textContent = name;
  $('lightbox').classList.add('on');
}

// ------------------------------------------------------------------ Tab

function switchTab(name) {
  [...$('tabs').children].forEach((b) => b.classList.toggle('active', b.dataset.tab === name));
  document.querySelectorAll('.pane').forEach((p) => p.classList.toggle('active', p.id === 'pane-' + name));
}

function bindTabs() {
  $('tabs').addEventListener('click', (e) => {
    const b = e.target.closest('button[data-tab]');
    if (b) switchTab(b.dataset.tab);
  });
  $('lightbox').addEventListener('click', () => $('lightbox').classList.remove('on'));
  $('btnSaveCfg').addEventListener('click', saveConfig);
  $('btnCancel').addEventListener('click', cancelRun);
  $('btnRun').addEventListener('click', startRun);
}

// ------------------------------------------------------------------ 主循环

let lastTs = 0;
let lastPanel = 0;

function tick(ts) {
  const dt = lastTs ? (ts - lastTs) / 1000 : 0;
  lastTs = ts;

  if (S.playing && S.tEnd > 0) {
    S.t += Math.min(dt, 0.25) * S.speed;
    if (S.t >= S.tEnd) { S.t = S.tEnd; setPlaying(false); }
    syncTimeline();
    S.dirty = true;
  }

  if (S.dirty) {
    S.dirty = false;
    if (S.mode3d) {
      if (viewer) viewer.update(S.t, S.sel, sampler);
    } else {
      draw();
    }
  }

  // 侧栏按 ~7 Hz 刷新即可，避免每帧重建 DOM
  if (ts - lastPanel > 140) {
    lastPanel = ts;
    if (S.boot) { renderDetail(); updateAgentsLive(); renderHud(); }
  }

  requestAnimationFrame(tick);
}

// ------------------------------------------------------------------ 启动

async function main() {
  resizeCanvas();
  bindCanvas();
  bindView3d();
  bindTimeline();
  bindTabs();
  try {
    await loadAll();
    applyUrlState();
    // 恢复上一次作业的日志（刷新页面后仍能看到重跑记录）
    S.jobHandled = true;
    await pollJob();
    if (S.job && S.job.status === 'running') S.jobHandled = false;
  } catch (e) {
    document.body.insertAdjacentHTML('beforeend',
      `<div class="toast bad on" style="font-size:13px">无法加载数据：${esc(e.message)}<br>
       请确认通过 <b>python console/server.py</b> 启动，而不是直接打开 HTML。</div>`);
  }
  requestAnimationFrame(tick);
}

/** 支持深链接：?t=42.5&sel=W-R03&tab=audit&play=1 */
function applyUrlState() {
  const q = new URLSearchParams(location.search);
  if (q.has('t')) {
    S.t = clamp(parseFloat(q.get('t')) || 0, 0, S.tEnd);
    syncTimeline();
    S.dirty = true;
  }
  if (q.has('sel') && S.byId[q.get('sel')]) selectAgent(q.get('sel'));
  if (q.has('tab')) switchTab(q.get('tab'));
  if (q.get('view') === '3d') switchView('3d');
  if (q.get('play') === '1') setPlaying(true);
}

document.addEventListener('DOMContentLoaded', main);
