#!/usr/bin/env bash
# Copy a reply draft to the macOS clipboard as plain text, for manual pasting
# into a GitLab thread. Reads a file argument, or stdin when given "-"/nothing.
#   clip.sh reply.md
#   echo "..." | clip.sh
set -euo pipefail
if [ "$#" -ge 1 ] && [ "$1" != "-" ]; then
  pbcopy <"$1"
else
  pbcopy
fi
