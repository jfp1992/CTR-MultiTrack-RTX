#!/usr/bin/env python3
"""Race data of a native NF track (nf_tracks.NATIVE: a Nitro-Fueled track with no
PS1 level, played over a stock host slot) from the Nitro-Fueled level itself,
in the PS1 game's structures (nf_collision.py writes them into the track's
.coll, native/game/MultiTrack/MT_NfColl.c points the level at them):

  * the race line: the middle lane of the AI splines (CRaceTrackSplineComponentData
    "AI" splines named <section><lane>, e.g. AI_Spline_302 = section 3 lane 2;
    shortcuts left out), sections chained in order; its direction is the
    driving direction
  * restart points (CheckpointNode: respawns, lap progress) along it from the
    start line, distToFinish in 8 unit steps
  * the start grid (DriverSpawn[8]): the stock levels' formation round
    PlayerStartAll, facing along the race line
  * three AI nav paths (NavHeader + NavFrame) along the middle lane and its
    neighbours
  * an invisible road where Nitro-Fueled carries the karts along a spline
    (bridges: Spyro Circuit's flight)
  * crates and fruit: the host level's own instances moved onto Nitro-Fueled's
    (PowerUpCrate / crate_question, WumpaFruitCrate / crate_fruit, WumpaFruit /
    fruit); every other host instance out of the world

    nf_race.py <tid>      print what a track's race data comes to
"""
import glob
import os
import re
import struct
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import nf_igz  # noqa: E402
import nf_level  # noqa: E402
import nf_tracks  # noqa: E402

NODE_SPACING = 800.0   # restart points (the stock levels': 780-1580)
FRAME_SPACING = 230.0  # nav frames (the stock levels': ~230)
MAX_NODES = 250        # (u8 indices, 0xFF = none)
GRID_AHEAD = 240.0     # the start line (node 0) ahead of the grid's centre (stock: 120 ahead of the front row)
START_BEHIND = 450.0 * 1.5  # (NF units) PlayerStartAll this far behind the start line (Twilight Tour)
ON_LINE = 900.0        # (NF units) a start marker this close to the race line is on it
SPAWN_CLEAR = 64.0     # the start grid this high over the ground (stock: 40-125)
AIR = 120.0            # a lane this far over its usual height over the ground is in the air (a jump)
NAV_MAGIC = -0x1303    # NavHeader.magicNumber (0xECFD)
NO_PATH_CHANGE = 0xC00
OUT_OF_WORLD = (0, -30000, 0)
# a race line segment this long joins two splines across a portal (Spyro
# Circuit: Nitro-Fueled teleports the karts, its progress spline through the
# portal's space has no ground): no distance, the game warps karts over it
WARP_SEG = 3000.0
WARP_RADIUS = 450     # (PS1 units) a kart this close to a warp's entry is warped
GROUNDED = 0.3        # a race spline with ground under less of it than this is a portal's (left out)

# the stock levels' start grid round slot 0: (across, along the driving direction)
FORMATION = [(0, 0), (190, 8), (379, 9), (564, 9), (-5, -249), (191, -221), (376, -245), (565, -250)]
GRID_CENTRE = (282.0, -120.0)

NAV_FRAME = np.dtype([("pos", "<i2", 3), ("rot", "u1", 4), ("dxyz", "<i2"), ("dxz", "<i2"), ("flags", "<u2"),
                      ("op", "<u2"), ("back", "u1"), ("special", "u1")])
NODE = np.dtype([("pos", "<i2", 3), ("dist", "<u2"), ("next", "u1", 4)])
assert NAV_FRAME.itemsize == 0x14 and NODE.itemsize == 0xC

ITEMS = {  # host instance name prefix: Nitro-Fueled entity names (some carry their author's name)
    "crate_question": re.compile(r"^(powerupcrate|crate_question)(_[a-z]+)?_?\d*$", re.I),
    "crate_fruit": re.compile(r"^(wumpafruitcrate|crate_fruit|carpet_wumpa_crate)(_[a-z]+)?_?\d*$", re.I),
    "fruit": re.compile(r"^(wumpafruit|fruit)(_[a-z]+)?_?\d*$", re.I),
}
SHORTCUT = re.compile(r"short|(^|_|\d)sc($|_|\d)", re.I)


def entities(z):
    return z.of_type("CEntity") + z.of_type("igEntity") + z.of_type("CGameEntity")


def race_splines(ldir):
    """name -> (shortcut, control points (N, 3) NF units) of every race track spline"""
    out = {}
    for p in sorted(glob.glob(os.path.join(ldir, "*.igz"))):
        z = nf_igz.Igz(p)
        if "CRaceTrackSplineComponentData" not in z.metatypes:
            continue
        names = {o: n for n, o in z.named_objects().items()}
        for e in entities(z):
            comp = z.reachable(e, {"CRaceTrackSplineComponentData"})
            if not comp:
                continue
            m, pos = nf_level.entity_transform(z, e)
            for sp in z.reachable(e, {"igSpline2"}):
                for lst in z.reachable(sp, {"igSplineControlPoint2List"}):
                    cp = np.array([z.vec(c + 0xC, 3) for c in z.object_list(lst) if c])
                    if len(cp) >= 2:
                        name = names.get(e, hex(e))
                        out[name] = (z.u32(comp[0] + 0x30) != 0 or bool(SHORTCUT.search(name)), cp @ m.T + pos)
    return out


def is_ai(name):
    return bool(re.search(r"(^|_)ai(_|\d)|ai_?spline|spline_?ai", name, re.I))


def ai_sections(spl):
    """{section: {lane: control points}} of the AI splines (named <section><lane>:
    AI_Spline_302 = section 3 lane 2; no shortcuts; of a section's variants
    (left / right / A / B) the first)"""
    by = {}
    for name, (short, cp) in spl.items():
        if short or not is_ai(name):
            continue
        m = re.search(r"(\d{3,4})(?!.*\d)", name)
        if not m:
            continue
        d = int(m.group(1))
        variant = re.sub(r"(?i)splines?|ai|\d|_", "", name).lower()
        by.setdefault(d // 100, {}).setdefault(variant, {})[d % 100] = cp
    return {s: v[sorted(v)[0]] for s, v in sorted(by.items())}


def catmull(cp, spacing=40.0):
    """a Catmull-Rom curve through control points, a point every ~spacing"""
    P = np.vstack([cp[0], cp, cp[-1]])
    out = []
    for i in range(1, len(P) - 2):
        p0, p1, p2, p3 = P[i - 1], P[i], P[i + 1], P[i + 2]
        n = max(1, int(np.linalg.norm(p2 - p1) / spacing))
        t = (np.arange(n) / n)[:, None]
        out.append(0.5 * ((2 * p1) + (-p0 + p2) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t ** 2 + (-p0 + 3 * p1 - 3 * p2 + p3) * t ** 3))
    out.append(cp[-1:])
    return np.concatenate(out)


def join(P, N):
    """two curves end to start, where they come closest (sections overlap or
    leave a gap): P up to there, then N from there"""
    a, b = len(P) // 2, len(N) // 2 + 1
    d = np.linalg.norm(P[a:, None] - N[None, :b], axis=2)
    i, j = np.unravel_index(np.argmin(d), d.shape)
    return P[:a + i + 1], N[j:], float(d[i, j])


def chain(curves, near):
    """curves (dense polylines) into one closed loop: from the one passing
    nearest `near` on to whichever starts nearest the end so far, until the
    first one's start is the nearest. Returns the loop and its joints' gaps."""
    first = int(np.argmin([np.linalg.norm(c - near, axis=1).min() for c in curves]))
    order, used = [curves[first]], {first}
    while True:
        end = order[-1][-1]
        cand = [(np.linalg.norm(c[0] - end), k) for k, c in enumerate(curves) if k not in used]
        back = np.linalg.norm(order[0][0] - end)
        if not cand or min(cand)[0] >= back or len(order) > 64:
            break
        k = min(cand)[1]
        order.append(curves[k])
        used.add(k)
    gaps = []
    for k in range(len(order)):
        nxt = (k + 1) % len(order)
        order[k], order[nxt], g = join(order[k], order[nxt])
        gaps.append(g)
    P = np.concatenate(order)
    keep = np.r_[True, np.linalg.norm(np.diff(P, axis=0), axis=1) > 1e-3]
    return P[keep], gaps


def smooth(P, w=7):
    """moving average along a closed polyline"""
    k = np.ones(w) / w
    return np.stack([np.convolve(np.r_[P[-w:, i], P[:, i], P[:w, i]], k, "same")[w:-w] for i in range(3)], 1)


def lanes(ldir, near, keep=None):
    """the race line (the position splines chained: they cover all of the lap,
    the AI splines not always) and two more lanes, all closed dense polylines
    (NF units): [middle, left, right] where the AI splines' middle lane and its
    neighbours run (within 800 NF units of the race line), the race line
    elsewhere; and the race line's joints' gaps"""
    spl = race_splines(ldir)
    pos = [catmull(cp) for n, (short, cp) in spl.items() if not short and re.search("position", n, re.I)]
    if keep is not None and any(keep(c) for c in pos):
        pos = [c for c in pos if keep(c)]  # (not a portal's: keep(curve, NF units))
    sec = ai_sections(spl)
    if not pos:  # (no position splines: the AI middle lanes)
        pos = [catmull(ln[sorted(ln)[len(ln) // 2]]) for ln in sec.values()]
    if not pos:
        raise SystemExit(f"{ldir}: no race splines")
    line, gaps = chain(pos, near)
    out = []
    import nf_collision
    for rank in (0, -1, 1):
        pts = [catmull(ln[sorted(ln)[min(max(len(ln) // 2 + rank, 0), len(ln) - 1)]]) for ln in sec.values()]
        if not pts:
            out.append(line)
            continue
        A = np.concatenate(pts)
        j, d = nf_collision.nearest(A, line)
        out.append(smooth(np.where((d < 800.0)[:, None], A[j], line)))
    return line, out, gaps


def player_start(ldir):
    """PlayerStartAll's position (NF units), or the first player start that
    isn't a respawn point"""
    best = None
    for p in sorted(glob.glob(os.path.join(ldir, "*.igz"))):
        z = nf_igz.Igz(p)
        if "CPlayerStartEntity" not in z.metatypes:
            continue
        names = {o: n for n, o in z.named_objects().items()}
        for e in z.of_type("CPlayerStartEntity"):
            n = names.get(e, "")
            if "respawn" in n.lower():
                continue
            _, pos = nf_level.entity_transform(z, e)
            if n.lower() == "playerstartall":
                return np.array(pos)
            best = best if best is not None else np.array(pos)
    if best is None:
        raise SystemExit(f"{ldir}: no player start")
    return best


def start_line(ldir, line):
    """arc position of the start line on the race line (NF units): the start
    line's sound emitter (AMB_2D_StartLine: on the line, Twilight Tour's remake
    has its line there), else PlayerStartAll + START_BEHIND (the grid's back;
    some levels keep it in a parent's space: not used when off the race line)"""
    import nf_collision
    marks = []
    for p in sorted(glob.glob(os.path.join(ldir, "*.igz"))):
        z = nf_igz.Igz(p)
        for n, o in z.named_objects().items():
            if re.fullmatch(r"amb_2d_startline\d*", n, re.I) and z.obj_type(o) in ("igEntity", "CGameEntity", "CEntity"):
                marks.append(np.array(nf_level.entity_transform(z, o)[1]))
    s, L = arc(line)
    for P in marks:
        i, d = nf_collision.nearest(line, P[None])
        if d[0] < ON_LINE:
            return s[i[0]], "start line sound"
    st = player_start(ldir)
    i, d = nf_collision.nearest(line, st[None])
    if d[0] < ON_LINE:
        return (s[i[0]] + START_BEHIND) % L, "player start"
    raise SystemExit(f"{ldir}: no start line on the race line")


def items(ldir):
    """{host name prefix: NF positions (N, 3)} of crates and fruit (not the art
    maps' decoration)"""
    out = {k: [] for k in ITEMS}
    for p in sorted(glob.glob(os.path.join(ldir, "*.igz"))):
        if re.search(r"_art", os.path.basename(p), re.I):
            continue
        z = nf_igz.Igz(p)
        for n, o in z.named_objects().items():
            for k, rx in ITEMS.items():
                if rx.match(n) and z.obj_type(o) in ("igEntity", "CGameEntity", "CEntity"):
                    out[k].append(np.array(nf_level.entity_transform(z, o)[1]))
    return {k: np.array(v).reshape(-1, 3) for k, v in out.items()}


def host_instances(slot):
    """the host level's InstDefs: [(body offset, name)]"""
    body = nf_tracks.host_lev(slot).read_bytes()[4:]
    n, at = struct.unpack_from("<2I", body, 0x0C)
    return [(at + i * 0x40, body[at + i * 0x40:at + i * 0x40 + 16].split(b"\0")[0].decode(errors="replace")) for i in range(n)]


def host_nav_ramp(slot):
    """rampPhys1/2 of the host level's first nav path (32 s16)"""
    body = nf_tracks.host_lev(slot).read_bytes()[4:]
    tab = struct.unpack_from("<I", body, 0x188)[0]
    h = struct.unpack_from("<I", body, tab)[0] if tab else 0
    return np.frombuffer(body, "<i2", 32, h + 0xC).copy() if h else np.zeros(32, np.int16)


def arc(P, closed=True):
    """cumulative length along a polyline (and the total, closed); a portal's
    warp segment (over WARP_SEG) counts nothing"""
    seg = np.linalg.norm(np.diff(np.vstack([P, P[:1]]) if closed else P, axis=0), axis=1)
    seg = np.where(seg > WARP_SEG, 0.0, seg)
    return np.r_[0.0, np.cumsum(seg)[:-1]] if closed else np.r_[0.0, np.cumsum(seg)], seg.sum()


def resample(P, step, start=0.0):
    """points every `step` along a closed polyline from arc `start` (count rounded
    to fit the loop evenly)"""
    s, L = arc(P)
    n = max(4, int(round(L / step)))
    q = (start + np.arange(n) * (L / n)) % L
    i = np.searchsorted(s, q, side="right") - 1
    nxt = (i + 1) % len(P)
    seg = np.where(nxt == 0, L - s[i], s[nxt] - s[i])
    f = np.where(seg > 0, (q - s[i]) / np.maximum(seg, 1e-9), 0.0)[:, None]
    return P[i] + (P[nxt] - P[i]) * f, L


def project(P, Q):
    """arc position on closed polyline P of the nearest point of each of Q"""
    s, _ = arc(P)
    import nf_collision
    i, _ = nf_collision.nearest(P, Q)
    return s[i]


def build(tid, m, t, ground, terrain_at, boost_at):
    """race data of native track tid in PS1 space (placement m, t). ground(P):
    the collision ground's height under points (NaN: none); terrain_at(P):
    terrain under them; boost_at(P): turbo pad step flags under them (0, 1, 2).
    Returns a dict for nf_collision.write_coll's race section."""
    _, pak, name, host = nf_tracks.native(tid)
    root, _ = nf_level.extract(pak)
    ldir = nf_level.level_dir(root)
    line_nf, (mid_nf, left_nf, right_nf), gaps = lanes(ldir, player_start(ldir), grounded_keep(m, t, ground))
    a_nf, how = start_line(ldir, line_nf)
    to_ps1 = lambda X: X @ m.T + t  # noqa: E731
    line, mid, left, right = to_ps1(line_nf), to_ps1(mid_nf), to_ps1(left_nf), to_ps1(right_nf)

    # the start line (node 0), the grid's centre GRID_AHEAD behind it
    s, L = arc(line)
    s0 = s[np.searchsorted(arc(line_nf)[0], a_nf, side="right") - 1]
    start = resample(line, L, s0 - GRID_AHEAD)[0][0]
    node_pts, _ = resample(line, L / min(MAX_NODES, max(8, int(round(L / NODE_SPACING)))), s0)
    nn = len(node_pts)
    gy = ground(node_pts + [0, 100, 0])
    nodes = np.zeros(nn, NODE)
    nodes["pos"] = np.round(np.c_[node_pts[:, 0], np.where(np.isnan(gy), node_pts[:, 1], gy), node_pts[:, 2]])
    nodes["dist"] = np.round((L - np.arange(nn) * (L / nn)) / 8.0).astype(np.int64)
    nodes["next"][:, 0] = (np.arange(nn) + 1) % nn
    nodes["next"][:, 1] = 0xFF
    nodes["next"][:, 2] = (np.arange(nn) - 1) % nn
    nodes["next"][:, 3] = 0xFF

    # the start grid round the player start, facing along the race line
    d = node_pts[1] - node_pts[0]
    nav_yaw = np.arctan2(d[0], d[2])
    yaw = nav_yaw - np.pi / 2  # (DriverSpawn.rot: a quarter turn from the nav paths' yaw)
    A = np.array([np.sin(yaw), 0.0, np.cos(yaw)])
    D = np.array([np.cos(yaw), 0.0, -np.sin(yaw)])
    p0 = start - GRID_CENTRE[0] * A - GRID_CENTRE[1] * D
    grid = np.array([p0 + a * A + b * D for a, b in FORMATION])
    gg = ground(grid + [0, 100, 0])
    grid[:, 1] = np.where(np.isnan(gg), grid[:, 1], gg) + SPAWN_CLEAR
    rot = int(round(yaw * 4096 / (2 * np.pi))) % 4096
    rot = rot - 4096 if rot >= 2048 else rot
    spawn = np.zeros((8, 6), np.int16)
    spawn[:, :3] = np.round(grid)
    spawn[:, 4] = rot

    # nav paths: frames from the start line along each lane
    s_node = np.arange(nn) * (L / nn)
    paths = []
    for lane in (mid, left, right):
        a0 = project(lane, node_pts[:1])[0]
        F, _ = resample(lane, FRAME_SPACING, a0)
        g = ground(F + [0, 100, 0])
        # (the splines ride a little over the road: in the air only well over that)
        ride = np.nanmedian(F[:, 1] - g) if (~np.isnan(g)).any() else 0.0
        air = np.isnan(g) | (F[:, 1] - g > ride + AIR)
        F[:, 1] = np.where(air, F[:, 1] - ride, g)
        nxt = np.roll(F, -1, axis=0)
        v = nxt - F
        dxz = np.hypot(v[:, 0], v[:, 2])
        slope = np.arctan2(v[:, 1], np.maximum(dxz, 1e-6)) * 4096 / (2 * np.pi)
        yaw12 = np.arctan2(v[:, 0], v[:, 2]) * 4096 / (2 * np.pi)
        fr = np.zeros(len(F), NAV_FRAME)
        fr["pos"] = np.round(F)
        fr["rot"][:, 0] = np.round(-slope / 16).astype(np.int64) & 0xFF
        fr["rot"][:, 1] = np.round(yaw12 / 16).astype(np.int64) & 0xFF
        fr["rot"][:, 3] = np.round(slope / 16).astype(np.int64) & 0xFF
        fr["dxyz"] = np.clip(np.round(np.linalg.norm(v, axis=1)), 1, 32767)
        fr["dxz"] = np.clip(np.round(dxz), 1, 32767)
        ter = np.clip(terrain_at(F + [0, 100, 0]), 0, 15).astype(np.int64)
        bst = boost_at(F + [0, 100, 0])
        fr["flags"] = (ter << 3) | np.where(bst == 1, 0x1, 0) | np.where(bst == 2, 0x100, 0) | np.where(air, 0x400, 0)
        # each frame's restart point: by progress along the race line
        prog = (project(line, F) - s0) % L
        fr["back"] = np.clip(np.searchsorted(s_node, prog, side="right") - 1, 0, nn - 1)
        paths.append(fr)
    # overtaking: the next path's nearest frame (NavFrame.pathChangeOpcode)
    import nf_collision
    for p, fr in enumerate(paths):
        q = (p + 1) % 3
        j, dist = nf_collision.nearest(paths[q]["pos"].astype(np.float64), fr["pos"].astype(np.float64))
        fr["op"] = np.where((dist < 400) & (j < 1024), (q << 10) | j, NO_PATH_CHANGE)
    ramp = host_nav_ramp(host)

    # crates and fruit: the host's own instances onto Nitro-Fueled's
    nf_items = items(ldir)
    moves, used = [], set()
    inst = host_instances(host)
    for prefix, pts in nf_items.items():
        mine = [off for off, n in inst if re.match(rf"^{prefix}#?\d*$", n, re.I)]
        P = to_ps1(pts) if len(pts) else np.zeros((0, 3))
        if len(P) > len(mine):  # (the host has fewer: an even spread along the lap)
            P = P[np.argsort((project(line, P) - s0) % L)]
            P = P[np.round(np.linspace(0, len(P) - 1, len(mine))).astype(np.int64)] if mine else P[:0]
        if len(P):
            gi = ground(P + [0, 100, 0])
            P[:, 1] = np.where(np.isnan(gi), P[:, 1], gi)
        for off, p in zip(mine, P):
            moves.append((off, np.round(p).astype(np.int64)))
            used.add(off)
    for off, n in inst:
        if off not in used:
            moves.append((off, np.array(OUT_OF_WORLD)))
    return {"nodes": nodes, "spawn": spawn, "paths": paths, "ramp": ramp, "moves": moves, "warps": warps(line),
            "line": line, "length": L, "start": start, "start_from": how, "gaps": gaps, "items": {k: len(v) for k, v in nf_items.items()},
            "moved": len(used), "host_items": sum(1 for _, n in inst if any(re.match(rf"^{k}#?\d*$", n, re.I) for k in ITEMS))}


# turbo pads / boost zones that are trigger volumes (not SpeedBoost collision):
# TurboPad_N, SuperBoostPad, Koala Carnival's TurboTrigger_<colour>, Oxide
# Station's TurboJump, N. Gin Labs' TunnelMultiBoost, Thunderstruck's JumpBoost;
# never a pad's "_Off" model
BOOST_NAME = re.compile(r"turbo_?pad|boost_?pad|turbo_?trigger|turbo_?jump|multi_?boost|jump_?boost|^boost$", re.I)
SUPER_NAME = re.compile(r"super|jump_?boost", re.I)  # super turbo (Thunderstruck's big jump ramp: JumpBoost)
BOOST_SKIP = re.compile(r"_off$|light|tint", re.I)
VOLUME_TYPES = ("CTriggerVolumeBoxComponentData", "CTriggerVolumeCylinderComponentData", "CTriggerVolumeCapsuleComponentData",
                "CTriggerVolumeSphereComponentData")


def _volume_base(z, v, size_at):
    """where a trigger volume's own fields start: +0x44 in the game's levels,
    +0x50 in the DLC's (the game's have the convex radius / integers there).
    size_at: offset from that start of a field that is a size (> 1 unit)"""
    dlc = z.f32(v + 0x50 + size_at)
    return 0x50 if np.isfinite(dlc) and 1.0 < abs(dlc) < 1e6 else 0x44


def trigger_box(z, v):
    """a trigger volume (CTriggerVolumeComponentData) in its entity's space as
    an oriented box: (centre, rotation (3, 3) local box -> entity, half
    extents (3,)); cylinders, capsules, spheres by their bounding box"""
    off = np.array(z.vec(v + 0x10, 3))  # _offset
    rot = nf_level.euler_matrix(*z.vec(v + 0x1C, 3))  # _rotation
    ty = z.obj_type(v)
    if ty == "CTriggerVolumeBoxComponentData":
        return off, rot, np.abs(np.array(z.vec(v + _volume_base(z, v, 0), 3))) / 2  # _dimensions
    if ty == "CTriggerVolumeSphereComponentData":
        r = abs(z.f32(v + _volume_base(z, v, 0)))  # _radius
        return off, rot, np.array([r, r, r])
    f = np.array(z.vec(v + _volume_base(z, v, 0x18), 7))  # _bottom, _top, _radius
    bottom, top, r = f[:3], f[3:6], abs(float(f[6]))
    axis = top - bottom
    length = float(np.linalg.norm(axis))
    zax = axis / length if length > 1e-6 else np.array([0.0, 0.0, 1.0])
    xax = np.cross([0.0, 1.0, 0.0] if abs(zax[1]) < 0.9 else [1.0, 0.0, 0.0], zax)
    xax /= np.linalg.norm(xax)
    frame = np.stack([xax, np.cross(zax, xax), zax], 1)
    cap = r if ty == "CTriggerVolumeCapsuleComponentData" else 0.0
    return off + rot @ ((bottom + top) / 2), rot @ frame, np.array([r, r, length / 2 + cap])


def trigger_volumes(ldir, keep):
    """[(name, position, entity axes (3, 3), (centre, rotation, half extents))]
    in NF units of every entity keep(z, entity, name) takes that has a trigger
    volume"""
    out = []
    for p in sorted(glob.glob(os.path.join(ldir, "*.igz"))):
        z = nf_igz.Igz(p)
        if not any(t in z.metatypes for t in VOLUME_TYPES):
            continue
        names = {o: n for n, o in z.named_objects().items()}
        for e in entities(z):
            n = names.get(e, "")
            if not keep(z, e, n):
                continue
            vols = [v for t in VOLUME_TYPES for v in z.reachable(e, {t})]
            if not vols:
                continue
            m, pos = nf_level.entity_transform(z, e)
            out.append((n, np.array(pos), np.array(m), trigger_box(z, vols[0])))
    return out


def boost_volumes(ldir):
    """Nitro-Fueled's turbo pads where they are trigger volumes: [(position,
    axes (3, 3), box, step flag 1 / 2 super)] in NF units (box: trigger_box)"""
    vols = trigger_volumes(ldir, lambda z, e, n: bool(BOOST_NAME.search(n)) and not BOOST_SKIP.search(n))
    return [(pos, m, box, 2 if SUPER_NAME.search(n) else 1) for n, pos, m, box in vols if np.all(box[2] > 0)]


VOLUME_MIN_HALF = 80.0  # (NF units) a volume at least this thick each way: gates (Cortex Castle's last jump: 20 thick, upright) catch the ground through them


def in_volume(pos, m, box, P_nf, margin=0.0):
    """whether each of P_nf (NF units) is in an entity's trigger box (+ margin
    along the box's own up axis)"""
    centre, rot, half = box
    local = ((P_nf - pos) @ np.linalg.inv(m).T - centre) @ rot
    return np.all(np.abs(local) <= np.maximum(half, VOLUME_MIN_HALF) + [0.0, 0.0, margin], axis=1)


def boost_at_volumes(vols, P_nf, margin=60.0):
    """step flag (0 / 1 / 2) of the turbo pad volume each of P_nf (NF units) is in"""
    out = np.zeros(len(P_nf), np.int64)
    for pos, m, box, flag in vols:
        out = np.where(in_volume(pos, m, box, P_nf, margin), np.maximum(out, flag), out)
    return out


BRIDGE_MIN = 2500.0    # the race line this long with no ground under it is a flight (no jump is that long)
BRIDGE_HALF = 400.0    # half the width of the road laid along it
BRIDGE_STEP = 200.0


def bridges(line, grid):
    """Nitro-Fueled carries karts along a spline in places (Spyro Circuit's
    flight): no ground there, the PS1 karts would fall. Triangles (N, 3, 3) of
    an invisible road along the race line (PS1 space) wherever it has no ground
    under it for BRIDGE_MIN, at the height karts ride the line elsewhere."""
    import nf_collision
    P, L = resample(line, 40.0)
    g = nf_collision.ground_under(grid, P + [0, 100, 0])
    ride = np.nanmedian(P[:, 1] - g) if (~np.isnan(g)).any() else 0.0
    miss = np.isnan(g) | (P[:, 1] - g > ride + AIR)
    n = len(P)
    if miss.all() or not miss.any():
        return np.zeros((0, 3, 3))
    k0 = int(np.argmin(miss))  # (walk the loop from a point with ground)
    order = (k0 + np.arange(n)) % n
    runs, cur = [], []
    for i in order:
        if miss[i]:
            cur.append(i)
        elif cur:
            runs.append(cur)
            cur = []
    if cur:
        runs.append(cur)
    tris = []
    step = max(1, int(BRIDGE_STEP / 40.0))
    for r in runs:
        if len(r) * 40.0 < BRIDGE_MIN:
            continue
        ext = 8  # (onto the ground either side)
        idx = [(r[0] - k) % n for k in range(ext, 0, -1)] + r + [(r[-1] + k) % n for k in range(1, ext + 1)]
        Q = P[idx[::step] + [idx[-1]]].copy()
        Q[:, 1] -= ride
        for p, q in zip(Q[:-1], Q[1:]):
            d = q - p
            side = np.array([d[2], 0.0, -d[0]]) / max(np.hypot(d[0], d[2]), 1e-6) * BRIDGE_HALF
            pl, pr, ql, qr = p + side, p - side, q + side, q - side
            for t in ((pl, pr, ql), (pr, qr, ql)):
                A, B, C = t
                if nf_collision.face_normals(A[None], B[None], C[None])[0, 1] < 0:
                    B, C = C, B
                tris.append((A, B, C))
    return np.round(np.array(tris)).reshape(-1, 3, 3)


def grounded_keep(m, t, ground):
    """keep(curve NF units): ground (PS1 heights, NaN none) under GROUNDED of it"""
    def keep(c):
        P = c[::8] @ m.T + t
        return np.mean(~np.isnan(ground(P + [0, 100, 0]))) >= GROUNDED
    return keep


def warps(line):
    """(entry, exit, exit yaw 0-4095 as DriverSpawn has it) of each warp
    segment of the race line (PS1 units)"""
    out = []
    n = len(line)
    for i in np.nonzero(np.linalg.norm(np.roll(line, -1, axis=0) - line, axis=1) > WARP_SEG)[0]:
        a, b = line[i], line[(i + 1) % n]
        d = line[(i + 4) % n] - b
        yaw = (np.arctan2(d[0], d[2]) - np.pi / 2) * 4096 / (2 * np.pi)
        out.append((np.round(a), np.round(b), int(round(yaw)) % 4096))
    return out


def race_line(tid, m, t, ground=None):
    """the race line in PS1 space, before any collision is built"""
    _, pak, _, _ = nf_tracks.native(tid)
    root, _ = nf_level.extract(pak)
    ldir = nf_level.level_dir(root)
    line, _, _ = lanes(ldir, player_start(ldir), grounded_keep(m, t, ground) if ground else None)
    return line @ m.T + t


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    import nf_collision
    for tid in argv[1:]:
        _, pak, name, host = nf_tracks.native(tid)
        root, _ = nf_level.extract(pak)
        ldir = nf_level.level_dir(root)
        st = player_start(ldir)
        line, (mid, left, right), gaps = lanes(ldir, st)
        _, L = arc(line)
        _, how = start_line(ldir, line)
        _, d = nf_collision.nearest(line, st[None])
        lane_off = [float(np.median(nf_collision.nearest(line, x)[1])) / 1.5 for x in (mid, left, right)]
        it = items(ldir)
        print(f"{tid} {name}: race line {L / 1.5:.0f} PS1 units, joint gaps {np.round(np.array(gaps) / 1.5).astype(int).tolist()}, "
              f"lanes off the line {np.round(lane_off).astype(int).tolist()}, player start {d[0] / 1.5:.0f} off the line, start line from {how}, "
              f"items { {k: len(v) for k, v in it.items()} }")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
