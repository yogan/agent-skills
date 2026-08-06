#!/usr/bin/env bash
# Render the current topic's working-tree diff as one fixed block, so the Stop
# hook can enforce it actually reaching the user before the fixup+push ACK —
# the same tool-output-collapses-and-gets-dropped bug present/quote/reply-view/
# change-preview already guard against also hit this step: the diff gets shown,
# then a `git blame` call (to name the fixup target) happens before the message
# is written, and that intervening call pushes the diff out of mind — the user
# ends up ACKing a fixup+push blind.
#   diff-view.sh <t> [-- <git-diff-args...>]
#
# git runs here (the `--` passthrough belongs in the shell); the block is rendered
# by `threads.py diff-view` (stateless), which also widens the fence when the diff
# touches a file that itself contains ``` — otherwise those lines close the block
# and the ACK question ends up inside the diff.
set -euo pipefail
t="${1:?usage: diff-view.sh <t> [-- git-diff-args...]}"
shift
if [ "${1:-}" = "--" ]; then shift; fi
SD="$(cd "$(dirname "$0")" && pwd)"
git diff "$@" | python3 "$SD/threads.py" diff-view "$t"
