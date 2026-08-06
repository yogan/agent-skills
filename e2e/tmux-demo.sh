#!/bin/sh
# Talk-day session builder: verify the rig is in the demo state, repair it if not,
# then create the three-window tmux session — one claude per demo segment.
#
#   ./tmux-demo.sh           verify, run fixture.py if needed, build the session
#   ./tmux-demo.sh --check   verify only — no fixture, no tmux, no changes
#   ./tmux-demo.sh --force   run fixture.py even if the state already verifies
#
# A non-zero exit means do not go on stage yet. Run of show: RUNBOOK.md.

set -u

SESSION=agentic-review-skills-demo
E2E=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REVIEW="$HOME/src/agent-skills-demo-review"
WORK="$HOME/src/agent-skills-demo"
SKILLS="$HOME/.claude/skills"
SLUG=demo-bulletproof-react       # project slug the skills derive their state dir from
URL=http://gitlab.test/users/sign_in
BOOT_TIMEOUT=300        # a cold GitLab boot is 3-5 min

CHECK_ONLY=0
FORCE=0
for a in "$@"; do
  case $a in
    --check) CHECK_ONLY=1 ;;
    --force) FORCE=1 ;;
    -h|--help) sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $a (try --help)" >&2; exit 2 ;;
  esac
done

say()  { printf '\n== %s\n' "$1"; }
ok()   { printf '   ok    %s\n' "$1"; }
warn() { printf '   ..    %s\n' "$1"; }
die()  { printf '   FAIL  %s\n' "$1" >&2; exit 1; }

# ── 1. container ─────────────────────────────────────────────────────────────
say "GitLab container"
if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx e2e-gitlab; then
  ok "e2e-gitlab is up"
else
  [ "$CHECK_ONLY" = 1 ] && die "e2e-gitlab is not running (--check makes no changes)"
  warn "e2e-gitlab is not running — starting it, cold boot is 3-5 min"
  (cd "$E2E" && docker compose up -d) || die "docker compose up failed"
fi

# ── 2. reachable ─────────────────────────────────────────────────────────────
# /-/readiness is allowlisted to 127.0.0.1 and always 404s from the host, so probe
# the sign-in page instead.
say "GitLab reachable"
waited=0
while :; do
  code=$(curl -sS -o /dev/null -w '%{http_code}' "$URL" 2>/dev/null) || code=000
  if [ "$code" = 200 ]; then ok "$URL -> 200"; break; fi
  [ "$waited" -ge "$BOOT_TIMEOUT" ] && die "$URL still $code after ${waited}s — see README 'Troubleshooting'"
  [ $((waited % 30)) = 0 ] && warn "$URL -> $code, waiting (${waited}s)"
  sleep 5
  waited=$((waited + 5))
done

# ── 3. fixture state ─────────────────────────────────────────────────────────
# Every assertion runs the skills' own CLI — the same code the demo runs — so this
# cannot pass while the demo would fail. Expected state is documented in RUNBOOK.md.
state_ok() {
  [ -d "$REVIEW" ] || { warn "missing review worktree $REVIEW"; return 1; }
  [ -d "$WORK" ]   || { warn "missing working copy $WORK"; return 1; }

  # glab resolves its host from the repo's remote, so this MUST run inside a demo
  # checkout. From ~/src/agent-skills (a GitHub remote) it returns 401.
  user=$(cd "$WORK" && glab api user 2>/dev/null | sed -n 's/.*"username":"\([^"]*\)".*/\1/p')
  [ "$user" = frank ] || { warn "glab identity is '${user:-none}', want frank"; return 1; }

  # MR !1 must be PRISTINE — it is reviewed for the first time live, and any leftover
  # findings.json makes the run resume mid-review instead of seeding from scratch. This
  # state survives both a project delete and a re-clone, so nothing else catches it.
  if [ -e "$HOME/.claude/review-mr/$SLUG--mr1/findings.json" ]; then
    warn "MR !1 already has local review state — a live run would resume mid-review"
    return 1
  fi

  mr2=$(cd "$REVIEW" && python3 "$SKILLS/review-mr/scripts/findings.py" sync --iid 2 2>&1)
  for pat in 'drafts in en' '3 need your ack' '2 awaiting author' '2 new version'; do
    printf '%s\n' "$mr2" | grep -q "$pat" || { warn "MR !2 sync lacks '$pat'"; return 1; }
  done

  mr3=$(cd "$WORK" && python3 "$SKILLS/rework-mr/scripts/threads.py" sync --iid 3 2>&1)
  printf '%s\n' "$mr3" | grep -q '0 of 3 topics done' || { warn "MR !3 is not three fresh open topics"; return 1; }
  return 0
}

say "Fixture state"
fixture_ran=0
need_fixture=0
if [ "$FORCE" = 1 ]; then
  warn "--force given, rebuilding regardless"
  need_fixture=1
elif state_ok; then
  ok "MR !2 parked mid-review (3 need ack, 2 pushes), MR !3 three open topics"
else
  need_fixture=1
fi

if [ "$need_fixture" = 1 ]; then
  [ "$CHECK_ONLY" = 1 ] && die "not in demo state (--check makes no changes) — run without --check"
  say "Rebuilding the fixture (~20 s)"
  (cd "$E2E" && python3 fixture.py) || die "fixture.py failed — read its output above"
  state_ok || die "fixture.py ran but the state still does not verify"
  fixture_ran=1
  ok "fixture rebuilt and verified"
fi

if [ "$CHECK_ONLY" = 1 ]; then
  say "Rig verified. No tmux session built (--check)."
  exit 0
fi

# ── 4. tmux ──────────────────────────────────────────────────────────────────
say "tmux session '$SESSION'"
if tmux has-session -t "$SESSION" 2>/dev/null; then
  ok "already exists — left untouched"
  if [ "$fixture_ran" = 1 ]; then
    warn "BUT fixture.py just re-cloned the checkouts and wiped local skill state:"
    warn "its panes may sit on deleted directories and any claude already mid-review"
    warn "is stale. Rebuild it:"
    warn "  tmux kill-session -t $SESSION && $0"
  fi
  printf '\n   attach:  tmux attach -t %s\n' "$SESSION"
  exit 0
fi

# Windows are addressed by name, so a non-zero base-index in .tmux.conf is fine.
tmux new-session -d -s "$SESSION" -n review   -c "$REVIEW" || die "tmux new-session failed"
tmux new-window  -t "$SESSION"    -n rereview -c "$REVIEW"
tmux new-window  -t "$SESSION"    -n rework   -c "$WORK"

# One line per window, naming the segment — nothing else. This is on a projector: beats
# and prompts belong in the presenter pane and RUNBOOK.md, not on the demo screen.
tmux send-keys -t "$SESSION:review" 'clear; echo ""; echo "  /review-mr !1   —   first pass (standalone mock server)"; echo ""' C-m

tmux send-keys -t "$SESSION:rereview" 'clear; echo ""; echo "  /review-mr !2   —   re-review (a review I started last week)"; echo ""' C-m

tmux send-keys -t "$SESSION:rework" 'clear; echo ""; echo "  /rework-mr !3   —   author hat (query-key factories)"; echo ""' C-m

# `claude` is typed but deliberately NOT submitted: warm the processes by pressing
# Enter yourself, and never let a skill run before the show.
for w in review rereview rework; do
  tmux send-keys -t "$SESSION:$w" 'claude'
done

tmux select-window -t "$SESSION:review"

ok "three windows created, 'claude' typed but not submitted in each"
tmux list-windows -t "$SESSION" -F '         #{window_index} #{window_name}  #{pane_current_path}'
printf '\n   attach:  tmux attach -t %s\n   then:    press Enter in each window to warm claude\n' "$SESSION"
