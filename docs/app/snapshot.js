/* 静态快照垫片：把 /api/* 与 /out/* 重定向到同目录下的冻结文件。
 * 只读——所有 POST（重跑、保存配置、写场景）都会被拒绝并给出提示，
 * 因为这些操作需要真实的 Python 服务端，而不是一份快照。
 */
(() => {
  const API = {
  "/api/bootstrap": "bootstrap.json",
  "/api/paths": "paths.json",
  "/api/status": "status.json",
  "/api/scenarios": "scenarios.json",
  "/api/pnl": "pnl.json",
  "/api/ledger": "ledger.json",
  "/api/schema": "schema.json",
  "/api/cost-spec": "cost-spec.json",
  "/api/scenario/template": "scenario/template.json"
};
  const realFetch = window.fetch.bind(window);
  const json = (obj, status = 200) =>
    Promise.resolve(new Response(JSON.stringify(obj), {
      status, headers: { 'Content-Type': 'application/json' },
    }));

  window.DC_SNAPSHOT = true;

  window.fetch = (input, init = {}) => {
    // three 的 FileLoader 会传 Request 对象，String(request) 是 '[object Request]'，
    // 直接截字符串拿不到 pathname —— 必须用 URL 解析（踩过一次）。
    const raw = input && input.url ? input.url : String(input);
    let u;
    try { u = new URL(raw, location.href); } catch (_) { return realFetch(input, init); }
    const path = u.pathname;
    const url = path + u.search;
    const method = (init.method || 'GET').toUpperCase();

    if (path.startsWith('/api/')) {
      if (method !== 'GET') {
        return json({ ok: false, static: true,
                      error: '这是静态快照，重跑/保存类操作不可用。请本地运行 python console/server.py' });
      }
      const file = API[path];
      if (file) return realFetch('./api/' + file, { cache: 'no-store' });
      // 轮询类接口：静态快照里永远"空闲"，让页面别转圈
      if (path === '/api/job') return json({ status: 'idle', lines: [], next: 0 });
      if (path === '/api/scenario/get')
        return json({ ok: false, static: true, error: '静态快照不含可编辑场景' });
      return json({ ok: false, static: true, error: 'no snapshot for ' + path });
    }
    if (path.startsWith('/out/')) return realFetch('.' + url, { cache: 'no-store' });
    if (path.startsWith('/data/')) return realFetch('.' + url, { cache: 'no-store' });
    if (path.startsWith('/static/')) return realFetch(url.replace('/static/', './'), init);
    return realFetch(input, init);
  };

  // 顶部横幅：让人一眼知道"这是快照，不是活的"
  const boot = () => {
    const bar = document.createElement('div');
    bar.textContent = '静态快照 · 只读 —— 3D/图表/KPI 来自一次真实运行；重跑与编辑请本地启动控制台';
    bar.style.cssText = [
      'position:fixed', 'left:0', 'right:0', 'top:0', 'z-index:9999',
      'padding:7px 14px', 'font:13px/1.4 system-ui,"Microsoft YaHei",sans-serif',
      'color:#0b1220', 'background:#facc15', 'text-align:center',
      'box-shadow:0 1px 6px rgba(15,23,42,.18)',
    ].join(';');
    document.body.appendChild(bar);
    document.body.style.paddingTop = '34px';
  };
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
