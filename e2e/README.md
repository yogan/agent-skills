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
| `patches/` | the frozen MR diffs (`mr1-flaws.patch`, `mr2/`, `mr3/`) |
| `.env.local` | generated token/host values (gitignored) |
| `.cache/` | bare upstream mirror + throwaway build tree (gitignored) |
| `backup/` | fallback explainer + cached seed for talk day (gitignored, so machine-local) |

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

Display names use the enterprise `Last, First - ID` shape on purpose — `findings.py:131` parses
exactly that, so the fixture exercises the real path.

## Reset before every run

```sh
python3 fixture.py          # ~28 s, idempotent, offline
```

Deletes and recreates the *project* (that is what restarts MR IIDs at 1), re-pushes branches,
recreates the MRs, seeds threads and the local review state, re-clones the working copy at
`~/src/agent-skills-demo` (plus a review worktree beside it), and wipes skill state under
`~/.claude/review-mr/` and `~/.claude/rework-mr/`.

On talk day use `./tmux-demo.sh` instead — it runs this only if the state does not verify.

### What it builds

| MR | Author | Contents | Used by |
|---|---|---|---|
| `!1` | `author-bot` | upstream **PR #175** (standalone mock server), 2 real commits, 20 files, with 2 planted flaws alongside 2 genuine upstream ones. Targets `release/2024-06`, not `main` | `/review-mr`, live first pass |
| `!2` | `author-bot` | route-level prefetch, plus a seeded conversation: fixed-by-push, author-asks-a-question, promised-but-not-done, untouched, and one peer thread | `/review-mr`, re-review |
| `!3` | **you** | query-key factories in 2 commits, with 3 reviewer threads (trivial / hard / small) | `/rework-mr` |

The MR !2 state is not hand-written. Threads are posted, recorded through `findings.py`'s own
CLI (`import` → `link`), the baseline is taken with `set-head` while the worktree still sits on
version 1, and only then are the author's two fixes pushed. Ordering is the mechanism, so no
SHA is hardcoded anywhere — and if a refactor breaks `import`/`link`/`set-head`, the reset
fails loudly. That is what makes this an E2E harness and not just a demo prop.

### Verify a reset

```sh
cd ~/src/agent-skills-demo-review
python3 ~/.claude/skills/review-mr/scripts/findings.py sync --iid 2
#   "drafts in en", t1/t2/t3 ◐ needs-ack, t4 ○ open, t5 💬 peer, 2 pushes since baseline

cd ~/src/agent-skills-demo
python3 ~/.claude/skills/rework-mr/scripts/threads.py sync --iid 3
#   three ○ open topics
```

`glab api user` must run **inside a demo checkout** — glab resolves its host from the git
remote, so from `e2e/` (a GitHub remote) it returns `401`, not `frank`.

## Why it is built this way

Decisions that are expensive to rediscover, and that look like cruft until you know why:

- **`main` is pinned at `74c76128`** (2024-05-03, parent of `e74349ee`). The era matters: the
  upstream monorepo split lands at `1508d6d`, and everything before it is a flat `src/`.
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

### VM memory

The rig ran in a **6 GB / 2 CPU** Rancher Desktop VM with Kubernetes enabled, but steady-state
GitLab sits at ~3.1 GiB with only ~1.3 GiB available. Enough for normal operation (git pushes
and API calls are cheap; `review-branch` and `explain-branch` run on the host), but no headroom
for a rails spike. For talk-day safety consider `rdctl set --virtual-machine.memory-in-gb 12`
— it restarts the VM and therefore every other container in it, so not done automatically.
