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
# The diff normally goes to a viewer window and the block here becomes a summary plus a
# pointer to it; `threads.py diff-view` falls back to an inline fenced diff on its own
# whenever that is not possible. Narrowing the diff with git arguments forces the inline
# shape: the viewer reloads from the working tree rather than from what is piped here, so
# a narrowed diff and the window would be showing two different things.
set -euo pipefail
t="${1:?usage: diff-view.sh <t> [--note FILE:LINE:TEXT]... [-- git-diff-args...]}"
shift
# Anything before a lone `--` is ours (notes for the viewer); anything after belongs to
# git. Collected rather than forwarded blindly so the two cannot be confused — and an
# unrecognised option is refused HERE, naming the separator. Swallowing it instead sent
# `diff-view.sh <t> --stat` into threads.py's argparse, which exits 2 complaining about a
# flag the caller never meant for it, under `pipefail`, with no block printed at all.
mine=()
while [ "$#" -gt 0 ] && [ "$1" != "--" ]; do
  case "$1" in
    --note)
      [ "$#" -ge 2 ] || { echo "diff-view.sh: --note needs FILE:LINE:TEXT" >&2; exit 2; }
      mine+=("$1" "$2"); shift 2 ;;
    --note=*) mine+=("--note" "${1#--note=}"); shift ;;
    *)
      echo "diff-view.sh: unknown option '$1'. Arguments for git go after a lone '--':" >&2
      echo "  diff-view.sh $t -- $*" >&2
      exit 2 ;;
  esac
done
if [ "${1:-}" = "--" ]; then shift; fi
SD="$(cd "$(dirname "$0")" && pwd)"
# `${a[@]+"${a[@]}"}` rather than a bare `"${a[@]}"`: expanding an EMPTY array under
# `set -u` aborts on bash 3.2, which is the bash macOS ships.
if [ "$#" -gt 0 ]; then
  git diff "$@" | python3 "$SD/threads.py" diff-view "$t" --plain ${mine[@]+"${mine[@]}"}
else
  git diff | python3 "$SD/threads.py" diff-view "$t" ${mine[@]+"${mine[@]}"}
fi
