"""Fail if a function-replacement in buildList.txt is bigger than the function it
overwrites, or if the hooked function is not the first code of its file.

The toolchain writes the compiled .bin over the original function in place and
does not check the size, so an oversized hook silently clobbers its neighbour.
Usage: python scripts/check_sizes.py MOD_DIR
"""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SYMS = ROOT / "psx-modding-toolchain/games/CTR/symbols/gcc-syms926.txt"
# overlays 230-233 share one address range; only compare within the same overlay
OVERLAY_PREFIX = {"230": ("MM_", "D230"), "231": ("RB_", "D231"), "232": ("AH_", "D232"), "233": ("CS_", "D233")}


def first_text_symbol(mod, src):
    """First .text.<fn> the linker placed for src (from debug/mod.map), as (name, addr)."""
    lines = (mod / "debug/mod.map").read_text().splitlines()
    sect = "." + src.stem + src.suffix.lstrip(".")
    for i, line in enumerate(lines):
        if line.split()[:1] == [sect]:
            for nxt in lines[i + 1:]:
                m = re.match(r"\s*\.text\.(\S+)", nxt)
                if m:
                    tail = nxt.split()[1:] or lines[lines.index(nxt) + 1].split()
                    return m.group(1), int(tail[0], 16)
                if nxt and not nxt[0].isspace():
                    break
    return None


def main():
    mod = pathlib.Path(sys.argv[1])
    addr = {}
    for line in SYMS.read_text().splitlines():
        if "=" in line:
            name, val = line.split("=")
            addr[name.strip()] = int(val.strip().rstrip(";"), 16)
    bad = 0
    for line in (mod / "buildList.txt").read_text().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if line.lstrip().startswith("//") or len(parts) < 5 or parts[2] not in addr or parts[3] != "0x0":
            continue
        start = addr[parts[2]]
        prefix = OVERLAY_PREFIX.get(parts[1])
        if parts[2] == "MM_EndOfFile":
            nxt = addr["RB_EndOfFile"]  # free tail of the 230-233 overlay region
        else:
            nxt = min(a for n, a in addr.items() if a > start and (prefix is None or n.startswith(prefix)))
        src = pathlib.Path(parts[4])
        out = mod / "output" / (src.stem + src.suffix.lstrip(".") + ".bin")
        if not out.exists():
            out = mod / src  # raw .bin data is used as-is
        if not out.exists():
            continue
        if src.suffix == ".c":
            # code is laid out in the order gcc emits it; the hooked function must be first
            first = first_text_symbol(mod, src)
            if first and first != (parts[2], start):
                print(f"WRONG ENTRY {parts[2]}: {first[0]} sits at {first[1]:#x}, callers jump there")
                bad += 1
        size, room = out.stat().st_size, nxt - start
        status = "OK " if size <= room else "TOO BIG"
        bad += size > room
        print(f"{status} {parts[2]}: {size}/{room} bytes")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
