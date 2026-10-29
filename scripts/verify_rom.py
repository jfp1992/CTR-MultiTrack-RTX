"""Static checks on a built out/CTR-MultiTrack.bin (no emulator needed).

* SYSTEM.CNF stack and exe header s_addr moved to 0x807FFFF0
* exe: MEMPACK_Init gets 0x700000, LOAD_GetBigfileIndex replaced by mod/output
* overlay 230: MM_TrackSelect_MenuProc replaced by mod/output
* BIGFILE.BIG: stock entries untouched, header fits the 0x4000-byte read,
  track table at the end of overlay 230 decodes and fits below MEMPACK
Usage: python -I scripts/verify_rom.py [path/to.bin]
"""
import json
import pathlib
import struct
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from extract_tracks import Disc, USA_BIN  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
MOD = ROOT / "psx-modding-toolchain/games/CTR/mods/MultiTrack"
SYMS = ROOT / "psx-modding-toolchain/games/CTR/symbols/gcc-syms926.txt"
EXE_BASE, EXE_HDR = 0x80010000, 0x800
OVL230_BASE = 0x800ab9f0
MT_MAGIC = 0x4B52544D
FLAG_REMASTER = 4  # pack_tracks.py: Nitro-Fueled track
ENTRY = struct.Struct("<BBH28s16s")

fails = 0


def check(ok, what):
    global fails
    fails += not ok
    print(("ok   " if ok else "FAIL ") + what)


def sym(name):
    for line in SYMS.read_text().splitlines():
        if line.split("=")[0].strip() == name:
            return int(line.split("=")[1].strip().rstrip(";"), 16)
    raise KeyError(name)


def main():
    path = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "out/CTR-MultiTrack.bin"
    d, s = Disc(path), Disc(USA_BIN)

    cnf = d.file("SYSTEM.CNF")
    check(b"STACK = 807FFFF0" in cnf, "SYSTEM.CNF stack at 0x807FFFF0")

    exe = d.file("SCUS_944.26")
    check(struct.unpack_from("<I", exe, 0x30)[0] == 0x807FFFF0, "exe header s_addr = 0x807FFFF0")
    check(struct.unpack_from("<I", exe, 0x8003c638 - EXE_BASE + EXE_HDR)[0] == 0x3C040070,
          "MEMPACK_Init(0x700000) in main")
    for addr, word, what in [(0x8003b250, 0x00068340, "1P x8"), (0x8003b27c, 0x00058300, "2P x4"),
                             (0x8003b2a4, 0x00108300, "4P x4")]:
        check(struct.unpack_from("<I", exe, addr - EXE_BASE + EXE_HDR)[0] == word, f"bigger PrimMem {what}")
    hook = (MOD / "output/bigfile_indexc.bin").read_bytes()
    off = sym("LOAD_GetBigfileIndex") - EXE_BASE + EXE_HDR
    check(exe[off:off + len(hook)] == hook, f"LOAD_GetBigfileIndex hook in exe ({len(hook)} B)")

    big, stock = d.bigfile(), s.bigfile()
    menu = (MOD / "output/track_selectc.bin").read_bytes()
    off = sym("MM_TrackSelect_MenuProc") - OVL230_BASE
    check(big[230][off:off + len(menu)] == menu, f"track select hook in overlay 230 ({len(menu)} B)")

    lba, _ = d.find("BIGFILE.BIG")
    count = struct.unpack_from("<I", d.read(lba, 8), 4)[0]
    check(8 + count * 8 <= 0x4000, f"bigfile header {count} entries fits LOAD_ReadDirectory's 8 sectors")
    baked = {}  # stock levels with ray-traced lighting (rt_bake.py / pack_tracks.py)
    for e in sorted((ROOT / "stock_tracks").glob("*/entry.json")):
        lev = e.parent / "track.baked.lev"
        if lev.exists():
            baked[json.loads(e.read_text())["entry"]] = lev.read_bytes()
    untouched = [i for i in range(len(stock)) if i not in (226, 230) and big[i] != stock[i]
                 and big[i] != baked.get(i)]
    check(not untouched, f"stock bigfile entries unchanged except 226/230 and baked levels (diff: {untouched[:10]})")
    n_baked = sum(big[i] == lev for i, lev in baked.items())
    print(f"     {n_baked} stock levels carry baked lighting")

    toff = sym("MM_EndOfFile") - OVL230_BASE
    magic, n = struct.unpack_from("<II", big[230], toff)
    table = big[230][toff:toff + 8 + n * ENTRY.size]
    check(magic == MT_MAGIC and len(table) == 8 + n * ENTRY.size, f"track table in overlay 230: {n} tracks")
    check(OVL230_BASE + len(big[230]) <= sym("RB_EndOfFile"), "overlay 230 + table ends before MEMPACK")
    bases = [ENTRY.unpack_from(table, 8 + i * ENTRY.size)[2] for i in range(n)]
    own = sorted(b for b in bases if b >= 608)
    check(len(big) == 608 + 8 * len(own) and own == list(range(608, 608 + 8 * len(own), 8)),
          f"bigfile = 608 stock + 8 x {len(own)} track entries (the other {n - len(own)} tracks: their stock slot's)")
    metas = {json.loads(p.read_text())["name"]: p.parent for p in (ROOT / "tracks").glob("*/meta.json")}
    bad = []
    n_nf = 0
    for i in range(n):
        slot, flags, base, name, author = ENTRY.unpack_from(table, 8 + i * ENTRY.size)
        name = name.rstrip(b"\0").decode()
        vrm, lev = big[base], big[base + 1]
        if not (lev[:4] and struct.unpack_from("<I", lev, 0)[0] < len(lev) and slot <= 24):
            bad.append(name)
        if flags & FLAG_REMASTER and base < 608:
            # Nitro-Fueled track over a stock slot (pack_tracks.py): that slot's own 8 entries
            if base != slot * 8:
                bad.append(f"{name} (not its stock slot's entries)")
            n_nf += 1
            continue
        n_nf += (flags & FLAG_REMASTER) != 0  # (one on a fan remake, key in author: the remake's entries, as a custom track's)
        for lod in range(1, 4):
            if big[base + lod * 2] != vrm or big[base + lod * 2 + 1] != lev:
                bad.append(f"{name} lod{lod}")
    check(not bad, f"every track's 8 entries consistent ({bad[:5]})")
    check(n - n_nf == len(metas), f"table has all {len(metas)} extracted tracks (skip_tracks.txt aside), {n_nf} NF tracks")

    print("ALL OK" if not fails else f"{fails} check(s) failed")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
