"""In-game screenshots from the native PC build (native/build/ctr_native).

Boots straight into one level (CTR_BOOT_TRACK / CTR_BOOT_LEVEL), runs it inside
a headless nested KWin and writes the N-th presented frame (CTR_SHOT /
CTR_SHOT_FRAME), then exits. Native counterpart of scripts/shot_track.py.

Usage: python -I scripts/shot_native.py [--frame N[,N...]] [--suffix S] [--env K=V ...] [--gpu nvidia]
                                        [--disc FILE.bin] [--game EXE] track_id|stock-<slot>|boot ...
       -> out/shots/native/<id>[S][-N].png ("boot" = normal start: intro, menus)
--gpu nvidia renders GL on the NVIDIA card (default: the compositor's choice).
--disc runs with another disc image (e.g. rom-usa/ctr-usa.bin) via a temporary assets dir.
"""
import os
import pathlib
import shutil
import signal
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import pack_tracks  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
GAME = ROOT / "native/build/ctr_native"
OUT = ROOT / "out/shots/native"
W, H = 1280, 720


def run(env_extra, frames, secs=120, game=GAME, gpu=None):
    prefix = OUT / ".shot"
    for old in OUT.glob(".shot-*.ppm"):
        old.unlink()
    env = " ".join(f"{k}={v}" for k, v in env_extra.items())
    xwayland = []
    if gpu == "nvidia":
        # 32-bit NVIDIA GL only works through GLX here: Xwayland + PRIME render offload
        env += " SDL_VIDEO_DRIVER=x11 __NV_PRIME_RENDER_OFFLOAD=1 __GLX_VENDOR_LIBRARY_NAME=nvidia"
        xwayland = ["--xwayland"]
    inner = (f"cd {game.parent} && SDL_AUDIO_DRIVER=dummy CTR_SHOT={prefix} CTR_SHOT_FRAME={frames} {env} "
             f"timeout -s TERM {secs} ./{game.name}")
    with open(OUT / "run.log", "w") as log:
        p = subprocess.Popen(["timeout", str(secs + 20), "dbus-run-session", "--", "kwin_wayland", "--virtual",
                              "--no-lockscreen", "--socket", "ctrnative-0", "--width", str(W), "--height", str(H), *xwayland,
                              "--exit-with-session", f"sh -c '{inner}'"],
                             stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
        p.wait()
    try:
        os.killpg(p.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    shots = sorted(OUT.glob(".shot-*.ppm"), key=lambda q: int(q.stem.split("-")[1]))
    if not shots:
        raise SystemExit(f"no frame written (see {OUT / 'run.log'})")
    return shots


def main():
    args = sys.argv[1:]
    frame, suffix, env, gpu, disc, game = "450", "", {}, None, None, GAME
    ids = []
    while args:
        a = args.pop(0)
        if a == "--frame":
            frame = args.pop(0)
        elif a == "--gpu":
            gpu = args.pop(0)
        elif a == "--disc":
            disc = pathlib.Path(args.pop(0)).resolve()
        elif a == "--game":
            game = pathlib.Path(args.pop(0)).resolve()
        elif a == "--suffix":
            suffix = args.pop(0)
        elif a == "--env":
            k, v = args.pop(0).split("=", 1)
            env[k] = v
        else:
            ids.append(a)
    if not ids:
        raise SystemExit(__doc__)
    from PIL import Image

    metas = pack_tracks.track_list()
    boot = {m["id"]: {"CTR_BOOT_TRACK": k} for k, m in enumerate(metas)}
    boot.update({f"stock-{slot}": {"CTR_BOOT_LEVEL": slot} for slot in range(25)})
    boot["boot"] = {}
    OUT.mkdir(parents=True, exist_ok=True)
    if disc is not None:
        # same game, other disc: a scratch dir with the exe, shaders and an assets/ dir
        alt = OUT / ".altgame"
        shutil.rmtree(alt, ignore_errors=True)
        (alt / "assets").mkdir(parents=True)
        for name in (game.name, "ctr_rtx"):  # copies: SDL finds assets/ next to the real exe path
            if (game.parent / name).exists():
                shutil.copy2(game.parent / name, alt / name)
        if (game.parent / "rtx_shaders").exists():
            (alt / "rtx_shaders").symlink_to(game.parent / "rtx_shaders")
        (alt / "assets/ctr-u.bin").symlink_to(disc)
        game = alt / game.name
    for tid in ids:
        if tid not in boot:
            raise SystemExit(f"unknown track {tid}")
        shots = run({**boot[tid], **env}, frame, game=game, gpu=gpu)
        for ppm in shots:
            n = ppm.stem.split("-")[1]
            out = OUT / (f"{tid}{suffix}.png" if len(shots) == 1 else f"{tid}{suffix}-{n}.png")
            Image.open(ppm).save(out)
            ppm.unlink()
            print(f"[shot_native] {out}")


if __name__ == "__main__":
    main()
