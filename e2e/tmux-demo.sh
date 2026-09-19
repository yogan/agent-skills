#!/bin/sh
# Talk-day session builder: verify the rig is in the demo state, repair it if not, then
# create the three-window tmux session — one claude per demo segment, already running,
# each with its opening line typed and waiting on your Enter.
#
#   ./tmux-demo.sh           verify, run fixture.py if needed, build the session
#   ./tmux-demo.sh --check   verify only — no fixture, no tmux, no changes
#   ./tmux-demo.sh --force   run fixture.py even if the state already verifies
#
# A non-zero exit means do not go on stage yet. Run of show: RUNBOOK.md.

set -u

SESSION=agentic-review-skills-demo
E2E=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
# One review worktree per MR, as the skill records them and as a real review uses them.
# Sharing one between !1 and !2 is what once let a run review the other MR's code.
REVIEW1="$HOME/src/agent-skills-demo-review-mr1"
REVIEW2="$HOME/src/agent-skills-demo-review-mr2"
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
    -h|--help) sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
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
#
# On this machine, port 80 is sometimes owned by Rancher Desktop's local Kubernetes
# Traefik (a LoadBalancer service that predates the container and wins the host's
# port forwarding) instead of e2e-gitlab. That looks identical to a slow boot —
# curl just keeps getting a non-200 — except it will NEVER turn into a 200, so
# without this check the loop burns the full timeout to say nothing more than
# "still 404". Traefik's default backend has a distinctive, un-GitLab-like shape
# (bare text body, text/plain), and only means this if the container itself is
# already healthy — during a real cold boot the same body could just be nginx not
# up yet.
other_lb_hijacked_port_80() {
  docker inspect -f '{{.State.Health.Status}}' e2e-gitlab 2>/dev/null | grep -qx healthy || return 1
  body=$(curl -sS "$URL" 2>/dev/null)
  [ "$body" = "404 page not found" ] || return 1
  ctype=$(curl -sS -o /dev/null -D - "$URL" 2>/dev/null | tr -d '\r' | sed -n 's/^[Cc]ontent-[Tt]ype: //p')
  case "$ctype" in text/plain*) return 0 ;; *) return 1 ;; esac
}

say "GitLab reachable"
waited=0
while :; do
  code=$(curl -sS -o /dev/null -w '%{http_code}' "$URL" 2>/dev/null) || code=000
  if [ "$code" = 200 ]; then ok "$URL -> 200"; break; fi
  if [ "$code" = 404 ] && other_lb_hijacked_port_80; then
    die "$URL -> 404, but e2e-gitlab is healthy — that 404 is Traefik's default backend, not GitLab's. Something else on the host owns port 80, most likely Rancher Desktop's local Kubernetes Traefik. Free it: kubectl --context rancher-desktop -n kube-system scale deployment traefik --replicas=0 (see README 'Troubleshooting')"
  fi
  [ "$waited" -ge "$BOOT_TIMEOUT" ] && die "$URL still $code after ${waited}s — see README 'Troubleshooting'"
  [ $((waited % 30)) = 0 ] && warn "$URL -> $code, waiting (${waited}s)"
  sleep 5
  waited=$((waited + 5))
done

# ── 3. fixture state ─────────────────────────────────────────────────────────
# Every assertion runs the skills' own CLI — the same code the demo runs — so this
# cannot pass while the demo would fail. Expected state is documented in RUNBOOK.md.
state_ok() {
  [ -d "$REVIEW1" ] || { warn "missing review worktree $REVIEW1"; return 1; }
  [ -d "$REVIEW2" ] || { warn "missing review worktree $REVIEW2"; return 1; }
  [ -d "$WORK" ]    || { warn "missing working copy $WORK"; return 1; }

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

  # …but its two PREPARED inputs must be there and must still apply. Both are recorded
  # against the tip they were made for, so a re-pushed branch silently invalidates them
  # — and the failure would land on stage as the very wait this is meant to remove:
  # minutes of explain-branch, then a live review-branch run. Assert here instead, by
  # asking the skill the same question the skill will ask.
  mr1ex=$(cd "$REVIEW1" && python3 "$SKILLS/review-mr/scripts/findings.py" \
          explainer --iid 1 2>/dev/null)
  [ -n "$mr1ex" ] && [ -e "$mr1ex" ] || {
    warn "MR !1 has no prepared explainer for its current tip"; return 1; }
  mr1seed=$(cd "$REVIEW1" && python3 "$SKILLS/review-mr/scripts/findings.py" \
            seed --iid 1 2>/dev/null)
  [ -n "$mr1seed" ] && [ -e "$mr1seed" ] || {
    warn "MR !1 has no prepared review-branch seed for its current tip"; return 1; }

  # Unregistered, the skill has to pick a checkout to review in, and that is the one
  # step it is allowed to stop and ask the user about.
  mr1wt=$(cd "$REVIEW1" && python3 "$SKILLS/review-mr/scripts/findings.py" \
          worktree --iid 1 2>/dev/null)
  [ -n "$mr1wt" ] && [ -d "$mr1wt" ] || {
    warn "MR !1 has no review worktree registered"; return 1; }

  mr2=$(cd "$REVIEW2" && python3 "$SKILLS/review-mr/scripts/findings.py" sync --iid 2 2>&1)
  for pat in 'drafts en' '3 need your ack' '2 awaiting author' '2 new version'; do
    printf '%s\n' "$mr2" | grep -q "$pat" || { warn "MR !2 sync lacks '$pat'"; return 1; }
  done
  # An untitled topic makes the skill refuse to show the table until it authors one —
  # right at the opener, which is the first thing on the projector.
  if printf '%s\n' "$mr2" | grep -q 'needs summary'; then
    warn "MR !2 has a topic with no authored summary — the opener would stall on it"
    return 1
  fi

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
  ok "MR !1 pristine with both prepared inputs, MR !2 parked mid-review (3 need ack, 2 pushes), MR !3 three open topics"
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
tmux new-session -d -s "$SESSION" -n review   -c "$REVIEW1" || die "tmux new-session failed"
tmux new-window  -t "$SESSION"    -n rereview -c "$REVIEW2"
tmux new-window  -t "$SESSION"    -n rework   -c "$WORK"

# One line per window, naming the segment — nothing else. This is on a projector: beats
# and prompts belong in the presenter pane and RUNBOOK.md, not on the demo screen.
tmux send-keys -t "$SESSION:review" 'clear; echo ""; echo "  /review-mr !1   —   first pass (standalone mock server)"; echo ""' C-m

tmux send-keys -t "$SESSION:rereview" 'clear; echo ""; echo "  /review-mr !2   —   re-review (a review I started last week)"; echo ""' C-m

tmux send-keys -t "$SESSION:rework" 'clear; echo ""; echo "  /rework-mr !3   —   author hat (query-key factories)"; echo ""' C-m

# Model and effort are per-session flags, so the demo runs small and fast without
# touching the settings real work uses. A talk is not the place to pay for deep
# reasoning: every expensive judgement call has been lifted out of the segments —
# MR !1's explainer and seed are prepared, !2 and !3 are seeded — so what is left is
# the skills driving their own CLIs, which is what the audience is here to see.
CLAUDE_ARGS="--model sonnet --effort low"

# !3 gets more, because it is the only segment that still does generative work: it
# reasons out the cache-key fix, writes it, and walks a longer chain of gated steps
# than the review segments do. At low effort it ran `present` and then a second gated
# command before writing its message, which drops the first block — the Stop hook
# catches that and the retry is correct, but a hook error on a projector is not.
# Raising effort reduces the chance; it cannot remove it, and the guard is what makes
# the outcome safe either way.
REWORK_ARGS="--model sonnet --effort medium"

claude_args_for() {
  case "$1" in
    rework) printf '%s' "$REWORK_ARGS" ;;
    *)      printf '%s' "$CLAUDE_ARGS" ;;
  esac
}

# Pre-heat: claude is STARTED here (its cold start is dead air on stage), but the line
# below it is only typed, never submitted. Nothing a skill does may happen before the
# show — a segment that has already run is a segment the audience watches you scroll.
for w in review rereview rework; do
  tmux send-keys -t "$SESSION:$w" "claude $(claude_args_for "$w")" C-m
done

# Keystrokes sent at a TUI that is still booting land nowhere, and the failure is
# invisible: you arrive to an empty prompt and type the whole line yourself under the
# lights. So wait for each one's input box to actually be drawn rather than guessing a
# sleep — box-drawing characters appear only once claude is at its prompt.
#
# Before that box there is usually a "do you trust this folder?" question, because a
# reset re-clones both checkouts and claude has never seen the directory that results.
# It must be answered HERE, and this is the sharp edge the whole pre-heat removes: its
# default option is "No, exit", so the Enter you press on stage to start a segment would
# have quit claude instead. Second option = trust, then carry on waiting for the box.
wait_for_claude() {
  waited=0
  answered=0
  while [ "$waited" -lt 60 ]; do
    pane=$(tmux capture-pane -p -t "$SESSION:$1" 2>/dev/null)
    case "$pane" in
      *"trust this folder"*)
        if [ "$answered" = 0 ]; then
          tmux send-keys -t "$SESSION:$1" Down
          tmux send-keys -t "$SESSION:$1" C-m
          answered=1
        fi
        ;;
      *)
        case "$pane" in *"│"*) return 0 ;; esac
        ;;
    esac
    sleep 1
    waited=$((waited + 1))
  done
  return 1
}

# The bare command, in all three. MR !1 used to need a paragraph of instructions after
# it, and every one of them has since been answered by the rig instead: the explainer
# and the seed are prepared, and the review worktree is registered, so there is nothing
# left for the skill to decide or ask. Telling it to "stop after the overview" was in
# fact harmful — it dropped the first topic out of a pasted block that has to go in
# whole, and the Stop hook caught that on stage.
send_prompt() {
  wait_for_claude "$1" || { warn "claude in '$1' did not reach its prompt — type the line yourself"; return; }
  tmux send-keys -t "$SESSION:$1" "$2"
}

send_prompt review   '/review-mr !1'
send_prompt rereview '/review-mr !2'
send_prompt rework   '/rework-mr !3'

tmux select-window -t "$SESSION:review"

ok "three windows created, claude running (reviews: $CLAUDE_ARGS · rework: $REWORK_ARGS), each prompt typed but NOT submitted"
tmux list-windows -t "$SESSION" -F '         #{window_index} #{window_name}  #{pane_current_path}'
printf '\n   attach:  tmux attach -t %s\n   then:    nothing — press Enter in a window when its segment starts\n' "$SESSION"
