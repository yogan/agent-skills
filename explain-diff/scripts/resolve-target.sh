#!/usr/bin/env bash
# Resolve an explain-diff target spec into a diff range.
# Usage: resolve-target.sh [spec]
#   spec="":              current branch since it diverged from origin/main|master
#   spec="branch:<name>":  a local or remote branch since it diverged from origin/main|master
#   spec="mr:<number>":     a GitLab MR's source branch since it diverged from its target branch (via glab)
#   spec="commit:<ref>":    a single commit's own diff (ref defaults to HEAD, i.e. "last commit")
#
# Prints shell-sourceable variables: BASE, HEAD_REF, LABEL, IS_SINGLE_COMMIT

set -euo pipefail

SPEC="${1:-}"
KIND="${SPEC%%:*}"
VALUE="${SPEC#*:}"
[ "$KIND" = "$SPEC" ] && VALUE=""

find_main_ref() {
  git branch -r 2>/dev/null | grep -E '^\s*origin/(main|master)$' | head -1 | xargs
}

case "$KIND" in
  "")
    MAIN_REF=$(find_main_ref)
    if [ -z "$MAIN_REF" ]; then
      echo "ERROR: Cannot find origin/main or origin/master. Are you in a git repo with a remote?" >&2
      exit 1
    fi
    HEAD_REF=$(git rev-parse HEAD)
    BASE=$(git merge-base HEAD "$MAIN_REF")
    BRANCH_NAME=$(git symbolic-ref --short HEAD 2>/dev/null || git describe --exact-match --tags HEAD 2>/dev/null || echo "detached@${HEAD_REF:0:7}")
    LABEL="$BRANCH_NAME"
    IS_SINGLE_COMMIT="false"
    ;;

  branch)
    if [ -z "$VALUE" ]; then
      echo "ERROR: branch spec requires a name, e.g. branch:feat/foo" >&2
      exit 1
    fi
    MAIN_REF=$(find_main_ref)
    if [ -z "$MAIN_REF" ]; then
      echo "ERROR: Cannot find origin/main or origin/master." >&2
      exit 1
    fi
    git fetch origin "$VALUE" --quiet 2>/dev/null || true
    if git show-ref --verify --quiet "refs/remotes/origin/$VALUE"; then
      HEAD_REF="origin/$VALUE"
    elif git show-ref --verify --quiet "refs/heads/$VALUE"; then
      HEAD_REF="$VALUE"
    else
      echo "ERROR: Branch '$VALUE' not found locally or on origin." >&2
      exit 1
    fi
    BASE=$(git merge-base "$HEAD_REF" "$MAIN_REF")
    LABEL="$VALUE"
    IS_SINGLE_COMMIT="false"
    ;;

  mr)
    NUM=$(echo "$VALUE" | tr -dc '0-9')
    if [ -z "$NUM" ]; then
      echo "ERROR: mr spec requires a number, e.g. mr:123" >&2
      exit 1
    fi
    if ! command -v glab >/dev/null 2>&1; then
      echo "ERROR: glab CLI not found. Install/auth glab to resolve MRs (see README)." >&2
      exit 1
    fi
    MR_JSON=$(glab mr view "$NUM" -F json 2>&1) || {
      echo "ERROR: glab could not fetch MR !$NUM: $MR_JSON" >&2
      exit 1
    }
    SRC=$(echo "$MR_JSON" | grep -o '"source_branch":"[^"]*"' | cut -d'"' -f4)
    TGT=$(echo "$MR_JSON" | grep -o '"target_branch":"[^"]*"' | cut -d'"' -f4)
    TITLE=$(echo "$MR_JSON" | grep -o '"title":"[^"]*"' | cut -d'"' -f4)
    if [ -z "$SRC" ] || [ -z "$TGT" ]; then
      echo "ERROR: Could not parse source/target branch for MR !$NUM." >&2
      exit 1
    fi
    git fetch origin "$SRC" "$TGT" --quiet 2>/dev/null || true
    HEAD_REF="origin/$SRC"
    BASE=$(git merge-base "$HEAD_REF" "origin/$TGT")
    LABEL="mr-${NUM}-${TITLE:-$SRC}"
    IS_SINGLE_COMMIT="false"
    ;;

  commit)
    REF="${VALUE:-HEAD}"
    if ! git rev-parse --verify --quiet "${REF}^{commit}" >/dev/null; then
      echo "ERROR: Commit '$REF' not found." >&2
      exit 1
    fi
    HEAD_REF=$(git rev-parse "$REF")
    if ! BASE=$(git rev-parse --verify --quiet "${REF}^"); then
      echo "ERROR: Commit '$REF' has no parent (nothing to diff against)." >&2
      exit 1
    fi
    LABEL="commit-${HEAD_REF:0:7}"
    IS_SINGLE_COMMIT="true"
    ;;

  *)
    echo "ERROR: Unknown spec kind '$KIND'. Use branch:, mr:, commit:, or leave blank for current branch." >&2
    exit 1
    ;;
esac

# Slugify label for safe filenames
LABEL=$(echo "$LABEL" | tr -c '[:alnum:]' '-' | tr -s '-' | sed 's/^-//;s/-$//')

echo "BASE='$BASE'"
echo "HEAD_REF='$HEAD_REF'"
echo "LABEL='$LABEL'"
echo "IS_SINGLE_COMMIT='$IS_SINGLE_COMMIT'"
