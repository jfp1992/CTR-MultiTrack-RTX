# CTR MultiTrack RTX

Crash Team Racing running natively on PC with real-time ray tracing, plus every
custom track you drop in, picked from a scrolling list on the normal
track-select screen (Arcade, Time Trial, VS, Battle). Adventure mode and cups
stay stock.

The game runs from decompiled C code (a [ctr-native](https://github.com/CTR-tools/ctr-native)
fork in `native/`, MultiTrack edits marked `MultiTrack:`), not under an
emulator. A separate 64-bit Vulkan renderer (`native/rtx/`) ray traces the
level, karts and objects, lit from the tracks' own art.

**No game data is included.** You need your own Crash Team Racing (USA) disc
image and the custom tracks you want; optionally your own Crash Team Racing
Nitro-Fueled PS4 package for the "NF" tracks.

## Requirements

* Linux x86-64 and a GPU with Vulkan ray queries (`VK_KHR_ray_query`: NVIDIA
  RTX, AMD RDNA2+). The renderer is 64-bit (NVIDIA's 32-bit Vulkan driver has
  no ray tracing).
* Host tools: `git curl unzip rsync cmake make gcc` with 32-bit (multilib)
  support, `glslc` (shaderc), `wine` + wine-mono (runs `bigtool.exe`), `7z`,
  `python3`, and SDL3's 32-bit build dependencies (X11/Wayland, ALSA/PulseAudio,
  udev, dbus). `scripts/setup.sh` checks for them.
* NF tracks only: .NET 8 SDK and about 50 GB free.

## Build

```sh
git clone --recursive https://github.com/jfp1992/CTR-MultiTrack-RTX.git
cd CTR-MultiTrack-RTX
```

1. **PS1 disc (required):** Crash Team Racing (USA), SCUS-94426, redump-style
   `.bin`. Copy it to `rom-usa/ctr-usa.bin` (the `.cue` isn't needed).
2. **Nitro-Fueled (optional):** put your own unencrypted PS4 packages in
   `rom-ps4/`: the game, and optionally the v1.21 update (adds the Grand Prix
   tracks). Tested: CUSA13795 (USA); other releases get a warning.
3. **Custom tracks (optional):** copy the downloads (`.xdelta`, `.vcdiff`, or
   the `.zip`/`.rar`/`.7z` they come in) into `tracks_in/` as they are (see
   `tracks_in/README.txt`).
4. Run `scripts/build_all.sh`: `setup.sh` (fetches pinned dependencies, makes
   the venv, checks tools and files) then `build_native.sh` (unpacks the PS4
   packages, extracts tracks, builds the data disc
   `out/CTR-MultiTrack-native.bin`, the game and renderer, converts NF levels,
   bakes lighting). The first run can take an hour or more; finished steps are
   skipped later.
5. Run `native/build/ctr_native`.

Added tracks? Drop them in `tracks_in/` and rerun `scripts/build_native.sh`:

```sh
scripts/build_native.sh               # extract tracks_in/, disc + game
scripts/build_native.sh --no-extract  # use tracks/ as it is
scripts/build_native.sh --no-disc     # game only, reuse the disc image
scripts/build_native.sh --no-shots    # don't take missing track select pictures
```

## Playing

Track select lists every stock, custom and NF track: D-pad up/down, L1/R1 =
page of 9, X = race. Each entry shows a ray traced picture of its start; custom
tracks also show their creator and the stock track they replace. Arcade needs
tracks with AI paths (others show `NO AI PATHS - USE TIME TRIAL OR VS`); every
track works in Time Trial and VS.

Pictures live in `out/track_shots/`; builds only take missing ones. Retake them
after renderer or NF changes with `scripts/update_track_pictures.sh` (all,
`--jobs=N`, default 8), `... nf`, or `... <id> ...` (ids:
`scripts/track_shots.py --list`).

| Key | |
|---|---|
| F7 | ray tracing on/off |
| F5 | lighting: full RT / hybrid (PS1 lighting + traced extras) |
| F6 | debug views |
| F12 | settings overlay (keys 1-8: render scale, quality, TAA, …) |
| Shift+F5 / Shift+F6 / Shift+F7 | save state / gamepad / VRAM dump |
| PrtSc | screenshot |

Environment: `CTR_RTX=0` (no ray tracing), `CTR_RES=psx` (PS1 resolution),
`CTR_RTX_SCALE`, `CTR_RTX_QUALITY`, `CTR_RTX_TAA`, `CTR_RTX_BUDGET_MS`,
`CTR_RTX_PIPELINE`, `CTR_RTX_FOG`, `CTR_RTX_DEBUG=1` (logs).

### Nitro-Fueled tracks

With the PS4 package, the list gains an "NF" version of every stock track and
battle arena. The game plays the stock PS1 level (collision, AI, items) while
the ray tracer draws Nitro-Fueled's version: models, materials, normal and
emission maps, animated foliage and machinery, skybox, point lights and
emissive surfaces.

`build_native.sh` runs `scripts/nf_setup.sh`, which unpacks the package into
`nf_raw/` once. The converter is `scripts/nf_level.py` (archives `nf_iga.py`,
objects `nf_igz.py`, textures `nf_tex.py`); `nf_tracks.py` holds the track
list, model proxies (`PROXIES`) and animation speeds. Per-level placement ships
in `scripts/nf_placement.json` (`NF_SOLVE=1` re-solves; `nf_level.py fit all`
reports). Nothing from the package is shared: `nf_raw/` is git-ignored and
`package_src.sh` refuses its file types.

Environment: `CTR_RTX_REMASTER=0` (PS1 level on NF tracks),
`CTR_RTX_REMASTER_GRASS=0` (no foliage), `CTR_RTX_REMASTER_WIND=0` (still
foliage), `CTR_RTX_NM_FLIPY=1` (flip normal map green).

## Layout

| Path | What |
|---|---|
| `rom-usa/`, `rom-ps4/`, `tracks_in/` | your disc image, PS4 packages, track downloads (not included) |
| `tracks.json` | ctrcustomtracks.com list: names, authors, links, file md5s |
| `tracks/<id>/` | extracted `track.{vrm,lev}` + `meta.json`; optional `bake.json` lighting overrides (keys: `DEFAULTS` in `scripts/rt_bake.py`) |
| `stock_tracks/` | stock levels from your disc + their bakes |
| `native/` | native game + renderer (`native/rtx/`: `ctr_rtx.c`, `shaders/`, `overrides/`) |
| `mod/` | MultiTrack PS1 mod (`mod/src/`), synced into the toolchain by `build_rom.sh` |
| `patches/` | CTR-ModSDK `plugin.py` patch (applied by `setup.sh`), LibOrbisPkg .NET 8 patch (applied by `nf_setup.sh`) |
| `scripts/` | setup, build, extract, bake, NF, test tooling |
| `tools/rt_bake/` | offline path tracer |
| `out/` | built discs, screenshots, reports |
| `skip_tracks.txt`, `skip_bake.txt`, `track_names.json` | optional: ids to skip / not bake, `{"<id>": "Name"}` renames |

Fetched by `setup.sh`: `psx-modding-toolchain/` (CTR-ModSDK in `games/CTR`),
`tools/gcc-mipsel/`, `tools/xdelta3`, `native/externals/{volk,Vulkan-Headers}`,
`.venv/`.

## Development

```sh
.venv/bin/python -I scripts/shot_native.py --frame 1500 acid-lake stock-3   # headless shots -> out/shots/native/
.venv/bin/python -I scripts/rtx_level_report.py      # lighting report -> out/rtx_report/report.md
.venv/bin/python -I scripts/native_crash_test.py     # every track, AI driving, headless; stacks on hang/crash
.venv/bin/python -I scripts/rt_bake.py --force --obj /tmp/obj acid-lake  # rebake one track, dump .obj
.venv/bin/python -I scripts/extract_tracks.py --prune   # re-extract tracks_in/, drop orphaned tracks
scripts/package_src.sh                      # source release -> dist/ (refuses game data)
```

`shot_native.py` uses `CTR_BOOT_TRACK`, `CTR_BOOT_LEVEL`, `CTR_SHOT`,
`CTR_SHOT_FRAME`; `CTR_AUTODRIVE=2` lets the AI drive player 1.

`scripts/scrape_tracks.py list|download` refreshes `tracks.json` and fetches
patches with a headed browser (solve the captcha by hand; needs `playwright` +
`playwright install chromium` in the venv).

**PS1 disc:** `scripts/build_rom.sh` (`--no-extract` optional) builds
`out/CTR-MultiTrack.bin/.cue` for DuckStation with **"Enable 8MB RAM (Dev
Console)"** on; `scripts/verify_rom.py` checks it.

## How it works

* **Extraction:** custom tracks are xdelta patches that put the level in a
  stock BIGFILE.BIG slot (`levelID*8`, usually Crash Cove) or, for
  PS1_TrackROM builds, entries 221/222. `extract_tracks.py` applies each patch
  to the USA bin, diffs the bigfile against stock and keeps the real track
  (cached in `raw/`), dropping base-ROM leftovers, splitting collections/cups
  into one track per LOD entry, and de-duplicating. Results:
  `extract_report.md`.
* **Packing:** `pack_tracks.py` (via `mod/bigfile_post_build.py`) appends 8
  entries per track plus a name table to BIGFILE.BIG. The native game reads the
  disc like the PS1 does.
* **Lighting:** `rt_bake.py` traces each level offline
  (`tools/rt_bake/rt_bake.c`, cached) for its indoor/outdoor mode; the renderer
  derives the rest live from the art: fitted sun, lamp pools, emissive
  surfaces, multi-bounce GI via a radiance cache.
* **Renderer:** the 32-bit game starts `native/build/ctr_rtx` (64-bit Vulkan)
  and feeds it through shared memory (`native/rtx/rtx_shared.h`). The level is
  a static acceleration structure; karts, items and scenery are captured each
  frame from the GTE (`native/platform/native_rtx.c`); textures come from VRAM.
  Sky, HUD, particles, menus, cutscenes and split screen stay PS1.
* **Mod (`mod/src/`):** `bigfile_index.c` redirects a custom track's host slot
  to its appended files (not in Adventure/cups); `track_select.c` is the list
  menu (native port: `native/game/MultiTrack/`); `patches/mempack7mb.s` +
  `stack.s` + `disc/SYSTEM.CNF` give 7MB level memory; `patches/lod*.s` is
  PS1_TrackROM's vistree fix. `scripts/check_sizes.py` fails the build if a
  hook outgrows the function it overwrites.
* `tools/xdelta3` is used because pyxdelta lacks LZMA secondary compression.

## Known limits

* Only the level is carried over: patch extras (characters, menu text, music,
  physics changes) are not. Music is the host track's.
* 2P/4P use the 1P level; ray tracing is 1P only.
* A custom track's time-trial ghosts/records share its host slot's save.
* NF tracks: the sun is still fitted to the PS1 art (Nitro-Fueled has no global
  sun). Moving Design-map scenery isn't drawn; PS1 gameplay objects are, unless
  `nf_tracks.PROXIES` maps them to an NF model. Skinned characters (Papu's
  piranha plants) stay PS1.

## Licence

GPL-3.0 (see `LICENSE`), the same as ctr-native, which `native/` is a fork of
(`native/LICENSE`, `native/THIRD_PARTY_NOTICES.md`). No game data is included
or may be shared: you need your own copies of the games.

Not affiliated with or endorsed by Activision, Sony, or the authors credited
below.

## Credits

* [ctr-native](https://github.com/CTR-tools/ctr-native) (CTR-tools): the
  decompiled game this runs on.
* [CTR-ModSDK](https://github.com/CTR-tools/CTR-ModSDK),
  [psx-modding-toolchain](https://github.com/mateusfavarin/psx-modding-toolchain):
  the PS1 mod build and the game's symbols.
* [ctr-tools](https://github.com/CTR-tools/CTR-tools): `bigtool.exe`, level
  format notes.
* [PSn00bSDK](https://github.com/Lameguy64/PSn00bSDK): the MIPS gcc.
* [xdelta](https://github.com/jmacd/xdelta): track patches.
* [volk](https://github.com/zeux/volk),
  [Vulkan-Headers](https://github.com/KhronosGroup/Vulkan-Headers): Vulkan
  loader and headers.
* [LibOrbisPkg](https://github.com/maxton/LibOrbisPkg) (maxton): PS4 package
  unpacking.
* igLibrary (NefariousTechSupport) and the fmt_alchemy_igz Noesis plugin:
  Alchemy IGA / IGZ format notes.
* [ctrcustomtracks.com](https://ctrcustomtracks.com/): the custom track list
  (`tracks.json`), and every custom track's creator (see its page).
* Fan PS1 remakes the Crash Nitro Kart NF tracks play on: Anfrost (Jungle
  Boogie, Barin Ruins, Deep Sea Driving, Out of Time, Thunderstruck, Assembly
  Lane, Android Alley), Boxicfunk (Inferno Island), PanchoPewPew (Meteor
  Gorge), robthefryman (Electron Avenue).
