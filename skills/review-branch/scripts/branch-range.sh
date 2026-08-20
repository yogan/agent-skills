#!/usr/bin/env bash
# Resolve the review range for the current branch, handling detached HEAD.
# Prints shell-sourceable variables: BASE, HEAD_SHA, MAIN_REF, COMMIT_COUNT, BRANCH_NAME
#
# Caller must say which base to use — one of:
#   REVIEW_BRANCH_BASE=<sha>      use this verbatim (e.g. review-mr, seeding from a
#                                  specific GitLab MR's own diff_refs.base_sha)
#   REVIEW_BRANCH_STANDALONE=1    derive it locally via `git merge-base HEAD
#                                  origin/main`/`origin/master` (plain /review-branch,
#                                  no MR context to ask)
# Neither set is a caller bug, not a "just guess" situation: silently falling back to
# the local heuristic is exactly what let an MR review seed itself from the wrong base
# once already — the heuristic assumes the target is always main/master and that the
# locally known tip of it is fresh, neither of which always holds, and a caller that
# forgot to set REVIEW_BRANCH_BASE deserves a loud error, not a plausible-looking wrong
# diff.

set -euo pipefail

HEAD_SHA=$(git rev-parse HEAD)

if [ -n "${REVIEW_BRANCH_BASE:-}" ]; then
  MAIN_REF="(given: REVIEW_BRANCH_BASE)"
  BASE="$REVIEW_BRANCH_BASE"
elif [ -n "${REVIEW_BRANCH_STANDALONE:-}" ]; then
  # Find the remote default branch (origin/main or origin/master)
  MAIN_REF=$(git branch -r 2>/dev/null | grep -E '^\s*origin/(main|master)$' | head -1 | xargs)
  if [ -z "$MAIN_REF" ]; then
    echo "ERROR: Cannot find origin/main or origin/master. Are you in a git repo with a remote?" >&2
    exit 1
  fi
  BASE=$(git merge-base HEAD "$MAIN_REF")
else
  echo "ERROR: neither REVIEW_BRANCH_BASE nor REVIEW_BRANCH_STANDALONE is set — refusing" \
       "to silently guess the review base. Set REVIEW_BRANCH_BASE=<sha> if you already" \
       "know the authoritative base (e.g. an MR's own base commit), or" \
       "REVIEW_BRANCH_STANDALONE=1 for a plain local-branch review." >&2
  exit 1
fi

COMMIT_COUNT=$(git log --oneline "${BASE}..HEAD" | wc -l | xargs)

# Branch name — works even in detached HEAD
BRANCH_NAME=$(git symbolic-ref --short HEAD 2>/dev/null || git describe --exact-match --tags HEAD 2>/dev/null || git log -1 --format='%D' HEAD | sed 's/.*origin\///;s/,.*//' | xargs || echo "detached@${HEAD_SHA:0:7}")

echo "MAIN_REF='$MAIN_REF'"
echo "BASE='$BASE'"
echo "HEAD_SHA='$HEAD_SHA'"
echo "COMMIT_COUNT='$COMMIT_COUNT'"
echo "BRANCH_NAME='$BRANCH_NAME'"
