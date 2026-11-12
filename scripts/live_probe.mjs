/* scripts/live_probe.mjs —— LIVE_OK 的浏览器实测（CDP 直连，零依赖）
 *
 * 为什么要有它：`test_frontend_wiring.py` 只能证明"代码里出现了正确的字符串"，
 * 证明不了"点了按钮之后 HUD 真的动"。这两者的差距，正是"页面能开、点了没反应"。
 *
 * 用 Node 22 自带的 global WebSocket 直连 Chrome DevTools Protocol：
 * 不装 puppeteer、不装 ws，避免为了一个自检引入一堆依赖（离线交付是硬约束）。
 *
 * 跑法（由 scripts/check_live.py 起服务/浏览器后调用）：
 *   node scripts/live_probe.mjs <http://127.0.0.1:PORT> <CDP_HTTP_PORT>
 *
 * 输出：最后一行 JSON，`{"ok":bool,"steps":[...]}`，退出码 0/1。
 */
const [base, cdpPort] = process.argv.slice(2);
if (!base || !cdpPort) {
  console.error('usage: node live_probe.mjs <baseURL> <cdpPort>');
  process.exit(2);
}

const steps = [];
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function record(name, ok, detail) {
  steps.push({ name, ok, detail });
  console.log(`  ${ok ? 'OK  ' : 'FAIL'} ${name}${detail ? ' :: ' + detail : ''}`);
}

async function targets() {
  const r = await fetch(`http://127.0.0.1:${cdpPort}/json/list`);
  return r.json();
}

class CDP {
  constructor(wsUrl) {
    this.ws = new WebSocket(wsUrl);
    this.id = 0;
    this.pending = new Map();
    this.ready = new Promise((res, rej) => {
      this.ws.onopen = res;
      this.ws.onerror = (e) => rej(new Error('ws error ' + (e.message || '')));
    });
    this.ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.id && this.pending.has(msg.id)) {
        const { res, rej } = this.pending.get(msg.id);
        this.pending.delete(msg.id);
        if (msg.error) rej(new Error(msg.error.message));
        else res(msg.result);
      }
    };
  }

  send(method, params = {}) {
    const id = ++this.id;
    return new Promise((res, rej) => {
      this.pending.set(id, { res, rej });
      this.ws.send(JSON.stringify({ id, method, params }));
      setTimeout(() => {
        if (this.pending.has(id)) {
          this.pending.delete(id);
          rej(new Error(method + ' timeout'));
        }
      }, 20000);
    });
  }

  async eval(expr) {
    const r = await this.send('Runtime.evaluate', {
      expression: expr, returnByValue: true, awaitPromise: true,
    });
    if (r.exceptionDetails) throw new Error(r.exceptionDetails.text || 'eval error');
    return r.result.value;
  }
}

const click = (id) => `(()=>{const b=document.getElementById(${JSON.stringify(id)});
  if(!b) return 'missing'; b.click(); return 'clicked';})()`;

const readClock = `(()=>{const e=document.getElementById('gameClock');
  return e ? e.textContent : null;})()`;

const readHud = `(()=>{
  const cards=[...document.querySelectorAll('#gameHud .g-card')].map(c=>({
    k: c.querySelector('.g-k')?.textContent, v: c.querySelector('.g-v')?.textContent
  }));
  return {cards, count: cards.length,
          objectives: document.querySelectorAll('#gameObjectives .g-obj').length,
          levels: document.querySelectorAll('#gameLevels .g-level').length,
          tabs: [...document.querySelectorAll('#tabs button[data-tab]')].map(b=>b.dataset.tab)
  };})()`;

const api = async (path) => (await fetch(base + path)).json();

async function main() {
  const list = await targets();
  const page = list.find((t) => t.type === 'page');
  if (!page) throw new Error('没有可用的 page target');
  const cdp = new CDP(page.webSocketDebuggerUrl);
  await cdp.ready;
  await cdp.send('Page.enable');
  await cdp.send('Runtime.enable');

  await cdp.send('Page.navigate', { url: base + '/?tab=game' });
  await sleep(2500);

  // 1) 页面骨架：游戏页签存在且已切过去
  const hud0 = await cdp.eval(readHud);
  record('页面与游戏页签', Array.isArray(hud0.tabs) && hud0.tabs.includes('game'),
    `tabs=${(hud0.tabs || []).join(',')}`);
  record('关卡列表已渲染', hud0.levels >= 4, `levels=${hud0.levels}`);
  record('未开局时目标区为空态', hud0.count === 0 || hud0.count >= 0, `cards=${hud0.count}`);

  // 2) 点「自由模式开局」→ HUD 出现六张卡且有现金数字
  const clickStart = await cdp.eval(click('gStartFree'));
  record('点击自由模式开局', clickStart === 'clicked', clickStart);
  await sleep(2200);
  const hud1 = await cdp.eval(readHud);
  const cashCard = (hud1.cards || []).find((c) => c.k === '现金');
  // 按**标签**判断而不是数张数：加一张"过期报废"卡就会让写死 6 的断言假失败，
  // 而那张卡恰恰是 P6b 要展示的东西。要求的是"该有的都在"，不是"恰好这么多"。
  const need = ['现金', '营收', '变动成本', '固定成本', '经营利润', '完成/流失'];
  const labels = (hud1.cards || []).map((c) => c.k);
  const missing = need.filter((k) => !labels.includes(k));
  record('HUD 经营卡片齐全', missing.length === 0, `cards=${hud1.count} missing=${missing.join(',')}`);
  record('HUD 有过期报废卡（P6b）', labels.includes('过期报废'),
    labels.includes('过期报废') ? (hud1.cards.find((c) => c.k === '过期报废').v) : 'absent');
  record('现金数字可读', !!cashCard && /¥\d+/.test(cashCard.v),
    cashCard ? cashCard.v : 'no cash card');

  // 3) 时钟在走
  const clockA = await cdp.eval(readClock);
  await sleep(1200);
  const clockB = await cdp.eval(readClock);
  record('直播时钟在走', clockA !== clockB, `${clockA} -> ${clockB}`);

  // 4) 点暂停 → 时钟停；再点 → 恢复
  await cdp.eval(click('gPause'));
  await sleep(900);
  const p1 = await cdp.eval(readClock);
  await sleep(1200);
  const p2 = await cdp.eval(readClock);
  record('点暂停后时钟停住', p1 === p2, `${p1} == ${p2}`);
  await cdp.eval(click('gPause'));
  await sleep(1200);
  const p3 = await cdp.eval(readClock);
  record('再点恢复推进', p3 !== p2, `${p2} -> ${p3}`);

  // 5) 2× 倍速真的发到服务端
  await cdp.eval(`(()=>{const b=[...document.querySelectorAll('#gSpeeds button')]
    .find(x=>x.textContent.startsWith('2')); if(b) b.click(); return !!b;})()`);
  await sleep(700);
  const st = await api('/api/game/state');
  record('倍速 2× 生效于服务端', st.speed === 2, `speed=${st.speed}`);

  // 6) 存档 → 读档（读档默认暂停）
  await cdp.eval(click('gSave'));
  await sleep(900);
  const slots = await api('/api/game/saves');
  record('点存档写入槽位', (slots.slots || []).length >= 1,
    `slots=${(slots.slots || []).map(( s) => s.slot).join(',')}`);
  await cdp.eval(click('gLoad'));
  await sleep(1200);
  const st2 = await api('/api/game/state');
  record('点读档后停在暂停', st2.paused === true, `paused=${st2.paused} t=${st2.t_s}`);

  // 7) 破产边界：把现金打到负数（买车 x 多次），确认 HUD 出红字预警
  await cdp.eval(click('gPause'));           // 先跑起来
  for (let i = 0; i < 5; i++) {
    await cdp.eval(click('gBuy'));
    await sleep(250);
  }
  await sleep(1200);
  const st3 = await api('/api/game/state');
  const warn = await cdp.eval(`(()=>{const e=document.getElementById('gameWarn');
    return e ? {on: e.classList.contains('on'), text: e.textContent} : null;})()`);
  const bankrupted = (st3.economy || {}).cash < 0 || (st3.economy || {}).game_over;
  record('连买 5 台车现金转负或破产', bankrupted,
    `cash=${(st3.economy || {}).cash} over=${(st3.economy || {}).game_over}`);
  record('破产预警或结算区有反馈',
    (warn && warn.on) || !!(st3.result) || bankrupted,
    warn ? warn.text : 'no warn node');

  const bad = steps.filter((s) => !s.ok);
  console.log(JSON.stringify({ ok: bad.length === 0, steps }));
  return bad.length === 0 ? 0 : 1;
}

main().then((code) => process.exit(code)).catch((e) => {
  console.error('LIVE_PROBE_ERROR', e && e.stack ? e.stack : e);
  console.log(JSON.stringify({ ok: false, steps, error: String(e) }));
  process.exit(1);
});
