/* 运营面板 + 自定义场景面板（COMPLETION_PLAN B5 / C1）
 * 依赖：app.js 已加载；本文件只做增量，不改主链前端逻辑。
 */
'use strict';

(function () {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const num = (n, d) => (typeof n === 'number' ? n.toFixed(d === undefined ? 2 : d) : '—');
  const tag = (text, color) => `<span style="display:inline-block;padding:2px 7px;border-radius:10px;font-size:11px;margin:0 4px 4px 0;background:${color};color:#fff">${esc(text)}</span>`;

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
  ];

  function formHTML(d) {
    let h = '';
    for (const [k, label, type, hint] of FIELDS) {
      const v = k.startsWith('mix.') ? (d.overrides['demand.orders.mix'] || {})[k.slice(4)]
        : d._form[k];
      h += `<div class="field"><label for="sf_${esc(k)}">${esc(label)}${hint ? '<small>' + esc(hint) + '</small>' : ''}</label>`
        + `<input id="sf_${esc(k)}" data-k="${esc(k)}" type="${type}" value="${esc(v)}"></div>`;
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
      else if (['seed', 'per_hour', 'fleet', 'horizon'].includes(k)) d._form[k] = Number(v);
      else d._form[k] = v;
    });
    d.id = d._form.id; d.name = d._form.name; d.seed = d._form.seed;
    d.overrides['demand.orders.per_hour'] = d._form.per_hour;
    d.overrides['fleet.size'] = d._form.fleet;
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
      const meta = `${s.per_hour || '—'} 单/h · ${s.fleet_size || '—'} 台 · ${(s.disruptions || []).length} 干扰`;
      return `<div class="item" data-id="${esc(s.file)}" style="cursor:pointer;padding:6px 8px;border-bottom:1px solid var(--line-2)">`
        + `<strong>${esc(s.name || s.id)}</strong> ${tag(s.kind === 'custom' ? '自定义' : '内置冻结', s.kind === 'custom' ? '#2563eb' : '#94a3b8')}`
        + `<div style="font-size:10px;color:var(--muted)">${esc(s.id)} · ${esc(meta)}</div></div>`;
    }).join('');
    $('scenList').querySelectorAll('[data-id]').forEach((n) => {
      n.addEventListener('click', async () => {
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
    $('btnScenNew').onclick = async () => {
      const t = await api('/api/scenario/template?id=my_scenario_' + Date.now().toString().slice(-5));
      setForm(t.template);
    };
    $('btnScenValidate').onclick = async () => {
      const d = collect();
      showResult(await api('/api/scenario/validate', { scenario: d }));
    };
    $('btnScenDry').onclick = async () => {
      showResult({ info: '正在试算 600 s，请稍候…' });
      const d = collect();
      showResult(await api('/api/scenario/dry-run', { scenario: d, horizon: 600 }));
    };
    $('btnScenSave').onclick = async () => {
      const d = collect();
      const r = await api('/api/scenario/save', { scenario: d });
      showResult(r);
      await loadScenarios();
      scenReady = true;
    };
    $('btnScenRun').onclick = async () => {
      showResult({ info: '正在运行完整仿真 + 账本 + 损益，请稍候…' });
      const d = collect();
      showResult(await api('/api/scenario/run', { id: d.id, horizon: d.simulation.duration_s }));
      await ensureOps();
    };
    $('btnScenDelete').onclick = async () => {
      const d = collect();
      if (!confirm('确认删除自定义场景 ' + d.id + '？内置场景无法删除。')) return;
      const r = await api('/api/scenario/delete', { id: d.id });
      showResult(r);
      await loadScenarios();
    };
  }

  // ------------------------------------------------------------ 运营面板
  function bar(label, value, color, max) {
    const w = Math.max(2, Math.abs(value) / max * 100);
    return `<div style="margin:5px 0"><div style="font-size:10.5px;display:flex;justify-content:space-between">`
      + `<span>${esc(label)}</span><strong>${num(value)}</strong></div>`
      + `<div style="height:9px;background:var(--line-2);border-radius:4px;overflow:hidden">`
      + `<div style="height:100%;width:${w}%;background:${color}"></div></div></div>`;
  }

  function renderPnl(data) {
    const p = data.pnl || {}, m = data.meta || {};
    $('opsBadges').innerHTML = tag('engine: ' + (p.engine || '—'), '#2563eb')
      + tag('prices: ' + (p.prices_source || '—'), p.prices_source === 'demo' ? '#d97706' : '#16a34a')
      + tag('账本: ' + (m.mode || '—'), '#475467')
      + (p.prices_source === 'demo' ? '<div style="font-size:10.5px;color:var(--warn);margin:4px 0">演示价格，禁止直接用于真实经营决策</div>' : '');
    const cards = [
      ['收入', p.revenue_total, '#2563eb'], ['变动成本', p.variable_cost_total, '#ea580c'],
      ['固定成本', p.fixed_cost_total, '#94a3b8'], ['运营利润', p.operating_profit, p.operating_profit >= 0 ? '#16a34a' : '#dc2626'],
      ['完成单量', p.orders_completed, '#475467'], ['盈亏平衡', p.breakeven_orders, '#475467'],
    ];
    $('opsKpis').innerHTML = '<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:6px;margin-bottom:8px">'
      + cards.map(([k, v, c]) => `<div style="background:var(--panel-2);border:1px solid var(--line);border-radius:6px;padding:6px">`
        + `<div style="font-size:10px;color:var(--muted)">${esc(k)}</div>`
        + `<div style="font-size:14px;font-weight:600;color:${c}">${num(v, v === p.orders_completed || v === p.breakeven_orders ? 0 : 2)}</div></div>`).join('')
      + '</div>';
    const max = Math.max(Math.abs(p.revenue_total || 0), Math.abs(p.variable_cost_total || 0),
      Math.abs(p.fixed_cost_total || 0), Math.abs(p.operating_profit || 0), 1);
    $('opsWaterfall').innerHTML = bar('收入', p.revenue_total || 0, '#2563eb', max)
      + bar('变动成本与罚金', -(p.variable_cost_total || 0), '#ea580c', max)
      + bar('固定成本分摊', -(p.fixed_cost_total || 0), '#94a3b8', max)
      + bar('运营利润', p.operating_profit || 0, (p.operating_profit || 0) >= 0 ? '#16a34a' : '#dc2626', max);
  }

  async function renderLedger(run) {
    const r = await api('/api/ledger?run=' + encodeURIComponent(run) + '&limit=30');
    $('opsLedger').innerHTML = (r.entries || []).map((e) =>
      `<div style="padding:5px 8px;border-bottom:1px solid var(--line-2);font-size:10.5px">`
      + `<strong>${esc(e.account)}</strong> <span style="color:${e.direction === 'credit' ? '#16a34a' : '#dc2626'}">${e.direction === 'credit' ? '+' : '-'}${num(e.amount, 4)}</span>`
      + `<div style="color:var(--muted)">seq ${e.source_event_seq} · t=${e.t_s}s · ${esc(e.memo)}</div></div>`).join('');
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

  async function ensureOps(run) {
    if (!opsReady) {
      opsReady = true;
      const [all, cs] = await Promise.all([api('/api/pnl?all=1'), api('/api/cost-spec')]);
      RUNS = all.runs || [];
      COST = cs.spec || {};
      $('opsRun').innerHTML = RUNS.map((r) =>
        `<option value="${esc(r.run_id)}">${esc(r.run_id)} (${num(r.profit)})</option>`).join('');
      $('opsRun').onchange = () => selectRun($('opsRun').value);
      priceForm(COST);
      $('opsCompare').innerHTML = (() => {
        const b = RUNS.find((r) => r.run_id.startsWith('baseline/'));
        const p = RUNS.find((r) => r.run_id.startsWith('peak/'));
        if (!b || !p) return '<span class="muted">未找到 baseline / peak</span>';
        const d = p.profit - b.profit;
        return `<div style="font-size:11px">baseline <strong>${num(b.profit)}</strong> → peak <strong>${num(p.profit)}</strong>，`
          + `利润差 <strong style="color:${d >= 0 ? '#16a34a' : '#dc2626'}">${num(d)}</strong></div>`
          + `<div style="font-size:10px;color:var(--muted);margin-top:4px">共 ${RUNS.length} 个运行；profit 最高的：${esc((RUNS.slice().sort((a, b2) => b2.profit - a.profit)[0] || {}).run_id || '—')}</div>`;
      })();
    }
    await selectRun(run || selectedRun || ($('opsRun').value || null));
  }

  async function selectRun(run) {
    if (!run) return;
    selectedRun = run;
    $('opsRun').value = run;
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
