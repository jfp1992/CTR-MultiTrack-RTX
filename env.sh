# source this: puts MIPS gcc + python venv on PATH
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
export PATH="$ROOT/tools/gcc-mipsel/bin:$ROOT/.venv/bin:$PATH"
