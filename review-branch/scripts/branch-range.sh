#!/usr/bin/env bash
# Resolve the review range for the current branch, handling detached HEAD.
# Prints shell-sourceable variables: BASE, HEAD_SHA, MAIN_REF, COMMIT_COUNT, BRANCH_NAME

set -euo pipefail

# Find the remote default branch (origin/main or origin/master)
MAIN_REF=$(git branch -r 2>/dev/null | grep -E '^\s*origin/(main|master)$' | head -1 | xargs)
if [ -z "$MAIN_REF" ]; then
  echo "ERROR: Cannot find origin/main or origin/master. Are you in a git repo with a remote?" >&2
  exit 1
fi

HEAD_SHA=$(git rev-parse HEAD)
BASE=$(git merge-base HEAD "$MAIN_REF")
COMMIT_COUNT=$(git log --oneline "${BASE}..HEAD" | wc -l | xargs)

# Branch name — works even in detached HEAD
BRANCH_NAME=$(git symbolic-ref --short HEAD 2>/dev/null || git describe --exact-match --tags HEAD 2>/dev/null || git log -1 --format='%D' HEAD | sed 's/.*origin\///;s/,.*//' | xargs || echo "detached@${HEAD_SHA:0:7}")

echo "MAIN_REF='$MAIN_REF'"
echo "BASE='$BASE'"
echo "HEAD_SHA='$HEAD_SHA'"
echo "COMMIT_COUNT='$COMMIT_COUNT'"
echo "BRANCH_NAME='$BRANCH_NAME'"
