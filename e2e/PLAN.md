# Local GitLab demo + E2E rig for `review-mr` / `rework-mr`

Purpose, in priority order:

1. A **reliable live demo** for a ~30 min talk to experienced devs (not the team).
2. A reusable **E2E fixture** so both skills can be exercised against a real GitLab
   without touching the customer instance.

Hard constraints: offline-capable (venue wifi is untrusted), no customer GitLab, resettable
in < 60 s, single live identity (no user switching on stage), English throughout.

---

## 1. Infrastructure

| Thing | Decision |
|---|---|
| GitLab | `gitlab/gitlab-ce:latest` in Docker — **arm64 image confirmed** (verified via `docker manifest inspect`), native on Apple Silicon, no emulation |
| Host runtime | Rancher Desktop (`docker` 29.5.3), 48 GB RAM available |
| URL | `http://gitlab.test` — `external_url http://gitlab.test`, container maps `80:80`, `/etc/hosts` entry |
| TLS | none — deliberately. Self-signed means a browser warning page on stage |
| CI runner | **none.** Repo has no `.gitlab-ci.yml` at the pinned base, so no pipelines exist. Nothing to strip |
| Lifecycle | container stays **warm**; never `compose down` on talk day (first boot is 3-5 min) |

Trim GitLab's `omnibus` config to shorten boot: disable registry, mattermost, prometheus,
grafana, kas.

### Identities

| User | Role | Used by |
|---|---|---|
| `root` | admin | bootstrap script only (create users, project, PATs) |
| **you** | the only live identity | both skills, browser, `glab` |
| `author-bot` | authors MR !1 and !2, replies in MR !2's threads | fixture script only |
| `reviewer-bot` | posts the review threads on MR !3 | fixture script only |
| `peer-bot` | opens one extra thread on MR !2 | fixture script only |

Two bots minimum — `findings.py:153-176` derives thread turn ("your turn" vs "author's turn")
from **author username vs `me`**, so a self-reviewing single user collapses the turn model.

Bot tokens live only inside the fixture script. `GITLAB_TOKEN` in glab is **host-agnostic** —
never export it globally, or it also hits `gitlab.dm-drogeriemarkt.com`.

### Local working copy

`~/src/agent-skills-demo` — clone of the seeded GitLab project. `/review-mr` also wants a
review worktree; the fixture sets it via `findings.py worktree --set`.

---

## 2. Source material

Upstream: `github.com/alan2207/bulletproof-react` (React + TS + Vitest, 535 files, ~23k LOC).

Era matters: the monorepo split is `1508d6d` (2024-07-20). Everything from 2024-04 → 2024-06
is flat `src/…`. **Pin `main` at `a3aff530`** (2024-04-27); all demo branches replay later
commits from that coherent era.

### The three MRs — IID == demo step

| MR | Upstream commit | Size | Author | Demo segment |
|---|---|---|---|---|
| **!1** | `e74349ee` *store the auth token in a cookie instead of localStorage* **+ planted flaws** | 11f / 183L | `author-bot` | `/review-mr` live first pass |
| **!2** | `9aecaf6e` *use react router loaders to fetch data before rendering* | 12f / 340L | `author-bot` | `/review-mr` re-review |
| **!3** | `7ddd5c46` *improve query keys* | 11f / 118L | **you** | `/rework-mr` |

Creation order sets the IIDs, so `!1`/`!2`/`!3` match the run-of-show. Deleting and
recreating the project on reset restarts IIDs at 1 — that is why reset recreates the
*project*, not just the branches.

Rationale: !1 is a **security** change (auth token storage) — the audience leans in, and
there is genuine substance to review. !3 is small and mechanical, so live fixups are quick.
!2 is the largest, giving room for five seeded threads.

### Replay mechanism — do **not** cherry-pick

Cherry-picking across a 5-week span can conflict, and a conflict at fixture-build time is a
flaky setup. Instead, per branch: branch from `main`, then

```
git checkout <upstream-sha> -- <paths touched by that commit>
git commit
```

Always applies cleanly, diff is still 100 % real upstream code, deterministic forever.

**MR !3 gets 2-3 commits**, not one (split by path group: `api/`, then `features/`) —
`/rework-mr` does fixup + force-push, so there must be commits to squash *into*.

### Planted flaws (MR !1 only)

Three deliberate defects layered on the real diff. Reason: the live review **must** surface
something worth discussing — unplanted real code is a gamble on stage. Three distinct
severities *and* three distinct kinds of finding:

| # | Plant | Location | Kind |
|---|---|---|---|
| 1 | cookie set with `sameSite: 'none'`, no `secure`, no `httpOnly` | `src/test/server/utils.ts` | security config — dead on-topic for the MR's own purpose |
| 2 | a surviving `storage.setToken(...)` call — the token **still** lands in localStorage | `src/lib/auth.tsx` | **contradicts the MR's stated purpose**; the money finding |
| 3 | `logout.ts` posts to `/auth/logout` but never clears the cookie → session survives logout | `src/features/auth/api/logout.ts` | behavioural bug, one breath to explain |

Rejected as a fourth plant: "cookie auth with no CSRF token added". Architecturally sound
finding, but least reliably surfaced, and four plants clutter the overview.

**Applied folded into the replayed commits**, so the diff looks native on GitLab — but kept as
a tracked `e2e/patches/mr1-flaws.patch` for provenance and for retuning after rehearsal.

Plants are known by filename, so the two topics driven live can be picked instantly regardless
of how many findings the seed produces.

**Keep `yarn.lock` in the replay** (395 of the commit's lines). Every exclusion manufactures a
false finding — drop the lockfile and the agent may lead with "lockfile not updated"; drop
`package.json` too and it becomes "`js-cookie` isn't declared". Real MRs carry lockfile churn
and GitLab collapses it in the UI.

---

## 3. Skill changes required

These are real portability fixes, not demo scaffolding.

1. **Scheme hardcoded to `https`** — `review-mr/scripts/_gl.py:49` and
   `rework-mr/scripts/_gl.py:47` build `"web": f"https://{host}/{path}"`. Against
   `http://gitlab.test` every diff/thread URL is broken. Blocks everything — do it first.

   Fix is **layered, most-authoritative first**:
   1. the MR's API **`web_url`** (already captured as `mr_web_url` in `findings.py:143` /
      `threads.py:99`) — authoritative for scheme, host, port and install path. Used wherever
      an MR object is in hand, i.e. most consumers.
   2. **`glab config get api_protocol --host <host>`** when there is no MR context.
   3. `https` as last resort.

   Do **not** parse the scheme out of `git remote get-url origin`: the dm host runs
   `git_protocol=ssh` with `api_protocol=https`, and an SSH remote carries no scheme at all,
   so that path is always a guess. Cost: `diff-url.py` gains one `mr_object(ctx, iid)` call on
   the `--iid` path (which currently skips `mr_view()`); `mr_object` with an explicit iid also
   avoids the wrong-worktree misfire `glab mr view` has.

2. **`review-mr` language is hardcoded German** (`REFERENCE.md:8-9`, `SKILL.md:128`).

   Naive fix ("mirror the surrounding language") is **wrong**: `REFERENCE.md:8` records that
   real MR titles/descriptions are English while comments are German, so mirroring would
   regress daily use.

   Second trap: **`findings.py` never generates prose** — the agent writes drafts via
   `set <t> --draft "…"`, and the language rule lives only in SKILL.md/REFERENCE.md. A state
   field alone is therefore **inert**. What makes it work is **rendering** it: print `lang: en`
   in the `sync` / `present` / `todo` header, so the instruction rides along with output the
   agent reads every turn instead of relying on 30 minutes of memory.

   Third trap: **state is per-MR, and MR !1's state does not exist until `sync` runs live**, so
   the fixture cannot pre-set it. Solution — a **repo-wide default file**, mirroring the
   worktree pointer the skill already keeps (`findings.py:13`):

   ```
   ~/.claude/review-mr/<slug>/lang        # "en", written once by the fixture
   ```

   `new_state()` seeds `lang` from it, so every MR in the project inherits it — including one
   reviewed for the first time on stage. Total: ~12 lines + two doc lines, docs changed from
   *"German. Hardcoded"* to *"German by default; the repo/state `lang` wins"*.

   Side benefit: the skill is currently unusable by any non-German team. This fixes that.

3. **`rework-mr`: no change needed.** `SKILL.md:243` already sets draft language from the
   thread, so English seeded threads yield English drafts.

4. **`review-branch` ownership — already resolved** (cleaned up 2026-08-04). The duplicate copy
   under `~/env/.agents/skills/` is gone and untracked; `~/.agents/skills/review-branch` now
   points at this repo. Verified: no broken links under `~/.claude/skills/` or
   `~/.agents/skills/`. It still has to be repointed by the restructure below.

### Repo restructure

Move skills into `skills/` so tooling can loop over directory entries:

```
skills/{review-mr,rework-mr,review-branch,explain-branch,explain-diff}/
e2e/
README.md
```

**Done.** All five links were repointed at `~/src/agent-skills/skills/<name>` (two directly
under `~/.claude/skills/`, three via `~/.agents/skills/`) and verified: every entry under both
directories resolves to a dir containing `SKILL.md`. A dangling skill link fails silently — the
skill just stops being offered — so that assertion is the checkpoint, not an afterthought.

No in-repo path edits were needed: skills refer to each other as
`~/.claude/skills/<name>/scripts/…`, which the repointed links still satisfy.

`README.md`'s linking guide now uses a loop over `skills/*/` plus a dangling-link check.

---

## 4. Fixture state per MR

### MR !3 — `/rework-mr` (no local state seeding)

`threads.py` has no `add`/`import` verb: on the author side every inbound thread belongs to a
reviewer, so `sync` adopts them automatically. Seed GitLab-side only; the demo opens with a
genuine first `sync`.

Seed as `reviewer-bot`, deliberately mixed so both response modes appear:

- one **trivial** thread (naming / key shape) → skill goes recommendation-first
- one **hard** thread (cache-invalidation semantics) → skill goes grilling-style
- one or two more for a plausible overview

That trivial-vs-hard contrast is `rework-mr`'s differentiator.

Live: `sync` → discuss both → fix → fixup + force-push → `reply-view` →
**paste one reply yourself, let the agent `p`-post the other** (`SKILL.md:229-234`; `p` is the
one allowed write, guarded by `guard-reply.sh`).

### MR !2 — `/review-mr` re-review (needs local state seeding)

`adopt_inbound` (`findings.py:284`) **skips threads where `mine` is true** — threads you
opened are expected to match a local draft topic via `candidates`/`link`. With an empty
`findings.json`, seeded "your" comments would be invisible. So local state must be seeded.

Build it by **driving the skill's own CLI**, never by writing `findings.json` directly — no
schema coupling, and the fixture becomes E2E coverage: if a refactor breaks `import`/`link`,
`make fixture` fails loudly.

Ordering is the trick (state emerges from real operations, no hardcoded SHAs):

1. post the five threads (as you / `author-bot` / `peer-bot`) — capture returned discussion ids
2. `findings.py import fixture-mr2-findings.json`
3. `findings.py link t<n> <discussion_id>` per thread
4. `findings.py worktree --set …`
5. `findings.py set-head` **while the local checkout is still at the pre-fix commit** — it
   takes no SHA, it marks the current tip, so ordering alone creates the baseline
6. `author-bot` replies, then pushes the fix commits (**two pushes**, so `updates` renders two
   compare URLs with diffstats)

Thread matrix:

| Thread | Seeded state | Demonstrates |
|---|---|---|
| t1 | author replied **and pushed a real fix** | `diff t1` shows the change → ◐ needs-ack → your ack closes it |
| t2 | author **asks a clarifying question** | ◐ needs-ack, your turn → skill drafts an answer |
| t3 | author replied *"will do"*, **no code change** | ○ open, author's turn — the claimed-≠-done case |
| t4 | **untouched, no reply** | ○ open, author's turn |
| t5 | opened by **`peer-bot`** | `adopt_inbound` (💬 peer) — free feature showcase |

### MR !1 — `/review-mr` first pass

No seeding beyond the branch + planted flaws. Everything is produced live.

---

## 5. Reset semantics

`make fixture` is **idempotent and state-deriving** — safe to run twice, and it detects
what already exists rather than assuming a clean slate.

1. delete + recreate the GitLab project (restarts MR IIDs at 1)
2. push `main` @ `a3aff530` + the three MR branches
3. create the three MRs via API as the correct authors
4. seed threads, interleaved with MR !2's fix pushes so outdated/addressed states are genuine
5. replay the `findings.py` CLI sequence for MR !2
6. **nuke and re-clone `~/src/agent-skills-demo`**, including any review worktree
7. **wipe skill state outside git and outside GitLab** — this survives both a project delete
   and a re-clone, and a stale file makes the live run *resume mid-review*:
   - `~/.claude/review-mr/<slug>--mr<iid>/findings.json`
   - `~/.claude/review-mr/<slug>/worktree` (would dangle at a deleted path)
   - `~/.claude/rework-mr/<slug>--mr<iid>/topics.json`

Target: < 60 s, offline, repeatable. No volume snapshots — they buy nothing `make fixture`
does not, and add a stale-state failure mode.

Run it **immediately before the talk**, and rehearse end-to-end at least twice the same way.

---

## 6. Run-of-show (~30 min)

Fire `/review-mr !1` **at t=0**, before the intro. It runs explain-branch *then* the
review-branch seed sequentially, so one kickoff hides both slow steps and by ~4:00 the
explainer HTML and the findings overview are both ready.

| Time | Segment | Content |
|---|---|---|
| 0:00 | kick off `/review-mr !1` | rehearsed prompt, then leave it |
| 0-3 | framing | why MR review hurts: tone, multi-day loops, "did they actually fix it?" |
| 3-4 | the rig | tmux + 3 claude sessions + local GitLab; "I can reset all of this in 60 s" |
| 4-13 | **`/review-mr` !1** | explainer → overview (the money shot) → curate **2 topics** → draft one comment → paste into the UI |
| 13-17 | **`/review-mr` !2** | "a review I started last week" → `sync` → `updates` (2 compare URLs) → `diff t1` → ack t1 → t2 question → t3 claimed-not-done |
| 17-26 | **`/rework-mr` !3** | hat switch → `sync` → trivial topic → hard topic (grilling, keep short) → fix → fixup+push → `reply-view` → paste one, agent posts one |
| 26-30 | wrap | glab-only, read-only-by-design, handle guard, state files, then the rig itself |

### Choreography decisions

- **Kickoff prompt must front-load every answer** so the run is genuinely unattended:
  roughly *"MR !1: generate the explainer, then seed with review-branch, then show me the
  overview and stop — don't ask me anything before that."* Commit it in the docs and rehearse
  the first 30 s; otherwise it sits waiting on a prompt while you talk.
- **Don't fight finding count.** If the seed yields 6 findings, the full overview table is
  *more* impressive than 2. Say "I'll take two of these for time" and drive the **planted**
  ones by filename. No dropping, no fakery, robust to any N.
- **Skip running `explain-diff`** and the full curation loop — mention, don't demo.
- **`/review-mr` !2 is the safest segment** (pre-seeded, no LLM-heavy step, highest wow).
  If time runs short, protect it; cut curation depth in !1 instead.
- **Pre-start the claude sessions** (warm process, correct cwd) but **do not pre-invoke** the
  skills beyond the t=0 kickoff — typing the command live is part of the show.

### tmux / browser layout

fish + tmux, one claude session per directory (each skill resolves its project from cwd;
`review-mr/SKILL.md:42` warns that a bare `glab mr view` in the wrong worktree resolves the
wrong thing):

| Window | cwd | Purpose |
|---|---|---|
| 0 | `e2e/` | fixture / reset commands |
| 1 `review` | worktree @ MR !1 branch | `/review-mr !1` |
| 2 `rereview` | worktree @ MR !2 branch | `/review-mr !2` |
| 3 `rework` | `~/src/agent-skills-demo` @ MR !3 branch | `/rework-mr` |

Browser: three tabs, same order. Large font, no second-monitor clutter.

### Backups

- `e2e/backup/explainer-mr1.html` — committed from a rehearsal run, in case explain-branch fails
- `e2e/backup/cached-seed-mr1.json` — from a rehearsal; if the live seed stalls past ~90 s,
  `findings.py import` it for the same visible outcome in 2 s

---

## 7. Phases

Each phase ends at a checkpoint that can be verified before moving on.

| Phase | Work | Checkpoint |
|---|---|---|
| **P0** infra | `docker-compose.yml`, `/etc/hosts`, omnibus trim, bootstrap script (users, PATs, project) | `glab api user` against `gitlab.test` returns you; project visible in browser |
| **P1** skill patches + restructure | layered scheme fix in both `_gl.py`; `lang` state + header render + repo-wide `lang` file; move to `skills/`; repoint all links in `~/.claude/skills/` **and** `~/.agents/skills/`; README linking guide | both skills run against `http://gitlab.test` with working URLs; **every** entry under both skill dirs resolves to a dir containing `SKILL.md`; drafts come out English |
| **P2** MR !1 + planted flaws | replay `e74349ee`, plant 2-3 defects, rehearse the live review | overview contains ≥2 planted findings; drafts are English |
| **P3** MR !3 | replay `7ddd5c46` as 2-3 commits, seed `reviewer-bot` threads (1 trivial + 1 hard) | `/rework-mr` `sync` shows correct turns; fixup + force-push works; agent `p`-post works |
| **P4** MR !2 | replay `9aecaf6e`, five-thread matrix, CLI replay, `set-head` ordering, two fix pushes | `sync` shows t1 ◐ needs-ack, `updates` renders 2 compare URLs, t5 adopted as 💬 |
| **P5** docs + rehearsal | `e2e/README.md` (setup) + run-of-show checklist + kickoff prompt; capture backup artifacts; two full rehearsals | cold `make fixture` → full run-of-show, twice, no surprises |
| **P6** *(post-talk)* | turn the fixture into asserted E2E tests | CI-able test run |

P2 before P3/P4 deliberately: it is the only phase whose quality depends on **LLM output
tuning** (do the planted flaws surface well?), so it needs the most iteration time.
P4 is the most intricate — last, when the rig is stable.
