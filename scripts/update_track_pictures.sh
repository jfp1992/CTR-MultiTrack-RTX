#!/usr/bin/env bash
# Retake the track select pictures (out/track_shots/) from the game as it is
# now: after renderer or NF level changes. Builds only take pictures a track is
# missing; this retakes existing ones too (8 games at a time; --jobs=N to change).
#   scripts/update_track_pictures.sh            every track
#   scripts/update_track_pictures.sh nf         the NF tracks
#   scripts/update_track_pictures.sh <id> ...   those (scripts/track_shots.py --list shows ids)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
[[ -f "$ROOT/env.sh" ]] && source "$ROOT/env.sh"
ids=("$@")
if [[ "${1:-}" == nf ]]; then
    mapfile -t ids < <(python -I "$ROOT/scripts/track_shots.py" --list | awk '$1 ~ /^nf-/ { print $1 }')
    ids+=("${@:2}") # (--jobs=N)
fi
python -I "$ROOT/scripts/track_shots.py" --force "${ids[@]}"
