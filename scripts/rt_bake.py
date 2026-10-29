"""Bake ray-traced lighting into the vertex colours of every custom track.

PS1 can't ray trace, but it draws level geometry Gouraud-shaded: each texel is
multiplied by the interpolated vertex colour (0x80 = unchanged). So the path
tracer in tools/rt_bake/ runs offline on each track's mesh (sun shadows, sky
light / AO, 2 bounces) and the result is multiplied into LevVertex.color_hi /
color_lo. Only those bytes change; size, pointers and ptr map stay the same.

Stock levels are baked too: every race/battle slot and LOD ({1P,2P,4P,relic})
is copied once from the USA disc to stock_tracks/<slot>-<lod>/track.lev, and
pack_tracks.py swaps the baked ones into BIGFILE.BIG (so Adventure and cups get
them as well). The Adventure hubs and the character select garage
(EXTRA_LEVELS) are copied to stock_tracks/<levelID>-0 for the native ray
tracer's lights only: marked native_only, the ROM keeps them unbaked.

Output: <dir>/track.baked.lev + track.baked.json (stamp: lev md5, params, code
md5) for every dir in tracks/ and stock_tracks/. pack_tracks.py uses a baked lev
only when its stamp matches the lev it replaces. Lighting params can be
overridden per dir in <dir>/bake.json (keys as in DEFAULTS). Dir names listed in
skip_bake.txt are left unlit.

Usage: python -I scripts/rt_bake.py [--force] [--obj DIR] [dir name ...]
       python -I scripts/rt_bake.py --export-lights DIR   (live lighting for the native ray tracer)
"""
import hashlib
import json
import math
import pathlib
import subprocess
import sys
import tempfile
import time

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
TRACK_DIR = ROOT / "tracks"
STOCK_DIR = ROOT / "stock_tracks"
SKIP_FILE = ROOT / "skip_bake.txt"
LODS = ["1P", "2P", "4P", "relic"]
TRACER_SRC = ROOT / "tools/rt_bake/rt_bake.c"
TRACER = ROOT / "tools/rt_bake/rt_bake"
# levels besides the race/battle slots the native game ray traces: levelID ->
# (BIGFILE.BIG lev entry, name). Hubs: 200 + 3 * (levelID - 25) + {vrm, lev, ptr
# map}; garage: main menu files 215 + 2 + {vrm, lev, ptr map} (LOAD_GetBigfileIndex)
EXTRA_LEVELS = {25: (201, "Gem Stone Valley"), 26: (204, "N. Sanity Beach"), 27: (207, "The Lost Ruins"),
                28: (210, "Glacier Park"), 29: (213, "Citadel City"), 40: (218, "Character Select")}

DEFAULTS = {
    "mode": "auto",  # outdoor (sun + sky) / indoor (ambient occlusion) / auto (by openness)
    "sun_elevation": 40.0,  # degrees above the horizon; low sun = long shadows
    "sun_azimuth": 35.0,  # degrees, around +Y from +X towards +Z
    "sun_color": [1.0, 0.93, 0.80],
    "sun_intensity": 2.6,
    "sun_cone": 1.0,  # degrees, soft shadow edge
    "sky_zenith": [0.30, 0.38, 0.55],
    "sky_horizon": [0.45, 0.48, 0.55],
    "ground_color": [0.10, 0.09, 0.08],  # rays that escape downwards (track edges)
    "indoor_ambient": [0.40, 0.42, 0.48],  # indoor: light from all directions no nearby wall blocks
    "indoor_ao_radius": 1500.0,  # indoor: occluders further than this (level units) don't count
    "indoor_light_spacing": 2000.0,  # indoor: one point light per cube this size holding driving surface
    "indoor_light_height": 700.0,  # above the surface (or 60% of the way to the ceiling)
    "indoor_light_color": [1.0, 0.86, 0.66],
    "indoor_light_intensity": 2.2,
    "indoor_light_falloff": 1100.0,  # distance where a light is down to half
    "indoor_light_size": 80.0,  # bulb radius: soft shadow edge
    "light_samples": 8,
    "exposure_floor": 0.7,  # median ground darker than this x open-sky light -> brighten up to it
    "indoor_below": 0.25,  # auto: openness under this = indoor
    "samples": 256,
    "sun_samples": 24,
    "bounces": 2,
    "albedo": 0.5,  # bounce albedo, tinted by the track's own vertex colours
    "strength": 1.0,  # 0 = original colours, 1 = full baked lighting
    "min_factor": 0.22,  # darkest a vertex gets (x original colour)
    "max_factor": 1.6,  # brightest (bounce light, PS1 colour 0x80 = 1.0, 0xFF ~ 2.0)
    "eps": 4.0,  # ray offset in level units
}

# LevVertex (include/namespace_Level.h): short pos[3]; u16 flags; u8 color_hi[4]; u8 color_lo[4]
VERT = np.dtype([("pos", "<i2", 3), ("flags", "<u2"), ("hi", "u1", 4), ("lo", "u1", 4)])
QUAD_SIZE = 0x5C
LUMA = np.array([0.299, 0.587, 0.114])
# QuadBlock.quadFlags bits as named by ctr-tools (not defined in this CTR-ModSDK checkout)
QUAD_INVISIBLE, QUAD_GROUND, QUAD_INVISIBLE_TRIGGER = 0x1, 0x1000, 0x8000
# QuadBlock.index[9] (read as u16: big levels pass 32767 verts) -> triangles, from
# COLL_FIXED_QUADBLK_TestTriangles (hi LOD); the last 4 only exist when
# index[2] != index[3] (quad, not triangle)
HI_TRIS = [(8, 6, 7), (7, 3, 8), (1, 7, 6), (2, 6, 8), (0, 4, 5), (4, 6, 5), (6, 4, 1), (5, 6, 2)]


def md5(b):
    return hashlib.md5(b).hexdigest()


def code_md5():
    return md5(TRACER_SRC.read_bytes() + pathlib.Path(__file__).read_bytes())


def build_tracer():
    if TRACER.exists() and TRACER.stat().st_mtime >= TRACER_SRC.stat().st_mtime:
        return
    subprocess.check_call(["cc", "-O2", "-fopenmp", "-o", str(TRACER), str(TRACER_SRC), "-lm"])


class Mesh:
    """The level mesh of one .lev: [u32 map_off][Level body][u32 map_bytes][ptr map]."""

    def __init__(self, lev):
        self.lev = lev
        body_end = 4 + int.from_bytes(lev[0:4], "little")
        mesh_info = int.from_bytes(lev[4:8], "little")  # Level.ptr_mesh_info
        nq, nv, _, pq, pv = np.frombuffer(lev, "<u4", 5, 4 + mesh_info)
        if not (0 < nv and pv + nv * VERT.itemsize <= body_end and pq + nq * QUAD_SIZE <= body_end):
            raise ValueError("mesh_info out of range")
        self.vert_off = 4 + int(pv)
        self.verts = np.frombuffer(lev, VERT, int(nv), self.vert_off)
        quads = np.frombuffer(lev, "u1", int(nq) * QUAD_SIZE, 4 + int(pq)).reshape(-1, QUAD_SIZE)
        self.index = quads[:, 0:18].copy().view("<u2").astype(np.int64)
        self.flags = quads[:, 18:20].copy().view("<u2")[:, 0]
        if self.index.max() >= nv:
            raise ValueError("quadblock vertex index out of range")

    def triangles(self):
        """Visible hi-LOD triangles, non-degenerate."""
        idx = self.index[(self.flags & (QUAD_INVISIBLE | QUAD_INVISIBLE_TRIGGER)) == 0]
        is_quad = idx[:, 2] != idx[:, 3]
        tris = [idx[:, list(t)] if k < 4 else idx[is_quad][:, list(t)] for k, t in enumerate(HI_TRIS)]
        tris = np.concatenate(tris)
        ok = (tris[:, 0] != tris[:, 1]) & (tris[:, 1] != tris[:, 2]) & (tris[:, 0] != tris[:, 2])
        return tris[ok]

    def ground_verts(self):
        return np.unique(self.index[(self.flags & QUAD_GROUND) != 0])


def vertex_normals(pos, tris):
    """Area-weighted vertex normals. CTR is Y up with clockwise winding, so face
    normal = (C-A) x (B-A); ground faces then point +Y."""
    a, b, c = pos[tris[:, 0]], pos[tris[:, 1]], pos[tris[:, 2]]
    fn = np.cross(c - a, b - a)
    n = np.zeros_like(pos)
    for k in range(3):
        np.add.at(n, tris[:, k], fn)
    length = np.linalg.norm(n, axis=1, keepdims=True)
    return np.where(length > 1e-6, n / np.maximum(length, 1e-6), 0.0)


def light_bases(mesh, pos, nrm, spacing):
    """One light base per spacing^3 cell holding floor: the centre vertex
    (index[6]) of visible quadblocks facing up (ground flags are too sparse in
    some levels, e.g. N. Gin Labs)."""
    quads = mesh.index[(mesh.flags & (QUAD_INVISIBLE | QUAD_INVISIBLE_TRIGGER)) == 0]
    centres = np.unique(quads[:, 6])
    centres = centres[nrm[centres, 1] > 0.7]
    _, first = np.unique(np.floor(pos[centres] / spacing).astype(np.int64), axis=0, return_index=True)
    return pos[centres[np.sort(first)]]


def scene_bytes(p, pos, nrm, alb, tris, indoor, lights=()):
    el, az = math.radians(p["sun_elevation"]), math.radians(p["sun_azimuth"])
    sun_dir = [math.cos(el) * math.cos(az), math.sin(el), math.cos(el) * math.sin(az)]
    if indoor:  # no sun; uniform ambient from every direction a nearby wall doesn't block
        sun, zen, hor, gnd, ao = [0.0] * 3, p["indoor_ambient"], p["indoor_ambient"], p["indoor_ambient"], p["indoor_ao_radius"]
    else:
        sun, zen, hor, gnd, ao = [c * p["sun_intensity"] for c in p["sun_color"]], p["sky_zenith"], p["sky_horizon"], p["ground_color"], 0.0
    floats = sun_dir + sun + [math.radians(p["sun_cone"])] + zen + hor + gnd
    head = np.array([0x4B425452, len(pos), len(tris)], "<u4").tobytes()
    head += np.array(floats, "<f4").tobytes()
    head += np.array([p["samples"], p["sun_samples"], p["bounces"]], "<u4").tobytes()
    head += np.array([p["eps"], ao], "<f4").tobytes()
    arrays = [a.astype("<f4").tobytes() for a in (pos, nrm, alb)]
    light = [[*b, p["indoor_light_height"], *(c * p["indoor_light_intensity"] for c in p["indoor_light_color"]),
              p["indoor_light_falloff"], p["indoor_light_size"]] for b in lights]
    tail = np.array([len(light), p["light_samples"]], "<u4").tobytes() + np.array(light, "<f4").tobytes()
    return head + b"".join(arrays) + tris.astype("<u4").tobytes() + tail


def open_irradiance(p, indoor):
    """Irradiance of flat, unoccluded ground: what maps to factor 1.0. The tracer's
    cosine-weighted sky samples see sky(d) = mix(horizon, zenith, sqrt(d.y)), whose
    mean is 0.2 horizon + 0.8 zenith."""
    if indoor:
        return np.array(p["indoor_ambient"], float)
    sky = 0.2 * np.array(p["sky_horizon"]) + 0.8 * np.array(p["sky_zenith"])
    return np.array(p["sun_color"]) * p["sun_intensity"] * math.sin(math.radians(p["sun_elevation"])) + sky


def trace(scene):
    with tempfile.TemporaryDirectory() as tmp:
        src, dst = pathlib.Path(tmp, "scene.bin"), pathlib.Path(tmp, "irr.bin")
        src.write_bytes(scene)
        r = subprocess.run([str(TRACER), str(src), str(dst)], capture_output=True, text=True)
        if r.returncode:
            raise ValueError(f"tracer failed: {r.stderr.strip()}")
        return np.frombuffer(dst.read_bytes(), "<f4").reshape(-1, 3).astype(np.float64)


def bake(lev, p):
    """Return (baked lev bytes, stats dict, (pos, tris, baked rgb) for previews)."""
    mesh = Mesh(lev)
    v = mesh.verts
    pos = v["pos"].astype(np.float64)
    tris = mesh.triangles()
    nrm = vertex_normals(pos, tris)
    tint = np.clip(v["hi"][:, :3] / 128.0, 0.0, 2.0)
    alb = np.clip(p["albedo"] * tint, 0.0, 0.9)
    lit = np.linalg.norm(nrm, axis=1) > 0
    ground = mesh.ground_verts()
    ground = ground[lit[ground]] if lit[ground].any() else np.flatnonzero(lit)

    # openness: median ground light vs open flat ground, outdoors. Low = indoor or
    # tunnel track: no sun or sky gets in, so light it with ambient occlusion instead
    irr = trace(scene_bytes(p, pos, nrm, alb, tris, False))
    openness = float(np.median(irr[ground] @ LUMA) / (open_irradiance(p, False) @ LUMA))
    indoor = p["mode"] == "indoor" or (p["mode"] == "auto" and openness < p["indoor_below"])
    lights = ()
    if indoor:
        lights = light_bases(mesh, pos, nrm, p["indoor_light_spacing"])
        irr = trace(scene_bytes(p, pos, nrm, alb, tris, True, lights))

    # per channel, so open sunlit ground keeps its colour; shade and bounce tint it.
    # Indoors every floor is under some ceiling, so the typical floor is the reference
    # and only corners, contact and enclosed spots go darker
    ref = open_irradiance(p, indoor)
    if indoor:
        ref = ref * np.median(irr[ground] @ LUMA) / (ref @ LUMA)
    else:  # half-enclosed: auto-exposure, like a camera would
        ref = ref * min(1.0, np.median(irr[ground] @ LUMA) / (ref @ LUMA) / p["exposure_floor"])
    factor = 1.0 + p["strength"] * (irr / ref - 1.0)
    factor = np.clip(factor, p["min_factor"], p["max_factor"])
    factor[~lit] = 1.0

    out = bytearray(lev)
    baked = np.frombuffer(out, VERT, len(v), mesh.vert_off)  # view into out
    for key in ("hi", "lo"):
        rgb = np.rint(v[key][:, :3] * factor)
        baked[key][:, :3] = np.clip(rgb, 0, 255).astype(np.uint8)
    out = bytes(out)
    check_only_colors_changed(lev, out, mesh)
    stats = {"verts": len(v), "tris": len(tris), "mean_factor": round(float(factor[lit].mean()), 3),
             "shadowed": round(float((factor[lit].mean(axis=1) < 0.8).mean()), 3),
             "openness": round(openness, 2), "mode": "indoor" if indoor else "outdoor", "lights": len(lights)}
    return out, stats, (pos, tris, baked["hi"][:, :3].copy())


def check_only_colors_changed(old, new, mesh):
    assert len(old) == len(new)
    diff = np.flatnonzero(np.frombuffer(old, "u1") != np.frombuffer(new, "u1"))
    rel = diff - mesh.vert_off
    ok = (rel >= 0) & (rel < len(mesh.verts) * VERT.itemsize) & (rel % VERT.itemsize >= 8)
    assert ok.all(), f"bake touched non-colour bytes at {diff[~ok][:4]}"


def write_obj(path, pos, tris, rgb):
    """Y-up OBJ with vertex colours (Blender: Attribute 'Col'); faces flipped to CCW."""
    with open(path, "w") as f:
        for (x, y, z), (r, g, b) in zip(pos / 1000.0, rgb / 255.0):
            f.write(f"v {x:.4f} {y:.4f} {z:.4f} {r:.3f} {g:.3f} {b:.3f}\n")
        for a, b, c in tris + 1:
            f.write(f"f {a} {c} {b}\n")


def ensure_stock():
    """stock_tracks/<slot>-<lod>/{track.lev,entry.json} from the USA disc, once,
    and stock_tracks/<levelID>-0 for EXTRA_LEVELS."""
    extra_done = all((STOCK_DIR / f"{lev}-0" / "entry.json").exists() for lev in EXTRA_LEVELS)
    if (STOCK_DIR / ".complete").exists() and extra_done:
        return
    sys.path.insert(0, str(ROOT / "scripts"))
    import extract_tracks as et

    disc = et.Disc(et.USA_BIN)
    big = disc.bigfile()
    disc.close()
    for slot in range(et.NUM_SLOTS):
        for lod in range(4):
            entry = slot * 8 + lod * 2 + 1  # {vrm, lev} per LOD
            if not big[entry]:
                continue
            d = STOCK_DIR / f"{slot:02d}-{lod}"
            d.mkdir(parents=True, exist_ok=True)
            (d / "track.lev").write_bytes(big[entry])
            (d / "entry.json").write_text(json.dumps({"entry": entry, "name": f"{et.SLOT_NAMES[slot]} {LODS[lod]}"}))
    for lev, (entry, name) in EXTRA_LEVELS.items():
        d = STOCK_DIR / f"{lev}-0"
        d.mkdir(parents=True, exist_ok=True)
        (d / "track.lev").write_bytes(big[entry])
        (d / "entry.json").write_text(json.dumps({"entry": entry, "name": name, "native_only": True}))
    (STOCK_DIR / ".complete").touch()


def params_for(d):
    p = dict(DEFAULTS)
    override = d / "bake.json"
    if override.exists():
        extra = json.loads(override.read_text())
        unknown = set(extra) - set(DEFAULTS)
        if unknown:
            raise SystemExit(f"{override}: unknown keys {sorted(unknown)}")
        p.update(extra)
    return p


def lights_text(d, p):
    """Lighting description of one level for the native ray tracer
    (native/rtx/ctr_rtx.c loadLighting). The live lighting is worked out from the
    level's own art (sun fitted to the vertex colours, lamp pools, glowing
    surfaces); this only says whether the track is indoors (no sun), with the
    mode picked like the bake does. Per-level overrides for the native renderer
    live in native/rtx/overrides/<key>.txt (copied next to these by build_native.sh)."""
    stamp = d / "track.baked.json"
    mode = json.loads(stamp.read_text())["stats"]["mode"] if stamp.exists() else ("indoor" if p["mode"] == "indoor" else "outdoor")
    lines = [f"indoor {int(mode == 'indoor')}"]
    return "\n".join(lines) + "\n"


def export_lights(out_dir):
    """out_dir/<key>.txt for every level the native game can load: key is
    custom-<first bigfile entry> (as packed by pack_tracks.py), stock-<levelID>,
    or nf-<id> (a Nitro-Fueled track on a remake, or a native one: its host
    slot's)."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import pack_tracks

    out_dir.mkdir(parents=True, exist_ok=True)
    skip = set(SKIP_FILE.read_text().split()) if SKIP_FILE.exists() else set()
    n = 0
    metas = pack_tracks.track_list()
    for meta, base in zip(metas, pack_tracks.big_bases(metas)):
        if meta.get("nf") and meta.get("host"):
            # a Nitro-Fueled track hosted on a remake: the remake's lighting (renderer key nf-<id>)
            d = TRACK_DIR / meta["host"]
            if d.name not in skip:
                (out_dir / f"{meta['id']}.txt").write_text(lights_text(d, params_for(d)))
                n += 1
            continue
        if meta.get("nf") and str(meta["id"]).startswith("nf-t"):
            # a native Nitro-Fueled track (no PS1 level): its host slot's lighting
            d = STOCK_DIR / f"{meta['slot']:02d}-0"
            if d.exists() and d.name not in skip:
                (out_dir / f"{meta['id']}.txt").write_text(lights_text(d, params_for(d)))
                n += 1
            continue
        if meta.get("nf"):
            continue  # Nitro-Fueled tracks: the stock level's file (renderer key nf-<slot>)
        d = TRACK_DIR / meta["id"]
        if d.name not in skip:
            (out_dir / f"custom-{base}.txt").write_text(lights_text(d, params_for(d)))
            n += 1
    for d in sorted(STOCK_DIR.glob("*-0")):  # 1P level of each slot
        if d.name not in skip:
            (out_dir / f"stock-{int(d.name.split('-')[0])}.txt").write_text(lights_text(d, params_for(d)))
            n += 1
    print(f"[rt_bake] {n} lighting files in {out_dir}")


def main():
    args = sys.argv[1:]
    if "--export-lights" in args:
        export_lights(pathlib.Path(args[args.index("--export-lights") + 1]))
        return
    force = "--force" in args
    obj_dir = None
    if "--obj" in args:
        i = args.index("--obj")
        obj_dir = pathlib.Path(args[i + 1])
        obj_dir.mkdir(parents=True, exist_ok=True)
        del args[i:i + 2]
    ids = [a for a in args if not a.startswith("--")]
    skip = set(SKIP_FILE.read_text().split()) if SKIP_FILE.exists() else set()

    build_tracer()
    ensure_stock()
    code = code_md5()
    every = sorted(d for d in TRACK_DIR.iterdir() if (d / "meta.json").exists())
    every += sorted(d for d in STOCK_DIR.iterdir() if (d / "entry.json").exists())
    dirs = [d for d in every if d.name in ids] if ids else every
    done = cached = 0
    for d in dirs:
        baked_lev, stamp_path = d / "track.baked.lev", d / "track.baked.json"
        if d.name in skip:
            baked_lev.unlink(missing_ok=True)
            stamp_path.unlink(missing_ok=True)
            continue
        lev = (d / "track.lev").read_bytes()
        p = params_for(d)
        stamp = {"lev_md5": md5(lev), "params": p, "code_md5": code}
        if not force and obj_dir is None and stamp_path.exists() and baked_lev.exists():
            old = json.loads(stamp_path.read_text())
            if {k: old.get(k) for k in stamp} == stamp:
                cached += 1
                continue
        t0 = time.time()
        try:
            out, stats, geo = bake(lev, p)
        except (ValueError, AssertionError) as e:
            print(f"[rt_bake] {d.name}: FAILED ({e}), left unlit")
            baked_lev.unlink(missing_ok=True)
            stamp_path.unlink(missing_ok=True)
            continue
        baked_lev.write_bytes(out)
        stamp_path.write_text(json.dumps({**stamp, "baked_md5": md5(out), "stats": stats}, indent=1))
        if obj_dir is not None:
            pos, tris, rgb = geo
            write_obj(obj_dir / f"{d.name}.obj", pos, tris, rgb.astype(np.float64))
            write_obj(obj_dir / f"{d.name}.orig.obj", pos, tris, Mesh(lev).verts["hi"][:, :3].astype(np.float64))
        done += 1
        print(f"[rt_bake] {d.name}: {stats['verts']} verts, {stats['tris']} tris, "
              f"mean x{stats['mean_factor']}, {stats['shadowed'] * 100:.0f}% shadowed, "
              f"openness {stats['openness']} ({stats['mode']}, {stats['lights']} lights), {time.time() - t0:.1f}s")
    print(f"[rt_bake] baked {done}, cached {cached}, skipped {len(skip & {d.name for d in dirs})}")


if __name__ == "__main__":
    main()
