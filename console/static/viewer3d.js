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
    iso: () => frameCamera(new THREE.Vector3(0.62, 0.78, 0.72), 0.84),
    top: () => frameCamera(new THREE.Vector3(0.001, 1, 0.02), 0.86),
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
