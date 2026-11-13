/* 功能区（areas）：2D/3D 可视化 + 自定义扩仓设计器
 *
 * 为什么单独一个文件：功能区的数据、配色、交互三处都要用（2D 沙盘画层、3D 覆盖层、
 * 右侧面板），塞进 app.js 会让那个文件继续膨胀；这里只在 app.js 里留 3 个钩子。
 *
 * 数据源：
 *   /api/areas          当前布局的 21 个区（物理区有 rect，逻辑区有 anchor→xy）
 *   /api/areas/catalog  14 个模板 + 预设 + W 仓深度上限
 *   /api/areas/plan     POST 选中模板 → 排布/深度/产能（纯计算，不写文件）
 *   /api/areas/apply    POST 写 selection 并生成扩仓变体
 */
'use strict';

const KIND_COLOR = {
  RECEIVING: '#2563eb', QA: '#0891b2', STAGING_IN: '#0ea5e9', STORAGE: '#64748b',
  PICK_FACE: '#f59e0b', REPLENISH: '#d97706', SORTING: '#7c3aed', PACKING: '#db2777',
  VAS: '#9333ea', STAGING_OUT: '#16a34a', RETURNS: '#dc2626',
  EMPTY_CONTAINER: '#78716c', BATTERY: '#0d9488', MAINTENANCE: '#475569', OFFICE: '#6b7280',
};
const KIND_CN = {
  RECEIVING: '收货', QA: '质检', STAGING_IN: '入库暂存', STORAGE: '存储',
  PICK_FACE: '拣选面', REPLENISH: '补货', SORTING: '分拣', PACKING: '包装',
  VAS: '增值', STAGING_OUT: '出库暂存', RETURNS: '退货', EMPTY_CONTAINER: '空容器',
  BATTERY: '电池间', MAINTENANCE: '维修', OFFICE: '办公',
};

const A = {
  browse: null,        // /api/areas
  catalog: null,       // /api/areas/catalog
  plan: null,          // /api/areas/plan 结果
  enabled: null,       // Set，自定义模式下的勾选
  mode: 'browse',
  filter: null,        // 只看某类
  hover: null,         // 2D 悬停/高亮的 area id
  show: true,
  visible3d: true,
  busy: false,
  // D10a/D12 草稿：位移只活在内存里，服务端回一个 token，提交时必须原样带回。
  // 拖动过程中**不写任何文件**——这是"拖完直接落盘"和"显式提交"的分界线。
  draft: { moves: {}, token: null, ok: null, issues: 0, drag: null, dirty: false },
};

const areaColor = (k) => KIND_COLOR[k] || '#94a3b8';

// ---------------------------------------------------------------- 2D 绘制

/** 在 2D 沙盘上画功能区。坐标为世界坐标系（米），X/Y/L 由 app.js 提供。 */
function areasDraw2d(ctx) {
  if (!(S.show && S.show.areas) || !A.browse) return;
  const areas = A.browse.areas || [];
  const zoom = S.view.scale;
  const showPhys = true;        // 物理区永远带名：它们是真占地
  // 逻辑区在 W 仓里很密（12 个），缩到全局时会糊成一团，所以只有放大到 ~1.6 倍
  // （scale > 11 px/m）才显示名称；前置仓每个只有 3 个区，任何时候都显示。
  const zoomed = zoom > 11;
  ctx.save();
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';

  // 物理区：矩形 + 斜纹 + 名称（真占地，必须一眼看出来）
  for (const a of areas) {
    if (!a.rect) continue;
    const [x0, z0, x1, z1] = a.rect;
    const px = X(x0), py = Y(z0), pw = L(x1 - x0), ph = L(z1 - z0);
    const c = areaColor(a.kind);
    const hot = A.hover === a.id;
    ctx.globalAlpha = hot ? 0.42 : 0.26;
    ctx.fillStyle = c;
    ctx.fillRect(px, py, pw, ph);
    ctx.globalAlpha = 1;
    ctx.strokeStyle = c;
    ctx.lineWidth = hot ? 2 : 1.2;
    ctx.setLineDash([5, 3]);
    ctx.strokeRect(px + 0.5, py + 0.5, pw - 1, ph - 1);
    ctx.setLineDash([]);
    if (showPhys && pw > 26 && ph > 12) {
      ctx.font = '600 10px -apple-system, "Microsoft YaHei", sans-serif';
      const label = a.name || a.id;
      const w = ctx.measureText(label).width;
      ctx.fillStyle = 'rgba(255,255,255,.86)';
      ctx.fillRect(px + pw / 2 - w / 2 - 3, py + ph / 2 - 7, w + 6, 14);
      ctx.fillStyle = c;
      ctx.fillText(label, px + pw / 2, py + ph / 2);
    }
  }

  // 草稿叠加：拖动时同时画"原位置虚影"和"现位置实线"，否则用户看不出挪了多远
  if (A.mode === 'custom' && A.plan && A.plan.placed) {
    for (const p of A.plan.placed) {
      if (!A.draft.moves[p.template] && !(A.draft.drag && A.draft.drag.tpl === p.template)) {
        continue;
      }
      const r = p.rect;
      const px = X(r[0]), py = Y(r[1]), pw = L(r[2] - r[0]), ph = L(r[3] - r[1]);
      ctx.globalAlpha = 0.85;
      ctx.strokeStyle = '#111827';
      ctx.lineWidth = 2;
      ctx.strokeRect(px + 0.5, py + 0.5, pw - 1, ph - 1);
      ctx.globalAlpha = 1;
      const r0 = (A.draft.drag && A.draft.drag.rect0) || null;
      if (r0 && A.draft.drag.tpl === p.template) {
        ctx.setLineDash([4, 4]);
        ctx.strokeStyle = '#9ca3af';
        ctx.lineWidth = 1;
        ctx.strokeRect(X(r0[0]) + 0.5, Y(r0[1]) + 0.5,
                       L(r0[2] - r0[0]) - 1, L(r0[3] - r0[1]) - 1);
        ctx.setLineDash([]);
      }
    }
  }

  // 逻辑区：锚点处的圆环 + 引线标签（没有 rect，只挂靠实体）
  for (const a of areas) {
    if (a.rect || !a.xy) continue;
    const [wx, wz] = a.xy;
    const cx = X(wx), cy = Y(wz);
    if (cx < -30 || cx > C.w + 30 || cy < -30 || cy > C.h + 30) continue;
    const c = areaColor(a.kind);
    const hot = A.hover === a.id;
    ctx.beginPath();
    ctx.arc(cx, cy, hot ? 6.5 : 5, 0, Math.PI * 2);
    ctx.fillStyle = 'rgba(255,255,255,.9)';
    ctx.fill();
    ctx.lineWidth = hot ? 2.4 : 1.6;
    ctx.strokeStyle = c;
    ctx.stroke();
    ctx.beginPath();
    ctx.arc(cx, cy, 2, 0, Math.PI * 2);
    ctx.fillStyle = c;
    ctx.fill();
    if (zoomed || a.warehouse !== 'W') {
      const label = a.name || a.id;
      ctx.font = '600 9.5px -apple-system, "Microsoft YaHei", sans-serif';
      const w = ctx.measureText(label).width;
      const lx = cx, ly = cy - 12;
      ctx.strokeStyle = c;
      ctx.globalAlpha = 0.55;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(cx, cy - 5);
      ctx.lineTo(lx, ly + 6);
      ctx.stroke();
      ctx.globalAlpha = 1;
      ctx.fillStyle = 'rgba(255,255,255,.9)';
      ctx.fillRect(lx - w / 2 - 3, ly - 7, w + 6, 13);
      ctx.fillStyle = c;
      ctx.fillText(label, lx, ly);
    }
  }
  // 自定义模式：把"生成后的变体"叠一层实线，能直接看出扩到哪、和现状差多少
  if (A.mode === 'custom' && A.variant && (A.variant.areas || []).length) {
    ctx.setLineDash([]);
    for (const a of A.variant.areas) {
      if (!a.rect) continue;
      const [x0, z0, x1, z1] = a.rect;
      const px = X(x0), py = Y(z0), pw = L(x1 - x0), ph = L(z1 - z0);
      ctx.fillStyle = areaColor(a.kind) + '33';
      ctx.fillRect(px, py, pw, ph);
      ctx.strokeStyle = areaColor(a.kind);
      ctx.lineWidth = 1.8;
      ctx.strokeRect(px + 1, py + 1, pw - 2, ph - 2);
      if (pw > 24 && ph > 10) {
        ctx.font = '700 9.5px -apple-system, "Microsoft YaHei", sans-serif';
        ctx.fillStyle = areaColor(a.kind);
        ctx.fillText(a.name || a.id, px + pw / 2, py + ph / 2);
      }
    }
    ctx.font = '600 11px -apple-system, "Microsoft YaHei", sans-serif';
    ctx.fillStyle = '#0f172a';
    ctx.textAlign = 'left';
    ctx.fillText('实线 = 扩仓变体（' + A.variant.areas.length + ' 区）', 16, C.h - 16);
    ctx.textAlign = 'center';
  }
  ctx.restore();
}

/** 2D 命中测试：返回鼠标下的功能区（物理区优先）。供点击/hover 用。 */
function areasHit2d(wx, wz) {
  if (!A.browse) return null;
  for (const a of A.browse.areas || []) {
    if (a.rect) {
      const [x0, z0, x1, z1] = a.rect;
      if (wx >= x0 && wx <= x1 && wz >= z0 && wz <= z1) return a;
    }
  }
  let best = null, bd = 1e9;
  for (const a of A.browse.areas || []) {
    if (a.rect || !a.xy) continue;
    const d = Math.hypot(a.xy[0] - wx, a.xy[1] - wz);
    if (d < bd) { bd = d; best = a; }
  }
  return bd < 4 ? best : null;
}

// ---------------------------------------------------------------- 面板

async function areasOpen() {
  if (!A.browse) {
    try {
      A.browse = await apiGet('/api/areas');
    } catch (e) {
      $('pane-areas').innerHTML = '<div class="pane-sub">加载失败：' + esc(e.message) + '</div>';
      return;
    }
  }
  areasRenderBadges();
  areasRenderList();
  // 深链接：?tab=areas&areas=custom 直接打开自定义（截图/分享都好用）
  const q = new URLSearchParams(location.search).get('areas');
  if (q === 'custom') areasMode('custom');
}

function areasRenderBadges() {
  const s = (A.browse && A.browse.summary) || {};
  const areas = (A.browse && A.browse.areas) || [];
  const chip = (t, v, c) => `<span class="badge" style="--c:${c}">${t} <b>${v}</b></span>`;
  $('areaBadges').innerHTML =
    chip('功能区', s.total ?? areas.length, '#0ea5e9') +
    chip('物理', s.physical ?? '—', '#f59e0b') +
    chip('逻辑', s.logical ?? '—', '#7c3aed') +
    chip('可算产能', s.with_throughput ?? '—', '#16a34a');

  // 类型筛选：点一下只看这一类
  const kinds = [...new Set(areas.map((a) => a.kind))];
  $('areaFilter').innerHTML = kinds.map((k) => {
    const on = A.filter === k;
    return `<button class="pill-btn${on ? ' on' : ''}" data-kind="${k}"
      style="--c:${areaColor(k)}">${KIND_CN[k] || k}</button>`;
  }).join('');
  [...$('areaFilter').children].forEach((b) => b.addEventListener('click', () => {
    A.filter = A.filter === b.dataset.kind ? null : b.dataset.kind;
    areasRenderBadges();
    areasRenderList();
    S.dirty = true;
  }));
}

function areasRenderList() {
  const all = (A.browse && A.browse.areas) || [];
  const rows = A.filter ? all.filter((a) => a.kind === A.filter) : all;
  $('areaList').innerHTML = rows.map((a) => {
    const c = areaColor(a.kind);
    const cap = Object.entries(a.capacity || {}).filter(([, v]) => v)
      .map(([k, v]) => `${k}=${v}`).join(' · ');
    const thr = a.derived
      ? `${num(a.derived.throughput_per_h)} /h` : '—';
    const where = a.rect
      ? `${num(a.area_m2)} m² · ${a.rect.join(',')}`
      : `挂靠 ${esc(a.anchor || '')}（${a.anchor_how || a.anchor_resolved || '?'}）`;
    return `<div class="area-row${A.hover === a.id ? ' hot' : ''}" data-id="${a.id}"
        style="--c:${c}">
      <div class="area-head">
        <code>${esc(a.id)}</code>
        <span class="pill" style="--c:${c}">${KIND_CN[a.kind] || a.kind}</span>
        <span class="muted">${a.physical ? '物理' : '逻辑'}</span>
        <b class="area-thr">${thr}</b>
      </div>
      <div class="muted area-sub">${esc(a.name || '')} · ${where}</div>
      ${cap ? `<div class="muted area-sub">容量 ${esc(cap)}</div>` : ''}
    </div>`;
  }).join('') || '<div class="muted" style="padding:8px">没有匹配的功能区</div>';

  [...$('areaList').children].forEach((row) => {
    const id = row.dataset.id;
    row.addEventListener('mouseenter', () => { A.hover = id; S.dirty = true; });
    row.addEventListener('mouseleave', () => { A.hover = null; S.dirty = true; });
    row.addEventListener('click', () => areasFocus(id));
  });
}

/** 3D 里点中某个区：只更新左上角信息，不切视图（切走会丢掉当前视角）。 */
function areasHud(id) {
  const a = ((A.browse && A.browse.areas) || []).find((x) => x.id === id);
  if (!a) return;
  A.hover = id;
  const box = $('hud');
  if (box) {
    box.innerHTML = `<b>${esc(a.name || a.id)}</b> · ${KIND_CN[a.kind] || a.kind}
      · ${a.rect ? num(a.area_m2) + ' m²' : '挂靠 ' + esc(a.anchor || '')}
      ${a.derived ? ' · ' + num(a.derived.throughput_per_h) + '/h' : ''}`;
  }
  areasRenderList();
}

/** 定位：切到 2D，把视野摆到该区，并高亮。 */
function areasFocus(id) {
  const a = ((A.browse && A.browse.areas) || []).find((x) => x.id === id);
  if (!a || !a.xy) return;
  if (S.mode3d) switchView('2d');
  A.hover = id;
  const span = 34;
  const s = Math.min(C.w, C.h) / span;
  S.view.scale = s;
  S.view.ox = C.w / 2 - a.xy[0] * s;
  S.view.oy = C.h / 2 - a.xy[1] * s;
  S.dirty = true;
  const box = $('hud');
  if (box) box.innerHTML = `<b>${esc(a.name || a.id)}</b> · ${KIND_CN[a.kind] || a.kind}
    · ${a.rect ? num(a.area_m2) + ' m²' : '挂靠 ' + esc(a.anchor || '')}`;
}

// ------------------------------------------------ 自定义（扩仓设计器）

function areasMode(mode) {
  A.mode = mode;
  [...$('areaMode').children].forEach((b) => b.classList.toggle('on', b.dataset.mode === mode));
  $('areaBrowse').hidden = mode !== 'browse';
  $('areaCustom').hidden = mode !== 'custom';
  if (mode === 'custom') {
    areasOpenCustom();
    if (!A.variant) {
      apiGet('/api/areas/variant').then((v) => {
        A.variant = v;
        if (v.exists) S.dirty = true;
      }).catch(() => {});
    }
  }
  S.dirty = true;
}

async function areasOpenCustom() {
  if (!A.catalog) {
    try {
      A.catalog = await apiGet('/api/areas/catalog');
    } catch (e) {
      $('tplGroups').innerHTML = '<div class="muted">加载模板失败：' + esc(e.message) + '</div>';
      return;
    }
    const sel = (A.catalog.selection && A.catalog.selection.enabled) || [];
    A.enabled = new Set(sel.length ? sel : (A.catalog.presets.ecommerce || []));
  }
  areasRenderTemplates();
  areasRenderDraft();
  await areasPlan();
}

function areasRenderTemplates() {
  const tpls = A.catalog.templates || [];
  const groups = {};
  for (const t of tpls) (groups[t.group || '其他'] ||= []).push(t);
  const order = ['入库', '拣选', '出库', '逆向', '支持', '其他'];
  $('tplGroups').innerHTML = order.filter((g) => groups[g]).map((g) => `
    <div class="tpl-group">
      <div class="tpl-group-head">${g}</div>
      ${groups[g].map((t) => `
        <label class="tpl-row" data-id="${t.template}">
          <input type="checkbox" ${A.enabled.has(t.template) ? 'checked' : ''}>
          <span class="tpl-name">${esc(t.name)}</span>
          <code>${t.template}</code>
          <span class="muted">${t.size[0]}×${t.size[1]} m</span>
          <b class="tpl-thr">${t.throughput_per_h ?? '—'}/h</b>
        </label>`).join('')}
    </div>`).join('');

  $('tplGroups').querySelectorAll('input').forEach((cb) => {
    cb.addEventListener('change', () => {
      const id = cb.closest('.tpl-row').dataset.id;
      cb.checked ? A.enabled.add(id) : A.enabled.delete(id);
      areasPlan();
    });
  });
}

let planTimer = null;
function areasPlan() {
  clearTimeout(planTimer);
  planTimer = setTimeout(async () => {
    const enabled = [...A.enabled];
    try {
      A.plan = await apiPost('/api/areas/plan',
                             { enabled, moves: A.draft.moves });
      A.draft.token = A.plan.token || null;
      A.draft.ok = !!A.plan.ok;
      A.draft.issues = (A.plan.placed && A.plan.ok) ? 0 : 1;
    } catch (e) {
      $('areaSum').innerHTML = '<span class="bad">计算失败：' + esc(e.message) + '</span>';
      return;
    }
    areasRenderSum();
  }, 120);
}

function areasRenderSum() {
  const p = A.plan;
  if (!p) return;
  if (!p.placed) {           // 静态快照里 plan 是被拒绝的，不能当正常结果渲染
    $('areaSum').innerHTML =
      '<div class="warn">这是只读快照：能看能勾，生成变体要回本地应用（python dc_app.pyw）。</div>';
    return;
  }
  const d = p.depth || {};
  const lim = d.limit == null ? '—' : num(d.limit);
  const over = p.ok ? '' : ' bad';
  $('areaSum').innerHTML = `
    <div class="sum-row"><span>已选</span><b>${p.totals.count} 个</b>
      <span class="muted">占地 ${num(p.totals.area_m2)} m² · 产能 ${num(p.totals.throughput_per_h)}/h</span></div>
    <div class="sum-row"><span>W 仓深度</span>
      <b class="${over}">${num(d.base)} → ${num(d.needed)} m</b>
      <span class="muted">上限 ${lim} m${p.ok ? '' : ' · 超出'}</span></div>
    <div class="sum-row"><span>作业带</span>
      <span class="muted">x ${num(p.band.x0)}–${num(p.band.x1)} m · z 从 ${num(p.band.z0)} m 起</span></div>
    ${p.ok ? '' : '<div class="warn">放不下：先取消一两个区，或把前置仓整体北移（计划书 §12.6）</div>'}`;
}

// ---------------------------------------------------------------- 拖拽（D10a）

function _rectHas(r, wx, wz) {
  return r && wx >= r[0] && wx <= r[2] && wz >= r[1] && wz <= r[3];
}

function areasBeginDrag(wx, wz) {
  if (A.mode !== 'custom' || !A.plan || !A.plan.placed) return false;
  const hit = (A.plan.placed || []).find((p) => _rectHas(p.rect, wx, wz));
  if (!hit) return false;
  A.draft.drag = {
    tpl: hit.template, wx, wz,
    base: (A.draft.moves[hit.template] || [0, 0]).slice(),
    rect0: hit.rect.slice(),     // 原矩形：拖动时画成虚影，用户知道从哪儿来
    item: hit,
  };
  return true;
}

function areasDragTo(wx, wz) {
  const d = A.draft.drag;
  if (!d) return;
  const dx = +(d.base[0] + (wx - d.wx)).toFixed(2);
  const dz = +(d.base[1] + (wz - d.wz)).toFixed(2);
  A.draft.moves[d.tpl] = [dx, dz];
  // 先本地跟手（不等服务端），松手后再以服务端结果为准
  d.item.rect = [d.rect0[0] + dx, d.rect0[1] + dz,
                 d.rect0[2] + dx, d.rect0[3] + dz];
  A.draft.dirty = true;
  A.draft.token = null;          // 位移一变，旧令牌立即作废（D12）
  areasRenderDraft();
  S.dirty = true;
}

function areasEndDrag() {
  const d = A.draft.drag;
  A.draft.drag = null;
  if (!d) return;
  if (!A.draft.dirty) { S.dirty = true; return; }
  A.draft.dirty = false;
  areasPlan();                   // 松手才向服务端要权威矩形与 token
}

function areasResetMoves() {
  A.draft.moves = {};
  A.draft.token = null;
  A.plan = null;
  areasPlan();
  areasRenderDraft();
}

function areasRenderDraft() {
  const el = $('areaDraft');
  if (!el) return;
  const n = Object.keys(A.draft.moves).length;
  const tok = A.draft.token;
  const span = el.querySelector('span');
  const btn = $('btnAreaCommit');
  if (span) {
    span.textContent = n
      ? (tok ? `草稿：已挪 ${n} 个区 · 已校验（${tok}）` : `草稿：已挪 ${n} 个区 · 未校验`)
      : '草稿：未改动';
    span.className = n && !tok ? 'bad' : 'muted';
  }
  if (btn) btn.disabled = !(tok && A.draft.ok);
}

async function areasValidate() {
  const btn = $('btnAreaValidate');
  btn.disabled = true;
  $('areaLog').textContent = '校验中（布局 L2–L6 + 功能区 L23–L26）…';
  try {
    const r = await apiPost('/api/areas/plan',
                            { enabled: [...A.enabled], moves: A.draft.moves });
    A.draft.token = r.token || null;
    A.draft.ok = !!r.ok;
    A.draft.issues = (r.issues || []).length;
    $('areaLog').textContent = r.ok
      ? '校验通过：0 issue，令牌 ' + A.draft.token + '\n>> 现在可以「提交布局」'
      : '校验未通过：' + (r.error || (r.issues || []).length + ' 条 issue') +
        '\n>> 拖回去或点「重置位移」';
  } catch (e) {
    $('areaLog').textContent = '校验失败：' + e.message;
  } finally {
    btn.disabled = false;
    areasRenderDraft();
  }
}

async function areasCommit() {
  const btn = $('btnAreaCommit');
  btn.disabled = true;
  $('areaLog').textContent = '提交中…';
  try {
    const r = await apiPost('/api/areas/commit',
                            { enabled: [...A.enabled], moves: A.draft.moves,
                              token: A.draft.token });
    $('areaLog').textContent = '已提交：' + (r.spec || '') +
      '\n>> 草稿已落盘，可继续拖动再提交';
  } catch (e) {
    // 409 带 code：说明是哪道闸拦下的，别把四种拒绝糊成一句话
    $('areaLog').textContent = '提交被拒：' + e.message;
  } finally {
    areasRenderDraft();
  }
}

async function areasApply() {
  if (A.busy) return;
  A.busy = true;
  const btn = $('btnAreaApply');
  btn.disabled = true;
  $('areaLog').textContent = '正在生成变体 spec 并做几何校验…';
  try {
    const r = await apiPost('/api/areas/apply', { enabled: [...A.enabled] });
    $('areaLog').textContent = (r.log || []).join('\n') ||
      (r.ok ? '完成' : '失败：' + (r.error || ''));
    if (r.ok) {
      $('areaLog').textContent += '\n>> 已写入 ' + r.spec +
        '\n>> 2D 沙盘会叠加显示"扩仓变体"的功能区（虚线框）';
      A.variant = await apiGet('/api/areas/variant');
      S.dirty = true;
    }
  } catch (e) {
    $('areaLog').textContent = '失败：' + e.message;
  } finally {
    A.busy = false;
    btn.disabled = false;
  }
}

function areasPreset(name) {
  const set = (A.catalog.presets || {})[name] || [];
  A.enabled = new Set(set);
  areasRenderTemplates();
  areasPlan();
}

function areasToggleAll(on) {
  A.enabled = on ? new Set(A.catalog.templates.map((t) => t.template)) : new Set();
  areasRenderTemplates();
  areasPlan();
}

// ---------------------------------------------------------------- 3D 覆盖层

/** 给 3D 查看器的覆盖数据（物理区当薄板，逻辑区当圆点）。 */
function areasFor3d() {
  const list = (A.browse && A.browse.areas) || [];
  return list.map((a) => ({
    id: a.id, kind: a.kind, name: a.name, color: areaColor(a.kind),
    rect: a.rect || null, xy: a.xy || null, physical: !!a.rect,
    m2: a.area_m2 || null,
  }));
}
