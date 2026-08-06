#!/usr/bin/env bash
# Render a trivial topic's proposed-change illustration as one fixed block, so
# the Stop hook can enforce it actually reaching the user — the same
# tool-output-collapses-and-gets-dropped bug that `present`/`quote`/
# `reply-view` already guard against also hit this freeform step (the model
# says "Trivial. Change:" then never actually shows it).
#   change-preview.sh <t> <file> [--for <path>]
#     <file>  the change, verbatim (a diff, a before/after snippet, or prose
#             interleaved with its own fenced blocks) — NOT applied, illustration only.
#     --for   path the change applies to; only used to pick the fence language for a
#             snippet that isn't a diff (a diff is detected and coloured on its own).
#
# The rendering lives in `threads.py change-view` (stateless: no glab, no state
# file), because getting the fences right is the whole job here and it is not a
# thing to do twice in bash: the illustration usually carries its own ```diff
# block, so wrapping it in another fence — which this script used to do — closed
# the block early, dropped the highlighting, and spilled the rest as prose.
set -euo pipefail
t="${1:?usage: change-preview.sh <t> <file> [--for <path>]}"
f="${2:?usage: change-preview.sh <t> <file> [--for <path>]}"
shift 2
[ -f "$f" ] || { echo "error: no such file: $f" >&2; exit 1; }
SD="$(cd "$(dirname "$0")" && pwd)"
exec python3 "$SD/threads.py" change-view "$t" "$f" "$@"
