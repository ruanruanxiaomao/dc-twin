/* Static demo viewer. Plain Canvas 2D: works over file:// with no fetch/modules. */
(function () {
  'use strict';
  const D = window.DC_DATA;
  if (!D) { document.body.innerHTML = '<p>data.js missing — run <code>dc export --target demo</code>.</p>'; return; }

  const B = D.bootstrap, P = D.paths, S = D.scenarios;
  const cv = document.getElementById('cv');
  const ctx = cv.getContext('2d');
  const timeEl = document.getElementById('time');
  const tLabel = document.getElementById('t-label');
  const btnPlay = document.getElementById('btn-play');
  const btnView = document.getElementById('btn-view');
  const speedEl = document.getElementById('speed');
  const chkTracks = document.getElementById('show-tracks');
  const chkRacks = document.getElementById('show-racks');
  const W = B.size[0], H = B.size[1];
  const PAD = 8;
  let view = 'top';
  let playing = false;
  let t = 0;
  const T_END = P.t_end_s || 1;
  const AGENT_IDS = Object.keys(P.agents).sort();
  const COLORS = ['#e2562d', '#0e8fbd', '#8a5bd6', '#2a9d62', '#d2890b', '#c1457a'];

  document.getElementById('b-seed').textContent = 'seed ' + B.seed;
  document.getElementById('b-agents').textContent = B.agents + ' AGVs';
  document.getElementById('b-dur').textContent = T_END.toFixed(2) + ' s';
  document.getElementById('gen-time').textContent = D.generated_at;
  document.getElementById('engine-note').textContent =
    'Engine: ' + S.engine + ' — do not read these as full per-vehicle A* results.';
  document.getElementById('facts').innerHTML = [
    ['Warehouses', B.warehouses.map(w => w.id).join(' / ')],
    ['Racks', B.warehouses.reduce((a, w) => a + w.racks.length, 0)],
    ['Locations', B.summary.locations],
    ['Orders', B.tasks + ' (100% executed)'],
    ['Event types', B.event_types.join(', ')],
    ['Total route', B.total_path_m + ' m']
  ].map(r => '<div><span>' + r[0] + '</span><b>' + r[1] + '</b></div>').join('');

  const tb = document.querySelector('#scen tbody');
  tb.innerHTML = S.scenarios.map(s => '<tr><td>' + s.id + '</td><td>' +
    (s.throughput_orders_per_h ?? '—') + '</td><td>' + (s.on_time_rate ?? '—') +
    '</td><td>' + (s.max_dock_queue ?? '—') + '</td><td class="' +
    (s.verdict === 'PASS' ? 'ok' : 'bad') + '">' + s.verdict + '</td></tr>').join('');

  function fit() {
    const dpr = window.devicePixelRatio || 1;
    const rect = cv.getBoundingClientRect();
    cv.width = Math.max(320, Math.round(rect.width * dpr));
    cv.height = Math.max(240, Math.round((rect.width * 0.6) * dpr));
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }
  window.addEventListener('resize', () => { fit(); draw(); });
  fit();

  function proj(x, y, z) {
    const cw = cv.clientWidth, ch = cv.clientHeight;
    if (view === 'top') {
      const s = Math.min((cw - 2 * PAD) / W, (ch - 2 * PAD) / H);
      return [PAD + x * s, PAD + (H - y) * s, s];
    }
    const s = Math.min((cw - 2 * PAD) / (W + H) * 1.35, (ch - 2 * PAD) / ((W + H) * 0.62));
    const ox = cw / 2 - (W - H) * s * 0.5;
    const oy = PAD + H * s * 0.30;
    return [ox + (x - y) * s * 0.86, oy + (x + y) * s * 0.42 - (z || 0) * s * 0.9, s];
  }

  function rect2d(x0, y0, x1, y1, fill, stroke, height) {
    const a = proj(x0, y0, 0), b = proj(x1, y0, 0),
          c = proj(x1, y1, 0), d = proj(x0, y1, 0);
    if (view === 'iso' && height) {
      const hh = height;
      const at = proj(x0, y0, hh), bt = proj(x1, y0, hh),
            ct = proj(x1, y1, hh), dt = proj(x0, y1, hh);
      ctx.fillStyle = shade(fill, -0.20);
      ctx.beginPath(); ctx.moveTo(d[0], d[1]); ctx.lineTo(c[0], c[1]);
      ctx.lineTo(ct[0], ct[1]); ctx.lineTo(dt[0], dt[1]); ctx.fill();
      ctx.fillStyle = shade(fill, -0.08);
      ctx.beginPath(); ctx.moveTo(b[0], b[1]); ctx.lineTo(c[0], c[1]);
      ctx.lineTo(ct[0], ct[1]); ctx.lineTo(bt[0], bt[1]); ctx.fill();
    }
    ctx.fillStyle = fill;
    ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]);
    ctx.lineTo(c[0], c[1]); ctx.lineTo(d[0], d[1]); ctx.closePath();
    ctx.fill();
    if (stroke) { ctx.strokeStyle = stroke; ctx.lineWidth = 0.5; ctx.stroke(); }
  }

  function shade(hex, amt) {
    const n = parseInt(hex.slice(1), 16);
    const ch = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map(v =>
      Math.max(0, Math.min(255, Math.round(v + 255 * amt))));
    return 'rgb(' + ch.join(',') + ')';
  }

  function posAt(track, tt) {
    if (tt <= track[0].t) return track[0];
    if (tt >= track[track.length - 1].t) return track[track.length - 1];  // PARK: stays in scene
    let lo = 0, hi = track.length - 1;
    while (lo + 1 < hi) { const m = (lo + hi) >> 1; if (track[m].t <= tt) lo = m; else hi = m; }
    const a = track[lo], b = track[hi], f = (tt - a.t) / Math.max(b.t - a.t, 1e-9);
    return { x: a.x + (b.x - a.x) * f, y: a.y + (b.y - a.y) * f, ph: b.ph };
  }

  function draw() {
    const cw = cv.clientWidth, ch = cv.clientHeight;
    ctx.clearRect(0, 0, cw, ch);
    ctx.fillStyle = '#f7f8f7'; ctx.fillRect(0, 0, cw, ch);

    B.warehouses.forEach(w => {
      const [ox, oz] = w.origin, [ww, dd] = w.size;
      rect2d(ox, oz, ox + ww, oz + dd, '#eef1f4', '#b9c2cb');
      w.docks.forEach(r => rect2d(r[0], r[1], r[2], r[3], '#f2c14e', '#c99a20'));
      if (chkRacks.checked) {
        w.racks.forEach(r => rect2d(r[0], r[1], r[0] + r[2], r[1] + r[3],
          '#9aa7b4', '#78899a', view === 'iso' ? 3.2 : 0));
      }
      w.homes.forEach(p => {
        const q = proj(p[0], p[1], 0), s = Math.max(2.5, q[2] * 0.9);
        ctx.fillStyle = '#7c55c7'; ctx.fillRect(q[0] - s / 2, q[1] - s / 2, s, s);
      });
      const lab = proj(ox + ww / 2, oz + dd / 2, 0);
      ctx.fillStyle = '#5a6672'; ctx.font = '600 12px system-ui, sans-serif';
      ctx.textAlign = 'center'; ctx.fillText(w.id, lab[0], lab[1]);
    });

    if (chkTracks.checked) {
      ctx.lineWidth = 1;
      AGENT_IDS.forEach((aid, i) => {
        const tr = P.agents[aid];
        ctx.strokeStyle = COLORS[i % COLORS.length] + '55';
        ctx.beginPath();
        tr.forEach((p, k) => {
          const q = proj(p.x, p.y, 0);
          if (k === 0) ctx.moveTo(q[0], q[1]); else ctx.lineTo(q[0], q[1]);
        });
        ctx.stroke();
      });
    }

    let active = 0;
    AGENT_IDS.forEach((aid, i) => {
      const p = posAt(P.agents[aid], t);
      if (!p) return;
      active++;
      const q = proj(p.x, p.y, 0);
      const r = Math.max(3, q[2] * 0.62);
      ctx.beginPath(); ctx.arc(q[0], q[1], r, 0, Math.PI * 2);
      ctx.fillStyle = p.ph === 'PICK' || p.ph === 'DROP' ? '#f0a11c' : COLORS[i % COLORS.length];
      ctx.fill();
      ctx.strokeStyle = '#ffffff'; ctx.lineWidth = 1; ctx.stroke();
    });
    ctx.fillStyle = '#3d4854'; ctx.font = '12px system-ui, sans-serif';
    ctx.textAlign = 'left';
    ctx.fillText('AGVs in scene: ' + active + ' / ' + AGENT_IDS.length, 12, ch - 12);
  }

  function step(ts) {
    if (!playing) return;
    if (!step.last) step.last = ts;
    const dt = Math.min((ts - step.last) / 1000, 0.1);
    step.last = ts;
    t += dt * parseFloat(speedEl.value);
    if (t > T_END) t = 0;
    timeEl.value = Math.round(1000 * t / T_END);
    tLabel.textContent = t.toFixed(2) + ' s';
    draw();
    requestAnimationFrame(step);
  }

  btnPlay.addEventListener('click', () => {
    playing = !playing;
    btnPlay.textContent = playing ? '❚❚ Pause' : '▶ Play';
    step.last = 0;
    if (playing) requestAnimationFrame(step);
  });
  timeEl.addEventListener('input', () => {
    t = T_END * (timeEl.value / 1000);
    tLabel.textContent = t.toFixed(2) + ' s';
    draw();
  });
  btnView.addEventListener('click', () => {
    view = view === 'top' ? 'iso' : 'top';
    btnView.textContent = 'View: ' + (view === 'top' ? 'top-down' : 'isometric');
    draw();
  });
  chkTracks.addEventListener('change', draw);
  chkRacks.addEventListener('change', draw);
  draw();
})();
