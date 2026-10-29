#!/usr/bin/env python3
"""Havok 2014 packfiles (Nitro-Fueled's StaticCollision_<level>.hkx): sections,
pointer fixups, objects by class.

    nf_hkx.py <file.hkx>      list the objects
"""
import struct
import sys


class Packfile:
    def __init__(self, path):
        d = open(path, "rb").read()
        if d[:8] != bytes.fromhex("57e0e05710c0c010"):
            raise ValueError(f"{path}: not a Havok packfile")
        self.version = struct.unpack_from("<I", d, 0x0C)[0]
        self.ptr_size = d[0x10]
        nsec = struct.unpack_from("<I", d, 0x14)[0]
        pred = struct.unpack_from("<H", d, 0x3E)[0] if self.version >= 11 else 0
        at = 0x40 + pred
        self.sections = {}
        for _ in range(nsec):
            tag = d[at:at + 19].split(b"\0")[0].decode()
            start, loc, glob, virt, exp, imp, end = struct.unpack_from("<7I", d, at + 20)
            self.sections[tag] = dict(start=start, loc=loc, glob=glob, virt=virt, exp=exp, imp=imp, end=end)
            at += 0x40 if self.version >= 11 else 0x30
        self.d = d
        s = self.sections["__data__"]
        self.data = d[s["start"]:s["start"] + s["loc"]]
        # local fixups: pointer at src -> data offset dst
        self.ptr = {}
        for o in range(s["start"] + s["loc"], s["start"] + s["glob"] - 7, 8):
            src, dst = struct.unpack_from("<2I", d, o)
            if src == 0xFFFFFFFF:
                continue
            self.ptr[src] = dst
        for o in range(s["start"] + s["glob"], s["start"] + s["virt"] - 11, 12):
            src, sec, dst = struct.unpack_from("<3I", d, o)
            if src == 0xFFFFFFFF:
                continue
            self.ptr[src] = dst  # (all within __data__ here)
        cn = self.sections["__classnames__"]
        self.objects = []  # (offset, class name)
        for o in range(s["start"] + s["virt"], s["start"] + s["exp"] - 11, 12):
            src, sec, name = struct.unpack_from("<3I", d, o)
            if src == 0xFFFFFFFF:
                continue
            nm = d[cn["start"] + name:].split(b"\0")[0].decode()
            self.objects.append((src, nm))
        self.objects.sort()

    def of_class(self, name):
        return [o for o, n in self.objects if n == name]

    def u32(self, o):
        return struct.unpack_from("<I", self.data, o)[0]

    def array(self, o):
        """hkArray at o: (data offset or None, size)"""
        size = struct.unpack_from("<I", self.data, o + self.ptr_size)[0]
        return self.ptr.get(o), size


# ---- hknp shapes (Havok 2014, 64-bit layout) ----------------------------------------

# compressed mesh shape tags: Nitro-Fueled's global collision material ids
# (loosedata/collisionmaterials.igz), solved from each mesh's material palette
# (shape_palette: the names it uses, unordered) over all levels; pairs the
# palettes leave open settled by that file's name order and by shape
MATERIALS = {
    0: "Unset", 4: "Default", 5: "Grass", 6: "Rock", 7: "Dirt", 8: "Snow", 9: "Metal", 10: "Ice", 11: "WaterShallow",
    12: "WaterDeep", 13: "Wood", 16: "Respawn", 25: "Mud", 27: "Goo", 31: "SpeedBoost", 32: "SpeedBoostSuper",
    33: "SandSlow", 34: "RockSlow", 35: "GrassSlow", 36: "DirtSlow", 37: "SnowSlow", 38: "SandDark", 40: "WaterShallowSlow",
    41: "WaterDeepSlow", 42: "MudSlow", 43: "HotAirBalloon", 46: "MetalGrid", 47: "Sand", 48: "Glass", 49: "SandWet",
    50: "RockAlwaysFloor", 51: "MetalAlwaysFloor", 52: "WaterAlwaysFloor", 53: "DeepMud", 55: "Asphalt", 56: "RockWet",
    57: "WoodWet", 58: "MudSlowNoSplash", 59: "SnowAlwaysFloor", 60: "MudDry", 61: "GrassSlowSlower", 62: "DirtSlowSlower",
    63: "MetalWet",
}


def material(tag):
    """material name of a shape tag (-1: convex / sphere shapes: Default)"""
    return MATERIALS.get(int(tag), "Default" if tag < 0 else f"Tag{int(tag)}")


def shape_palette(p, shape):
    """names of the collision materials a shape uses (its hkRefCountedProperties'
    hknpShapeMaterialStringPalette, key 0x8899), or []"""
    props = p.ptr.get(shape + 0x20)
    if props is None:
        return []
    objs = dict(p.objects)
    at, n = p.array(props)
    for k in range(n):
        obj = p.ptr.get(at + 0x10 * k)
        if obj is None or objs.get(obj) != "hknpShapeMaterialStringPalette":
            continue
        sa, sn = p.array(obj + 0x10)
        return [p.data[p.ptr[sa + 8 * j]:].split(b"\0")[0].decode() for j in range(sn) if (sa + 8 * j) in p.ptr]
    return []

def mesh_shape(p, shape):
    """hknpCompressedMeshShape -> (vertices (N, 3) shape space, triangles (M, 3),
    primitive data run value per triangle)"""
    import numpy as np
    data = p.ptr[shape + 0x58]  # hknpCompressedMeshShapeData
    dmin = np.array(struct.unpack_from("<3f", p.data, data + 0x20))
    dmax = np.array(struct.unpack_from("<3f", p.data, data + 0x30))
    sec_at, nsec = p.array(data + 0x50)
    prim_at, nprim = p.array(data + 0x60)
    sidx_at, nsidx = p.array(data + 0x70)
    pv_at, npv = p.array(data + 0x80)
    sv_at, nsv = p.array(data + 0x90)
    run_at, nrun = p.array(data + 0xA0)
    prims = np.frombuffer(p.data, "u1", nprim * 4, prim_at).reshape(-1, 4).astype(np.int64) if nprim else np.zeros((0, 4), np.int64)
    sidx = np.frombuffer(p.data, "<u2", nsidx, sidx_at).astype(np.int64) if nsidx else np.zeros(0, np.int64)
    pv = np.frombuffer(p.data, "<u4", npv, pv_at).astype(np.int64) if npv else np.zeros(0, np.int64)
    sv = np.frombuffer(p.data, "<u8", nsv, sv_at).astype(np.uint64) if nsv else np.zeros(0, np.uint64)
    runs = [struct.unpack_from("<HBB", p.data, run_at + 4 * k) for k in range(nrun)] if nrun else []  # (shape tag, first primitive, count)
    # shared vertices: 21 / 21 / 22 bits over the mesh's domain
    if nsv:
        q = np.stack([(sv & np.uint64(0x1FFFFF)), (sv >> np.uint64(21)) & np.uint64(0x1FFFFF), sv >> np.uint64(42)], 1).astype(np.float64)
        shared = dmin + q * ((dmax - dmin) / np.array([0x1FFFFF, 0x1FFFFF, 0x3FFFFF], np.float64))
    else:
        shared = np.zeros((0, 3))
    verts, tris, vals = [], [], []
    for k in range(nsec):
        o = sec_at + 0x60 * k
        cp = np.array(struct.unpack_from("<6f", p.data, o + 0x30))
        first_pv, shv, prm, drs = struct.unpack_from("<4I", p.data, o + 0x48)
        n_pv, n_sh = p.data[o + 0x58], p.data[o + 0x59]
        q = pv[first_pv:first_pv + n_pv]
        packed = cp[:3] + np.stack([q & 0x7FF, (q >> 11) & 0x7FF, q >> 22], 1) * cp[3:]
        sh = shared[sidx[(shv >> 8) + np.arange(n_sh)]] if n_sh else np.zeros((0, 3))
        local = np.concatenate([packed, sh])
        base = sum(len(v) for v in verts)
        verts.append(local)
        p0, pc = prm >> 8, prm & 0xFF
        r0, rc = drs >> 8, drs & 0xFF
        val = np.zeros(pc, np.int64)
        for value, start, count in runs[r0:r0 + rc]:
            val[start:start + count] = value
        for j, ix in enumerate(prims[p0:p0 + pc]):
            tris.append((base + ix[0], base + ix[1], base + ix[2]))
            vals.append(val[j])
            if ix[2] != ix[3]:
                tris.append((base + ix[0], base + ix[2], base + ix[3]))
                vals.append(val[j])
    V = np.concatenate(verts) if verts else np.zeros((0, 3))
    return V, np.array(tris, np.int64).reshape(-1, 3), np.array(vals, np.int64)


def compound_instances(p):
    """hknpStaticCompoundShape instances: (4x4 matrix shape -> level space,
    scale (3,), shape offset, shape class)"""
    import numpy as np
    out = []
    for comp in p.of_class("hknpStaticCompoundShape"):
        at, n = p.array(comp + 0x60)
        for k in range(n):
            o = at + 0x80 * k
            if o + 0x50 not in p.ptr:
                continue  # (a free slot)
            cols = np.array(struct.unpack_from("<16f", p.data, o)).reshape(4, 4)
            m = np.eye(4)
            m[:3, :3] = cols[:3, :3].T  # (columns)
            m[:3, 3] = cols[3, :3]
            scale = np.array(struct.unpack_from("<3f", p.data, o + 0x40))
            shape = p.ptr[o + 0x50]
            out.append((m, scale, shape, dict(p.objects).get(shape, "?")))
    return out


def convex_shape(p, shape):
    """hknpConvexPolytopeShape -> (vertices (N, 3), triangles (M, 3)) from its
    faces (fans), each facing out along its plane"""
    import numpy as np

    def rel(field):  # hkRelArray: u16 count, u16 offset from the field
        n, off = struct.unpack_from("<2H", p.data, shape + field)
        return shape + field + off, n
    va, nv = rel(0x28)
    pa, _ = rel(0x2C)
    fa, nf = rel(0x30)
    ia, ni = rel(0x34)
    V = np.array([struct.unpack_from("<3f", p.data, va + 16 * k) for k in range(nv)])
    idx = np.frombuffer(p.data, "u1", ni, ia).astype(np.int64)
    tris = []
    for k in range(nf):
        first, count = struct.unpack_from("<HB", p.data, fa + 4 * k)
        plane = np.array(struct.unpack_from("<3f", p.data, pa + 16 * k))
        f = idx[first:first + count]
        for j in range(1, count - 1):
            t = [f[0], f[j], f[j + 1]]
            n = np.cross(V[t[1]] - V[t[0]], V[t[2]] - V[t[0]])
            tris.append(t if n @ plane >= 0 else [t[0], t[2], t[1]])  # (right handed, out)
    return V, np.array(tris, np.int64).reshape(-1, 3)


def sphere_shape(p, shape):
    """hknpSphereShape -> an icosahedron round its centre (radius at +0x14)"""
    import numpy as np
    r = struct.unpack_from("<f", p.data, shape + 0x14)[0]
    n, off = struct.unpack_from("<2H", p.data, shape + 0x28)
    c = np.array(struct.unpack_from("<3f", p.data, shape + 0x28 + off))
    g = (1 + 5 ** 0.5) / 2
    V = np.array([(-1, g, 0), (1, g, 0), (-1, -g, 0), (1, -g, 0), (0, -1, g), (0, 1, g), (0, -1, -g), (0, 1, -g),
                  (g, 0, -1), (g, 0, 1), (-g, 0, -1), (-g, 0, 1)], np.float64)
    V = c + V / np.linalg.norm(V, axis=1, keepdims=True) * r
    T = np.array([(0, 11, 5), (0, 5, 1), (0, 1, 7), (0, 7, 10), (0, 10, 11), (1, 5, 9), (5, 11, 4), (11, 10, 2), (10, 7, 6), (7, 1, 8),
                  (3, 9, 4), (3, 4, 2), (3, 2, 6), (3, 6, 8), (3, 8, 9), (4, 9, 5), (2, 4, 11), (6, 2, 10), (8, 6, 7), (9, 8, 1)], np.int64)
    return V, T


def level_triangles(path):
    """every collision triangle of a StaticCollision_<level>.hkx in level
    space (Havok: metres, Z up, right handed counter-clockwise = outward):
    (a, b, c (N, 3) each, shape tag per triangle (-1: convex / sphere))"""
    import numpy as np
    p = Packfile(path)
    A, B, C, tags = [], [], [], []
    for m, scale, shape, cls in compound_instances(p):
        if cls == "hknpCompressedMeshShape":
            V, T, val = mesh_shape(p, shape)
        elif cls == "hknpConvexPolytopeShape":
            V, T = convex_shape(p, shape)
            val = np.full(len(T), -1)
        elif cls == "hknpSphereShape":
            V, T = sphere_shape(p, shape)
            val = np.full(len(T), -1)
        else:
            continue
        if not len(T):
            continue
        W = (V * scale) @ m[:3, :3].T + m[:3, 3]
        A.append(W[T[:, 0]])
        B.append(W[T[:, 1]])
        C.append(W[T[:, 2]])
        tags.append(val)
    return np.concatenate(A), np.concatenate(B), np.concatenate(C), np.concatenate(tags)


def main(argv):
    p = Packfile(argv[1])
    print(f"version {p.version}, pointers {p.ptr_size} bytes, data {len(p.data)} bytes, {len(p.ptr)} pointers")
    from collections import Counter
    for n, c in Counter(n for _, n in p.objects).most_common():
        print(f"  {c:4d} {n}  e.g. at {[hex(o) for o in p.of_class(n)[:3]]}")
    for o in p.of_class("hknpCompressedMeshShape"):
        _, _, val = mesh_shape(p, o)
        used = sorted(Counter(val.tolist()).items())
        print(f"  mesh {o:#x}: {[(material(t), c) for t, c in used]} palette {shape_palette(p, o)}")


if __name__ == "__main__":
    main(sys.argv)
