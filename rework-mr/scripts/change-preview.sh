#!/usr/bin/env bash
# Render a trivial topic's proposed-change illustration as one fixed block, so
# the Stop hook can enforce it actually reaching the user — the same
# tool-output-collapses-and-gets-dropped bug that `present`/`quote`/
# `reply-view` already guard against also hit this freeform step (the model
# says "Trivial. Change:" then never actually shows it).
#   change-preview.sh <t> <file>     # <file>: the change, verbatim (e.g. a diff
#                                    # or a before/after snippet) — NOT applied,
#                                    # illustration only.
set -euo pipefail
t="${1:?usage: change-preview.sh <t> <file>}"
f="${2:?usage: change-preview.sh <t> <file>}"
[ -f "$f" ] || { echo "error: no such file: $f" >&2; exit 1; }
echo "**Change (${t}):**"
echo
echo '```'
cat "$f"
echo '```'
echo
echo "Agreed?"
