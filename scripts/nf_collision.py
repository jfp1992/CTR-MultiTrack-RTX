#!/usr/bin/env python3
"""Nitro-Fueled collision: what an NF track's karts collide with
(native/game/MultiTrack/MT_NfColl.c).

The NF tracks draw the Nitro-Fueled level over a PS1 level (the stock one, or a
fan remake for the hosted tracks). This writes <key>.coll next to the remaster
mesh: Nitro-Fueled's own collision (models/maps/Octane/<Level>/
StaticCollision_<Level>.hkx, scripts/nf_hkx.py), no PS1 quadblocks, in the
game's own QuadBlock / LevVertex / BSP structures, with the native port's
limits lifted (MT_NfColl.c):

  * one triangle per QuadBlock (kind 1), tested by the game as that one exact
    triangle; its normal scale (triNormalVecDividend[LO_0]) is the stock
    files' formula, 4096 x 2^shift / |cross|
  * vertex indices count from each quadblock's own base (its BSP leaf's
    vertices), BSP children are 32-bit: no 65535 vertex / 16383 node caps

Havok's ground is a simplified mesh (Dingo Canyon's road triangles are ~500
units across: 6-24 units off the drawn road on curves), so the ground is
conformed to the level as drawn (conform()): Havok's triangles are split,
crack free, where the drawn surface (the remaster mesh's opaque and decal
triangles, assets/rtx_remaster/<key>.rtxm) bends away from them, and their
corners moved onto it. Havok has the say where there is ground, its walls,
and what each surface is (its collision material, nf_hkx.MATERIALS):

  * terrain by material (TERRAIN), as the PS1 levels have it on the same
    surfaces: the base materials play as the track, the slow ones as grass /
    dirt / snow / mud
  * liquids (water, deep mud) over a bed: the bed is driven on, in water (as
    the stock levels' water); with no bed in Havok, the liquid itself is
    conformed down onto the drawn bed under it
  * Respawn: kill planes; SpeedBoost / SpeedBoostSuper: turbo pad triggers
  * checkpoint (lap progress, respawns) from the nearest PS1 quadblock of the
    kind: race data, the restart path the game still plays
  * the PS1 level's instance hitboxes (crates, ...) in the leaf holding each
  * the PS1 start grid and restart points lifted onto the NF ground (version 2)

The PS1 level is still what's loaded and drawn (GL), with its start grid,
restart path, AI paths and instances.

File (little endian): MTCollHeader (MT_NfColl.c), LevVertex[], QuadBlock[]
(pointers 0), u32 quad vertex base[], u8 quad kind[], BSP[] (branch:
childID[0..1] low, [2..3] high halves of 32-bit child indices; leaf:
ptrQuadBlockArray = first quad index, bspHitboxArray = hitbox byte offset + 1),
hitboxes (BSP 0x20 each, 0x20 zero bytes after each leaf's; instDef = stock
level body offset + 1), s16 start grid y[8], s16 restart point y[].

    nf_collision.py <tid> [out_dir]       default native/build/assets/rtx_remaster
    nf_collision.py all [out_dir]         every NF track, when stale (build_native.sh)
"""
import pathlib
import struct
import sys

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import nf_level  # noqa: E402
import nf_tracks  # noqa: E402

ROOT = HERE.parent
REMASTER = ROOT / "native/build/assets/rtx_remaster"

QUAD = 0x5C
BSPN = 0x20
VERT_DT = np.dtype([("pos", "<i2", 3), ("flags", "<u2"), ("hi", "u1", 4), ("lo", "u1", 4)])
QUAD_DT = np.dtype([("index", "<u2", 9), ("flags", "<u2"), ("dol", "<u4"), ("doh", "<u4"), ("tex", "<u4", 4), ("bmin", "<i2", 3),
                    ("bmax", "<i2", 3), ("terrain", "u1"), ("wint", "u1"), ("wvan", "u1"), ("mulny", "i1"), ("block", "<i2"),
                    ("checkpoint", "u1"), ("shift", "u1"), ("texlow", "<u4"), ("pvs", "<u4"), ("div", "<i2", 10)])
assert QUAD_DT.itemsize == QUAD
HEADER = "<4sI11I4I2I"
VERSION = 3

# QuadBlock.quadFlags (native/include/namespace_Level.h)
Q_NO_RESPONSE, Q_TRIGGER, Q_KILL, Q_CAMERA = 0x0010, 0x0040, 0x0200, 0x0800
Q_GROUND, Q_WALL = 0x1000, 0x2000
Q_SKIP_WATER = 0x8000
Q_COLLIDE = Q_GROUND | Q_WALL
DOUBLE_SIDED = 0x80000000  # QuadBlock.draw_order_low: collides from both sides
LEAF, LEAF_WATER = 0x1, 0x2
LEAF_QUADS = 24
NO_CHILD = 0xFFFFFFFF

# what of the NF mesh is solid: opaque draws (the rtxm's first group), not glass / water / decals / foliage
MAT_SKIP = nf_level.MAT_GLASS | nf_level.MAT_WATER | nf_level.MAT_DECAL | nf_level.MAT_TRANSLUCENT | nf_level.MAT_GRASS
GROUND_NY = 0.5      # up-facing at least this much: ground (drivable), else wall
COVER = 60.0         # (visual fallback) a stock wall with NF surface this close is NF's to do
COVER_FLOOR = 300.0  # (visual fallback) a stock floor with no NF surface this close: kept
LINE_CLEAR = 150.0   # (visual fallback) stock walls closer than this to the race line are dropped

HAVOK_TO_NF = 39.37  # Havok metres -> NF units (inches)
MAX_EDGE = 1500.0    # (the stock level's collision triangles: up to ~1700)

# conform(): the ground onto the drawn surface
WELD = 0.5           # Havok corners this close are one vertex
REACH = 40.0         # drawn surface this far over / under Havok's is the one it stands for
FACING = np.cos(np.radians(35.0))  # and faces the same way within this
LIFT = 8.0           # a drawn surface up to this over the nearest one is on top of it (decal over opaque)
LIFT_OVER = 3.0      # (and no more than this over Havok's: not a kerb or prop on the road)
FIT = 2.0            # conformed until the drawn surface is this close
MIN_EDGE = 48.0      # (no finer than this: about a kart's length)
FOOT = 20.0          # the drawn surface's top within this of a corner: what a kart's wheels ride on (not plank gaps, seams)
SMOOTH_TOL = 4.0     # smoothing moves conformed corners no further than this off the drawn surface
SMOOTH_STEPS = 12
MAX_TILT = np.radians(14.0)  # a conformed ground triangle tilts no more than this off its Havok triangle (ramp lips)
CORRIDOR = 1000.0    # split only this close to the race line (the PS1 restart path; arenas: everywhere)
SPLIT_NY = 0.7       # and only ground this flat (steeper banks: corners moved, not split)
SPAWN_SHIFT = 150.0  # the start grid / restart points move onto the NF ground by no more than this
PAD_REACH = 40.0     # a drawn turbo pad this close over / under the ground is on it
LIQUID_BED = 60.0    # a liquid with solid ground this close under it is driven through, on the bed

# TerrainType (native/include/namespace_Level.h)
(T_ASPHALT, T_DIRT, T_GRASS, T_WOOD, T_WATER, T_STONE, T_ICE, T_TRACK, T_ICY_ROAD, T_SNOW, T_NONE, T_HARDPACK, T_METAL,
 T_FASTWATER, T_MUD, T_SIDESLIP, T_RIVERASPHALT, T_STEAMASPHALT, T_OCEANASPHALT, T_SLOWGRASS, T_SLOWDIRT) = range(21)
# Nitro-Fueled collision material -> the PS1 levels' terrain on the same
# surfaces (measured: NF triangles vs the nearest stock floor, all 25 levels):
# the PS1 plays its roads (asphalt, sand, snow, rock, metal, wood) as asphalt
TERRAIN = {
    "Grass": T_HARDPACK, "Mud": T_HARDPACK, "MudDry": T_HARDPACK, "SandDark": T_HARDPACK, "SandWet": T_HARDPACK,
    "GrassSlow": T_GRASS, "GrassSlowSlower": T_GRASS, "DirtSlow": T_DIRT, "DirtSlowSlower": T_SLOWDIRT, "SandSlow": T_DIRT,
    "RockSlow": T_DIRT, "MudSlowNoSplash": T_DIRT, "Dirt": T_HARDPACK, "SnowSlow": T_SNOW, "Ice": T_ICE,
    "MudSlow": T_MUD, "DeepMud": T_MUD,
    "RockAlwaysFloor": T_RIVERASPHALT, "MetalAlwaysFloor": T_RIVERASPHALT, "WaterAlwaysFloor": T_RIVERASPHALT,
    "MetalWet": T_OCEANASPHALT,
    "WaterDeep": T_FASTWATER, "WaterShallow": T_FASTWATER, "WaterDeepSlow": T_WATER, "WaterShallowSlow": T_WATER,
}
BOOST = {"SpeedBoost": 1, "SpeedBoostSuper": 2}  # trigger step flags (COLL_STEP_TRIGGER_*)
# flags as the stock levels have them: turbo pad triggers, kill planes
# instance hitboxes the karts set off by touch here (BSP hitbox class, flag byte
# 1): Mystery Caves' turtles are solid spheres in the stock level, a hit the
# swept kart only takes before any ground's; on the NF ground the ground's
# always comes first, so the turtle never bounced it (RB_Turtle_LInC)
HITBOX_TOUCH = 4
TOUCH_MODELS = {0x51}  # DYNAMIC_TURTLE
# the road (the race line runs on it) is never slow ground, nor under a kill
# plane, nor behind a wall, nor under a camera floor
SLOW_TERRAIN = {T_DIRT, T_GRASS, T_SNOW, T_MUD, T_SLOWDIRT, T_SLOWGRASS}
ROAD_SHARE = 0.10    # a material under this much of the race line is road: never slow
KILL_NEAR = 800.0    # Havok kill planes this close (x/z) to the race line ...
KILL_UNDER = 100.0   # ... and less than this under it: dropped (Gingerbread Joyride's steps)
CAMERA_OVER = 150.0  # ground this far over the race line nearest it is no camera floor (roofs, overpass tops)
FLAGS_TRIGGER = 0xA040
FLAGS_KILL = 0x2200


def is_liquid(name):
    return (name.startswith("Water") and name != "WaterAlwaysFloor") or name == "DeepMud"


def stock_level(slot):
    data = nf_tracks.host_lev(slot).read_bytes()
    size = struct.unpack_from("<I", data, 0)[0]
    body = data[4:4 + size]
    nmap = struct.unpack_from("<I", data, 4 + size)[0]
    ptrs = set(np.frombuffer(data, "<u4", nmap // 4, 8 + size).tolist())
    mi = struct.unpack_from("<I", body, 0)[0]
    nq, nv, _, pq, pv, _, broot, nb = struct.unpack_from("<8I", body, mi)
    q = np.frombuffer(body, QUAD_DT, nq, pq).copy()
    v = np.frombuffer(body, VERT_DT, nv, pv).copy()
    bsp = np.frombuffer(body, "u1", nb * BSPN, broot).reshape(nb, BSPN)
    flag = bsp[:, 0:2].copy().view("<u2")[:, 0]
    water = np.zeros(nq, bool)
    hitboxes = []
    for i in np.nonzero(flag & LEAF)[0]:
        o = broot + i * BSPN
        n = struct.unpack_from("<I", body, o + 0x18)[0]
        first = (struct.unpack_from("<I", body, o + 0x1C)[0] - pq) // QUAD
        if flag[i] & LEAF_WATER:
            water[first:first + n] = True
        hb = struct.unpack_from("<I", body, o + 0x14)[0]
        while hb and struct.unpack_from("<I", body, hb)[0] != 0:
            raw = bytearray(body[hb:hb + BSPN])
            inst = struct.unpack_from("<I", raw, 0x1C)[0] if (hb + 0x1C) in ptrs else 0
            struct.pack_into("<I", raw, 0x1C, inst + 1 if inst else 0)
            if inst and raw[1] == 0 and struct.unpack_from("<h", body, struct.unpack_from("<I", body, inst + 0x10)[0] + 0x10)[0] in TOUCH_MODELS:
                raw[1] = HITBOX_TOUCH
            hitboxes.append(bytes(raw))
            hb += BSPN
    # start grid (Level.DriverSpawn[8] @ 0x6C: pos, rot) and restart points
    spawn = np.frombuffer(body, "<i2", 8 * 6, 0x6C).reshape(8, 6)[:, :3].astype(np.float64)
    nr, pr = struct.unpack_from("<2I", body, 0x148)
    restart = (np.frombuffer(body, np.dtype([("pos", "<i2", 3), ("rest", "u1", 6)]), nr, pr)["pos"].astype(np.float64)
               if nr and pr and pr + nr * 12 <= size else np.zeros((0, 3)))
    return {"q": q, "v": v, "water": water, "hitboxes": hitboxes, "size": size, "spawn": spawn, "restart": restart}


def read_rtxm(path, decals=False):
    """PS1 space positions, vertex normals and the solid triangles (and the
    decals' and alpha tested ones', decals=True)"""
    data = open(path, "rb").read()
    h = struct.unpack_from(nf_level.HEADER, data, 0)
    nverts, ntris, nmats = h[2], h[3], h[4]
    gstart = h[8:13]
    off_verts, off_tris, off_mats = h[16], h[17], h[18]
    vdt = np.dtype([("pos", "<f4", 3), ("uv", "<f4", 2), ("n", "<u4"), ("c", "u1", 4), ("t", "<u4")])
    vert = np.frombuffer(data, vdt, nverts, off_verts)
    alltri = np.frombuffer(data, np.dtype([("v", "<u4", 3), ("m", "<u4")]), ntris, off_tris)
    mats = np.frombuffer(data, "<u4", nmats * 12, off_mats).reshape(nmats, 12)
    tri = alltri[:gstart[0]]
    tri = tri[(mats[tri["m"], 3] & MAT_SKIP) == 0]
    # octahedral snorm16 x2 -> unit vector (nf_level.oct_encode)
    xy = np.stack([(vert["n"] & 0xFFFF).astype(np.int16), (vert["n"] >> 16).astype(np.int16)], 1).astype(np.float64) / 32767.0
    z = 1.0 - np.abs(xy).sum(1)
    neg = z < 0
    xy[neg] = (1.0 - np.abs(xy[neg][:, ::-1])) * np.where(xy[neg] >= 0, 1.0, -1.0)
    n = np.column_stack([xy, z])
    out = (vert["pos"].astype(np.float64), n, tri["v"].astype(np.int64))
    if decals:
        # decals, and alpha tested draws (plank bridges, grids) of the second group
        dec = alltri[gstart[2]:gstart[3]]
        dec = dec[(mats[dec["m"], 3] & (nf_level.MAT_GLASS | nf_level.MAT_WATER)) == 0]
        alpha = alltri[gstart[0]:gstart[1]]
        f = mats[alpha["m"], 3]
        alpha = alpha[((f & nf_level.MAT_ALPHA_CLIP) != 0) & ((f & (MAT_SKIP & ~nf_level.MAT_DECAL)) == 0)]
        out += (np.concatenate([dec["v"], alpha["v"]]).astype(np.int64),)
    return out


def read_pads(path):
    """the turbo pads as drawn (materials nf_level.MAT_TURBO / _SUPER): their
    triangles (N, 3, 3) in PS1 space and step flag 1 / 2 each"""
    data = open(path, "rb").read()
    h = struct.unpack_from(nf_level.HEADER, data, 0)
    vert = np.frombuffer(data, "<f4", h[2] * 8, h[16]).reshape(h[2], 8)[:, :3].astype(np.float64)
    tri = np.frombuffer(data, np.dtype([("v", "<u4", 3), ("m", "<u4")]), h[3], h[17])
    mats = np.frombuffer(data, "<u4", h[4] * 12, h[18]).reshape(h[4], 12)
    f = mats[tri["m"], 3]
    sel = (f & (nf_level.MAT_TURBO | nf_level.MAT_TURBO_SUPER)) != 0
    v = tri["v"][sel].astype(np.int64)
    return vert[v], np.where((f[sel] & nf_level.MAT_TURBO_SUPER) != 0, 2, 1)


def pad_triggers(path, grid):
    """trigger triangles of the drawn turbo pads: each pad triangle's corners
    dropped onto the collision ground (TriGrid `grid`) under them (pads float a
    little over it; none where a corner has no ground within PAD_REACH), facing
    up -> (triangles (N, 3, 3), step flags)"""
    P, kind = read_pads(path)
    if not len(P):
        return np.zeros((0, 3, 3)), np.zeros(0, np.int64)
    flat = P.reshape(-1, 3)
    y = ground_under(grid, flat + [0.0, PAD_REACH, 0.0], above=0.0, below=2 * PAD_REACH).reshape(-1, 3)
    ok = ~np.isnan(y).any(1)
    T = P[ok].copy()
    T[:, :, 1] = y[ok]
    flip = face_normals(T[:, 0], T[:, 1], T[:, 2])[:, 1] < 0
    T[flip] = T[flip][:, [0, 2, 1]]
    keep = np.linalg.norm(face_normals(T[:, 0], T[:, 1], T[:, 2]), axis=1) > 1e-3
    return np.round(T[keep]), kind[ok][keep]


def face_normals(a, b, c):
    """the game's triangle normal: (C - A) x (B - A) (ground points +Y)"""
    return np.cross(c - a, b - a)


def unit(v):
    return v / np.maximum(np.linalg.norm(v, axis=-1, keepdims=True), 1e-12)


def nearest(src, query):
    import cv2
    index = cv2.flann_Index(np.ascontiguousarray(src, np.float32), dict(algorithm=1, trees=4))
    i, d = index.knnSearch(np.ascontiguousarray(query, np.float32), 1, params=dict(checks=64))
    return i[:, 0].astype(np.int64), np.sqrt(d[:, 0])


def surface_points(a, b, c, spacing=40.0):
    """points over triangles (corners, centroid, a barycentric grid on large ones)"""
    L = np.maximum.reduce([np.linalg.norm(b - a, axis=1), np.linalg.norm(c - b, axis=1), np.linalg.norm(a - c, axis=1)])
    k = np.clip(np.ceil(L / spacing), 1, 64).astype(np.int64)
    out = [a, b, c, (a + b + c) / 3]
    for n in np.unique(k[k > 1]):
        sel = k == n
        i, j = np.meshgrid(np.arange(n + 1), np.arange(n + 1), indexing="ij")
        ok = (i + j) <= n
        u, v = i[ok] / n, j[ok] / n
        A, B, C = a[sel][:, None], b[sel][:, None], c[sel][:, None]
        out.append((A + (B - A) * u[None, :, None] + (C - A) * v[None, :, None]).reshape(-1, 3))
    return np.concatenate(out)


def normal_scale(cross):
    """triNormalVecBitShift and triNormalVecDividend for each triangle's cross
    product (int64 (N, 3)): normal = cross * dividend >> shift, length 4096;
    the largest shift (<= 18) keeping the dividend <= 16384, as the stock files"""
    m = np.maximum(np.linalg.norm(cross.astype(np.float64), axis=1), 1e-9)
    shift = np.clip(np.floor(np.log2(16384.0 * m / 4096.0)), 0, 18).astype(np.int64)
    div = np.clip(np.round(4096.0 * 2.0 ** shift / m), 0, 32767).astype(np.int64)
    return shift, div


class TriGrid:
    """triangles (a, b, c (N, 3)) binned by their x/z boxes: what lies straight
    over or under points"""

    def __init__(self, a, b, c, cell):
        self.a, self.b, self.c, self.cell = a, b, c, cell
        n = len(a)
        lo = np.minimum(np.minimum(a, b), c)[:, [0, 2]]
        hi = np.maximum(np.maximum(a, b), c)[:, [0, 2]]
        self.org = lo.min(0) if n else np.zeros(2)
        i0 = np.floor((lo - self.org) / cell).astype(np.int64)
        i1 = np.floor((hi - self.org) / cell).astype(np.int64)
        self.dim = (i1.max(0) + 1) if n else np.ones(2, np.int64)
        nx, nz = i1[:, 0] - i0[:, 0] + 1, i1[:, 1] - i0[:, 1] + 1
        cnt = nx * nz
        tri = np.repeat(np.arange(n), cnt)
        k = np.arange(cnt.sum()) - np.repeat(np.cumsum(cnt) - cnt, cnt)
        cid = (i0[tri, 1] + k // nx[tri]) * self.dim[0] + i0[tri, 0] + k % nx[tri]
        order = np.argsort(cid, kind="stable")
        self.tri = tri[order]
        self.start = np.searchsorted(cid[order], np.arange(self.dim[0] * self.dim[1] + 1))

    def hits(self, P, chunk=100000):
        """(point index, triangle index, triangle height) of every triangle
        straight over or under each of P (M, 3)"""
        out_p, out_t, out_y = [], [], []
        for s in range(0, len(P), chunk):
            p = P[s:s + chunk]
            c = np.floor((p[:, [0, 2]] - self.org) / self.cell).astype(np.int64)
            ok = (c >= 0).all(1) & (c < self.dim).all(1)
            cid = np.where(ok, c[:, 1] * self.dim[0] + c[:, 0], 0)
            n = np.where(ok, self.start[cid + 1] - self.start[cid], 0)
            pi = np.repeat(np.arange(len(p)), n)
            k = np.arange(n.sum()) - np.repeat(np.cumsum(n) - n, n)
            ti = self.tri[self.start[cid[pi]] + k]
            A, B, C = self.a[ti], self.b[ti], self.c[ti]
            v0, v1, v2 = (B - A)[:, [0, 2]], (C - A)[:, [0, 2]], p[pi][:, [0, 2]] - A[:, [0, 2]]
            den = v0[:, 0] * v1[:, 1] - v1[:, 0] * v0[:, 1]
            good = np.abs(den) > 1e-9
            den = np.where(good, den, 1.0)
            u = (v2[:, 0] * v1[:, 1] - v1[:, 0] * v2[:, 1]) / den
            v = (v0[:, 0] * v2[:, 1] - v2[:, 0] * v0[:, 1]) / den
            e = 1e-5
            ins = good & (u >= -e) & (v >= -e) & (u + v <= 1 + e)
            out_p.append(pi[ins] + s)
            out_t.append(ti[ins])
            out_y.append((A[:, 1] + u * (B[:, 1] - A[:, 1]) + v * (C[:, 1] - A[:, 1]))[ins])
        if not out_p:
            return np.zeros(0, np.int64), np.zeros(0, np.int64), np.zeros(0)
        return np.concatenate(out_p), np.concatenate(out_t), np.concatenate(out_y)


def drawn_surface(key, out_dir):
    """the level as drawn, up-facing: opaque, alpha tested and decal triangles
    of the remaster mesh, facing up by their vertex normals -> (TriGrid, unit
    normals)"""
    pos, nrm, tris, dec = read_rtxm(pathlib.Path(out_dir) / f"{key}.rtxm", decals=True)
    t = np.concatenate([tris, dec])
    a, b, c = pos[t[:, 0]], pos[t[:, 1]], pos[t[:, 2]]
    fn = face_normals(a, b, c)
    flip = (fn * nrm[t].sum(1)).sum(1) < 0
    b, c = np.where(flip[:, None], c, b), np.where(flip[:, None], b, c)
    n = unit(face_normals(a, b, c))
    up = n[:, 1] > 0.3
    return TriGrid(a[up], b[up], c[up], 32.0), n[up]


def drawn_heights(vis, P, N):
    """height of the drawn surface Havok's points P (M, 3) (facing N) stand
    for: the nearest one within REACH facing alike, then the top one up to LIFT
    over it and LIFT_OVER over Havok (a decal on the road); NaN where none"""
    grid, vn = vis
    pi, ti, y = grid.hits(P)
    dh = y - P[pi, 1]
    ok = (np.abs(dh) <= REACH) & ((vn[ti] * N[pi]).sum(1) >= FACING)
    pi, y, dh = pi[ok], y[ok], dh[ok]
    out = np.full(len(P), -np.inf)
    if len(pi):
        order = np.lexsort((np.abs(dh), pi))
        first = order[np.r_[True, pi[order][1:] != pi[order][:-1]]]
        base = np.full(len(P), np.nan)
        base[pi[first]] = y[first]
        top = (y <= base[pi] + LIFT) & (y <= np.maximum(base[pi], P[pi, 1] + LIFT_OVER))
        np.maximum.at(out, pi[top], y[top])
    out[np.isinf(out)] = np.nan
    return out


def weld(a, b, c):
    """triangle soup -> vertices, triangles (corners within WELD one vertex;
    triangles collapsed by it dropped) and which of the input each is"""
    P = np.concatenate([a, b, c])
    key = np.round(P / WELD).astype(np.int64)
    _, inv, cnt = np.unique(key, axis=0, return_inverse=True, return_counts=True)
    inv = inv.ravel()
    V = np.zeros((len(cnt), 3))
    np.add.at(V, inv, P)
    V /= cnt[:, None]
    T = inv.reshape(3, -1).T
    keep = (T[:, 0] != T[:, 1]) & (T[:, 1] != T[:, 2]) & (T[:, 2] != T[:, 0])
    return V, T[keep], np.nonzero(keep)[0]


BARY = np.array([(i / 4, j / 4, 1 - (i + j) / 4) for i in range(5) for j in range(5 - i)
                 if max(i, j, 4 - i - j) < 4])  # (the 4 x 4 grid, corners left out)


def conform(V, T, ground, vis, line):
    """Havok's ground triangles (T[ground]) onto the drawn surface: split on
    their longest edges (every triangle on a split edge split with it, so no
    cracks or T-junctions) while the drawn surface between their corners is
    more than FIT off a flat triangle through them (those flatter than
    SPLIT_NY within CORRIDOR of the race line `line`, if any; any with an edge
    over MAX_EDGE), then their corners moved to it. Returns V, T and, per
    triangle, the input triangle it came from."""
    near = nf_level.Nearest(line) if len(line) else None
    src = np.arange(len(T))
    src_n = unit(face_normals(V[T[:, 0]], V[T[:, 1]], V[T[:, 2]]))
    target = np.full(len(V), np.nan)
    known = np.zeros(len(V), bool)
    fresh = ground.copy()
    for it in range(32):
        A, B, C = V[T[:, 0]], V[T[:, 1]], V[T[:, 2]]
        cr = face_normals(A, B, C)
        g = ground[src]
        # drawn heights at the ground's new corners
        need = np.zeros(len(V), bool)
        need[T[g].ravel()] = True
        need &= ~known
        if need.any():
            vn = np.zeros((len(V), 3))
            np.add.at(vn, T[g].ravel(), np.repeat(unit(cr[g]), 3, axis=0))
            idx = np.nonzero(need)[0]
            target[idx] = drawn_top(vis, V[idx], unit(vn[idx]))
            known[idx] = True
        height = np.where(np.isnan(target), V[:, 1], target)
        E = np.stack([np.linalg.norm(B - A, axis=1), np.linalg.norm(C - B, axis=1), np.linalg.norm(A - C, axis=1)], 1)
        longest = np.argmax(E, axis=1)
        lmax = E[np.arange(len(T)), longest]
        mark = lmax > MAX_EDGE
        flat = cr[:, 1] >= SPLIT_NY * np.linalg.norm(cr, axis=1)
        ev = np.nonzero(fresh & g & flat & (lmax > MIN_EDGE))[0]
        if near is not None and len(ev):
            ev = ev[near(((A + B + C) / 3)[ev])[1] - lmax[ev] / 2 <= CORRIDOR]
        if len(ev):
            P = np.einsum("sk,nkd->nsd", BARY, np.stack([V[T[ev, 0]], V[T[ev, 1]], V[T[ev, 2]]], 1))
            P[:, :, 1] = np.einsum("sk,nk->ns", BARY, height[T[ev]])
            N = np.repeat(unit(cr[ev]), len(BARY), axis=0)
            d = np.abs(drawn_heights(vis, P.reshape(-1, 3), N) - P.reshape(-1, 3)[:, 1]).reshape(len(ev), -1)
            mark[ev[np.nanmax(np.where(np.isnan(d), 0.0, d), axis=1) > FIT]] = True
        if not mark.any():
            break
        # edges to split: the marked triangles' longest, then every triangle
        # with a split edge has its longest split too (bisection stays conforming)
        nv = len(V)
        ek = np.sort(np.stack([T, np.roll(T, -1, axis=1)], 2), axis=2)
        ek = ek[:, :, 0] * nv + ek[:, :, 1]
        uniq, einv = np.unique(ek.ravel(), return_inverse=True)
        einv = einv.reshape(-1, 3)
        rows = np.arange(len(T))
        split = np.zeros(len(uniq), bool)
        split[einv[mark, longest[mark]]] = True
        while True:
            fix = split[einv].any(1) & ~split[einv[rows, longest]]
            if not fix.any():
                break
            split[einv[fix, longest[fix]]] = True
        se = np.nonzero(split)[0]
        mid = np.full(len(uniq), -1)
        mid[se] = nv + np.arange(len(se))
        V = np.vstack([V, (V[uniq[se] // nv] + V[uniq[se] % nv]) / 2])
        target = np.r_[target, np.full(len(se), np.nan)]
        known = np.r_[known, np.zeros(len(se), bool)]
        # each split triangle: v0 v1 its longest edge, then its other split edges
        sp = np.nonzero(split[einv].any(1))[0]
        r = longest[sp]
        v0, v1, v2 = T[sp, r], T[sp, (r + 1) % 3], T[sp, (r + 2) % 3]
        m01, m12, m20 = mid[einv[sp, r]], mid[einv[sp, (r + 1) % 3]], mid[einv[sp, (r + 2) % 3]]
        s12, s20 = m12 >= 0, m20 >= 0
        kids, ksrc = [], []
        for sel, tris in (
            (~s12 & ~s20, [(v0, m01, v2), (m01, v1, v2)]),
            (s12 & ~s20, [(v0, m01, v2), (m01, v1, m12), (m01, m12, v2)]),
            (~s12 & s20, [(v0, m01, m20), (m20, m01, v2), (m01, v1, v2)]),
            (s12 & s20, [(v0, m01, m20), (m20, m01, v2), (m01, v1, m12), (m01, m12, v2)]),
        ):
            for x, y, z in tris:
                kids.append(np.stack([x[sel], y[sel], z[sel]], 1))
                ksrc.append(src[sp[sel]])
        keep = np.ones(len(T), bool)
        keep[sp] = False
        T = np.concatenate([T[keep]] + kids)
        src = np.concatenate([src[keep]] + ksrc)
        fresh = np.r_[np.zeros(int(keep.sum()), bool), np.ones(len(T) - int(keep.sum()), bool)]
    moved = known & ~np.isnan(target)
    havok_y = V[:, 1].copy()
    V[moved, 1] = target[moved]
    V = smooth_ground(V, T, ground[src], src_n[src], moved, target, havok_y)
    return V, T, src


def drawn_top(vis, P, N):
    """drawn_heights where a kart's wheels ride: the highest of the drawn
    surface at P and FOOT round it, each carried to P along the plane N (P's
    surface) so a slope doesn't count; NaN where none"""
    ang = np.arange(6) * (np.pi / 3)
    offs = np.r_[[[0.0, 0.0]], np.stack([np.cos(ang), np.sin(ang)], 1) * FOOT]
    ny = np.maximum(N[:, 1], 0.2)
    est = np.full((len(P), len(offs)), np.nan)
    for k, (dx, dz) in enumerate(offs):
        Q = P + [dx, 0.0, dz]
        est[:, k] = drawn_heights(vis, Q, N) + (N[:, 0] * dx + N[:, 2] * dz) / ny
    out = np.full(len(P), np.nan)
    ok = ~np.isnan(est).all(1)
    out[ok] = np.nanmax(est[ok], axis=1)
    return out


def smooth_ground(V, T, g, src_n, moved, target, havok_y):
    """conformed ground (triangles T[g], moved corners `moved`) without the
    drawn surface's small steps: corners averaged with their neighbours
    (SMOOTH_STEPS, kept within SMOOTH_TOL of the drawn surface; corners not
    moved stay on Havok's), and any triangle tilted over MAX_TILT off its Havok
    triangle (normals src_n) has its corners pulled back towards Havok's"""
    tri = T[g]
    if not len(tri) or not moved.any():
        return V
    e = np.concatenate([tri[:, [0, 1]], tri[:, [1, 2]], tri[:, [2, 0]]])
    e = np.unique(np.sort(e, axis=1), axis=0)
    deg = np.bincount(e.ravel(), minlength=len(V)).astype(np.float64)
    lo, hi = target - SMOOTH_TOL, target + SMOOTH_TOL
    y = V[:, 1].copy()
    cos_max = np.cos(MAX_TILT)
    for _ in range(SMOOTH_STEPS):
        acc = np.zeros(len(V))
        np.add.at(acc, e[:, 0], y[e[:, 1]])
        np.add.at(acc, e[:, 1], y[e[:, 0]])
        avg = np.where(deg > 0, acc / np.maximum(deg, 1), y)
        y = np.where(moved, np.clip(0.5 * y + 0.5 * avg, lo, hi), y)
        P = V.copy()
        P[:, 1] = y
        n = unit(face_normals(P[tri[:, 0]], P[tri[:, 1]], P[tri[:, 2]]))
        tilted = (n * src_n[g]).sum(1) < cos_max
        if tilted.any():
            back = np.zeros(len(V), bool)
            back[tri[tilted].ravel()] = True
            back &= moved
            y = np.where(back, 0.5 * y + 0.5 * havok_y, y)
    V = V.copy()
    V[:, 1] = y
    return V


def nf_level_pak(tid):
    return next(lv[1] for lv in nf_tracks.available() if lv[0] == tid)


def havok_file(slot):
    """the level's StaticCollision_<level>.hkx (Nitro-Fueled's collision), or None"""
    import glob
    root, _ = nf_level.extract(nf_level_pak(slot))
    f = sorted(glob.glob(str(pathlib.Path(root) / "models/maps/Octane/*/StaticCollision_*.hkx")))
    return f[0] if f else None


def havok_triangles(slot, hkx, placed=False):
    """Havok's triangles in PS1 space, the game's winding (ground faces +Y),
    and each one's material name (placed=True: and the placement m, t)"""
    import nf_hkx
    ha, hb, hc, tags = nf_hkx.level_triangles(hkx)
    root, _ = nf_level.extract(nf_level_pak(slot))
    m, t, _ = nf_level.placement(slot, root)
    # metres -> NF units (inches) -> PS1 by the level's placement; right handed
    # counter-clockwise -> the game's winding (a, c, b), unless the placement
    # mirrors (hosted tracks: free yaw and scale)
    a, b, c = [(x * HAVOK_TO_NF) @ m.T + t for x in (ha, hb, hc)]
    if np.linalg.det(m) > 0:
        b, c = c, b
    names = np.array([nf_hkx.material(x) for x in tags])
    return (a, b, c, names, m, t) if placed else (a, b, c, names)


def top_hit(grid, P, above=100.0, below=400.0):
    """index of the top triangle of grid from `above` over each of P down to
    `below` under it, -1 where none"""
    pi, ti, y = grid.hits(P)
    dh = y - P[:, 1][pi]
    idx = np.nonzero((dh <= above) & (dh >= -below))[0]
    out = np.full(len(P), -1)
    if len(idx):
        o = idx[np.lexsort((y[idx], pi[idx]))]
        last = o[np.r_[pi[o][1:] != pi[o][:-1], True]]
        out[pi[last]] = ti[last]
    return out


def ground_under(grid, P, above=100.0, below=400.0):
    """height of the ground under each of P (the top one from `above` over it
    down to `below` under), else the lowest over it; NaN where none"""
    pi, _, y = grid.hits(P)
    dh = y - P[:, 1][pi]
    out = np.full(len(P), -np.inf)
    sel = (dh <= above) & (dh >= -below)
    np.maximum.at(out, pi[sel], y[sel])
    over = np.full(len(P), np.inf)
    np.minimum.at(over, pi[dh > above], y[dh > above])
    out = np.where(np.isinf(out), over, out)
    out[np.isinf(out)] = np.nan
    return out


RESPAWN_CELL = 300.0   # (NF units) respawn volumes' kill planes in squares this size
RESPAWN_CLEAR = 200.0  # a square with drivable ground in the volume or this far over its top: none there
RESPAWN_LINE = 250.0   # nor one this close to the race line (thin sheets at road height beside it)


def respawn_kills(slot, pm, pt, solid, line):
    """Nitro-Fueled's respawn trigger volumes (CRespawnTriggerComponentData:
    under bridges, jumps, Twilight Tour's huge boxes under the whole track) as
    kill planes over their top faces (triangles (N, 3, 3), PS1 space, facing
    up), in squares with no drivable ground (TriGrid `solid`) in the volume
    or just over it; upright volumes (walls) left out"""
    import nf_race
    root, _ = nf_level.extract(nf_level_pak(slot))
    vols = nf_race.trigger_volumes(nf_level.level_dir(root), lambda z, e, n: bool(z.reachable(e, {"CRespawnTriggerComponentData"})))
    out = []
    for _, pos, m, (centre, rot, half) in vols:
        to_nf = lambda L: (L @ rot.T + centre) @ m.T + pos  # noqa: E731
        up = unit((to_nf(np.array([[0.0, 0.0, 1.0]])) - to_nf(np.zeros((1, 3)))) @ pm.T)[0]
        if up[1] < 0.9 or half[2] <= 0:
            continue
        nx, ny = max(1, int(np.ceil(2 * half[0] / RESPAWN_CELL))), max(1, int(np.ceil(2 * half[1] / RESPAWN_CELL)))
        xs, ys = np.linspace(-half[0], half[0], nx + 1), np.linspace(-half[1], half[1], ny + 1)
        gx, gy = np.meshgrid(np.arange(nx), np.arange(ny), indexing="ij")
        gx, gy = gx.ravel(), gy.ravel()
        corner = lambda i, j, zz: to_nf(np.stack([xs[i], ys[j], np.full(len(i), zz)], 1)) @ pm.T + pt  # noqa: E731
        q = [corner(gx, gy, half[2]), corner(gx + 1, gy, half[2]), corner(gx + 1, gy + 1, half[2]), corner(gx, gy + 1, half[2])]
        top = (q[0] + q[1] + q[2] + q[3]) / 4
        bottom = (corner(gx, gy, -half[2])[:, 1] + corner(gx + 1, gy + 1, -half[2])[:, 1]) / 2
        # (the square's corners and middle: any drivable ground between the
        # volume's bottom and RESPAWN_CLEAR over its top keeps it open)
        busy = np.zeros(len(top), bool)
        for P in q + [top]:
            pi, _, y = solid.hits(P)
            near = (y >= bottom[pi] - 1.0) & (y <= P[pi, 1] + RESPAWN_CLEAR / nf_level.NF_SCALE)
            busy[pi[near]] = True
        keep = ~busy & np.all(np.abs(np.stack(q)) < 32000, axis=(0, 2))
        if len(line) and keep.any():
            keep[keep] = nearest(line, top[keep])[1] > RESPAWN_LINE
        for t in ((q[0], q[1], q[2]), (q[0], q[2], q[3])):
            tri = np.stack([x[keep] for x in t], 1)
            flip = face_normals(tri[:, 0], tri[:, 1], tri[:, 2])[:, 1] < 0
            tri[flip] = tri[flip][:, [0, 2, 1]]
            out.append(np.round(tri))
    return np.concatenate(out) if out else np.zeros((0, 3, 3))


def _line_y(line, P):
    """the race line's height nearest (3D) each of P, and the x/z distance to it"""
    j, _ = nearest(line, P)
    return line[j, 1], np.hypot(*(P[:, [0, 2]] - line[j][:, [0, 2]]).T)


def road_kills(a, b, c, kill, line):
    """Havok kill triangles within KILL_NEAR (x/z) of the race line and less
    than KILL_UNDER under it"""
    out = np.zeros(len(a), bool)
    if not len(line) or not kill.any():
        return out
    k = np.nonzero(kill)[0]
    cen = (a[k] + b[k] + c[k]) / 3
    # (nearest in x/z: the line's point over / under the plane)
    j, _ = nearest(line[:, [0, 2]], cen[:, [0, 2]])
    dxz = np.hypot(*(cen[:, [0, 2]] - line[j][:, [0, 2]]).T)
    out[k] = (dxz < KILL_NEAR) & (cen[:, 1] > line[j, 1] - KILL_UNDER)
    return out


def line_blockers(a, b, c, skip, line):
    """walls (not `skip`) the race line runs through, 20-60 over it"""
    out = np.zeros(len(a), bool)
    if len(line) < 2:
        return out
    w = np.nonzero(~skip)[0]
    j, d = nearest(line, (a[w] + b[w] + c[w]) / 3)
    near = d < 400.0
    w, j = w[near], j[near]
    n = len(line)
    for off in range(-4, 4):
        p0, p1 = line[(j + off) % n], line[(j + off + 1) % n]
        for h in (20.0, 40.0, 60.0):
            o, dv = p0 + [0, h, 0], p1 - p0
            e1, e2 = b[w] - a[w], c[w] - a[w]
            pv = np.cross(dv, e2)
            det = (e1 * pv).sum(1)
            ok = np.abs(det) > 1e-9
            inv = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
            tv = o - a[w]
            u = (tv * pv).sum(1) * inv
            qv = np.cross(tv, e1)
            v = (dv * qv).sum(1) * inv
            t = (e2 * qv).sum(1) * inv
            out[w] |= ok & (u >= 0) & (v >= 0) & (u + v <= 1) & (t >= 0) & (t <= 1)
    return out


def road_terrain(a, b, c, sel, nm, terrain, line):
    """terrain with the road's materials (each under ROAD_SHARE of the race
    line or more) never slow"""
    if not len(line) or not sel.any():
        return terrain
    idx = np.nonzero(sel)[0]
    hit = top_hit(TriGrid(a[idx], b[idx], c[idx], 128.0), line + [0, 60, 0], above=60.0, below=300.0)
    names, counts = np.unique(nm[idx][hit[hit >= 0]], return_counts=True)
    road = set(names[counts >= ROAD_SHARE * max(int((hit >= 0).sum()), 1)].tolist())
    slow = np.isin(terrain, list(SLOW_TERRAIN)) & np.isin(nm, list(road))
    if slow.any():
        print(f"  road materials {sorted(road)}: {int(slow.sum())} slow triangles played as hardpack")
    return np.where(slow, T_HARDPACK, terrain).astype(np.uint8)


def camera_floor(a, b, c, line):
    """ground the camera may stand over: not more than CAMERA_OVER over the
    race line nearest it (a roof's or an overpass' top over the road would lift
    the camera into a top-down view)"""
    if not len(line):
        return np.ones(len(a), bool)
    cen = (a + b + c) / 3
    ly, _ = _line_y(line, cen)
    return cen[:, 1] <= ly + CAMERA_OVER


def build(slot, out_dir):
    hkx = havok_file(slot)
    if hkx:
        return build_havok(slot, hkx, out_dir)
    return build_visual(slot, out_dir)


def build_havok(slot, hkx, out_dir):
    key = nf_tracks.key(slot)
    native = nf_tracks.native(slot) is not None
    # (a native track: its host's level, only for the instances moved onto it)
    S = stock_level(nf_tracks.native_host(slot) if native else slot)
    q, sv = S["q"], S["v"]
    sp = sv["pos"].astype(np.float64)
    sidx = q["index"].astype(np.int64)
    scen = sp[sidx[:, :4]].mean(1)
    sflags = q["flags"].astype(np.int64)
    print(f"{key}: {pathlib.Path(hkx).name}; PS1 level {len(q)} quadblocks, {len(S['hitboxes'])} hitboxes")

    a, b, c, names, pm, pt = havok_triangles(slot, hkx, placed=True)
    n0 = len(a)
    ny = unit(face_normals(a, b, c))[:, 1]
    liquid = np.array([is_liquid(x) for x in names])
    respawn = names == "Respawn"
    solid_ground = ~liquid & ~respawn & (ny >= GROUND_NY)
    # (glass isn't in the drawn surface: kept where Havok has it)
    fit = solid_ground & (names != "Glass")

    # liquids over a bed: driven through on the bed (as the stock levels' water)
    cen = (a + b + c) / 3
    bed_grid = TriGrid(a[solid_ground], b[solid_ground], c[solid_ground], 128.0)
    pi, ti, y = bed_grid.hits(cen[liquid])
    dh = cen[liquid][pi, 1] - y
    has_bed = np.zeros(int(liquid.sum()), bool)
    has_bed[pi[(dh > 1.0) & (dh <= LIQUID_BED)]] = True
    sheet = np.zeros(n0, bool)
    sheet[np.nonzero(liquid)[0][has_bed]] = True
    sheet_grid = TriGrid(a[sheet], b[sheet], c[sheet], 128.0)
    pi, ti, y = sheet_grid.hits(cen[solid_ground])
    dh = y - cen[solid_ground][pi, 1]
    ok = (dh > 1.0) & (dh <= LIQUID_BED)
    bed_of = np.full(int(solid_ground.sum()), -1)
    bed_of[pi[ok]] = np.nonzero(sheet)[0][ti[ok]]
    bed = np.full(n0, -1)
    bed[np.nonzero(solid_ground)[0]] = bed_of  # (the liquid over it)
    for nm in sorted(set(names[sheet])):
        print(f"  {nm}: {int((sheet & (names == nm)).sum())} triangles over a bed: driven through")
    keep = ~sheet
    print(f"  {n0} Havok triangles: {int(respawn.sum())} respawn, {int((liquid & keep).sum())} solid liquid, "
          f"{int((bed >= 0).sum())} under a liquid")

    # the ground onto the level as drawn (a liquid with no bed in Havok: down
    # onto the bed drawn under it, the drawn liquid isn't part of that)
    vis = drawn_surface(key, out_dir)
    idx = np.nonzero(keep)[0]
    V, T, wsrc = weld(a[idx], b[idx], c[idx])
    idx = idx[wsrc]
    if native:
        import nf_race
        line = nf_race.race_line(slot, pm, pt, lambda P: ground_under(bed_grid, P))
    else:
        line = nf_level.ps1_track_points(nf_tracks.host_lev(slot).read_bytes())
    V, T, src = conform(V, T, (fit | (liquid & (ny >= GROUND_NY)))[idx], vis, line)
    src = idx[src]
    V = np.round(V)
    a, b, c = V[T[:, 0]], V[T[:, 1]], V[T[:, 2]]
    inside = np.all(np.abs(np.stack([a, b, c])) < 32000, axis=(0, 2))
    cross = face_normals(a, b, c).astype(np.int64)
    ok = inside & np.any(cross != 0, axis=1)
    a, b, c, cross, src = a[ok], b[ok], c[ok], cross[ok], src[ok]
    nt = len(a)
    # ground or wall as Havok has it (a conformed triangle tilts a little)
    ground = ny[src] >= GROUND_NY
    nm = names[src]
    kill = respawn[src]
    under = bed[src]
    # kill planes beside / over the road and walls standing across it go
    drop = road_kills(a, b, c, kill, line) | line_blockers(a, b, c, ground | kill, line)
    if drop.any():
        print(f"  {int((drop & kill).sum())} kill triangles by the road, {int((drop & ~kill).sum())} walls across the race line dropped")
        k = ~drop
        a, b, c, cross, src, nm, kill, under, ground = a[k], b[k], c[k], cross[k], src[k], nm[k], kill[k], under[k], ground[k]
        nt = len(a)
    if native:  # (an invisible road where Nitro-Fueled carries the karts: nf_race.bridges)
        import nf_race
        sel = ground & ~kill
        br = nf_race.bridges(line, TriGrid(a[sel], b[sel], c[sel], 128.0))
        if len(br):
            a, b, c = np.concatenate([a, br[:, 0]]), np.concatenate([b, br[:, 1]]), np.concatenate([c, br[:, 2]])
            cross = face_normals(a, b, c).astype(np.int64)
            nm = np.r_[nm, np.full(len(br), "Default")]
            kill = np.r_[kill, np.zeros(len(br), bool)]
            under = np.r_[under, np.full(len(br), -1)]
            nt = len(a)
            ground = np.r_[ground, np.ones(len(br), bool)]
            print(f"  {len(br)} triangles of invisible road where the karts are carried")
    # respawn volumes: kill planes over them (where nothing is driven on)
    sel = ground & ~kill
    rk = respawn_kills(slot, pm, pt, TriGrid(a[sel], b[sel], c[sel], 128.0), line)
    if len(rk):
        a, b, c = np.concatenate([a, rk[:, 0]]), np.concatenate([b, rk[:, 1]]), np.concatenate([c, rk[:, 2]])
        cross = face_normals(a, b, c).astype(np.int64)
        nm = np.r_[nm, np.full(len(rk), "Respawn")]
        kill = np.r_[kill, np.ones(len(rk), bool)]
        under = np.r_[under, np.full(len(rk), -1)]
        nt = len(a)
        ground = np.r_[ground, np.ones(len(rk), bool)]
    print(f"  {len(rk)} kill triangles over respawn volumes")
    boost = np.array([BOOST.get(x, 0) for x in nm])
    # turbo pads that are trigger volumes in Nitro-Fueled (ramps' and jumps'
    # pads, the DLC levels' all): the ground in them
    import nf_race
    root, _ = nf_level.extract(nf_level_pak(slot))
    vols = nf_race.boost_volumes(nf_level.level_dir(root))
    # the turbo pads as drawn: their own triggers (exact shape: a ramp's pad
    # boosts where it is, not over the volume round it)
    sel = ground & ~kill
    drawn_pads, drawn_kind = pad_triggers(pathlib.Path(out_dir) / f"{key}.rtxm", TriGrid(a[sel], b[sel], c[sel], 128.0))
    if len(drawn_pads) and vols:
        pc_nf = (drawn_pads.mean(1) - pt) @ np.linalg.inv(pm).T
        # (a drawn pad in a super volume is a super turbo: the big jump's ramp)
        for v in vols:
            if v[3] == 2:
                drawn_kind = np.where(nf_race.in_volume(v[0], v[1], v[2], pc_nf, 60.0), 2, drawn_kind)
        vols = [v for v in vols if not nf_race.in_volume(v[0], v[1], v[2], pc_nf, 60.0).any()]
    print(f"  {len(drawn_pads)} trigger triangles of drawn turbo pads")
    if vols:
        tc_nf = ((a + b + c) / 3 - pt) @ np.linalg.inv(pm).T
        boost = np.where(ground & ~kill, np.maximum(boost, nf_race.boost_at_volumes(vols, tc_nf)), boost)
        print(f"  {len(vols)} turbo pad volumes: {int((boost > 0).sum())} ground triangles in them")
    print(f"  conformed: {nt} triangles ({int(ground.sum())} ground, {int((~ground).sum())} wall), "
          f"{int(boost.astype(bool).sum())} turbo pad")

    # attributes
    terrain = np.array([TERRAIN.get(x, T_ASPHALT) for x in nm], np.uint8)
    terrain = np.where(under >= 0, np.array([TERRAIN.get(x, T_WATER) for x in names[np.maximum(under, 0)]], np.uint8), terrain)
    terrain = road_terrain(a, b, c, ground & ~kill, nm, terrain, line)
    flags = np.where(ground, Q_GROUND | np.where(camera_floor(a, b, c, line), Q_CAMERA, 0), Q_WALL)
    flags = np.where(kill, FLAGS_KILL, flags)
    terrain = np.where(kill, 0, terrain)
    # checkpoint (race data: the restart point each spot respawns at) from the
    # nearest PS1 quadblock of the kind (native tracks: their own restart
    # points, below)
    tc = (a + b + c) / 3
    checkpoint = np.full(nt, 0xFF, np.uint8)
    for kind_flag, sel in (() if native else ((Q_GROUND, ground), (Q_WALL, ~ground))):
        cand = np.nonzero((sflags & kind_flag) != 0)[0]
        if len(cand) and sel.any():
            j, _ = nearest(scen[cand], tc[sel])
            checkpoint[sel] = q["checkpoint"][cand[j]]

    # turbo pads: a trigger over each pad triangle
    pads = np.nonzero(boost > 0)[0]
    a, b, c = np.concatenate([a, a[pads]]), np.concatenate([b, b[pads]]), np.concatenate([c, c[pads]])
    cross = np.concatenate([cross, cross[pads]])
    flags = np.r_[flags, np.full(len(pads), FLAGS_TRIGGER)]
    terrain = np.r_[terrain, boost[pads].astype(np.uint8)]
    checkpoint = np.r_[checkpoint, checkpoint[pads]]
    water = np.r_[(under >= 0) | np.array([is_liquid(x) for x in nm]), np.zeros(len(pads), bool)]
    if len(drawn_pads):
        a, b, c = np.concatenate([a, drawn_pads[:, 0]]), np.concatenate([b, drawn_pads[:, 1]]), np.concatenate([c, drawn_pads[:, 2]])
        cross = np.concatenate([cross, face_normals(drawn_pads[:, 0], drawn_pads[:, 1], drawn_pads[:, 2]).astype(np.int64)])
        flags = np.r_[flags, np.full(len(drawn_pads), FLAGS_TRIGGER)]
        terrain = np.r_[terrain, drawn_kind.astype(np.uint8)]
        # (checkpoint: the nearest ground triangle's)
        j, _ = nearest(((a[:nt] + b[:nt] + c[:nt]) / 3), drawn_pads.mean(1))
        checkpoint = np.r_[checkpoint, checkpoint[j]]
        water = np.r_[water, np.zeros(len(drawn_pads), bool)]
    nq = len(a)

    Q = np.zeros(nq, QUAD_DT)
    Q["flags"] = flags
    Q["terrain"] = terrain
    Q["checkpoint"] = checkpoint
    shift, div = normal_scale(cross)
    Q["shift"] = shift
    Q["div"][:, 8] = div
    tp = np.stack([a, b, c], 1)
    Q["bmin"] = tp.min(1)
    Q["bmax"] = tp.max(1)
    corners = np.zeros((nq, 9, 3))
    corners[:, 0], corners[:, 1], corners[:, 2], corners[:, 3] = a, b, c, c
    corners[:, 4:] = a[:, None]
    kind = np.ones(nq, np.uint8)

    g = ((Q["flags"] & Q_GROUND) != 0) & ((Q["flags"] & (Q_TRIGGER | Q_KILL)) == 0)
    nf_grid = TriGrid(a[g], b[g], c[g], 128.0)
    if native:
        return finish_native(slot, key, S, Q, corners, kind, water, out_dir, pm, pt, nf_grid, g, line)

    # the start grid and restart points onto the NF ground: moved by how far
    # the NF ground under them is from the PS1 floor (not where either is
    # missing or they're far apart: a gap, a different surface)
    col = np.nonzero((sflags & Q_GROUND) != 0)[0]
    st = np.concatenate([sidx[col][:, list(tr)] for tr in ((0, 4, 5), (4, 6, 5), (6, 4, 1), (5, 6, 2), (8, 6, 7), (7, 3, 8), (1, 7, 6), (2, 6, 8))])
    ps1_grid = TriGrid(sp[st[:, 0]], sp[st[:, 1]], sp[st[:, 2]], 128.0)

    def lift(P):
        if not len(P):
            return np.zeros(0, np.int16)
        shift = ground_under(nf_grid, P) - ground_under(ps1_grid, P)
        y = P[:, 1] + np.where(np.abs(np.nan_to_num(shift, nan=1e9)) <= SPAWN_SHIFT, shift, 0.0)
        return np.clip(np.round(y), -32768, 32767).astype(np.int16)
    spawn_y, restart_y = lift(S["spawn"]), lift(S["restart"])
    dsp = spawn_y - S["spawn"][:, 1]
    drs = restart_y - S["restart"][:, 1] if len(restart_y) else np.zeros(1)
    print(f"  start grid lifted {dsp.min():+.0f}..{dsp.max():+.0f}, restart points median {np.median(drs):+.0f} "
          f"(max |{np.abs(drs).max():.0f}|)")

    write_coll(key, S, Q, corners, kind, water, out_dir, spawn_y, restart_y)

def finish_native(slot, key, S, Q, corners, kind, water, out_dir, pm, pt, nf_grid, g, line):
    """a native track's race data (nf_race.py) on its collision: checkpoints by
    the restart points, the host's crates and fruit moved, the race section"""
    import nf_race
    tri = corners[:, :3]
    ter = Q["terrain"][g]
    trig = (Q["flags"] & Q_TRIGGER) != 0
    pad_grid = TriGrid(tri[trig, 0], tri[trig, 1], tri[trig, 2], 128.0)
    pad_kind = Q["terrain"][trig]

    def terrain_at(P):
        i = top_hit(nf_grid, P)
        return np.where(i >= 0, ter[np.maximum(i, 0)], 0)

    def boost_at(P):
        i = top_hit(pad_grid, P, above=60.0, below=160.0)
        return np.where(i >= 0, pad_kind[np.maximum(i, 0)], 0) if len(pad_kind) else np.zeros(len(P), np.int64)

    race = nf_race.build(slot, pm, pt, lambda P: ground_under(nf_grid, P), terrain_at, boost_at)
    nodes = race["nodes"]
    nn = len(nodes)
    # checkpoint: the restart point a spot is at (progress along the race line)
    rl = race["line"]
    s_line, L = nf_race.arc(rl)
    s0 = nf_race.project(rl, nodes["pos"][:1].astype(np.float64))[0]
    tc = tri.mean(1)
    j, d = nearest(rl, tc)
    prog = (s_line[j] - s0) % L
    Q["checkpoint"] = np.where(d < 1500.0, np.clip((prog / (L / nn)).astype(np.int64), 0, nn - 1), 0xFF)

    # hitboxes of the moved crates and fruit, where they now are
    body = nf_tracks.host_lev(nf_tracks.native_host(slot)).read_bytes()[4:]
    moved = {off: p for off, p in race["moves"] if tuple(p) != nf_race.OUT_OF_WORLD}
    hbs = []
    for raw in S["hitboxes"]:
        inst = struct.unpack_from("<I", raw, 0x1C)[0]
        if not inst or (inst - 1) not in moved:
            continue
        old = np.frombuffer(body, "<i2", 3, inst - 1 + 0x30).astype(np.int64)
        dlt = moved[inst - 1] - old
        r = bytearray(raw)
        box = np.frombuffer(bytes(r[4:16]), "<i2").astype(np.int64).reshape(2, 3) + dlt
        r[4:16] = np.clip(box, -32768, 32767).astype("<i2").tobytes()
        cen = np.frombuffer(bytes(r[0x10:0x16]), "<i2").astype(np.int64) + dlt
        r[0x10:0x16] = np.clip(cen, -32768, 32767).astype("<i2").tobytes()
        hbs.append(bytes(r))
    S = dict(S, hitboxes=hbs)

    fr = race["paths"]
    gb = [len(set(p["back"].tolist())) for p in fr]
    print(f"  race: line {L:.0f} units from the {race['start_from']}, {nn} restart points (track length {int(nodes['dist'][0]) * 8}), "
          f"nav paths {[len(p) for p in fr]} frames ({gb} restart points each), {race['moved']} of the host's "
          f"{race['host_items']} crates / fruit onto {race['items']}, {len(hbs)} hitboxes, "
          f"{int((Q['checkpoint'] == 0xFF).sum())} quads without a checkpoint")
    write_coll(key, S, Q, corners, kind, water, out_dir, np.zeros(0, np.int16), np.zeros(0, np.int16), race=race)


def build_visual(slot, out_dir):
    """(no Havok file) the converted NF level's opaque triangles, attributes and
    the barriers it lacks from the PS1 level"""
    key = nf_tracks.key(slot)
    S = stock_level(slot)
    q, sv = S["q"], S["v"]
    sp = sv["pos"].astype(np.float64)
    sidx = q["index"].astype(np.int64)
    scen = sp[sidx[:, :4]].mean(1)
    sflags = q["flags"].astype(np.int64)
    print(f"{key}: stock {len(q)} quadblocks, {len(sv)} vertices, {len(S['hitboxes'])} hitboxes")

    pos, nrm, tris = read_rtxm(pathlib.Path(out_dir) / f"{key}.rtxm")
    P = np.round(pos)
    fn = face_normals(P[tris[:, 0]], P[tris[:, 1]], P[tris[:, 2]])
    flip = (fn * nrm[tris].sum(1)).sum(1) < 0
    tris[flip] = tris[flip][:, [0, 2, 1]]
    a, b, c = P[tris[:, 0]], P[tris[:, 1]], P[tris[:, 2]]
    inside = np.all(np.abs(np.stack([a, b, c])) < 32000, axis=(0, 2))
    a, b, c = a[inside], b[inside], c[inside]
    cross = face_normals(a, b, c).astype(np.int64)
    ok = np.any(cross != 0, axis=1)
    a, b, c, cross = a[ok], b[ok], c[ok], cross[ok]
    nt = len(a)
    ny = cross[:, 1] / np.maximum(np.linalg.norm(cross, axis=1), 1e-9)
    ground = ny >= GROUND_NY
    print(f"  NF: {nt} solid triangles ({int(ground.sum())} ground, {int((~ground).sum())} wall)")

    special = (sflags & (Q_TRIGGER | Q_KILL | Q_NO_RESPONSE)) != 0
    wet = np.isin(q["terrain"], [T_WATER, T_FASTWATER])
    collide = (sflags & Q_COLLIDE) != 0
    _, dcov = nearest(surface_points(a, b, c, 40.0), scen)
    is_floor = (sflags & Q_GROUND) != 0
    keep = collide & (special | (wet & (dcov > COVER)) | (~wet & is_floor & (dcov > COVER_FLOOR)) | (~wet & ~is_floor & (dcov > COVER)))
    line = nf_level.ps1_track_points(nf_tracks.host_lev(slot).read_bytes())
    if len(line):
        bmin, bmax = q["bmin"].astype(np.float64), q["bmax"].astype(np.float64)
        _, dl = nearest(line, np.clip(line[nearest(line, scen)[0]], bmin, bmax))
        keep &= ~(keep & ~special & ~wet & ~is_floor & (dl < LINE_CLEAR))
    kept = np.nonzero(keep)[0]

    tc = (a + b + c) / 3
    src = np.zeros(nt, np.int64)
    for kind_flag, sel in ((Q_GROUND, ground), (Q_WALL, ~ground)):
        cand = np.nonzero(((sflags & kind_flag) != 0) & ~special)[0]
        if len(cand) and sel.any():
            j, _ = nearest(scen[cand], tc[sel])
            src[sel] = cand[j]
    shift, div = normal_scale(cross)
    nq = nt + len(kept)
    Q = np.zeros(nq, QUAD_DT)
    s = q[src]
    Q["flags"][:nt] = (s["flags"].astype(np.int64) & (0xFFFF & ~(Q_TRIGGER | Q_KILL | Q_NO_RESPONSE | Q_COLLIDE | Q_CAMERA))) | np.where(ground, Q_GROUND | Q_CAMERA, Q_WALL)
    # sides of things collide from both sides (thin NF geometry is often one surface)
    Q["dol"][:nt] = np.where(np.abs(ny) < GROUND_NY, DOUBLE_SIDED, 0)
    for f in ("terrain", "wint", "wvan", "mulny", "checkpoint"):
        Q[f][:nt] = s[f]
    Q["shift"][:nt] = shift
    Q["div"][:nt, 8] = div
    tp = np.stack([a, b, c], 1)
    Q["bmin"][:nt] = tp.min(1)
    Q["bmax"][:nt] = tp.max(1)
    k = q[kept]
    for f in ("flags", "dol", "doh", "terrain", "wint", "wvan", "mulny", "checkpoint", "shift", "div", "bmin", "bmax"):
        Q[f][nt:] = k[f]
    corners = np.zeros((nq, 9, 3))
    corners[:nt, 0], corners[:nt, 1], corners[:nt, 2], corners[:nt, 3] = a, b, c, c
    corners[:nt, 4:] = a[:, None]
    corners[nt:] = sp[sidx[kept]]
    kind = np.r_[np.ones(nt, np.uint8), np.zeros(len(kept), np.uint8)]
    water = np.r_[np.isin(Q["terrain"][:nt], [T_WATER, T_FASTWATER]), S["water"][kept]]
    write_coll(key, S, Q, corners, kind, water, out_dir, S["spawn"][:, 1].astype(np.int16),
               S["restart"][:, 1].astype(np.int16))


def build_bsp(cen, lo, hi, groups):
    """BSP over quads (centres, boxes), each group (an index array) its own
    subtree under the root; leaves of up to LEAF_QUADS. Returns nodes and the
    quad order (each leaf a contiguous range)."""
    nodes = [None]
    order = []
    sys.setrecursionlimit(10000)

    def node(ids):
        i = len(nodes)
        nodes.append(None)
        box = (lo[ids].min(0), hi[ids].max(0))
        if len(ids) <= LEAF_QUADS:
            nodes[i] = {"leaf": True, "box": box, "first": len(order), "n": len(ids)}
            order.extend(ids.tolist())
            return i
        c = cen[ids]
        ax = int(np.argmax(c.max(0) - c.min(0)))
        h = len(ids) // 2
        part = np.argpartition(c[:, ax], h)
        nodes[i] = {"leaf": False, "box": box, "axis": ax, "dist": float(c[part[h], ax])}
        left = node(ids[part[:h]])
        right = node(ids[part[h:]])
        nodes[i]["child"] = (left, right)
        return i

    kids = [node(g) for g in groups if len(g)]
    allq = np.concatenate([g for g in groups if len(g)])
    nodes[0] = {"leaf": False, "box": (lo[allq].min(0), hi[allq].max(0)), "axis": 0, "dist": 0.0,
                "child": (kids[0], kids[1] if len(kids) > 1 else NO_CHILD)}
    return nodes, np.array(order, np.int64)


def water_leaf(n, water):
    return bool(n["leaf"] and n["n"] and water[n["first"]])


def race_bytes(race, ramp_default=None):
    """the race section (version 3, native tracks): u32 8 + s16 DriverSpawn[8][6];
    u32 n + CheckpointNode[n]; u32 3 + 3 x (u32 frames + NavHeader + NavFrame[]);
    u32 n + n x (u32 InstDef body offset, s16 pos[3], s16 0); each 4 aligned"""
    import nf_race
    out = bytearray()

    def put(b):
        out.extend(b)
        out.extend(b"\0" * (-len(out) % 4))
    put(struct.pack("<I", 8) + race["spawn"].astype("<i2").tobytes())
    put(struct.pack("<I", len(race["nodes"])) + race["nodes"].tobytes())
    put(struct.pack("<I", len(race["paths"])))
    for fr in race["paths"]:
        hdr = struct.pack("<hhiI", nf_race.NAV_MAGIC, len(fr), int(fr["pos"][0][1]) << 16, 0) + race["ramp"].astype("<i2").tobytes()
        assert len(hdr) == 0x4C
        put(struct.pack("<I", len(fr)) + hdr + fr.tobytes())
    put(struct.pack("<I", len(race["moves"])) + b"".join(struct.pack("<I3hh", off, *[int(v) for v in p], 0) for off, p in race["moves"]))
    # (optional, last) portals: u32 n + n x s16 entry[3], exit[3], exit yaw, radius
    w = race.get("warps", [])
    put(struct.pack("<I", len(w)) + b"".join(struct.pack("<8h", *[int(v) for v in a], *[int(v) for v in b], yaw, nf_race.WARP_RADIUS) for a, b, yaw in w))
    return bytes(out)


def write_coll(key, S, Q, corners, kind, water, out_dir, spawn_y, restart_y, race=None):
    """BSP (water leaves apart: their flag gives karts the water line), each
    leaf's own vertices, hitboxes, the file"""
    sv = S["v"]
    sp = sv["pos"].astype(np.float64)
    nq = len(Q)
    Q["block"] = (np.arange(nq) & 0x7FFF).astype(np.int16)
    lo, hi = Q["bmin"].astype(np.float64), Q["bmax"].astype(np.float64)
    cen = (lo + hi) / 2
    ids = np.arange(nq)
    nodes, order = build_bsp(cen, lo, hi, [ids[~water], ids[water]])
    Q, corners, kind, water = Q[order], corners[order], kind[order], water[order]
    leaves = [i for i, n in enumerate(nodes) if n["leaf"]]
    print(f"  BSP: {len(nodes)} nodes, {len(leaves)} leaves")

    leaf_of = np.zeros(nq, np.int64)
    for li, i in enumerate(leaves):
        n = nodes[i]
        leaf_of[n["first"]:n["first"] + n["n"]] = li
    rows = np.column_stack([np.repeat(leaf_of, 9), corners.reshape(-1, 3).astype(np.int64)])
    uniq, inv = np.unique(rows, axis=0, return_inverse=True)
    inv = inv.reshape(nq, 9)
    leaf_start = np.searchsorted(uniq[:, 0], np.arange(len(leaves)))
    base = leaf_start[leaf_of]
    local = inv - base[:, None]
    assert local.min() >= 0 and local.max() < 65536
    Q["index"] = local.astype(np.uint16)
    V = np.zeros(len(uniq), VERT_DT)
    V["pos"] = uniq[:, 1:].astype(np.int16)
    j, _ = nearest(sp, uniq[:, 1:].astype(np.float64))  # (vertex colours light the karts on GL)
    V["hi"], V["lo"] = sv["hi"][j], sv["lo"][j]

    leaf_hb = {i: [] for i in leaves}
    dry = [i for i in leaves if not water_leaf(nodes[i], water)]
    lc = np.array([(nodes[i]["box"][0] + nodes[i]["box"][1]) / 2 for i in dry])
    for raw in S["hitboxes"]:
        bmin = np.frombuffer(raw[4:10], "<i2").astype(np.float64)
        bmax = np.frombuffer(raw[10:16], "<i2").astype(np.float64)
        cc = (bmin + bmax) / 2
        li = dry[int(np.argmin(np.linalg.norm(lc - cc, axis=1)))]
        leaf_hb[li].append(raw)
        b0, b1 = nodes[li]["box"]
        nodes[li]["box"] = (np.minimum(b0, bmin), np.maximum(b1, bmax))

    def grow(i):
        n = nodes[i]
        if n["leaf"]:
            return n["box"]
        bx = [grow(ch) for ch in n["child"] if ch != NO_CHILD]
        n["box"] = (np.minimum.reduce([x[0] for x in bx]), np.maximum.reduce([x[1] for x in bx]))
        return n["box"]
    grow(0)
    hb_blob, hb_at = bytearray(), {}
    for li, lst in leaf_hb.items():
        if lst:
            hb_at[li] = len(hb_blob) + 1
            hb_blob += b"".join(lst) + b"\0" * BSPN

    N = bytearray(len(nodes) * BSPN)
    for i, n in enumerate(nodes):
        o = i * BSPN
        bmin = np.clip(np.floor(n["box"][0]), -32768, 32767).astype("<i2")
        bmax = np.clip(np.ceil(n["box"][1]), -32768, 32767).astype("<i2")
        N[o + 4:o + 16] = bmin.tobytes() + bmax.tobytes()
        if n["leaf"]:
            struct.pack_into("<Hh", N, o, LEAF | (LEAF_WATER if water_leaf(n, water) else 0), i & 0x7FFF)
            struct.pack_into("<4I", N, o + 0x10, 0, hb_at.get(i, 0), n["n"], n["first"])
        else:
            struct.pack_into("<Hh", N, o, 0, i & 0x7FFF)
            axis = [0, 0, 0]
            axis[n["axis"]] = 4096
            c0, c1 = n["child"]
            struct.pack_into("<4h4H", N, o + 0x10, *axis, int(np.clip(n["dist"], -32768, 32767)),
                             c0 & 0xFFFF, c1 & 0xFFFF, (c0 >> 16) & 0xFFFF, (c1 >> 16) & 0xFFFF)

    rb = race_bytes(race) if race else b""
    parts = [V.tobytes(), Q.tobytes(), base.astype("<u4").tobytes(), kind.tobytes(), bytes(N), bytes(hb_blob),
             np.asarray(spawn_y, "<i2").tobytes(), np.asarray(restart_y, "<i2").tobytes(), rb]
    offs, at = [], struct.calcsize(HEADER)
    for p_ in parts:
        at += -at % 4
        offs.append(at)
        at += len(p_)
    hdr = struct.pack(HEADER, b"RTXC", VERSION, len(V), nq, len(nodes), len(hb_blob), *offs[:6], S["size"],
                      len(spawn_y), offs[6], len(restart_y), offs[7], len(rb), offs[8])
    out = pathlib.Path(out_dir) / f"{key}.coll"
    tmp = pathlib.Path(str(out) + ".tmp")
    with open(tmp, "wb") as f:
        f.write(hdr)
        for o, p_ in zip(offs, parts):
            f.write(b"\0" * (o - f.tell()))
            f.write(p_)
    tmp.replace(out)
    print(f"  {out}: {nq} quadblocks, {len(V)} vertices, {len(nodes)} BSP nodes, "
          f"{sum(map(len, leaf_hb.values()))} hitboxes, {out.stat().st_size / 1e6:.0f} MB")


def build_all(out_dir):
    """every nf_tracks.collision_tracks() track whose NF level is converted, when stale"""
    for slot in nf_tracks.collision_tracks():
        key = nf_tracks.key(slot)
        rtxm, out = pathlib.Path(out_dir) / f"{key}.rtxm", pathlib.Path(out_dir) / f"{key}.coll"
        if not rtxm.exists():
            continue
        lev = nf_tracks.host_lev(nf_tracks.native_host(slot) if nf_tracks.native(slot) else slot)
        src = max(rtxm.stat().st_mtime, pathlib.Path(__file__).stat().st_mtime, (HERE / "nf_hkx.py").stat().st_mtime,
                  (HERE / "nf_race.py").stat().st_mtime, lev.stat().st_mtime)
        if out.exists() and out.stat().st_mtime > src:
            continue
        build(slot, out_dir)
    return 0


def main(argv):
    out_dir = argv[2] if len(argv) > 2 else str(REMASTER)
    if len(argv) > 1 and argv[1] == "all":
        return build_all(out_dir)
    if len(argv) > 1:
        build(int(argv[1]) if argv[1].isdigit() else argv[1], out_dir)
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
