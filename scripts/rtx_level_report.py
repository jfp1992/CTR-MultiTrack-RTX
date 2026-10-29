#!/usr/bin/env python3
"""Per-level report of the native ray tracer's lighting (log only, no images).

Boots every stock level (or the given slots) with scripts/shot_native.py and
CTR_RTX_DEBUG=1, keeps each run's log in out/rtx_report/ and writes
out/rtx_report/report.md: indoor/outdoor, sun fit, ambient, light counts,
art-fit error, exposure, samples per pixel, renderer problems.
  scripts/rtx_level_report.py [slot ...]
"""
import pathlib
import re
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "out/rtx_report"
LOG = ROOT / "out/shots/native/run.log"
NAMES = {}


def level_name(slot):
    entry = ROOT / f"stock_tracks/{slot:02d}-0/entry.json"
    if entry.exists():
        import json
        return json.loads(entry.read_text()).get("name", "").replace(" 1P", "")
    return f"slot {slot}"


def parse(text):
    r = {}
    def grab(pattern, key, cast=str, last=False):
        found = re.findall(pattern, text)
        if found:
            r[key] = cast(found[-1] if last else found[0])
    grab(r"lighting stock-\d+: (indoor|outdoor)", "file")
    grab(r"sun fit \(r ([\d.]+)", "sun_r", float)
    grab(r"sun fit .*sun ([\d.]+ [\d.]+ [\d.]+)", "sun")
    grab(r"no direction explains the vertex colours \(best r ([-\d.]+)\)", "sun_none", float)
    grab(r"ambient from the vertex colours: ([\d. ]+)", "ambient")
    grab(r"sky from the level's skybox colours \(x([\d.]+)\)", "sky_scale", float)
    grab(r"(\d+) glowing triangles -> (\d+) lights", "glow")
    grab(r"lamp pools: .*-> (\d+) lights", "pools", int)
    m = re.search(r"art fit \((\d+) vertices.*error ([\d.]+) -> ([\d.]+), sun x([\d.]+), sky x([\d.]+), lights (\d+) -> (\d+), (\d+) glowing", text)
    if m:
        r["fit_err0"], r["fit_err"] = float(m.group(2)), float(m.group(3))
        r["fit_sun"], r["fit_sky"] = float(m.group(4)), float(m.group(5))
        r["lights"] = f"{m.group(6)}->{m.group(7)}"
        r["glow_verts"] = int(m.group(8))
    grab(r"exposure ([\d.]+)", "exposure", float, last=True)
    grab(r"exposure .*?, (\d+) spp", "spp", int, last=True)
    grab(r"renderer ([\d.]+) ms", "ms", float, last=True)
    grab(r"(\d+) emitters ->", "emitters", int, last=True)
    r["timeouts"] = text.count("renderer timed out")
    r["errors"] = len(re.findall(r"failed: -?\d+|renderer exited|Segmentation|validation", text))
    return r


def main():
    slots = [int(a) for a in sys.argv[1:]] or list(range(25))
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    for slot in slots:
        print(f"[rtx_report] stock-{slot} {level_name(slot)}", flush=True)
        subprocess.run([sys.executable, str(ROOT / "scripts/shot_native.py"), "--frame", "900", "--env", "CTR_RTX_DEBUG=1", f"stock-{slot}"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=400)
        if LOG.exists():
            shutil.copy2(LOG, OUT / f"stock-{slot}.log")
        text = (OUT / f"stock-{slot}.log").read_text(errors="replace") if (OUT / f"stock-{slot}.log").exists() else ""
        rows.append((slot, parse(text)))
    cols = ["file", "sun_r", "sun_none", "sun", "ambient", "sky_scale", "glow", "pools", "lights", "fit_err0", "fit_err", "fit_sun", "fit_sky",
            "glow_verts", "exposure", "spp", "ms", "emitters", "timeouts", "errors"]
    lines = ["# Ray tracer per-level report", "", "| slot | level | " + " | ".join(cols) + " |", "|" + "---|" * (len(cols) + 2)]
    for slot, r in rows:
        lines.append(f"| {slot} | {level_name(slot)} | " + " | ".join(str(r.get(c, "")) for c in cols) + " |")
    (OUT / "report.md").write_text("\n".join(lines) + "\n")
    print(f"[rtx_report] {OUT / 'report.md'}")


if __name__ == "__main__":
    main()
