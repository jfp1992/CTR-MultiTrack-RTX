#!/usr/bin/env python3
"""Minimal reader for Alchemy engine IGZ files (version 0x0A, 64-bit PS4) as
used by Crash Team Racing Nitro-Fueled.

The format notes come from igLibrary's igIGZLoader (Apache-2.0,
NefariousTechSupport) and the fmt_alchemy_igz Noesis plugin: a header with up
to 32 section descriptors, a fixup section of tagged tables (TMET metatype
names, TSTR strings, RVTB object offsets, ROFS pointer fields, ...), then the
object pool and the memory pools that pointers address as
(pool << 27) | offset.

    nf_igz.py dump <file.igz>         list objects with their types
    nf_igz.py hex <file.igz> <offset> [len]
"""
import struct
import sys


def unpack_compressed_ints(data, count, version):
    """RVTB/ROFS/... tables: nibble varints, delta coded, scaled by 4."""
    out = []
    prev = 0
    pos = 0
    high = False

    def nibble():
        nonlocal pos, high
        if not high:
            v = data[pos] & 0xF
            high = True
        else:
            v = data[pos] >> 4
            pos += 1
            high = False
        return v

    for _ in range(count):
        cur = nibble()
        value = cur & 7
        shift = 3
        while cur & 8:
            cur = nibble()
            value |= (cur & 7) << shift
            shift += 3
        prev = prev + value * 4 + (4 if version < 9 else 0)
        out.append(prev)
    return out


class Igz:
    def __init__(self, path_or_bytes):
        if isinstance(path_or_bytes, (bytes, bytearray)):
            self.d = bytes(path_or_bytes)
            self.path = "<bytes>"
        else:
            self.path = path_or_bytes
            with open(path_or_bytes, "rb") as f:
                self.d = f.read()
        d = self.d
        magic, self.version, self.type_hash, self.platform, nfix = struct.unpack_from("<5I", d, 0)
        if magic != 0x49475A01:
            raise ValueError(f"{self.path}: not a little endian IGZ")
        if self.version != 0x0A:
            raise ValueError(f"{self.path}: IGZ version {self.version:#x} unsupported")
        self.sections = []
        for i in range(32):
            off, size, align, flags = struct.unpack_from("<4I", d, 0x18 + i * 16)
            if off == 0:
                break
            self.sections.append((off, size))
        # pools addressed by serialized pointers start at section 1
        self.pools = [s[0] for s in self.sections[1:]]
        self.metatypes = []
        self.metasizes = []
        self.strings = []
        self.externals = []      # EXNM (namespace, name)
        self.external_is_handle = []  # EXNM entry is a named handle (igHandle fields)
        self.thumbnails = []     # TMHN (size, ptr)
        self.ext_ids = []        # EXID (name hash, namespace hash)
        self.vtables = []        # object offsets (serialized)
        self.offsets = set()     # ROFS: fields holding serialized pointers
        self.string_refs = set()
        self.handles = set()
        self.root = None
        self.deps = []
        pos = self.sections[0][0]
        for _ in range(nfix):
            tag, count, length, start = struct.unpack_from("<4I", d, pos)
            tag = struct.pack("<I", tag).decode("latin-1")
            body = d[pos + start:pos + length]
            if tag in ("TMET", "TSTR", "TDEP"):
                lst = []
                p = 0
                for _ in range(count):
                    e = body.index(b"\0", p)
                    lst.append(body[p:e].decode("latin-1"))
                    # version > 7: TMET/TSTR entries are padded to 2 bytes
                    p = e + 1 if tag == "TDEP" else p + 2 + ((e - p) & ~1)
                {"TMET": self.metatypes, "TSTR": self.strings, "TDEP": self.deps}[tag].extend(lst)
            elif tag == "MTSZ":
                self.metasizes = list(struct.unpack_from(f"<{count}I", body, 0))
            elif tag == "EXNM":
                for j in range(count):
                    name_i, ns_i = struct.unpack_from("<II", body, j * 8)
                    self.externals.append((self.strings[ns_i & 0x7FFFFFFF],
                                           self.strings[name_i & 0x7FFFFFFF]))
                    self.external_is_handle.append(bool(ns_i & 0x80000000))
            elif tag == "EXID":
                self.ext_ids = [struct.unpack_from("<II", body, j * 8) for j in range(count)]
            elif tag == "TMHN":
                for j in range(count):
                    self.thumbnails.append(struct.unpack_from("<QQ", body, j * 16))
            elif tag == "RVTB":
                self.vtables = unpack_compressed_ints(body, count, self.version)
            elif tag == "ROFS":
                self.offsets = set(unpack_compressed_ints(body, count, self.version))
            elif tag in ("RSTT", "RSTR"):
                self.string_refs |= set(unpack_compressed_ints(body, count, self.version))
            elif tag in ("RHND", "RMHN"):
                self.handles |= set(unpack_compressed_ints(body, count, self.version))
            elif tag == "ROOT":
                self.root = unpack_compressed_ints(body, count, self.version)[0]
            pos += length
        self.ptr_fields = {self.addr(o) for o in self.offsets}
        self.handle_fields = {self.addr(o) for o in self.handles}
        self.objects = {}
        for v in self.vtables:
            a = self.addr(v)
            (ti,) = struct.unpack_from("<Q", d, a)
            self.objects[a] = self.metatypes[ti]

    # serialized offset -> file offset
    def addr(self, ser):
        return self.pools[ser >> 27] + (ser & 0x07FFFFFF)

    def u8(self, a): return self.d[a]
    def u16(self, a): return struct.unpack_from("<H", self.d, a)[0]
    def u32(self, a): return struct.unpack_from("<I", self.d, a)[0]
    def i32(self, a): return struct.unpack_from("<i", self.d, a)[0]
    def u64(self, a): return struct.unpack_from("<Q", self.d, a)[0]
    def f32(self, a): return struct.unpack_from("<f", self.d, a)[0]
    def vec(self, a, n): return struct.unpack_from(f"<{n}f", self.d, a)

    def ptr(self, a):
        """Object/memory pointer stored at file offset a, or None."""
        if a not in self.ptr_fields:
            return None
        raw = self.u64(a)
        if raw & 0x80000000:
            return None
        return self.addr(raw)

    def string(self, a):
        raw = self.u64(a)
        if raw < len(self.strings):
            return self.strings[raw]
        return None

    def obj_type(self, a):
        return self.objects.get(a)

    def mem(self, a):
        """igMemory: u32 size|flags, pad, u64 pointer -> (file offset, size)."""
        size = self.u32(a) & 0x00FFFFFF
        p = self.ptr(a + 8)
        return p, size

    def mem_handle(self, a):
        """igMemoryRef handle field: index into the TMHN thumbnails."""
        size, raw = self.thumbnails[self.u64(a)]
        return self.addr(raw), size & 0x00FFFFFF

    def vector(self, a):
        """igVector: u64 count, u64 size|flags, u64 pointer -> (count, ptr)."""
        count = self.u64(a)
        p = self.ptr(a + 16)
        return count, p

    def data_list(self, a):
        """igDataList: count at +0x10, igMemory at +0x18 -> (count, ptr)."""
        count = self.u32(a + 0x10)
        p, _ = self.mem(a + 0x18)
        return count, p

    def object_list(self, a):
        count, p = self.data_list(a)
        return [self.ptr(p + 8 * i) for i in range(count)] if p else []

    def name(self, a):
        """igNamedObject name (string ref at +0x10)."""
        return self.string(a + 0x10)

    def obj_size(self, a):
        t = self.objects.get(a)
        return self.metasizes[self.metatypes.index(t)] if t else 0

    def handle_name(self, a):
        """(namespace, name) of the named handle field at a, or None."""
        if a not in self.handle_fields:
            return None
        v = self.u64(a)
        if not v & 0x80000000:
            return None
        i = v & 0x7FFFFFFF
        named = [e for e, h in zip(self.externals, self.external_is_handle) if h]
        return named[i] if i < len(named) else None

    def reachable(self, a, types=None):
        """Objects reachable from a through pointers (a itself included)."""
        seen, out = set(), []
        stack = [(a, self.obj_size(a))]
        while stack:
            base, size = stack.pop()
            if base in seen:
                continue
            seen.add(base)
            if base in self.objects and (types is None or self.objects[base] in types):
                out.append(base)
            for o in range(base, base + size, 4):
                if o in self.ptr_fields:
                    t = self.ptr(o)
                    if t is None or t in seen:
                        continue
                    if t in self.objects:
                        stack.append((t, self.obj_size(t)))
                    else:
                        stack.append((t, self.u32(o - 8) & 0x00FFFFFF))
        return out

    def walk_handles(self, a, seen=None):
        """Named handles reachable from object a, depth first in field order."""
        if seen is None:
            seen = set()
        out = []
        stack = [(a, self.obj_size(a))]
        while stack:
            base, size = stack.pop()
            if base in seen:
                continue
            seen.add(base)
            found = []
            for o in range(base, base + size, 4):
                h = self.handle_name(o)
                if h:
                    out.append(h)
                elif o in self.ptr_fields:
                    t = self.ptr(o)
                    if t is None or t in seen:
                        continue
                    if t in self.objects:
                        found.append((t, self.obj_size(t)))
                    else:  # igMemory: size|flags 8 bytes before the pointer
                        found.append((t, self.u32(o - 8) & 0x00FFFFFF))
            stack.extend(reversed(found))
        return out

    def named_objects(self):
        """Root object list paired with the ONAM name list: {name: address}."""
        if self.root is None:
            return {}
        objs = self.object_list(self.addr(self.root))
        lists = [a for a, t in self.objects.items() if t == "igNameList"]
        if not lists:
            return {}
        count, p = self.data_list(lists[0])
        names = [self.string(p + 16 * i) for i in range(count)]
        return {n: o for n, o in zip(names, objs) if n and o}

    def of_type(self, t):
        return [a for a, ty in self.objects.items() if ty == t]


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 2
    z = Igz(argv[2])
    if argv[1] == "dump":
        print(f"platform {z.platform} sections {[(hex(a), hex(b)) for a, b in z.sections]}")
        print("deps", z.deps)
        print("externals", z.externals)
        for a in sorted(z.objects):
            t = z.objects[a]
            extra = ""
            nm = z.string(a + 0x10)
            if nm:
                extra = f" '{nm}'"
            print(f"{a:#08x} {t}{extra}")
    elif argv[1] == "hex":
        a = int(argv[3], 0)
        n = int(argv[4], 0) if len(argv) > 4 else 0x80
        for o in range(a, a + n, 16):
            row = z.d[o:o + 16]
            notes = []
            for k in range(0, 16, 8):
                if (o + k - z.pools[0]) >= 0 and (o + k) in {z.addr(x) for x in z.offsets}:
                    notes.append(f"+{k:x}->ptr")
            print(f"{o:08x}: {row.hex(' ', 4)}  {' '.join(notes)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
