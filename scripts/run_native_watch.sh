#!/usr/bin/env bash
# Play the native build under gdb with a hardware watchpoint on the Time Trial
# ghost tapes: whatever overwrites one (a crash seen on NF Crash Cove) is
# stopped in the act and its backtrace written to native/build/ghost_watch.log.
# Plays normally otherwise (hardware watchpoints cost nothing).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/native/build"
cat > /tmp/ctr_ghost_watch.py <<'PY'
import gdb
gdb.execute("set pagination off")
gdb.execute("set debuginfod enabled off")
gdb.execute("handle SIGPIPE SIGUSR1 SIGUSR2 nostop noprint pass")
LOG = open("ghost_watch.log", "a")

class Hit(gdb.Breakpoint):
    def stop(self):
        LOG.write("\n==== ghost tape overwritten ====\n")
        LOG.write(gdb.execute("bt 16", to_string=True))
        LOG.write(gdb.execute("info registers eip", to_string=True))
        LOG.flush()
        return True

class Init2(gdb.Breakpoint):
    watches = []
    def stop(self):
        for w in Init2.watches:
            try:
                w.delete()
            except Exception:
                pass
        Init2.watches = []
        for i in range(2):
            if int(gdb.parse_and_eval("sdata->ptrGhostTape[%d]" % i)) == 0:
                continue
            for field in ("constDEADC0ED", "ptrStart"):  # (written once each, at the race's start: any later write is the culprit)
                addr = int(gdb.parse_and_eval("&sdata->ptrGhostTape[%d]->%s" % (i, field)))
                Init2.watches.append(Hit("*(unsigned int *)%d" % addr, type=gdb.BP_WATCHPOINT, internal=True))
        return False

Init2("GhostReplay_Init2", internal=True)
gdb.execute("run")
PY
exec gdb -q -x /tmp/ctr_ghost_watch.py ./ctr_native
