/* G1 · 可视化布局编辑器（纯前端、零后端）
 * 交互参照 Stegocode/warehouse_layout_editor（MIT，已核实），代码自研。
 * 产出永远是 DRAFT，绝不写 config/layout_spec.json。
 */
(function () {
  'use strict';

  const TOOLS = [
    ['select', '选择 / 移动  (V)'],
    ['zone', '+ 分区  (Z)'],
    ['rack', '+ 货架排  (R)'],
    ['door', '+ 月台门  (D)'],
    ['home', '+ 待命位  (H)'],
    ['delete', '删除  (X)'],
  ];

  const state = {
    canvas: null, ctx: null, data: null, tool: 'select',
    items: [], selected: null, history: [], bg: null, bgLoaded: false,
    calibPoints: [], calibMeters: 100, scaleMPerPx: null,
    drag: null, onChange: null, W: 1000, H: 720, pad: 26,
  };

  function worldToScreen(x, y) {
    const [sw, sh] = state.data.layout.size;
    const k = Math.min((state.W - state.pad * 2) / sw, (state.H - state.pad * 2) / sh);
    return [state.pad + x * k, state.H - state.pad - y * k];
  }
  function screenToWorld(px, py) {
    const [sw, sh] = state.data.layout.size;
    const k = Math.min((state.W - state.pad * 2) / sw, (state.H - state.pad * 2) / sh);
    return [(px - state.pad) / k, (state.H - state.pad - py) / k];
  }
  function scaleK() {
    const [sw, sh] = state.data.layout.size;
    return Math.min((state.W - state.pad * 2) / sw, (state.H - state.pad * 2) / sh);
  }

  function clone(o) { return JSON.parse(JSON.stringify(o)); }

  function seedItems() {
    const out = [];
    state.data.layout.warehouses.forEach((w) => {
      (w.zones || []).forEach((z) => {
        const pts = z.polygon || [];
        if (pts.length >= 3) {
          const xs = pts.map(p => p[0]), ys = pts.map(p => p[1]);
          out.push({ id: z.id, type: 'zone', warehouse: w.id,
            rect: [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)],
            color: z.color || '#3b82f6', locked: false });
        }
      });
      (w.homes || []).forEach((h) => {
        out.push({ id: h[2], type: 'home', warehouse: w.id, x: h[0], y: h[1] });
      });
    });
    return out;
  }

  function reset() {
    state.items = seedItems();
    state.history = [];
    state.selected = null;
    draw();
    emit();
  }

  function pushHistory() { state.history.push(clone(state.items)); if (state.history.length > 40) state.history.shift(); }
  function undo() { if (state.history.length) { state.items = state.history.pop(); state.selected = null; draw(); emit(); } }

  function validate() {
    const issues = [];
    const [sw, sh] = state.data.layout.size;
    state.items.forEach((it) => {
      if (it.type === 'zone') {
        const [x0, y0, x1, y1] = it.rect;
        if (x0 < 0 || y0 < 0 || x1 > sw || y1 > sh) issues.push(it.id + ' 分区超出场地边界');
        if (x1 <= x0 || y1 <= y0) issues.push(it.id + ' 分区宽高无效');
      } else {
        if (it.x < 0 || it.y < 0 || it.x > sw || it.y > sh) issues.push(it.id + ' 超出场地边界');
      }
    });
    const homes = state.items.filter(i => i.type === 'home');
    for (let i = 0; i < homes.length; i++) for (let j = i + 1; j < homes.length; j++) {
      const d = Math.hypot(homes[i].x - homes[j].x, homes[i].y - homes[j].y);
      if (d < 2) issues.push(homes[i].id + ' / ' + homes[j].id + ' 待命位间距 ' + d.toFixed(2) + ' m < 2 m');
    }
    const zones = state.items.filter(i => i.type === 'zone');
    for (let i = 0; i < zones.length; i++) for (let j = i + 1; j < zones.length; j++) {
      const a = zones[i].rect, b = zones[j].rect;
      if (a[0] < b[2] && b[0] < a[2] && a[1] < b[3] && b[1] < a[3])
        issues.push(zones[i].id + ' / ' + zones[j].id + ' 分区重叠');
    }
    return issues;
  }

  function draw() {
    const c = state.ctx, W = state.W, H = state.H;
    c.clearRect(0, 0, W, H);
    c.fillStyle = '#fbfcf9'; c.fillRect(0, 0, W, H);
    const k = scaleK();
    const [sw, sh] = state.data.layout.size;

    // site border
    const [ox, oy] = worldToScreen(0, sh);
    c.strokeStyle = '#d5ded6'; c.lineWidth = 1;
    c.strokeRect(ox, oy, sw * k, sh * k);
    c.fillStyle = '#8d9b92'; c.font = '11px system-ui';
    c.fillText(sw + ' × ' + sh + ' m', ox + 4, oy - 6);

    // tracing background
    if (state.bg && state.bgLoaded) {
      c.save(); c.globalAlpha = .42;
      const bw = state.bg.width, bh = state.bg.height;
      const f = Math.min((W - state.pad * 2) / bw, (H - state.pad * 2) / bh);
      c.drawImage(state.bg, state.pad, state.pad + (H - state.pad * 2 - bh * f) / 2,
        (W - state.pad * 2), bh * f);
      c.restore();
    }

    // existing racks as reference (not editable)
    c.fillStyle = '#dfe6df';
    state.data.layout.warehouses.forEach((w) => {
      w.racks.forEach((r) => {
        const [x, y] = worldToScreen(w.origin[0] + r[0], w.origin[1] + r[1]);
        c.fillRect(x, y - r[3] * k, Math.max(1, r[2] * k), Math.max(1, r[3] * k));
      });
    });
    // warehouse outlines
    state.data.layout.warehouses.forEach((w) => {
      const [x, y] = worldToScreen(w.origin[0], w.origin[1] + w.size[1]);
      c.strokeStyle = '#b9c7bc'; c.setLineDash([5, 4]);
      c.strokeRect(x, y, w.size[0] * k, w.size[1] * k); c.setLineDash([]);
      c.fillStyle = '#5d6d63'; c.font = 'bold 12px system-ui';
      c.fillText(w.id, x + 6, y + 16);
    });

    const issues = validate();
    const badIds = new Set(issues.map(s => s.split(' ')[0]));
    state.items.forEach((it) => {
      const bad = badIds.has(it.id);
      c.lineWidth = it.id === state.selected ? 3 : 1.5;
      c.strokeStyle = bad ? '#c05252' : (it.id === state.selected ? '#1f513f' : '#9fb3a6');
      c.fillStyle = bad ? 'rgba(192,82,82,.12)' : 'rgba(47,117,89,.10)';
      if (it.type === 'zone') {
        const [x, y] = worldToScreen(it.rect[0], it.rect[3]);
        const w = (it.rect[2] - it.rect[0]) * k, h = (it.rect[3] - it.rect[1]) * k;
        c.fillRect(x, y, w, h); c.strokeRect(x, y, w, h);
        c.fillStyle = '#42534b'; c.font = '10px system-ui'; c.fillText(it.id, x + 4, y + 12);
      } else {
        const [x, y] = worldToScreen(it.x, it.y);
        const r = it.type === 'rack' ? 9 : 6;
        c.beginPath();
        if (it.type === 'door') c.rect(x - r, y - 4, r * 2, 8);
        else c.arc(x, y, r, 0, Math.PI * 2);
        c.fill(); c.stroke();
        if (it.type === 'rack') {
          c.fillStyle = '#42534b'; c.font = '9px system-ui'; c.fillText('货架排', x + 11, y + 3);
        }
      }
    });

    // calibration points
    state.calibPoints.forEach((p, i) => {
      c.beginPath(); c.arc(p[0], p[1], 6, 0, Math.PI * 2);
      c.fillStyle = '#c98b3a'; c.fill();
      c.fillStyle = '#7a5a25'; c.font = 'bold 11px system-ui';
      c.fillText('P' + (i + 1), p[0] + 9, p[1] - 6);
    });
    if (state.calibPoints.length === 2) {
      c.strokeStyle = '#c98b3a'; c.setLineDash([4, 3]); c.beginPath();
      c.moveTo(...state.calibPoints[0]); c.lineTo(...state.calibPoints[1]); c.stroke();
      c.setLineDash([]);
    }
  }

  function hit(px, py) {
    for (let i = state.items.length - 1; i >= 0; i--) {
      const it = state.items[i];
      if (it.type === 'zone') {
        const [x, y] = worldToScreen(it.rect[0], it.rect[3]);
        const w = (it.rect[2] - it.rect[0]) * scaleK(), h = (it.rect[3] - it.rect[1]) * scaleK();
        if (px >= x && px <= x + w && py >= y && py <= y + h) return it;
      } else {
        const [x, y] = worldToScreen(it.x, it.y);
        if (Math.hypot(px - x, py - y) <= 12) return it;
      }
    }
    return null;
  }

  function addAt(px, py) {
    const [wx, wy] = screenToWorld(px, py);
    const n = state.items.length + 1;
    let it = null;
    if (state.tool === 'zone') it = { id: 'ZONE-N' + n, type: 'zone', warehouse: 'W',
      rect: [wx - 12, wy - 8, wx + 12, wy + 8], color: '#c98b3a' };
    else if (state.tool === 'rack') it = { id: 'RACKROW-N' + n, type: 'rack', warehouse: 'W', x: wx, y: wy };
    else if (state.tool === 'door') it = { id: 'DOOR-N' + n, type: 'door', warehouse: 'W', x: wx, y: wy };
    else if (state.tool === 'home') it = { id: 'HOME-N' + n, type: 'home', warehouse: 'W', x: wx, y: wy };
    if (it) { pushHistory(); state.items.push(it); state.selected = it.id; draw(); emit(); }
  }

  function emit() {
    const issues = validate();
    if (state.onChange) state.onChange({ issues, count: state.items.length,
      calibration: state.scaleMPerPx });
  }

  function draft() {
    return {
      schema_version: '1.0', kind: 'layout_spec_draft', status: 'DRAFT_ONLY',
      source: 'DC Twin Studio visual editor (G1)',
      calibration: state.scaleMPerPx ? { meters_per_px: state.scaleMPerPx,
        points: state.calibPoints, meters: state.calibMeters } : null,
      site: { width: state.data.layout.size[0], depth: state.data.layout.size[1] },
      entities: state.items,
      existing_reference: { warehouses: state.data.layout.warehouses.map(w => w.id),
        racks: state.data.layout.warehouses.reduce((a, w) => a + w.racks.length, 0) },
      issues: validate(),
      next_step: '人工核对 → 生成 layout_spec.json → spec_layout L1–L12 校验 → 显式保存',
      never_overwrites: 'config/layout_spec.json',
    };
  }

  function init(canvas, onChange) {
    state.canvas = canvas; state.ctx = canvas.getContext('2d');
    state.data = window.DC_SUBMISSION; state.onChange = onChange;
    state.W = canvas.width; state.H = canvas.height;
    reset();

    canvas.addEventListener('mousedown', (e) => {
      const r = canvas.getBoundingClientRect();
      const px = (e.clientX - r.left) * canvas.width / r.width;
      const py = (e.clientY - r.top) * canvas.height / r.height;
      if (state.tool === 'select') {
        const it = hit(px, py);
        state.selected = it ? it.id : null;
        if (it) pushHistory();
        state.drag = it ? { id: it.id, dx: 0, dy: 0 } : null;
        draw();
      } else if (state.tool === 'delete') {
        const it = hit(px, py);
        if (it) { pushHistory(); state.items = state.items.filter(x => x !== it);
          state.selected = null; draw(); emit(); }
      } else if (state.calibPoints.length < 2 && state.bgLoaded) {
        state.calibPoints.push([px, py]); draw(); emit();
      } else {
        addAt(px, py);
      }
    });
    canvas.addEventListener('mousemove', (e) => {
      if (!state.drag) return;
      const r = canvas.getBoundingClientRect();
      const px = (e.clientX - r.left) * canvas.width / r.width;
      const py = (e.clientY - r.top) * canvas.height / r.height;
      const it = state.items.find(x => x.id === state.drag.id);
      if (!it) return;
      const [wx, wy] = screenToWorld(px, py);
      if (it.type === 'zone') {
        const w = it.rect[2] - it.rect[0], h = it.rect[3] - it.rect[1];
        it.rect = [wx - w / 2, wy - h / 2, wx + w / 2, wy + h / 2];
      } else { it.x = wx; it.y = wy; }
      draw();
    });
    window.addEventListener('mouseup', () => { if (state.drag) { state.drag = null; emit(); } });
    window.addEventListener('keydown', (e) => {
      const map = { v: 'select', z: 'zone', r: 'rack', d: 'door', h: 'home', x: 'delete' };
      const k = e.key.toLowerCase();
      if (map[k] && document.activeElement.tagName !== 'INPUT') setTool(map[k]);
      if ((e.key === 'Delete' || e.key === 'Backspace') && state.selected) {
        pushHistory(); state.items = state.items.filter(x => x.id !== state.selected);
        state.selected = null; draw(); emit();
      }
    });
    return api;
  }

  function setTool(t) {
    state.tool = t;
    document.querySelectorAll('.tool-btn').forEach(b =>
      b.classList.toggle('active', b.dataset.tool === t));
  }

  function setBackground(file, meters) {
    const reader = new FileReader();
    reader.onload = () => {
      const img = new Image();
      img.onload = () => { state.bg = img; state.bgLoaded = true;
        state.calibPoints = []; state.scaleMPerPx = null; draw(); emit(); };
      img.src = reader.result;
    };
    reader.readAsDataURL(file);
    state.calibMeters = Number(meters) || 100;
  }

  function calibrate(meters) {
    state.calibMeters = Number(meters) || 100;
    if (state.calibPoints.length === 2) {
      const d = Math.hypot(state.calibPoints[0][0] - state.calibPoints[1][0],
        state.calibPoints[0][1] - state.calibPoints[1][1]);
      state.scaleMPerPx = d > 0 ? +(state.calibMeters / d).toFixed(6) : null;
    }
    draw(); emit();
    return state.scaleMPerPx;
  }

  const api = { init, reset, undo, setTool, setBackground, calibrate, draft,
    get issues() { return validate(); },
    get calibration() { return state.scaleMPerPx; },
    get calibPoints() { return state.calibPoints.length; },
    TOOLS };
  window.DCEditor = api;
})();
