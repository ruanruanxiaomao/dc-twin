#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""glb_canonical.py — 把 GLB 规整成**字节可复现**的形式（纯 Python，不依赖 Blender）。

## 为什么需要

同一份数据连跑两次（Blender 5.2.41），`dc_preview.glb` 的字节不同。实测两类原因：

1. **三角化顺序**：7 个索引缓冲各 720 字节，内容排序后完全相同，只有顺序不同。
   这是 `export_apply=True` 三角化时面顺序随内部哈希走造成的——几何没变。
2. **多余的 accessor**：带机理场景两次运行相差 1 个**没有任何 mesh 引用**的
   accessor（889 vs 888），连带 bufferView 也不一样，文件差 852 字节。

两者都不是"场景变了"，但都会让 sha256 变。金标准要是拿 sha256 当锚，就等于把
"这台机器当时怎么调度线程"冻进去——门会永远红，而且红得没信息量（ADR-12）。

## 做法

导出后原地规整，两步：

- **索引规范化**：每个三角形旋转到以最小顶点开头，再把三角形按字典序排序。
  同一块几何因此只有一种字节表示（三角形的顶点**绕序**保持不变，法线方向不受影响）。
- **未引用数据剔除**：只保留被 mesh/primitive 真正引用的 accessor 与 bufferView，
  其余丢弃，并重新紧密排布二进制块。

输出仍然是合法 glTF 2.0：JSON chunk 补空格、BIN chunk 补零，各自 4 字节对齐。

> 口径：规整只改**表示**，不改内容。`geometry_signature()` 是内容指纹（顶点坐标排序
> 摘要），无论规整前后都一致——用它来证明"规整没动几何"。
"""
from __future__ import annotations

import hashlib
import json
import struct

GLB_MAGIC = b"glTF"
CHUNK_JSON = 0x4E4F534A
CHUNK_BIN = 0x004E4942
ELEMENT_ARRAY = 34963
COMP_FMT = {5126: "f", 5125: "I", 5123: "H", 5121: "B", 5122: "h", 5120: "b"}


def parse(blob):
    """返回 (doc, bin_bytes)。只接受单 BIN chunk 的 GLB（Blender 导出的就是这种）。"""
    magic, version, total = struct.unpack("<4sII", blob[:12])
    if magic != GLB_MAGIC:
        raise ValueError("不是 GLB（magic=%r）" % (magic,))
    off, doc, binc = 12, None, b""
    while off < len(blob):
        clen, ctype = struct.unpack("<II", blob[off:off + 8])
        chunk = blob[off + 8:off + 8 + clen]
        if ctype == CHUNK_JSON:
            doc = json.loads(chunk.decode("utf-8"))
        elif ctype == CHUNK_BIN:
            binc = chunk
        off += 8 + clen
    return doc, binc


def build(doc, binc):
    js = json.dumps(doc, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    js += b" " * ((4 - len(js) % 4) % 4)
    bn = binc + b"\x00" * ((4 - len(binc) % 4) % 4)
    out = bytearray(GLB_MAGIC)
    out += struct.pack("<II", 2, 12 + 8 + len(js) + (8 + len(bn) if bn else 0))
    out += struct.pack("<II", len(js), CHUNK_JSON) + js
    if bn:
        out += struct.pack("<II", len(bn), CHUNK_BIN) + bn
    return bytes(out)


def _read_view(binc, view):
    start = view.get("byteOffset", 0)
    return binc[start:start + view.get("byteLength", 0)]


NCOMP = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}


def _acc_bytes(doc, binc, acc):
    """按 accessor 的语义切出它自己的字节（不依赖 bufferView 是否共享）。"""
    view = doc["bufferViews"][acc["bufferView"]]
    fmt = COMP_FMT[acc["componentType"]]
    n = acc["count"] * NCOMP[acc["type"]] * struct.calcsize(fmt)
    start = view.get("byteOffset", 0) + acc.get("byteOffset", 0)
    return binc[start:start + n]


def _canonical_indices(acc, raw):
    """把三角形索引用唯一顺序重写：三角形转到最小顶点开头，再按字典序排列。

    只改**顺序**：顶点绕序（从而法线朝向）不变，三角形的多重集不变。
    """
    fmt = COMP_FMT[acc["componentType"]]
    n = len(raw) // struct.calcsize(fmt)
    if n < 3:
        return raw
    vals = list(struct.unpack("<%d%s" % (n, fmt), raw[:n * struct.calcsize(fmt)]))
    body, rest = vals[:(n // 3) * 3], vals[(n // 3) * 3:]
    tris = []
    for i in range(0, len(body), 3):
        t = body[i:i + 3]
        k = t.index(min(t))
        tris.append(tuple(t[k:] + t[:k]))
    tris.sort()
    flat = [v for t in tris for v in t] + rest
    return struct.pack("<%d%s" % (len(flat), fmt), *flat)


def canonicalize(blob):
    """返回规整后的 GLB 字节。幂等（对已规整输入再跑一次结果相同）。

    三步：① 丢掉没被引用的 accessor；② 一个 accessor 一个紧密 bufferView（不依赖
    导出器怎么合并视图）；③ 索引规范化 + **相同数据合并**（导出器是否复用一个
    accessor 是随机的，实测同一场景 889 vs 888，把这条随机性按掉）。
    """
    import hashlib
    doc, binc = parse(blob)
    accs = doc.get("accessors", [])

    refs = set()
    for m in doc.get("meshes", []):
        for pr in m.get("primitives", []):
            refs.update(v for v in (pr.get("attributes") or {}).values() if isinstance(v, int))
            if isinstance(pr.get("indices"), int):
                refs.add(pr["indices"])
            for t in pr.get("targets", []) or []:
                refs.update(v for v in t.values() if isinstance(v, int))
    for sk in doc.get("skins", []) or []:
        if isinstance(sk.get("inverseBindMatrices"), int):
            refs.add(sk["inverseBindMatrices"])
    for an in doc.get("animations", []) or []:
        for sm in an.get("samplers", []):
            refs.update(v for v in (sm.get("input"), sm.get("output")) if isinstance(v, int))

    uniq = {}          # 内容键 -> 新 index
    acc_map = {}       # 旧 index -> 新 index
    new_accs, chunks = [], []
    cursor = 0
    for old in sorted(refs):
        acc = accs[old]
        raw = _acc_bytes(doc, binc, acc)
        old_view = doc["bufferViews"][acc["bufferView"]]
        if old_view.get("target") == ELEMENT_ARRAY:
            raw = _canonical_indices(acc, raw)
        key = (acc["componentType"], acc["type"], acc["count"],
               bool(acc.get("normalized")), hashlib.sha256(raw).hexdigest())
        if key in uniq:
            acc_map[old] = uniq[key]
            continue
        new = {k: v for k, v in acc.items() if k not in ("bufferView", "byteOffset")}
        new["bufferView"] = len(chunks)
        new["byteOffset"] = 0
        acc_map[old] = len(new_accs)
        uniq[key] = len(new_accs)
        new_accs.append(new)
        chunks.append({"raw": raw, "target": old_view.get("target"),
                       "byteOffset": cursor})
        cursor += len(raw) + ((4 - len(raw) % 4) % 4)

    binout = bytearray()
    for c in chunks:
        binout += c["raw"]
        binout += b"\x00" * ((4 - len(c["raw"]) % 4) % 4)

    for m in doc.get("meshes", []):
        for pr in m.get("primitives", []):
            if pr.get("attributes"):
                pr["attributes"] = {k: acc_map[v] for k, v in pr["attributes"].items()}
            if isinstance(pr.get("indices"), int):
                pr["indices"] = acc_map[pr["indices"]]
            if pr.get("targets"):
                pr["targets"] = [{k: acc_map[v] for k, v in t.items()} for t in pr["targets"]]

    doc["accessors"] = new_accs
    doc["bufferViews"] = [
        {"buffer": 0, "byteOffset": c["byteOffset"], "byteLength": len(c["raw"]),
         **({"target": c["target"]} if c["target"] else {})} for c in chunks
    ]
    doc["buffers"] = [{"byteLength": len(binout)}]
    doc = json.loads(json.dumps(doc, sort_keys=True))
    return build(doc, bytes(binout))


def geometry_signature(blob):
    """内容指纹：所有 POSITION 的坐标排序后取哈希。规整前后必须相同。"""
    doc, binc = parse(blob)
    accs = doc.get("accessors", [])
    pts = []
    for m in doc.get("meshes", []):
        for pr in m.get("primitives", []):
            idx = (pr.get("attributes") or {}).get("POSITION")
            if not isinstance(idx, int):
                continue
            acc = accs[idx]
            view = doc["bufferViews"][acc["bufferView"]]
            raw = _read_view(binc, view)
            count = acc.get("count", 0)
            vals = struct.unpack("<%df" % (count * 3), raw[:count * 12])
            pts.extend(tuple(round(v, 4) for v in vals[i:i + 3])
                       for i in range(0, len(vals), 3))
    pts.sort()
    return hashlib.sha256(json.dumps(pts).encode("utf-8")).hexdigest()


def _main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="规整 GLB：索引规范化 + 剔除未引用数据")
    ap.add_argument("path")
    ap.add_argument("--check", action="store_true", help="只比对规整前后是否一致，不写盘")
    a = ap.parse_args(argv)
    raw = open(a.path, "rb").read()
    out = canonicalize(raw)
    sig_before, sig_after = geometry_signature(raw), geometry_signature(out)
    print("GLB_CANON %s bytes %d -> %d  geometry %s" % (
        a.path, len(raw), len(out),
        "SAME" if sig_before == sig_after else "CHANGED!"))
    if sig_before != sig_after:
        return 2
    if a.check:
        print("GLB_CANON_STABLE" if out == raw else "GLB_CANON_WOULD_CHANGE")
        return 0 if out == raw else 1
    open(a.path, "wb").write(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
