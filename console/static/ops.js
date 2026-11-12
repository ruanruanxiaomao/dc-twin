/* 运营损益板 + 自定义场景面板
 * 依赖：app.js 已加载；本文件只做增量，不改主链前端逻辑。
 *
 * 损益这块以前是 400px 侧栏里的一串色块，收入/成本/利润挤在一起，
 * 瀑布、构成、敏感性、明细全塞不下——用户看不出"钱到底亏在哪"。
 * 现在改成整幅工作区：上面 8 张 KPI 卡，下面四张图 + 明细表 + 诊断。
 * 图表全部手写 SVG，不引第三方库（离线 exe 里也要能画）。
 */
'use strict';

(function () {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const f2 = (n) => (typeof n === 'number' && isFinite(n) ? n.toFixed(2) : '—');
  const f0 = (n) => (typeof n === 'number' && isFinite(n) ? Math.round(n).toLocaleString('en-US') : '—');
  const pct = (n) => (typeof n === 'number' && isFinite(n) ? (n * 100).toFixed(1) + '%' : '—');
  const cls = (n) => (n >= 0 ? 'pos' : 'neg');

  let SCENARIOS = [], COST = null, RUNS = [], opsReady = false, scenReady = false;
  let selectedRun = null;

  async function api(path, body) {
    const opt = body === undefined ? {} : {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    };
    const r = await fetch(path, opt);
    return r.json();
  }

  // ------------------------------------------------------------ 场景面板
  const FIELDS = [
    ['id', '场景 ID', 'text', '只允许字母数字下划线，不能与内置 8 个重名'],
    ['name', '名称', 'text', ''],
    ['seed', '随机种子', 'number', '同 seed 可逐 tick 复现'],
    ['per_hour', '订单到达率（单/h）', 'number', '1–1000'],
    ['fleet', '车队规模（台）', 'number', '1–200'],
    ['horizon', '仿真时长（s）', 'number', '试算固定 600s，正式运行用此值'],
    ['mix.INBOUND', '入库占比', 'number', ''],
    ['mix.OUTBOUND', '出库占比', 'number', ''],
    ['mix.PICK', '拣选占比', 'number', ''],
    ['mix.REPLENISH', '补货占比', 'number', ''],
    ['mix.TRANSFER', '移库占比', 'number', ''],
    // 策略层：以前这三项写死在引擎里，只能看不能改。现在随场景保存，
    // 存两个场景（只改策略）= 一次策略 A/B，结果直接进「运营 → 场景对比」。
    ['policy.dispatch', '派单策略', 'select', '选空闲车的规则',
      [['nearest_first', '最近的车优先'], ['farthest_first', '最远的车优先'],
       ['highest_soc', '电量最高的优先'], ['lowest_soc', '电量最低的优先'],
       ['least_completed', '完成单数最少的优先（均衡）']]],
    ['policy.charge', '充电策略', 'select', '何时去充电',
      [['opportunistic', '低电才充（不打断任务）'], ['threshold', '低于 22% 就补电']]],
    ['policy.reserve', '电量保留（%）', 'number', '低于此值不派单，留给回程'],
  ];

  function formHTML(d) {
    let h = '';
    for (const [k, label, type, hint] of FIELDS) {
      const v = k.startsWith('mix.') ? (d.overrides['demand.orders.mix'] || {})[k.slice(4)]
        : d._form[k];
      const lab = `<label for="sf_${esc(k)}">${esc(label)}${hint ? '<small>' + esc(hint) + '</small>' : ''}</label>`;
      if (type === 'select') {
        const opts = (FIELDS.find((f) => f[0] === k) || [])[4] || [];
        h += `<div class="field">${lab}<select id="sf_${esc(k)}" data-k="${esc(k)}" class="sel">`
          + opts.map(([val, txt]) => `<option value="${esc(val)}"${val === v ? ' selected' : ''}>${esc(txt)}</option>`).join('')
          + '</select></div>';
      } else {
        h += `<div class="field">${lab}`
          + `<input id="sf_${esc(k)}" data-k="${esc(k)}" type="${type}" value="${esc(v)}"></div>`;
      }
    }
    h += `<div style="margin-top:6px"><label style="font-size:11px;color:var(--text-2)">干扰事件（JSON 数组，如 [{"type":"dock_blocked","dock":"W-OU-02","start_s":100,"duration_s":300}]）</label>`
      + `<textarea id="sf_disruptions" style="width:100%;height:52px;font:11px monospace">${esc(JSON.stringify(d.disruptions || []))}</textarea></div>`;
    h += `<div style="margin-top:6px"><label style="font-size:11px;color:var(--text-2)">断言（JSON 数组，必须可能失败）</label>`
      + `<textarea id="sf_assertions" style="width:100%;height:52px;font:11px monospace">${esc(JSON.stringify(d.assertions || []))}</textarea></div>`;
    return h;
  }

  function normalize(d) {
    const o = Object.assign({
      schema_version: '1.0', id: 'my_scenario', name: '我的自定义场景',
      layout_spec: '../layout_spec.json', fleet_spec: '../fleet_spec.json',
      demand_spec: '../demand_spec.json', seed: 20261109,
      simulation: { warmup_s: 0, duration_s: 3600, dt_s: 0.1, fps: 8, max_wall_clock_s: 900 },
      overrides: {}, disruptions: [], assertions: [], outputs: [],
    }, d || {});
    o.overrides = Object.assign({
      'demand.orders.per_hour': 60, 'fleet.size': 24,
      'demand.orders.mix': { INBOUND: 0.2, OUTBOUND: 0.25, PICK: 0.25, REPLENISH: 0.15, TRANSFER: 0.15 },
    }, o.overrides || {});
    o._form = {
      id: o.id, name: o.name, seed: o.seed,
      per_hour: o.overrides['demand.orders.per_hour'],
      fleet: o.overrides['fleet.size'],
      horizon: o.simulation.duration_s,
      'policy.dispatch': o.overrides['dispatch.policy'] || 'nearest_first',
      'policy.charge': o.overrides['battery.charge_policy'] || 'opportunistic',
      'policy.reserve': o.overrides['dispatch.battery_reserve_pct'] == null
        ? 20 : o.overrides['dispatch.battery_reserve_pct'],
    };
    return o;
  }

  function setForm(d) {
    const n = normalize(d);
    $('scenForm').innerHTML = formHTML(n);
    $('scenForm').dataset.rawId = n.id;
    showResult({ info: '已载入 ' + n.id + '，修改后先「校验」再「试算 / 保存」' });
  }

  function collect() {
    const d = normalize(null);
    document.querySelectorAll('#scenForm input[data-k]').forEach((i) => {
      const k = i.dataset.k, v = i.value;
      if (k.startsWith('mix.')) d.overrides['demand.orders.mix'][k.slice(4)] = Number(v);
      else if (['seed', 'per_hour', 'fleet', 'horizon', 'policy.reserve'].includes(k)) d._form[k] = Number(v);
      else d._form[k] = v;
    });
    d.id = d._form.id; d.name = d._form.name; d.seed = d._form.seed;
    d.overrides['demand.orders.per_hour'] = d._form.per_hour;
    d.overrides['fleet.size'] = d._form.fleet;
    d.overrides['dispatch.policy'] = d._form['policy.dispatch'];
    d.overrides['battery.charge_policy'] = d._form['policy.charge'];
    d.overrides['dispatch.battery_reserve_pct'] = d._form['policy.reserve'];
    d.simulation.duration_s = d._form.horizon;
    d.simulation.max_wall_clock_s = Math.max(900, d._form.horizon / 2);
    try { d.disruptions = JSON.parse($('sf_disruptions').value); }
    catch (e) { d.disruptions = 'INVALID_JSON'; }
    try { d.assertions = JSON.parse($('sf_assertions').value); }
    catch (e) { d.assertions = 'INVALID_JSON'; }
    d.outputs = ['sim_kpi.json', 'sim_events.jsonl', 'pnl.json'];
    delete d._form;
    return d;
  }

  function showResult(obj) {
    $('scenResult').textContent = typeof obj === 'string' ? obj : JSON.stringify(obj, null, 2);
  }

  async function loadScenarios() {
    const r = await api('/api/scenarios');
    SCENARIOS = r.scenarios || [];
    $('scenList').innerHTML = SCENARIOS.map((s) => {
      const meta = `${s.per_hour || '—'} 单/h · ${s.fleet_size || '—'} 台 · ${(s.disruptions || []).length} 干扰`
        + (s.dispatch && s.dispatch !== 'nearest_first' ? ` · 派单 ${s.dispatch}` : '');
      const custom = s.kind === 'custom';
      return `<div class="scen-item" data-id="${esc(s.file)}" data-sid="${esc(s.id)}">`
        + `<span class="nm">${esc(s.name || s.id)}</span>`
        + `<span class="kd${custom ? ' custom' : ''}">${custom ? '自定义' : '内置'}</span>`
        + `<span class="mt">${esc(s.id)} · ${esc(meta)}</span></div>`;
    }).join('') || '<div class="muted" style="padding:8px">没有可用场景</div>';

    $('scenList').querySelectorAll('[data-id]').forEach((n) => {
      n.addEventListener('click', async () => {
        $('scenList').querySelectorAll('.scen-item').forEach((x) => x.classList.toggle('sel', x === n));
        const s = SCENARIOS.find((x) => x.file === n.dataset.id);
        if (!s) return;
        if (s.kind !== 'custom') {
          const t = await api('/api/scenario/template?id=' + encodeURIComponent(s.id + '_copy'));
          const c = normalize(t.template);
          c.id = s.id + '_copy'; c.name = s.name + '（副本）';
          c.overrides['demand.orders.per_hour'] = s.per_hour || 60;
          c.overrides['fleet.size'] = s.fleet_size || 21;
          c.disruptions = s.disruptions || [];
          c.assertions = s.assertions || [];
          setForm(c);
          showResult({ info: '内置场景只读，已创建副本 ' + c.id + '，保存后即为自定义场景' });
        } else {
          const g = await api('/api/scenario/get?id=' + encodeURIComponent(s.id));
          if (g.ok) { setForm(g.scenario); showResult({ info: '已载入自定义场景 ' + s.id + '，来源 ' + g.path }); }
          else showResult(g);
        }
      });
    });
  }

  async function ensureScen() {
    if (scenReady) return;
    scenReady = true;
    try {
      await loadScenarios();
      const t = await api('/api/scenario/template?id=my_scenario');
      setForm(t.template);
    } catch (e) { showResult({ error: String(e) }); }
  }

  function bindScen() {
    if (!$('btnScenNew')) return;
    $('btnScenNew').onclick = async () => {
      const t = await api('/api/scenario/template?id=my_scenario_' + Date.now().toString().slice(-5));
      setForm(t.template);
    };
    $('btnScenValidate').onclick = async () => {
      showResult(await api('/api/scenario/validate', { scenario: collect() }));
    };
    $('btnScenDry').onclick = async () => {
      showResult({ info: '正在试算 600 s，请稍候…' });
      showResult(await api('/api/scenario/dry-run', { scenario: collect(), horizon: 600 }));
    };
    $('btnScenSave').onclick = async () => {
      const r = await api('/api/scenario/save', { scenario: collect() });
      showResult(r);
      await loadScenarios();
      scenReady = true;
    };
    $('btnScenRun').onclick = async () => {
      showResult({ info: '正在运行完整仿真 + 账本 + 损益，请稍候…' });
      const d = collect();
      showResult(await api('/api/scenario/run', { id: d.id, horizon: d.simulation.duration_s }));
      await refreshRuns();
    };
    $('btnScenDelete').onclick = async () => {
      const d = collect();
      if (!confirm('确认删除自定义场景 ' + d.id + '？内置场景无法删除。')) return;
      const r = await api('/api/scenario/delete', { id: d.id });
      showResult(r);
      await loadScenarios();
    };
  }

  // ------------------------------------------------------------ SVG 工具
  const C = {
    revenue: '#2563eb', variable: '#f97316', fixed: '#94a3b8',
    profit: '#16a34a', loss: '#dc2626', grid: '#eef1f5', axis: '#dfe5ee', text: '#475467',
  };
  const DONUT = ['#2563eb', '#0ea5e9', '#14b8a6', '#f59e0b', '#f97316', '#8b5cf6',
    '#64748b', '#94a3b8', '#ef4444', '#22c55e'];

  function svg(w, h, body, extra) {
    return `<svg class="chart" viewBox="0 0 ${w} ${h}" preserveAspectRatio="xMidYMid meet" `
      + `role="img" ${extra || ''}>${body}</svg>`;
  }

  /* 取"整"刻度：直接按 (hi-lo)/4 画会得到 352/-196/-744/-1292 这种读不出来的轴 */
  function niceTicks(lo, hi, n) {
    n = n || 4;
    const span = Math.max(hi - lo, 1e-6);
    const mag = Math.pow(10, Math.floor(Math.log10(span / n)));
    const norm = span / n / mag;
    const step = (norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 2.5 ? 2.5 : norm <= 5 ? 5 : 10) * mag;
    const start = Math.floor(lo / step) * step;
    const out = [];
    // 必须**覆盖**两端：只画到 <=hi 的话，收入柱会顶出图表上沿，
    // 数字直接压到标题上（第一版就是这样）。
    for (let v = start; v < hi - step * 1e-6; v += step) out.push(Math.round(v / step) * step);
    const last = out[out.length - 1];
    if (last < hi - step * 1e-6) out.push(Math.round((last + step) / step) * step);
    return out;
  }

  function money(n) {
    const a = Math.abs(n);
    if (a >= 10000) return (n / 10000).toFixed(1) + ' 万';
    return n.toFixed(a >= 100 ? 0 : 1);
  }

  /* 瀑布图：收入一根柱子，成本两根往下减，利润落在累计值上。 */
  function waterfall(p) {
    const W = 620, H = 215, padL = 48, padR = 12, padT = 16, padB = 30;
    const steps = [
      ['收入', p.revenue_total || 0, 'revenue'],
      ['变动成本', -(p.variable_cost_total || 0), 'variable'],
      ['固定成本', -(p.fixed_cost_total || 0), 'fixed'],
      ['运营利润', p.operating_profit || 0, 'profit'],
    ];
    const cum = [0];
    steps.forEach((s, i) => cum.push(cum[i] + s[1]));
    // 纵轴按"实际出现过的累计值"取域，不要用对称域：利润 −567 时
    // 对称域会从 +1133 起，一半画布是空的，柱子被压成薄片。
    const rawHi = Math.max(0, ...cum), rawLo = Math.min(0, ...cum);
    const ticks = niceTicks(rawLo, rawHi, 4);
    const hi = ticks[ticks.length - 1], lo = ticks[0];
    const span = Math.max(hi - lo, 1);
    const y = (v) => padT + (hi - v) / span * (H - padT - padB);
    const bw = (W - padL - padR) / steps.length;
    let b = '';
    for (const v of ticks) {
      b += `<line class="gl" x1="${padL}" x2="${W - padR}" y1="${y(v)}" y2="${y(v)}"/>`;
      b += `<text x="${padL - 6}" y="${y(v) + 3}" text-anchor="end" class="lbl">${money(v)}</text>`;
    }
    b += `<line class="ax" x1="${padL}" x2="${W - padR}" y1="${y(0)}" y2="${y(0)}"/>`;
    steps.forEach((s, i) => {
      const top = Math.max(cum[i], cum[i + 1]);
      const bot = Math.min(cum[i], cum[i + 1]);
      const x = padL + i * bw + bw * 0.16;
      const w = bw * 0.68;
      const col = s[1] >= 0 ? (s[2] === 'profit' ? C.profit : C.revenue)
        : (s[2] === 'variable' ? C.variable : C.fixed);
      const hh = Math.max(2, y(bot) - y(top));
      b += `<rect x="${x}" y="${y(top)}" width="${w}" height="${hh}" rx="3" fill="${col}" opacity=".92"/>`;
      b += `<text x="${x + w / 2}" y="${y(top) - 5}" text-anchor="middle" font-weight="700" fill="${col}">`
        + `${s[1] >= 0 ? '+' : '−'}${money(Math.abs(s[1]))}</text>`;
      b += `<text x="${x + w / 2}" y="${H - padB + 15}" text-anchor="middle" class="lbl">${s[0]}</text>`;
      if (i < steps.length - 1) {
        b += `<line class="gl" x1="${x + w}" x2="${x + bw}" y1="${y(cum[i + 1])}" y2="${y(cum[i + 1])}"/>`;
      }
    });
    return svg(W, H, b, 'aria-label="损益瀑布"');
  }

  function donut(acc) {
    const items = Object.entries(acc || {})
      .filter(([k]) => k.startsWith('Cost:'))
      .map(([k, v]) => [k.split(':').pop(), Math.abs(v)])
      .filter(([, v]) => v > 0)
      .sort((a, b) => b[1] - a[1]);
    const total = items.reduce((s, x) => s + x[1], 0);
    if (!total) return '<div class="muted">没有成本科目</div>';
    const W = 300, H = 210, cx = 105, cy = 100, R = 78, r = 48;
    let a0 = -Math.PI / 2, body = '';
    items.forEach(([name, v], i) => {
      const a1 = a0 + v / total * Math.PI * 2;
      const big = a1 - a0 > Math.PI ? 1 : 0;
      const p = (a, rr) => [cx + rr * Math.cos(a), cy + rr * Math.sin(a)];
      const [x0, y0] = p(a0, R), [x1, y1] = p(a1, R);
      const [x2, y2] = p(a1, r), [x3, y3] = p(a0, r);
      body += `<path d="M${x0} ${y0}A${R} ${R} 0 ${big} 1 ${x1} ${y1}`
        + `L${x2} ${y2}A${r} ${r} 0 ${big} 0 ${x3} ${y3}Z" `
        + `fill="${DONUT[i % DONUT.length]}" opacity=".9"><title>${esc(name)} ${f2(v)} CNY `
        + `(${pct(v / total)})</title></path>`;
      a0 = a1;
    });
    body += `<text x="${cx}" y="${cy - 4}" text-anchor="middle" class="lbl">总成本</text>`;
    body += `<text x="${cx}" y="${cy + 15}" text-anchor="middle" font-size="15" font-weight="700" `
      + `fill="#121821">${money(total)}</text>`;
    const legend = items.slice(0, 6).map(([name, v], i) =>
      `<div><i style="background:${DONUT[i % DONUT.length]}"></i>${esc(name)} `
      + `<b>${f2(v)}</b> <span class="muted">${pct(v / total)}</span></div>`).join('');
    return svg(W, H, body, 'aria-label="成本构成"')
      + `<div class="legend-inline">${legend}</div>`;
  }

  /* 敏感性龙卷风：每行一个参数，以基准利润为中线，左右各一条 */
  function tornado(sens, base) {
    const rows = Object.entries(sens || {}).map(([k, v]) => [k, v]);
    if (!rows.length) return '<div class="muted">没有敏感性数据</div>';
    const W = 620, rowH = 44, H = rows.length * rowH + 34, padL = 168, padR = 74;
    const vals = rows.flatMap(([, v]) => [v.low, v.high, base]).filter((x) => typeof x === 'number');
    const lo = Math.min(...vals), hi = Math.max(...vals), span = Math.max(hi - lo, 1);
    const x = (v) => padL + (v - lo) / span * (W - padL - padR);
    let b = `<line class="ax" x1="${x(base)}" x2="${x(base)}" y1="10" y2="${H - 22}"/>`;
    b += `<text x="${x(base)}" y="${H - 8}" text-anchor="middle" class="lbl">基准 ${f2(base)}</text>`;
    rows.forEach(([k, v], i) => {
      const cy = 18 + i * rowH;
      const a = Math.min(x(v.low), x(v.high)), bb = Math.max(x(v.low), x(v.high));
      b += `<rect x="${a}" y="${cy}" width="${Math.max(2, bb - a)}" height="13" rx="3" `
        + `fill="${v.high >= base ? C.profit : C.loss}" opacity=".28"/>`;
      b += `<circle cx="${x(v.low)}" cy="${cy + 6.5}" r="3.4" fill="${C.loss}"/>`;
      b += `<circle cx="${x(v.high)}" cy="${cy + 6.5}" r="3.4" fill="${C.profit}"/>`;
      // 备注名最长 14 个汉字：再长就压到图里去了，宁可截断留 tooltip
      const note = (v.note || k).replace('，', ' · ');
      const short = note.length > 15 ? note.slice(0, 14) + '…' : note;
      b += `<text x="${padL - 10}" y="${cy + 10}" text-anchor="end" class="lbl">`
        + `<title>${esc(note)}</title>${esc(short)}</text>`;
      const d = (x2) => (x2 - base >= 0 ? '+' : '') + f2(x2 - base);
      b += `<text x="${W - padR + 4}" y="${cy + 10}" font-size="10.5">`
        + `<tspan fill="${C.loss}">${d(v.low)}</tspan>`
        + `<tspan fill="#c3cad6"> / </tspan>`
        + `<tspan fill="${C.profit}">${d(v.high)}</tspan></text>`;
    });
    return svg(W, H, b, 'aria-label="敏感性"');
  }

  /* 场景对比：横向条，正负分色，另附一张表 */
  function compare(runs, cur) {
    if (!runs || !runs.length) return '<div class="muted">没有可比运行</div>';
    const rows = runs.slice().sort((a, b) => (b.profit || 0) - (a.profit || 0));
    const W = 620, rowH = 30, H = rows.length * rowH + 26, padL = 150, padR = 76;
    const lo = Math.min(0, ...rows.map((r) => r.profit || 0));
    const hi = Math.max(0, ...rows.map((r) => r.profit || 0));
    const span = Math.max(hi - lo, 1);
    const x = (v) => padL + (v - lo) / span * (W - padL - padR);
    let b = `<line class="ax" x1="${x(0)}" x2="${x(0)}" y1="6" y2="${H - 18}"/>`;
    rows.forEach((r, i) => {
      const v = r.profit || 0;
      const y = 8 + i * rowH;
      const x0 = Math.min(x(0), x(v)), w = Math.max(2, Math.abs(x(v) - x(0)));
      b += `<rect x="${x0}" y="${y}" width="${w}" height="15" rx="3" `
        + `fill="${v >= 0 ? C.profit : C.loss}" opacity="${r.run_id === cur ? 1 : .55}"/>`;
      const name = r.run_id.length > 26 ? r.run_id.slice(0, 25) + '…' : r.run_id;
      b += `<text x="${padL - 8}" y="${y + 11}" text-anchor="end" class="lbl">${esc(name)}</text>`;
      b += `<text x="${W - padR + 4}" y="${y + 11}" font-weight="600" fill="${v >= 0 ? C.profit : C.loss}">`
        + `${f2(v)}</text>`;
    });
    const tbl = `<div class="table-wrap" style="margin-top:10px"><table class="tbl">`
      + '<thead><tr><th>运行</th><th class="n">完成单量</th><th class="n">收入</th>'
      + '<th class="n">变动成本</th><th class="n">固定成本</th><th class="n">运营利润</th></tr></thead><tbody>'
      + rows.map((r) => `<tr${r.run_id === cur ? ' class="sum"' : ''}><td>${esc(r.run_id)}</td>`
        + `<td class="n">${f0(r.orders)}</td><td class="n">${f2(r.revenue)}</td>`
        + `<td class="n">${f2(r.variable)}</td><td class="n">${f2(r.fixed)}</td>`
        + `<td class="n ${cls(r.profit)}">${f2(r.profit)}</td></tr>`).join('')
      + '</tbody></table></div>';
    return svg(W, H, b, 'aria-label="场景对比"') + tbl;
  }

  // ------------------------------------------------------------ 运营面板
  function kpiCard(label, value, unit, note, tone) {
    return `<div class="kpi-card${tone ? ' ' + tone : ''}"><div class="k">${esc(label)}</div>`
      + `<div class="v">${value}${unit ? `<span class="u">${esc(unit)}</span>` : ''}</div>`
      + (note ? `<div class="d">${note}</div>` : '') + '</div>';
  }

  function renderPnl(data) {
    const p = data.pnl || {}, m = data.meta || {};
    const rev = p.revenue_total || 0, cost = p.total_cost || 0, prof = p.operating_profit || 0;
    const margin = rev ? prof / rev : 0;
    const badge = (t, c) => `<span class="chip" style="border-color:${c}33;background:${c}12">`
      + `<span class="k">${esc(t[0])}</span> <b style="color:${c}">${esc(t[1])}</b></span>`;
    $('opsBadges').innerHTML =
      badge(['账本引擎', p.engine || '—'], '#2563eb')
      + badge(['价格来源', p.prices_source || '—'], p.prices_source === 'demo' ? '#d97706' : '#16a34a')
      + badge(['记账模式', (m.mode || p.mode || '—')], '#475467')
      + badge(['账本分录', f0(p.ledger_postings) + ' 笔'], '#8b5cf6')
      + (p.prices_source === 'demo'
        ? '<span class="chip" style="border-color:#fcd34d;background:#fef3c7"><b style="color:#92400e">演示价格，不可用于真实经营决策</b></span>' : '');

    const gap = (p.breakeven_orders || 0) - (p.orders_completed || 0);
    $('opsKpis').innerHTML =
      kpiCard('收入', f2(rev), 'CNY', `${f0(p.orders_completed)} 单 / ${f2(p.run_hours)} h`)
      + kpiCard('总成本', f2(cost), 'CNY', `变动 ${f2(p.variable_cost_total)} + 固定 ${f2(p.fixed_cost_total)}`)
      + kpiCard('运营利润', f2(prof), 'CNY', `利润率 ${pct(margin)}`, prof >= 0 ? 'good' : 'bad')
      + kpiCard('单均利润', f2(p.per_order && p.per_order.profit), 'CNY/单',
        `单均收入 ${f2(p.per_order && p.per_order.revenue)}`, (p.per_order || {}).profit >= 0 ? 'good' : 'bad')
      + kpiCard('边际贡献', f2(p.contribution_margin), 'CNY', '收入 − 变动成本')
      + kpiCard('盈亏平衡单量', f0(p.breakeven_orders), '单',
        gap > 0 ? `还差 ${f0(gap)} 单` : `已超 ${f0(-gap)} 单`, gap > 0 ? 'bad' : 'good');

    $('opsWaterfall').innerHTML = waterfall(p);
    $('opsDonut').innerHTML = donut(p.accounts);
    $('opsTornado').innerHTML = tornado(p.sensitivity, prof);
    $('opsCompare').innerHTML = compare(RUNS, selectedRun);
    $('opsStatement').innerHTML = statement(p);
    $('opsDiagnosis').innerHTML = diagnosis(p);
  }

  function statement(p) {
    const acc = p.accounts || {};
    const groups = [
      ['收入', 'Revenue:', 1],
      ['变动成本', 'Cost:Variable:', -1],
      ['固定成本', 'Cost:Fixed:', -1],
    ];
    let b = '<div class="table-wrap"><table class="tbl"><thead><tr><th>科目</th>'
      + '<th class="n">金额 CNY</th><th class="n">占收入</th><th class="n">占总成本</th></tr></thead><tbody>';
    const rev = p.revenue_total || 0, cost = p.total_cost || 0;
    groups.forEach(([label, prefix, sign]) => {
      const rows = Object.entries(acc).filter(([k]) => k.startsWith(prefix));
      if (!rows.length) return;
      b += `<tr class="sum"><td colspan="4">${label}</td></tr>`;
      rows.forEach(([k, v]) => {
        const name = k.slice(prefix.length);
        const amt = sign * v;
        // 收入行算"占总成本"没有意义，留空
        const costShare = sign < 0 && cost ? pct(Math.abs(amt) / cost) : '—';
        b += `<tr><td>${esc(name)}</td><td class="n">${f2(amt)}</td>`
          + `<td class="n">${rev ? pct(Math.abs(amt) / rev) : '—'}</td>`
          + `<td class="n">${costShare}</td></tr>`;
      });
      const sub = rows.reduce((acc2, [, v]) => acc2 + v, 0) * sign;
      b += `<tr class="sum"><td>${label}小计</td><td class="n">${f2(sub)}</td>`
        + `<td class="n">${rev ? pct(Math.abs(sub) / rev) : '—'}</td>`
        + `<td class="n">${sign < 0 && cost ? pct(Math.abs(sub) / cost) : '—'}</td></tr>`;
    });
    b += `<tr class="total"><td>运营利润（收入 − 总成本）</td>`
      + `<td class="n ${cls(p.operating_profit)}">${f2(p.operating_profit)}</td>`
      + `<td class="n">${rev ? pct((p.operating_profit || 0) / rev) : '—'}</td><td class="n">—</td></tr>`;
    b += '</tbody></table></div>';
    if (p.scope) b += `<div class="muted" style="font-size:10.5px;margin-top:8px">${esc(p.scope)}</div>`;
    return b;
  }

  /* 亏损诊断：不讲"亏了"，讲"差多少、差在哪、改哪个参数能补回来" */
  function diagnosis(p) {
    const rows = [];
    const rev = p.revenue_total || 0, prof = p.operating_profit || 0;
    const fixed = p.fixed_cost_total || 0, var_ = p.variable_cost_total || 0;
    const be = p.breakeven_orders || 0, done = p.orders_completed || 0;
    const cm = p.contribution_margin || 0;
    const unitCm = done ? cm / done : 0;

    if (prof < 0) {
      const need = be - done;
      rows.push(['!', 'bad',
        `本班<b>亏 ${f2(-prof)} CNY</b>：做到 <b>${f0(done)}</b> 单，收支平衡要 <b>${f0(be)}</b> 单，`
        + `差 <b>${f0(Math.max(0, need))}</b> 单（${done ? (be / done).toFixed(2) : '—'} 倍）。`]);
    } else {
      rows.push(['✓', 'ok', `本班<b>盈利 ${f2(prof)} CNY</b>，已越过盈亏平衡点 ${f0(be)} 单。`]);
    }

    if (fixed > 0) {
      const share = fixed / Math.max(fixed + var_, 1);
      rows.push(['◧', share > 0.85 ? 'warn' : '', 
        `固定成本占总成本 <b>${pct(share)}</b>（${f2(fixed)} CNY），`
        + `其中人工/折旧/租金是大头。固定成本与单量无关，`
        + `所以<b>单量越低，摊到每单的固定成本越高</b>：本班 ${f0(done)} 单 → `
        + `<b>${f2(done ? fixed / done : 0)}</b> CNY/单。`]);
    }

    if (unitCm > 0 && be > 0) {
      const needHours = (be * 3600) / Math.max(p.orders_completed ? (p.run_hours * 3600) / Math.max(done, 1) : 1, 1);
      rows.push(['◷', 'warn',
        `按当前节拍（${f2(done / Math.max(p.run_hours, 0.01))} 单/h），补到 ${f0(be)} 单需要 `
        + `<b>${(needHours / 3600).toFixed(1)} h</b>。单班只有 ${f2(p.run_hours)} h，`
        + `这段缺口<b>不可能靠"再跑一会儿"补上</b>，只能提单量或提单价。`]);
    }

    if (var_ >= 0) {
      const energy = (p.accounts || {})['Cost:Variable:Energy'] || 0;
      rows.push(['⚡', '',
        `变动成本只有 <b>${f2(var_)}</b> CNY（其中电费 ${f2(energy)}），`
        + `占收入 ${pct(rev ? var_ / rev : 0)}——<b>能耗不是亏损主因</b>，`
        + `把电价砍一半也只省 ${f2(energy / 2)}。`]);
    }

    const sens = p.sensitivity || {};
    const fl = sens.fleet_pm2, ov = sens.order_volume_pm20, en = sens.energy_tariff_pm20;
    if (fl && ov) {
      const dOrder = Math.abs(ov.high - ov.base), dFleet = Math.abs(fl.low - fl.base);
      rows.push(['⇅', '',
        `敏感性：单量 +20% 影响 <b>${f2(dOrder)}</b>，车队 ±2 台影响 <b>${f2(dFleet)}</b>，`
        + `电价 ±20% 影响 <b>${f2(Math.abs((en || {}).high - (en || {}).base) || 0)}</b>。`
        + `要改善利润，先动<b>单量</b>，再动<b>车队规模</b>；电价可以忽略。`]);
    }
    return '<div class="diag">' + rows.map(([ic, tone, txt]) =>
      `<div class="diag-row${tone ? ' ' + tone : ''}"><span class="ic">${ic}</span><div>${txt}</div></div>`).join('')
      + '</div>';
  }

  async function renderLedger(run) {
    const r = await api('/api/ledger?run=' + encodeURIComponent(run) + '&limit=40');
    const rows = r.entries || [];
    if (!rows.length) { $('opsLedger').innerHTML = '<div class="muted">没有分录</div>'; return; }
    $('opsLedger').innerHTML = '<table class="tbl"><thead><tr><th>序号</th><th>科目</th>'
      + '<th class="n">方向</th><th class="n">金额</th><th class="n">t (s)</th><th>摘要</th></tr></thead><tbody>'
      + rows.map((e) => `<tr><td>${e.source_event_seq}</td><td>${esc(e.account)}</td>`
        + `<td class="n ${e.direction === 'credit' ? 'pos' : 'neg'}">${e.direction === 'credit' ? '贷 / +' : '借 / −'}</td>`
        + `<td class="n">${f2(e.amount)}</td><td class="n">${f2(e.t_s)}</td>`
        + `<td class="muted">${esc(e.memo)}</td></tr>`).join('')
      + '</tbody></table>';
  }

  function priceForm(spec) {
    const f = [
      ['prices_source', '价格来源', spec.prices_source, 'text'],
      ['variable_costs.energy_tariff_per_kwh', '电价 (CNY/kWh)', spec.variable_costs.energy_tariff_per_kwh, 'number'],
      ['revenue.per_order.OUTBOUND', '出库单价', spec.revenue.per_order.OUTBOUND, 'number'],
      ['revenue.per_order.PICK', '拣选单价', spec.revenue.per_order.PICK, 'number'],
      ['variable_costs.consumables_per_order', '耗材/单', spec.variable_costs.consumables_per_order, 'number'],
      ['fixed_costs_per_day.labor.hourly_rate', '人工时薪', spec.fixed_costs_per_day.labor.hourly_rate, 'number'],
    ];
    $('opsPriceForm').innerHTML = f.map(([k, label, v, t]) =>
      `<div class="field"><label for="pf_${esc(k)}">${esc(label)}</label>`
      + `<input id="pf_${esc(k)}" data-path="${esc(k)}" type="${t}" value="${esc(v)}"></div>`).join('');
  }

  async function refreshRuns() {
    const all = await api('/api/pnl?all=1');
    RUNS = all.runs || [];
    if (!$('opsRun')) return;
    const prev = selectedRun;
    $('opsRun').innerHTML = RUNS.map((r) =>
      `<option value="${esc(r.run_id)}">${esc(r.run_id)} · 利润 ${f2(r.profit)}</option>`).join('');
    const keep = prev && RUNS.some((r) => r.run_id === prev) ? prev : ($('opsRun').value || null);
    await selectRun(keep);
  }

  async function ensureOps(run) {
    if (!opsReady) {
      opsReady = true;
      const cs = await api('/api/cost-spec');
      COST = cs.spec || {};
      priceForm(COST);
      $('opsRun').onchange = () => selectRun($('opsRun').value);
      $('btnOpsClose').onclick = () => window.switchTab('agv');
    }
    await refreshRuns();
    if (run) await selectRun(run);
  }

  async function selectRun(run) {
    if (!run) return;
    selectedRun = run;
    if ($('opsRun')) $('opsRun').value = run;
    const d = await api('/api/pnl?run=' + encodeURIComponent(run));
    if (d.ok) renderPnl(d);
    await renderLedger(run);
  }

  function bindOps() {
    $('btnSavePrice').onclick = async () => {
      const patch = {};
      document.querySelectorAll('#opsPriceForm input[data-path]').forEach((i) => {
        patch[i.dataset.path] = i.dataset.path === 'prices_source' ? i.value : Number(i.value);
      });
      const r = await api('/api/cost-spec', { patch });
      $('priceMsg').textContent = r.ok
        ? '已保存 ' + Object.keys(r.applied || {}).length + ' 项；' + (r.note || '')
        : '失败：' + JSON.stringify(r.rejected || r.issues);
    };
  }

  // ------------------------------------------------------------ 启动
  document.addEventListener('DOMContentLoaded', () => {
    bindScen();
    bindOps();
    const orig = window.switchTab;
    window.switchTab = function (name) {
      if (typeof orig === 'function') orig(name);
      if (name === 'scenarios') ensureScen();
      if (name === 'ops') ensureOps();
    };
    const q = new URLSearchParams(location.search);
    if (q.get('tab') === 'scenarios') ensureScen();
    if (q.get('tab') === 'ops') ensureOps(q.get('run'));
  });
})();
