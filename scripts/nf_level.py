#!/usr/bin/env python3
"""Crash Team Racing Nitro-Fueled level -> native RTX remaster mesh.

Reads a level's map files (igEntity placements of models), the models
(igModelData draw calls), their materials and textures, its point lights and
skybox from an extracted level archive (scripts/nf_iga.py) and writes what the
64-bit renderer draws on the "NF" track (scripts/nf_tracks.py) in place of
the PS1 level mesh; the game still plays the stock PS1 level underneath.

    nf_level.py all [out_dir]       every NF track whose archive is unpacked
                                    (scripts/nf_setup.sh): nf_raw/<pak>/,
                                    out_dir/nf-<tid>.rtxm + .floor when stale
    nf_level.py stats <tid>...      art maps chosen, entities, models, triangles
    nf_level.py fit <tid>...        how well the PS1 drivable surface lies on
                                    the NF ground under the placement
    (tid: a stock slot, or a hosted track's id, nf_tracks.HOSTED: t211 ...)

Nitro-Fueled places the PS1 tracks 1.5x larger, Z up, each turned its own way
(and Dingo Canyon moved): placement() solves PS1 = M @ NF + t per level from
the race spline against the PS1 restart path, then the PS1 floor onto the NF
surfaces (nf_raw/placement.json). Fan remakes (hosted tracks) have their own
scale too (solve_similar); one that doesn't fit is marked "bad", left out.

.rtxm (little endian, native/rtx/ctr_rtx.c loadRemaster), version 3:
  header   RtxmHeader, 128 bytes (pack HEADER)
  verts    f32 pos[3] (PS1 units, Y up), f32 uv[2], u32 normal (octahedral
           snorm16 x2), u32 rgba8, u32 tangent (octahedral snorm16 x2, low bit
           of y: bitangent sign) or, grass, u16 sway (x1/1024 units), u16 phase
                                                                    32 bytes
           grass vertices last (from grassVert): the wind animates them
  tris     u32 v[3], u32 material, grouped: opaque | alpha tested + water |
           grass | decals (groupStart)                               16 bytes
  mats     u32 tex0, tex1, emit, flags, normal0, normal1; f32 alphaRef; u32 emit2;
           f32 tint[4] (w: opacity of blended ones; directional
           blends: direction x scale, bias; second layer scrolls: z, w
           half2 scale, scroll)                                      48 bytes
  textures u32 kind (1 BC1, 3 BC3, 5 BC5, 8 BGRA8), w, h, mips, u64 offset,
           u64 size: every mip's blocks, top first                   32 bytes
  lights   f32 pos[3], radius, colour[3] (x intensity), kind        32 bytes
           (kind 0: a level point light, 1: a glowing surface's, 2: lava's, glow_lights)
  sky      6 x skySize^2 BGRA8 cube faces (+X -X +Y -Y +Z -Z, PS1 axes)
.floor     the NF drivable surface for kart seating (native_rtx.c): header
           "RTXF", u32 version 1, f32 minX, minZ, cell, u32 dimX, dimZ,
           layers; i16 height[dimZ][dimX][layers] (-32768 none)
"""
import glob
import math
import re
import os
import pathlib
import struct
import sys

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import nf_igz  # noqa: E402
import nf_tex  # noqa: E402
import nf_tracks  # noqa: E402

ROOT = HERE.parent
WORK = ROOT / "nf_raw"
OUT_DIR = ROOT / "native/build/assets/rtx_remaster"

PS1_FROM_NF = np.array([[0.0, -1.0, 0.0], [0.0, 0.0, 1.0], [-1.0, 0.0, 0.0]]) / 1.5
NF_SCALE = 1.5

# material flags (shared with ctr_rtx.c / common.glsl)
MAT_ALPHA_CLIP = 1  # texture alpha < alphaRef: no hit
MAT_DECAL = 2       # drawn over the surface below (camera hits, blended)
MAT_WATER = 4       # semi-transparent reflective water
MAT_BLEND2 = 8      # two colour layers blended by vertex alpha + height
MAT_UNLIT = 16      # emissive as drawn
MAT_GRASS = 32      # foliage: vertex colour = wind weight, swayed
MAT_NORMAL = 64     # normal map(s)
MAT_EMIT = 128      # emission map
MAT_TRANSLUCENT = 256  # alpha blended in Nitro-Fueled: coverage by texture alpha
MAT_ICE = 512       # ice: mirror-like (TRI_ICE)
MAT_GLASS = 1024    # glass: clear, a little reflective (TRI_GLASS)
MAT_EMIT_MASK = 2048  # the emission texture is a composite whose red is a glow mask over the colour (lava)
MAT_DIRBLEND = 4096   # second layer only where the surface faces a direction (snow on rocks' tops): tint = (dir x scale, bias)
MAT_EMIT_LAYER1 = 8192   # two layers, one glow mask, and it is the first layer's: glows only where that layer shows
MAT_EMIT_LAYER2 = 16384  # ... the second layer's (Oxide Station's floor lights over its floor)
MAT_BLEND_TEX = 32768    # two layers on a draw with no vertex colours: the second where the first's alpha x tint.x + tint.y is low
MAT_UV_SCROLL2 = 65536   # the second layer's uvs scaled and scrolling (Oxide Station's floor lights): tint.z, tint.w = half2 scale, scroll per second
MAT_EMIT_ALBEDO = 131072  # the emission map is a flat grey (Common!G_50): the colour glows that much (waterfalls), not white
MAT_UV_SCROLL1 = 262144   # the (first) layer's uvs scaled and scrolling: tint.x, tint.y = half2 scale, scroll per second (waterfalls, N. Gin Labs' tunnel)
ADDITIVE_COVER = 0.35     # (an additive material drawn as a translucent glow this opaque)
MAT_TURBO = 524288        # a turbo pad as drawn (nf_collision: its triggers); MAT_TURBO_SUPER a super one
MAT_TURBO_SUPER = 1048576
PAD_NAME = re.compile(r"boost_?pad|turbo_?pad|speed_?boost|jump_?boost", re.I)
FLAT_GREY = re.compile(r"Common!(G|Grey)_(\d+)")  # an emission map of one grey level (percent)
RUNNING_LIGHT_BASE = 0.025  # (common.glsl) a scrolling glow layer's mask between its pulses: the channels always glow


def texture_stem(image):
    """a texture reference's own name without its channel suffix: ...!T154_FloorLight01_E`tga
    and ...!T154_FloorLight01_C`tga -> t154_floorlight01 (composites: their first source)"""
    name = image.split("`")[0].split("!")[-1]
    return re.sub(r"_(\{[^}]*\}|[A-Za-z])$", "", name).lower()

ICE_NAME = re.compile(r"icejump|ice_?decal|fakeice|start_ice|waterfall_ice|frozenlake|frozensnow|ice_floor|iceroad|(^|_)ice\d", re.I)

TEX_KIND = {"bc1": 1, "bc3": 3, "bc5": 5, "bgra8": 8, "rgba8": 9}
HEADER = "<4sI4I2I5I3I6Q8h4f2IQ"  # RtxmHeader (version 4)
KIND_STATIC, KIND_GRASS, KIND_ANIM, KIND_PROXY = 0, 1, 2, 3  # vertex ranges, in this order
# NF model space (x forward, z up) -> a PS1 model's own (z forward, y up), PS1 size
PROXY_FROM_NF = np.array([[0.0, 1.0, 0.0], [0.0, 0.0, 1.0], [1.0, 0.0, 0.0]]) / 1.5
SKY_SIZE = 512
FLOOR_CELL = 32.0
# effects not drawable as plain triangles: cloud particle sheets (their quads are
# expanded in NF's vertex shader; as stored they are kilometre wide blobs over
# Hot Air Skyway's road) and rim-lit glass (only its fresnel edge shows)
SKIP_EFFECTS = ("CloudParticleSorted", "UnlitRim")
# Composite3 textures: (emission mask, gloss, metal) channels; an _E source = it glows
EMIT_COMPOSITE = re.compile(r"_E[,`}]|_\{E,")
EMIT_MASK_GAIN = 8.0  # (common.glsl: mask x colour x this)
# glow-mask surfaces' lights x this (their glow sits in the cracks the mask
# picks; the lights stand for the whole surface). Lava the PS1 level has too is
# lit as the stock track lights it (the renderer: ctr_rtx.c levelPrep)
GLOW_MASK_BOOST = 2.0
# glowing surfaces light their surroundings: point lights per cluster (ctr_rtx.c addGlowLights' way)
GLOW_CELL = 300.0
GLOW_MAX = 384  # (as the PS1 levels: ctr_rtx.c addGlowLights)
GLOW_GAIN = 24.0  # (ctr_rtx.c emissive_gain)
# materials only distorting what's behind them (N.Gin Labs' heat haze, on the Water
# effect; the waterfalls' white sheets, Water with default_c) or hazing it (Sewer
# Speedway's orange fog sheets, on FlowWater; the tracer has its own haze)
SKIP_MATERIALS = re.compile(r"heat_?waves?|fog|waterfall_vfx", re.I)
# Water / FlowWater effect surfaces that are see-through solids: Dingo Canyon's
# windows glass, ice blocks (round Polar Pass' bear) frosted see-through ice
# with their own colour, not water
WATER_AS_GLASS = re.compile(r"window", re.I)
WATER_AS_ICE = re.compile(r"(^|_)ice", re.I)

# vertex element types (IG_VERTEX_TYPE)
VT_FLOAT2, VT_FLOAT3, VT_FLOAT4 = 1, 2, 3
VT_UBYTE4N_COLOR = 4
VT_HALF2, VT_HALF4 = 0x2A, 0x2B
# usages (IG_VERTEX_USAGE)
VU_POSITION, VU_NORMAL, VU_TANGENT, VU_COLOR, VU_TEXCOORD = 0, 1, 2, 4, 5


def model_file(root, ref):
    """'models:\\Levels\\X\\Y.igb' -> <root>/models/Levels/X/Y.igz"""
    ns, _, rest = ref.partition(":")
    rel = rest.replace("\\", "/").lstrip("/")
    rel = os.path.splitext(rel)[0] + ".igz"
    return os.path.join(root, ns, rel)


def _element(z, vb_data, count, stride, etype, offset):
    raw = np.frombuffer(z.d, np.uint8, count * stride, vb_data).reshape(count, stride)
    col = raw[:, offset:]
    if etype == VT_FLOAT3:
        return col[:, :12].copy().view("<f4").reshape(count, 3)
    if etype == VT_FLOAT2:
        return col[:, :8].copy().view("<f4").reshape(count, 2)
    if etype == VT_HALF2:
        return col[:, :4].copy().view("<f2").reshape(count, 2).astype(np.float32)
    if etype == VT_HALF4:
        return col[:, :8].copy().view("<f2").reshape(count, 4).astype(np.float32)
    if etype == VT_UBYTE4N_COLOR:
        return col[:, :4].copy()
    raise ValueError(f"vertex type {etype:#x} unsupported")


ANIM_RATE = 30.0       # animated node keys sampled per second (the GPU interpolates)
ANIM_MAX_KEYS = 2048
ANIM_MAX_PERIOD = 120.0


def quat_rows(q):
    """(x, y, z, w) -> 3x3 rotation for row vectors (p @ R), as Alchemy applies
    its keys (the transpose of the usual column-vector matrix: N.Gin Labs' tesla
    coil arcs only land back on their coil this way)"""
    x, y, z, w = q / max(float(np.linalg.norm(q)), 1e-12)
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


class AnimSource:
    """igAnimatedTransformSource: keyed translation (channel bit 0), rotation
    quaternion (bit 1), scale (bit 3), at channel offsets [translation,
    rotation, scale]; channels it lacks come from the node's own matrix."""

    def __init__(self, z, s, base):
        stride = max(z.u32(s + 0x0C), 1)
        data = np.frombuffer(z.d, "<f4", z.u32(s + 0x18) // 4, z.ptr(s + 0x20)).astype(np.float64) if z.ptr(s + 0x20) else np.zeros(0)
        nk = len(data) // stride
        self.keys = data[:nk * stride].reshape(nk, stride)
        times = np.frombuffer(z.d, "<f4", z.u32(s + 0x30) // 4, z.ptr(s + 0x38)).astype(np.float64) if z.ptr(s + 0x38) else np.zeros(0)
        self.duration = float(z.f32(s + 0x40))
        self.times = times if len(times) == nk else np.linspace(0.0, self.duration, max(nk, 1))
        self.offset = float(z.f32(s + 0x44))
        chans = z.u32(s + 0x48)
        offs = list(z.d[s + 0x4C:s + 0x4F])
        self.chan = {}
        for k, (bit, size) in enumerate(((0, 3), (1, 4), (3, 3))):
            if chans & (1 << bit) and offs[k] + size <= stride:
                self.chan[bit] = offs[k]
        self.base = base
        self.ok = nk > 0 and self.duration > 0 and np.isfinite(self.keys).all()

    def value(self, bit, size, t):
        o = self.chan[bit]
        t = (t + self.offset) % self.duration
        i = int(np.clip(np.searchsorted(self.times, t, "right") - 1, 0, len(self.times) - 1))
        j = min(i + 1, len(self.times) - 1)
        span = self.times[j] - self.times[i]
        f = (t - self.times[i]) / span if span > 1e-9 else 0.0
        a, b = self.keys[i, o:o + size], self.keys[j, o:o + size]
        if bit == 1 and np.dot(a, b) < 0:
            b = -b
        return a + (b - a) * f

    def matrix(self, t):
        m = self.base.copy()
        if not self.ok:
            return m
        upper = m[:3, :3]
        norms = np.linalg.norm(upper, axis=1)
        if 1 in self.chan:
            sc = self.value(3, 3, t) if 3 in self.chan else norms
            upper = sc[:, None] * quat_rows(self.value(1, 4, t))
        elif 3 in self.chan:
            upper = (self.value(3, 3, t) / np.maximum(norms, 1e-12))[:, None] * upper
        m[:3, :3] = upper
        if 0 in self.chan:
            m[3, :3] = self.value(0, 3, t)
        return m


def model_draw_transforms(z):
    """(draw call, 4x4 row-vector transform or None, animation or None) in
    igModelData order: each draw call's node transform (1-based
    _drawCallTransformIndices into _transforms, an igAnimatedTransform matrix at
    +0x20) composed with its parents (_transformHierarchy). Multi-part models
    (Hot Air Skyway's zeppelins: parts at 1/40 scale) are only right with these.
    Nodes with an igAnimatedTransformSource (+0x60: propellers, fans, gears,
    wheels) are posed by their keys at time 0; animation = (period, (n, 4, 4)
    chain sampled at ANIM_RATE over the period)."""
    out = []
    for md in z.of_type("igModelData"):
        def vec(a, size=8):
            n, p = z.u64(a), z.ptr(a + 16)
            return [z.u64(p + size * i) if size == 8 else z.i32(p + 4 * i) for i in range(n)] if p else []
        n_tr, p_tr = z.u64(md + 0x40), z.ptr(md + 0x50)
        mats, srcs = [], []
        for i in range(n_tr if p_tr else 0):
            t = z.ptr(p_tr + 8 * i)
            ok = t and z.obj_type(t) == "igAnimatedTransform"
            m = np.array(z.vec(t + 0x20, 16)).reshape(4, 4) if ok else np.eye(4)
            src = z.ptr(t + 0x60) if ok else None
            a = AnimSource(z, src, m) if src and z.obj_type(src) == "igAnimatedTransformSource" else None
            mats.append(m)
            srcs.append(a if a is not None and a.ok else None)
        parent = vec(md + 0x58, 4)
        n_dc, p_dc = z.u64(md + 0x70), z.ptr(md + 0x80)
        dc_tr = vec(md + 0x88, 4)

        def chain(k):  # 1-based transform index -> its nodes, leaf first
            out_, depth = [], 0
            while 0 < k <= len(mats) and depth <= 32:
                out_.append(k - 1)
                k = parent[k - 1] if k - 1 < len(parent) else 0
                depth += 1
            return out_

        def world(nodes, t):
            m = np.eye(4)
            for n in nodes:
                m = m @ (srcs[n].matrix(t) if srcs[n] is not None else mats[n])
            return m

        for i in range(n_dc if p_dc else 0):
            dc = z.ptr(p_dc + 8 * i)
            if dc and z.obj_type(dc) == "igModelDrawCallData":
                k = dc_tr[i] if i < len(dc_tr) else 0
                nodes = chain(k) if k > 0 else []
                anim = None
                durs = [srcs[n].duration for n in nodes if srcs[n] is not None]
                if durs:
                    period = min(max(durs), ANIM_MAX_PERIOD)
                    nk = int(min(math.ceil(period * ANIM_RATE) + 1, ANIM_MAX_KEYS))
                    keys = np.stack([world(nodes, j * period / (nk - 1)) for j in range(nk)])
                    if np.isfinite(keys).all() and np.ptp(keys, axis=0).max() > 1e-6:
                        anim = (period, keys)
                out.append((dc, world(nodes, 0.0) if nodes else None, anim))
    seen = {dc for dc, _, _ in out}
    out += [(dc, None, None) for dc in z.of_type("igModelDrawCallData") if dc not in seen]
    return out


def load_model(path):
    """Draw calls of a model: dicts with pos/normal/tangent/uv/color/index/material
    (in model space: node transforms applied)."""
    z = nf_igz.Igz(path)
    draws = []
    for dc, xf, anim in model_draw_transforms(z):
        gvb, gib = z.ptr(dc + 0x48), z.ptr(dc + 0x50)
        vb, ib = z.ptr(gvb + 0x20), z.ptr(gib + 0x20)
        count = z.u32(vb + 0x0C)
        vdata, _ = z.mem(vb + 0x20)
        vf = z.ptr(vb + 0x30)
        stride = z.u32(vf + 0x0C)
        ep, es = z.mem(vf + 0x10)
        d = {"pos": None, "normal": None, "tangent": None, "uv": None, "color": None}
        for k in range(es // 12):
            e = z.d[ep + 12 * k:ep + 12 * k + 12]
            etype, usage, uidx = e[0], e[4], e[5]
            if etype == 0x2C:
                break
            off = int.from_bytes(e[8:10], "little")
            if usage == VU_POSITION:
                d["pos"] = _element(z, vdata, count, stride, etype, off)
            elif usage == VU_NORMAL:
                d["normal"] = _element(z, vdata, count, stride, etype, off)
            elif usage == VU_TANGENT and uidx == 0:
                d["tangent"] = _element(z, vdata, count, stride, etype, off)[:, :3]
            elif usage == VU_COLOR and uidx == 0:
                d["color"] = _element(z, vdata, count, stride, etype, off)
            elif usage == VU_TEXCOORD and uidx == 0:
                d["uv"] = _element(z, vdata, count, stride, etype, off)[:, :2]
        icount = z.u32(ib + 0x0C)
        idata, isize = z.mem(ib + 0x20)
        if isize >= icount * 4 and count > 0xFFFF:
            idx = np.frombuffer(z.d, "<u4", icount, idata)
        else:
            idx = np.frombuffer(z.d, "<u2", icount, idata)
        d["index"] = idx.astype(np.uint32).reshape(-1, 3)
        d["material_ns"], d["material"] = z.handle_name(dc + 0x40) or (None, None)
        d["anim"] = None
        if anim is not None and d["pos"] is not None:
            # (node space: the GPU moves these by the sampled chain)
            d["anim"] = (anim[0], anim[1], d["pos"].astype(np.float64), d["normal"], d["tangent"])
        if xf is not None and d["pos"] is not None and not np.allclose(xf, np.eye(4)):
            r = xf[:3, :3]
            d["pos"] = (d["pos"].astype(np.float64) @ r + xf[3, :3]).astype(np.float32)
            inv_t = np.linalg.inv(r) if abs(np.linalg.det(r)) > 1e-12 else r
            if d["normal"] is not None:
                d["normal"] = (d["normal"].astype(np.float64) @ inv_t.T).astype(np.float32)
            if d["tangent"] is not None:
                d["tangent"] = (d["tangent"].astype(np.float64) @ r).astype(np.float32)
            if np.linalg.det(r) < 0:
                d["index"] = d["index"][:, ::-1].copy()
        draws.append(d)
    return draws


def euler_matrix(rx, ry, rz):
    """Alchemy parent space rotation (radians): R = Rz * Ry * Rx."""
    cx, sx = math.cos(rx), math.sin(rx)
    cy, sy = math.cos(ry), math.sin(ry)
    cz, sz = math.cos(rz), math.sin(rz)
    mx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    my = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    mz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    return mz @ my @ mx


def entity_transform(z, e):
    pos = np.array(z.vec(e + 0x20, 3))
    m = np.eye(3)
    tr = z.ptr(e + 0x30)
    if tr:
        m = euler_matrix(*z.vec(tr + 0x60, 3)) * np.array(z.vec(tr + 0x70, 3))[None, :]
    return m, pos


def entity_components(z, e):
    ed = z.ptr(e + 0x40)
    table = z.ptr(ed + 0x10) if ed else None
    if not table:
        return []
    vp, vs = z.mem(table + 0x10)
    return [v for v in (z.ptr(vp + 8 * k) for k in range(vs // 8)) if v]


def visible_model(ref):
    """Collision / shadow-only meshes the art maps also place (drawn with test
    textures in Dingo Canyon; *_ShadowOnly / *_Shadow casters) are not for drawing."""
    name = ref.replace("\\", "/").rsplit("/", 1)[-1].lower()
    stem = name.rsplit(".", 1)[0]
    return not (any(k in name for k in ("collision", "_col_", "shadowcaster", "navmesh", "shadowonly")) or stem.endswith("_shadow"))


def load_entities(path, stats=None):
    """Model placements in a map file: (model ref, 3x3 matrix, translation).

    Prefabs: an entity whose entityData has an igPrefabComponentData stands for
    the entities of its _prefabEntities list, each placed relative to it (their
    own transforms are local; they are bolted to the prefab by name). Those
    member entities are drawn only through their prefab's instances."""
    z = nf_igz.Igz(path)

    def info(e):
        model, kids = None, []
        for v in entity_components(z, e):
            t = z.obj_type(v)
            if t == "CModelComponentData":
                model = z.string(v + 0x10)
            elif t == "igPrefabComponentData":
                lst = z.ptr(v + 0x10)
                kids += [k for k in (z.object_list(lst) if lst else []) if k and z.obj_type(k) == "igEntity"]
        return model, kids

    ents = z.of_type("igEntity")
    data = {e: info(e) for e in ents}
    members = {k for _, kids in data.values() for k in kids}
    out = []

    def place(e, pm, pt, depth):
        model, kids = data.get(e) or info(e)
        m, pos = entity_transform(z, e)
        wm, wt = pm @ m, pm @ pos + pt
        if model and visible_model(model):
            out.append((model, wm, wt))
        for k in kids if depth < 8 else []:
            place(k, wm, wt, depth + 1)

    orphans = 0
    for e in ents:
        if e in members:
            continue
        b = z.ptr(e + 0x10)
        if b and z.obj_type(b) == "CEntityBolt" and z.handle_name(b + 0x10):
            orphans += 1  # bolted to something no prefab here lists (stale): placed as it is
        place(e, np.eye(3), np.zeros(3), 0)
    if stats is not None:
        stats["members"] = stats.get("members", 0) + len(members)
        stats["orphans"] = stats.get("orphans", 0) + orphans
    return out


def level_dir(root):
    dirs = [d for d in glob.glob(os.path.join(root, "maps", "Octane", "*")) if os.path.isdir(d)]
    if len(dirs) != 1:
        raise SystemExit(f"{root}: expected one level under maps/Octane, found {dirs}")
    return dirs[0]


def map_wanted(name, level):
    """Whether the PS4 one player race loads map file <level><suffix> (art,
    lighting): the exclusion lists (_Excl_2P, _Ex_4P, _Exclude_4P, _NoSwitch...)
    yes; the Switch / NX only sets, the low resolution / last-gen variants and
    the 2P / 4P only extras no."""
    s = name[len(level):].lower()
    if any(k in s for k in ("switch_only", "switchdetail", "switch_detail", "switchsp", "switch_sp", "nx_only", "1ponly")):
        return False
    if any(k in s for k in ("excl", "_ex_", "exclu")) or s.endswith("_ex"):
        return True
    if any(k in s for k in ("switch", "_nx", "lowres", "gen7")) or s.endswith(("_2p", "_4p")):
        return False
    return True


def level_entities(root, stats=None):
    ents = []
    ldir = level_dir(root)
    for p in sorted(glob.glob(os.path.join(ldir, "*_[Aa]rt*.igz"))):
        if map_wanted(os.path.basename(p)[:-4], os.path.basename(ldir)):
            ents += [(os.path.basename(p),) + e for e in load_entities(p, stats)]
    # (models placed by script, in world space: nf_tracks.WORLD_MODELS)
    for pattern in nf_tracks.WORLD_MODELS.get(os.path.basename(os.path.normpath(root)), []):
        for f in sorted(glob.glob(os.path.join(root, "models", pattern))):
            rel = os.path.relpath(f, os.path.join(root, "models"))
            ents.append(("world", "models:\\" + os.path.splitext(rel)[0].replace("/", "\\") + ".igb", np.eye(3), np.zeros(3)))
    return ents


class Shared:
    """Files a level archive references but doesn't hold (permanent.pak and other
    levels' archives): found through an index of every archive, extracted once
    into <dir>. archives: one directory, or a list searched in order (the game
    package's first; files only a later one has are extracted to <dir>_update,
    so the game package's tracks never pick them up)."""

    def __init__(self, archives, shared_dir):
        self.archives = None if archives is None else [str(d) for d in ([archives] if isinstance(archives, (str, os.PathLike)) else archives)]
        self.dir = shared_dir
        self.index = None
        self.by_name = None

    def _out_dir(self, k):
        return self.dir if k == 0 else self.dir + "_update"

    def _load_index(self):
        import json
        import nf_iga
        self.index = {}
        for k, adir in enumerate(self.archives):
            cache = os.path.join(self.dir, "pak_index.json" if k == 0 else f"pak_index_{k}.json")
            if os.path.exists(cache):
                with open(cache) as f:
                    index = json.load(f)
            else:
                index = {}
                for f in sorted(glob.glob(os.path.join(adir, "*.pak"))):
                    try:
                        a = nf_iga.Iga(f)
                    except ValueError:
                        continue
                    for fi in a.files:
                        index.setdefault((fi["logical"] or fi["name"]).lower(), []).append(os.path.basename(f))
                if not index:
                    continue  # (that package isn't unpacked)
                os.makedirs(self.dir, exist_ok=True)
                with open(cache, "w") as f:
                    json.dump(index, f)
            for name, paks in index.items():
                self.index.setdefault(name, []).extend((k, p) for p in paks)
        self.by_name = {}
        for k in self.index:
            self.by_name.setdefault(os.path.basename(k), k)

    def find(self, rel):
        """Path of archive-relative file rel (any case), extracted on first use, or None."""
        n = 1 if self.archives is None else len(self.archives)
        for k in range(n):
            out = os.path.join(self._out_dir(k), rel.lower())
            if os.path.exists(out):
                return out
        if self.archives is None:
            return None
        if self.index is None:
            self._load_index()
        paks = self.index.get(rel.lower())
        if not paks:
            return None
        import nf_iga
        k, pak = paks[0]
        a = nf_iga.Iga(os.path.join(self.archives[k], pak))
        out = os.path.join(self._out_dir(k), rel.lower())
        for fi in a.files:
            if (fi["logical"] or fi["name"]).lower() == rel.lower():
                os.makedirs(os.path.dirname(out), exist_ok=True)
                with open(out, "wb") as o:
                    o.write(a.read(fi))
                return out
        return None

    def find_name(self, basename):
        """By file name alone (materials are referenced by namespace)."""
        if self.archives is None:
            return None
        if self.index is None:
            self._load_index()
        k = self.by_name.get(basename.lower())
        return self.find(k) if k else None


def archive_dirs(tid):
    """where a track's shared files are looked for: the game package only for
    the stock NF tracks (as they always were), the update's too for hosted ones"""
    if isinstance(tid, int):
        return [nf_tracks.ARCHIVES]
    return [nf_tracks.ARCHIVES, nf_tracks.UPDATE, nf_tracks.UPDATE_LEVELS]


def blending_enabled(z, m):
    """Whether a material's render state commands enable alpha blending: its
    igMemoryCommandStream memory holds u64 words (0x0e, 2, 1) (state 2 on)."""
    for c in z.reachable(m, {"igMemoryCommandStream"}):
        for o in range(0x10, z.obj_size(c), 8):
            if c + o not in z.ptr_fields:
                continue
            p, n = z.ptr(c + o), z.u32(c + o - 8) & 0xFFFFFF
            if p and p not in z.objects and n >= 24:
                w = struct.unpack_from(f"<{n // 8}Q", z.d, p)
                for i in range(len(w) - 2):
                    if w[i] == 0x0E and w[i + 1] == 2:
                        return w[i + 2] == 1
    return False


def shader_constants(z, m):
    """A material's named shader constants (name after '__' -> float, bool as
    int, or vec4): each value object comes just before its constant."""
    out, last = {}, None
    for o in z.reachable(m):
        t = z.obj_type(o)
        if t.startswith("igShaderConstantValue") and t != "igShaderConstantValueList":
            last = z.vec(o + 0x20, 4) if t.endswith("Vector") else z.u32(o + 0x18) if t.endswith("Bool") else z.f32(o + 0x18)
        elif t == "igGraphicsShaderConstant":
            out[(z.name(o) or "").split("__")[-1]] = last
            last = None
    return out


def colour_value(z, m):
    """A material's colour constant (rgba; Standard: ig_color_value, its alpha
    the opacity of blended ones: glass), from its command stream (u64 words
    0x1e, 0, slot, 0, then the vec4); None when it sets none or several."""
    found = []
    for c in z.reachable(m, {"igMemoryCommandStream"}):
        for o in range(0x10, z.obj_size(c), 8):
            if c + o not in z.ptr_fields:
                continue
            p, n = z.ptr(c + o), z.u32(c + o - 8) & 0xFFFFFF
            if p and p not in z.objects and n >= 48:
                w = struct.unpack_from(f"<{n // 8}Q", z.d, p)
                for i in range(len(w) - 5):
                    if w[i] == 0x1E and w[i + 1] == 0 and w[i + 3] == 0:
                        found.append(struct.unpack_from("<4f", z.d, p + 8 * (i + 4)))
    return found[0] if len(found) == 1 else None


def texture_scrolls(z, m):
    """A material's texture matrix animations (igGraphicsMaterialAnimation ->
    igAnimatedTransformSource keying translation and scale; the colour ones
    key only a vec4): (scale u, v, scroll u, v per second) each, the scroll
    from its first key to its last (they run linearly: Oxide Station's floor
    lights 0 -> 4 over 10 s, their texture stretched 10x along the road)."""
    out = []
    for a in z.reachable(m, {"igGraphicsMaterialAnimation"}):
        s = z.ptr(a + 0x10)
        if not s or z.obj_type(s) != "igAnimatedTransformSource":
            continue
        src = AnimSource(z, s, np.eye(4))
        if not src.ok or 0 not in src.chan or 3 not in src.chan:
            continue
        k0, k1 = src.keys[0], src.keys[-1]
        sc, t0, t1 = k0[src.chan[3]:src.chan[3] + 2], k0[src.chan[0]:src.chan[0] + 2], k1[src.chan[0]:src.chan[0] + 2]
        out.append((float(sc[0]), float(sc[1]), *((t1 - t0) / src.duration).tolist()))
    return out


class Materials:
    """Material (namespace, name) -> record with its textures."""

    def __init__(self, root, shared):
        self.shared = shared
        self.files = {os.path.splitext(os.path.basename(f))[0]: f
                      for f in glob.glob(os.path.join(root, "materialinstances", "**", "*.igz"), recursive=True)}
        self.igz = {}
        self.ids = {}
        self.records = []
        self.textures = []  # image namespaces
        self.tex_ids = {}

    def texture(self, ns):
        if ns is None:
            return None
        if ns not in self.tex_ids:
            self.tex_ids[ns] = len(self.textures)
            self.textures.append(ns)
        return self.tex_ids[ns]

    def material(self, ns, name):
        key = (ns, name)
        if key in self.ids:
            return self.ids[key]
        rec = {"name": name, "effect": "?", "flags": 0, "alpha_ref": 0.5, "tex": [], "nrm": [], "emit": None, "colour": None}
        f = self.files.get(ns) or (self.shared.find_name(ns + ".igz") if ns else None)
        if f:
            if f not in self.igz:
                self.igz[f] = nf_igz.Igz(f)
            z = self.igz[f]
            m = z.named_objects().get(name)
            if m:
                hs = z.walk_handles(m)
                rec["effect"] = hs[0][0] if hs and hs[0][1] == "graphics_effect" else "?"
                images = [h[0] for h in hs if h[1] == "image"]
                rec["tex"] = [i for i in images if i.startswith("ColorMap,")]
                rec["nrm"] = [i for i in images if i.startswith("NormalMap,")]
                emit = [i for i in images if i.startswith("ColorMapHalfRes,") and "default_e" not in i]
                rec["emit"] = emit[0] if emit else None
                masks = [i for i in images if i.startswith("Composite3HalfRes,") and EMIT_COMPOSITE.search(i)]
                if not emit and masks:
                    rec["emit"], rec["emit2"] = masks[0], masks[1] if len(masks) > 1 else None
                    rec["flags"] |= MAT_EMIT_MASK
                for b in z.reachable(m, {"CAlphaClipBundle"}):
                    if z.u32(b + 0x18):
                        rec["flags"] |= MAT_ALPHA_CLIP
                        rec["alpha_ref"] = z.f32(b + 0x1C)
                rec["blend"] = blending_enabled(z, m)
                rec["colour"] = colour_value(z, m) if rec["effect"] == "Standard" else None
                if rec["effect"] == "UberShaderBlended":
                    c = shader_constants(z, m)
                    d, p = c.get("directionalBlendDirection"), c.get("directionalBlendParams")
                    if c.get("directionalBlend") == 1 and d and p:
                        rec["dirblend"] = (d[:3], p[0], p[1])  # second layer x saturate(dot(n, dir) x scale + bias)
                    rec["alpha_params"], rec["vert_alpha_height"] = c.get("alphaParams"), c.get("useVertAlphaAsHeight")
                    rec["layer2_colour"] = (c.get("blendStateParams1") or (1.0,))[0] != 0.0  # (0: a blank second layer, ads, grids)
                    scrolls = texture_scrolls(z, m)
                    if len(scrolls) == 1:
                        rec["uv2_scroll"] = scrolls[0]  # (only one: the second layer's; the first stays put)
                    elif len(scrolls) > 1:
                        rec["uv1_scroll"], rec["uv2_scroll"] = scrolls[0], scrolls[1]  # (VFX: both layers run)
                    rec["additive"] = c.get("additive") == 1
                elif rec["effect"] in ("Standard", "GBufferDecal", "Water", "FlowWater", "Unlit"):
                    scrolls = texture_scrolls(z, m)
                    if scrolls:
                        rec["uv1_scroll"] = scrolls[0]  # (several: colour, normal, emission maps alike; the first)
        e = rec["effect"]
        if e == "GBufferDecal":
            rec["flags"] |= MAT_DECAL
        elif e in ("Water", "FlowWater"):
            rec["flags"] |= (MAT_GLASS if WATER_AS_GLASS.search(name or "") else MAT_WATER | MAT_ICE if WATER_AS_ICE.search(name or "")
                             else MAT_WATER)
        elif e == "Unlit":
            rec["flags"] |= MAT_UNLIT
        elif e == "GrassOutdoor":
            rec["flags"] |= MAT_GRASS | MAT_ALPHA_CLIP
        if e == "UberShaderBlended" and len(rec["tex"]) >= 2 and rec.get("layer2_colour", True):
            rec["flags"] |= MAT_BLEND2
            if rec.get("dirblend"):
                rec["flags"] |= MAT_DIRBLEND
            if rec["flags"] & MAT_EMIT_MASK and not rec.get("emit2"):
                # one mask: the layer whose texture it was made for glows, not both
                mask = texture_stem(rec["emit"])
                if mask == texture_stem(rec["tex"][1]):
                    rec["flags"] |= MAT_EMIT_LAYER2
                elif mask == texture_stem(rec["tex"][0]):
                    rec["flags"] |= MAT_EMIT_LAYER1
        if rec.get("blend") and e not in ("GBufferDecal", "Water", "FlowWater", "GrassOutdoor") and not rec["flags"] & MAT_ALPHA_CLIP:
            rec["flags"] |= MAT_TRANSLUCENT  # signs, foam, cobwebs: see-through by texture alpha (x colour alpha)
            if re.search("glass", name or "", re.I):
                rec["flags"] |= MAT_GLASS  # (its texture is opaque, its colour alpha 0.3-1: drawn clear instead)
        if rec["nrm"] and not rec["flags"] & (MAT_GRASS | MAT_WATER | MAT_UNLIT):
            rec["flags"] |= MAT_NORMAL
        if rec.get("additive"):
            # additive VFX / overlays (N. Gin Labs' speed tunnel, Drive-Thru
            # Danger's floor lights under glass): light of their own, added at
            # ADDITIVE_COVER of their strength (see-through, dithered)
            rec["flags"] |= MAT_UNLIT | MAT_TRANSLUCENT
        if rec["emit"]:
            rec["flags"] |= MAT_EMIT
            if FLAT_GREY.search(rec["emit"]) and not rec["flags"] & MAT_EMIT_MASK:
                rec["flags"] |= MAT_EMIT_ALBEDO
        if (PAD_NAME.search(name or "") or any("turbo_pad" in t.lower() for t in rec["tex"])) and not re.search(r"_off\b|_off$", name or "", re.I):
            rec["flags"] |= MAT_TURBO_SUPER if re.search("super", name or "", re.I) else MAT_TURBO
        if e == "Ice" or ICE_NAME.search(name or "") or any(ICE_NAME.search(t) for t in rec["tex"][:1]):
            rec["flags"] |= MAT_ICE
        self.ids[key] = len(self.records)
        self.records.append(rec)
        return self.ids[key]

    def uncoloured(self, mid):
        """material for a draw of a two-layer material that has no vertex colours
        (white, as Nitro-Fueled has them: the second layer, Mystery Caves' lava
        falls' flowing lava). When the vertex alpha is not what blends it
        (useVertAlphaAsHeight 0): the second layer where the first layer's alpha
        (alphaParams x, y) is low (Oxide Station's floor lights between its
        panels); directional blends (snow on tops) as they are."""
        rec = self.records[mid]
        if (not rec["flags"] & MAT_BLEND2 or rec["flags"] & MAT_DIRBLEND or rec.get("alpha_params") is None
                or rec.get("vert_alpha_height") != 0):
            return mid
        x, y = rec["alpha_params"][:2]
        key = ("uncoloured", mid)
        if key not in self.ids:
            self.ids[key] = len(self.records)
            self.records.append(dict(rec, flags=rec["flags"] | MAT_BLEND_TEX, tex_map=(float(x), float(y))))
        return self.ids[key]

    def group(self, mid):
        """0 opaque, 1 alpha tested / translucent / water / glass, 2 grass, 3 decal"""
        f = self.records[mid]["flags"]
        return 3 if f & MAT_DECAL else 2 if f & MAT_GRASS else 1 if f & (MAT_ALPHA_CLIP | MAT_WATER | MAT_TRANSLUCENT | MAT_GLASS) else 0


def oct_encode(n):
    """unit vectors (N, 3) -> octahedral snorm16 x2 (int32 pairs, -32767..32767)"""
    n = n / np.maximum(np.abs(n).sum(1, keepdims=True), 1e-12)
    xy = n[:, :2].copy()
    neg = n[:, 2] < 0
    xy[neg] = (1.0 - np.abs(xy[neg][:, ::-1])) * np.where(xy[neg] >= 0, 1.0, -1.0)
    return np.clip(np.round(xy * 32767.0), -32767, 32767).astype(np.int32)


def pack16(a, b):
    return ((a.astype(np.int64) & 0xFFFF) | ((b.astype(np.int64) & 0xFFFF) << 16)).astype(np.uint32)


def unit(v):
    return v / np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-12)


def bitangent_signs(p, uv, nrm, tan, idx):
    """per vertex: does the uv v axis run along cross(n, t) (+1) or against (-1)"""
    a, b, c = p[idx[:, 0]], p[idx[:, 1]], p[idx[:, 2]]
    ta, tb, tc = uv[idx[:, 0]], uv[idx[:, 1]], uv[idx[:, 2]]
    e1, e2, d1, d2 = b - a, c - a, tb - ta, tc - ta
    det = d1[:, 0] * d2[:, 1] - d1[:, 1] * d2[:, 0]
    bt = (e2 * d1[:, :1] - e1 * d2[:, :1]) * np.sign(det)[:, None]  # dP/dv direction
    acc = np.zeros(len(p))
    for k in range(3):
        v = idx[:, k]
        s = np.einsum("ij,ij->i", np.cross(nrm[v], tan[v]), bt)
        np.add.at(acc, v, s)
    return np.where(acc >= 0.0, 1, -1)


# ---- NF lighting: point lights, wind ------------------------------------------

def level_lights(ldir, pm, pt):
    """Point lights of the level's lighting / art maps: (pos PS1, radius, colour)."""
    out = []
    for p in sorted(glob.glob(os.path.join(ldir, "*.igz"))):
        if not map_wanted(os.path.basename(p)[:-4], os.path.basename(ldir)):
            continue
        z = nf_igz.Igz(p)
        if "CPointLightComponentData" not in z.metatypes:
            continue
        for e in z.of_type("igEntity"):
            for v in entity_components(z, e):
                if z.obj_type(v) != "CPointLightComponentData":
                    continue
                col = np.array(z.vec(v + 0x10, 3)) * z.f32(v + 0x20)
                radius = z.f32(v + 0x24)
                _, pos = entity_transform(z, e)
                out.append((pos @ pm.T + pt, radius / NF_SCALE, col))
    return out


def texture_small(t, size=32):
    """a texture's rgb (0..1, as stored) at size x size: its mip nearest above, box filtered"""
    lv = 0
    while lv + 1 < t.levels and min(t.width, t.height) >> (lv + 1) >= size:
        lv += 1
    px = t.rgba(lv)[..., :3].astype(np.float64) / 255.0
    h, w = px.shape[:2]
    fy, fx = max(h // size, 1), max(w // size, 1)
    px = px[:h // fy * fy, :w // fx * fx]
    return px.reshape(h // fy, fy, w // fx, fx, 3).mean((1, 3))


def texture_alpha(t, size=32):
    """a texture's alpha (0..1) at about size x size (its mip nearest above)"""
    lv = 0
    while lv + 1 < t.levels and min(t.width, t.height) >> (lv + 1) >= size:
        lv += 1
    return t.rgba(lv)[..., 3].astype(np.float64) / 255.0


def emission_mean(r, tex_data):
    """mean linear emission of a material (as common.glsl draws it): emission
    map x 2, or colour x glow mask x EMIT_MASK_GAIN texel by texel (lava's
    glow sits in its bright cracks)"""
    if r["flags"] & MAT_EMIT_MASK:
        # (two-layer materials: each layer's colour under its own mask, as blended)
        layers = [(r["tex_ids"][1 if r["flags"] & MAT_EMIT_LAYER2 and len(r["tex_ids"]) > 1 else 0], r["emit_id"])]
        if len(r["tex_ids"]) > 1 and r.get("emit2_id") is not None and tex_data[r["tex_ids"][1]] is not None and tex_data[r["emit2_id"]] is not None:
            layers.append((r["tex_ids"][1], r["emit2_id"]))
        out = []
        for ci, mi in layers:
            c, m = texture_small(tex_data[ci]), texture_small(tex_data[mi])
            if r["flags"] & MAT_EMIT_LAYER2 and r.get("uv2_scroll") and ci == r["tex_ids"][-1]:
                m = np.maximum(m, RUNNING_LIGHT_BASE)  # (common.glsl remRunningLight)
            n = min(c.shape[0], m.shape[0]), min(c.shape[1], m.shape[1])
            out.append(((c[:n[0], :n[1]] ** 2.2) * m[:n[0], :n[1], :1]).reshape(-1, 3).mean(0))
        return np.mean(out, axis=0) * EMIT_MASK_GAIN
    if r["flags"] & MAT_EMIT_ALBEDO:
        # (common.glsl: the colour x the grey's level)
        level = float(FLAT_GREY.search(r["emit"]).group(2)) / 100.0
        if not r["tex_ids"] or tex_data[r["tex_ids"][0]] is None:
            return np.zeros(3)
        tint = np.clip(np.array(r["colour"][:3]), 0.0, 1.0) ** 2.2 if r.get("colour") else 1.0
        return (texture_small(tex_data[r["tex_ids"][0]]) ** 2.2).reshape(-1, 3).mean(0) * level * tint
    return (texture_small(tex_data[r["emit_id"]]) ** 2.2).reshape(-1, 3).mean(0) * 2.0


def glow_lights(pos, tris, tmat, mats, tex_data, missing, alpha=None):
    """Point lights for glowing surfaces (lava, lamps, screens): their emitted
    power (mean emission x area, half each way) per GLOW_CELL cluster, the
    strongest GLOW_MAX; as the PS1 levels' glow lights (ctr_rtx.c
    addGlowLights): falloff from the cluster's spread, at least 200, so a
    bigger glow is a stronger light, not a dimmer wider one. As level_lights'
    (pos, radius, colour) plus kind 1: the renderer makes falloff 0.35 x
    radius, colour x 2. alpha: vertex alpha (0-255), how much of a blended
    material's second layer shows (a one-layer glow mask's share)."""
    emit = np.zeros((len(mats.records), 3))
    for i, r in enumerate(mats.records):
        if not r["flags"] & MAT_EMIT or r["emit_id"] in missing:
            continue
        if r["flags"] & MAT_EMIT_MASK and (not r["tex_ids"] or r["tex_ids"][0] in missing):
            continue
        emit[i] = emission_mean(r, tex_data) * (GLOW_MASK_BOOST if r["flags"] & MAT_EMIT_MASK else 1.0)
    sel = np.flatnonzero(emit[tmat].max(1) > 0.02)
    if len(sel) == 0:
        return []
    p = pos[tris[sel]].astype(np.float64)
    fn = np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0])
    area = 0.5 * np.linalg.norm(fn, axis=1)
    keep = area >= 1.0
    p, fn, area, sel = p[keep], fn[keep], area[keep], sel[keep]
    centre = p.mean(1)
    nrm = fn / (2 * area[:, None])
    power = emit[tmat[sel]] * (area * 0.5)[:, None]
    if alpha is not None:
        # (common.glsl remAlbedo: second layer where the vertex alpha is over half)
        a = alpha[tris[sel]].mean(1) / 255.0
        share = np.clip((a - 0.4) / 0.2, 0.0, 1.0)
        fl = np.array([r["flags"] for r in mats.records])[tmat[sel]]
        # (MAT_BLEND_TEX: where the first layer's alpha says, over its texture)
        tex_share = np.ones(len(mats.records))
        for i, r in enumerate(mats.records):
            if r["flags"] & MAT_BLEND_TEX and r["tex_ids"] and r["tex_ids"][0] not in missing:
                h = np.clip(texture_alpha(tex_data[r["tex_ids"][0]]) * r["tex_map"][0] + r["tex_map"][1], 0.0, 1.0)
                tex_share[i] = float(np.clip((0.6 - h) / 0.2, 0.0, 1.0).mean())
        share = np.where(fl & MAT_BLEND_TEX, tex_share[tmat[sel]], share)
        power *= np.where(fl & MAT_EMIT_LAYER2, share, np.where(fl & MAT_EMIT_LAYER1, 1.0 - share, 1.0))[:, None]
    w = power @ np.array([0.299, 0.587, 0.114]) + 1e-9
    cell = np.floor(centre / GLOW_CELL).astype(np.int64)
    _, cid = np.unique(cell, axis=0, return_inverse=True)
    cid = cid.ravel()
    nc = cid.max() + 1
    sw = np.bincount(cid, w, nc)
    # (lava: the renderer lights it as the stock track does, from the PS1 level's lava)
    lava = np.array([any("lava" in texture_stem(t) for t in r["tex"][:2] + [r.get("emit") or ""]) for r in mats.records])
    lava_share = np.bincount(cid, w * lava[tmat[sel]], nc) / sw
    sp = np.stack([np.bincount(cid, power[:, k], nc) for k in range(3)], 1)
    c = np.stack([np.bincount(cid, centre[:, k] * w, nc) for k in range(3)], 1) / sw[:, None]
    spread = np.sqrt(np.maximum(np.bincount(cid, (np.linalg.norm(centre - c[cid], axis=1) ** 2) * w, nc) / sw, 0.0))
    n = np.stack([np.bincount(cid, nrm[:, k] * w, nc) for k in range(3)], 1)
    n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-9)
    out = []
    for k in np.argsort(-sw)[:GLOW_MAX]:
        falloff = max(200.0, float(spread[k]) * 1.5)
        col = sp[k] / (math.pi * falloff * falloff) * GLOW_GAIN
        if col @ np.array([0.299, 0.587, 0.114]) < 0.01:
            continue
        out.append((c[k] + n[k] * 30.0, falloff / 0.35, col / 2.0, 2 if lava_share[k] > 0.5 else 1))
    used = set(np.unique(tmat).tolist())
    one = [sum(1 for i in used if mats.records[i]["flags"] & f) for f in (MAT_EMIT_LAYER1, MAT_EMIT_LAYER2)]
    print(f"  glowing surfaces: {len(sel)} triangles -> {len(out)} lights ({sum(1 for l in out if l[3] == 2)} lava); one-layer glow masks: {one[0]} first layer, {one[1]} second")
    return out


def level_wind(level_file):
    """CGlobalWindData of the level (CWorldData_globalWind): strength, gust frequency, gust min / max."""
    z = nf_igz.Igz(level_file)
    for a in z.of_type("CGlobalWindData"):
        return [z.f32(a + 0x10), z.f32(a + 0x1C), z.f32(a + 0x2C), z.f32(a + 0x30)]
    return [0.25, 0.1, 0.0, 0.6]


# ---- skybox -> cube map --------------------------------------------------------

def skybox_names(ldir):
    names = []
    for p in sorted(glob.glob(os.path.join(ldir, "*_Lighting*.igz"))):
        z = nf_igz.Igz(p)
        names += [s for s in z.strings if "skybox" in s.lower() and "\\" in s and s not in names]
    # base first, then overlays (stars, planets, clouds) composited by alpha
    order = lambda s: (0 if s.lower().endswith("_skybox") else 1 if "star" in s.lower() else 2 if "planet" in s.lower() else 3)
    return sorted(names, key=order)


def cube_dirs(size):
    """unit directions of every texel of the 6 cube faces (Vulkan order, PS1 axes)"""
    c = (np.arange(size) + 0.5) / size * 2.0 - 1.0
    u, v = np.meshgrid(c, c)  # v down the face
    faces = [(1, -v, -u), (-1, -v, u), (u, 1, v), (u, -1, -v), (u, -v, 1), (-u, -v, -1)]
    out = []
    for x, y, z in faces:
        d = np.stack([np.broadcast_to(np.asarray(a, float), u.shape) for a in (x, y, z)], -1)
        out.append(unit(d.reshape(-1, 3)))
    return out


def sample_rgba(img, uv):
    """bilinear RGBA (0..1) of img (H, W, 4 uint8) at repeating uv (N, 2)"""
    h, w = img.shape[:2]
    x = (uv[:, 0] % 1.0) * w - 0.5
    y = (uv[:, 1] % 1.0) * h - 0.5
    x0, y0 = np.floor(x).astype(int), np.floor(y).astype(int)
    fx, fy = (x - x0)[:, None], (y - y0)[:, None]
    f = img.astype(np.float32) / 255.0
    g = lambda yy, xx: f[yy % h, xx % w]
    return (g(y0, x0) * (1 - fx) * (1 - fy) + g(y0, x0 + 1) * fx * (1 - fy) +
            g(y0 + 1, x0) * (1 - fx) * fy + g(y0 + 1, x0 + 1) * fx * fy)


def skybox_cube(ldir, shared, mats, pm, size=SKY_SIZE):
    """6 BGRA8 faces of the level's skybox models seen from their centre, or None."""
    names = skybox_names(ldir)
    if not names:
        return None
    dirs = cube_dirs(size)
    faces = [np.zeros((len(d), 4), np.float32) for d in dirs]
    covered = [np.zeros(len(d), bool) for d in dirs]
    for layer, nm in enumerate(names):
        f = shared.find("sky/" + nm.replace("\\", "/").lower() + ".igz")
        if not f:
            print(f"  skybox {nm}: not found")
            continue
        draws = load_model(f)
        for fi, dps in enumerate(dirs):
            dn = dps @ pm  # PS1 direction -> NF (the placement's rotation; any scale)
            best = np.full(len(dn), np.inf)
            col = np.zeros((len(dn), 4), np.float32)
            for d in draws:
                rec = mats.records[mats.material(d["material_ns"], d["material"])]
                if not rec["tex"]:
                    continue
                tf = shared.find("textures/" + rec["tex"][0] + ".igz")
                if not tf:
                    continue
                img = nf_tex.Texture(tf)
                if not img.mips:
                    print(f"  skybox {nm}: texture {rec['tex'][0]} has no image data, skipped")
                    continue
                lvl = min(len(img.mips) - 1, max(0, int(math.log2(max(img.width / size, 1)))))
                rgba = img.rgba(lvl)
                p, uv = d["pos"].astype(np.float64), d["uv"].astype(np.float64)
                for t in d["index"]:
                    a, b, c = p[t[0]], p[t[1]], p[t[2]]
                    e1, e2 = b - a, c - a
                    pv = np.cross(dn, e2)
                    det = pv @ e1
                    ok = np.abs(det) > 1e-9
                    inv = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
                    s = -a
                    uu = (pv @ s) * inv
                    q = np.cross(s, e1)
                    vv = (dn @ q) * inv
                    tt = (q @ e2) * inv
                    hit = ok & (uu >= 0) & (vv >= 0) & (uu + vv <= 1) & (tt > 0) & (tt < best)
                    if not hit.any():
                        continue
                    best[hit] = tt[hit]
                    w = np.stack([1 - uu[hit] - vv[hit], uu[hit], vv[hit]], 1)
                    tuv = w[:, :1] * uv[t[0]] + w[:, 1:2] * uv[t[1]] + w[:, 2:3] * uv[t[2]]
                    col[hit] = sample_rgba(rgba, tuv)
            hit = np.isfinite(best)
            if layer == 0:
                faces[fi][hit] = col[hit]
                faces[fi][hit, 3] = 1.0
                covered[fi] |= hit
            else:
                a = col[hit, 3:4]
                faces[fi][hit, :3] = faces[fi][hit, :3] * (1 - a) + col[hit, :3] * a
    # below the box's open bottom: the colour at the horizon below each direction
    allc = np.concatenate(covered)
    if not allc.any():
        return None
    alld = np.concatenate(dirs)
    allf = np.concatenate(faces)
    hor = np.abs(alld[:, 1]) < 0.08
    horc = allf[hor & allc, :3].mean(0) if (hor & allc).any() else allf[allc, :3].mean(0)
    out = []
    for f, cov in zip(faces, covered):
        f[~cov, :3] = horc
        bgra = (np.clip(f[:, [2, 1, 0]], 0, 1) * 255 + 0.5).astype(np.uint8)
        out.append(np.concatenate([bgra, np.full((len(f), 1), 255, np.uint8)], 1).tobytes())
    return b"".join(out)


# ---- kart floor ----------------------------------------------------------------

def raster_heights(tri, cell, minx, minz, dimx, dimz):
    """(cell index, height) samples of triangles (N, 3, 3), every half cell"""
    cells, heights = [], []
    edge = np.max(np.linalg.norm(tri - np.roll(tri, 1, axis=1), axis=2), axis=1)
    steps = np.clip(np.ceil(edge / (cell * 0.5)).astype(int), 1, 64)
    for n in np.unique(steps):
        t = tri[steps == n]
        i, j = np.meshgrid(np.arange(n + 1), np.arange(n + 1))
        keep = (i + j) <= n
        bw = np.stack([1 - (i + j)[keep] / n, i[keep] / n, j[keep] / n], 1)  # (S, 3)
        pts = np.einsum("sk,nkd->nsd", bw, t).reshape(-1, 3)
        cx = ((pts[:, 0] - minx) / cell).astype(int)
        cz = ((pts[:, 2] - minz) / cell).astype(int)
        ok = (cx >= 0) & (cz >= 0) & (cx < dimx) & (cz < dimz)
        cells.append(cz[ok] * dimx + cx[ok])
        heights.append(pts[ok, 1])
    return (np.concatenate(cells), np.concatenate(heights)) if cells else (np.zeros(0, int), np.zeros(0))


def floor_field(ps1_lev, nf_tris, layers=4):
    """Multi-layer height field of the NF drivable surface over the PS1 floor
    (no PS1 level, a native track: all of the NF level's ground)."""
    if ps1_lev is None:
        n = np.cross(nf_tris[:, 1] - nf_tris[:, 0], nf_tris[:, 2] - nf_tris[:, 0])
        pt = nf_tris[np.abs(n[:, 1]) > 0.5 * np.linalg.norm(n, axis=1)]
        pt = pt[np.all(np.abs(pt) < 32000, axis=(1, 2))]
    else:
        import rt_bake
        mesh = rt_bake.Mesh(ps1_lev)
        pos = mesh.verts["pos"].astype(np.float64)
        pt = pos[mesh.triangles()]
        fn = np.cross(pt[:, 2] - pt[:, 0], pt[:, 1] - pt[:, 0])  # CTR winding: ground faces +Y
        up = np.abs(fn[:, 1]) > 0.5 * np.linalg.norm(fn, axis=1)
        pt = pt[up]
    lo, hi = pt.reshape(-1, 3).min(0), pt.reshape(-1, 3).max(0)
    cell = FLOOR_CELL
    dimx, dimz = int((hi[0] - lo[0]) / cell) + 1, int((hi[2] - lo[2]) / cell) + 1
    if ps1_lev is not None:
        pc, ph = raster_heights(pt, cell, lo[0], lo[2], dimx, dimz)
    n = np.cross(nf_tris[:, 1] - nf_tris[:, 0], nf_tris[:, 2] - nf_tris[:, 0])
    nf = nf_tris[np.abs(n[:, 1]) > 0.5 * np.linalg.norm(n, axis=1)]
    inb = ((nf[:, :, 0].max(1) >= lo[0]) & (nf[:, :, 0].min(1) <= hi[0]) & (nf[:, :, 2].max(1) >= lo[2]) & (nf[:, :, 2].min(1) <= hi[2]))
    nc, nh = raster_heights(nf[inb], cell, lo[0], lo[2], dimx, dimz)
    # keep NF heights within the PS1 floor's height range in that cell (the race
    # surface, not cliffs or roofs above it)
    if ps1_lev is not None:
        lo_h = np.full(dimx * dimz, np.inf)
        hi_h = np.full(dimx * dimz, -np.inf)
        np.minimum.at(lo_h, pc, ph)
        np.maximum.at(hi_h, pc, ph)
        near = (nh > lo_h[nc] - 150.0) & (nh < hi_h[nc] + 150.0)
        nc, nh = nc[near], nh[near]
    out = np.full((dimz * dimx, layers), -32768, np.int16)
    order = np.lexsort((-nh, nc))  # by cell, highest first
    nc, nh = nc[order], nh[order]
    starts = np.flatnonzero(np.r_[True, nc[1:] != nc[:-1]])
    ends = np.r_[starts[1:], len(nc)]
    for s, e in zip(starts, ends):
        kept = []
        for h in nh[s:e]:
            if all(abs(h - k) > 24.0 for k in kept):
                kept.append(h)  # surfaces at least a kart's height apart: the top of each
                if len(kept) == layers:
                    break
        out[nc[s], :len(kept)] = np.clip(np.round(kept), -32767, 32767)
    hdr = struct.pack("<4sI3f3I", b"RTXF", 1, lo[0], lo[2], cell, dimx, dimz, layers)
    return hdr + out.tobytes(), int((out[:, 0] != -32768).sum()), dimx * dimz


# ---- build ---------------------------------------------------------------------

def build(slot, root, out_dir, archives, hide):
    """slot: the track id (stock slot, or a hosted track's id)"""
    pm, pt, _ = placement(slot, root)
    ldir = level_dir(root)
    level = os.path.basename(ldir)
    shared = Shared(archives, str(WORK / "shared"))
    ents = level_entities(root)
    mats = Materials(root, shared)
    models = {}
    parts = []  # per draw: (kind, dict)
    nodes = []  # animated / proxy parts (KIND_ANIM, KIND_PROXY draws), in vertex order within their kind

    def add_draw(d, M, T, kind=None, proxy=0, drop=(), speed=1.0):
        """one draw call, model space -> PS1 world by p @ M.T + T (proxies: -> the
        PS1 model's own space, the game's instance matrix does the rest)"""
        n = len(d["pos"])
        if n == 0 or len(d["index"]) == 0:
            return
        mid = mats.material(d["material_ns"], d["material"])
        if d["color"] is None:
            mid = mats.uncoloured(mid)
        rec = mats.records[mid]
        if (rec["flags"] & MAT_DECAL) and (not rec["tex"] or "!default_c`" in rec["tex"][0]):
            return  # decals that change only normals / roughness (blank colour map)
        if rec["effect"] in SKIP_EFFECTS or SKIP_MATERIALS.search(d["material"] or "") or d["material"] in drop or (kind == KIND_PROXY and rec["flags"] & MAT_DECAL):
            return
        if not np.isfinite(d["pos"]).all():
            return
        grass = bool(rec["flags"] & MAT_GRASS)
        if kind is None:
            kind = KIND_ANIM if d["anim"] is not None and not rec["flags"] & (MAT_DECAL | MAT_GRASS) else KIND_GRASS if grass else KIND_STATIC
        p = d["pos"].astype(np.float64) @ M.T + T
        nrm_m = np.linalg.inv(M)
        if d["normal"] is not None:
            nn = unit(d["normal"].astype(np.float64) @ nrm_m)
        else:
            nn = np.tile([0.0, 1.0, 0.0], (n, 1))
        uv = np.nan_to_num(d["uv"].astype(np.float64), nan=0.0, posinf=0.0, neginf=0.0) if d["uv"] is not None else np.zeros((n, 2))
        col = d["color"] if d["color"] is not None else np.full((n, 4), 255, np.uint8)
        if rec["colour"] and not grass and any(abs(x - 1.0) > 1e-3 for x in rec["colour"][:3]):
            # (a Standard material's colour tints its colour map: Nina's
            # Nightmare's blue lamp glass over a white map)
            col = col.copy()
            col[:, :3] = np.clip(np.round(col[:, :3] * np.clip(np.array(rec["colour"][:3]), 0.0, 1.0)), 0, 255).astype(np.uint8)
        sign = None
        if grass and kind == KIND_GRASS:
            # vertex colour is the wind weight (palms: 0 at the base); none
            # (all white): the height within the draw
            g = col[:, :3].mean(1) / 255.0
            if g.min() > 0.99:
                z = d["pos"][:, 2]
                g = (z - z.min()) / max(float(np.ptp(z)), 1e-6)
            height = float(np.ptp(p[:, 1]))
            sway = g * np.clip(0.04 * height, 1.0, 40.0)
            phase = ((p[:, 0] * 0.0021 + p[:, 2] * 0.0017) % 1.0)
            tw = pack16(np.clip(np.round(sway * 1024), 0, 65535).astype(np.int64), np.round(phase * 65535).astype(np.int64))
            col = np.full((n, 4), 255, np.uint8)
        elif d["tangent"] is not None and rec["flags"] & MAT_NORMAL:
            tt = unit(d["tangent"].astype(np.float64) @ M.T)
            tt = unit(tt - nn * np.einsum("ij,ij->i", tt, nn)[:, None])
            sign = bitangent_signs(p, uv, nn, tt, d["index"])
            q = oct_encode(tt)
            q[:, 1] = (q[:, 1] & ~1) | (sign > 0)
            tw = pack16(q[:, 0], q[:, 1])
        else:
            tw = np.zeros(n, np.uint32)
        if rec["flags"] & MAT_DECAL:
            p = p + nn * 0.6  # just over the surface it decorates (common.glsl remDecals)
        q = oct_encode(nn)
        rest = {"pos": p.astype(np.float32), "uv": uv.astype(np.float32), "n": pack16(q[:, 0], q[:, 1]),
                "c": col, "t": tw, "idx": d["index"].astype(np.int64), "mat": mid}
        if kind in (KIND_ANIM, KIND_PROXY):
            # node space vertices + what moves them: G (model -> world / PS1
            # model space, 3x4) after the node chain K(t) (keys, 3x4 each)
            if d["anim"] is not None:
                period, keys, raw, rn, rt = d["anim"]
            else:
                period, keys, raw, rn, rt = 0.0, np.eye(4)[None], d["pos"].astype(np.float64), d["normal"], d["tangent"]
            qn = oct_encode(unit(rn.astype(np.float64)) if rn is not None else np.tile([0.0, 0.0, 1.0], (n, 1)))
            if rt is not None and sign is not None:
                qt = oct_encode(unit(rt.astype(np.float64)))
                qt[:, 1] = (qt[:, 1] & ~1) | (sign > 0)
                tw_raw = pack16(qt[:, 0], qt[:, 1])
            else:
                tw_raw = rest["t"]
            nodes.append({"kind": kind, "G": np.concatenate([M, T[:, None]], 1), "keys": keys, "period": period / speed, "proxy": proxy,
                          "raw": raw.astype(np.float32), "rn": pack16(qn[:, 0], qn[:, 1]), "rt": tw_raw})
            rest["node"] = len(nodes) - 1
        parts.append((kind, rest))

    for _, ref, m, t in ents:
        f = model_file(root, ref)
        if not os.path.exists(f):
            f = shared.find(os.path.relpath(f, root)) or f
        if not os.path.exists(f):
            continue
        if abs(np.linalg.det(m)) < 1e-9:
            continue  # (scaled to nothing)
        if f not in models:
            models[f] = load_model(f)
        name = os.path.basename(ref.replace("\\", "/"))
        speed = next((v for k, v in nf_tracks.ANIM_SPEED.get(slot, {}).items() if name.startswith(k)), 1.0)
        for d in models[f]:
            add_draw(d, pm @ m, pm @ t + pt, speed=speed)

    # proxies: moving PS1 models drawn as NF ones, one copy per instance the game reports
    proxy_slots = []
    for model_id, names, drop, scale, light in nf_tracks.PROXIES.get(slot, []):
        for copy, name in enumerate(names):
            hits = glob.glob(os.path.join(root, "models", "**", name + ".igz"), recursive=True)
            if not hits:
                print(f"  proxy model {name} not found")
                continue
            proxy_slots.append((model_id, copy, scale, light))
            for d in load_model(hits[0]):
                add_draw(d, PROXY_FROM_NF * scale, np.zeros(3), KIND_PROXY, len(proxy_slots), drop)

    # vertices: static, grass (the wind pass), animated nodes, proxies (theirs
    # moved by node / instance: the dynamic range)
    parts.sort(key=lambda gp: gp[0])
    vdt = np.dtype([("pos", "<f4", 3), ("uv", "<f4", 2), ("n", "<u4"), ("c", "u1", 4), ("t", "<u4")])
    nverts = sum(len(d["pos"]) for _, d in parts)
    vert = np.zeros(nverts, vdt)
    tri_l, mat_l, kind_l = [], [], []
    base = 0
    first = {}
    node_order = []
    for kind, d in parts:
        n = len(d["pos"])
        first.setdefault(kind, base)
        sl = slice(base, base + n)
        vert["pos"][sl], vert["uv"][sl], vert["n"][sl], vert["c"][sl], vert["t"][sl] = d["pos"], d["uv"], d["n"], d["c"], d["t"]
        tri_l.append(d["idx"] + base)
        mat_l.append(np.full(len(d["idx"]), d["mat"], np.int64))
        kind_l.append(np.full(len(d["idx"]), kind, np.int64))
        if "node" in d:
            node_order.append((d["node"], base, n))
        base += n
    grass_vert = min([first[k] for k in (KIND_GRASS, KIND_ANIM, KIND_PROXY) if k in first], default=nverts)
    anim_vert = min([first[k] for k in (KIND_ANIM, KIND_PROXY) if k in first], default=nverts)
    tris = np.concatenate(tri_l)
    tmat = np.concatenate(mat_l)
    tkind = np.concatenate(kind_l)

    # textures: colour layers, normal maps, emission of every material used
    for rec in mats.records:
        rec["tex_ids"] = [mats.texture(ns) for ns in rec["tex"][:2]]
        rec["nrm_ids"] = [mats.texture(ns) for ns in rec["nrm"][:2]] if rec["flags"] & MAT_NORMAL else []
        rec["emit_id"] = mats.texture(rec["emit"])
        rec["emit2_id"] = mats.texture(rec.get("emit2"))
    tex_data = []
    for ns in mats.textures:
        f = os.path.join(root, "textures", ns + ".igz")
        if not os.path.exists(f):
            f = shared.find("textures/" + ns + ".igz") or f
        try:
            tex_data.append(nf_tex.Texture(f))
        except (OSError, ValueError, StopIteration) as e:
            print(f"  texture {ns}: {e}")
            tex_data.append(None)
    missing = {i for i, t in enumerate(tex_data) if t is None}

    groups = np.array([mats.group(i) for i in range(len(mats.records))])
    tgroup = np.where(tkind == KIND_ANIM, 4, np.where(tkind == KIND_PROXY, 5, groups[tmat]))
    order = np.argsort(tgroup, kind="stable")
    tris, tmat, tgroup = tris[order], tmat[order], tgroup[order]
    gstart = [int(np.searchsorted(tgroup, g, "left")) for g in (1, 2, 3, 4, 5)] + [len(tris)]

    def tid(lst, k):
        i = lst[k] if len(lst) > k else None
        return 0xFFFFFFFF if i is None or i in missing else i

    mat_bytes = b""
    for r in mats.records:
        flags = r["flags"]
        if tid(r["nrm_ids"], 0) == 0xFFFFFFFF:
            flags &= ~MAT_NORMAL
        if tid([r["emit_id"]], 0) == 0xFFFFFFFF:
            flags &= ~(MAT_EMIT | MAT_EMIT_MASK)
        tint = (1.0, 1.0, 1.0, ADDITIVE_COVER if r.get("additive") else min(max(r["colour"][3], 0.0), 1.0) if r["colour"] else 1.0)
        if flags & MAT_TRANSLUCENT:
            flags &= ~MAT_DIRBLEND  # (tint.w is its opacity)
        if flags & MAT_DIRBLEND:
            # NF world direction -> PS1 axes (the placement's linear part)
            d, scale, bias = r["dirblend"]
            v = pm @ np.array(d, np.float64)
            v = v / max(np.linalg.norm(v), 1e-9) * scale
            tint = (float(v[0]), float(v[1]), float(v[2]), float(bias))
        elif flags & MAT_BLEND_TEX:
            tint = (*r["tex_map"], 0.0, tint[3])
        tint = list(struct.unpack("<4I", struct.pack("<4f", *tint)))
        half2 = lambda a, b: int(np.array([a, b], np.float16).view(np.uint16).astype(np.uint32) @ [1, 1 << 16])  # noqa: E731
        if (flags & MAT_BLEND2 and r.get("uv2_scroll") and not flags & (MAT_TRANSLUCENT | MAT_DIRBLEND)
                and (flags & MAT_EMIT_LAYER2 or (flags & MAT_EMIT_MASK and r.get("emit2")) or r.get("uv1_scroll"))):
            # a second layer running over the first: a glowing one (Oxide Station's
            # and Drive-Thru Danger's floor lights, Mystery Caves' lava flows), or
            # both layers of a VFX (N. Gin Labs' tunnel); tint.zw: unused by these
            flags |= MAT_UV_SCROLL2
            tint[2], tint[3] = half2(*r["uv2_scroll"][:2]), half2(*r["uv2_scroll"][2:])
        if r.get("uv1_scroll") and not flags & (MAT_DIRBLEND | MAT_BLEND_TEX):
            # the first layer running (waterfalls; tint.xy: unused by these)
            flags |= MAT_UV_SCROLL1
            tint[0], tint[1] = half2(*r["uv1_scroll"][:2]), half2(*r["uv1_scroll"][2:])
        mat_bytes += struct.pack("<6IfI4I", tid(r["tex_ids"], 0), tid(r["tex_ids"], 1), tid([r["emit_id"]], 0), flags,
                                 tid(r["nrm_ids"], 0), tid(r["nrm_ids"], 1), r["alpha_ref"], tid([r["emit2_id"]], 0), *tint)

    lights = level_lights(ldir, pm, pt) + glow_lights(vert["pos"], tris[tgroup < 4], tmat[tgroup < 4], mats, tex_data, missing, vert["c"][:, 3])
    light_bytes = b"".join(struct.pack("<8f", *l[0], l[1], *l[2], l[3] if len(l) > 3 else 0) for l in lights)
    sky = skybox_cube(ldir, shared, mats, pm)
    wind = level_wind(os.path.join(ldir, level + ".igz"))

    tri = np.zeros(len(tris), np.dtype([("v", "<u4", 3), ("m", "<u4")]))
    tri["v"], tri["m"] = tris, tmat

    # animation (u32 words): numParts, keys at, vertices at, vertices, proxy slots
    # at, slots, 0, 0; parts (16: G 3x4, first key, keys, period, proxy slot + 1);
    # keys (12: 3x4); vertices from anim_vert (6: node space xyz, part, normal,
    # tangent); proxy slots (12: model id, copy, light xyz, rgb, falloff, size, 0, 0)
    part_w, key_w, vert_w = [], [], []
    nkeys = 0
    key_ids = {}
    for pi, (ni, vbase, n) in enumerate(node_order):
        nd = nodes[ni]
        kid = id(nd["keys"])
        if kid not in key_ids:  # (instances of one model share its draws' keys)
            key_ids[kid] = (nkeys, len(nd["keys"]))
            kc = np.transpose(nd["keys"][:, :, :3], (0, 2, 1))  # (n, 3, 4): column form [R | t]
            key_w.append(kc.astype(np.float32).reshape(-1).view(np.uint32))
            nkeys += len(nd["keys"])
        k0, kn = key_ids[kid]
        part_w.append(np.concatenate([nd["G"].astype(np.float32).reshape(-1).view(np.uint32),
                                      np.array([k0, kn], np.uint32), np.array([nd["period"]], np.float32).view(np.uint32),
                                      np.array([nd["proxy"]], np.uint32)]))
        w = np.zeros((n, 6), np.uint32)
        w[:, :3] = nd["raw"].view(np.uint32)
        w[:, 3] = pi
        w[:, 4] = nd["rn"]
        w[:, 5] = nd["rt"]
        vert_w.append(w.reshape(-1))
    slot_w = [np.concatenate([np.array([mid, cp], np.int32).view(np.uint32),
                              np.array(list(PROXY_FROM_NF * sc @ np.array(lt[0])) + list(lt[1]) + [lt[2], lt[3], 0, 0] if lt else [0] * 10, np.float32).view(np.uint32)])
              for mid, cp, sc, lt in proxy_slots]
    n_parts, n_anim_verts = len(part_w), sum(len(w) // 6 for w in vert_w)
    keys_at = 8 + 16 * n_parts
    verts_at = keys_at + 12 * nkeys
    slots_at = verts_at + 6 * n_anim_verts
    words = [np.array([n_parts, keys_at, verts_at, n_anim_verts, slots_at, len(slot_w), 0, 0], np.uint32)] + part_w + key_w + vert_w + slot_w
    assert all(w.dtype == np.uint32 for w in words)  # (floats bit cast, never converted)
    anim = np.concatenate(words) if n_parts else np.zeros(0, np.uint32)
    assert n_anim_verts == (len(vert) - anim_vert if n_parts else 0)
    anim_bytes = anim.tobytes()
    hdr_size = struct.calcsize(HEADER)
    off_verts = hdr_size
    off_tris = off_verts + vert.nbytes
    off_mats = off_tris + tri.nbytes
    off_lights = off_mats + len(mat_bytes)
    off_sky = off_lights + len(light_bytes)
    off_tex = off_sky + (len(sky) if sky else 0)
    off_anim = off_tex + 32 * len(tex_data)
    off_data = off_anim + len(anim_bytes)
    tex_table, blobs, cursor = b"", [], off_data
    for t in tex_data:
        if t is None:
            tex_table += struct.pack("<4IQQ", 0, 0, 0, 0, 0, 0)
            continue
        blob = b"".join(m[2] for m in t.mips)
        tex_table += struct.pack("<4IQQ", TEX_KIND[t.kind], t.width, t.height, len(t.mips), cursor, len(blob))
        blobs.append(blob)
        cursor += len(blob)
    hide = (list(hide) + [0] * 8)[:8]
    hdr = struct.pack(HEADER, b"RTXM", 4, len(vert), len(tri), len(mats.records), len(tex_data), len(lights),
                      SKY_SIZE if sky else 0, *gstart[:5], grass_vert, anim_vert, len(slot_w),
                      off_verts, off_tris, off_mats, off_tex, off_lights, off_sky, *hide, *wind, n_parts, len(anim), off_anim)
    key = nf_tracks.key(slot)
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, key + ".rtxm")
    with open(out + ".tmp", "wb") as o:
        for b in (hdr, vert.tobytes(), tri.tobytes(), mat_bytes, light_bytes, sky or b"", tex_table, anim_bytes, *blobs):
            o.write(b)
    os.replace(out + ".tmp", out)

    opaque = vert["pos"][tris[:gstart[0]]].astype(np.float64)
    lev = None if nf_tracks.native(slot) else nf_tracks.host_lev(slot).read_bytes()
    floor, filled, cells = floor_field(lev, opaque)
    with open(os.path.join(out_dir, key + ".floor"), "wb") as o:
        o.write(floor)
    print(f"{key} {level}: {len(ents)} entities, {len(vert)} verts, {len(tri)} tris (opaque {gstart[0]}, alpha "
          f"{gstart[1] - gstart[0]}, grass {gstart[2] - gstart[1]}, decal {gstart[3] - gstart[2]}, animated {gstart[4] - gstart[3]}, "
          f"proxy {gstart[5] - gstart[4]}; {n_parts} moving parts, {nkeys} keys, {len(slot_w)} proxy slots), {len(mats.records)} materials, "
          f"{len(tex_data)} textures ({len(missing)} missing), {len(lights)} lights, sky {'yes' if sky else 'no'}, "
          f"floor {filled}/{cells} cells, {cursor / 1e6:.0f} MB")
    return 0


def extract(pak):
    import nf_iga
    src = nf_tracks.pak_path(pak)
    root = WORK / pak
    stamp = root / ".extracted"
    if not stamp.exists() or stamp.stat().st_mtime < src.stat().st_mtime:
        a = nf_iga.Iga(str(src))
        for fi in a.files:
            dst = root / nf_iga.clean_name(fi["logical"] or fi["name"])
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(a.read(fi))
        stamp.touch()
    return str(root), stamp.stat().st_mtime


def unnest_update():
    """The update package's own levels (the Grand Prix tracks: archives/T5xx_*.pak,
    T155_*) come inside its update*.pak: taken out to nf_tracks.UPDATE_LEVELS
    once (lower case names, as the game package's)."""
    import nf_iga
    paks = sorted(glob.glob(str(nf_tracks.UPDATE / "update*.pak")))
    if not paks:
        return
    stamp = nf_tracks.UPDATE_LEVELS / ".unnested"
    newest = max(os.path.getmtime(p) for p in paks)
    if stamp.exists() and stamp.stat().st_mtime >= newest:
        return
    nf_tracks.UPDATE_LEVELS.mkdir(parents=True, exist_ok=True)
    level = re.compile(r"archives/([ta]\d{3}_[a-z_]+)\.pak$")
    n = 0
    for p in paks:
        a = nf_iga.Iga(p)
        for fi in a.files:
            m = level.match((fi["logical"] or fi["name"]).replace("\\", "/").lower())
            if m and "flyby" not in m.group(1):
                (nf_tracks.UPDATE_LEVELS / (m.group(1) + ".pak")).write_bytes(a.read(fi))
                n += 1
    stamp.touch()
    print(f"update package: {n} level archives taken out to {nf_tracks.UPDATE_LEVELS}")


def build_all(out_dir):
    code = max((HERE / f).stat().st_mtime for f in ("nf_level.py", "nf_igz.py", "nf_tex.py", "nf_iga.py", "nf_tracks.py"))
    os.makedirs(out_dir, exist_ok=True)
    unnest_update()
    keys = set()
    for slot, pak, extra in nf_tracks.available():
        root, stamp = extract(pak)
        key = nf_tracks.key(slot)
        out = os.path.join(out_dir, key + ".rtxm")
        if os.path.exists(out) and os.path.exists(out[:-5] + ".floor") and os.path.getmtime(out) > max(code, stamp):
            keys |= {key + ".rtxm", key + ".floor"}
            continue
        if nf_tracks.hosted(slot):
            _, _, med = placement(slot, root)
            if nf_tracks.placement_bad(slot):
                print(f"{key}: the remake's layout doesn't match the Nitro-Fueled level (median {med:.0f} units): left out")
                continue
        keys |= {key + ".rtxm", key + ".floor"}
        build(slot, root, out_dir, archive_dirs(slot), nf_tracks.hide_ids(extra))
    for f in os.listdir(out_dir):
        if f not in keys and not f.endswith(".coll"):  # (.coll: scripts/nf_collision.py's)
            os.remove(os.path.join(out_dir, f))  # earlier keys (stock-N), tracks gone
    return 0


# ---- placement: PS1 = M @ NF + t ---------------------------------------------------

def nf_track_points(ldir):
    """Control points of the race system's track splines (NF units), densified.
    Some Crash Nitro Kart levels keep them in the _Design map (or spell it
    "Racesystem"); without any: the design map's race / AI splines."""
    files = sorted(glob.glob(os.path.join(ldir, "*.igz")))
    pts = _track_points([p for p in files if re.search(r"race_?system", os.path.basename(p), re.I)], True)
    if not pts:
        pts = _track_points([p for p in files if "_design" in os.path.basename(p).lower()], True)
    if not pts:
        pts = _track_points([p for p in files if "_design" in os.path.basename(p).lower()], False)
    return np.concatenate(pts) if pts else np.zeros((0, 3))


def _track_points(files, race_component):
    pts = []
    for p in files:
        z = nf_igz.Igz(p)
        if race_component and "CRaceTrackSplineComponentData" not in z.metatypes:
            continue
        if "igSpline2" not in z.metatypes:
            continue
        names = {o: n for n, o in z.named_objects().items()}
        for e in z.of_type("CEntity") + z.of_type("igEntity") + z.of_type("CGameEntity"):
            if race_component:
                if not z.reachable(e, {"CRaceTrackSplineComponentData"}):
                    continue
            elif not re.search(r"race|_ai_|^ai_|spline_ai", names.get(e, "").lower()):
                continue
            m, pos = entity_transform(z, e)
            for sp in z.reachable(e, {"igSpline2"}):
                for lst in z.reachable(sp, {"igSplineControlPoint2List"}):
                    cp = np.array([z.vec(c + 0xC, 3) for c in z.object_list(lst) if c])
                    if len(cp) < 2:
                        continue
                    cp = cp @ m.T + pos
                    for a, b in zip(cp[:-1], cp[1:]):
                        n = max(1, int(np.linalg.norm(b - a) / 75.0))
                        pts.append(a + (b - a) * (np.arange(n)[:, None] / n))
    return pts


def ps1_track_points(lev):
    """PS1 restart path (CheckpointNode chain), densified."""
    n = int.from_bytes(lev[4 + 0x148:4 + 0x14C], "little")
    off = int.from_bytes(lev[4 + 0x14C:4 + 0x150], "little")
    if n < 4 or off + 4 + n * 12 > len(lev):
        return np.zeros((0, 3))
    rp = np.frombuffer(lev, np.dtype([("pos", "<i2", 3), ("dist", "<u2"), ("nx", "u1", 4)]), n, 4 + off)
    p = rp["pos"].astype(np.float64)
    out = []
    for i in range(n):
        j = rp["nx"][i][0]
        if j < n:
            k = max(1, int(np.linalg.norm(p[j] - p[i]) / 50.0))
            out.append(p[i] + (p[j] - p[i]) * (np.arange(k)[:, None] / k))
    return np.concatenate(out) if out else p


class Nearest:
    def __init__(self, pts):
        import cv2
        self.p = pts.astype(np.float32)
        self.idx = cv2.flann_Index(self.p, dict(algorithm=1, trees=4))

    def __call__(self, q):
        i, d = self.idx.knnSearch(q.astype(np.float32), 1, params=dict(checks=64))
        return self.p[i[:, 0]].astype(np.float64), np.sqrt(d[:, 0])


def rigid_y(a, b):
    """rotation about Y + translation taking points a onto b (least squares)"""
    ma, mb = a.mean(0), b.mean(0)
    h = (a - ma)[:, [0, 2]].T @ (b - mb)[:, [0, 2]]
    u, _, vt = np.linalg.svd(h)
    r2 = vt.T @ u.T
    if np.linalg.det(r2) < 0:
        vt[1] *= -1
        r2 = vt.T @ u.T
    r = np.eye(3)
    r[np.ix_([0, 2], [0, 2])] = r2
    return r, mb - r @ ma


def similar_y(a, b):
    """rotation about Y, uniform scale + translation taking points a onto b
    (least squares, Umeyama in the ground plan; the scale applies to Y too)"""
    r, _ = rigid_y(a, b)
    ma, mb = a.mean(0), b.mean(0)
    ra = (a - ma) @ r.T
    sc = float(((b - mb)[:, [0, 2]] * ra[:, [0, 2]]).sum() / max((ra[:, [0, 2]] ** 2).sum(), 1e-9))
    return r * sc, mb - sc * (r @ ma)


def icp_3d(src, nearest, m, t, iters=30, keep=0.7):
    """refine PS1 = m @ NF + t: a rotation about Y + translation of the mapped NF
    surfaces onto the PS1 points src (trimmed ICP); returns m, t, median distance"""
    for _ in range(iters):
        q, d = nearest.query(src, m, t)
        k = d <= np.quantile(d, keep)
        r, tt = rigid_y(q[k], src[k])  # mapped NF -> PS1
        m, t = r @ m, r @ t + tt
    q, d = nearest.query(src, m, t)
    return m, t, float(np.median(d))


class MappedNearest:
    """nearest NF point (mapped to PS1 by m, t) to each PS1 point: NF stays in its
    own kd-tree, queries go back through the inverse mapping"""

    def __init__(self, nf_pts):
        self.n = Nearest(nf_pts)

    def query(self, ps1, m, t):
        mi = np.linalg.inv(m)
        q, _ = self.n((ps1 - t) @ mi.T)
        mq = q @ m.T + t
        return mq, np.linalg.norm(mq - ps1, axis=1)


def ps1_crates(lev):
    """Weapon crate positions (crate_question InstDefs) of a PS1 level."""
    n = int.from_bytes(lev[4 + 0x0C:4 + 0x10], "little")
    off = int.from_bytes(lev[4 + 0x10:4 + 0x14], "little")
    out = []
    for i in range(min(n, 4096)):
        b = 4 + off + i * 0x40
        if b + 0x40 > len(lev):
            break
        if lev[b:b + 0x10].split(b"\0")[0].lower().startswith(b"crate_question"):
            out.append(struct.unpack_from("<3h", lev, b + 0x30))
    return np.array(out, np.float64).reshape(-1, 3)


def nf_crates(ldir):
    """Weapon crate positions (crate_question entities) of an NF level, NF units."""
    out = []
    for p in sorted(glob.glob(os.path.join(ldir, "*.igz"))):
        z = nf_igz.Igz(p)
        for n, o in z.named_objects().items():
            if n.lower().startswith("crate_question") and z.obj_type(o) in ("igEntity", "CGameEntity", "CEntity"):
                out.append(z.vec(o + 0x20, 3))
    return np.array(out, np.float64).reshape(-1, 3)


def crate_fit(a, b_nf, m, t, iters=20):
    """translation refit of PS1 = m @ NF + t on crate positions; median distance"""
    if len(a) < 3 or len(b_nf) < 3:
        return t, None
    for _ in range(iters):
        bm = b_nf @ m.T + t
        d = np.linalg.norm(a[:, None, :] - bm[None, :, :], axis=2)
        j = d.argmin(1)
        t = t + np.median(a - bm[j], axis=0)
    bm = b_nf @ m.T + t
    return t, float(np.median(np.linalg.norm(a[:, None, :] - bm[None, :, :], axis=2).min(1)))


def turn_of(m):
    """the heading (radians) of placement m relative to PS1_FROM_NF: m = Ry(a) @ PS1_FROM_NF"""
    r = m @ np.linalg.inv(PS1_FROM_NF)
    return math.atan2(r[2, 0], r[0, 0])


def solve_placement(slot, root):
    """PS1 = M @ NF + t for this level: the race spline against the PS1 restart
    path over 24 headings, then the PS1 floor onto the NF surfaces (ICP); the
    default (Crash Cove's) refined the same way competes. Scale is NF's 1.5."""
    import rt_bake
    ldir = level_dir(root)
    lev = nf_tracks.host_lev(slot).read_bytes()
    mesh = rt_bake.Mesh(lev)
    floor = mesh.verts["pos"][mesh.ground_verts()].astype(np.float64)
    pts, models = [], {}
    for _, ref, m, t in level_entities(root):
        f = model_file(root, ref)
        if not os.path.exists(f):
            continue
        if f not in models:
            models[f] = load_model(f)
        for d in models[f]:
            w = d["pos"].astype(np.float64) @ m.T + t
            pts += [w, w[d["index"]].mean(1)]
    nf_near = MappedNearest(np.concatenate(pts))
    cands = [(PS1_FROM_NF.copy(), np.zeros(3))]
    a2 = ps1_track_points(lev)
    b = nf_track_points(ldir)
    if len(a2) and len(b):
        bn = Nearest(b)  # NF spline, NF units
        for k in range(24):
            ang = k * math.pi / 12
            c, sn = math.cos(ang), math.sin(ang)
            r = np.array([[c, 0, -sn], [0, 1, 0], [sn, 0, c]]) @ PS1_FROM_NF
            t = a2.mean(0) - b.mean(0) @ r.T
            for _ in range(40):  # 2D curve ICP on the PS1 side
                mi = np.linalg.inv(r)
                q, d = bn((a2 - t) @ mi.T)
                q = q @ r.T + t
                kk = d <= np.quantile(d, 0.8)
                rr, tt = rigid_y(q[kk], a2[kk])
                rr[1, 1], tt[1] = 1.0, 0.0  # (heading and ground plan only)
                r, t = rr @ r, rr @ t + tt
            mi = np.linalg.inv(r)
            q, d = bn((a2 - t) @ mi.T)
            q = q @ r.T + t
            t[1] += float(np.median(a2[:, 1] - q[:, 1]))
            cands.append((r, t, float(np.mean(d))))
        cands = [cands[0]] + sorted(cands[1:], key=lambda c: c[2])[:3]
    # every quarter turn of the default too, from the best curve offset and from none
    offsets = [np.zeros(3)] + ([cands[1][1]] if len(cands) > 1 else [])
    for k in range(4):
        c, sn = math.cos(k * math.pi / 2), math.sin(k * math.pi / 2)
        r = np.array([[c, 0, -sn], [0, 1, 0], [sn, 0, c]]) @ PS1_FROM_NF
        cands += [(r, off) for off in offsets]
    best = None
    for c in cands:
        m, t, med = icp_3d(floor, nf_near, c[0], c[1])
        if best is None or med < best[2]:
            best = (m, t, med)
    # Nitro-Fueled turns its tracks by whole quarter turns: snap the heading
    # (ICP drifts a fraction of a degree, tens of units at a track's far end)
    # and refit the offset alone
    m, t, _ = best
    q = round(turn_of(m) / (math.pi / 2)) * (math.pi / 2)
    c, sn = math.cos(q), math.sin(q)
    m = np.array([[c, 0, -sn], [0, 1, 0], [sn, 0, c]]) @ PS1_FROM_NF
    for _ in range(30):
        mq, d = nf_near.query(floor, m, t)
        k = d <= np.quantile(d, 0.7)
        t = t + (floor[k] - mq[k]).mean(0)
    _, d = nf_near.query(floor, m, t)
    snapped = (m, t, float(np.median(d)))
    best = snapped if snapped[2] <= best[2] * 1.15 else best
    # symmetric arenas fit their floor in several headings: the weapon crates
    # (the PS1 level's InstDefs, NF's crate_question entities) pick the one
    pc, nc = ps1_crates(lev), nf_crates(ldir)
    _, cur = crate_fit(pc, nc, best[0], best[1])
    if cur is not None and cur > 60.0:
        alt = []
        for k in range(4):
            c, sn = math.cos(k * math.pi / 2), math.sin(k * math.pi / 2)
            mk = np.array([[c, 0, -sn], [0, 1, 0], [sn, 0, c]]) @ PS1_FROM_NF
            tk = pc.mean(0) - nc.mean(0) @ mk.T
            tk, score = crate_fit(pc, nc, mk, tk)
            alt.append((score, mk, tk))
        score, mk, tk = min(alt, key=lambda x: x[0])
        if score < cur * 0.5:
            for _ in range(30):  # the floor then settles the offset
                mq, d = nf_near.query(floor, mk, tk)
                k = d <= np.quantile(d, 0.7)
                tk = tk + (floor[k] - mq[k]).mean(0)
            _, d = nf_near.query(floor, mk, tk)
            print(f"  nf-{slot}: crates choose another heading (crates {cur:.0f} -> {score:.0f} units)")
            best = (mk, tk, float(np.median(d)))
            return best
    # battle arenas whose crates moved in NF (Lab Basement): their walls and
    # pillars, the whole PS1 mesh, against each quarter turn
    if nf_tracks.is_battle(slot):
        tri = mesh.triangles()
        allp = np.concatenate([mesh.verts["pos"][np.unique(tri)].astype(np.float64), mesh.verts["pos"][tri].astype(np.float64).mean(1)])
        res = []
        for k in range(4):
            c, sn = math.cos(k * math.pi / 2), math.sin(k * math.pi / 2)
            mk = np.array([[c, 0, -sn], [0, 1, 0], [sn, 0, c]]) @ PS1_FROM_NF
            for start in (np.zeros(3), best[1].copy()):
                tk = start
                for _ in range(30):
                    mq, d = nf_near.query(allp, mk, tk)
                    kk = d <= np.quantile(d, 0.7)
                    tk = tk + (allp[kk] - mq[kk]).mean(0)
                _, d = nf_near.query(allp, mk, tk)
                res.append((float(np.median(d)), k, mk, tk))
        res = [min((r for r in res if r[1] == k), key=lambda r: r[0]) for k in range(4)]
        cur = res[round(turn_of(best[0]) / (math.pi / 2)) % 4][0]
        top = min(res, key=lambda r: r[0])
        # (the walls also settle the offset a flat arena floor can't)
        if top[0] <= cur:
            mk, tk = top[2], top[3]
            mq, d = nf_near.query(floor, mk, tk)
            tk[1] += float(np.median((floor - mq)[d <= np.quantile(d, 0.7), 1]))
            _, d = nf_near.query(floor, mk, tk)
            if top[0] < cur * 0.8:
                print(f"  nf-{slot}: the level's walls choose another heading ({cur:.0f} -> {top[0]:.0f} units)")
            best = (mk, tk, float(np.median(d)))
    return best


# a remake whose floor lies further than this (median, units) from the
# Nitro-Fueled surfaces under the best fit isn't the same layout: left out
HOSTED_MAX_MEDIAN = 60.0


def solve_similar(tid, root):
    """PS1 = M @ NF + t for a Nitro-Fueled track on a fan remake: drawn at its
    own scale and heading (not NF's 1.5 and quarter turns), so the race spline
    is fitted to the remake's restart path over 24 headings x a range of
    scales, then the remake's floor onto the NF surfaces (trimmed ICP with
    scale)."""
    import rt_bake
    ldir = level_dir(root)
    lev = nf_tracks.host_lev(tid).read_bytes()
    mesh = rt_bake.Mesh(lev)
    floor = mesh.verts["pos"][mesh.ground_verts()].astype(np.float64)
    pts, models = [], {}
    for _, ref, m, t in level_entities(root):
        f = model_file(root, ref)
        if not os.path.exists(f):
            continue
        if f not in models:
            models[f] = load_model(f)
        for d in models[f]:
            w = d["pos"].astype(np.float64) @ m.T + t
            pts += [w, w[d["index"]].mean(1)]
    nf_near = MappedNearest(np.concatenate(pts))
    a2 = ps1_track_points(lev)
    b = nf_track_points(ldir)
    if not len(a2) or not len(b):
        print(f"  nf-{tid}: no race path to line up ({len(a2)} remake, {len(b)} NF points)")
        return PS1_FROM_NF.copy(), np.zeros(3), float("inf")
    bn = Nearest(b)
    # the remake's track length against NF's (both densified the same way):
    # scales around that ratio
    span_a = np.linalg.norm(a2.max(0) - a2.min(0))
    span_b = np.linalg.norm((b @ PS1_FROM_NF.T).max(0) - (b @ PS1_FROM_NF.T).min(0))
    base = span_a / max(span_b, 1e-6)
    cands = []
    for sc in base * np.geomspace(0.7, 1.4, 9):
        for k in range(24):
            ang = k * math.pi / 12
            c, sn = math.cos(ang), math.sin(ang)
            r = np.array([[c, 0, -sn], [0, 1, 0], [sn, 0, c]]) @ PS1_FROM_NF * sc
            t = a2.mean(0) - b.mean(0) @ r.T
            for _ in range(30):  # 2D curve ICP on the PS1 side, with scale
                mi = np.linalg.inv(r)
                q, d = bn((a2 - t) @ mi.T)
                q = q @ r.T + t
                kk = d <= np.quantile(d, 0.8)
                rr, tt = similar_y(q[kk], a2[kk])
                rr[1, 1], tt[1] = np.sqrt(abs(np.linalg.det(rr[np.ix_([0, 2], [0, 2])]))), 0.0
                r, t = rr @ r, rr @ t + tt
            mi = np.linalg.inv(r)
            q, d = bn((a2 - t) @ mi.T)
            q = q @ r.T + t
            t[1] += float(np.median(a2[:, 1] - q[:, 1]))
            cands.append((r, t, float(np.mean(d[d <= np.quantile(d, 0.8)]))))
    cands.sort(key=lambda c: c[2])
    best = None
    for r, t, _ in cands[:4]:
        m, t2 = r, t
        for _ in range(30):  # the floor onto the NF surfaces, with scale
            q, d = nf_near.query(floor, m, t2)
            k = d <= np.quantile(d, 0.7)
            rr, tt = similar_y(q[k], floor[k])
            m, t2 = rr @ m, rr @ t2 + tt
        _, d = nf_near.query(floor, m, t2)
        med = float(np.median(d))
        if best is None or med < best[2]:
            best = (m, t2, med)
    return best


# native NF tracks (nf_tracks.NATIVE: no PS1 level): Nitro-Fueled's axes as the
# stock levels have them (x, z up -> PS1 x, y up; 1.5 NF units a PS1 unit)
NATIVE_M = np.array([[2.0 / 3.0, 0.0, 0.0], [0.0, 0.0, 2.0 / 3.0], [0.0, -2.0 / 3.0, 0.0]])


def placement(slot, root):
    """solve_placement: as solved for this project (scripts/nf_placement.json:
    every build lines the levels up alike, no solving), else solved and cached in
    nf_raw/placement.json (NF_SOLVE=1: always that). Native tracks: NATIVE_M,
    the race line's box centred on the origin (PS1 positions are 16 bit)."""
    import json
    if nf_tracks.native(slot):
        pts = nf_track_points(level_dir(root))
        c = (pts.min(0) + pts.max(0)) / 2
        return NATIVE_M, np.round(-NATIVE_M @ c), 0.0
    shipped = HERE / "nf_placement.json"
    if shipped.exists() and os.environ.get("NF_SOLVE") != "1":
        e = json.loads(shipped.read_text()).get(str(slot))
        if e:
            t = np.array(e["t"])
            t[1] -= e.get("dy", 0.0)
            return np.array(e["m"]), t, e["median"]
    cache = WORK / "placement.json"
    data = json.loads(cache.read_text()) if cache.exists() else {}
    k = str(slot)
    if k not in data:
        if nf_tracks.hosted(slot):
            m, t, med = solve_similar(slot, root)
            data[k] = {"m": m.tolist(), "t": t.tolist(), "median": med, "bad": not med <= HOSTED_MAX_MEDIAN}
        else:
            m, t, med = solve_placement(slot, root)
            data[k] = {"m": m.tolist(), "t": t.tolist(), "median": med}
        cache.write_text(json.dumps(data, indent=1))
    e = data[k]
    if e.get("bad"):
        return np.array(e["m"]), np.array(e["t"]), e["median"]
    if "dy" not in e:
        e["dy"] = settle_height(slot, root, np.array(e["m"]), np.array(e["t"]))
        cache.write_text(json.dumps(data, indent=1))
    t = np.array(e["t"])
    t[1] -= e["dy"]
    return np.array(e["m"]), t, e["median"]


def settle_height(slot, root, m, t):
    """How far the NF ground lies above the PS1 drivable floor under a placement:
    the median, over floor cells, of the nearest up-facing NF surface's height
    difference. Nearest point ICP leaves flat floors (the arenas) a dozen units
    off, as it matches any NF point (kerbs, rugs, props) to the floor."""
    import rt_bake
    lev = nf_tracks.host_lev(slot).read_bytes()
    mesh = rt_bake.Mesh(lev)
    pos = mesh.verts["pos"].astype(np.float64)
    idx = mesh.index[(mesh.flags & rt_bake.QUAD_GROUND) != 0]
    g = pos[np.concatenate([idx[:, list(q)] for q in rt_bake.HI_TRIS[:4]])]
    lo, hi = g.reshape(-1, 3).min(0), g.reshape(-1, 3).max(0)
    cell = FLOOR_CELL
    dimx, dimz = int((hi[0] - lo[0]) / cell) + 1, int((hi[2] - lo[2]) / cell) + 1
    pc, ph = raster_heights(g, cell, lo[0], lo[2], dimx, dimz)
    mats = Materials(root, Shared(archive_dirs(slot), str(WORK / "shared")))
    models, nc, nh = {}, [], []
    for _, ref, em, et in level_entities(root):
        f = model_file(root, ref)
        if not os.path.exists(f):
            continue
        if f not in models:
            models[f] = load_model(f)
        for d in models[f]:
            mid = mats.material(d["material_ns"], d["material"])
            if mats.group(mid) != 0 or mats.records[mid]["effect"] in SKIP_EFFECTS:
                continue
            w = ((d["pos"].astype(np.float64) @ em.T + et) @ m.T + t)[d["index"]]
            n = np.cross(w[:, 1] - w[:, 0], w[:, 2] - w[:, 0])
            w = w[n[:, 1] > 0.5 * np.linalg.norm(n, axis=1)]  # (PS1 Y up)
            if len(w):
                c, h = raster_heights(w, cell, lo[0], lo[2], dimx, dimz)
                nc.append(c)
                nh.append(h)
    if not nc:
        return 0.0
    nc, nh = np.concatenate(nc), np.concatenate(nh)
    # each PS1 floor sample against the NF heights in its cell: the nearest
    order = np.argsort(nc, kind="stable")
    nc, nh = nc[order], nh[order]
    a, b = np.searchsorted(nc, pc), np.searchsorted(nc, pc, side="right")
    diffs = []
    for i in np.flatnonzero(b > a)[:: max(1, len(pc) // 200000)]:
        d = nh[a[i]:b[i]] - ph[i]
        d = d[np.argmin(np.abs(d))]
        if abs(d) < 40.0:
            diffs.append(d)
    dy = float(np.median(diffs)) if len(diffs) > 100 else 0.0
    print(f"  nf-{slot}: NF ground {dy:+.1f} units off the PS1 floor ({len(diffs)} samples)")
    return round(dy, 2)


def fit(slot, root):
    """the level's placement and how well the PS1 floor lies on the NF surfaces"""
    m, t, med = placement(slot, root)
    s = np.cbrt(abs(np.linalg.det(m)))
    head = math.degrees(turn_of(m))
    print(f"nf-{slot}: scale 1/{1 / s:.3f}, heading {head:7.2f}, offset {np.round(t, 1)}, median distance PS1 floor -> NF {med:.1f}"
          + ("  <-- poor fit" if med > 40 else "") + ("  (left out: not the same layout)" if nf_tracks.placement_bad(slot) else ""))
    return med


def main(argv):
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "all":
        return build_all(argv[2] if len(argv) > 2 else str(OUT_DIR))
    if cmd in ("stats", "fit") and len(argv) > 2:
        unnest_update()
        slots = [int(a) if a.isdigit() else a for a in argv[2:]] if argv[2] != "all" else [lv[0] for lv in nf_tracks.available()]
        paks = {lv[0]: lv[1] for lv in nf_tracks.LEVELS}
        paks.update({h[0]: h[1] for h in nf_tracks.HOSTED})
        paks.update({n[0]: n[1] for n in nf_tracks.NATIVE})
        for slot in slots:
            root, _ = extract(paks[slot])
            if cmd == "fit":
                fit(slot, root)
                continue
            from collections import Counter
            ents = level_entities(root)
            chosen = sorted({e[0] for e in ents})
            ldir = level_dir(root)
            skipped = sorted(os.path.basename(p) for p in glob.glob(os.path.join(ldir, "*_[Aa]rt*.igz"))
                             if not map_wanted(os.path.basename(p)[:-4], os.path.basename(ldir)))
            models = Counter(e[1] for e in ents)
            missing = [m for m in models if not os.path.exists(model_file(root, m))]
            print(f"nf-{slot}: {len(ents)} entities from {chosen}; skipped {skipped}; {len(models)} models, {len(missing)} missing")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
