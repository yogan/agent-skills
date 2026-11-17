#!/usr/bin/env bash
# Copy a reply draft to the macOS clipboard as plain text, for manual pasting
# into a GitLab thread. Reads a file argument, or stdin when given "-"/nothing.
#   clip.sh reply.md
#   echo "..." | clip.sh
# Refuses (via guard-reply.sh) any body carrying an internal topic handle
# (t5, t6, …) — those must never reach GitLab.
set -euo pipefail
SD="$(cd "$(dirname "$0")" && pwd)"
# Without pbcopy (not macOS) the copy is skipped, as the skill's docs promise: the draft
# is in the chat, and posting does not depend on it.
copy() { if command -v pbcopy >/dev/null; then pbcopy; else cat >/dev/null; echo "clip.sh: no pbcopy, copy skipped" >&2; fi; }
if [ "$#" -ge 1 ] && [ "$1" != "-" ]; then
  "$SD/guard-reply.sh" - <"$1"    # aborts if a topic handle is present
  copy <"$1"
else
  # stdin can only be read once — buffer it so guard + pbcopy both see the
  # exact same bytes (a shell $(...) capture would strip trailing newlines).
  tmp="$(mktemp)"
  trap 'rm -f "$tmp"' EXIT
  cat >"$tmp"
  "$SD/guard-reply.sh" - <"$tmp"
  copy <"$tmp"
fi
