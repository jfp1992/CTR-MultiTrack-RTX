#!/usr/bin/env python3
"""Minimaps of the NF tracks: the track surface seen from above (the level as
drawn, its colours, only where the karts drive: collision ground near the race
line), north up, a white border round it so it reads over a road-coloured
background, at MAP_SCALE x the PS1 HUD's pixels.

    <out_dir>/<key>.png   RGBA, transparent off the track
    <out_dir>/<key>.map   s16 x0, x1, z0, z1 (PS1 world range the image spans),
                          w, h (its size in HUD pixels)

The game (native/game/MultiTrack/MT_Map.c) draws it where the stock minimap
goes, in the race (the karts' dots placed by a UIMap made from the .map) and
on the track select panel.

    nf_minimap.py [tid ...] [--dir DIR] [--out OUT]   default: every NF track built
"""
import pathlib
import struct
import sys

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import nf_coll_check as K  # noqa: E402
import nf_collision as C  # noqa: E402
import nf_level  # noqa: E402
import nf_tex  # noqa: E402
import nf_tracks  # noqa: E402

OUT = HERE.parent / "native/build/assets/track_maps"
MAP_W, MAP_H = 104, 80  # (HUD pixels) the image fits in this, aspect kept (stock maps: about this size)
MAP_SCALE = 4           # image pixels per HUD pixel
ROAD_NEAR = 700.0       # collision ground this close to the race line is the track
BORDER = 2              # (HUD pixels) the white border
SHADE = 0.85            # (the surface a little darker than its texture: the border stands out)


def race_line(tid, coll):
    race = K.read_race(coll)
    if race is not None:
        P = race["nodes"]["pos"].astype(np.float64)
        Q = np.roll(P, -1, axis=0)
        return np.concatenate([P + (Q - P) * f for f in np.linspace(0, 1, 16, endpoint=False)])
    return nf_level.ps1_track_points(nf_tracks.host_lev(tid).read_bytes())


def rtxm_surface(path, keep):
    """the drawn surface: triangles (N, 3, 3), uvs (N, 3, 2), colour map
    small images per triangle (index into the list), of the opaque / decal
    / alpha tested draws facing up whose centre keep(centre) takes"""
    data = open(path, "rb").read()
    h = struct.unpack_from(nf_level.HEADER, data, 0)
    nv, nt, nm, ntex = h[2], h[3], h[4], h[5]
    g = h[8:13]
    vd = np.dtype([("pos", "<f4", 3), ("uv", "<f4", 2), ("n", "<u4"), ("c", "u1", 4), ("t", "<u4")])
    vert = np.frombuffer(data, vd, nv, h[16])
    tri = np.frombuffer(data, np.dtype([("v", "<u4", 3), ("m", "<u4")]), nt, h[17])
    mats = np.frombuffer(data, "<u4", nm * 12, h[18]).reshape(nm, 12)
    skip = nf_level.MAT_GLASS | nf_level.MAT_WATER | nf_level.MAT_GRASS | nf_level.MAT_TRANSLUCENT
    sel = np.zeros(nt, bool)
    sel[:g[3]] = True  # opaque, alpha tested, grass (left out by flag), decals
    sel &= (mats[tri["m"], 3] & skip) == 0
    t = tri[sel]
    P = vert["pos"][t["v"]].astype(np.float64)
    n = np.cross(P[:, 1] - P[:, 0], P[:, 2] - P[:, 0])
    n *= np.sign(n[:, 1:2] + 1e-12)  # (either winding: facing up)
    up = n[:, 1] > 0.5 * np.linalg.norm(n, axis=1)
    k = up & keep(P.mean(1))
    t, P = t[k], P[k]
    uv = vert["uv"][t["v"]].astype(np.float64)
    col = vert["c"][t["v"]][:, :, :3].astype(np.float64).mean(1) / 255.0
    # each material's colour map, small (decoded once)
    texs = {}

    def small(ti):
        if ti in texs or ti >= ntex:
            return texs.get(ti)
        kind, w, hh, mips, off, size = struct.unpack_from("<4I2Q", data, h[19] + ti * 32)
        img = None
        o = off
        for m in range(mips):
            mw, mh = max(w >> m, 1), max(hh >> m, 1)
            bp = (1, 4) if kind > 5 else (4, 8 if kind == 1 else 16)
            nbytes = ((mw + bp[0] - 1) // bp[0]) * ((mh + bp[0] - 1) // bp[0]) * bp[1]
            if (min(mw, mh) <= 64 or m == mips - 1) and mw >= 4 and mh >= 4:
                blk = data[o:o + nbytes]
                try:
                    if kind in (1, 3, 5):
                        img = nf_tex.decode_bc(blk, mw, mh, {1: "bc1", 3: "bc3", 5: "bc5"}[kind])[:, :, :3]
                    else:
                        img = np.frombuffer(blk, np.uint8).reshape(mh, mw, 4)[:, :, [2, 1, 0] if kind == 8 else [0, 1, 2]]
                except Exception:
                    img = None
                break
            o += nbytes
        texs[ti] = img
        return img
    tex0 = mats[t["m"], 0]
    return P, uv, col, tex0, small


def build(tid, coll_dir, out_dir):
    key = nf_tracks.key(tid)
    coll = pathlib.Path(coll_dir) / f"{key}.coll"
    rtxm = pathlib.Path(coll_dir) / f"{key}.rtxm"
    if not coll.exists() or not rtxm.exists():
        return
    line = race_line(tid, coll)
    if not len(line):
        return
    tri, flags, _ = K.read_coll(coll)
    g = ((flags & C.Q_GROUND) != 0) & ((flags & (C.Q_TRIGGER | C.Q_KILL)) == 0)
    _, d = C.nearest(line, tri[:, :, :].mean(1))
    road = tri[g & (d < ROAD_NEAR)]
    road_grid = C.TriGrid(road[:, 0], road[:, 1], road[:, 2], 128.0)

    # the image's world rectangle: the road's, with room for the border
    lo = road.reshape(-1, 3).min(0)
    hi = road.reshape(-1, 3).max(0)
    span = np.array([hi[0] - lo[0], hi[2] - lo[2]])
    fit = min((MAP_W - 2 * BORDER - 2) / span[0], (MAP_H - 2 * BORDER - 2) / span[1])
    w, h = int(np.ceil(span[0] * fit)) + 2 * BORDER + 2, int(np.ceil(span[1] * fit)) + 2 * BORDER + 2
    h += h & 1  # (the game's UIMap halves the height)
    cx, cz = (lo[0] + hi[0]) / 2, (lo[2] + hi[2]) / 2
    x0, x1 = cx - w / 2 / fit, cx + w / 2 / fit
    z0, z1 = cz - h / 2 / fit, cz + h / 2 / fit

    # every image pixel (north = +z up): the road under it?
    W, H = w * MAP_SCALE, h * MAP_SCALE
    px = x0 + (np.arange(W) + 0.5) / W * (x1 - x0)
    pz = z1 - (np.arange(H) + 0.5) / H * (z1 - z0)
    gx, gz = np.meshgrid(px, pz)
    pts = np.stack([gx.ravel(), np.full(gx.size, 1e5), gz.ravel()], 1)
    rh = C.ground_under(road_grid, pts, above=0.0, below=1e6)
    on = ~np.isnan(rh)

    # its colour: the top drawn surface within 80 of the road there
    Pd, uv, col, tex0, small = rtxm_surface(rtxm, lambda c: C.nearest(road.mean(1), c)[1] < 300.0)
    rgb = np.zeros((len(pts), 3))
    filled = np.zeros(len(pts), bool)
    if len(Pd):
        dg = C.TriGrid(Pd[:, 0], Pd[:, 1], Pd[:, 2], 64.0)
        idx = np.nonzero(on)[0]
        q = pts[idx].copy()
        q[:, 1] = rh[idx]
        pi, ti, y = dg.hits(q)
        ok = np.abs(y - q[pi, 1]) <= 80.0
        pi, ti, y = pi[ok], ti[ok], y[ok]
        o = np.lexsort((y, pi))
        last = o[np.r_[pi[o][1:] != pi[o][:-1], True]]
        pi, ti = pi[last], ti[last]
        A, B, Cc = Pd[ti, 0], Pd[ti, 1], Pd[ti, 2]
        v0, v1, v2 = (B - A)[:, [0, 2]], (Cc - A)[:, [0, 2]], q[pi][:, [0, 2]] - A[:, [0, 2]]
        den = v0[:, 0] * v1[:, 1] - v1[:, 0] * v0[:, 1]
        den = np.where(np.abs(den) > 1e-9, den, 1.0)
        b1 = (v2[:, 0] * v1[:, 1] - v1[:, 0] * v2[:, 1]) / den
        b2 = (v0[:, 0] * v2[:, 1] - v2[:, 0] * v0[:, 1]) / den
        b0 = 1.0 - b1 - b2
        tuv = uv[ti, 0] * b0[:, None] + uv[ti, 1] * b1[:, None] + uv[ti, 2] * b2[:, None]
        c = np.zeros((len(ti), 3))
        for tex in np.unique(tex0[ti]):
            s = tex0[ti] == tex
            img = small(int(tex)) if tex != 0xFFFFFFFF else None
            if img is None:
                c[s] = 0.5
                continue
            ih, iw = img.shape[:2]
            u = np.mod(tuv[s, 0], 1.0)
            v = np.mod(tuv[s, 1], 1.0)
            c[s] = img[np.minimum((v * ih).astype(int), ih - 1), np.minimum((u * iw).astype(int), iw - 1)] / 255.0
        c *= np.clip(col[ti] * 1.6, 0.4, 1.0)  # (vertex colour: baked shade, tints)
        rgb[idx[pi]] = c
        filled[idx[pi]] = True
    rgb[on & ~filled] = 0.45  # (road with nothing drawn on it: grey)

    img = np.zeros((H, W, 4))
    img[:, :, :3] = (rgb * SHADE).reshape(H, W, 3)
    mask = on.reshape(H, W)
    # the white border: the road grown by BORDER HUD pixels, under it
    r = BORDER * MAP_SCALE
    yy, xx = np.mgrid[-r:r + 1, -r:r + 1]
    disk = (xx * xx + yy * yy) <= r * r
    grown = np.zeros_like(mask)
    pad = np.pad(mask, r)
    for dy, dx in zip(*np.nonzero(disk)):
        grown |= pad[dy:dy + H, dx:dx + W]
    img[grown & ~mask, :3] = 1.0
    img[:, :, 3] = grown
    from PIL import Image
    out = pathlib.Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.round(np.clip(img, 0, 1) * 255).astype(np.uint8), "RGBA").save(out / f"{key}.png")
    (out / f"{key}.map").write_bytes(struct.pack("<6h", *[int(round(v)) for v in (x0, x1, z0, z1)], w, h))
    print(f"{key}: minimap {w}x{h} HUD pixels, world x {x0:.0f}..{x1:.0f} z {z0:.0f}..{z1:.0f}, "
          f"{int(mask.sum())} road pixels ({int(filled.sum())} coloured)", flush=True)


def main(argv):
    coll_dir, out_dir = str(C.REMASTER), str(OUT)
    for flag in ("--dir", "--out"):
        if flag in argv:
            i = argv.index(flag)
            if flag == "--dir":
                coll_dir = argv[i + 1]
            else:
                out_dir = argv[i + 1]
            argv = argv[:i] + argv[i + 2:]
    tids = [int(x) if x.isdigit() else x for x in argv[1:]] or [t for t in nf_tracks.collision_tracks() if not nf_tracks.is_battle(t)]
    for tid in tids:
        build(tid, coll_dir, out_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
