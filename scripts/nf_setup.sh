#!/usr/bin/env bash
# Nitro-Fueled remaster levels, step 1: unpack your own Crash Team Racing
# Nitro-Fueled PS4 package (an unencrypted backup: the zero passcode) into nf_raw/pkg,
# and its update package (optional; the Grand Prix tracks) into nf_raw/pkg_update.
# Builds LibOrbisPkg's PkgTool (pinned, patched for .NET 8: OpenSSL 3, and
# DeflateStream partial reads, which left every 64 KB block mostly zeros) into
# tools/pkgtool. Needs the .NET 8+ SDK. Nothing from the game is ever shared:
# nf_raw/ is ignored and scripts/package_src.sh refuses its formats.
#   scripts/nf_setup.sh               the .pkg files in rom-ps4/ (none: nothing to do)
#   scripts/nf_setup.sh <pkg>...      those (game and/or update)
# Game or update: told apart by the PKG header's content flags (patch bit).
# Already unpacked from the same file: skipped. scripts/build_native.sh runs it,
# then converts the levels (scripts/nf_level.py all).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TOOL="$ROOT/tools/pkgtool"
OUT="$ROOT/nf_raw/pkg"
OUT_UPDATE="$ROOT/nf_raw/pkg_update"
LIBORBIS_COMMIT=643477263b2644e0803e0f58b8726ea4e3f3b7d4
TESTED_TITLE=CUSA13795 # Crash Team Racing Nitro-Fueled (USA)

if (( $# )); then
    PKGS=("$@")
else
    shopt -s nullglob
    PKGS=("$ROOT"/rom-ps4/*.pkg)
    (( ${#PKGS[@]} )) || exit 0 # no Nitro-Fueled package: no NF tracks
fi

# content flags (PKG header 0x78, big endian): 0x40000000 = a patch (update)
is_update() {
    local flags
    flags=$(dd if="$1" bs=1 skip=$((0x78)) count=4 2>/dev/null | od -An -tx1 | tr -d ' \n')
    (( (0x$flags & 0x40000000) != 0 ))
}

GAME="" UPDATE=""
for p in "${PKGS[@]}"; do
    [[ -f "$p" ]] || { echo "ERROR: no such package: $p" >&2; exit 1; }
    magic=$(head -c 4 "$p" | od -An -c | tr -d ' ')
    [[ "$magic" == '177CNT' ]] || { echo "ERROR: $p is not a PS4 package" >&2; exit 1; }
    if is_update "$p"; then
        [[ -z "$UPDATE" ]] || { echo "ERROR: more than one update .pkg: keep one" >&2; exit 1; }
        UPDATE="$p"
    else
        [[ -z "$GAME" ]] || { echo "ERROR: more than one game .pkg: keep one" >&2; exit 1; }
        GAME="$p"
    fi
done

build_tool() {
    command -v dotnet >/dev/null && dotnet --list-sdks 2>/dev/null | grep -qE '^([89]|[1-9][0-9])\.' \
        || { echo "ERROR: unpacking the PS4 package needs the .NET 8 (or newer) SDK (e.g. package dotnet-sdk)" >&2; exit 1; }
    if [[ ! -f "$TOOL/bin/PkgTool.Core.dll" ]]; then
        echo "== building PkgTool (LibOrbisPkg @ ${LIBORBIS_COMMIT:0:9})"
        rm -rf "$TOOL/src"
        git clone -q https://github.com/maxton/LibOrbisPkg.git "$TOOL/src"
        git -C "$TOOL/src" checkout -q "$LIBORBIS_COMMIT"
        git -C "$TOOL/src" apply "$ROOT/patches/LibOrbisPkg-net8.patch"
        dotnet build -c Release "$TOOL/src/PkgTool.Core/PkgTool.Core.csproj" -o "$TOOL/bin" >/dev/null
    fi
}

# unpack <pkg> <out dir> <what>
unpack() {
    local pkg="$1" out="$2" what="$3" stamp="$2/.unpacked" id content free n
    # the same file unpacked before: done
    id="$(basename "$pkg") $(stat -c '%s %Y' "$pkg")"
    if [[ -f "$stamp" && "$(cat "$stamp")" == "$id" && -d "$out/uroot/archives" ]]; then
        return 0
    fi
    # content ID at 0x40 (UP0002-CUSA13795_00-...)
    content=$(dd if="$pkg" bs=1 skip=64 count=36 2>/dev/null | tr -d '\0')
    echo "== Nitro-Fueled $what package: $content"
    [[ "$content" == *"$TESTED_TITLE"* ]] || echo "WARNING: only $TESTED_TITLE (USA) is tested; other releases may lay their levels out differently"
    build_tool
    free=$(df -Pk "$ROOT" | awk 'NR == 2 { print int($4 / 1048576) }')
    (( free >= 40 )) || echo "WARNING: ${free} GB free; the unpacked packages and the converted levels take about 50 GB"
    echo "== unpacking (several minutes)"
    rm -rf "$out"
    mkdir -p "$out"
    DOTNET_SYSTEM_GLOBALIZATION_INVARIANT=1 dotnet "$TOOL/bin/PkgTool.Core.dll" pkg_extract \
        --passcode 00000000000000000000000000000000 "$pkg" "$out"
    n=$(ls "$out/uroot/archives" 2>/dev/null | wc -l)
    (( n > 0 )) || { echo "ERROR: no level archives unpacked (an encrypted package? it must be an unencrypted backup)" >&2; exit 1; }
    echo "archives: $n"
    echo "$id" > "$stamp"
}

[[ -n "$GAME" ]] || { echo "ERROR: an update package alone: the game package is needed too" >&2; exit 1; }
unpack "$GAME" "$OUT" game
if [[ -n "$UPDATE" ]]; then
    unpack "$UPDATE" "$OUT_UPDATE" update
fi
