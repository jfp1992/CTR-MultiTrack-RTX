#!/usr/bin/env bash
# Source release for sharing: dist/CTR-MultiTrack-RTX-src.tar.gz. Only this
# project's own files go in (no game data, no track levels, no fetched
# toolchain); the receiver adds their own disc image / PS4 package and runs
# scripts/build_all.sh.
#   scripts/package_src.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
NAME=CTR-MultiTrack-RTX
OUT="$ROOT/dist/$NAME-src.tar.gz"
LIST="$(mktemp)"
trap 'rm -f "$LIST"' EXIT

cd "$ROOT"
{
    printf '%s\n' README.md .gitignore env.sh requirements.txt tracks.json tools/rt_bake/rt_bake.c \
        rom-usa/README.txt rom-ps4/README.txt tracks_in/README.txt
    find scripts mod patches -type f ! -path '*/__pycache__/*'
    # native/: tracked + new files, minus local screenshots / notes (volk and
    # Vulkan-Headers are fetched by setup.sh)
    git -C native ls-files -co --exclude-standard | grep -vE '^[^/]+\.png$|^Good Tracks$' | sed 's|^|native/|'
} | sort -u > "$LIST"

# nothing from the game disc or a track patch may ship
if grep -iE '\.(bin|cue|iso|img|chd|lev|vrm|big|xdelta|vcdiff|zip|rar|7z|spv|tga|mcd|pkg|pak|igz|igb|hkx|rtxm)$|(^|/)scus_[0-9.]+$' "$LIST"; then
    echo "ERROR: game data in the file list above" >&2
    exit 1
fi

mkdir -p "$ROOT/dist"
tar -czf "$OUT" --transform "s|^|$NAME/|" -T "$LIST"
echo "$(wc -l < "$LIST") files -> $OUT ($(du -h "$OUT" | cut -f1))"
