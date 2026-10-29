#!/usr/bin/env python3
"""How well an NF track's collision (<key>.coll, scripts/nf_collision.py)
matches the level as drawn (<key>.rtxm): numbers only, no game run.

Per track:
  * height of the collision ground minus the drawn surface it stands for
    (nf_collision.drawn_heights) at points spread over the ground near the race
    line (the PS1 restart path +-800; arenas: all of it): p50 / p95 / p99 / max
  * the same along the race line itself
  * open ground edges near the race line (cracks a kart could catch)
  * kill planes within 150 of the race line
  * turbo pad triggers

    nf_coll_check.py [tid ...] [--dir DIR]     default: every NF track built
"""
import pathlib
import struct
import sys

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import nf_collision as C  # noqa: E402
import nf_level  # noqa: E402
import nf_tracks  # noqa: E402

NEAR = 800.0
VERBOSE = 0


def read_coll(path):
    """triangles (N, 3, 3) of kind-1 quads, their flags and terrain"""
    data = open(path, "rb").read()
    h = struct.unpack_from(C.HEADER, data, 0)
    nv, nq = h[2], h[3]
    ov, oq, ob, ok = h[6], h[7], h[8], h[9]
    V = np.frombuffer(data, C.VERT_DT, nv, ov)["pos"].astype(np.float64)
    Q = np.frombuffer(data, C.QUAD_DT, nq, oq)
    base = np.frombuffer(data, "<u4", nq, ob).astype(np.int64)
    kind = np.frombuffer(data, "u1", nq, ok)
    s = kind == 1
    idx = base[s, None] + Q["index"][s][:, :3].astype(np.int64)
    return V[idx], Q["flags"][s].astype(np.int64), Q["terrain"][s]


def read_race(path):
    """a native track's race section (nf_collision.race_bytes), or None"""
    import nf_race
    data = open(path, "rb").read()
    h = struct.unpack_from(C.HEADER, data, 0)
    if h[1] < 3 or not h[17]:
        return None
    r = data[h[18]:h[18] + h[17]]
    at = 0

    def u32():
        nonlocal at
        v = struct.unpack_from("<I", r, at)[0]
        at += 4
        return v

    def align():
        nonlocal at
        at = (at + 3) & ~3
    u32()
    spawn = np.frombuffer(r, "<i2", 48, at).reshape(8, 6).astype(np.float64)
    at += 96
    align()
    nn = u32()
    nodes = np.frombuffer(r, nf_race.NODE, nn, at)
    at += nn * 12
    align()
    paths = []
    for _ in range(u32()):
        nf = u32()
        paths.append(np.frombuffer(r, nf_race.NAV_FRAME, nf, at + 0x4C))
        at += 0x4C + nf * 0x14
        align()
    nm = u32()
    moves = [struct.unpack_from("<I3h", r, at + 12 * k) for k in range(nm)]
    return {"spawn": spawn, "nodes": nodes, "paths": paths, "moves": moves}


def race_checks(race, tri, flags, grid):
    """problems in a native track's race data (empty: none) and a summary"""
    import nf_race
    bad = []
    nodes, nn = race["nodes"], len(race["nodes"])
    if not 2 <= nn <= 254:
        bad.append(f"{nn} restart points")
    if int(nodes["dist"][0]) <= 4000:
        bad.append(f"track length {int(nodes['dist'][0]) * 8} (laps need > 32000)")
    P = nodes["pos"].astype(np.float64)
    g = C.ground_under(grid, P + [0, 30, 0], above=60, below=200)
    # (a few over jumps are fine: a respawn walks on to the next one with ground)
    if np.isnan(g).mean() > 0.1 or np.isnan(g).all():
        bad.append(f"{int(np.isnan(g).sum())} restart points with no ground under them")
    kill = (flags & C.Q_KILL) != 0
    kgrid = C.TriGrid(tri[kill, 0], tri[kill, 1], tri[kill, 2], 128.0) if kill.any() else None
    if kgrid is not None:
        on_kill = C.top_hit(kgrid, P, above=40.0, below=40.0) >= 0
        if on_kill.any():
            bad.append(f"{int(on_kill.sum())} restart points on a kill plane")
    sp = race["spawn"]
    gs = C.ground_under(grid, sp[:, :3], above=20, below=200)
    if np.isnan(gs).any() or (sp[:, 1] - gs > 150).any():
        bad.append("start grid not over ground")
    d = P[1] - P[0]
    nav_yaw = np.arctan2(d[0], d[2]) * 4096 / (2 * np.pi)
    turn = ((sp[0, 4] + 1024 - nav_yaw + 2048) % 4096) - 2048
    if abs(turn) > 300:
        bad.append(f"start grid faces {turn:+.0f} off the race line")
    for k, fr in enumerate(race["paths"]):
        if (fr["dxyz"] <= 0).any() or len(set(fr["back"].tolist())) < nn // 2:
            bad.append(f"nav path {k}: bad distances or restart points")
    air = [float((fr["flags"] & 0x400 != 0).mean()) for fr in race["paths"]]
    moved = np.array([m[1:] for m in race["moves"] if tuple(m[1:]) != nf_race.OUT_OF_WORLD], np.float64).reshape(-1, 3)
    gi = C.ground_under(grid, moved + [0, 30, 0], above=60, below=200) if len(moved) else np.zeros(0)
    summary = (f"race: {nn} restart points ({int(np.isnan(g).sum())} over gaps), length {int(nodes['dist'][0]) * 8}, "
               f"nav {[len(f) for f in race['paths']]} frames ({np.round(np.array(air) * 100).astype(int).tolist()}% in the air), "
               f"{len(moved)} crates / fruit ({int(np.isnan(gi).sum()) if len(moved) else 0} floating, as Nitro-Fueled has them over jumps)")
    return bad, summary


def coverage(grid, vis, line, near=600.0):
    """drawn up-facing surface within `near` of the race line with no collision
    ground within 40 over / under it (a road the karts would fall through):
    share, and the biggest patches (500 unit cells: centre, points)"""
    g, vn = vis
    flat = vn[:, 1] > 0.7
    pts = C.surface_points(g.a[flat], g.b[flat], g.c[flat], 48.0)
    if not len(pts) or not len(line):
        return 0.0, []
    _, d = C.nearest(line, pts)
    pts = pts[d < near]
    if len(pts) > 300000:
        pts = pts[np.random.default_rng(2).choice(len(pts), 300000, replace=False)]
    gy = C.ground_under(grid, pts, above=40.0, below=40.0)
    miss = pts[np.isnan(gy) | (np.abs(gy - pts[:, 1]) > 40.0)]
    cells = {}
    for p in miss:
        k = tuple(np.floor(p[[0, 2]] / 500.0).astype(int))
        cells.setdefault(k, []).append(p)
    top = sorted(cells.values(), key=len, reverse=True)[:5]
    return len(miss) / max(len(pts), 1), [(np.mean(c, axis=0).round().astype(int).tolist(), len(c)) for c in top]


def stats(d):
    d = d[~np.isnan(d)]
    if not len(d):
        return "n/a"
    a = np.abs(d)
    return (f"p50 {np.percentile(a, 50):.1f} p95 {np.percentile(a, 95):.1f} p99 {np.percentile(a, 99):.1f} "
            f"max {a.max():.0f} (n {len(d)})")


def check(tid, out_dir):
    key = nf_tracks.key(tid)
    path = pathlib.Path(out_dir) / f"{key}.coll"
    if not path.exists():
        return
    tri, flags, terrain = read_coll(path)
    ground = ((flags & C.Q_GROUND) != 0) & ((flags & (C.Q_TRIGGER | C.Q_KILL)) == 0)
    g = tri[ground]
    a, b, c = g[:, 0], g[:, 1], g[:, 2]
    vis = C.drawn_surface(key, out_dir)
    race = read_race(path)
    if race is not None:
        P = race["nodes"]["pos"].astype(np.float64)
        Q = np.roll(P, -1, axis=0)
        line = np.concatenate([P + (Q - P) * f for f in np.linspace(0, 1, 16, endpoint=False)])
    else:
        line = nf_level.ps1_track_points(nf_tracks.host_lev(tid).read_bytes())
    pts = C.surface_points(a, b, c, 48.0)
    if len(line):
        _, d = C.nearest(line, pts)
        pts = pts[d < NEAR]
    if len(pts) > 400000:
        pts = pts[np.random.default_rng(1).choice(len(pts), 400000, replace=False)]
    grid = C.TriGrid(a, b, c, 128.0)
    # (the nearest drawn up-facing surface, whichever way it slopes)
    C.FACING = np.cos(np.radians(75.0))
    up = np.tile([0.0, 1.0, 0.0], (len(pts), 1))
    dev = pts[:, 1] - C.drawn_heights(vis, pts, up)
    out = [f"{key}: ground {stats(dev)}"]
    if VERBOSE:
        kp = pts
        for i in np.argsort(-np.nan_to_num(np.abs(dev)))[:VERBOSE]:
            print(f"   {kp[i].round().astype(int)} {dev[i]:+.1f}")
    if len(line):
        gy = C.ground_under(grid, line + [0, 30, 0], above=60, below=200)
        lp = line.copy()
        lp[:, 1] = gy
        okl = ~np.isnan(gy)
        dl = lp[okl, 1] - C.drawn_heights(vis, lp[okl], np.tile([0.0, 1.0, 0.0], (int(okl.sum()), 1)))
        out.append(f"race line {stats(dl)}")
        if VERBOSE:
            for i in np.argsort(-np.nan_to_num(np.abs(dl)))[:VERBOSE // 2]:
                print(f"   line {lp[okl][i].round().astype(int)} {dl[i]:+.1f}")
        # open edges of the solid mesh along the ground near the line
        sol = tri[(flags & (C.Q_TRIGGER | C.Q_KILL)) == 0]
        P = np.round(sol.reshape(-1, 3)).astype(np.int64)
        _, vid = np.unique(P, axis=0, return_inverse=True)
        T = vid.reshape(-1, 3)
        E = np.sort(np.concatenate([T[:, [0, 1]], T[:, [1, 2]], T[:, [2, 0]]]), axis=1)
        mid = np.concatenate([(sol[:, 0] + sol[:, 1]) / 2, (sol[:, 1] + sol[:, 2]) / 2, (sol[:, 2] + sol[:, 0]) / 2])
        isg = np.tile(ground[(flags & (C.Q_TRIGGER | C.Q_KILL)) == 0], 3)
        _, inv, cnt = np.unique(E, axis=0, return_inverse=True, return_counts=True)
        open_ = (cnt[inv.ravel()] == 1) & isg
        _, de = C.nearest(line, mid[open_]) if open_.any() else (None, np.zeros(0))
        out.append(f"open ground edges within 300 of the line {int((de < 300).sum())}")
        share, patches = coverage(grid, vis, line)
        out.append(f"drawn road with no ground {share * 100:.1f}%" + (f" (most at {patches[:3]})" if share > 0.002 else ""))
        kill = tri[(flags & C.Q_KILL) != 0]
        if len(kill):
            _, dk = C.nearest(line, kill.mean(1))
            out.append(f"kill planes within 150 of the line {int((dk < 150).sum())}")
    out.append(f"turbo triggers {int(((flags & C.Q_TRIGGER) != 0).sum())}")
    if race is not None:
        bad, summary = race_checks(race, tri, flags, grid)
        out.append(summary)
        out.append("RACE DATA OK" if not bad else "RACE DATA PROBLEMS: " + "; ".join(bad))
    print("; ".join(out), flush=True)


def main(argv):
    out_dir = str(C.REMASTER)
    if "--dir" in argv:
        i = argv.index("--dir")
        out_dir = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]
    global VERBOSE
    if "-v" in argv:
        VERBOSE = 30
        argv = [x for x in argv if x != "-v"]
    tids = [int(x) if x.isdigit() else x for x in argv[1:]] or nf_tracks.collision_tracks()
    for tid in tids:
        check(tid, out_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
