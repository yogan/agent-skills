# E2E / demo rig

A local GitLab CE with a seeded project, three MRs and pre-seeded review conversations, for
demoing and end-to-end testing `review-mr` and `rework-mr` without touching a real instance.

Talk-day checklist and run of show: [RUNBOOK.md](RUNBOOK.md).

## Layout

| File | Purpose |
|---|---|
| `docker-compose.yml` | GitLab CE (arm64-native), `http://gitlab.test`, no CI runner |
| `bootstrap.py` | one-time: users, deterministic tokens, group, project, `glab` auth |
| `fixture.py` | resettable: branches, the three MRs, seeded threads, skill state |
| `tmux-demo.sh` | talk day: verify the rig, reset it only if needed, build the demo tmux session |
| `drive.py` | drives a real claude in tmux through a scenario: `verify` checks it, `showcase` shows it |
| `scenarios.py` | the scenarios `drive.py` runs |
| `mock_model.py` | the model's stand-in: replays a recorded run's replies to a real claude, and cuts recordings from transcripts |
| `recordings/` | one recording per scenario, played by default; made from a passing real-model run (`--record`) |
| `screenshot.py` | a colour capture (`.ansi`) as a PNG drawn as Ghostty draws it, the whole screen or the sidebar alone |
| `transcript.py` | what a session ran, from its transcript: prompts, replies, calls, results, engine notes; a failed verify run keeps it as `transcript.txt` |
| `news.py` | MR !1 changes while its review waits: `reply`, `push`, `both` or `fix`, as its author (the review-ui mod's inbox); MR !3 as a whole: `approve` (its reviewer), `merge` |
| `.runs/` | `drive.py verify` captures, one folder per run (gitignored) |
| `patches/` | the frozen MR diffs (`mr1-flaws.patch`, `mr2/`, `mr3/`) |
| `artifacts/` | MR !1's frozen explainer and review-branch seed — tracked, so the live first pass generates neither |
| `.env.local` | generated token/host values (gitignored) |
| `.cache/` | bare upstream mirror + throwaway build tree (gitignored) |
| `backup/` | superseded by `artifacts/` — still gitignored so an old local copy stays invisible |

## One-time setup

1. **hosts entry** (needs sudo, so do it yourself):

   ```sh
   sudo sh -c 'echo "127.0.0.1 gitlab.test" >> /etc/hosts'
   ```

2. **start GitLab** — first boot is 3-5 min:

   ```sh
   cd e2e && docker compose up -d
   ```

3. **bootstrap** — waits for readiness, then creates everything:

   ```sh
   python3 bootstrap.py
   ```

   Idempotent: safe to re-run — it reuses any token in `.env.local` that still
   authenticates. The root token is deterministic (minted via `gitlab-rails`); the user
   tokens are minted through the admin API, which returns a value only once, so they are
   persisted to `.env.local` rather than re-derived.

### Accounts

| User | Role | Login |
|---|---|---|
| `root` | admin — bootstrap only | `root` / `Rk8xQ2-mT9zV4-pB6nL1` |
| `frank` | **the live identity** — both skills, browser, `glab` | `frank` / `Ud5jH3-wN7cY2-qF4tM8` |
| `author-bot` | authors MR !1, !2; replies in !2's threads | fixture only |
| `reviewer-bot` | posts the review threads on MR !3 | fixture only |
| `peer-bot` | one extra thread on MR !2 | fixture only |

Display names use the enterprise `Last, First - ID` shape on purpose — `lib/mr_common.py`'s
`first_name()` parses exactly that, so the fixture exercises the real path.

## Reset before every run

```sh
python3 fixture.py          # ~22 s, idempotent, offline
```

Deletes and recreates the *project* (that is what restarts MR IIDs at 1), re-pushes branches,
recreates the MRs, seeds threads and the local review state, re-clones the working copy at
`~/src/agent-skills-demo` (plus `-review-mr1` and `-review-mr2` beside it — one review
worktree per reviewed MR, as the skill records them), wipes skill state under
`~/.claude/review-mr/` and `~/.claude/rework-mr/`, and finally registers MR !1's two prepared
inputs (below).

### MR !1 generates nothing live

Its first pass used to spend minutes on two generative steps — `explain-branch` in a subagent
and a `review-branch` seed — and neither came out the same way twice. Both are frozen in
`artifacts/` and tracked, and the last fixture stage registers them with the skill:

```sh
python3 fixture.py --only prepared     # re-register after editing an artifact
```

`/review-mr` asks for a prepared explainer and a prepared seed before generating anything, so
the live run looks them up instead: it pastes a clickable `file://` link to the explainer and
imports five findings, straight into the overview table. Registration records MR !1's tip
alongside each path, and a recorded input whose tip has moved is refused rather than reused —
`tmux-demo.sh` asserts both still resolve, so that failure lands in the pre-flight check and
never on stage.

This deliberately leaves **no** `findings.json`: MR !1 still opens as a *fresh* review, the
import happens live, and only the inputs are prepared.

To re-cut the seed (five findings, one per file, spanning all four severities) or replace the
explainer, edit `artifacts/` and re-run the stage above. Both are plain files —
`seed-mr1.json` is exactly what `findings.py import` eats.

On talk day use `./tmux-demo.sh` instead — it runs this only if the state does not verify.

### What it builds

| MR | Author | Contents | Used by |
|---|---|---|---|
| `!1` | `author-bot` | upstream **PR #175** (standalone mock server), 2 real commits, 20 files, with 2 planted flaws alongside 2 genuine upstream ones. Targets `release/2024-06`, not `main`. Explainer and seed prepared, so nothing is generated live | `/review-mr`, live first pass |
| `!2` | `author-bot` | route-level prefetch, plus a seeded conversation: fixed-by-push, author-asks-a-question, promised-but-not-done, untouched, and one peer thread | `/review-mr`, re-review |
| `!3` | **you** | query-key factories in 2 commits, with 3 reviewer threads (trivial / hard / scope creep) | `/rework-mr` |

The MR !2 state is not hand-written. Threads are posted, recorded through `findings.py`'s own
CLI (`import` → `link`), the baseline is taken with `set-head` while the worktree still sits on
version 1, and only then are the author's two fixes pushed. Ordering is the mechanism, so no
SHA is hardcoded anywhere — and if a refactor breaks `import`/`link`/`set-head`, the reset
fails loudly. That is what makes this an E2E harness and not just a demo prop.

### Verify a reset

```sh
cd ~/src/agent-skills-demo-review-mr2
python3 ~/.claude/skills/review-mr/scripts/findings.py sync --iid 2
#   "drafts en", t1/t2/t3 ◐ needs-ack, t4 ○ open, t5 💬 peer, 2 pushes since baseline

cd ~/src/agent-skills-demo
python3 ~/.claude/skills/rework-mr/scripts/threads.py sync --iid 3
#   three ○ open topics
```

`glab api user` must run **inside a demo checkout** — glab resolves its host from the git
remote, so from `e2e/` (a GitHub remote) it returns `401`, not `frank`.

## Driving a real session

`drive.py` starts claude in tmux, sends it prompts and keys, and reads its screen. Two modes
run the same scenarios (`python3 drive.py list`):

```sh
python3 drive.py verify rework-3          # detached, on the recording; non-zero names the failed step
python3 drive.py verify smoke --keep      # leave the tmux session up to look at
python3 drive.py verify rework-3 --model sonnet --record   # real model (paid): re-record it
python3 drive.py showcase rework-3        # a window in your tmux, on the recording; Enter steps it
python3 drive.py showcase rework-3 --pace 4    # or unattended, 4 s between steps
python3 screenshot.py .runs/<run> --region panel   # every step's screen as a PNG, the sidebar alone
```

**The model is a recording by default.** Claude Code, the skills' scripts, the mod, the hooks
and GitLab all run for real; only the model's replies are played back from
`recordings/<scenario>.json` (`mock_model.py`). Such a run is free, comes out the same every
time, and can only do what the recording does: its tools are exactly the recorded ones, and
anything else is refused. It proves the parts work together, never that a model follows the
skills' wording. That is what `--model` is for: a real model, money and minutes, run with the
owner's go. It lists afterwards the commands it ran beyond the skills' own work
(`improvised:`), and `--record` keeps a passing run as the scenario's new recording. A
recording is re-made when a change breaks it: a prompt, a button's text or a script's output
the replay no longer matches fails the step with "the recording has no answer". It stores the
home and source folders as `{HOME}` and `{SRC}`, follows the ids a run makes (a comment's, a
commit's, a press's token) from each call's output to the calls after it, and may be trimmed by
hand as long as the tool calls stay.

**verify** runs at a fixed 160×50 and leaves your
own settings out, so your Stop hooks, skills, CLAUDE.md and permission rules play no part: the
repo's skills come back in as a plugin, and the scenario carries its own allow list. A
scenario marked `rig` resets the fixture first (~30 s), because the run before it wrote skill
state and posted to the merge request; `--no-reset` skips that. After every step the screen is
saved as text and with colours under `.runs/<time>-<scenario>/`, and a failed run adds what
the session ran (`transcript.txt`, from `transcript.py`) and, on the recording, what each
request was answered with (`mock.log`). `screenshot.py` turns those colour captures into PNGs
drawn as Ghostty draws them, so a change to the cards or the sidebar can be looked at without
running a session by hand.

**showcase** opens a window in the tmux session you are in (or `--session NAME`), with your
settings, on the recording, and waits for Enter before each step; the recording stops
answering when the driver exits. A scenario that ends in a handover runs on your own model,
since what is typed after it was never recorded, and leaves claude running with its line typed: `tmux-demo.sh` builds the talk-day windows
that way, from the `stage-*` scenarios.

A path to a `.py` file that defines `SCENARIOS` runs that file instead, for a one-off probe
that does not belong in the repo.

## Why it is built this way

Decisions that are expensive to rediscover, and that look like cruft until you know why:

- **`main` is pinned at `74c76128`** (2024-05-03, parent of `e74349ee`). The era matters: the
  upstream monorepo split lands at `1508d6d`, and everything before it is a flat `src/`.
- **`main` carries one commit of the demo's own** on top (`patches/main/`): a quick source
  check in plain node, the check `/rework-mr` runs on every fix. The project's real ones
  (eslint, tsc, vitest) need an install the demo never does. It checks something no MR
  touches, so it passes on every fix: a rule an MR breaks would have failed before review.
  MRs !2 and !3 build on it; MR !1 has a base of its own.
- **Only MR !1 is real upstream code**, replayed onto the PR's own base — conflict-free by
  construction, since that is what happened historically. MRs !2 and !3 are **authored** on the
  pinned base instead. Measured: 39 commits sit between the candidate bases, including a
  sweeping camelCase→kebab-case file rename, and cherry-picking any of the chosen upstream
  commits conflicted in 8-10 files each. Authoring them buys coherent trees, controlled diff
  size and guaranteed review substance — and it means a reset can never fail on a conflict.
- **MR !1 cannot target `main`.** PR #175 sits later on upstream master than the base, so a
  diff against `main` would drag in every unrelated change in between.
- **`yarn.lock` churn stays in the diff.** Every exclusion manufactures a false finding — drop
  the lockfile and the agent leads with "lockfile not updated"; drop `package.json` too and it
  becomes "`js-cookie` isn't declared".
- **Two bot identities minimum.** `findings.py` derives a thread's turn ("yours" vs "the
  author's") from author username vs `me`, so a self-reviewing single user collapses the turn
  model entirely.
- **MR !3 gets 2 commits, not 1** — `/rework-mr` does fixup + force-push, so there must be
  commits to squash *into*.
- **IID == demo step.** Creation order sets the IIDs, which is why a reset recreates the
  *project* rather than just the branches.
- **The planted flaws stay a tracked patch** (`patches/mr1-flaws.patch`) even though they are
  folded into the replayed commits, so the diff looks native on GitLab but is still reviewable
  when a flaw needs retuning.
- **`GITLAB_TOKEN` is never exported globally** — glab is host-agnostic, so a global token also
  hits the corporate instance. It lives in `.env.local` and the fixture script only.

## Troubleshooting

```sh
docker compose logs -f gitlab                    # boot progress
docker exec e2e-gitlab gitlab-ctl status
curl -sS -o /dev/null -w '%{http_code}\n' http://gitlab.test/users/sign_in   # want 200
docker exec e2e-gitlab gitlab-psql -t -c 'select username from users'
cd ~/src/agent-skills-demo && glab api user      # should print frank
```

### Gotchas hit while building this (all fixed in the scripts, kept as a record)

| Symptom | Cause |
|---|---|
| container crash-loops, `Reading unsupported config value grafana` | omnibus **rejects unknown keys** as a hard failure. `grafana`, `mattermost`, `gitlab_ci` were all removed in 16.x/17.x. Keep `GITLAB_OMNIBUS_CONFIG` conservative |
| `/-/readiness` always 404 | that endpoint is IP-allowlisted to `127.0.0.1`; from the host you arrive via the docker gateway. Probe `/users/sign_in` instead |
| **whole VM dies**, `Cannot connect to the Docker daemon` | `gitlab-rails runner` boots a second Rails app (~1.5 GB) on top of puma+sidekiq. In a 6 GB Rancher VM that OOM-kills the VM, not just the container. `bootstrap.py` stops puma+sidekiq around its single rails call |
| `users` table empty, `no root user` | the first-boot seed died in that same OOM, and `gitlab-ctl reconfigure` will **not** retry it (its cache says done). Force it: `docker exec -e GITLAB_ROOT_PASSWORD=… e2e-gitlab gitlab-rake db:seed_fu` |
| seed prints `Password must not contain commonly used combinations of words and letters` | GitLab 19 strength-checks passwords, and the admin seed then **silently** creates no user. Passwords here are deliberately opaque |
| `Validation failed: Namespace can't be blank` | creating users with `User.new(...).save!` in rails skips personal-namespace creation. Use the REST API, which goes through GitLab's own service |
| reset dies with `no topic ◈t1` | `fixture.py` parses `findings.py import`'s output for topic handles. Match `t\d+`, never split the decorated string |
| `/users/sign_in` stuck at a bare-text `404 page not found` even though `gitlab-ctl status` and the container healthcheck are fine | something else on the host owns port 80 and is winning the port forward ahead of `e2e-gitlab` — on this machine that was Rancher Desktop's local Kubernetes Traefik, a `LoadBalancer` service that predates the container. `tmux-demo.sh` detects this shape and fails fast instead of waiting out the full boot timeout |

### VM memory

The rig runs in a **12 GB / 4 CPU** Rancher Desktop VM with Kubernetes off:

    rdctl set --virtual-machine.memory-in-gb 12 --virtual-machine.number-cpus 4 --kubernetes.enabled=false

It restarts the VM and every container in it. At the old 6 GB / 2 CPU with Kubernetes on,
GitLab had a few hundred MB to spare, and a run of scenarios used that up: each reset was
slower than the one before, from about 30 s to over 5 minutes across the 14 scenarios. At
12 GB / 4 CPU six resets in a row each take about 30 s, with ~7 GB still free, and a cold
start answers in well under a minute.

