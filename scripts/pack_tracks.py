"""Append every extracted custom track to a built BIGFILE.BIG.

Layout after packing (see mod/src/multitrack.h):
  [stock entries 0..607]
  [track k: 8 entries, {1P,2P,4P,relic} x {vrm,lev}]  for k in tracks
Identical blobs (e.g. one lev reused for every LOD) are stored once and shared.
With MT_NF=1 (native build) and the user's Nitro-Fueled package unpacked, "NF"
tracks are added too (scripts/nf_tracks.py): a stock level's own entries again
(Nitro-Fueled only tracks: their fan remake's), flagged for the ray tracer's
Nitro-Fueled remaster mesh.
The track table (MTHeader + MTEntry[]) is compiled into overlay 230 instead,
so the menu never touches the CD.

Usage: python -I scripts/pack_tracks.py --table-only mod/src/track_table.bin   (before compiling)
       python -I scripts/pack_tracks.py path/to/BIGFILE.BIG                    (toolchain hook)
"""
import hashlib
import json
import os
import pathlib
import re
import struct
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
TRACK_DIR = ROOT / "tracks"
SKIP_FILE = ROOT / "skip_tracks.txt"  # one track id per line, for tracks that crash

SECTOR = 2048
MAX_ENTRIES = (0x4000 - 8) // 8  # LOAD_ReadDirectory reads 8 header sectors
MT_MAGIC = 0x4B52544D
STOCK_ENTRIES = 608
TABLE_ROOM = 0x800ba548 - 0x800b6834  # MM_EndOfFile .. RB_EndOfFile (largest 230-233 overlay)
NAME_LEN, AUTHOR_LEN = 28, 16
FLAG_BATTLE = 1
FLAG_NO_AI = 2  # no nav paths in the lev, so no arcade bots
FLAG_REMASTER = 4  # Nitro-Fueled track: stock level, drawn by the ray tracer from assets/rtx_remaster
# glyphs in data.font_characterIconID; @ [ ^ * draw button icons, ~ is a colour code
FONT_OK = re.compile(r"[^A-Z0-9 !%'+,\-./:<=>?]")
WITH_NF = os.environ.get("MT_NF") == "1"  # build_native.sh: Nitro-Fueled tracks


def font_text(s, n):
    s = s.upper().replace("’", "'").replace("–", "-").replace("&", "+")
    s = re.sub(r"\s*[(\[]\s*", " - ", s).replace(")", " ").replace("]", " ")
    s = FONT_OK.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[: n - 1].encode("ascii")


def read_big(path):
    data = path.read_bytes()
    count = struct.unpack_from("<I", data, 4)[0]
    out = []
    for i in range(count):
        sector, size = struct.unpack_from("<II", data, 8 + i * 8)
        out.append(data[sector * SECTOR: sector * SECTOR + size])
    return out


def write_big(path, blobs):
    """blobs: list of bytes; identical blobs share storage."""
    n = len(blobs)
    if n > MAX_ENTRIES:
        raise SystemExit(f"{n} bigfile entries > {MAX_ENTRIES}; drop some tracks")
    head_sectors = (8 + n * 8 + SECTOR - 1) // SECTOR
    table, body, where = [], bytearray(), {}
    for b in blobs:
        key = hashlib.sha1(b).digest()
        if key not in where:
            where[key] = head_sectors + len(body) // SECTOR
            body += b + b"\0" * (-len(b) % SECTOR)
        table.append((where[key] if b else 0, len(b)))
    head = bytearray(struct.pack("<II", 0, n))
    for sector, size in table:
        head += struct.pack("<II", sector, size)
    head += b"\0" * (head_sectors * SECTOR - len(head))
    path.write_bytes(bytes(head) + bytes(body))
    return len(head) + len(body)


def track_list():
    skip = set(SKIP_FILE.read_text().split()) if SKIP_FILE.exists() else set()
    metas = []
    for d in sorted(TRACK_DIR.iterdir()):
        m = d / "meta.json"
        if m.exists() and d.name not in skip:
            metas.append(json.loads(m.read_text()))
    if WITH_NF:
        sys.path.insert(0, str(ROOT / "scripts"))
        import extract_tracks
        import nf_tracks
        for tid, _, _ in nf_tracks.available():
            n = nf_tracks.native(tid)
            if n:
                # its own race data over a stock host slot (no PS1 level of its
                # own): the host's blobs; the author field carries the renderer key
                metas.append({"id": nf_tracks.key(tid), "name": "NF " + n[2], "author": nf_tracks.key(tid), "slot": n[3],
                              "battle": False, "has_nav": True, "nf": True})
                continue
            h = nf_tracks.hosted(tid)
            if h is None:
                metas.append({"id": nf_tracks.key(tid), "name": "NF " + extract_tracks.SLOT_NAMES[tid], "author": "Nitro-Fueled",
                              "slot": tid, "battle": tid >= nf_tracks.FIRST_BATTLE_SLOT, "has_nav": True, "nf": True})
                continue
            # on a remake (its lev played): the author field carries the renderer key
            host = json.loads((TRACK_DIR / h[3] / "meta.json").read_text())
            metas.append({"id": nf_tracks.key(tid), "name": "NF " + h[2], "author": nf_tracks.key(tid), "slot": host["slot"],
                          "battle": host["battle"], "has_nav": host["has_nav"], "nf": True, "host": h[3]})
    metas.sort(key=lambda m: m["name"].upper())
    return metas


def own_entries(m):
    """whether a track has bigfile entries of its own: not a Nitro-Fueled track
    over a stock slot (those play that slot's own 8 entries; the bigfile holds
    at most 2047)"""
    return not (m.get("nf") and not m.get("host"))


def big_bases(metas):
    """each track's first bigfile entry: its stock slot's (slot x 8) or its own,
    appended after the stock ones in table order"""
    out, k = [], 0
    for m in metas:
        if own_entries(m):
            out.append(STOCK_ENTRIES + 8 * k)
            k += 1
        else:
            out.append(m["slot"] * 8)
    return out


def table_bytes(metas):
    """MTHeader + MTEntry[]; bigBase from big_bases."""
    entries = []
    for m, base in zip(metas, big_bases(metas)):
        entries.append(struct.pack(
            f"<BBH{NAME_LEN}s{AUTHOR_LEN}s", m["slot"],
            (FLAG_BATTLE if m["battle"] else 0) | (0 if m["has_nav"] else FLAG_NO_AI) | (FLAG_REMASTER if m.get("nf") else 0),
            base,
            font_text(m["name"], NAME_LEN), font_text(m["author"] or "?", AUTHOR_LEN)))
    table = struct.pack("<II", MT_MAGIC, len(entries)) + b"".join(entries)
    if len(table) > TABLE_ROOM:
        raise SystemExit(f"track table {len(table)} B > {TABLE_ROOM} B free after overlay 230; drop tracks")
    return table


def main():
    if sys.argv[1] == "--table-only":
        out = pathlib.Path(sys.argv[2])
        table = table_bytes(track_list())
        out.write_bytes(table)
        print(f"[pack_tracks] track table: {len(table)} / {TABLE_ROOM} B -> {out}")
        return

    big_path = pathlib.Path(sys.argv[1])
    blobs = read_big(big_path)
    if len(blobs) != STOCK_ENTRIES:
        raise SystemExit(f"BIGFILE.BIG has {len(blobs)} entries, expected stock {STOCK_ENTRIES}")
    metas = track_list()
    for m in metas:
        if not own_entries(m):
            continue  # (its stock slot's own entries: big_bases)
        # (an NF track on a remake: the remake's, deduplicated against its own entry)
        d = TRACK_DIR / m.get("host", m["id"])
        vrm, lev = (d / "track.vrm").read_bytes(), (d / "track.lev").read_bytes()
        # the custom engines these tracks were built for are 1P only, so every
        # LOD ({1P,2P,4P,relic}) gets the one lev/vrm pair; dedup stores it once
        for lod in range(4):
            blobs += [vrm, lev]

    # must agree with the table compiled into overlay 230 (--table-only, run before compiling)
    table = (ROOT / "psx-modding-toolchain/games/CTR/mods/MultiTrack/src/track_table.bin").read_bytes()
    if table != table_bytes(metas):
        raise SystemExit("mod/src/track_table.bin is stale; rerun scripts/build_rom.sh")

    size = write_big(big_path, blobs)
    print(f"[pack_tracks] {len(metas)} tracks ({sum(map(own_entries, metas))} with entries of their own) after {STOCK_ENTRIES} stock entries; "
          f"{len(blobs)} entries, BIGFILE.BIG = {size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
