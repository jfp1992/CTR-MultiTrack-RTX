"""Toolchain hook (see plugins/plugin.py): append custom tracks to BIGFILE.BIG."""
import os
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "..", "..", "..", ".."))
sys.exit(subprocess.call([sys.executable, "-I", os.path.join(ROOT, "scripts", "pack_tracks.py"), sys.argv[1]]))
