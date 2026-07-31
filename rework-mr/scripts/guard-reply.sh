#!/usr/bin/env bash
# Gate a reply body before it leaves for GitLab: refuse if it contains this
# skill's internal topic handles (t5, t6, t10, …). Those are the skill's own
# bookkeeping ids — meaningless to a GitLab reader and never wanted in a posted
# comment. Reference another discussion by its thread URL instead
# (`threads.py url <other-t>`). Reads a file argument, or stdin ("-"/nothing).
# Exits 0 if clean, 1 (with a message) if a handle is found — so clip.sh and the
# post step can hard-block on it, not merely be told not to.
#
# Delegates the actual match to threads.py's `check-handles` (HANDLE_RE) instead
# of reimplementing the regex here — one definition, not two that can drift.
set -euo pipefail
SD="$(cd "$(dirname "$0")" && pwd)"
if [ "$#" -ge 1 ] && [ "$1" != "-" ]; then
  hits="$(python3 "$SD/threads.py" check-handles <"$1")"
else
  hits="$(python3 "$SD/threads.py" check-handles)"
fi
if [ -n "$hits" ]; then
  echo "error: reply contains internal topic handle(s): ${hits}" >&2
  echo "       these mean nothing on GitLab — reword, or link the other thread" >&2
  echo "       (threads.py url <other-t>). Refusing to copy/post." >&2
  exit 1
fi
