#!/usr/bin/env bash
# Everything from your own game files to a runnable game: put your Crash Team
# Racing (USA) disc image at rom-usa/ctr-usa.bin, optionally your Nitro-Fueled
# PS4 package in rom-ps4/ (NF tracks) and custom tracks in tracks_in/, then
#   scripts/build_all.sh
# = scripts/setup.sh (toolchain, checks) + scripts/build_native.sh (unpacks the
# PS4 package, builds the disc image, the game, the renderer, converts the NF
# levels). Safe to rerun: finished steps are skipped.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
"$ROOT/scripts/setup.sh"
"$ROOT/scripts/build_native.sh" "$@"
