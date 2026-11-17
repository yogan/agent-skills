#!/usr/bin/env bash
# Copy a reply draft to the macOS clipboard as plain text, for manual pasting
# into a GitLab thread. Reads a file argument, or stdin when given "-"/nothing.
#   clip.sh reply.md
#   echo "..." | clip.sh
set -euo pipefail
# Without pbcopy (not macOS) the copy is skipped, as the skill's docs promise: the draft
# is in the chat, and posting does not depend on it.
copy() { if command -v pbcopy >/dev/null; then pbcopy; else cat >/dev/null; echo "clip.sh: no pbcopy, copy skipped" >&2; fi; }
if [ "$#" -ge 1 ] && [ "$1" != "-" ]; then
  copy <"$1"
else
  copy
fi
