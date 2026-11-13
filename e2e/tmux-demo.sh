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

# Its checks read the scripts' plain views. Run from a session the review-ui mod draws (a
# `! ./tmux-demo.sh` in Claude Code), they would render the mod's words instead (lib/ui.py).
export REVIEW_UI_DRAWN=0

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
# GitLab runs in a Colima VM of its own (rig.py): every docker call here goes there, whatever
# the default context is.
export DOCKER_CONTEXT="${E2E_DOCKER_CONTEXT:-colima-gitlab}"
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
# A rig left unused is paused, later stopped (rig.py): a paused one is unpaused here, even under
# --check, and the use is noted so it is not paused under the demo. Starting stays below.
python3 "$E2E/rig.py" up --no-wait --no-start
if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx e2e-gitlab; then
  ok "e2e-gitlab is up"
else
  [ "$CHECK_ONLY" = 1 ] && die "e2e-gitlab is not running (--check makes no changes)"
  warn "e2e-gitlab is not running — starting it (a cold boot, about a minute)"
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

# The windows are built by the driver's `stage-*` scenarios (scenarios.py): each starts claude
# in its checkout, answers the trust-this-folder question a fresh clone always raises (its
# default is "No, exit", so the Enter pressed on stage would quit claude), waits until the
# prompt box is drawn — keys sent at a TUI still booting land nowhere — and types the bare
# command, unsent. Nothing a skill does may happen before the show.
#
# Model and effort are per-session flags, so the demo runs small and fast without touching
# the settings real work uses. Every expensive judgement call has been lifted out of the
# segments (MR !1's explainer and seed are prepared, !2 and !3 are seeded), so what is left
# is the skills driving their own CLIs. The scenarios add the effort: low for the reviews,
# medium for !3, the one segment that still reasons out and writes a fix.
MODEL=sonnet
python3 "$E2E/drive.py" showcase stage-review stage-rereview stage-rework \
  --session "$SESSION" --model "$MODEL" --pace 0 || die "the driver could not build the windows"

tmux select-window -t "$SESSION:review"

ok "three windows created, claude running on $MODEL, each prompt typed but NOT submitted"
tmux list-windows -t "$SESSION" -F '         #{window_index} #{window_name}  #{pane_current_path}'
printf '\n   attach:  tmux attach -t %s\n   then:    nothing — press Enter in a window when its segment starts\n' "$SESSION"
