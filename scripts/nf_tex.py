#!/usr/bin/env python3
"""Crash Team Racing Nitro-Fueled (PS4) igImage2 textures -> linear BC blocks.

PS4 textures are stored per mip level in 8x8 element micro tiles (GNM
1D thin: elements Morton ordered inside each tile), each level padded to
whole tiles; an element is a 4x4 block for BC formats, a pixel otherwise.
The format is an external reference to a metaimage named by FNV-1a hash.

    nf_tex.py png <texture.igz> <out.png>     (preview, mip 0)
"""
import struct
import sys
import os

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nf_igz  # noqa: E402


def fnv1a(s):
    h = 0x811C9DC5
    for c in s.encode():
        h = ((h ^ c) * 0x01000193) & 0xFFFFFFFF
    return h


# metaimage name -> (block bytes, block size in pixels, Vulkan-ish tag)
FORMATS = {
    "dxt1_tile_ps4": (8, 4, "bc1"),
    "dxt1_srgb_tile_ps4": (8, 4, "bc1"),
    "dxt5_tile_ps4": (16, 4, "bc3"),
    "dxt5_srgb_tile_ps4": (16, 4, "bc3"),
    "bc5_tile_ps4": (16, 4, "bc5"),
    "b8g8r8a8_tile_ps4": (4, 1, "bgra8"),
    "r8g8b8a8_tile_ps4": (4, 1, "rgba8"),
}
FORMAT_BY_HASH = {fnv1a(n): n for n in FORMATS}


def _morton_tile(bpe):
    """Byte gather index for one 8x8 tile: linear (y, x) <- tiled order."""
    order = np.zeros((8, 8), np.int64)
    for i in range(64):
        x = y = 0
        for b in range(3):
            x |= ((i >> (2 * b)) & 1) << b
            y |= ((i >> (2 * b + 1)) & 1) << b
        order[y, x] = i
    return order


_ORDER = _morton_tile(0)


def untile(data, w, h, bpe):
    """GNM 1D thin micro tiles -> linear rows; w/h in elements (padded to 8)."""
    tw, th = (w + 7) // 8, (h + 7) // 8
    tiles = np.frombuffer(data, np.uint8, tw * th * 64 * bpe).reshape(th, tw, 64, bpe)
    tiles = tiles[:, :, _ORDER.reshape(-1), :].reshape(th, tw, 8, 8, bpe)
    lin = tiles.transpose(0, 2, 1, 3, 4).reshape(th * 8, tw * 8, bpe)
    return lin[:h, :w]


class Texture:
    def __init__(self, path):
        z = nf_igz.Igz(path)
        img = next(a for a, t in z.objects.items() if t == "igImage2")
        self.width = z.u16(img + 0x18)
        self.height = z.u16(img + 0x1A)
        self.levels = z.u16(img + 0x1E)
        ext = z.u32(img + 0x28) & 0x7FFFFFFF
        self.format = FORMAT_BY_HASH.get(z.ext_ids[ext][0]) if ext < len(z.ext_ids) else None
        if self.format is None:
            raise ValueError(f"{path}: unknown image format")
        self.bpe, self.block, self.kind = FORMATS[self.format]
        ptr, size = z.mem(img + 0x38)
        data = z.d[ptr:ptr + size]
        self.mips = []  # linear element rows per level (bytes)
        pos = 0
        w, h = self.width, self.height
        for _ in range(self.levels):
            ew = max(1, (w + self.block - 1) // self.block)
            eh = max(1, (h + self.block - 1) // self.block)
            pw, ph = (ew + 7) // 8 * 8, (eh + 7) // 8 * 8
            n = pw * ph * self.bpe
            if pos + n > len(data):
                break
            self.mips.append((w, h, untile(data[pos:pos + n], ew, eh, self.bpe).tobytes()))
            pos += n
            w, h = max(1, w // 2), max(1, h // 2)

    def rgba(self, level=0):
        """Decoded RGBA8 of one mip (numpy HxWx4) for previews and fitting."""
        w, h, blocks = self.mips[level]
        if self.kind in ("bgra8", "rgba8"):
            a = np.frombuffer(blocks, np.uint8).reshape(h, w, 4)
            return a[:, :, [2, 1, 0, 3]] if self.kind == "bgra8" else a.copy()
        return decode_bc(blocks, w, h, self.kind)


def decode_bc(blocks, w, h, kind):
    from PIL import Image
    import io
    fourcc = {"bc1": b"DXT1", "bc3": b"DXT5", "bc5": b"ATI2"}[kind]
    hdr = struct.pack("<4sIIIIIII44xII4sIIIIIIIIII", b"DDS ", 124, 0x1007, h, w,
                      len(blocks), 0, 1, 32, 4, fourcc, 0, 0, 0, 0, 0, 0x1000, 0, 0, 0, 0)
    im = Image.open(io.BytesIO(hdr + blocks))
    return np.asarray(im.convert("RGBA"))


def main(argv):
    if len(argv) < 4 or argv[1] != "png":
        print(__doc__)
        return 2
    from PIL import Image
    t = Texture(argv[2])
    print(t.format, t.width, t.height, t.levels, len(t.mips))
    Image.fromarray(t.rgba()).save(argv[3])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
