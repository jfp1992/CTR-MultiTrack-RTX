#!/usr/bin/env bash
# One-time setup: fetch the build toolchain and check host tools. Safe to rerun;
# anything already in place is left alone. Needs your own CTR (USA) disc image
# at rom-usa/ctr-usa.bin (single-track MODE2/2352 .bin).
#   scripts/setup.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TC="$ROOT/psx-modding-toolchain"
SDK="$TC/games/CTR"
ROM="$ROOT/rom-usa/ctr-usa.bin"

# pinned upstream versions this project is built and tested against
TOOLCHAIN_URL=https://github.com/mateusfavarin/psx-modding-toolchain.git
TOOLCHAIN_REV=9332a8d43943c513502e92a7b8b03d9950fff5bb
MODSDK_URL=https://github.com/CTR-tools/CTR-ModSDK.git
MODSDK_REV=e3f19686fb07d62847836ae77c733573a54d3534
VOLK_URL=https://github.com/zeux/volk.git
VOLK_REV=0dc3ce00bf98b9f0b6fe708ca0f7eb74e2830173
VKHEADERS_URL=https://github.com/KhronosGroup/Vulkan-Headers.git
VKHEADERS_REV=acbb9391c0a597272800cd3f4363661c00a27c77
XDELTA_URL=https://github.com/jmacd/xdelta.git
XDELTA_REV=5d936d2c30f78729375c3ae070248a05bef27bd2
GCC_URL=https://github.com/Lameguy64/PSn00bSDK/releases/download/v0.24/gcc-mipsel-none-elf-12.3.0-linux.zip
ROM_MD5=ab95bfca8a4bb3d90daa6519acf6e944

step() { echo "== $*"; }
fail() { echo "ERROR: $*" >&2; exit 1; }

# git clone <url> <rev> <dir>, unless <dir> already exists
fetch() {
    [[ -e "$3" ]] && return
    step "fetching $(basename "$1" .git) @ ${2:0:9}"
    git init -q "$3"
    git -C "$3" remote add origin "$1"
    git -C "$3" fetch -q --depth 1 origin "$2"
    git -C "$3" checkout -q FETCH_HEAD
}

step "host tools"
missing=()
for t in git curl unzip rsync cmake make gcc glslc wine 7z python3; do
    command -v "$t" >/dev/null || missing+=("$t")
done
(( ${#missing[@]} )) && fail "missing: ${missing[*]} (glslc = shaderc, 7z = p7zip, wine needs wine-mono for ctr-tools)"
echo 'int main(void){return 0;}' | gcc -m32 -x c - -o /dev/null 2>/dev/null \
    || fail "gcc -m32 does not link: install 32-bit libc/gcc multilib (e.g. lib32-glibc + gcc-multilib)"

step "disc image"
[[ -f "$ROM" ]] || fail "put your Crash Team Racing (USA) disc image at rom-usa/ctr-usa.bin"
md5=$(md5sum "$ROM" | cut -d' ' -f1)
[[ "$md5" == "$ROM_MD5" ]] || echo "WARNING: rom-usa/ctr-usa.bin md5 $md5, expected $ROM_MD5 (other dumps/regions are untested)"

# optional: Nitro-Fueled PS4 package for the "NF" tracks (scripts/nf_setup.sh unpacks it)
step "Nitro-Fueled package (optional)"
shopt -s nullglob
pkgs=("$ROOT"/rom-ps4/*.pkg)
shopt -u nullglob
if (( ${#pkgs[@]} == 0 )); then
    echo "none in rom-ps4/: no NF tracks"
else
    (( ${#pkgs[@]} == 1 )) || fail "more than one .pkg in rom-ps4/: keep one"
    echo "$(basename "${pkgs[0]}")"
    command -v dotnet >/dev/null && dotnet --list-sdks 2>/dev/null | grep -qE '^([89]|[1-9][0-9])\.' \
        || fail "the PS4 package needs the .NET 8 (or newer) SDK to unpack (e.g. package dotnet-sdk)"
fi

fetch "$TOOLCHAIN_URL" "$TOOLCHAIN_REV" "$TC"
if [[ ! -e "$SDK" ]]; then
    fetch "$MODSDK_URL" "$MODSDK_REV" "$SDK"
    git -C "$SDK" apply "$ROOT/patches/CTR-ModSDK-plugin.patch"   # wine runner + bigfile_post_build.py hook
fi
mkdir -p "$SDK/build" "$SDK/mods/MultiTrack"
ln -sfn ../../../../rom-usa/ctr-usa.bin "$SDK/build/ctr-u.bin"   # the mod-builder extracts it on first build

if [[ ! -x "$ROOT/tools/gcc-mipsel/bin/mipsel-none-elf-gcc" ]]; then
    step "MIPS gcc (PSn00bSDK)"
    tmp=$(mktemp -d)
    curl -fL# -o "$tmp/gcc.zip" "$GCC_URL"
    unzip -q "$tmp/gcc.zip" -d "$tmp/gcc"
    top=$(dirname "$(dirname "$(find "$tmp/gcc" -path '*/bin/mipsel-none-elf-gcc' | head -1)")")
    rm -rf "$ROOT/tools/gcc-mipsel"
    mkdir -p "$ROOT/tools"
    mv "$top" "$ROOT/tools/gcc-mipsel"
    rm -rf "$tmp"
fi

# xdelta3 with LZMA (some track patches need it; pyxdelta is the fallback)
if [[ ! -x "$ROOT/tools/xdelta3" ]] && ! command -v xdelta3 >/dev/null; then
    step "xdelta3"
    tmp=$(mktemp -d)
    fetch "$XDELTA_URL" "$XDELTA_REV" "$tmp/xdelta"
    cmake -S "$tmp/xdelta/xdelta3" -B "$tmp/build" -DCMAKE_BUILD_TYPE=Release >/dev/null
    cmake --build "$tmp/build" -j"$(nproc)" --target xdelta3 >/dev/null
    cp "$(find "$tmp/build" -name xdelta3 -type f -perm -u+x | head -1)" "$ROOT/tools/xdelta3"
    rm -rf "$tmp"
fi

fetch "$VOLK_URL" "$VOLK_REV" "$ROOT/native/externals/volk"
fetch "$VKHEADERS_URL" "$VKHEADERS_REV" "$ROOT/native/externals/Vulkan-Headers"

if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
    step "python venv"
    python3 -m venv "$ROOT/.venv"
    "$ROOT/.venv/bin/pip" install -q -r "$ROOT/requirements.txt"
fi

mkdir -p "$ROOT/tracks_in" "$ROOT/rom-ps4"
step "done. Drop track patches into tracks_in/, then run scripts/build_native.sh (or scripts/build_all.sh: both steps)"
