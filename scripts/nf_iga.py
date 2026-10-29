#!/usr/bin/env python3
"""List / extract Alchemy engine IGA archives (.pak) from Crash Team Racing
Nitro-Fueled (IGA version 0x0B/0x0D, little endian).

Layout follows igLibrary's igArchive (Apache-2.0, NefariousTechSupport):
header 0x38 bytes, u32 hashes[n], file infos[n] {u64 ordinal<<40|offset,
u32 length, u32 blockIndex}, large(u32)/medium(u16)/small(u8) block tables,
name table at nameTableOffset. Files are split into 0x8000 byte blocks; a
block's sector index has its top bit set when compressed (u16 size, u32
from version 0x0D, then
LZMA props + data, or zlib/LZ4 data).

    nf_iga.py list <archive.pak> [filter]
    nf_iga.py extract <archive.pak> <outdir> [filter]
"""
import lzma
import os
import struct
import sys
import zlib

BLOCK = 0x8000


def fnv1a(data):
    h = 0x811C9DC5
    for c in data:
        h = ((h ^ c) * 0x01000193) & 0xFFFFFFFF
    return h


class Iga:
    def __init__(self, path):
        self.path = path
        self.f = open(path, "rb")
        h = self.f.read(0x38)
        magic, ver = struct.unpack_from("<II", h, 0)
        if magic != 0x1A414749:
            raise ValueError(f"{path}: not an IGA archive")
        self.version = ver
        if ver not in (0x0B, 0x0D):
            raise ValueError(f"{path}: IGA version {ver:#x} unsupported")
        (self.toc_size, self.n, self.sector, _div, _slop, n_large, n_med,
         n_small, name_off, _name_size, self.flags) = struct.unpack_from("<8IQII", h, 8)
        self.f.seek(0x38 + self.n * 4)
        self.files = []
        for _ in range(self.n):
            a, length, block = struct.unpack("<QII", self.f.read(16))
            # (offset: the 40 bits under the ordinal; archives past 4 GB, the update's, need them all)
            self.files.append({"offset": a & 0xFFFFFFFFFF, "ordinal": a >> 40,
                               "length": length, "block": block})
        self.large = struct.unpack(f"<{n_large}I", self.f.read(4 * n_large))
        self.medium = struct.unpack(f"<{n_med}H", self.f.read(2 * n_med))
        self.small = self.f.read(n_small)
        self.f.seek(0x38)
        hashes = struct.unpack(f"<{self.n}I", self.f.read(4 * self.n))
        for fi, h in zip(self.files, hashes):
            fi["hash"], fi["name"], fi["logical"] = h, f"unnamed/{h:08x}.bin", ""
        # Version 0x0D archives have no usable per-file offset list in front
        # of the name table, so match each "name\0logical\0" string pair to a
        # file by hashing the logical name (FNV-1a, lower case when flags & 1).
        self.f.seek(name_off)
        table = self.f.read(_name_size)
        by_hash = {h: i for i, h in enumerate(hashes)}
        strings = table.split(b"\0")
        for prev, cur in zip(strings, strings[1:]):
            key = cur.lower() if self.flags & 1 else cur
            i = by_hash.get(fnv1a(key))
            if i is not None and cur:
                fi = self.files[i]
                fi["name"] = prev.decode("latin-1")
                fi["logical"] = cur.decode("latin-1")

    def _blocks(self, fi):
        nblocks = (fi["length"] + BLOCK - 1) // BLOCK
        base = fi["block"] & 0x0FFFFFFF
        if 0x7F * self.sector < fi["length"]:
            if 0x7FFF * self.sector < fi["length"]:
                table, top = self.large, 0x80000000
            else:
                table, top = self.medium, 0x8000
        else:
            table, top = self.small, 0x80
        for j in range(nblocks):
            v = table[base + j]
            yield bool(v & top), v & (top - 1)

    def read(self, fi):
        if fi["block"] == 0xFFFFFFFF:
            self.f.seek(fi["offset"])
            return self.f.read(fi["length"])
        ctype = fi["block"] >> 28
        out = bytearray()
        for j, (compressed, sector) in enumerate(self._blocks(fi)):
            want = min(BLOCK, fi["length"] - j * BLOCK)
            self.f.seek(fi["offset"] + sector * self.sector)
            if not compressed:
                out += self.f.read(want)
                continue
            if self.version >= 0x0D:
                (csize,) = struct.unpack("<I", self.f.read(4))
            else:
                (csize,) = struct.unpack("<H", self.f.read(2))
            if ctype == 2:  # LZMA: 5 props bytes, then raw stream
                props = self.f.read(5)
                d = props[0]
                lc, lp, pb = d % 9, (d // 9) % 5, d // 45
                (dict_size,) = struct.unpack_from("<I", props, 1)
                dec = lzma.LZMADecompressor(lzma.FORMAT_RAW, filters=[{
                    "id": lzma.FILTER_LZMA1, "dict_size": dict_size,
                    "lc": lc, "lp": lp, "pb": pb}])
                data = dec.decompress(self.f.read(csize), max_length=want)
            elif ctype == 1:
                data = zlib.decompressobj(-15).decompress(self.f.read(csize), want)
            elif ctype == 3:
                import lz4.block
                data = lz4.block.decompress(self.f.read(csize), uncompressed_size=want)
            else:
                raise ValueError(f"compression type {ctype} unsupported")
            if len(data) != want:
                raise ValueError(f"{fi['name']}: block {j} gave {len(data)} of {want} bytes")
            out += data
        return bytes(out)


def clean_name(name):
    if len(name) > 1 and name[1] == ":":
        name = name[2:]
    return name.replace("\\", "/").lstrip("/")


def main(argv):
    if len(argv) < 3 or argv[1] not in ("list", "extract"):
        print(__doc__)
        return 2
    iga = Iga(argv[2])
    if argv[1] == "list":
        filt = argv[3].lower() if len(argv) > 3 else ""
        for fi in iga.files:
            if filt in fi["name"].lower():
                print(f"{fi['length']:>10} {fi['block'] >> 28:x} {fi['logical'] or fi['name']}")
        return 0
    outdir = argv[3]
    filt = argv[4].lower() if len(argv) > 4 else ""
    done = 0
    for fi in iga.files:
        if filt not in fi["name"].lower():
            continue
        dst = os.path.join(outdir, clean_name(fi["logical"] or fi["name"]))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, "wb") as o:
            o.write(iga.read(fi))
        done += 1
    print(f"extracted {done} files to {outdir}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
