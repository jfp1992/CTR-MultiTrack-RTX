# CTR MultiTrack RTX

Crash Team Racing running natively on PC with real-time ray tracing, plus every
custom track you drop in, picked from a scrolling list on the normal
track-select screen (Arcade, Time Trial, VS, Battle). Adventure mode and cups
stay stock.

The game runs from the decompiled C code ([ctr-native](https://github.com/CTR-tools/ctr-native)
fork in `native/`, MultiTrack edits marked `MultiTrack:`), not under a PS1
emulator. A separate 64-bit Vulkan renderer (`native/rtx/`) ray traces the
level, karts and objects, lit from the tracks' own art.

**No game data is included.** You need your own Crash Team Racing (USA) disc
image and the custom tracks you want; optionally your own Crash Team Racing
Nitro-Fueled PS4 package for the "NF" tracks.

## Requirements

* Linux x86-64 and a GPU with Vulkan ray queries (`VK_KHR_ray_query`: NVIDIA
  RTX, AMD RDNA2+). On NVIDIA the 64-bit driver is used: its 32-bit Vulkan
  driver has no ray tracing.
* Host tools: `git curl unzip rsync cmake make gcc` with 32-bit (multilib)
  support, `glslc` (shaderc), `wine` + wine-mono (runs ctr-tools' `bigtool.exe`
  during the disc build), `7z` (p7zip), `python3`, and the SDL3 build
  dependencies (X11/Wayland, ALSA/PulseAudio, udev, dbus dev packages, 32-bit).
  `scripts/setup.sh` checks for the tools.
* NF tracks only: the .NET 8 SDK (unpacks the PS4 packages) and about 50 GB free.

## Build

```sh
git clone --recursive https://github.com/jfp1992/CTR-MultiTrack-RTX.git
cd CTR-MultiTrack-RTX
```

1. **PS1 disc (required).** Crash Team Racing (USA), SCUS-94426, a redump-style
   dump: one `.bin` + `.cue` (e.g. `CTR - Crash Team Racing (USA).bin`). Copy the
   `.bin` to `rom-usa/ctr-usa.bin` (rename it; the `.cue` isn't needed).
2. **Nitro-Fueled (optional, for the "NF" tracks).** Put both PS4 packages in
   `rom-ps4/` as they are (fake/debug PKGs; tested: CUSA13795 USA):
   * the game: `...CUSA13795-USA-Game-(6.72+)-PS4.pkg`
   * update v1.21: `...CUSA13795-USA-Update-v1.21-(6.72+)-PS4.pkg` (adds the
     Grand Prix tracks; optional)

   Needs the .NET 8 SDK and about 50 GB free.
3. **Custom tracks (optional).** Copy the downloads (`.xdelta`, `.vcdiff`, or
   the `.zip` / `.rar` / `.7z` they come in) into `tracks_in/` as they are
   (see `tracks_in/README.txt`).
4. Run `scripts/build_all.sh`. That is `scripts/setup.sh` (fetches the pinned
   psx-modding-toolchain, CTR-ModSDK, MIPS gcc, volk, Vulkan-Headers, xdelta3,
   makes the Python venv, checks the tools and your files) then
   `scripts/build_native.sh` (unpacks the PS4 packages, extracts the tracks,
   builds the game's data disc `out/CTR-MultiTrack-native.bin`, the game, the
   renderer, converts the NF levels). The first run also traces each level once
   offline and, with the PS4 packages, unpacks and converts them: allow an hour
   or more. Everything finished is skipped on later runs.
5. Run `native/build/ctr_native`.

Added more tracks? Drop them into `tracks_in/` and run `scripts/build_native.sh` again.

```sh
scripts/build_native.sh               # extract tracks_in/, disc + game
scripts/build_native.sh --no-extract  # use tracks/ as it is
scripts/build_native.sh --no-disc     # game only, reuse the disc image
scripts/build_native.sh --no-shots    # don't take missing track select pictures
```

## Playing

Track select is the PS1's carousel again, with every stock, custom and NF track
on it: D-pad up/down, L1/R1 = a page of 9, X = race. Each track shows a ray
traced picture of its start (stock tracks and their NF versions also their
minimap); under a custom track's picture, its creator and the stock track it
runs in place of. The pictures are taken by the build (`scripts/track_shots.py`:
the game boots into each track with `CTR_SHOT_CLEAN=1`, no karts, no HUD) and
kept in `out/track_shots/`: a build only takes the pictures that are missing,
never retakes one. After changing the renderer or the NF levels, update them
yourself with `scripts/update_track_pictures.sh` (every track, 8 at a time:
`--jobs=N` for another number),
`scripts/update_track_pictures.sh nf` (the NF tracks) or
`scripts/update_track_pictures.sh <id> ...` (`scripts/track_shots.py --list`
shows the ids). Only tracks
whose creators made AI paths can be raced in Arcade (the others show
`NO AI PATHS - USE TIME TRIAL OR VS`); every track works in Time Trial and VS.

| Key | |
|---|---|
| F7 | ray tracing on/off |
| F5 | lighting style: full RT / hybrid (PS1 lighting + traced extras) |
| F6 | debug views |
| F12 | settings overlay (keys 1-8: render scale, quality, TAA, …) |
| Shift+F5 / Shift+F6 / Shift+F7 | save state / gamepad / VRAM dump |
| PrtSc | screenshot |

Environment: `CTR_RTX=0` (no ray tracing), `CTR_RES=psx` (PS1 resolution),
`CTR_RTX_SCALE`, `CTR_RTX_QUALITY`, `CTR_RTX_TAA`, `CTR_RTX_BUDGET_MS`,
`CTR_RTX_PIPELINE`, `CTR_RTX_FOG`, `CTR_RTX_DEBUG=1` (logs).

### Nitro-Fueled tracks

With your own Crash Team Racing Nitro-Fueled PS4 package, the track list gains
an "NF" version of every stock track and battle arena (NF Crash Cove, NF Skull
Rock, ...): the game plays the stock PS1 level (collision, AI, items) and the
ray tracer draws Nitro-Fueled's version of it (models, materials, normal and
emission maps, swaying foliage, turning propellers / fans / gears, skybox,
point lights; Dragon Mines' carts as Nitro-Fueled carts with headlamps; glass
clear and slightly reflective, as is the ice round Polar Pass' bear; lava and
other glowing surfaces light their surroundings; snow only on rocks' tops). The
stock entries stay as they were. Native build only.

Put the package in `rom-ps4/` and build as above (`scripts/build_native.sh`
runs `scripts/nf_setup.sh`, which unpacks it into `nf_raw/` once; it can also
be given a package path). Tested with CUSA13795 (USA); other releases get a
warning.

`scripts/nf_level.py` reads the Alchemy engine archives (`nf_iga.py`), object
files (`nf_igz.py`) and PS4 textures (`nf_tex.py`); `scripts/nf_tracks.py` lists
the tracks, moving objects drawn as NF models (`PROXIES`) and animation speeds
(`ANIM_SPEED`). Nitro-Fueled builds each track 1.5x larger and turned its own
way: the placement was solved per level from the race spline and the floor and
ships in `scripts/nf_placement.json`, so every build lines the levels up alike
(`NF_SOLVE=1` solves again; `nf_level.py fit all` reports it). Environment: `CTR_RTX_REMASTER=0` (draw the
PS1 level on NF tracks too), `CTR_RTX_REMASTER_GRASS=0` (no grass / foliage),
`CTR_RTX_REMASTER_WIND=0` (foliage stands still), `CTR_RTX_NM_FLIPY=1` (normal
maps' green the other way up). Nothing from the package is shared: `nf_raw/`
is ignored and `scripts/package_src.sh` refuses its file types.

## Layout

| Path | What |
|---|---|
| `rom-usa/ctr-usa.bin` | your USA disc image (not included) |
| `tracks_in/` | your custom-track downloads (`.xdelta`, `.vcdiff`, `.zip`, `.rar`, `.7z`) |
| `tracks.json` | ctrcustomtracks.com track list: names, authors, download links, file md5s (to name dropped files and split cups) |
| `tracks/<id>/` | extracted level `track.{vrm,lev}` + `meta.json`; `bake.json` (optional, hand-made) = per-track lighting overrides (keys: `DEFAULTS` in `scripts/rt_bake.py`) |
| `stock_tracks/<slot>-<lod>/` | stock levels from your disc + their bakes |
| `native/` | native game + ray tracer (`native/rtx/`: `ctr_rtx.c`, `shaders/`, per-level `overrides/`) |
| `mod/` | MultiTrack PS1 mod (C): track list menu, bigfile redirect, memory patches; synced into the toolchain by `build_rom.sh` |
| `patches/` | change to CTR-ModSDK's `plugins/plugin.py` (wine runner, `bigfile_post_build.py` hook), applied by `setup.sh` |
| `scripts/` | setup, build, extract, bake, test tooling |
| `tools/rt_bake/` | offline path tracer (built automatically) |
| `out/` | built disc images, screenshots, reports |
| `skip_tracks.txt`, `skip_bake.txt`, `track_names.json` | (optional) track ids to leave out / not bake, `{"<id>": "Name"}` renames |

Fetched by `setup.sh` (not shipped): `psx-modding-toolchain/` (CTR-ModSDK in
`games/CTR`), `tools/gcc-mipsel/`, `tools/xdelta3`, `native/externals/{volk,Vulkan-Headers}`, `.venv/`.

## Development

```sh
scripts/build_native.sh                     # rebuild everything (see above)
.venv/bin/python -I scripts/shot_native.py --frame 1500 acid-lake stock-3   # headless screenshots -> out/shots/native/
.venv/bin/python -I scripts/rtx_level_report.py      # per-level lighting log -> out/rtx_report/report.md
.venv/bin/python -I scripts/native_crash_test.py     # every track, AI driving, headless; hangs/crashes get stacks
.venv/bin/python -I scripts/rt_bake.py --force --obj /tmp/obj acid-lake  # rebake one track, dump .obj with vertex colours
.venv/bin/python -I scripts/extract_tracks.py --prune   # re-extract tracks_in/, drop tracks with no patch left
scripts/package_src.sh                      # source release -> dist/ (refuses game data)
```

`shot_native.py` uses `CTR_BOOT_TRACK`, `CTR_BOOT_LEVEL`, `CTR_SHOT`,
`CTR_SHOT_FRAME`; `CTR_AUTODRIVE=2` lets the AI drive player 1.

Optional: `scripts/scrape_tracks.py list|download` refreshes `tracks.json` and
fetches patches into `tracks_in/<slug>/` with a headed browser (solve the
site's captcha by hand; needs `pip install playwright` +
`playwright install chromium` in the venv).

### PS1 disc

The same tooling still builds a PS1 disc (DuckStation, **"Enable 8MB RAM (Dev
Console)"** on): `scripts/build_rom.sh` -> `out/CTR-MultiTrack.bin/.cue`
(`--no-extract` to use `tracks/` as it is). `verify_rom.py` runs static checks on it.

## How it works

* Custom tracks ship as xdelta patches made with custom 1P-only engines. The
  level sits in a stock level's BIGFILE.BIG slot (`levelID*8`, almost always
  Crash Cove) or, for PS1_TrackROM builds, in entries 221/222 (Dingo Canyon).
  `extract_tracks.py` applies every patch in `tracks_in/` to the USA bin, diffs
  the bigfile against stock and keeps the real track (cached per patch in `raw/`):
  * the slot's other LOD entries (2P/4P/relic) are leftovers from base ROMs
    (same file in 3+ unrelated patches, or a known one) and are dropped;
  * a patch linked from several pages, or with 4 different levels, is a
    collection/cup: one track per LOD entry, named from the pack's own menu
    strings, the site's page order, or "<cup> #N";
  * a track released alone and inside a cup is kept once;
  * a file `tracks.json` doesn't know becomes a track named after the file.
  `extract_report.md` lists failures, collections and patches that change more
  than the level.
* `pack_tracks.py` (run by the toolchain through `mod/bigfile_post_build.py`)
  appends 8 entries per track (one lev/vrm for every LOD, stored once) plus a
  name table to BIGFILE.BIG. The native game reads the disc like the PS1 does.
* `rt_bake.py` traces each level offline (`tools/rt_bake/rt_bake.c`, cached by
  lev md5 + params + code). The native renderer takes the level's indoor/outdoor mode from it
  (`--export-lights` -> `native/build/assets/rtx_lights/`) and works out the rest
  live from the level's art: sun fitted to the vertex colours, lamp pools,
  glowing surfaces, multi-bounce GI through a radiance cache.
* Native renderer: the 32-bit game (`native/build/ctr_native`) starts
  `native/build/ctr_rtx`, a 64-bit Vulkan renderer, and feeds it through shared
  memory (`native/rtx/rtx_shared.h`). The level mesh is a static acceleration
  structure; karts, items and scenery are captured each frame from the GTE
  (`native/platform/native_rtx.c`). PS1 textures are read straight from VRAM.
  Sky, HUD and particles stay on the PS1 path. Menus, cutscenes and split
  screen keep the PS1 look.
* The mod (`mod/`):
  * `bigfile_index.c` replaces `LOAD_GetBigfileIndex`: while a custom track is
    selected (and not in Adventure or cups) its host slot loads the appended files;
  * `track_select.c` replaces the track-select menu with the list
    (native port: `native/game/MultiTrack/`);
  * `patches/mempack7mb.s` + `stack.s` + `src/disc/SYSTEM.CNF`: 7MB level memory
    (custom levels go up to 2.5MB) and the stack moved to the top of 8MB;
  * `patches/lod*.s`: PS1_TrackROM's vistree fix for big levels (1P).
* `scripts/check_sizes.py` fails the build if a hook is larger than the
  function it overwrites (the toolchain does not check this).
* `tools/xdelta3`: pyxdelta lacks LZMA secondary compression (GCN Baby Park).

## Known limits

* Only the level is carried over. Patch extras (custom characters, menu text,
  music, engine changes such as Retro-Fueled physics) are not; see
  `extract_report.md`. Music is the host track's.
* 2P/4P use the 1P level; ray tracing is 1P only.
* Time-trial ghosts/records of a custom track share its host slot's save.
* NF tracks: Nitro-Fueled keeps no global sun (its maps only override it per
  zone), so the sun's direction and colour are still fitted to the PS1 level's
  art; sky light and lamps are Nitro-Fueled's. Moving scenery (Design maps:
  boats, turtles' NF versions) isn't drawn: the PS1 gameplay objects are,
  except the ones `nf_tracks.PROXIES` maps to an NF model (Dragon Mines' carts).
  Skinned characters (Papu's piranha plants) stay PS1: their animations are
  Havok data the converter doesn't read.

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
  format notes (quad flags).
* [PSn00bSDK](https://github.com/Lameguy64/PSn00bSDK): the MIPS gcc.
* [xdelta](https://github.com/jmacd/xdelta): track patches.
* [volk](https://github.com/zeux/volk),
  [Vulkan-Headers](https://github.com/KhronosGroup/Vulkan-Headers): the
  renderer's Vulkan loader and headers.
* [LibOrbisPkg](https://github.com/maxton/LibOrbisPkg) (maxton): unpacks the PS4
  package.
* igLibrary (NefariousTechSupport) and the fmt_alchemy_igz Noesis plugin:
  Alchemy engine IGA / IGZ format notes behind the Nitro-Fueled converter.
* [ctrcustomtracks.com](https://ctrcustomtracks.com/): the custom track list
  (`tracks.json`), and every custom track's creator (see its page).
* Fan PS1 remakes the Crash Nitro Kart NF tracks play on: Anfrost (Jungle
  Boogie, Barin Ruins, Deep Sea Driving, Out of Time, Thunderstruck, Assembly
  Lane, Android Alley), Boxicfunk (Inferno Island), PanchoPewPew (Meteor
  Gorge), robthefryman (Electron Avenue).
