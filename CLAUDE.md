# CLAUDE.md — CTR MultiTrack RTX

## Token rules (top priority: minimum tokens)

- **User does all testing.** Never run the game, emulator, builds-to-verify, test suites, or screenshots unless explicitly asked. Make the change, say what to test, stop.
- No subagents, plan/explore/review agents, artifacts, docs, or skill loads unless user asks by name.
- Grep first, then read only the needed line ranges. Never read whole large files, ROM/binary data, or anything under `rom-usa/`, `rom-ps4/`, `tracks_in/`.
- Don't descend into `native/` (submodule) unless the task is in it.
- Pipe command output through `head`/`tail`/`grep`; use quiet flags.
- Don't re-read files already in context or just edited.
- Do only what was asked. No refactors, cleanups, extra tests, comments, docs, or suggestions. Exception: fixing a gap needed for the request to actually work.
- No clarifying questions when a sensible default exists — pick it, state it in one line.
- Replies: result + what user must do/test. No recaps, no option lists.
- Commit/push only when asked.

## Layout

- `scripts/`, `tools/` — build scripts, NF converter
- `mod/src/` — PS1 mod
- `patches/` — CTR-ModSDK + LibOrbisPkg patches
- `native/` — native fork (git submodule)
- `tracks.json`, `tracks_in/` — track config / inputs
- `env.sh` — environment setup
