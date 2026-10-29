"""Track select pictures: a ray traced screenshot of the start of every track the
native build lists (stock, custom, NF), no karts and no HUD.

Each one is taken by booting the game straight into the track with
CTR_SHOT_CLEAN=1 (the game hides the karts and HUD and writes the frame once
the start camera has settled, then exits), inside a headless nested KWin when
there is one. Pictures are kept in out/track_shots/<track id>.png and only
taken when missing, so a build never retakes one; scripts/update_track_pictures.sh
(--force) retakes them. A track whose shot fails gets <id>.failed (skipped
until --retry) and the menu shows no picture for it. The game finds them as
native/build/assets/track_shots/<key>.png (stock-<levelID>, track-<n>: n-th
entry of the track table), links into the cache.

Several are taken at once (--jobs=N, default 8; each in its own nested KWin):
frame rate doesn't matter, the shot waits for the game's frame count, and each
game's ray tracer keeps its full sample count however slow (CTR_RTX_BUDGET_MS).

Usage: python -I scripts/track_shots.py [--force] [--retry] [--list] [--install-only] [--jobs=N] [id ...]
       (no ids: every track missing a picture; --install-only: link what there is)
"""
import os
import pathlib
import shutil
import signal
import struct
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import extract_tracks  # noqa: E402
import pack_tracks  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
BUILD = ROOT / "native/build"
GAME = BUILD / "ctr_native"
CACHE = ROOT / "out/track_shots"
INSTALL = BUILD / "assets/track_shots"
W, H = 1280, 720          # window the shot is taken at
SAVE_W, SAVE_H = 854, 480  # picture kept (the menu shows it at 170x71 PS1 pixels, ~440x250 at 720p)
STOCK_LEVELS = 25          # 18 race tracks, 7 battle arenas
TIMEOUT = 600  # (seconds; a shot ends when written: ~30 s alone, longer sharing the GPU)
JOBS = 20
RUNNING = set()  # process groups of the games being shot (Ctrl+C ends them)
STOPPED = threading.Event()


def table_entries():
    """(slot, flags, bigBase, name) of the built track table (assets/track_table.bin)"""
    data = (BUILD / "assets/track_table.bin").read_bytes()
    magic, count = struct.unpack_from("<II", data, 0)
    if magic != pack_tracks.MT_MAGIC:
        raise SystemExit("assets/track_table.bin: not a track table (build the game first)")
    size = struct.calcsize(f"<BBH{pack_tracks.NAME_LEN}s{pack_tracks.AUTHOR_LEN}s")
    out = []
    for k in range(count):
        slot, flags, base, name, _ = struct.unpack_from(f"<BBH{pack_tracks.NAME_LEN}s{pack_tracks.AUTHOR_LEN}s", data, 8 + k * size)
        out.append((slot, flags, base, name.split(b"\0")[0].decode("ascii", "replace")))
    return out


def tracks():
    """[(cache id, game key, boot env, label)] for everything the menu lists"""
    out = [(f"stock-{lev}", f"stock-{lev}", {"CTR_BOOT_LEVEL": str(lev)}, extract_tracks.SLOT_NAMES[lev]) for lev in range(STOCK_LEVELS)]
    pack_tracks.WITH_NF = True  # (the native build's table has the NF tracks)
    metas = pack_tracks.track_list()
    entries = table_entries()
    same = len(metas) == len(entries) and all(pack_tracks.font_text(m["name"], pack_tracks.NAME_LEN).decode() == e[3]
                                              for m, e in zip(metas, entries))
    for k, e in enumerate(entries):
        # cache by the track's own id (stable when tracks are added); the table
        # alone (a disc built from another track set): by its name
        tid = metas[k]["id"] if same else "name-" + "".join(c if c.isalnum() else "_" for c in e[3])
        out.append((str(tid), f"track-{k}", {"CTR_BOOT_TRACK": str(k)}, e[3]))
    return out


def shoot(tid, env_extra):
    """the clean start screenshot of one track as a PIL image, or None"""
    from PIL import Image
    work = CACHE / ".work" / tid
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    prefix = work / "shot"
    # (budget: the tracer's full samples even at a few frames a second)
    env = {"SDL_AUDIO_DRIVER": "dummy", "CTR_SHOT": str(prefix), "CTR_SHOT_CLEAN": "1", "CTR_RTX_BUDGET_MS": "100000", **env_extra}
    assign = " ".join(f"{k}={v}" for k, v in env.items())
    inner = f"cd {GAME.parent} && {assign} timeout -s TERM {TIMEOUT} ./{GAME.name}"
    log = open(work / "run.log", "w")
    if shutil.which("kwin_wayland"):
        cmd = ["timeout", str(TIMEOUT + 20), "dbus-run-session", "--", "kwin_wayland", "--virtual", "--no-lockscreen",
               "--socket", f"ctrshots-{os.getpid()}-{tid}", "--width", str(W), "--height", str(H), "--exit-with-session", f"sh -c '{inner}'"]
    else:
        # (no nested compositor: a window on this desktop, at the game's own size)
        cmd = ["sh", "-c", inner]
    p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
    RUNNING.add(p.pid)
    try:
        p.wait(TIMEOUT + 30)
    except subprocess.TimeoutExpired:
        pass
    end_group(p.pid)
    log.close()
    shots = sorted(work.glob("shot-*.ppm"))
    if not shots:
        return None
    img = Image.open(shots[-1]).convert("RGB")
    return img.resize((SAVE_W, SAVE_H), Image.LANCZOS)


def end_group(pid):
    RUNNING.discard(pid)
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass


def install(entries):
    """assets/track_shots/<game key>.png -> the cached pictures there are"""
    shutil.rmtree(INSTALL, ignore_errors=True)
    INSTALL.mkdir(parents=True)
    n = 0
    for tid, key, _, _ in entries:
        src = CACHE / f"{tid}.png"
        if src.exists():
            (INSTALL / f"{key}.png").symlink_to(src)
            n += 1
    return n


def main():
    args = sys.argv[1:]
    force, retry = "--force" in args, "--retry" in args
    ids = [a for a in args if not a.startswith("--")]
    if not GAME.exists():
        raise SystemExit("build the game first (scripts/build_native.sh)")
    CACHE.mkdir(parents=True, exist_ok=True)
    entries = tracks()
    if "--install-only" in args:
        print(f"[track_shots] {install(entries)} of {len(entries)} tracks have a picture")
        return 0
    if "--list" in args:
        for tid, key, env, label in entries:
            have = "ok" if (CACHE / f"{tid}.png").exists() else "failed" if (CACHE / f"{tid}.failed").exists() else "missing"
            print(f"{tid:40s} {key:10s} {have:8s} {label}")
        return 0
    todo = []
    for e in entries:
        tid = e[0]
        if ids and tid not in ids:
            continue
        png, failed = CACHE / f"{tid}.png", CACHE / f"{tid}.failed"
        if png.exists() and not force:
            continue
        if failed.exists() and not (retry or force or ids):
            continue
        todo.append(e)
    jobs = next((int(a.split("=", 1)[1]) for a in args if a.startswith("--jobs=")), JOBS)
    if todo:
        print(f"[track_shots] {len(todo)} pictures to take, {jobs} at a time (cached in {CACHE})")
    done = [0]
    lock = threading.Lock()

    def take(entry):
        tid, key, env, label = entry
        img = shoot(tid, env)
        if img is None and STOPPED.is_set():
            return  # (ended by Ctrl+C: not a failure)
        png, failed = CACHE / f"{tid}.png", CACHE / f"{tid}.failed"
        if img is None:
            # (its run.log stays in .work/<id>)
            failed.write_text(f"no frame written: see out/track_shots/.work/{tid}/run.log\n")
        else:
            img.save(png, optimize=True)
            failed.unlink(missing_ok=True)
            shutil.rmtree(CACHE / ".work" / tid, ignore_errors=True)
        with lock:
            done[0] += 1
            print(f"[track_shots] {done[0]}/{len(todo)} {label}{'' if img else ': FAILED'}", flush=True)

    pool = ThreadPoolExecutor(max(1, jobs))
    try:
        list(pool.map(take, todo))
    except KeyboardInterrupt:
        STOPPED.set()
        pool.shutdown(wait=False, cancel_futures=True)
        for pid in list(RUNNING):
            end_group(pid)
        print("[track_shots] stopped; pictures taken so far are kept")
        install(entries)
        return 1
    pool.shutdown()
    n = install(entries)
    print(f"[track_shots] {n} of {len(entries)} tracks have a picture")
    return 0


if __name__ == "__main__":
    sys.exit(main())
