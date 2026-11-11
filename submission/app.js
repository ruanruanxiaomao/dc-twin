/* DC TWIN STUDIO · activity submission app (zero backend, zero dependency)
 * Every number on this page is inlined from real project artifacts by
 * scripts/build_submission.py. The 3D view is a self-contained software
 * projection renderer (twin3d.js), not a Blender real-time viewport. */
(function () {
  'use strict';
  const D = window.DC_SUBMISSION;
  const $ = id => document.getElementById(id);
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g,
    c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const fmt = (n, d) => (typeof n === 'number' ? n.toFixed(d === undefined ? 2 : d) : '—');
  const money = n => (typeof n === 'number' ? '¥' + n.toFixed(2) : '—');
  const short = (o, n) => { const s = typeof o === 'string' ? o : JSON.stringify(o);
    return s == null ? '—' : (s.length > n ? s.slice(0, n) + '…' : s); };
  const table = (heads, rows) => '<thead><tr>' + heads.map(h =>
    '<th' + (h.num ? ' class="num"' : '') + '>' + h.t + '</th>').join('') + '</tr></thead><tbody>' +
    rows.map(r => '<tr>' + r.map(c => '<td' + (c && c.num ? ' class="num"' : '') + '>' +
      (c && c.html !== undefined ? c.html : esc(c && c.t !== undefined ? c.t : c)) + '</td>').join('') +
      '</tr>').join('') + '</tbody>';

  // ---------------------------------------------------------------- palette / theme
  const CV_MAP = {
    sky0: 'sky-0', sky1: 'sky-1', ground: 'ground', groundEdge: 'ground-edge',
    grid: 'grid', warehouseFill: 'warehouse-fill', warehouseEdge: 'warehouse-edge',
    rack: 'rack', rackAlt: 'rack-alt', rackTop: 'rack-top', rackBand: 'rack-band',
    dockIn: 'dock-in', dockOut: 'dock-out', home: 'home', shadow: 'shadow',
    agvDeck: 'agv-deck', agvDeckTop: 'agv-deck-top', lamp: 'lamp',
    cargo: 'cargo', cargoTop: 'cargo-top', labelBg: 'label-bg',
    labelEdge: 'label-edge', labelInk: 'label-ink', hud: 'hud', accent: 'accent'
  };
  function cssPal() {
    const cs = getComputedStyle(document.documentElement), p = {};
    Object.keys(CV_MAP).forEach(k => {
      p[k] = cs.getPropertyValue('--cv-' + CV_MAP[k]).trim() || '#888888';
    });
    return p;
  }
  let PAL = {};
  let savedTheme = null;
  try { savedTheme = localStorage.getItem('dc-theme'); } catch (e) { /* file:// may block */ }
  if (savedTheme === 'light') document.documentElement.dataset.theme = 'light';
  PAL = cssPal();
  function syncThemeButton() {
    $('btnTheme').textContent = document.documentElement.dataset.theme === 'dark' ? '浅色' : '深色';
  }
  syncThemeButton();
  $('btnTheme').onclick = () => {
    const toLight = document.documentElement.dataset.theme === 'dark';
    document.documentElement.dataset.theme = toLight ? 'light' : 'dark';
    try { localStorage.setItem('dc-theme', toLight ? 'light' : 'dark'); } catch (e) { /* noop */ }
    syncThemeButton();
    PAL = cssPal();
    twin.setPalette(PAL);
    if (window.__mediaView) window.__mediaView.setPalette(PAL);
    if (window.DCEditor && window.DCEditor.setPalette) window.DCEditor.setPalette(PAL);
    if (window.__editorReady) window.DCEditor.redraw && window.DCEditor.redraw();
    renderCurrent();
  };

  // ---------------------------------------------------------------- tabs
  let activeTab = 'home';
  function switchTab(name) {
    if (!$('pane-' + name)) return;
    activeTab = name;
    document.querySelectorAll('#nav button').forEach(b =>
      b.classList.toggle('active', b.dataset.tab === name));
    document.querySelectorAll('.pane').forEach(p =>
      p.classList.toggle('active', p.id === 'pane-' + name));
    if (name === 'editor' && !window.__editorReady) initEditor();
    if (name === 'media' && !window.__mediaReady) initMedia();
    if (name === 'twin') twin.markDirty();
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }
  $('nav').addEventListener('click', e => {
    const b = e.target.closest('button[data-tab]'); if (b) switchTab(b.dataset.tab);
  });
  document.querySelectorAll('[data-go]').forEach(b =>
    b.addEventListener('click', () => switchTab(b.dataset.go)));

  // ---------------------------------------------------------------- header / hero
  $('topBadges').innerHTML =
    '<span class="badge">engine: sim</span>' +
    '<span class="badge warn">prices: demo</span>' +
    '<span class="badge gray">零后端 · 可离线</span>';
  $('genAt').textContent = D.generated_at.replace('T', ' ').replace('+00:00', ' UTC');
  $('heroDesc').textContent = D.project.description;
  $('heroImg').src = D.project.hero_image;
  $('heroNote').textContent = '数据快照生成于 ' + D.generated_at +
    '；本页展示的全部指标、几何、事件与资产均来自项目真实产物，评审可用仓库内脚本独立复算。';
  const s = D.project.stats;
  $('heroStats').innerHTML = [
    ['仓', s.warehouses], ['货架', s.racks], ['库位', s.locations], ['AGV', s.robots],
  ].map(([k, v]) => '<div><b>' + v + '</b><small>' + k + '</small></div>').join('');
  $('homeFlow').innerHTML = D.architecture.map(a =>
    '<div class="flow-node"><b>' + esc(a.n) + ' / ' + esc(a.icon) + '</b><strong>' +
    esc(a.title) + '</strong><span>' + esc(a.out.split(' · ')[0]) + '</span></div>').join('');

  const features = [
    ['01 / 布局', '单一真相源', '四层 JSON 配置 → spec_layout 展开 + L1–L13 校验，生成 4 仓 196 货架 441 库位，冻结基线逐字节可复现。'],
    ['02 / 仿真', 'tick 级 DES', '订单持续到达、在线派单、9 态 FSM、电量与充电、故障重派、反应式门控；8 个场景真实执行，39 项 measured KPI。'],
    ['03 / 盈亏', '事件溯源账本', '仿真事件 → 分录 → 损益表、单均成本、盈亏平衡与敏感性；F1–F7 独立复算，演示价格强制徽章。'],
    ['04 / 定制', '不改 Python', '可视化场景编排、布局拖拽编辑、DXF/LIF 草稿导入、fleet_spec 容量守卫、WMS 只读适配器框架。'],
  ];
  $('featureGrid').innerHTML = features.map(([n, t, p]) =>
    '<div class="feature"><div class="num">' + n + '</div><h4>' + t + '</h4><p>' + p + '</p></div>').join('');
  $('tourList').innerHTML = [
    '在「实时孪生」用可旋转的 3D 立体场景看 21 台 AGV 沿真实关键帧完成 HOME→取货→交付→回位闭环。',
    '在「项目全景」看六环节数据链、四层配置、数据契约与 57 个真实交付物。',
    '在「模型与渲染」播放 Blender 动画、查看 3D 模型审计数字与 10 张渲染图，并下载 .blend / .glb / .mp4。',
    '在「场景实验室」「运营盈亏」核对 8 场景 KPI、损益瀑布、账本分录与敏感性。',
    '在「布局编辑」拖一拖实体、上传平面图做两点标定，导出草稿 JSON。',
    '在「验收证据」逐条核对 A1–A40 / S1–S9 / F1–F7、27 项 KPI 定义与真实事件流。',
  ].map(x => '<li>' + x + '</li>').join('');

  // ---------------------------------------------------------------- project map
  $('archGrid').innerHTML = D.architecture.map(a =>
    '<div class="arch-card"><div class="arch-head"><span class="arch-n">' + esc(a.n) +
    '</span><h4>' + esc(a.title) + ' · ' + esc(a.icon) + '</h4></div>' +
    '<div class="arch-io">IN &nbsp;<b>' + esc(a.in) + '</b><br>OUT <b>' + esc(a.out) +
    '</b></div><p>' + esc(a.body) + '</p></div>').join('');
  const specNotes = [
    ['layout_spec.json', '布局层', '仓库 / 货架 / 库位 / 门 / 分区 / 待命位；L1–L13 校验'],
    ['fleet_spec.json', '车队层', '车型、电量、速度、部署数量；L13 超配守卫'],
    ['demand_spec.json', '需求层', '到达率、5 类业务混合比、优先级与时效'],
    ['sim_spec.json', '仿真层', 'tick、horizon、调度、交通、电量、故障与输出契约'],
  ];
  $('specGrid').innerHTML = specNotes.map(([f, t, p]) => {
    const item = D.deliverables.items.find(x => x.path === 'config/' + f);
    return '<div class="spec-card"><b>' + esc(f) + ' · ' + (item ? item.size : '—') +
      '</b><h4>' + t + '</h4><p>' + p + '</p></div>';
  }).join('');
  $('contractTable').innerHTML = table(
    [{ t: '文件' }, { t: '行数', num: true }, { t: '体积', num: true }, { t: '首行字段 / 结构' }],
    D.contracts.map(c => [c.file, { t: c.lines, num: true }, { t: c.size, num: true },
      { html: '<span class="mono">' + esc(short(c.fields, 90)) + '</span>' }]));
  $('fileCount').textContent = D.deliverables.count;
  let fileGroup = '全部';
  $('fileChips').innerHTML = ['全部'].concat(D.deliverables.groups).map(g =>
    '<button class="chip' + (g === '全部' ? ' active' : '') + '" data-g="' + esc(g) + '">' +
    esc(g) + '</button>').join('');
  function renderFiles() {
    const q = ($('fileSearch').value || '').toLowerCase();
    const rows = D.deliverables.items.filter(x =>
      (fileGroup === '全部' || x.group === fileGroup) &&
      (!q || (x.path + ' ' + x.note).toLowerCase().includes(q)));
    $('fileTable').innerHTML = table(
      [{ t: '分组' }, { t: '文件' }, { t: '说明' }, { t: '体积', num: true }],
      rows.map(x => [{ html: '<span class="tag">' + esc(x.group) + '</span>' },
        { html: '<code>' + esc(x.path) + '</code>' }, esc(x.note),
        { t: x.size, num: true }]));
  }
  renderFiles();
  $('fileSearch').oninput = renderFiles;
  $('fileChips').addEventListener('click', e => {
    const b = e.target.closest('[data-g]'); if (!b) return;
    fileGroup = b.dataset.g;
    document.querySelectorAll('#fileChips .chip').forEach(c =>
      c.classList.toggle('active', c === b));
    renderFiles();
  });
  $('cliBox').textContent =
    '# 安装 / 自检\n' +
    'python -m dc_twin.cli doctor\n' +
    'python -m dc_twin.cli run --scope all      # 主链 21 步\n\n' +
    '# 数据链（可单独重跑）\n' +
    'python scripts/spec_layout.py --spec config/layout_spec.json --fleet-spec config/fleet_spec.json --write data/layout.json\n' +
    'python scripts/plan_paths.py               # 闭环任务 + A* + 几何校验\n' +
    'python scripts/gen_data.py && python scripts/validate.py\n\n' +
    '# 真仿真 / 账本 / 验收\n' +
    'python scripts/sim_engine.py --all\n' +
    'python scripts/ledger.py --all --rebuild\n' +
    'python scripts/pnl_report.py --all\n' +
    'python scripts/validate_sim.py && python scripts/validate_pnl.py\n' +
    'python -m pytest -q\n\n' +
    '# 3D 与作品页\n' +
    'blender -b -P scripts/build_dc.py && blender -b -P scripts/animate.py\n' +
    'python scripts/build_submission.py\n' +
    'python -m http.server 8000 -d submission\n' +
    'python -m dc_twin.cli serve              # 本地控制台（运营 / 场景 / 自定义）';

  // ---------------------------------------------------------------- twin (3D / flat / render)
  const twin = window.DCTwin3D.create($('twinCanvas'), D, PAL);
  const PHASE = twin.phase;
  const PH_NAME = { M: '行驶', R: '旋转', F: '朝向作业面', P: '取货', D: '交付', K: '待命' };
  const tEnd = D.paths.t_end || 1;
  let simT = 0, playing = true, speed = 1, view = '3d', trailsOn = true;

  function setView(v) {
    view = v;
    $('twinCanvas').hidden = v !== '3d';
    $('flatCanvas').hidden = v !== 'flat';
    $('renderView').hidden = v !== 'render';
    document.querySelectorAll('#viewTabs button').forEach(b =>
      b.classList.toggle('active', b.dataset.view === v));
    if (v === 'render') $('renderView').src = D.project.hero_image;
    renderCurrent();
  }
  $('viewTabs').addEventListener('click', e => {
    const b = e.target.closest('[data-view]'); if (b) setView(b.dataset.view);
  });
  $('camBtns').addEventListener('click', e => {
    const b = e.target.closest('[data-cam]'); if (!b) return;
    const k = b.dataset.cam;
    if (k === 'path') {
      trailsOn = !trailsOn; twin.setTrails(trailsOn);
      b.classList.toggle('active', !trailsOn);
      b.textContent = trailsOn ? '显示/隐藏轨迹' : '轨迹已隐藏';
    } else if (k === 'follow') {
      const on = twin.following();
      twin.follow(on ? null : 'W-R01');
      b.classList.toggle('active', !on);
      b.textContent = on ? '跟随 W-R01' : '退出跟随';
    } else {
      twin.preset(k);
      document.querySelectorAll('#camBtns [data-cam]').forEach(x => {
        if (['iso', 'wide', 'close', 'top'].includes(x.dataset.cam))
          x.classList.toggle('active', x.dataset.cam === k);
      });
      const f = $('camBtns').querySelector('[data-cam="follow"]');
      if (f) { f.classList.remove('active'); f.textContent = '跟随 W-R01'; }
    }
    renderCurrent();
  });

  function drawFlat(t) {
    const cv = $('flatCanvas'), c = cv.getContext('2d'), W = cv.width, H = cv.height;
    const [SW, SH] = D.layout.size, PAD = 26;
    const K = Math.min((W - PAD * 2) / SW, (H - PAD * 2) / SH);
    const toS = (x, y) => [PAD + x * K, H - PAD - y * K];
    c.fillStyle = PAL.sky1; c.fillRect(0, 0, W, H);
    c.strokeStyle = PAL.grid; c.lineWidth = 1; c.beginPath();
    for (let x = 0; x <= SW; x += 10) { const a = toS(x, 0), b = toS(x, SH);
      c.moveTo(a[0], a[1]); c.lineTo(b[0], b[1]); }
    for (let y = 0; y <= SH; y += 10) { const a = toS(0, y), b = toS(SW, y);
      c.moveTo(a[0], a[1]); c.lineTo(b[0], b[1]); }
    c.stroke();
    D.layout.warehouses.forEach(w => {
      const [x, y] = toS(w.origin[0], w.origin[1] + w.size[1]);
      c.fillStyle = PAL.warehouseFill; c.globalAlpha = .55;
      c.fillRect(x, y, w.size[0] * K, w.size[1] * K); c.globalAlpha = 1;
      c.strokeStyle = PAL.warehouseEdge; c.setLineDash([5, 4]);
      c.strokeRect(x, y, w.size[0] * K, w.size[1] * K); c.setLineDash([]);
      c.fillStyle = PAL.labelInk; c.font = 'bold 13px system-ui';
      c.fillText(w.id + ' · ' + w.size[0] + '×' + w.size[1] + 'm', x + 7, y + 18);
      // entity coordinates in layout.json are GLOBAL (origin is only the
      // warehouse frame corner) -- do not add w.origin a second time.
      (w.zones || []).forEach(z => {
        c.beginPath();
        z.polygon.forEach((p, i) => { const q = toS(p[0], p[1]);
          i ? c.lineTo(q[0], q[1]) : c.moveTo(q[0], q[1]); });
        c.closePath(); c.globalAlpha = .12; c.fillStyle = z.color || PAL.accent;
        c.fill(); c.globalAlpha = 1;
      });
      w.racks.forEach(r => {
        const q = toS(r[0], r[1]);
        c.fillStyle = PAL.rack;
        c.fillRect(q[0], q[1] - r[3] * K, Math.max(1.5, r[2] * K), Math.max(1.5, r[3] * K));
      });
      w.docks.forEach(dk => {
        // rect = [x0, y0, x1, y1]
        const r = dk.rect, dw = r[2] - r[0], dh = r[3] - r[1];
        const q = toS(r[0], r[1]);
        c.globalAlpha = .45;
        c.fillStyle = dk.kind === 'INBOUND' ? PAL.dockIn : PAL.dockOut;
        c.fillRect(q[0], q[1] - dh * K, dw * K, Math.max(2, dh * K));
        c.globalAlpha = 1;
      });
      w.homes.forEach(h => { const q = toS(h[0], h[1]);
        c.beginPath(); c.arc(q[0], q[1], 2.6, 0, Math.PI * 2);
        c.fillStyle = PAL.home; c.fill(); });
    });
    twin.ids.forEach(id => {
      const p = twin.agentAt(id, t); if (!p) return;
      const q = toS(p[1], p[2]), col = (PHASE[p[3]] || PHASE.M).c;
      c.beginPath(); c.arc(q[0], q[1], 5, 0, Math.PI * 2);
      c.fillStyle = col; c.fill();
      c.strokeStyle = PAL.sky1; c.lineWidth = 1.5; c.stroke();
    });
    c.fillStyle = PAL.labelInk; c.font = 'bold 12px ui-monospace, Menlo, monospace';
    c.fillText('TOP VIEW  ·  t = ' + t.toFixed(1) + ' s / ' + tEnd.toFixed(1) + ' s', PAD + 4, PAD + 12);
  }

  function renderCurrent() {
    if (view === '3d') twin.render(simT);
    else if (view === 'flat') drawFlat(simT);
    const c = twin.camera;
    $('twinHud').textContent = 'T + ' + simT.toFixed(1) + ' s / ' + tEnd.toFixed(1) +
      ' s   ·   ' + twin.ids.length + ' AGV   ·   ' + (playing ? 'RUNNING' : 'PAUSED') +
      '   ·   YAW ' + Math.round(c.yaw * 180 / Math.PI) + '°  PITCH ' +
      Math.round(c.pitch * 180 / Math.PI) + '°   ·   ' + view.toUpperCase();
  }
  let last = 0, lastDraw = 0;
  function loop(ts) {
    if (!last) last = ts;
    const dt = Math.min(.1, (ts - last) / 1000); last = ts;
    if (playing) {
      simT += dt * speed;
      if (simT > tEnd) simT = 0;
      $('timeRange').value = Math.round(simT / tEnd * 1000);
      $('timeLabel').textContent = simT.toFixed(1) + ' s';
    }
    if (ts - lastDraw >= 32) {
      lastDraw = ts;
      if (activeTab === 'twin') renderCurrent();
      else if (activeTab === 'media' && window.__mediaView) window.__mediaView.render(0);
    }
    requestAnimationFrame(loop);
  }
  $('btnPlay').onclick = () => { playing = !playing;
    $('btnPlay').textContent = playing ? '暂停' : '播放'; renderCurrent(); };
  $('btnReset').onclick = () => { simT = 0; $('timeRange').value = 0;
    $('timeLabel').textContent = '0.0 s'; renderCurrent(); };
  $('timeRange').oninput = e => { simT = +e.target.value / 1000 * tEnd;
    $('timeLabel').textContent = simT.toFixed(1) + ' s'; renderCurrent(); };
  $('speedRange').oninput = e => { speed = +e.target.value / 2;
    $('speedLabel').textContent = speed.toFixed(1) + '×'; };
  $('twinSubtitle').innerHTML = '立体场景由 <code>layout.json</code> 真实几何投影，运动来自 ' +
    '<code>paths.jsonl</code> 的 ' + D.paths.total_keyframes + ' 条关键帧（内联 ' +
    D.paths.inlined_keyframes + ' 条降采样点）';
  $('twinLegend').innerHTML = '真实关键帧 ' + D.paths.total_keyframes + ' 条，内联 ' +
    D.paths.inlined_keyframes + ' 条（降采样） · ' +
    Object.entries(PH_NAME).map(([k, v]) =>
      '<span style="color:' + PHASE[k].c + '">● ' + v + '</span>').join(' ');
  $('twinNote').innerHTML = '<strong>诚实说明</strong><br>3D 画面是浏览器端的<strong>软件投影渲染</strong>：' +
    '几何来自 <code>data/layout.json</code>（196 组立体货架按深度排序与光照着色），' +
    '运动来自 <code>data/paths.jsonl</code> 的 ' + D.paths.total_keyframes + ' 条闭环关键帧；' +
    '它不是 Blender 实时视口，Blender 的真 3D 成果在「模型与渲染」页。' +
    '右侧 KPI 来自所选场景的 tick 仿真报告，与动画不是同一次运行。' +
    '碰撞口径为采样验证 + 预约表复核，非形式化证明。';

  $('twinScenario').innerHTML = D.scenarios.map(x =>
    '<option value="' + esc(x.id) + '">' + esc(x.name) + '</option>').join('');
  function showScenario(id) {
    const x = D.scenarios.find(s => s.id === id) || D.scenarios[0], m = x.metrics;
    const rows = [['完成单量', m.orders_completed, 0], ['吞吐量 (单/h)', m.throughput_orders_per_h, 1],
      ['充电次数', m.charge_events, 0], ['故障重派', m.task_reassignments, 0],
      ['峰值排队', m.max_queue_depth, 0], ['车辆级空闲率', m.idle_rate == null ? null : m.idle_rate * 100, 1],
      ['车队规模', m.agents, 0], ['最小采样间距 (m)', m.min_pair_distance_m, 3],
      ['严格冲突', m.strict_collisions, 0]];
    $('twinMetrics').innerHTML = rows.map(([k, val, d]) =>
      '<div><span>' + k + '</span><b>' + (val == null ? '—' : Number(val).toFixed(d)) + '</b></div>').join('');
  }
  $('twinScenario').onchange = e => showScenario(e.target.value);
  showScenario(D.scenarios[0].id);

  // ---------------------------------------------------------------- lightbox
  function openLightbox(src, cap) {
    $('lightboxImg').src = src; $('lightboxCap').textContent = cap;
    $('lightbox').classList.add('on');
  }
  $('lightbox').onclick = () => $('lightbox').classList.remove('on');

  // ---------------------------------------------------------------- media
  function initMedia() {
    window.__mediaReady = true;
    const mp4 = D.media.binary.find(x => x.file.endsWith('.mp4'));
    if (mp4) {
      $('videoPlayer').src = mp4.file;
      $('videoPlayer').poster = D.project.hero_image;
    }
    $('videoNote').innerHTML = '由 <code>scripts/animate.py</code> 输出关键帧、' +
      '<code>scripts/encode_video.py</code> 编码；21 台 AGV 与 21 件货物全程在场，' +
      '货物状态随车从 IN_STOCK → LOADED → DELIVERED。体积 ' + (mp4 ? mp4.size : '—') + '。';
    window.__mediaView = window.DCTwin3D.create($('modelCanvas'), D, PAL);
    window.__mediaView.preset('wide');
    window.__mediaView.setTrails(false);
    window.__mediaView.render(0);
    $('modelNote').textContent = '同一套自研 3D 投影引擎的静态模式：拖拽可绕看 ' +
      D.project.stats.racks + ' 组立体货架与 4 个仓库分区，用于快速核对布局；' +
      '高精度材质与光照请看右侧 Blender 渲染图，或下载 .blend / .glb 在本地打开。';
    const bl = D.media.blend, gl = D.media.glb;
    $('modelStats').innerHTML = [
      [bl.objects, 'Blender 对象'], [bl.meshes, '网格数据块'], [bl.materials, '材质'],
      [bl.actions, '动画动作'], [bl.frames, '动画帧 @ ' + bl.fps + ' fps'], [gl.nodes, 'glTF 节点'],
    ].map(([n, t]) => '<div><b>' + (n ?? '—') + '</b><small>' + t + '</small></div>').join('');
    $('assetList').innerHTML = D.media.binary.map(x => {
      const ext = x.file.split('.').pop().toUpperCase();
      return '<a class="asset" href="' + esc(x.file) + '" download>' +
        '<span class="ico">' + esc(ext) + '</span><span><b>' + esc(x.file.split('/').pop()) +
        '</b><small>' + esc(x.note) + ' · ' + esc(x.size) + '</small></span></a>';
    }).join('') + '<a class="asset" href="' + esc(D.project.hero_image) + '" download>' +
      '<span class="ico">WEBP</span><span><b>02_interior.webp</b><small>' +
      '仓内透视渲染图 · ' + esc(D.media.renders[1].size) + '</small></span></a>';
    $('mediaGallery').innerHTML = D.media.renders.map(r =>
      '<figure><img src="' + esc(r.file) + '" alt="' + esc(r.caption) + '" data-src="' +
      esc(r.file) + '" data-cap="' + esc(r.caption) + '"><figcaption>' + esc(r.caption) +
      '<br><span class="mono">' + esc(r.resolution) + ' · ' + esc(r.size) + '</span></figcaption></figure>').join('');
    $('mediaGallery').addEventListener('click', e => {
      if (e.target.tagName === 'IMG')
        openLightbox(e.target.dataset.src, e.target.dataset.cap + ' · Blender 5.2 EEVEE');
    });
  }

  // ---------------------------------------------------------------- lab
  $('scenGrid').innerHTML = D.scenarios.map((x, i) => {
    const m = x.metrics;
    return '<div class="scen-card' + (i === 0 ? ' active' : '') + '" data-id="' + esc(x.id) + '">' +
      '<div class="sid">' + esc(x.id) + '</div><h4>' + esc(x.name) + '</h4>' +
      '<div class="big">' + (m.orders_completed ?? '—') + '</div><div class="mini">' +
      '<span>完成单</span><span>' + fmt(m.throughput_orders_per_h, 1) + ' 单/h</span>' +
      '<span>充电 ' + m.charge_events + '</span><span>冲突 ' + m.strict_collisions + '</span>' +
      '</div></div>';
  }).join('');
  $('scenGrid').addEventListener('click', e => {
    const c = e.target.closest('.scen-card'); if (!c) return;
    document.querySelectorAll('.scen-card').forEach(x => x.classList.remove('active'));
    c.classList.add('active');
    $('twinScenario').value = c.dataset.id; showScenario(c.dataset.id);
  });
  $('scenSpecTable').innerHTML = table(
    [{ t: '场景' }, { t: 'seed', num: true }, { t: '覆盖参数' }, { t: '干扰事件' }, { t: '断言' }],
    D.scenario_specs.builtin.map(x => [
      { html: '<code>' + esc(x.id) + '</code><br><small>' + esc(x.name) + '</small>' },
      { t: x.seed, num: true },
      { html: '<span class="mono">' + esc(short(x.overrides, 100)) + '</span>' },
      { html: '<span class="mono">' + (x.disruptions.length ? esc(short(x.disruptions, 90)) : '—') + '</span>' },
      { html: '<span class="mono">' + esc(short(x.assertions, 90)) + '</span>' },
    ]));

  const LAB_FIELDS = [
    ['id', '场景 ID', 'text', 'my_scenario'], ['name', '场景名称', 'text', '我的自定义场景'],
    ['seed', '随机种子', 'number', '20261109'], ['per_hour', '订单到达率 (单/h)', 'number', '60'],
    ['fleet', '车队规模 (台)', 'number', '24'], ['horizon', '仿真时长 (s)', 'number', '3600'],
    ['mix.INBOUND', '入库占比', 'number', '0.20'], ['mix.OUTBOUND', '出库占比', 'number', '0.25'],
    ['mix.PICK', '拣选占比', 'number', '0.25'], ['mix.REPLENISH', '补货占比', 'number', '0.15'],
    ['mix.TRANSFER', '移库占比', 'number', '0.15'],
  ];
  $('labForm').innerHTML = '<h3 style="margin:0 0 12px">参数编排</h3>' +
    LAB_FIELDS.map(([k, l, t, v]) =>
      '<div class="field"><label for="lf_' + esc(k) + '">' + l + '</label>' +
      '<input id="lf_' + esc(k) + '" data-k="' + esc(k) + '" type="' + t + '" value="' + v + '"></div>').join('') +
    '<div class="field" style="grid-template-columns:1fr"><label for="lf_dis">干扰事件 (JSON 数组)</label>' +
    '<textarea id="lf_dis" rows="3">[]</textarea></div>' +
    '<div class="hint">静态页只做参数编排与草稿导出；把 JSON 保存到 ' +
    '<code>config/scenarios/custom/</code> 后运行 CLI 才会真正仿真。</div>';
  function buildScenario() {
    const v = {};
    document.querySelectorAll('#labForm [data-k]').forEach(i => v[i.dataset.k] = i.value);
    const mix = {};
    Object.keys(v).filter(k => k.startsWith('mix.')).forEach(k => mix[k.slice(4)] = Number(v[k]));
    let dis = [];
    try { dis = JSON.parse($('lf_dis').value); } catch (e) { dis = 'INVALID_JSON'; }
    return {
      schema_version: '1.0', id: v.id, name: v.name,
      layout_spec: '../layout_spec.json', fleet_spec: '../fleet_spec.json',
      demand_spec: '../demand_spec.json', seed: Number(v.seed),
      simulation: { warmup_s: 0, duration_s: Number(v.horizon), dt_s: 0.1, fps: 8,
        max_wall_clock_s: Math.max(900, Number(v.horizon) / 2) },
      overrides: { 'demand.orders.per_hour': Number(v.per_hour),
        'fleet.size': Number(v.fleet), 'demand.orders.mix': mix },
      disruptions: dis,
      assertions: [{ metric: 'orders_completed', op: '>', value: 0 },
        { metric: 'strict_collisions', op: '==', value: 0 }],
      outputs: ['sim_kpi.json', 'sim_events.jsonl', 'pnl.json'],
      _note: 'DRAFT · 由 DC Twin Studio 场景实验室生成，需 scenario_tools.py 校验后保存',
    };
  }
  function download(name, obj) {
    const blob = new Blob([JSON.stringify(obj, null, 2)], { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = name; a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  }
  $('btnGenScen').onclick = () => {
    const o = buildScenario();
    $('scenPreview').textContent = JSON.stringify(o, null, 2);
    $('scenHint').innerHTML = '草稿已生成。下载后放入 <code>config/scenarios/custom/' +
      esc(o.id) + '.json</code>，或在项目根目录运行：<br><code>python scripts/scenario_tools.py run --id ' +
      esc(o.id) + '</code>';
  };
  $('btnDownloadScen').onclick = () => {
    const o = buildScenario(); download(o.id + '.json', o);
    $('scenHint').textContent = '已下载 ' + o.id + '.json';
  };

  // ---------------------------------------------------------------- ops
  $('opsSubtitle').textContent = '收入 / 变动成本 / 固定分摊全部来自 ledger.jsonl 分录；' +
    '价格来源 ' + D.pnl[0].prices_source + '（演示值，禁止用于真实经营决策）';
  const b = D.pnl.find(x => x.run_id.startsWith('baseline/'));
  function bars(el, rows, max) {
    $(el).innerHTML = rows.map(([label, val, color]) => {
      const w = Math.max(2, Math.abs(val) / max * 100);
      return '<div class="bar-row"><div class="bar-head"><span>' + label + '</span><strong>' +
        money(val) + '</strong></div><div class="bar-track"><div class="bar-fill" style="width:' +
        w + '%;background:' + color + '"></div></div></div>';
    }).join('');
  }
  const C_OK = 'var(--accent)', C_WARN = 'var(--amber)', C_MUTE = 'var(--muted)', C_BAD = 'var(--red)';
  const maxB = Math.max(b.revenue, b.variable, b.fixed, Math.abs(b.profit), 1);
  bars('waterfall', [['收入合计', b.revenue, C_OK], ['变动成本与罚金', -b.variable, C_WARN],
    ['固定成本分摊', -b.fixed, C_MUTE], ['运营利润', b.profit, b.profit >= 0 ? C_OK : C_BAD]], maxB);
  $('breakeven').innerHTML =
    '<div class="metric-list">' +
    '<div><span>完成单量</span><b>' + b.orders + ' 单</b></div>' +
    '<div><span>单均利润</span><b>' + money(b.per_order_profit) + '</b></div>' +
    '<div><span>盈亏平衡点</span><b>' + (b.breakeven ?? '不可达') + ' 单/班</b></div>' +
    '<div><span>baseline 利润</span><b style="color:var(--red)">' + money(b.profit) + '</b></div>' +
    '</div><div class="hint">1 小时班次内固定成本分摊高于收入，因此所有演示场景均亏损；' +
    'peak 提高单量后亏损收窄 ' + money(D.compare.delta) + '。这是模型结论，不是计算错误。</div>';
  bars('pnlChart', D.pnl.map(x => [x.run_id.split('/')[0], x.profit,
    x.profit >= 0 ? C_OK : C_BAD]), Math.max(...D.pnl.map(x => Math.abs(x.profit)), 1));
  const sens = b.sensitivity || {};
  $('sensitivity').innerHTML = Object.keys(sens).map(k => {
    const s = sens[k];
    return '<div class="bar-row"><div class="bar-head"><span>' + esc(s.note) +
      '</span><strong>' + money(s.low) + ' ~ ' + money(s.high) + '</strong></div>' +
      '<div class="bar-track"><div class="bar-fill" style="width:100%;background:linear-gradient(90deg,var(--accent-soft),var(--amber),var(--accent-soft))"></div></div></div>';
  }).join('');
  $('ledgerAccounts').innerHTML = '<div class="metric-list">' +
    D.ledger.accounts.map(a => '<div><span class="mono">' + esc(a.account) +
      '</span><b>' + a.count + '</b></div>').join('') +
    '<div><span>分录总数</span><b>' + D.ledger.total + '</b></div></div>';
  $('ledgerTable').innerHTML = table(
    [{ t: 'seq' }, { t: 't_s', num: true }, { t: '科目' }, { t: '借/贷' }, { t: '金额', num: true }, { t: '备注' }],
    D.ledger.sample.map(e => [
      { t: e.source_event_seq }, { t: e.t_s, num: true },
      { html: '<span class="mono">' + esc(e.account) + '</span>' }, esc(e.direction),
      { html: '<b>' + fmt(e.amount, 4) + '</b>', num: true }, esc(e.memo),
    ]));
  $('pnlTable').innerHTML = table(
    [{ t: '运行' }, { t: '引擎' }, { t: '收入', num: true }, { t: '变动', num: true },
      { t: '固定', num: true }, { t: '利润', num: true }, { t: '单量', num: true }, { t: '平衡单量', num: true }],
    D.pnl.map(x => [esc(x.run_id), esc(x.engine), { t: fmt(x.revenue), num: true },
      { t: fmt(x.variable), num: true }, { t: fmt(x.fixed), num: true },
      { html: '<b style="color:' + (x.profit >= 0 ? 'var(--accent)' : 'var(--red)') + '">' +
        fmt(x.profit) + '</b>', num: true },
      { t: x.orders, num: true }, { t: x.breakeven ?? '—', num: true }]));

  // ---------------------------------------------------------------- editor (G1)
  function initEditor() {
    window.__editorReady = true;
    $('editorTools').innerHTML = window.DCEditor.TOOLS.map(([k, l]) =>
      '<button class="tool-btn' + (k === 'select' ? ' active' : '') + '" data-tool="' + k + '">' + l + '</button>').join('');
    $('editorTools').addEventListener('click', e => {
      const btn = e.target.closest('[data-tool]');
      if (btn) {
        window.DCEditor.setTool(btn.dataset.tool);
        document.querySelectorAll('#editorTools .tool-btn').forEach(x =>
          x.classList.toggle('active', x === btn));
      }
    });
    window.DCEditor.init($('editorCanvas'), res => {
      $('editorIssues').className = 'issue' + (res.issues.length ? '' : ' ok');
      $('editorIssues').textContent = res.issues.length
        ? res.issues.slice(0, 6).join('\n') + (res.issues.length > 6 ? '\n… 共 ' + res.issues.length + ' 项' : '')
        : '校验通过 · ' + res.count + ' 个实体，0 项问题';
    });
    $('btnUndo').onclick = () => window.DCEditor.undo();
    $('btnResetEditor').onclick = () => window.DCEditor.reset();
    $('bgFile').onchange = e => {
      if (e.target.files[0]) {
        window.DCEditor.setBackground(e.target.files[0], $('bgMeters').value);
        $('calibHint').textContent = '已载入底图。请在画布上依次点击两个已知距离的点，然后点「两点标定」。';
      }
    };
    $('btnCalib').onclick = () => {
      const r = window.DCEditor.calibrate($('bgMeters').value);
      $('calibHint').textContent = r
        ? '标定完成：' + r + ' 米/像素（已写入草稿）'
        : '需要先在画布上点两个标定点（当前 ' + window.DCEditor.calibPoints + '/2）。';
    };
    $('btnExportDraft').onclick = () => {
      const d = window.DCEditor.draft();
      download('layout_spec_draft.json', d);
      $('editorIssues').textContent = '已导出草稿（' + d.entities.length + ' 实体，' +
        d.issues.length + ' 项待人工核对）；不会覆盖 config/layout_spec.json。';
    };
  }

  // ---------------------------------------------------------------- proof
  const v = D.validation;
  $('proofGrid').innerHTML = [
    [v.A.passed + '/' + v.A.total, 'A1–A40 既有硬门槛验收', '布局 / 交通 / 几何 / 预约 / 负测试'],
    [v.S.checks, 'S 系列检查全过', 'S1–S9 + 8 场景断言，' + v.S.negative + ' 个负测试能失败'],
    [v.F.checks, 'F 系列检查全过', 'F1–F7 独立复算，' + v.F.negative + ' 个负测试能失败'],
    [v.pytest, 'pytest 用例通过', '含 C 阶段与 G 阶段契约测试'],
    [v.chain_steps, '主链步骤 RUN_OK', 'DATA → SCENARIO → 仿真 → 账本 → 验收'],
    [D.kpi.length, 'measured KPI', '每项带 definition / formula / source'],
    [v.min_sep_m + ' m', 'MIN_SEP 未降级', '2×AGV_RADIUS，全程采样 0 违规'],
    [D.paths.total_keyframes, '真实路径关键帧', '21 台 AGV 闭环作业'],
  ].map(([n, t, p]) => '<div class="proof"><b>' + n + '</b><div>' + t + '</div><small>' + p + '</small></div>').join('');

  let checkKind = 'A';
  function renderChecks() {
    let heads, rows;
    if (checkKind === 'K') {
      heads = [{ t: 'KPI' }, { t: '值', num: true }, { t: '单位' }, { t: '定义' }, { t: '公式 / 口径' }, { t: '来源' }];
      rows = D.kpi.map(k => [{ html: '<code>' + esc(k.key) + '</code>' },
        { t: typeof k.value === 'number' ? fmt(k.value, 3) : String(k.value), num: true },
        esc(k.unit), esc(k.definition), { html: '<span class="mono">' + esc(k.formula) + '</span>' }, esc(k.source)]);
    } else if (checkKind === 'E') {
      heads = [{ t: 'seq' }, { t: 't_s', num: true }, { t: '事件类型' }, { t: '载荷字段' }];
      rows = D.events.sample.map(e => {
        const rest = Object.assign({}, e);
        delete rest.seq; delete rest.t_s; delete rest.type;
        return [{ t: e.seq }, { t: e.t_s, num: true },
          { html: '<span class="tag">' + esc(e.type) + '</span>' },
          { html: '<span class="mono">' + esc(short(rest, 150)) + '</span>' }];
      });
    } else {
      heads = [{ t: '检查项' }, { t: '结果' }, { t: '证据 / 描述' }];
      rows = D.checks[checkKind].map(c => [esc(c.id),
        { html: '<span class="tag' + (c.status === 'PASS' ? '' : ' fail') + '">' + esc(c.status) + '</span>' },
        esc(c.detail)]);
    }
    $('checkTable').innerHTML = table(heads, rows);
  }
  renderChecks();
  $('checkTabs').addEventListener('click', e => {
    const b = e.target.closest('[data-check]'); if (!b) return;
    checkKind = b.dataset.check;
    document.querySelectorAll('#checkTabs button').forEach(x =>
      x.classList.toggle('active', x === b));
    renderChecks();
  });
  $('githubTable').innerHTML = table(
    [{ t: '项目' }, { t: '许可证' }, { t: '使用方式' }],
    D.github.map(g => [esc(g.repo), esc(g.license), esc(g.role)]));
  $('gallery').innerHTML = D.media.renders.map(r =>
    '<img src="' + esc(r.file) + '" alt="' + esc(r.caption) + '" data-src="' + esc(r.file) +
    '" data-cap="' + esc(r.caption) + '">').join('');
  $('gallery').addEventListener('click', e => {
    if (e.target.tagName === 'IMG')
      openLightbox(e.target.dataset.src, e.target.dataset.cap + ' · Blender 5.2 EEVEE');
  });
  $('limitsList').innerHTML = [
    '演示价格（prices_source=demo）不可用于真实经营决策；所有场景 1h 班次均亏损。',
    '本页 3D 是软件投影渲染，非 Blender 实时视口；GLB 为静态快照（动画在 mp4 / .blend）。',
    'scale_50 场景有 50 台车，但只有 30 台实际接单移动，空闲车如实报告。',
    'LIF 是语义对齐的自研草稿格式，非 VDA 5050 认证；边为示意连通关系。',
    '未对接任何真实 WMS；适配器 ack 默认 dry-run，连接密钥仅用环境变量名引用。',
    'CBS/L3 求解器标 experimental（D3）；复杂 DXF 图纸需人工描图确认（G2 仅覆盖 ASCII 子集）。',
    '拖拽布局编辑器为 2D，暂无 3D 拖拽预览；碰撞结论为采样验证 + 预约表复核，非形式化保证。',
  ].map(x => '<li>' + x + '</li>').join('');

  // ---------------------------------------------------------------- submit
  const intro = D.project.name + '\n\n' + D.project.tagline + '。\n' +
    D.project.description + '\n\n' +
    '作品以 ' + D.project.stats.warehouses + ' 个仓库、' + D.project.stats.racks + ' 组货架、' +
    D.project.stats.locations + ' 个库位与 ' + D.project.stats.robots + ' 台 AGV 为对象，' +
    '实现 tick 级离散事件仿真（持续订单、在线派单、电量/充电、故障重派、到达互斥与车间距门控）、' +
    '事件溯源运营账本与损益表，并通过可视化布局编辑器、DXF/LIF 草稿导入和自定义场景面板支持定制。' +
    'Blender 5.2 程序化生成 3D 场景、10 张渲染图与闭环动画；本作品页用可旋转的软件 3D 投影' +
    '完整展示项目全链路，并可下载 .blend / .glb / .mp4。\n\n' +
    '全部指标可独立复算：既有验收 A1–A40 40/40，真仿真 S1–S9 共 ' + v.S.checks +
    ' 项检查、盈亏 F1–F7 共 ' + v.F.checks + ' 项检查、pytest ' + v.pytest +
    ' 项用例全部通过；GitHub 开源对照已逐项核实许可证并区分代码衍生与概念参照。\n\n' +
    '技术栈：Python 标准库、Blender 5.2 (bpy)、Canvas 2D 软件 3D 投影、零后端静态发布。';
  $('introBox').value = intro;
  $('btnCopyIntro').onclick = () => {
    const t = $('introBox'); t.select();
    try { document.execCommand('copy'); $('copyMsg').textContent = '已复制到剪贴板'; }
    catch (e) { $('copyMsg').textContent = '请手动全选复制'; }
  };
  $('submitInfo').innerHTML = [
    ['作品名称', D.project.name], ['作品类型', '网页应用（零后端静态站）'],
    ['技术栈', D.project.tech.join(' / ')], ['仓库 / 货架 / 库位',
      D.project.stats.warehouses + ' / ' + D.project.stats.racks + ' / ' + D.project.stats.locations],
    ['场景数量', D.scenarios.length + ' 内置 + 自定义'], ['损益运行数', D.pnl.length],
    ['3D 资产', '.blend + .glb + .mp4 + 10 张渲染图（均可下载）'],
    ['许可证', 'Apache-2.0（第三方声明见 THIRD_PARTY_NOTICES.md）'],
    ['数据来源', 'out/*.json 真实报告快照，非手工编造'],
    ['在线链接', D.project.site_url],
  ].map(([k, val]) => '<tr><th>' + k + '</th><td>' + esc(val) + '</td></tr>').join('');
  $('shareSteps').innerHTML = [
    '打开创作这个作品的 WorkBuddy 对话，点击页面右上角「分享」按钮。',
    '选择要分享的对话（建议只勾选本作品相关对话，避免带入无关内容）。',
    '在底部工具栏选择「链接」，复制生成的地址。',
    '把链接填入活动表单「05 WorkBuddy 创作对话记录」。',
    '<strong>提交前重新打开一次链接</strong>，确认评审人员可以正常访问（未登录也可查看）。',
    '把本作品页的在线链接一并填入作品地址栏：' + D.project.site_url,
  ].map(x => '<li>' + x + '</li>').join('');
  $('privacyWarn').innerHTML = '<strong>分享前务必检查</strong>：确认对话中不含个人隐私、' +
    '商业机密等敏感信息。本项目已做过一次扫描：源码、文档、配置与本页面数据快照中' +
    '<strong>无个人绝对路径</strong>，<code>config/integrations.json</code> 不含任何密钥（只用环境变量名）。' +
    '但对话记录里可能出现你的姓名、学号、本机路径、截图中的窗口信息——' +
    '分享前请在对话里逐条确认，必要时新建一个精简对话再分享。';
  $('checklist').innerHTML = [
    '作品页已发布为可公开访问的在线链接，并用无痕窗口打开验证',
    '创作对话记录分享链接已生成，重新打开确认评委可访问',
    '对话记录已检查：无姓名 / 学号 / 手机 / 家庭住址 / 本机绝对路径',
    '对话记录已检查：无密钥、令牌、账号密码、企业内部资料',
    '作品简介已复制，作品名称与技术栈填写一致',
    '截图 / 渲染图中不含个人信息或第三方受版权保护素材',
  ].map(x => '<label class="check-item"><input type="checkbox"><span>' + x + '</span></label>').join('');
  $('repro').textContent =
    '# 本地完整复现（可选，评审无需安装）\n' +
    'git clone <repo>\ncd dc_project\n' +
    'python run.py --scope scenarios        # 主链 21 步\n' +
    'python scripts/validate.py             # A1–A40\n' +
    'python scripts/validate_sim.py         # S1–S9\n' +
    'python scripts/validate_pnl.py         # F1–F7\n' +
    'python -m pytest -q                    # 41 项\n' +
    'python scripts/build_submission.py     # 重建本页数据与媒体资产\n' +
    'python -m dc_twin.cli serve            # 本地控制台（运营 / 场景 / 自定义面板）\n' +
    'python -m http.server 8000 -d submission   # 本地预览本作品页';

  // ---------------------------------------------------------------- start
  twin.render(0);
  renderCurrent();
  requestAnimationFrame(loop);
  const q = new URLSearchParams(location.search);
  if (q.get('tab')) switchTab(q.get('tab'));
})();
