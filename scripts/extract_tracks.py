"""Pull custom tracks' level files out of the patches dropped into tracks_in/.

Drop each track's download (.xdelta/.vcdiff, or a .zip/.rar/.7z holding one)
into tracks_in/, loose or in sub-folders. Files are matched to the site's pages
in tracks.json by md5, then published file name, then folder name; a file
nobody knows becomes a track named after the file. Archives are unpacked with
7z into raw/unpacked/<md5>/ and one .xdelta inside is used: variants with AI
bots win (their lev carries the nav data arcade mode needs), time-trial /
ghost / reduced variants are the fallback.

Phase 1 (raw, cached per unique patch file in raw/<patch-md5>/):
  apply the patch to the USA bin, read BIGFILE.BIG via ISO9660, diff every entry
  against stock and keep each changed level slot's LOD files plus the
  PS1_TrackROM entries 221 (lev) / 222 (vrm).

Phase 2 (classify, writes tracks/<id>/{track.vrm,track.lev,meta.json}):
  Custom-track ROMs run a 1P-only engine, so only one LOD per slot is a real
  track. The other LOD entries are either leftovers from a shared base ROM
  (the same lev shows up in several unrelated patches; dropped) or, in
  collections (one patch linked from several track pages), further tracks in
  page order (prev/next links on the site), one per LOD entry.
Only tracks from the patches present are (re)written; other tracks/ entries
are kept (--prune removes them). bake.json overrides survive a rewrite.

Run isolated (inputs are untrusted):  python -I scripts/extract_tracks.py [--force] [--prune]
"""
import collections
import difflib
import hashlib
import json
import pathlib
import re
import shutil
import struct
import subprocess
import sys
import tempfile

import pyxdelta

ROOT = pathlib.Path(__file__).resolve().parent.parent
USA_BIN = ROOT / "rom-usa" / "ctr-usa.bin"
XDELTA3 = ROOT / "tools" / "xdelta3"  # built by scripts/setup.sh (LZMA), else xdelta3 on PATH
IN_DIR = ROOT / "tracks_in"
RAW_DIR = ROOT / "raw"
OUT_DIR = ROOT / "tracks"
TRACKS_JSON = ROOT / "tracks.json"
NAMES_JSON = ROOT / "track_names.json"  # optional {"<id>": "Display Name"} overrides
REPORT = ROOT / "extract_report.md"

RAW_SECTOR = 2352
DATA_OFFSET = 24  # MODE2 form1 user data starts after sync+header+subheader
SECTOR = 2048

NUM_SLOTS = 25          # DINGO_CANYON .. LAB_BASEMENT, 8 entries each
FIRST_BATTLE_SLOT = 18  # NITRO_COURT
LEV_ENTRY, VRM_ENTRY = 221, 222
TRACKROM_SLOT = 0       # PS1_TrackROM CUSTOM_LEVEL_ID
SLOT_NAMES = [
    "Dingo Canyon", "Dragon Mines", "Blizzard Bluff", "Crash Cove", "Tiger Temple",
    "Papu's Pyramid", "Roo's Tubes", "Hot Air Skyway", "Sewer Speedway", "Mystery Caves",
    "Cortex Castle", "N. Gin Labs", "Polar Pass", "Oxide Station", "Coco Park",
    "Tiny Arena", "Slide Coliseum", "Turbo Track", "Nitro Court", "Rampage Ruins",
    "Parking Lot", "Skull Rock", "The North Bowl", "Rocky Road", "Lab Basement",
]
# pages that announce a pack rather than a single track
LEFTOVER_PATCHES = 3
# levs seen in 3+ unrelated patches of the full ctrcustomtracks.com set, for when
# only a few patches are dropped in
KNOWN_LEFTOVERS = {
    "0d8b78c424bc63bc6edd93d34b364439", "47c74da07befac5792b52a42b2f031eb", "5b01eabe8da3098327b1ecff7d9786eb",
    "6668d23f61580f66816ebf3a4265d974", "d9f685c211ada441ae10b7f5de943794",
}
PATCHES = (".xdelta", ".vcdiff")
ARCHIVES = (".zip", ".rar", ".7z")
PACK_PAGE = re.compile(r"\b(cup|collection|pack)\b", re.I)


class Disc:
    def __init__(self, path):
        self.f = open(path, "rb")

    def close(self):
        self.f.close()

    def read(self, lba, nbytes):
        out = bytearray()
        while len(out) < nbytes:
            self.f.seek(lba * RAW_SECTOR + DATA_OFFSET)
            out += self.f.read(SECTOR)
            lba += 1
        return bytes(out[:nbytes])

    def find(self, name):
        """Locate a root-directory file; returns (lba, size)."""
        pvd = self.read(16, SECTOR)
        root = pvd[156:156 + 34]
        lba, size = struct.unpack_from("<I", root, 2)[0], struct.unpack_from("<I", root, 10)[0]
        data = self.read(lba, size)
        i = 0
        while i < len(data):
            ln = data[i]
            if ln == 0:  # records never straddle sectors; skip padding
                i = (i // SECTOR + 1) * SECTOR
                continue
            rec = data[i:i + ln]
            ident = rec[33:33 + rec[32]].decode("ascii", "replace").split(";")[0]
            if ident.upper() == name.upper():
                return struct.unpack_from("<I", rec, 2)[0], struct.unpack_from("<I", rec, 10)[0]
            i += ln
        raise FileNotFoundError(name)

    def file(self, name):
        lba, size = self.find(name)
        return self.read(lba, size)

    def bigfile(self):
        """All BIGFILE.BIG entries as a list of bytes."""
        lba, _ = self.find("BIGFILE.BIG")
        count = struct.unpack_from("<I", self.read(lba, 8), 4)[0]
        table = self.read(lba, 8 + count * 8)
        out = []
        for i in range(count):
            sector, size = struct.unpack_from("<II", table, 8 + i * 8)
            out.append(self.read(lba + sector, size) if size else b"")
        return out


def lev_len(lev):
    """Real length of [u32 mapOffset][Level ...][u32 mapBytes][ptr map], or 0 if malformed.

    Slot-replacement patches keep the stock file size, so the new lev is usually
    followed by padding / leftovers of the original; callers trim to this length.
    """
    if len(lev) < 0x200:
        return 0
    map_off = struct.unpack_from("<I", lev, 0)[0]
    if 4 + map_off + 4 > len(lev):
        return 0
    map_bytes = struct.unpack_from("<I", lev, 4 + map_off)[0]
    end = 4 + map_off + 4 + map_bytes
    return end if end <= len(lev) and map_bytes % 4 == 0 else 0


def has_nav(lev):
    body = lev[4:]
    nav_ptr = struct.unpack_from("<I", body, 0x188)[0]  # Level.LevNavTable
    return 0 < nav_ptr < len(body) - 12 and any(struct.unpack_from("<III", body, nav_ptr))


def md5(b):
    return hashlib.md5(b).hexdigest()


# ---------------------------------------------------------------- phase 1

def extract_raw(patch, patch_md5, stock, stock_exe):
    """Write raw/<patch-md5>/ with every changed level candidate; return its info dict."""
    dest = RAW_DIR / patch_md5
    info_path = dest / "info.json"
    if info_path.exists():
        return json.loads(info_path.read_text())

    with tempfile.TemporaryDirectory() as td:
        out_bin = pathlib.Path(td) / "patched.bin"
        xdelta3 = XDELTA3 if XDELTA3.exists() else shutil.which("xdelta3")
        if xdelta3:
            # full xdelta3 build: supports LZMA secondary compression and verifies the source
            r = subprocess.run([str(xdelta3), "-d", "-f", "-s", str(USA_BIN), str(patch), str(out_bin)],
                               capture_output=True, text=True)
            if r.returncode != 0:
                raise RuntimeError(f"xdelta3: {r.stderr.strip()[:200]}")
        else:
            pyxdelta.decode(str(USA_BIN), str(patch), str(out_bin))
        if not out_bin.exists() or out_bin.stat().st_size == 0:
            raise RuntimeError("xdelta decode failed (made for a different base ROM?)")
        d = Disc(out_bin)
        big = d.bigfile()
        exe = d.file("SCUS_944.26")
        d.close()

    changed = [i for i in range(len(big)) if i >= len(stock) or big[i] != stock[i]]
    cands = []  # (slot, lod, vrm, lev) in LOD order; lod -1 = PS1_TrackROM entries
    for slot in sorted({i // 8 for i in changed if i < NUM_SLOTS * 8}):
        for lod in range(4):
            vi, li = slot * 8 + lod * 2, slot * 8 + lod * 2 + 1
            if li in changed and lev_len(big[li]):
                cands.append((slot, lod, big[vi], big[li][:lev_len(big[li])]))
    if LEV_ENTRY in changed and lev_len(big[LEV_ENTRY]):
        cands.append((TRACKROM_SLOT, -1, big[VRM_ENTRY], big[LEV_ENTRY][:lev_len(big[LEV_ENTRY])]))

    level_entries = {s * 8 + l * 2 + k for s, l, _, _ in cands if l >= 0 for k in (0, 1)}
    if any(l < 0 for _, l, _, _ in cands):
        level_entries |= {LEV_ENTRY, VRM_ENTRY}

    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True)
    diff = bytes(a if a != b else 0 for a, b in zip(exe, stock_exe))
    strings = [m.group().decode() for m in re.finditer(rb"[ -~]{4,40}", diff)]
    info = {"patch": str(patch.relative_to(ROOT) if patch.is_relative_to(ROOT) else patch), "exe_changed": exe != stock_exe, "exe_strings": strings,
            "extra_entries": [i for i in changed if i not in level_entries], "candidates": []}
    for n, (slot, lod, vrm, lev) in enumerate(cands):
        (dest / f"c{n}.vrm").write_bytes(vrm)
        (dest / f"c{n}.lev").write_bytes(lev)
        info["candidates"].append({"n": n, "slot": slot, "lod": lod, "lev_md5": md5(lev),
                                   "has_nav": has_nav(lev), "size": len(lev) + len(vrm)})
    info_path.write_text(json.dumps(info, indent=1))
    return info


# ---------------------------------------------------------------- phase 2

def page_order(slugs, tracks):
    """Order a collection's pages by the site's «prev | Next: next» links."""
    by_name = {tracks[s]["name"].lower(): s for s in slugs}
    nxt = {}
    for s in slugs:
        m = re.search(r"Next:\s*(.+?)»", tracks[s].get("text", ""))
        name = (tracks[s].get("next") or (m.group(1) if m else "")).strip().lower()
        if name in by_name:
            nxt[s] = by_name[name]
    heads = [s for s in slugs if s not in nxt.values()]
    order, seen = [], set()
    for h in heads + list(slugs):
        while h and h not in seen:
            order.append(h)
            seen.add(h)
            h = nxt.get(h)
    return order


def norm(name):
    return re.sub(r"[^a-z0-9]", "", name.lower().replace("’", "'"))


def exe_track_names(info, tracks):
    """Track names a pack's code lists (its own menu), in order, matched to site pages."""
    by_norm = {norm(t["name"]): s for s, t in tracks.items()}
    out = []
    for st in info.get("exe_strings", []):
        n = norm(st)
        if len(n) < 4:
            continue
        close = difflib.get_close_matches(n, by_norm, n=1, cutoff=0.85)
        hit = by_norm.get(n) or (by_norm[close[0]] if close else None)
        if hit and hit not in [h for h, _ in out]:
            out.append((hit, st))
    return out


def pack_name(track):
    """Short pack name from the page's category list ("SMK – Star Cup" -> "SMK Star Cup")."""
    if track.get("pack"):
        return track["pack"]
    m = re.search(r"([^|,\n]*\b(?:Cup|Collection|Pack)\b[^|,\n]*)", track.get("text", "")[:600])
    return re.sub(r"\s*[–-]\s*", " ", m.group(1)).strip() if m else f"{track['name']} pack"


def classify(tracks, raws):
    """raws: slug -> raw info. Writes tracks/<id>/ and returns the track metas."""
    # A lev found in 3+ different patch files is base-ROM leftover (the same stale
    # 2P/4P/relic files ride along in many patches). Twice is usually one track
    # released alone and again inside a cup; it is kept once (see `taken`).
    seen_in = collections.defaultdict(set)
    for info in raws.values():
        for c in info["candidates"]:
            seen_in[c["lev_md5"]].add(info["patch_md5"])
    leftover = {h for h, ps in seen_in.items() if len(ps) >= LEFTOVER_PATCHES} | KNOWN_LEFTOVERS

    groups = collections.defaultdict(list)
    for slug, info in raws.items():
        groups[info["patch_md5"]].append(slug)
    page_group = {slug: info["patch_md5"] for slug, info in raws.items()}

    plans = []  # (is_pack, patch_md5, [(candidate, page_slug or None, name)])
    for patch_md5, slugs in groups.items():
        info = raws[slugs[0]]
        cands = [c for c in info["candidates"] if c["lev_md5"] not in leftover] or info["candidates"][:1]
        uniq = []
        for c in cands:
            if c["lev_md5"] not in {u["lev_md5"] for u in uniq}:
                uniq.append(c)
        # a pack: several pages share the patch, or one page's patch fills all 4 LOD entries
        is_pack = len(slugs) > 1 or len(uniq) >= 4
        if not is_pack:
            plans.append((False, patch_md5, [(uniq[0], slugs[0], tracks[slugs[0]]["name"])]))
            continue
        listed = exe_track_names(info, tracks)
        if len(listed) == len(uniq):
            entries = [(c, slug, tracks[slug]["name"]) for c, (slug, _) in zip(uniq, listed)]
        else:
            order = [s for s in page_order(slugs, tracks) if not PACK_PAGE.search(tracks[s]["name"])]
            order += [s for s in slugs if s not in order]
            pack = pack_name(tracks[order[0]])
            entries = [(c, order[i] if i < len(order) else None,
                        tracks[order[i]]["name"] if i < len(order) else f"{pack} #{i + 1}")
                       for i, c in enumerate(uniq)]
        plans.append((True, patch_md5, entries))

    names = json.loads(NAMES_JSON.read_text()) if NAMES_JSON.exists() else {}
    # tracks already extracted from patches no longer in tracks_in/
    existing = {md5((d / "track.lev").read_bytes()): d.name for d in OUT_DIR.glob("*") if (d / "track.lev").exists()}
    metas, taken, pages_done = [], set(), set()
    for is_pack, patch_md5, entries in sorted(plans, key=lambda p: p[0]):  # standalone releases first
        info = raws[groups[patch_md5][0]]
        for i, (c, page, name) in enumerate(entries):
            if c["lev_md5"] in taken:
                continue
            if page and (page in pages_done or (page in page_group and page_group[page] != patch_md5)):
                continue  # that page's own download provides this track
            tid = page or f"{groups[patch_md5][0]}#{i}"
            if existing.get(c["lev_md5"], tid) != tid:
                continue  # already a track under another id
            taken.add(c["lev_md5"])
            pages_done.add(page)
            slug = page or groups[patch_md5][0]
            dest = OUT_DIR / tid
            src = RAW_DIR / patch_md5
            same = all((dest / f"track.{x}").exists() and
                       (dest / f"track.{x}").read_bytes() == (src / f"c{c['n']}.{x}").read_bytes() for x in ("lev", "vrm"))
            if not same:  # new or changed: start clean, keeping hand-made lighting overrides
                if dest.exists():
                    for f in dest.iterdir():
                        if f.name != "bake.json":
                            f.unlink()
                dest.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src / f"c{c['n']}.vrm", dest / "track.vrm")
                shutil.copyfile(src / f"c{c['n']}.lev", dest / "track.lev")
            meta = {
                "id": tid, "slug": slug, "name": names.get(tid, name), "author": tracks[slug].get("author", ""),
                "slot": c["slot"], "slot_name": SLOT_NAMES[c["slot"]],
                "kind": "trackrom" if c["lod"] < 0 else "slot", "battle": c["slot"] >= FIRST_BATTLE_SLOT,
                "from_lod": c["lod"], "collection": groups[patch_md5] if is_pack else None,
                "patch": info["patch"], "has_nav": c["has_nav"], "extra_entries": info["extra_entries"],
                "exe_changed": info["exe_changed"], "size": c["size"],
            }
            (dest / "meta.json").write_text(json.dumps(meta, indent=1))
            metas.append(meta)
    return metas


def write_report(raws, metas, errors):
    got = {m["slug"] for m in metas}
    missing = [s for s in raws if s not in got]
    lines = ["# Extract report", "",
             f"{len(metas)} tracks extracted, {len(errors)} patches failed, {len(missing)} pages without a track",
             "", "## Failed", ""]
    lines += [f"- **{s}**: {why}" for s, why in errors.items()]
    lines += [f"- **{s}**: its patch held fewer tracks than pages, or the track is already in tracks/" for s in missing]
    lines += ["", "## Collections (one patch, several tracks; names assigned in page order, check them)", ""]
    for m in metas:
        if m["collection"]:
            lines.append(f"- {m['id']} = {m['name']} (LOD entry {m['from_lod']} of {m['patch']})")
    lines += ["", "## Possible issues (not carried into the multi-track ROM)", ""]
    for m in metas:
        notes = []
        if m["exe_changed"]:
            notes.append("patch also changes game code (SCUS exe)")
        if m["extra_entries"]:
            notes.append(f"also changes bigfile entries {m['extra_entries']}")
        if notes:
            lines.append(f"- **{m['id']}** ({m['slot_name']}): " + "; ".join(notes))
    REPORT.write_text("\n".join(lines) + "\n")


def slugify(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "track"


def best_patch(archive):
    """The .xdelta to use from a track archive, unpacked into raw/unpacked/<md5>/."""
    out = RAW_DIR / "unpacked" / md5(archive.read_bytes())
    if not out.exists():
        tmp = out.with_suffix(".tmp")
        shutil.rmtree(tmp, ignore_errors=True)
        subprocess.run(["7z", "x", "-y", f"-o{tmp}", str(archive)], check=True, stdout=subprocess.DEVNULL)
        tmp.rename(out)

    def rank(p):
        n = p.name.lower()
        if "bot" in n:
            return 0
        if re.search(r"tt\b|timetrial|ghost|no ?barrel|norespawn", n):
            return 2
        return 1
    patches = sorted((p for p in out.rglob("*") if p.suffix.lower() in PATCHES), key=lambda p: (rank(p), p.name))
    if not patches:
        raise RuntimeError(f"no .xdelta inside {archive.name}")
    if patches[1:]:
        print(f"{archive.name}: using {patches[0].name} (also: {[p.name for p in patches[1:]]})")
    return patches[0]


def dropped(tracks):
    """[(file, [page slugs])] for every patch / archive under tracks_in/; unknown
    files get a page entry of their own in tracks."""
    by_md5, by_name = collections.defaultdict(list), collections.defaultdict(list)
    for slug, t in tracks.items():
        for h in {t.get("file_md5"), t.get("patch_md5")} - {None}:
            by_md5[h].append(slug)
        if t.get("file_name"):
            by_name[t["file_name"].lower()].append(slug)
    files = sorted(p for p in IN_DIR.rglob("*") if p.is_file() and p.suffix.lower() in PATCHES + ARCHIVES)
    files.sort(key=lambda p: p.suffix.lower() in PATCHES)  # a patch beats an archive of the same page
    out = []
    for f in files:
        slugs = by_md5.get(md5(f.read_bytes())) or by_name.get(f.name.lower())
        if not slugs and f.parent != IN_DIR and f.parent.name in tracks:
            slugs = [f.parent.name]
        if not slugs:
            slug = slugify(f.stem)
            tracks.setdefault(slug, {"name": re.sub(r"[_-]+", " ", f.stem).strip(), "author": ""})
            slugs = [slug]
        out.append((f, slugs))
    return out


def main():
    tracks = json.loads(TRACKS_JSON.read_text()) if TRACKS_JSON.exists() else {}
    if "--force" in sys.argv:
        shutil.rmtree(RAW_DIR, ignore_errors=True)
    d = Disc(USA_BIN)
    stock, stock_exe = d.bigfile(), d.file("SCUS_944.26")
    d.close()

    raws, errors = {}, {}
    for f, slugs in dropped(tracks):
        try:
            patch = best_patch(f) if f.suffix.lower() in ARCHIVES else f
            patch_md5 = md5(patch.read_bytes())
            info = extract_raw(patch, patch_md5, stock, stock_exe)
            info["patch_md5"] = patch_md5
            if not info["candidates"]:
                raise RuntimeError(f"no level data changed; other bigfile changes: {info['extra_entries'][:20]}")
            for slug in slugs:
                raws[slug] = info
                errors.pop(slug, None)
        except Exception as e:
            for slug in slugs:
                errors[slug] = str(e)
            print(f"FAIL {f.name}: {e}", flush=True)

    OUT_DIR.mkdir(exist_ok=True)
    metas = classify(tracks, raws)
    for m in metas:
        print(f"OK   {m['id']}: {m['name']} on {m['slot_name']} ({m['kind']}, lod {m['from_lod']})", flush=True)
    if "--prune" in sys.argv:
        keep = {m["id"] for m in metas}
        for d in OUT_DIR.iterdir():
            if d.is_dir() and d.name not in keep:
                print(f"PRUNE {d.name}")
                shutil.rmtree(d)
    write_report(raws, metas, errors)
    print(f"{len(metas)} tracks from {IN_DIR.name}/, {len(errors)} failed -> {REPORT}")


if __name__ == "__main__":
    main()
