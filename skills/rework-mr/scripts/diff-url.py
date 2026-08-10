#!/usr/bin/env python3
"""Thin shim — see lib/diff_url.py for the actual implementation, shared verbatim
with review-mr's own copy of this file (there is no per-skill logic here at all)."""
import os
import sys

# Repo root, 4 levels up from skills/rework-mr/scripts/diff-url.py — needed so `lib/`,
# which lives outside this skill's own directory, is importable regardless of how this
# script is invoked (direct, or symlinked into ~/.claude/skills/).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.realpath(__file__)))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from lib.diff_url import main  # noqa: E402

if __name__ == "__main__":
    main()
