#!/usr/bin/env bash
# Build out/CTR-MultiTrack.bin/.cue: stock USA CTR + every track in tracks/.
#   scripts/build_rom.sh            extract tracks_in/ patches, compile, build ISO
#   scripts/build_rom.sh --no-extract
#   scripts/build_rom.sh --out NAME     write out/NAME.bin/.cue instead of out/CTR-MultiTrack.*
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
source "$ROOT/env.sh"
MOD="$ROOT/psx-modding-toolchain/games/CTR/mods/MultiTrack"
BUILD="$ROOT/psx-modding-toolchain/games/CTR/build"
TOOL="$ROOT/psx-modding-toolchain/tools/mod-builder/main.py"

EXTRACT=1 NAME=CTR-MultiTrack
while (( $# )); do
    case "$1" in
        --no-extract) EXTRACT=0 ;;
        --out) NAME="$2"; shift ;;
        *) echo "unknown option $1" >&2; exit 2 ;;
    esac
    shift
done

if (( EXTRACT )); then
    python -I "$ROOT/scripts/extract_tracks.py"   # tracks_in/ -> tracks/
fi

rsync -a "$ROOT/mod/" "$MOD/"   # mod/ is the source; the mod-builder needs it inside the toolchain
python -I "$ROOT/scripts/pack_tracks.py" --table-only "$MOD/src/track_table.bin"

cd "$MOD"
python "$TOOL" 2 >/dev/null          # clean
python "$TOOL" 1 1 | grep -E "Compilation|error|warning" || true
python "$ROOT/scripts/check_sizes.py" "$MOD"
python "$TOOL" 4 1 | grep -E "pack_tracks|Plugin|Total image size|successfully" || true

mkdir -p "$ROOT/out"
mv "$BUILD/ctr-u_MultiTrack.bin" "$ROOT/out/$NAME.bin"
printf 'FILE "%s.bin" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n' "$NAME" > "$ROOT/out/$NAME.cue"
rm -rf "$BUILD/ctr-u_MultiTrack" "$BUILD/ctr-u_MultiTrack.cue"
ls -la "$ROOT/out/$NAME.bin"
python -I "$ROOT/scripts/verify_rom.py" "$ROOT/out/$NAME.bin"
