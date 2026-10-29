#!/usr/bin/env python3
"""Crash test of the native build: boot every level and drive it for a while.

Each level runs in its own headless nested KWin (several at once), with the AI
driving player 1 (CTR_AUTODRIVE=2), ray tracing on and CTR_RTX_DEBUG logs. A
run passes when the game reaches the target frame (CTR_SHOT writes it and
exits); anything else (a signal, the renderer dying or timing out, a hang, no
frame) is a failure. Logs go to out/crash_test/<id>.log, the summary to
out/crash_test/summary.md.
  scripts/native_crash_test.py [--frame N] [--jobs J] [--timeout S] [--game EXE] [--out DIR] [--env K=V ...] [id ...]
ids: stock-<slot> (0-29), boot, custom track ids (pack_tracks); default all.
Split screen, by id only: battle<n>-<slot> (18-24), race<n>-<slot> (0-17), n = 2-4.
"""
import concurrent.futures
import os
import pathlib
import re
import signal
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import pack_tracks  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
GAME = ROOT / "native/build/ctr_native"
OUT = ROOT / "out/crash_test"

PROBLEMS = [
    (r"Segmentation fault|SIGSEGV|EXIT=139", "game segfault"),
    (r"EXIT=134|Assertion .* failed", "game abort"),
    (r"EXIT=124|EXIT=143", "game hang (timeout: aborted, see its core dump)"),
    (r"renderer exited", "renderer died"),
    (r"renderer timed out", "renderer timeout"),
    (r"\[ctr_rtx\] .*failed: -?\d+", "vulkan call failed"),
    (r"shared memory version mismatch|missing shader", "renderer setup"),
]


def run_one(tid, env_extra, worker, frame, timeout, game=GAME):
    prefix = OUT / f".shot-{tid}"
    for old in OUT.glob(f".shot-{tid}-*.ppm"):
        old.unlink()
    env = {"SDL_AUDIO_DRIVER": "dummy", "CTR_SHOT": str(prefix), "CTR_SHOT_FRAME": str(frame), "CTR_AUTODRIVE": "2", "CTR_RTX_DEBUG": "1",
           "SDL_VIDEO_DRIVER": "x11", "__NV_PRIME_RENDER_OFFLOAD": "1", "__GLX_VENDOR_LIBRARY_NAME": "nvidia", **env_extra}
    envs = " ".join(f"{k}={v}" for k, v in env.items())
    inner = f"cd {game.parent} && {envs} timeout -s ABRT {timeout} ./{game.name}; echo EXIT=$?"
    log_path = OUT / f"{tid}.log"
    with open(log_path, "w") as log:
        p = subprocess.Popen(["timeout", str(timeout + 30), "dbus-run-session", "--", "kwin_wayland", "--virtual", "--no-lockscreen", "--socket",
                              f"ctrcrash-{worker}", "--width", "1280", "--height", "720", "--xwayland", "--exit-with-session", f"sh -c '{inner}'"],
                             stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
        p.wait()
    try:
        os.killpg(p.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    text = log_path.read_text(errors="replace")
    shots = list(OUT.glob(f".shot-{tid}-*.ppm"))
    for s in shots:
        s.unlink()
    found = [name for pattern, name in PROBLEMS if re.search(pattern, text)]
    if not shots and not found:
        found.append("no frame reached")
    exit_code = re.findall(r"EXIT=(\d+)", text)
    return tid, found, (exit_code[-1] if exit_code else "?"), bool(shots)


def main():
    args = sys.argv[1:]
    global OUT
    frame, jobs, timeout, ids, game, socket, extra = 900, 4, 150, [], GAME, "ctrcrash", {}
    while args:
        a = args.pop(0)
        if a == "--frame":
            frame = int(args.pop(0))
        elif a == "--jobs":
            jobs = int(args.pop(0))
        elif a == "--timeout":
            timeout = int(args.pop(0))
        elif a == "--game":
            game = pathlib.Path(args.pop(0)).resolve()
            socket = "ctrcrashalt"
        elif a == "--env":
            k, v = args.pop(0).split("=", 1)
            extra[k] = v
        elif a == "--out":
            OUT = pathlib.Path(args.pop(0)).resolve()
        else:
            ids.append(a)
    metas = pack_tracks.track_list()
    boot = {f"stock-{slot}": {"CTR_BOOT_LEVEL": str(slot)} for slot in range(30)}
    boot["boot"] = {}
    boot.update({m["id"]: {"CTR_BOOT_TRACK": str(k)} for k, m in enumerate(metas)})
    ids = ids or list(boot)
    # split screen (asked for by id only): battle<n>-<slot> = n players' battle on
    # an arena, race<n>-<slot> = n players' race
    for n in (2, 3, 4):
        boot.update({f"battle{n}-{slot}": {"CTR_BOOT_LEVEL": str(slot), "CTR_BOOT_PLAYERS": str(n), "CTR_BOOT_BATTLE": "1"} for slot in range(18, 25)})
        boot.update({f"race{n}-{slot}": {"CTR_BOOT_LEVEL": str(slot), "CTR_BOOT_PLAYERS": str(n)} for slot in range(18)})
    OUT.mkdir(parents=True, exist_ok=True)
    results = []
    free = list(range(jobs))
    with concurrent.futures.ThreadPoolExecutor(jobs) as pool:
        pending = {}
        queue = list(ids)
        while queue or pending:
            while queue and free:
                tid = queue.pop(0)
                w = free.pop()
                pending[pool.submit(run_one, tid, {**boot[tid], **extra}, f"{socket}{w}" if socket != "ctrcrash" else w, frame, timeout, game)] = w
            done, _ = concurrent.futures.wait(pending, return_when=concurrent.futures.FIRST_COMPLETED)
            for f in done:
                free.append(pending.pop(f))
                tid, found, code, ok = f.result()
                results.append((tid, found, code, ok))
                print(f"[crash_test] {tid}: {'ok' if not found else ', '.join(found)} (exit {code})", flush=True)
    order = {t: i for i, t in enumerate(ids)}
    results.sort(key=lambda r: order[r[0]])
    bad = [r for r in results if r[1]]
    lines = ["# Native crash test", "", f"{len(results)} levels, {len(bad)} failing (frame {frame}).", "", "| level | result | exit |", "|---|---|---|"]
    for tid, found, code, ok in results:
        lines.append(f"| {tid} | {'ok' if not found else ', '.join(found)} | {code} |")
    (OUT / "summary.md").write_text("\n".join(lines) + "\n")
    print(f"[crash_test] {len(bad)} of {len(results)} failing: {OUT / 'summary.md'}")


if __name__ == "__main__":
    main()
