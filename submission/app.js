/* DC TWIN STUDIO · 活动提交作品页主逻辑（零后端） */
(function () {
  'use strict';
  const D = window.DC_SUBMISSION;
  const $ = id => document.getElementById(id);
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g,
    c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const fmt = (n, d) => (typeof n === 'number' ? n.toFixed(d === undefined ? 2 : d) : '—');
  const money = n => (typeof n === 'number' ? '¥' + n.toFixed(2) : '—');

  // ---------------------------------------------------------------- tabs
  function switchTab(name) {
    document.querySelectorAll('#nav button').forEach(b =>
      b.classList.toggle('active', b.dataset.tab === name));
    document.querySelectorAll('.pane').forEach(p =>
      p.classList.toggle('active', p.id === 'pane-' + name));
    if (name === 'editor' && !window.__editorReady) initEditor();
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }
  $('nav').addEventListener('click', e => {
    const b = e.target.closest('button[data-tab]');
    if (b) switchTab(b.dataset.tab);
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
  $('heroImg').src = D.images[0] || '';
  $('heroNote').textContent = '数据快照生成于 ' + D.generated_at +
    '；所有指标来自 out/*.json 报告，评审可用仓库内脚本独立复算。';
  const s = D.project.stats;
  $('heroStats').innerHTML = [
    ['仓', s.warehouses], ['货架', s.racks], ['库位', s.locations], ['AGV', s.robots],
  ].map(([k, v]) => '<div><b>' + v + '</b><small>' + k + '</small></div>').join('');

  const features = [
    ['01 / 布局', '单一真相源', '四层 JSON 配置 → spec_layout 展开 + L1–L12 校验，生成 4 仓 196 货架 441 库位，冻结基线逐字节可复现。'],
    ['02 / 仿真', 'tick 级 DES', '订单持续到达、在线派单、9 态 FSM、电量与充电、故障重派、L1 时空预约；8 个场景真实执行，27 项 measured KPI。'],
    ['03 / 盈亏', '事件溯源账本', '仿真事件 → 分录 → 损益表、单均成本、盈亏平衡与敏感性；F1–F7 独立复算，演示价格强制徽章。'],
    ['04 / 定制', '不改 Python', '可视化场景编排、布局拖拽编辑、DXF/LIF 草稿导入、fleet_spec 容量守卫、WMS 只读适配器框架。'],
  ];
  $('featureGrid').innerHTML = features.map(([n, t, p]) =>
    '<div class="feature"><div class="num">' + n + '</div><h4>' + t + '</h4><p>' + p + '</p></div>').join('');
  $('tourList').innerHTML = [
    '在「实时孪生」看 21 台 AGV 沿真实关键帧完成 HOME→取货→交付→回位闭环。',
    '在「场景实验室」对比 8 个场景的完成单量、充电、故障重派与最小间距。',
    '在「运营盈亏」看收入→变动→固定→利润的瀑布，以及盈亏平衡单量。',
    '在「布局编辑」拖一拖实体、上传平面图做两点标定，导出草稿 JSON。',
    '在「验收证据」核对 A1–A40 / S1–S9 / F1–F7 与 GitHub 许可证对照。',
    '在「提交材料」复制作品简介，按步骤生成创作对话记录分享链接。',
  ].map(x => '<li>' + x + '</li>').join('');

  // ---------------------------------------------------------------- twin canvas
  const cv = $('twinCanvas'), ctx = cv.getContext('2d');
  const PAD = 26, W = cv.width, H = cv.height;
  const [SW, SH] = D.layout.size;
  const K = Math.min((W - PAD * 2) / SW, (H - PAD * 2) / SH);
  const toS = (x, y) => [PAD + x * K, H - PAD - y * K];
  const PH_COLOR = { M: '#2f7559', R: '#7c5bb0', F: '#2f8fa8', P: '#c98b3a', D: '#3b6ea8', K: '#9aa9a0' };
  const PH_NAME = { M: '行驶', R: '旋转', F: '朝向作业面', P: '取货', D: '交付', K: '待命' };
  const paths = D.paths.agents, tEnd = D.paths.t_end || 1;
  let simT = 0, playing = true, speed = 2, last = 0;

  function agentAt(pts, t) {
    if (!pts || !pts.length) return null;
    if (t <= pts[0][0]) return pts[0];
    for (let i = 1; i < pts.length; i++) {
      if (pts[i][0] >= t) {
        const a = pts[i - 1], b = pts[i];
        const f = b[0] === a[0] ? 0 : (t - a[0]) / (b[0] - a[0]);
        return [t, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f, b[3] || a[3]];
      }
    }
    return pts[pts.length - 1];
  }

  function drawTwin(t) {
    ctx.clearRect(0, 0, W, H);
    ctx.fillStyle = '#fbfcf9'; ctx.fillRect(0, 0, W, H);
    // grid
    ctx.strokeStyle = '#eef2ec'; ctx.lineWidth = 1;
    for (let x = 0; x <= SW; x += 10) { const [a, b] = toS(x, 0); const [c, d] = toS(x, SH);
      ctx.beginPath(); ctx.moveTo(a, b); ctx.lineTo(c, d); ctx.stroke(); }
    for (let y = 0; y <= SH; y += 10) { const [a, b] = toS(0, y); const [c, d] = toS(SW, y);
      ctx.beginPath(); ctx.moveTo(a, b); ctx.lineTo(c, d); ctx.stroke(); }
    // warehouses
    D.layout.warehouses.forEach(w => {
      const [x, y] = toS(w.origin[0], w.origin[1] + w.size[1]);
      ctx.fillStyle = 'rgba(47,117,89,.035)';
      ctx.fillRect(x, y, w.size[0] * K, w.size[1] * K);
      ctx.strokeStyle = '#b9c7bc'; ctx.setLineDash([5, 4]);
      ctx.strokeRect(x, y, w.size[0] * K, w.size[1] * K); ctx.setLineDash([]);
      ctx.fillStyle = '#5d6d63'; ctx.font = 'bold 13px system-ui';
      ctx.fillText(w.id + ' · ' + w.size[0] + '×' + w.size[1] + 'm', x + 7, y + 18);
      // zones
      (w.zones || []).forEach(z => {
        ctx.beginPath();
        z.polygon.forEach((p, i) => { const [px, py] = toS(w.origin[0] + p[0], w.origin[1] + p[1]);
          i ? ctx.lineTo(px, py) : ctx.moveTo(px, py); });
        ctx.closePath(); ctx.fillStyle = (z.color || '#3b82f6') + '14'; ctx.fill();
      });
      // racks
      ctx.fillStyle = '#d7e0d6';
      w.racks.forEach(r => {
        const [rx, ry] = toS(w.origin[0] + r[0], w.origin[1] + r[1]);
        ctx.fillRect(rx, ry - r[3] * K, Math.max(1.5, r[2] * K), Math.max(1.5, r[3] * K));
      });
      // docks
      w.docks.forEach(dk => {
        const [dx, dy] = toS(w.origin[0] + dk.rect[0], w.origin[1] + dk.rect[1]);
        ctx.fillStyle = dk.kind === 'INBOUND' ? 'rgba(59,110,168,.35)' : 'rgba(201,139,58,.35)';
        ctx.fillRect(dx, dy - dk.rect[3] * K, dk.rect[2] * K, Math.max(2, dk.rect[3] * K));
      });
      // homes
      w.homes.forEach(h => {
        const [hx, hy] = toS(h[0], h[1]);
        ctx.beginPath(); ctx.arc(hx, hy, 2.6, 0, Math.PI * 2);
        ctx.fillStyle = '#9aa9a0'; ctx.fill();
      });
    });
    // agents + short trails
    Object.keys(paths).forEach(id => {
      const pts = paths[id], p = agentAt(pts, t);
      if (!p) return;
      const [px, py] = toS(p[1], p[2]);
      const idx = pts.findIndex(q => q[0] >= t);
      if (idx > 2) {
        ctx.beginPath();
        pts.slice(Math.max(0, idx - 22), idx).forEach((q, i) => {
          const [qx, qy] = toS(q[1], q[2]); i ? ctx.lineTo(qx, qy) : ctx.moveTo(qx, qy);
        });
        ctx.strokeStyle = (PH_COLOR[p[3]] || '#2f7559') + '55'; ctx.lineWidth = 1.6; ctx.stroke();
      }
      ctx.beginPath(); ctx.arc(px, py, 4.6, 0, Math.PI * 2);
      ctx.fillStyle = PH_COLOR[p[3]] || '#2f7559'; ctx.fill();
      ctx.strokeStyle = '#fff'; ctx.lineWidth = 1.4; ctx.stroke();
    });
    // clock
    ctx.fillStyle = '#1d2b26'; ctx.font = 'bold 13px system-ui';
    ctx.fillText('t = ' + t.toFixed(1) + ' s / ' + tEnd.toFixed(1) + ' s', PAD + 4, PAD + 12);
  }

  function loop(ts) {
    if (!last) last = ts;
    const dt = Math.min(.1, (ts - last) / 1000); last = ts;
    if (playing) {
      simT += dt * speed;
      if (simT > tEnd) simT = 0;
      $('timeRange').value = Math.round(simT / tEnd * 1000);
      $('timeLabel').textContent = simT.toFixed(1) + ' s';
      drawTwin(simT);
    }
    requestAnimationFrame(loop);
  }
  $('btnPlay').onclick = () => { playing = !playing; $('btnPlay').textContent = playing ? '暂停' : '播放'; };
  $('btnReset').onclick = () => { simT = 0; $('timeRange').value = 0; drawTwin(0); };
  $('timeRange').oninput = e => { simT = +e.target.value / 1000 * tEnd;
    $('timeLabel').textContent = simT.toFixed(1) + ' s'; drawTwin(simT); };
  $('speedRange').oninput = e => { speed = +e.target.value / 2;
    $('speedLabel').textContent = speed.toFixed(1) + '×'; };
  $('twinLegend').innerHTML = '真实关键帧 ' + D.paths.total_keyframes + ' 条，内联 ' +
    D.paths.inlined_keyframes + ' 条（降采样） · ' +
    Object.entries(PH_NAME).map(([k, v]) =>
      '<span style="color:' + PH_COLOR[k] + '">● ' + v + '</span>').join(' ');
  $('twinNote').innerHTML = '<strong>诚实说明</strong><br>画面由主链 <code>data/paths.jsonl</code> 的 21 车闭环关键帧驱动；' +
    '右侧 KPI 来自所选场景的 tick 仿真报告，两者不是同一次运行。碰撞口径为采样验证 + 预约表复核，非形式化证明。';

  // scenario select + metrics
  $('twinScenario').innerHTML = D.scenarios.map(x =>
    '<option value="' + esc(x.id) + '">' + esc(x.name) + '</option>').join('');
  function showScenario(id) {
    const x = D.scenarios.find(s => s.id === id) || D.scenarios[0];
    const m = x.metrics;
    const rows = [['完成单量', m.orders_completed, 0], ['吞吐量 (单/h)', m.throughput_orders_per_h, 1],
      ['充电次数', m.charge_events, 0], ['故障重派', m.task_reassignments, 0],
      ['峰值排队', m.max_queue_depth, 0], ['车辆级空闲率', (m.idle_rate * 100), 1],
      ['车队规模', m.agents, 0], ['最小采样间距 (m)', m.min_pair_distance_m, 3],
      ['严格冲突', m.strict_collisions, 0]];
    $('twinMetrics').innerHTML = rows.map(([k, v, d]) =>
      '<div><span>' + k + '</span><b>' + (v == null ? '—' : Number(v).toFixed(d) + (k.includes('%') ? '' : '')) + '</b></div>').join('');
  }
  $('twinScenario').onchange = e => showScenario(e.target.value);
  showScenario(D.scenarios[0].id);
  drawTwin(0); requestAnimationFrame(loop);

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
    a.href = URL.createObjectURL(blob); a.download = name; a.click();
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
  const maxB = Math.max(b.revenue, b.variable, b.fixed, Math.abs(b.profit), 1);
  bars('waterfall', [['收入合计', b.revenue, '#2f7559'],
    ['变动成本与罚金', -b.variable, '#c98b3a'],
    ['固定成本分摊', -b.fixed, '#9aa9a0'],
    ['运营利润', b.profit, b.profit >= 0 ? '#2f7559' : '#c05252']], maxB);
  $('breakeven').innerHTML =
    '<div class="metric-list">' +
    '<div><span>完成单量</span><b>' + b.orders + ' 单</b></div>' +
    '<div><span>单均利润</span><b>' + money(b.per_order_profit) + '</b></div>' +
    '<div><span>盈亏平衡点</span><b>' + (b.breakeven ?? '不可达') + ' 单/班</b></div>' +
    '<div><span>baseline 利润</span><b style="color:#c05252">' + money(b.profit) + '</b></div>' +
    '</div><div class="hint">1 小时班次内固定成本分摊高于收入，因此所有演示场景均亏损；' +
    'peak 提高单量后亏损收窄 ' + money(D.compare.delta) + '。这是模型结论，不是计算错误。</div>';
  bars('pnlChart', D.pnl.map(x => [x.run_id.split('/')[0], x.profit,
    x.profit >= 0 ? '#2f7559' : '#c05252']),
    Math.max(...D.pnl.map(x => Math.abs(x.profit)), 1));
  const sens = b.sensitivity || {};
  $('sensitivity').innerHTML = Object.keys(sens).map(k => {
    const s = sens[k];
    return '<div class="bar-row"><div class="bar-head"><span>' + esc(s.note) +
      '</span><strong>' + money(s.low) + ' ~ ' + money(s.high) + '</strong></div>' +
      '<div class="bar-track"><div class="bar-fill" style="width:100%;background:linear-gradient(90deg,#e8f2ec,#c98b3a,#e8f2ec)"></div></div></div>';
  }).join('');
  $('pnlTable').innerHTML = '<thead><tr><th>运行</th><th>引擎</th><th class="num">收入</th>' +
    '<th class="num">变动</th><th class="num">固定</th><th class="num">利润</th>' +
    '<th class="num">单量</th><th class="num">平衡单量</th></tr></thead><tbody>' +
    D.pnl.map(x => '<tr><td>' + esc(x.run_id) + '</td><td>' + esc(x.engine) + '</td>' +
      '<td class="num">' + fmt(x.revenue) + '</td><td class="num">' + fmt(x.variable) +
      '</td><td class="num">' + fmt(x.fixed) + '</td><td class="num" style="color:' +
      (x.profit >= 0 ? '#2f7559' : '#c05252') + '"><b>' + fmt(x.profit) + '</b></td>' +
      '<td class="num">' + x.orders + '</td><td class="num">' + (x.breakeven ?? '—') + '</td></tr>').join('') +
    '</tbody>';

  // ---------------------------------------------------------------- editor (G1)
  function initEditor() {
    window.__editorReady = true;
    $('editorTools').innerHTML = window.DCEditor.TOOLS.map(([k, l]) =>
      '<button class="tool-btn' + (k === 'select' ? ' active' : '') + '" data-tool="' + k + '">' + l + '</button>').join('');
    $('editorTools').addEventListener('click', e => {
      const b = e.target.closest('[data-tool]');
      if (b) window.DCEditor.setTool(b.dataset.tool);
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
    [v.pytest, 'pytest 用例通过', '含 C 阶段 11 项契约测试'],
    [v.chain_steps, '主链步骤 RUN_OK', 'DATA → SCENARIO → 仿真 → 账本 → 验收'],
    ['27', 'measured KPI', '每项带 definition / formula / source'],
    [v.min_sep_m + ' m', 'MIN_SEP 未降级', '2×AGV_RADIUS，全程采样 0 违规'],
    [D.paths.total_keyframes, '真实路径关键帧', '21 台 AGV 闭环作业'],
  ].map(([n, t, p]) => '<div class="proof"><b>' + n + '</b><div>' + t + '</div><small>' + p + '</small></div>').join('');
  $('githubTable').innerHTML = '<thead><tr><th>项目</th><th>许可证</th><th>使用方式</th></tr></thead><tbody>' +
    D.github.map(g => '<tr><td>' + esc(g.repo) + '</td><td>' + esc(g.license) +
      '</td><td>' + esc(g.role) + '</td></tr>').join('') + '</tbody>';
  $('gallery').innerHTML = D.images.map((src, i) =>
    '<img src="' + src + '" data-i="' + i + '" alt="渲染图 ' + (i + 1) + '">').join('');
  $('gallery').addEventListener('click', e => {
    if (e.target.tagName !== 'IMG') return;
    $('lightboxImg').src = e.target.src;
    $('lightboxCap').textContent = 'Blender 5.2 程序化渲染 · ' + e.target.alt;
    $('lightbox').classList.add('on');
  });
  $('lightbox').onclick = () => $('lightbox').classList.remove('on');
  $('limitsList').innerHTML = [
    '演示价格（prices_source=demo）不可用于真实经营决策；所有场景 1h 班次均亏损。',
    'scale_50 场景有 50 台车，但只有 30 台实际接单移动，空闲车如实报告。',
    'LIF 是语义对齐的自研草稿格式，非 VDA 5050 认证；边为示意连通关系。',
    '未对接任何真实 WMS；适配器 ack 默认 dry-run，连接密钥仅用环境变量名引用。',
    'CBS/L3 求解器标 experimental（D3）；真实 DXF 复杂图纸需人工描图确认（G2）。',
    '碰撞结论为采样验证 + 预约表复核，不宣称形式化保证；无物理级仿真 / 多楼层。',
  ].map(x => '<li>' + x + '</li>').join('');

  // ---------------------------------------------------------------- submit
  const intro = D.project.name + '\n\n' + D.project.tagline + '。\n' +
    D.project.description + '\n\n' +
    '作品以 ' + D.project.stats.warehouses + ' 个仓库、' + D.project.stats.racks + ' 组货架、' +
    D.project.stats.locations + ' 个库位与 ' + D.project.stats.robots + ' 台 AGV 为对象，' +
    '实现 tick 级离散事件仿真（持续订单、在线派单、电量/充电、故障重派、时空预约）、' +
    '事件溯源运营账本与损益表，并通过可视化布局编辑器、DXF/LIF 草稿导入和自定义场景面板支持定制。\n\n' +
    '全部指标可独立复算：既有验收 A1–A40 40/40，真仿真 S1–S9 共 ' + v.S.checks +
    ' 项检查、盈亏 F1–F7 共 ' + v.F.checks + ' 项检查、pytest ' + v.pytest +
    ' 项用例全部通过；GitHub 开源对照已逐项核实许可证并区分代码衍生与概念参照。\n\n' +
    '技术栈：Python 标准库、Blender 5.2 (bpy)、Canvas 2D / WebGL、零后端静态发布。';
  $('introBox').value = intro;
  $('btnCopyIntro').onclick = () => {
    const t = $('introBox');
    t.select();
    try { document.execCommand('copy'); $('copyMsg').textContent = '已复制到剪贴板'; }
    catch (e) { $('copyMsg').textContent = '请手动全选复制'; }
  };
  $('submitInfo').innerHTML = [
    ['作品名称', D.project.name], ['作品类型', '网页应用（零后端静态站）'],
    ['技术栈', D.project.tech.join(' / ')], ['仓库数量', D.project.stats.warehouses],
    ['场景数量', D.scenarios.length + ' 内置 + 自定义'], ['损益运行数', D.pnl.length],
    ['许可证', 'MIT（第三方声明见 THIRD_PARTY_NOTICES.md）'],
    ['数据来源', 'out/*.json 真实报告快照，非手工编造'],
    ['在线链接', '发布后填入（见下方说明）'],
  ].map(([k, val]) => '<tr><th>' + k + '</th><td>' + esc(val) + '</td></tr>').join('');
  $('shareSteps').innerHTML = [
    '打开创作这个作品的 WorkBuddy 对话，点击页面右上角「分享」按钮。',
    '选择要分享的对话（建议只勾选本作品相关对话，避免带入无关内容）。',
    '在底部工具栏选择「链接」，复制生成的地址。',
    '把链接填入活动表单「05 WorkBuddy 创作对话记录」。',
    '<strong>提交前重新打开一次链接</strong>，确认评审人员可以正常访问（未登录也可查看）。',
    '把本作品页的在线链接一并填入作品地址栏。',
  ].map(x => '<li>' + x + '</li>').join('');
  $('privacyWarn').innerHTML = '<strong>分享前务必检查</strong>：确认对话中不含个人隐私、' +
    '商业机密等敏感信息。本项目已做过一次扫描：源码、文档、配置中<strong>无个人绝对路径</strong>，' +
    '<code>config/integrations.json</code> 不含任何密钥（只用环境变量名）。' +
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
    'python -m pytest -q                    # 35 项\n' +
    'python scripts/build_submission.py     # 重建本页数据\n' +
    'python -m dc_twin.cli serve            # 本地控制台（含运营 / 场景面板）\n' +
    'python -m http.server 8000 -d submission   # 本地预览本作品页';

  // ---------------------------------------------------------------- start
  const q = new URLSearchParams(location.search);
  if (q.get('tab')) switchTab(q.get('tab'));
})();
