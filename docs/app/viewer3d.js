/* 3D 透视视图 —— three.js + dc_preview.glb
 *
 * 坐标系：GLB 是 Y-up，layout 的 [x, z] 映射为 glTF 的 (x, h, -z)。
 * AGV 节点名就是 agent id（如 W-R01），是 DC_ROOT 的直接子节点，无中间变换。
 * 朝向：glTF 绕 Y 轴角度 = π/2 − theta（theta 是 paths 里的运动方向角）。
 */

import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

const AGV_RE = /^(?:W|F\d)-R\d{2}$/;

export async function create(canvas, opts = {}) {
  const onProgress = opts.onProgress || (() => {});

  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.05;

  const scene = new THREE.Scene();
  scene.background = new THREE.Color('#eaf0f8');

  const camera = new THREE.PerspectiveCamera(42, 1, 0.5, 1400);
  camera.position.set(140, 120, 90);

  // ---- 光照（GLB 不含灯光，需自建）----
  scene.add(new THREE.HemisphereLight(0xffffff, 0xa8b8cc, 1.55));

  const sun = new THREE.DirectionalLight(0xfff4e2, 2.5);
  sun.position.set(-90, 140, 70);
  scene.add(sun);

  const fill = new THREE.DirectionalLight(0xdce9ff, 0.95);
  fill.position.set(110, 80, -110);
  scene.add(fill);

  const rim = new THREE.DirectionalLight(0xffffff, 0.45);
  rim.position.set(0, 40, 160);
  scene.add(rim);

  // ---- 控件 ----
  const controls = new OrbitControls(camera, canvas);
  controls.enableDamping = true;
  controls.dampingFactor = 0.075;
  controls.maxPolarAngle = Math.PI * 0.495;   // 不许钻到地面以下
  controls.minDistance = 8;
  controls.maxDistance = 460;
  controls.screenSpacePanning = false;

  // ---- 选中标记 ----
  const ring = new THREE.Mesh(
    new THREE.RingGeometry(0.78, 1.12, 44),
    new THREE.MeshBasicMaterial({
      color: 0x2563eb, transparent: true, opacity: 0.9,
      side: THREE.DoubleSide, depthWrite: false, depthTest: false,
    }),
  );
  ring.rotation.x = -Math.PI / 2;
  ring.visible = false;
  ring.renderOrder = 15;
  scene.add(ring);

  const beam = new THREE.Mesh(
    new THREE.CylinderGeometry(0.44, 0.44, 7, 18, 1, true),
    new THREE.MeshBasicMaterial({
      color: 0x3b82f6, transparent: true, opacity: 0.17,
      side: THREE.DoubleSide, depthWrite: false, depthTest: false,
    }),
  );
  beam.visible = false;
  beam.renderOrder = 15;
  scene.add(beam);

  // ---- 加载 GLB ----
  const loader = new GLTFLoader();
  const gltf = await loader.loadAsync(opts.url, (ev) => {
    if (ev && ev.total) onProgress(ev.loaded / ev.total);
  });

  const root = gltf.scene;
  scene.add(root);

  const agents = {};
  let agvCount = 0;
  root.traverse((o) => {
    if (AGV_RE.test(o.name)) {
      agents[o.name] = { obj: o, y: o.position.y };
      agvCount++;
    }
  });

  // ---- 取景范围 ----
  // 不用 GLB 的 bbox：GROUND_EXTERIOR 是一整块大地面，会把包围盒撑大，
  // 导致仓库不在画面中心。改用 layout 的真实平面范围。
  const bd = opts.bounds || { x0: 0, z0: 0, x1: 112, z1: 114 };
  const center = new THREE.Vector3((bd.x0 + bd.x1) / 2, 0, -(bd.z0 + bd.z1) / 2);
  const size = new THREE.Vector3(bd.x1 - bd.x0, 12, bd.z1 - bd.z0);

  let nodeTotal = 0;
  root.traverse(() => nodeTotal++);

  function frameCamera(dir, pad) {
    const d = dir.clone().normalize();
    const fov = camera.fov * Math.PI / 180;
    // 用三维外接球求距离：等轴测下角点会突出，按平面矩形算会裁边
    const radius = 0.5 * Math.hypot(size.x, size.z, size.y);
    const dist = (radius / Math.sin(fov / 2)) * pad;

    // 看向场景高度中心，而不是地面，否则整体会沉到画面下半部
    const tgt = new THREE.Vector3(center.x, size.y * 0.5, center.z);
    camera.position.copy(tgt).add(d.multiplyScalar(dist));
    controls.target.copy(tgt);
    controls.update();
  }

  const PRESETS = {
    iso: () => frameCamera(new THREE.Vector3(0.62, 0.78, 0.72), 0.66),
    top: () => frameCamera(new THREE.Vector3(0.001, 1, 0.02), 0.74),
    // 近景对准选中车；没选中就看 W 仓中部
    close: () => {
      const tgt = selId && agents[selId]
        ? new THREE.Vector3(
            agents[selId].obj.position.x, 1.2, agents[selId].obj.position.z)
        : new THREE.Vector3(center.x - size.x * 0.18, 2, center.z);
      camera.position.copy(tgt).add(new THREE.Vector3(7.5, 5.5, 8.5));
      controls.target.copy(tgt);
      controls.update();
    },
  };

  function preset(name) {
    (PRESETS[name] || PRESETS.iso)();
  }

  // ---- AGV 头顶标记 ----
  // 车体只有 1.12 m，在 112 m 的场景里不足 5 px；用屏幕尺寸恒定的圆点，
  // 远看也能认出「哪台车在哪、什么相位」，颜色与 2D 视图保持一致
  const PH3 = {
    MOVE: 0x2563eb, ROTATE: 0x7c3aed, FACE: 0x0891b2,
    PICK: 0xea580c, DROP: 0x16a34a, PARK: 0x94a3b8,
  };

  function discTexture() {
    const s = 64;
    const cv = document.createElement('canvas');
    cv.width = cv.height = s;
    const g = cv.getContext('2d');
    g.beginPath();
    g.arc(s / 2, s / 2, s / 2 - 3, 0, Math.PI * 2);
    g.fillStyle = '#ffffff';
    g.fill();
    g.lineWidth = 5;
    g.strokeStyle = 'rgba(15,23,42,.45)';
    g.stroke();
    const t = new THREE.CanvasTexture(cv);
    t.colorSpace = THREE.SRGBColorSpace;
    return t;
  }

  const disc = discTexture();
  const markers = {};
  for (const id in agents) {
    const m = new THREE.SpriteMaterial({
      map: disc, color: PH3.PARK, sizeAttenuation: false,
      transparent: true, depthTest: false, depthWrite: false,
    });
    const sp = new THREE.Sprite(m);
    sp.scale.set(0.017, 0.017, 1);
    sp.renderOrder = 20;
    sp.userData.agentId = id;
    scene.add(sp);
    markers[id] = sp;
  }


  // ---- 功能区覆盖层 ----
  // GLB 里只有 3 个物理区有地面板块，另外 18 个逻辑区是"挂靠"在货架/月台上的小标记，
  // 板子小、字更小，远看根本不存在。这里用 glTF 坐标在 three 里再画一层：
  // 物理区 = 半透明薄板 + 名称牌，逻辑区 = 圆点 + 名称牌，可整层开关、可点击。
  const areaGroup = new THREE.Group();
  areaGroup.name = 'AREA_OVERLAY';
  scene.add(areaGroup);
  const areaPicks = [];
  const areaLabels = [];

  function labelSprite(text, color) {
    const pad = 8, fs = 26;
    const cv = document.createElement('canvas');
    const g = cv.getContext('2d');
    g.font = '600 ' + fs + 'px "Microsoft YaHei", -apple-system, sans-serif';
    const w = Math.ceil(g.measureText(text).width) + pad * 2;
    cv.width = w;
    cv.height = fs + pad * 2;
    const gg = cv.getContext('2d');
    gg.font = '600 ' + fs + 'px "Microsoft YaHei", -apple-system, sans-serif';
    gg.fillStyle = 'rgba(255,255,255,.92)';
    gg.strokeStyle = color;
    gg.lineWidth = 2;
    const r = 8;
    gg.beginPath();
    gg.moveTo(r, 0); gg.lineTo(cv.width - r, 0); gg.quadraticCurveTo(cv.width, 0, cv.width, r);
    gg.lineTo(cv.width, cv.height - r); gg.quadraticCurveTo(cv.width, cv.height, cv.width - r, cv.height);
    gg.lineTo(r, cv.height); gg.quadraticCurveTo(0, cv.height, 0, cv.height - r);
    gg.lineTo(0, r); gg.quadraticCurveTo(0, 0, r, 0); gg.closePath();
    gg.fill(); gg.stroke();
    gg.fillStyle = color;
    gg.textBaseline = 'middle';
    gg.fillText(text, pad, cv.height / 2);

    const tex = new THREE.CanvasTexture(cv);
    tex.colorSpace = THREE.SRGBColorSpace;
    // sizeAttenuation:false 才是"屏幕上恒定大小"。开着的话 scale 是世界单位，
    // 0.02 就是 2 cm，缩到整场视角时牌子上只剩一个白点（第一次就是这样翻车的）。
    const mat = new THREE.SpriteMaterial({ map: tex, transparent: true,
                                           depthTest: false, depthWrite: false,
                                           sizeAttenuation: false });
    const sp = new THREE.Sprite(mat);
    const h = 0.026;                       // 约占视口高度的 2.6%（3% 时标签块太抢眼）
    sp.scale.set(h * cv.width / cv.height, h, 1);
    sp.renderOrder = 30;
    return sp;
  }

  function disposeAreaGroup() {
    for (const c of [...areaGroup.children]) {
      areaGroup.remove(c);
      if (c.geometry) c.geometry.dispose();
      if (c.material) {
        if (c.material.map) c.material.map.dispose();
        c.material.dispose();
      }
    }
    areaPicks.length = 0;
    areaLabels.length = 0;
  }

  function setAreas(list) {
    disposeAreaGroup();
    for (const a of list || []) {
      const col = new THREE.Color(a.color || '#0ea5e9');
      if (a.rect && a.rect.length === 4) {
        const [x0, z0, x1, z1] = a.rect;
        const w = Math.max(0.5, x1 - x0), d = Math.max(0.5, z1 - z0);
        const geo = new THREE.BoxGeometry(w, 0.16, d);
        const mesh = new THREE.Mesh(geo, new THREE.MeshBasicMaterial({
          color: col, transparent: true, opacity: 0.46, depthWrite: false,
        }));
        mesh.position.set((x0 + x1) / 2, 0.12, -(z0 + z1) / 2);
        mesh.userData.areaId = a.id;
        mesh.renderOrder = 2;
        areaGroup.add(mesh);
        areaPicks.push(mesh);

        const edge = new THREE.LineSegments(
          new THREE.EdgesGeometry(geo),
          new THREE.LineBasicMaterial({ color: col }));
        edge.position.copy(mesh.position);
        areaGroup.add(edge);

        const sp = labelSprite(a.name || a.id, a.color || '#0ea5e9');
        sp.position.set((x0 + x1) / 2, 2.4, -(z0 + z1) / 2);
        sp.userData.areaId = a.id;
        sp.userData.kind = 'rect';
        areaGroup.add(sp);
        areaLabels.push(sp);
        areaPicks.push(sp);
      } else if (a.xy) {
        const dot = new THREE.Mesh(
          new THREE.CircleGeometry(1.6, 24),
          new THREE.MeshBasicMaterial({ color: col, transparent: true, opacity: 0.75,
                                        depthWrite: false, side: THREE.DoubleSide }));
        dot.rotation.x = -Math.PI / 2;
        dot.position.set(a.xy[0], 0.1, -a.xy[1]);
        dot.userData.areaId = a.id;
        areaGroup.add(dot);
        areaPicks.push(dot);

        const sp = labelSprite(a.name || a.id, a.color || '#0ea5e9');
        sp.position.set(a.xy[0], 1.5, -a.xy[1]);
        sp.userData.areaId = a.id;
        sp.userData.kind = 'dot';
        areaGroup.add(sp);
        areaLabels.push(sp);
        areaPicks.push(sp);
      }
    }
  }

  setAreas(opts.areas || []);
  areaGroup.visible = opts.areasVisible !== false;

  // ---- 标签降噪 ----
  // 21 个功能区里 18 个是挂靠小圆点，从整场斜视看过去全挤在仓库中部；
  // 关掉整层会丢信息、全开又糊成一片。做法：按屏幕矩形做贪心去重——
  // 物理区（有地面板）优先，谁先占住屏幕位置谁留下，重叠的隐藏。
  // 缩小到一定距离只留物理区，放大后再把小圆点放出来。
  let labelMode = 'all';        // all | key | off
  const _proj = new THREE.Vector3();

  function declutter() {
    if (!areaLabels.length) return;
    const W = renderer.domElement.clientWidth || 1;
    const H = renderer.domElement.clientHeight || 1;
    if (labelMode === 'off' || !areaGroup.visible) {
      for (const sp of areaLabels) sp.visible = false;
      return;
    }
    // 小圆点（逻辑区）也一起参与去重：不按距离硬关，让贪心算法自己挑——
    // 整场视角下平均能留下 6–9 个不重叠的，比"只剩 3 个物理区"信息量高。
    const showDots = labelMode === 'all';

    const cands = [];
    for (const sp of areaLabels) {
      const kind = sp.userData.kind || 'dot';
      sp.getWorldPosition(_proj);
      _proj.project(camera);
      if (_proj.z > 1 || _proj.z < -1) { sp.visible = false; continue; }
      const x = (_proj.x + 1) / 2 * W;
      const y = (1 - _proj.y) / 2 * H;
      if (x < -60 || x > W + 60 || y < -30 || y > H + 30) { sp.visible = false; continue; }
      const hPx = sp.scale.y * H;
      const wPx = sp.scale.x * H;
      const box = [x - wPx / 2, y - hPx / 2, x + wPx / 2, y + hPx / 2];
      const priority = (kind === 'rect' ? 0 : showDots ? 2 : 9) + _proj.z;
      cands.push({ sp, box, priority, kind });
    }
    cands.sort((a, b) => a.priority - b.priority);

    const taken = [];
    const hit = (a, b) => a[0] < b[2] && b[0] < a[2] && a[1] < b[3] && b[1] < a[3];
    for (const c of cands) {
      if (c.kind === 'dot' && !showDots) { c.sp.visible = false; continue; }
      if (taken.some((t) => hit(t, c.box))) { c.sp.visible = false; continue; }
      taken.push(c.box);
      c.sp.visible = true;
    }
  }

  // ---- 状态 ----
  let selId = null;
  let follow = false;
  let visible = true;
  let raf = 0;
  let disposed = false;

  function update(t, sel, sampler) {
    selId = sel;
    for (const id in agents) {
      const a = agents[id];
      const p = sampler(id, t);
      if (!p) continue;
      a.obj.position.set(p.x, a.y, -p.y);
      // 原始 quaternion 只有 Y 分量，直接覆盖 rotation.y 是安全的
      a.obj.rotation.set(0, Math.PI / 2 - p.th, 0);

      const mk = markers[id];
      if (mk) {
        mk.position.set(p.x, 2.2, -p.y);
        mk.material.color.setHex(PH3[p.ph] || PH3.PARK);
        const sc = id === selId ? 0.027 : 0.017;
        mk.scale.set(sc, sc, 1);
      }
    }

    const cur = selId && agents[selId];
    if (cur) {
      const o = cur.obj.position;
      ring.position.set(o.x, 0.12, o.z);
      beam.position.set(o.x, 3.5, o.z);
      ring.visible = true;
      beam.visible = true;
    } else {
      ring.visible = false;
      beam.visible = false;
    }
  }

  // ---- 点击拾取 AGV ----
  const raycaster = new THREE.Raycaster();
  const ndc = new THREE.Vector2();
  let downPt = null;

  canvas.addEventListener('pointerdown', (e) => {
    downPt = { x: e.clientX, y: e.clientY };
  });
  canvas.addEventListener('pointerup', (e) => {
    if (!downPt) return;
    const moved = Math.hypot(e.clientX - downPt.x, e.clientY - downPt.y);
    downPt = null;
    if (moved > 4 || !opts.onPick) return;

    const r = canvas.getBoundingClientRect();
    ndc.x = ((e.clientX - r.left) / r.width) * 2 - 1;
    ndc.y = -((e.clientY - r.top) / r.height) * 2 + 1;
    raycaster.setFromCamera(ndc, camera);

    // 功能区优先：薄板在地面上、面积大，否则点哪都是"点到天花板"
    if (areaGroup.visible && opts.onAreaPick) {
      const hitA = raycaster.intersectObjects(areaPicks, false);
      if (hitA.length) {
        const id = hitA[0].object.userData.areaId;
        if (id) { opts.onAreaPick(id); return; }
      }
    }

    // 车体只有几像素，主要靠头顶标记点命中
    const targets = [
      ...Object.values(agents).map((a) => a.obj),
      ...Object.values(markers),
    ];
    const hits = raycaster.intersectObjects(targets, true);
    if (!hits.length) return;

    const hit = hits[0].object;
    if (hit.userData && hit.userData.agentId) {
      opts.onPick(hit.userData.agentId);
      return;
    }
    let o = hit;
    while (o && !AGV_RE.test(o.name)) o = o.parent;
    if (o) opts.onPick(o.name);
  });

  let frameNo = 0;
  function loop() {
    if (disposed) return;
    raf = requestAnimationFrame(loop);
    if (!visible) return;

    if (follow && selId && agents[selId]) {
      const o = agents[selId].obj.position;
      const goal = new THREE.Vector3(o.x, o.y + 1.2, o.z);
      const offset = camera.position.clone().sub(controls.target);
      controls.target.lerp(goal, 0.14);
      camera.position.copy(controls.target).add(offset);
    }
    controls.update();
    // 标签去重每 4 帧做一次：字体大小随相机变，逐帧算没必要
    if (++frameNo % 4 === 0) declutter();
    renderer.render(scene, camera);
  }
  loop();

  function resize() {
    const w = canvas.clientWidth || canvas.parentElement.clientWidth;
    const h = canvas.clientHeight || canvas.parentElement.clientHeight;
    if (!w || !h) return;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  }
  resize();
  preset('iso');

  return {
    update,
    preset,
    resize,
    setFollow(v) { follow = !!v; },
    setAreas,
    setAreasVisible(v) { areaGroup.visible = !!v; },
    setLabelMode(m) { labelMode = m || 'all'; frameNo = 3; },
    areaCount() { return areaPicks.length / 2; },
    setVisible(v) {
      visible = !!v;
      if (v) { resize(); controls.update(); }
    },
    stats() {
      return { agv: agvCount, nodes: nodeTotal, size };
    },
    dispose() {
      disposed = true;
      cancelAnimationFrame(raf);
      controls.dispose();
      renderer.dispose();
    },
  };
}
