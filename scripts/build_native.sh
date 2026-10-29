#!/usr/bin/env bash
# Build the native PC version (native/, a ctr-native fork with MultiTrack ported in).
# The native game reads its data from a MultiTrack disc image built without the
# baked vertex lighting (lighting is done live there), out/CTR-MultiTrack-native.bin.
# Run scripts/setup.sh once first (scripts/build_all.sh: both).
#   scripts/build_native.sh               extract tracks_in/, build disc image + game
#   scripts/build_native.sh --no-extract  same, using tracks/ as it is
#   scripts/build_native.sh --no-disc     game only (reuse the existing disc image)
#   scripts/build_native.sh --no-shots    without taking missing track select pictures
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
NATIVE="$ROOT/native"
DISC=1 EXTRACT= SHOTS=1
for arg in "$@"; do
    case "$arg" in
        --no-disc) DISC=0 ;;
        --no-extract) EXTRACT=--no-extract ;;
        --no-shots) SHOTS=0 ;;
        *) echo "unknown option $arg" >&2; exit 2 ;;
    esac
done
source "$ROOT/env.sh"

# "NF" tracks in the list when the Nitro-Fueled package is unpacked
# (scripts/nf_tracks.py); the lights export below numbers tracks the same way.
# Your own package in rom-ps4/ is unpacked first (once), so the disc's track
# list has them
"$ROOT/scripts/nf_setup.sh"
export MT_NF=1

# the levels the disc's track list is made from, before it is packed: custom
# tracks, the stock levels (rt_bake.py copies them; its bake is cached), the NF
# tracks' Nitro-Fueled levels (what the ray tracer draws on them, from the
# user's own unpacked PS4 packages: scripts/nf_setup.sh; NF_ARCHIVES: where the
# game's are) and their collision (nf_collision.py)
if (( DISC )) && [[ -z "$EXTRACT" ]]; then
    python -I "$ROOT/scripts/extract_tracks.py"
fi
python -I "$ROOT/scripts/rt_bake.py" | grep -E "FAILED|baked [0-9]" || true
python -I "$ROOT/scripts/nf_level.py" all "$NATIVE/build/assets/rtx_remaster"
python -I "$ROOT/scripts/nf_collision.py" all "$NATIVE/build/assets/rtx_remaster"
python -I "$ROOT/scripts/nf_minimap.py" --out "$NATIVE/build/assets/track_maps"

if (( DISC )); then
    "$ROOT/scripts/build_rom.sh" --no-extract --out CTR-MultiTrack-native
fi

cmake -S "$NATIVE" -B "$NATIVE/build" -DCMAKE_BUILD_TYPE=Release -DCMAKE_C_FLAGS="-m32" -DCMAKE_EXE_LINKER_FLAGS="-m32" >/dev/null
cmake --build "$NATIVE/build" -j"$(nproc)" 2>&1 | grep -E "error|Linking" || true

# 64-bit ray tracer (NVIDIA's 32-bit Vulkan driver has no ray tracing)
mkdir -p "$NATIVE/build/rtx_shaders"
for s in trace atrous final lights cache sunfit bloom bake taa wind rembake lmbake; do
    glslc --target-env=vulkan1.3 -O -I"$NATIVE/rtx/shaders" "$NATIVE/rtx/shaders/$s.comp" -o "$NATIVE/build/rtx_shaders/$s.spv"
done
gcc -O2 -Wall -o "$NATIVE/build/ctr_rtx" "$NATIVE/rtx/ctr_rtx.c" -I"$NATIVE/rtx" -I"$NATIVE/externals/volk" \
    -I"$NATIVE/externals/Vulkan-Headers/include" -ldl -lm
# CPU lighting baker for Settings "Bake now" without a ray tracing GPU (the offline tracer)
gcc -O2 -fopenmp -o "$NATIVE/build/ctr_bake" "$ROOT/tools/rt_bake/rt_bake.c" -lm

mkdir -p "$NATIVE/build/assets"
ln -sf "$ROOT/out/CTR-MultiTrack-native.bin" "$NATIVE/build/assets/ctr-u.bin"
cp "$ROOT/psx-modding-toolchain/games/CTR/mods/MultiTrack/src/track_table.bin" "$NATIVE/build/assets/track_table.bin"
# indoor/outdoor per level comes from the offline bake's stats (rt_bake.py above)
python -I "$ROOT/scripts/rt_bake.py" --export-lights "$NATIVE/build/assets/rtx_lights"
cp "$NATIVE"/rtx/overrides/*.txt "$NATIVE/build/assets/rtx_lights/" 2>/dev/null || true # per-level overrides: <key>.override.txt
# track select pictures: a ray traced shot of each track's start, taken once
# (cached in out/track_shots; the first build takes them all, ~30 s a track; only
# missing ones after that: scripts/update_track_pictures.sh retakes them)
if (( SHOTS )); then
    python -I "$ROOT/scripts/track_shots.py" || echo "WARNING: track select pictures not taken (scripts/track_shots.py); the menu shows none"
else
    python -I "$ROOT/scripts/track_shots.py" --install-only
fi
ls -la "$NATIVE/build/ctr_native"
echo "run: $NATIVE/build/ctr_native"
