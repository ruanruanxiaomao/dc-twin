/* DC TWIN STUDIO · lightweight 3D projection renderer (zero dependency)
 *
 * Why this exists: the submission app must open from file:// with no backend,
 * no CDN and no build step, so a full WebGL engine (three.js) is out of scope.
 * This is a small self-contained software 3D renderer on Canvas 2D:
 *   - perspective-ish oblique projection with a rotatable orbit camera
 *   - per-face Lambert shading against a fixed light
 *   - painter's algorithm depth sorting (racks / doors / AGVs occlude correctly)
 *   - static geometry cached until the camera, theme or size changes
 * Geometry comes from data/layout.json; motion comes from data/paths.jsonl.
 * It is a browser visualisation, NOT the Blender real-time render.
 */
(function () {
  'use strict';

  var TAU = Math.PI * 2;

  function rgb(hex) {
    var h = String(hex || '#888888').replace('#', '');
    if (h.length === 3) h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2];
    var n = parseInt(h.slice(0, 6), 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
  }
  function shade(hex, f, alpha) {
    var c = rgb(hex), a = alpha == null ? 1 : alpha;
    return 'rgba(' + Math.min(255, Math.round(c[0] * f)) + ',' +
      Math.min(255, Math.round(c[1] * f)) + ',' +
      Math.min(255, Math.round(c[2] * f)) + ',' + a + ')';
  }
  function clamp(v, a, b) { return v < a ? a : (v > b ? b : v); }

  function agentAt(pts, t) {
    if (!pts || !pts.length) return null;
    if (t <= pts[0][0]) return pts[0];
    for (var i = 1; i < pts.length; i++) {
      if (pts[i][0] >= t) {
        var a = pts[i - 1], b = pts[i];
        var f = b[0] === a[0] ? 0 : (t - a[0]) / (b[0] - a[0]);
        return [t, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f, b[3] || a[3],
          Math.atan2(b[2] - a[2], b[1] - a[1])];
      }
    }
    var z = pts[pts.length - 1];
    return [z[0], z[1], z[2], z[3], 0];
  }

  function create(canvas, data, palette) {
    var ctx = canvas.getContext('2d');
    var W = canvas.width, H = canvas.height;
    var layout = data.layout, SW = layout.size[0], SH = layout.size[1];
    var agents = data.paths.agents, ids = Object.keys(agents);
    var tEnd = data.paths.t_end || 1;
    var pal = palette;
    var cam = { yaw: -0.68, pitch: 0.62, zoom: 1.12, tx: SW / 2, ty: SH / 2, follow: null };
    var staticDirty = true, ground = [], solids = [], labels = [], showTrails = true;
    var meta = {}; // per-agent load window + phase colors

    var PHASE = {
      M: { name: '行驶', c: '#39d6a6' }, R: { name: '旋转', c: '#a18cf5' },
      F: { name: '朝向作业面', c: '#57c8e8' }, P: { name: '取货', c: '#f5b461' },
      D: { name: '交付', c: '#5b9cf8' }, K: { name: '待命', c: '#8fa5a0' }
    };

    ids.forEach(function (id) {
      var pts = agents[id], p = null, d = null;
      for (var i = 0; i < pts.length; i++) {
        if (pts[i][3] === 'P' && p === null) p = pts[i][0];
        if (pts[i][3] === 'D') d = pts[i][0];
      }
      meta[id] = { load: [p == null ? Infinity : p, d == null ? Infinity : d],
        pts: pts, lastIdx: 0 };
    });

    // ---- camera / projection -------------------------------------------------
    function basis() {
      var cy = Math.cos(cam.yaw), sy = Math.sin(cam.yaw);
      var sp = Math.sin(cam.pitch), cp = Math.cos(cam.pitch);
      var base = Math.min((W - 90) / SW, (H - 130) / SH) * cam.zoom;
      return { cy: cy, sy: sy, sp: sp, cp: cp, s: base, focal: 320 };
    }
    function project(x, y, z, b) {
      b = b || basis();
      var dx = x - cam.tx, dy = y - cam.ty;
      var X = dx * b.cy - dy * b.sy;
      var Y = dx * b.sy + dy * b.cy;
      var depth = Y * b.cp - z * b.sp;          // larger = farther
      var k = b.focal / Math.max(60, b.focal + depth * 0.85);
      var s = b.s * k;
      return {
        x: W / 2 + X * s,
        y: H * 0.56 - Y * b.sp * s - z * b.cp * s,
        d: depth, k: k, s: s
      };
    }
    // face normal in camera space -> visibility + Lambert brightness
    function face(nx, ny, nz, b) {
      var X = nx * b.cy - ny * b.sy;
      var Y = nx * b.sy + ny * b.cy;
      var toward = -Y * b.cp + nz * b.sp;       // dot with camera direction
      if (toward <= 0.001) return null;
      var lum = 0.62 + 0.5 * Math.max(0, -(X * 0.42) + (Y * 0.36) + nz * 0.83);
      return lum;
    }
    function quad(list, corners, color, alpha, stroke) {
      var d = 0, i;
      for (i = 0; i < corners.length; i++) d += corners[i].d;
      list.push({ p: corners, c: color, a: alpha == null ? 1 : alpha,
        d: d / corners.length, stroke: stroke || null });
    }
    function poly2d(list, worldPts, z, color, alpha, stroke) {
      var b = basis(), out = [];
      for (var i = 0; i < worldPts.length; i++)
        out.push(project(worldPts[i][0], worldPts[i][1], z, b));
      quad(list, out, color, alpha, stroke);
    }
    // axis-aligned box: only camera-facing faces are emitted
    function box(list, x, y, w, d, z0, z1, color, opt) {
      opt = opt || {};
      var b = basis(), x0 = x - w / 2, x1 = x + w / 2, y0 = y - d / 2, y1 = y + d / 2;
      var P = [
        project(x0, y0, z0, b), project(x1, y0, z0, b),
        project(x1, y1, z0, b), project(x0, y1, z0, b),
        project(x0, y0, z1, b), project(x1, y0, z1, b),
        project(x1, y1, z1, b), project(x0, y1, z1, b)
      ];
      var cx = x, cy = y, cz = (z0 + z1) / 2;
      var cd = project(cx, cy, cz, b).d;
      var faces = [
        [0, 1, 5, 4, 0, -1, 0], [2, 3, 7, 6, 0, 1, 0],
        [1, 2, 6, 5, 1, 0, 0], [3, 0, 4, 7, -1, 0, 0],
        [4, 5, 6, 7, 0, 0, 1], [3, 2, 1, 0, 0, 0, -1]
      ];
      for (var i = 0; i < faces.length; i++) {
        var f = faces[i], lum = face(f[4], f[5], f[6], b);
        if (lum == null) continue;
        var col = f[6] === 1 && opt.top ? shade(opt.top, lum, opt.alpha)
          : shade(color, lum * (opt.lum || 1), opt.alpha);
        var pts = [P[f[0]], P[f[1]], P[f[2]], P[f[3]]];
        var q = { p: pts, c: col, a: 1, d: cd + (f[6] === 1 ? -0.01 : 0), stroke: opt.stroke || null };
        list.push(q);
      }
    }

    // ---- static scene --------------------------------------------------------
    function buildStatic() {
      ground = []; solids = []; labels = [];
      var PAD = 16;
      // ground plate + subtle outer frame
      poly2d(ground, [[-PAD, -PAD], [SW + PAD, -PAD], [SW + PAD, SH + PAD], [-PAD, SH + PAD]],
        0, pal.ground, 1, pal.groundEdge);
      layout.warehouses.forEach(function (w) {
        var ox = w.origin[0], oy = w.origin[1], sw = w.size[0], sh = w.size[1];
        // NOTE: layout.json stores GLOBAL absolute coordinates for racks,
        // docks, zones and homes. `origin` is only the warehouse frame corner;
        // never add it again to entity coordinates.
        (w.zones || []).forEach(function (z) {
          poly2d(ground, z.polygon, 0.02, shade(z.color || pal.accent, 1, 0.10), 1);
        });
        poly2d(ground, [[ox, oy], [ox + sw, oy], [ox + sw, oy + sh], [ox, oy + sh]],
          0.03, pal.warehouseFill, 1, pal.warehouseEdge);
        // docks as coloured floor slabs + two door posts
        (w.docks || []).forEach(function (dk) {
          var r = dk.rect;
          // dock rect is [x0, y0, x1, y1] (two corners), NOT [x, y, w, h].
          var col = dk.kind === 'INBOUND' ? pal.dockIn : pal.dockOut;
          var x0 = r[0], y0 = r[1], x1 = r[2], y1 = r[3];
          var dw = x1 - x0, dh = y1 - y0;
          poly2d(ground, [[x0, y0], [x1, y0], [x1, y1], [x0, y1]],
            0.05, shade(col, 1, 0.42), 1, shade(col, 1, 0.8));
          box(solids, (x0 + x1) / 2, y0 + 0.45, dw * 0.86, Math.min(0.6, dh), 0, 3.2,
            col, { alpha: 0.5, top: col, lum: 0.9 });
        });
        // racks: real 3D boxes, three shelf levels hinted by a darker band
        (w.racks || []).forEach(function (r, i) {
          var x = r[0] + r[2] / 2, y = r[1] + r[3] / 2;
          var col = i % 2 ? pal.rackAlt : pal.rack;
          box(solids, x, y, r[2], r[3], 0.05, 3.2, col,
            { top: pal.rackTop, lum: 1.0, stroke: shade(pal.rackTop, 1, 0.22) });
          box(solids, x, y, r[2] * 0.98, r[3] * 0.98, 1.12, 1.24, pal.rackBand, { lum: 0.9 });
          box(solids, x, y, r[2] * 0.98, r[3] * 0.98, 2.2, 2.32, pal.rackBand, { lum: 0.9 });
        });
        // home / standby markers
        (w.homes || []).forEach(function (h) {
          poly2d(ground, [[h[0] - 0.9, h[1] - 0.9], [h[0] + 0.9, h[1] - 0.9],
            [h[0] + 0.9, h[1] + 0.9], [h[0] - 0.9, h[1] + 0.9]], 0.06, pal.home, 0.55);
        });
        labels.push({ x: ox + sw / 2, y: oy + sh / 2, text: w.id, sub: sw + '×' + sh + ' m' });
      });
      // site grid lines (drawn separately for speed)
      staticDirty = false;
    }

    function drawGrid(b) {
      ctx.lineWidth = 1;
      ctx.strokeStyle = pal.grid;
      ctx.beginPath();
      for (var x = 0; x <= SW; x += 10) {
        var a = project(x, -16, 0, b), c = project(x, SH + 16, 0, b);
        ctx.moveTo(a.x, a.y); ctx.lineTo(c.x, c.y);
      }
      for (var y = 0; y <= SH; y += 10) {
        var e = project(-16, y, 0, b), f = project(SW + 16, y, 0, b);
        ctx.moveTo(e.x, e.y); ctx.lineTo(f.x, f.y);
      }
      ctx.stroke();
    }
    function drawQuad(q) {
      var p = q.p;
      ctx.beginPath(); ctx.moveTo(p[0].x, p[0].y);
      for (var i = 1; i < p.length; i++) ctx.lineTo(p[i].x, p[i].y);
      ctx.closePath();
      ctx.globalAlpha = q.a;
      ctx.fillStyle = q.c; ctx.fill();
      if (q.stroke) { ctx.strokeStyle = q.stroke; ctx.lineWidth = 1; ctx.stroke(); }
      ctx.globalAlpha = 1;
    }
    function drawAll(list) {
      list.sort(function (a, b) { return b.d - a.d; });
      for (var i = 0; i < list.length; i++) drawQuad(list[i]);
    }

    // ---- dynamic: trails + AGVs ----------------------------------------------
    function drawTrails(t, b) {
      if (!showTrails) return;
      ids.forEach(function (id) {
        var pts = meta[id].pts, idx = 0;
        // binary-ish lookup from cached index
        while (idx < pts.length && pts[idx][0] < t) idx += 1;
        meta[id].lastIdx = idx;
        if (idx < 2) return;
        var start = Math.max(0, idx - 26), seg = pts.slice(start, idx);
        var ph = agentAt(pts, t), col = (PHASE[ph && ph[3]] || PHASE.M).c;
        ctx.beginPath();
        for (var i = 0; i < seg.length; i++) {
          var p = project(seg[i][1], seg[i][2], 0.1, b);
          i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y);
        }
        var last = project(seg[seg.length - 1][1], seg[seg.length - 1][2], 0.1, b);
        var now = project(ph[1], ph[2], 0.1, b);
        ctx.lineTo(now.x, now.y);
        ctx.strokeStyle = shade(col, 1, 0.22); ctx.lineWidth = 9; ctx.stroke();
        ctx.strokeStyle = shade(col, 1, 0.95); ctx.lineWidth = 2.6; ctx.stroke();
        // head marker with halo
        ctx.beginPath(); ctx.arc(now.x, now.y, 8, 0, TAU);
        ctx.fillStyle = shade(col, 1, 0.16); ctx.fill();
        ctx.beginPath(); ctx.arc(now.x, now.y, 3, 0, TAU);
        ctx.fillStyle = col; ctx.fill();
      });
    }
    function drawAgents(t, b, list) {
      ids.forEach(function (id) {
        var pts = meta[id].pts, p = agentAt(pts, t);
        if (!p) return;
        var ph = PHASE[p[3]] || PHASE.M, col = ph.c;
        var x = p[1], y = p[2], ang = p[4] || 0;
        // ground shadow
        poly2d(list, [[x - 0.95, y - 0.7], [x + 0.95, y - 0.7],
          [x + 0.95, y + 0.7], [x - 0.95, y + 0.7]], 0.08, pal.shadow, 0.5);
        // chassis + deck + beacon
        box(list, x, y, 1.8, 1.25, 0.12, 0.68, col, { top: shade(col, 1.3, 1), lum: 1.0 });
        box(list, x, y, 1.4, 0.98, 0.68, 0.8, pal.agvDeck, { top: pal.agvDeckTop });
        box(list, x + Math.cos(ang) * 0.66, y + Math.sin(ang) * 0.48,
          0.3, 0.24, 0.8, 0.96, pal.lamp);
        // carried pallet between PICK and DELIVER
        var lw = meta[id].load;
        if (t >= lw[0] && t < lw[1]) {
          box(list, x, y, 1.08, 0.86, 0.8, 1.52, pal.cargo,
            { top: pal.cargoTop, lum: 1.05 });
        }
      });
    }
    function drawBeacons(t, b) {
      ids.forEach(function (id) {
        var p = agentAt(meta[id].pts, t); if (!p) return;
        var col = (PHASE[p[3]] || PHASE.M).c;
        var q = project(p[1], p[2], 1.0, b);
        ctx.beginPath(); ctx.arc(q.x, q.y, 9 * q.k, 0, TAU);
        ctx.fillStyle = shade(col, 1, 0.18); ctx.fill();
        ctx.beginPath(); ctx.arc(q.x, q.y, 3.2 * q.k, 0, TAU);
        ctx.fillStyle = col; ctx.fill();
      });
    }

    // ---- main render ----------------------------------------------------------
    function render(t) {
      // follow must update the camera BEFORE static geometry is projected,
      // otherwise cached polys and the live camera basis disagree.
      if (cam.follow) {
        var fp = agentAt(meta[cam.follow].pts, t);
        if (fp) {
          var ntx = cam.tx + (fp[1] - cam.tx) * 0.16;
          var nty = cam.ty + (fp[2] - cam.ty) * 0.16;
          if (Math.abs(ntx - cam.tx) > 0.01 || Math.abs(nty - cam.ty) > 0.01) staticDirty = true;
          cam.tx = ntx; cam.ty = nty;
        }
      }
      if (staticDirty) buildStatic();
      var b = basis();
      // sky / vignette
      var g = ctx.createLinearGradient(0, 0, 0, H);
      g.addColorStop(0, pal.sky0); g.addColorStop(1, pal.sky1);
      ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);
      var glow = ctx.createRadialGradient(W / 2, H * 0.54, 20, W / 2, H * 0.54, W * 0.62);
      glow.addColorStop(0, shade(pal.accent, 1, 0.09));
      glow.addColorStop(1, 'rgba(0,0,0,0)');
      ctx.fillStyle = glow; ctx.fillRect(0, 0, W, H);
      drawGrid(b);
      drawAll(ground.slice());
      drawTrails(t, b);
      var dyn = [];
      drawAgents(t, b, dyn);
      drawAll(solids.concat(dyn));
      drawBeacons(t, b);
      // floating warehouse labels
      labels.forEach(function (L) {
        var p = project(L.x, L.y, 4.4, b);
        ctx.font = '600 12px system-ui, sans-serif';
        var tw = ctx.measureText(L.text).width, w = tw + 20;
        ctx.fillStyle = pal.labelBg;
        ctx.beginPath();
        if (ctx.roundRect) ctx.roundRect(p.x - w / 2, p.y - 12, w, 22, 7);
        else ctx.rect(p.x - w / 2, p.y - 12, w, 22);
        ctx.fill();
        ctx.strokeStyle = pal.labelEdge; ctx.lineWidth = 1; ctx.stroke();
        ctx.fillStyle = pal.labelInk; ctx.textAlign = 'center';
        ctx.fillText(L.text, p.x, p.y + 3); ctx.textAlign = 'left';
      });
    }

    // ---- interaction ----------------------------------------------------------
    var drag = null;
    canvas.addEventListener('pointerdown', function (e) {
      drag = { x: e.clientX, y: e.clientY, yaw: cam.yaw, pitch: cam.pitch };
      canvas.setPointerCapture && canvas.setPointerCapture(e.pointerId);
    });
    canvas.addEventListener('pointermove', function (e) {
      if (!drag) return;
      cam.yaw = drag.yaw + (e.clientX - drag.x) * 0.006;
      cam.pitch = clamp(drag.pitch + (e.clientY - drag.y) * 0.004, 0.18, 1.5);
      staticDirty = true;
    });
    canvas.addEventListener('pointerup', function () { drag = null; });
    canvas.addEventListener('pointercancel', function () { drag = null; });
    canvas.addEventListener('wheel', function (e) {
      e.preventDefault();
      cam.zoom = clamp(cam.zoom * (e.deltaY > 0 ? 0.91 : 1.1), 0.5, 3.4);
      staticDirty = true;
    }, { passive: false });
    canvas.addEventListener('dblclick', function () { preset('iso'); });

    function preset(name) {
      cam.follow = null;
      if (name === 'top') { cam.yaw = 0; cam.pitch = 1.5; cam.zoom = 1.05; }
      else if (name === 'close') { cam.yaw = -0.9; cam.pitch = 0.38; cam.zoom = 1.9; }
      else if (name === 'wide') { cam.yaw = -0.4; cam.pitch = 0.9; cam.zoom = 0.88; }
      else { cam.yaw = -0.68; cam.pitch = 0.62; cam.zoom = 1.12; }
      cam.tx = SW / 2; cam.ty = SH / 2;
      staticDirty = true;
      return name;
    }
    function follow(id) {
      cam.follow = id || null;
      if (id) { cam.pitch = 0.42; cam.zoom = 2.2; }
      else preset('iso');
      staticDirty = true;
    }

    return {
      render: render,
      preset: preset,
      follow: follow,
      setPalette: function (p) { pal = p; staticDirty = true; },
      setTrails: function (v) { showTrails = !!v; },
      markDirty: function () { staticDirty = true; },
      camera: cam,
      phase: PHASE,
      agentAt: function (id, t) { return agentAt(meta[id].pts, t); },
      ids: ids,
      following: function () { return cam.follow; }
    };
  }

  window.DCTwin3D = { create: create, shade: shade, rgb: rgb };
})();
