/* 游戏前端（P2-2）：直播模式 + 经营 HUD + 玩家面板
 *
 * 三条边界（ADR-GAME-02）：
 *   1. 前端不算时间：S.t 由服务端 `t_s` 决定，倍速/暂停只是往服务端发参数；
 *   2. 前端不算钱：现金/利润全部来自 /api/game/state，前端只格式化；
 *   3. 回放模式保留：不点「开局」时，原来的关键帧回放照旧跑（复盘模式）。
 *
 * 对外只暴露 DC_LIVE 四个钩子，app.js 用它们判断"现在该不该走直播"：
 *   DC_LIVE.active() / pos(id,t) / time() / setPaused(v) / setSpeed(v)
 */
'use strict';

(function () {
  const POLL_MS = 600;          // 与 /api/job 的轮询节奏一致
  const SPEEDS = [1, 2, 4, 8];

  const G = {
    state: null,
    timer: null,
    last: {},                   // id -> {x,y}（算朝向用）
    heading: {},                // id -> th
    lastPoll: 0,
    err: '',
  };

  const $ = (id) => document.getElementById(id);
  const el = (tag, cls, txt) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (txt !== undefined) n.textContent = txt;
    return n;
  };
  const money = (v) => (v === null || v === undefined ? '—' : Number(v).toFixed(0));
  const pct = (v) => (v === null || v === undefined ? '—' : (Number(v) * 100).toFixed(1) + '%');

  // 引擎状态 → 3D 相位（复用 viewer3d 的配色语义）
  function phaseOf(state) {
    if (!state) return 'PARK';
    if (state.indexOf('MOVING') === 0 || state === 'RETURNING' || state === 'TO_CHARGER') return 'MOVE';
    if (state === 'PICKING') return 'PICK';
    if (state === 'DROPPING') return 'DROP';
    if (state === 'CHARGING' || state === 'FAULT') return 'PARK';
    if (state === 'WAITING') return 'ROTATE';
    return 'FACE';
  }

  async function api(path, body) {
    const opt = body === undefined ? {} : {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    };
    const r = await fetch(path, opt);
    const d = await r.json().catch(() => ({}));
    if (!r.ok || d.ok === false) throw new Error(d.error || ('HTTP ' + r.status));
    return d;
  }

  // ------------------------------------------------------------------ 对外钩子
  window.DC_LIVE = {
    active() { return !!G.state && !G.state.error; },
    time() { return G.state ? G.state.t_s : null; },
    horizon() { return G.state ? G.state.horizon_s : null; },
    pos(id, t) {
      const r = G.state && G.state.robots && G.state.robots[id];
      if (!r) return null;
      const prev = G.last[id];
      const x = r.pos[0], y = r.pos[1];
      if (prev) {
        const dx = x - prev.x, dy = y - prev.y;
        if (Math.hypot(dx, dy) > 1e-6) G.heading[id] = Math.atan2(dy, dx);
      }
      G.last[id] = { x, y };
      const th = (r.state.indexOf('MOVING') === 0) ? (G.heading[id] || 0) : (G.heading[id] || Math.PI / 2);
      return { x, y, th, ph: phaseOf(r.state) };
    },
    async setPaused(v) {
      try {
        await api('/api/game/pause', { paused: !!v });
        await poll();
      } catch (e) { toast(e.message); }
    },
    async setSpeed(v) {
      const sp = SPEEDS.indexOf(Number(v)) >= 0 ? Number(v) : 1;
      try {
        await api('/api/game/speed', { speed: sp });
        toast('倍速 ' + sp + '×');
      } catch (e) { toast(e.message); }
    },
  };

  // ------------------------------------------------------------------ 渲染
  function toast(msg) {
    const t = $('gameToast');
    if (!t) return;
    t.textContent = msg;
    t.classList.add('on');
    clearTimeout(t._h);
    t._h = setTimeout(() => t.classList.remove('on'), 2200);
  }

  function render() {
    // 注意：这里必须判 gameHud（真实的容器），判一个不存在的 id 会让整个渲染静默
    // 直接 return——页面上表现为"时钟不动、卡片空白"，但控制台没有任何报错。
    const hudBox = $('gameHud');
    if (!hudBox) return;
    if (!G.state || G.state.error === 'no_session') {
      hudBox.innerHTML = '<div class="g-empty">还没开局。选一个关卡开始，或直接「自由模式」。</div>';
      const clock0 = $('gameClock');
      if (clock0) clock0.textContent = '—';
      return;
    }
    const s = G.state;
    const e = s.economy || {};
    const c = s.counts || {};
    const done = (c.TASK_COMPLETED || 0);
    const lost = (c.ORDER_LOST || 0);

    const xp = s.expiry && s.expiry.enabled ? s.expiry : null;
    const cards = [
      ['现金', '¥' + money(e.cash), e.cash < 0 ? 'bad' : ''],
      ['营收', '¥' + money(e.revenue), ''],
      ['变动成本', '¥' + money(e.variable_cost), ''],
      ['固定成本', '¥' + money(e.fixed_cost), ''],
      ['经营利润', '¥' + money(e.operating_profit),
        Number(e.operating_profit) >= 0 ? 'good' : 'bad'],
      ['完成/流失', done + ' / ' + lost, lost > 0 ? 'bad' : ''],
      // 过期报废：**不并进成本卡片**。它不计现金（采购从未入账），
      // 混进"变动成本"会让玩家以为扔货和发货同价，那是错的引导。
      ['过期报废', xp ? (xp.expired_units + ' 件 · ' + (xp.policy || '-')) : '—',
        xp && xp.expired_units > 0 ? 'bad' : ''],
    ];
    const hud = hudBox;
    hud.innerHTML = '';
    for (const [k, v, cls] of cards) {
      const d = el('div', 'g-card ' + (cls || ''));
      d.appendChild(el('div', 'g-k', k));
      d.appendChild(el('div', 'g-v', v));
      hud.appendChild(d);
    }

    // 时间/状态
    const tEl = $('gameClock');
    const mm = Math.floor(s.t_s / 60), ss = Math.floor(s.t_s % 60);
    tEl.textContent = (mm < 10 ? '0' : '') + mm + ':' + (ss < 10 ? '0' : '') + ss
      + ' · ' + (s.paused ? '暂停' : s.speed + '×')
      + (s.finished ? ' · 已结算' : '');

    // 目标
    const objBox = $('gameObjectives');
    objBox.innerHTML = '';
    const lv = s.level;
    if (lv && lv.objectives) {
      for (const o of lv.objectives) {
        const row = el('div', 'g-obj');
        row.appendChild(el('span', o.primary ? 'g-dot primary' : 'g-dot', ''));
        row.appendChild(el('span', '', o.label || (o.metric + ' ' + o.op + ' ' + o.value)));
        objBox.appendChild(row);
      }
    } else {
      objBox.appendChild(el('div', 'g-empty', '自由模式：没有胜负目标，只有现金。'));
    }

    // 破产预警
    if (e.bankrupt_warning) {
      $('gameWarn').textContent = '现金为负，宽限期结束后判负';
      $('gameWarn').classList.add('on');
    } else {
      $('gameWarn').classList.remove('on');
    }

    // 结算
    if (s.result) {
      const r = s.result;
      $('gameResult').innerHTML = r.won
        ? '通关 · ' + '★'.repeat(Math.max(1, r.stars || 1))
        : (r.bankrupt ? '破产' : '未达标');
      $('gameResult').classList.add('on');
    } else {
      $('gameResult').classList.remove('on');
    }

    // 车况
    const robots = s.robots || {};
    const ids = Object.keys(robots).sort();
    const busy = ids.filter((i) => robots[i].order).length;
    $('gameFleet').textContent = '车队 ' + ids.length + ' 台 · 在任务 ' + busy
      + ' · 排队 ' + (s.queue_depth || 0);
  }

  // ------------------------------------------------------------------ 轮询
  async function poll() {
    try {
      G.state = await api('/api/game/state');
      G.err = '';
    } catch (e) {
      G.err = e.message;
    }
    render();
  }

  function ensurePoll() {
    if (G.timer) return;
    G.timer = setInterval(poll, POLL_MS);
    poll();
  }

  // ------------------------------------------------------------------ 动作
  async function start(payload) {
    try {
      const st = await api('/api/game/start', payload);
      G.state = st;
      G.last = {};
      toast('开局：' + (st.scenario_id || ''));
      ensurePoll();
      render();
    } catch (e) { toast(e.message); }
  }

  async function cmd(type, payload) {
    try {
      await api('/api/game/command', { type, payload });
      toast('已下达：' + type);
      poll();
    } catch (e) { toast(e.message); }
  }

  async function levels() {
    try {
      const d = await api('/api/game/levels');
      return d;
    } catch (e) { toast(e.message); return { levels: [], unlocked: {} }; }
  }

  // ------------------------------------------------------------------ DOM
  function build() {
    const pane = $('pane-game');
    if (!pane || pane.dataset.built) return;
    pane.dataset.built = '1';

    pane.innerHTML = [
      '<div class="pane-sub">直播模式：时钟在服务端。这里的倍速/暂停是真的改仿真推进速度，不是改播放动画。</div>',
      '<div class="g-toolbar">',
      '  <button id="gStartFree">自由模式开局</button>',
      '  <button id="gPause">暂停/继续</button>',
      '  <span class="g-speeds" id="gSpeeds"></span>',
      '  <button id="gSave">存档</button>',
      '  <button id="gLoad">读档</button>',
      '</div>',
      '<div class="g-warn" id="gameWarn"></div>',
      '<div class="g-clock" id="gameClock">—</div>',
      '<div class="g-hud" id="gameHud"></div>',
      '<div class="g-sec-title">当前关卡目标</div>',
      '<div id="gameObjectives"></div>',
      '<div class="g-result" id="gameResult"></div>',
      '<div class="g-sec-title">关卡</div>',
      '<div id="gameLevels"></div>',
      '<div class="g-sec-title">经营动作</div>',
      '<div class="g-actions">',
      '  <label>派单策略 <select id="gPolicy">',
      '    <option value="nearest_first">最近优先</option>',
      '    <option value="farthest_first">最远优先</option>',
      '    <option value="highest_soc">电量最高优先</option>',
      '    <option value="lowest_soc">电量最低优先</option>',
      '    <option value="least_completed">最少完成优先</option>',
      '  </select></label>',
      '  <button id="gApplyPolicy">应用策略</button>',
      '  <label>买车到 <select id="gBuyWid">',
      '    <option value="W">区域仓 W</option><option value="F1">F1</option>',
      '    <option value="F2">F2</option><option value="F3">F3</option>',
      '  </select></label><button id="gBuy">买一台（capex 12 万）</button>',
      '  <label>OUTBOUND 单价 <input id="gPrice" type="number" step="1" value="30"></label>',
      '  <button id="gApplyPrice">改价</button>',
      '  <button id="gFault">注入故障（60s）</button>',
      '</div>',
      '<div class="g-fleet" id="gameFleet"></div>',
      '<div class="g-toast" id="gameToast"></div>',
    ].join('');

    const spBox = $('gSpeeds');
    for (const sp of SPEEDS) {
      const b = el('button', sp === 1 ? 'on' : '', sp + '×');
      b.onclick = () => {
        [...spBox.children].forEach((c) => c.classList.remove('on'));
        b.classList.add('on');
        window.DC_LIVE.setSpeed(sp);
      };
      spBox.appendChild(b);
    }

    $('gStartFree').onclick = () => start({ scenario_id: 'baseline', mode: 'live' });
    $('gPause').onclick = async () => {
      if (!G.state) return;
      window.DC_LIVE.setPaused(!G.state.paused);
    };
    $('gSave').onclick = async () => {
      try {
        const d = await api('/api/game/save', { slot: 'slot1' });
        toast('已存档 t=' + d.t_s + 's / ' + d.bytes + 'B');
      } catch (e) { toast(e.message); }
    };
    $('gLoad').onclick = async () => {
      try {
        const d = await api('/api/game/load', { slot: 'slot1' });
        G.state = d; G.last = {};
        toast('已读档 t=' + d.t_s + 's（默认暂停）');
        ensurePoll(); render();
      } catch (e) { toast(e.message); }
    };
    $('gApplyPolicy').onclick = () =>
      cmd('SET_POLICY', { 'dispatch.policy': $('gPolicy').value });
    $('gBuy').onclick = () => cmd('BUY_AGV', { wid: $('gBuyWid').value });
    $('gApplyPrice').onclick = () =>
      cmd('SET_PRICE', { order_type: 'OUTBOUND', price: Number($('gPrice').value) });
    $('gFault').onclick = () =>
      cmd('INJECT_FAULT', { type: 'robot_down', agent: 'ANY_BUSY', duration_s: 60 });

    renderLevels();
    // 打开页面就应该看到"现在这一局"的样子：不主动拉一次，deep-link 进来会是一片空 HUD
    ensurePoll();
  }

  async function renderLevels() {
    const box = $('gameLevels');
    if (!box) return;
    const d = await levels();
    box.innerHTML = '';
    for (const lv of d.levels || []) {
      const unlocked = d.unlocked && d.unlocked[lv.id];
      const row = el('div', 'g-level' + (unlocked ? '' : ' locked'));
      const head = el('div', 'g-level-head');
      head.appendChild(el('span', 'g-level-name', lv.name || lv.id));
      const stars = (d.meta && d.meta.levels && d.meta.levels[lv.id] && d.meta.levels[lv.id].stars) || 0;
      head.appendChild(el('span', 'g-level-stars', stars ? '★'.repeat(stars) : ''));
      row.appendChild(head);
      if (!unlocked) {
        row.appendChild(el('div', 'g-level-lock', '未解锁：先通关上一关'));
      } else {
        const b = el('button', '', '开局');
        b.onclick = () => start({ scenario_id: lv.scenario_id, mode: 'live', level_id: lv.id });
        row.appendChild(b);
      }
      box.appendChild(row);
    }
  }

  // 切到游戏页时拉一次关卡（解锁状态可能刚变）
  document.addEventListener('click', (e) => {
    const b = e.target.closest('button[data-tab]');
    if (b && b.dataset.tab === 'game') {
      build();
      renderLevels();
      if (G.state) ensurePoll();
    }
  });

  // 深链 `?tab=game`：app.js 只会切页签，不会"点"按钮，所以这里补一次构建
  document.addEventListener('DOMContentLoaded', () => {
    const q = new URLSearchParams(location.search);
    if (q.get('tab') === 'game') { build(); renderLevels(); }
  });

  window.DC_GAME = { build, poll, start };
})();
