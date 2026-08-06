# Talk runbook

30 min slot, reviewer→author order. Design rationale: [PLAN.md](PLAN.md). Setup:
[README.md](README.md).

## T-30 min — pre-flight

```sh
docker ps --format '{{.Names}} {{.Status}}'          # e2e-gitlab must be Up (never `compose down`)
curl -sS -o /dev/null -w '%{http_code}\n' http://gitlab.test/users/sign_in   # want 200
cd ~/src/agent-skills/e2e && python3 fixture.py      # ~20 s
glab api user | python3 -c 'import json,sys; print(json.load(sys.stdin)["username"])'   # frank
```

Then confirm the fixture really is in the demo state — this is the check that matters,
because it runs the same code the demo runs:

```sh
cd ~/src/agent-skills-demo-review
python3 ~/.claude/skills/review-mr/scripts/findings.py sync --iid 2
#   expect: "drafts in en", t1/t2/t3 ◐ needs-ack, t4 ○ open, t5 💬 peer, 2 pushes

cd ~/src/agent-skills-demo
python3 ~/.claude/skills/rework-mr/scripts/threads.py sync --iid 3
#   expect: three ○ open topics
```

Browser: log in as `frank` (password in `.env.local`, `E2E_PASSWORD`), open three tabs

1. `http://gitlab.test/demo/bulletproof-react/-/merge_requests/1`
2. `…/merge_requests/2`
3. `…/merge_requests/3`

Terminal: 4 tmux windows, one claude session per directory (each skill resolves its
project from `cwd`, and `review-mr/SKILL.md:42` warns that a bare `glab mr view` in the
wrong worktree resolves the wrong MR).

| Window | cwd | For |
|---|---|---|
| 0 `rig` | `~/src/agent-skills/e2e` | `fixture.py`, spare shell |
| 1 `review` | `~/src/agent-skills-demo-review` | `/review-mr !1` |
| 2 `rereview` | `~/src/agent-skills-demo-review` | `/review-mr !2` |
| 3 `rework` | `~/src/agent-skills-demo` | `/rework-mr` |

> Windows 1 and 2 share one worktree (the pointer is repo-wide, one path per project).
> Run `!1` first, and let `/review-mr` switch the checkout when you move to `!2`.

Deck: `cd ~/src/agent-skills-slides && npm run dev`, open **Agentic Code Reviews**, `F` for
play mode and `P` for the presenter window (notes + elapsed timer). Four pages, **15 `→`
presses** — the two skill pages reveal their five rows one press at a time.

Start the claude sessions now so the processes are warm, but **do not invoke the skills** —
typing the command live is part of the show.

## T-0 — the kickoff, on slide 2

Slide 2 is the `/review-mr` page. Type this in window 1 **as you land on it**, before you
start talking through the rows, and leave it running. It front-loads every answer so the run
is genuinely unattended:

```
/review-mr !1 — generate the explainer first, then seed findings with review-branch,
then show me the overview and stop. Don't ask me anything before the overview.
```

It takes ~4 min, which is what the slide's five rows are there to fill. By the time you
switch to the terminal, the explainer HTML *and* the findings overview are both parked.
Rehearse the first 30 s at least once: if it stops on a question you did not pre-answer, it
sits idle while you talk and you arrive at nothing.

## Run of show

**Measured on a rehearsal:** a bare `/review-mr !1` reached the parked overview in **4 min
flat**, with the explainer written to `/private/tmp/<date>-explanation-<branch>.html` and
opened automatically. It produced **7 findings**, critical → low, with both planted flaws in
the list (CORS wildcard+credentials at 🔴 critical, the missing `await` at 🟡 medium) plus four
it found on its own. No prompt beyond `/review-mr !1` was needed.

| Time | Where | Beats |
|---|---|---|
| 0-3 | **slide 1** — title | Show of hands. Why MR review hurts: tone, multi-day loops, "did they actually fix it?" |
| 3-7 | **slide 2** — `/review-mr` | **type the kickoff on arrival**, then talk the five rows while it runs: explainer · agent review · human review · drafting · follow-up |
| 7-14 | terminal — **!1** | explainer → **overview table = the money shot** → curate 2 topics → **post a comment of your own in the browser, then `sync`** → draft 1 comment → paste into the UI and post |
| 14-18 | terminal — **!2** | "a review I started last week" → `sync` → `updates` (2 compare URLs) → `diff t1` → ack t1 → t2 the author's question → t3 claimed-but-not-done |
| 18-20 | **slide 3** — `/rework-mr` | hat switch. Five rows: threads · grilling · fixing · pushing · replies |
| 20-27 | terminal — **!3** | `sync` → t1 trivial (recommendation-first) → t2 hard (grilling, keep it short) → fix → fixup + force-push → `reply-view` → paste one reply, let the agent post the other |
| 27-30 | **slide 4** — outro | Don't oversell it: `glab`-only, read-only by design, the handle guard, state files. Questions |

Rows 1-4 of slide 2 are the `!1` segment; row 5 (`follow-up`) is `!2`. The slide stays up for
both, so it is talked once and cashed in twice.

**The rig is deliberately not a segment.** No slide, no tour of tmux or the local GitLab —
nobody needs the talk's own scaffolding. If it comes up in questions, one sentence: it's a
local GitLab in Docker, reset in 20 seconds.

### Things worth saying out loud

- **MR !1 is a real upstream MR** — `bulletproof-react` PR #175, "standalone mock server",
  replayed commit for commit. **Two of its flaws are genuinely upstream's**, not planted:
  `loadDb()` returns `null` on a read error while callers spread the result, and the mock DB
  path is relative so it depends on cwd. Two are planted: `cors({ origin: '*', credentials:
  true })` — wildcard origin *with* credentials, the classic misconfiguration — and one
  `persistDb('comment')` left un-awaited, in the very PR that adds those `await`s. The CORS
  one is the finding to dwell on; the missing `await` is the one that shows the tool reading
  carefully.
- **Don't fight the finding count.** If the seed produces six findings, show the whole
  table — it is more impressive than two. Say "I'll take two of these for time" and drive
  the two you know by filename.
- **The parallel-posting beat (do this on !1).** While the skill is working, write a comment
  yourself in the GitLab UI — a naming nit on a file none of the drafts touch — then say
  `sync`. It appears in the table as a 👤 topic without you being asked anything, and it is
  tracked from then on: you will see the author's reply and whether they actually addressed
  it. This is the moment to point out *why* the skill is read-only — it never fights you for
  the browser. (Comment on a file a draft already covers and it deliberately asks instead:
  it cannot tell that apart from your own draft posted by hand.)
- **On !2, `diff t1` is the beat that lands**: the author said "reworked it", and the skill
  shows you the actual change rather than making you trust the reply.
- **MR !1 targets `release/2024-06`**, not `main` — if anyone asks, the PR sits later on
  upstream master than the repo's `main`, so it is pinned to its own base. Nothing about the
  demo depends on it.
- **On !3, t2 is a real cache bug**: `discussionKeys.all` is `['discussions']` while detail
  queries are `['discussion', id]` — singular, no shared prefix — so refetching `all`
  never refreshes the detail query the mutation just updated.

## If something breaks

| Symptom | Move |
|---|---|
| the review-branch seed on !1 stalls past ~90 s | `python3 $SD/findings.py import ~/src/agent-skills/e2e/backup/cached-seed-mr1.json --iid 1` — 7 findings, same visible outcome, 2 s. Captured from a real rehearsal run |
| explain-branch fails or takes too long | open `e2e/backup/explainer-mr1.html` (captured from a rehearsal: 2 chapters, diagrams, quizzes) |
| a skill resolves the wrong MR | you are in the wrong cwd; always pass `--iid` / `!N` explicitly |
| **502 on every page** (container "running" but unhealthy) | puma stopped listening while runit still reported it up. `docker exec e2e-gitlab gitlab-ctl restart puma`, wait ~60 s. Seen once with single-mode puma; the compose file now runs one supervised worker instead |
| GitLab feels slow | it is memory; see README "VM memory". Do not restart it mid-talk |
| state looks wrong before you start | `python3 fixture.py` — 20 s, and it wipes local skill state too |
| you are running late | protect the `!2` segment and cut curation depth on `!1`. `!2` is pre-seeded, cheap, and the part nobody else has |

**Never** `docker compose down` on talk day: a cold GitLab boot is 3-5 min.

## Still to do before the talk

- [ ] Rehearse end to end twice, from `python3 fixture.py` to the wrap.
- [x] `backup/explainer-mr1.html` captured — 2 chapters, 4 diagrams, quizzes, 47 KB.
- [x] `backup/cached-seed-mr1.json` captured — the 7 findings from a real rehearsal.
      **Not yet test-imported** (that would have polluted a live review); import it once into
      a throwaway run before relying on it.
- [x] Explainer confirmed running on a bare `/review-mr !1` and opening in the browser.
- [ ] Rehearse steps 2-4 (review segment, re-review, rework) end to end, twice.
- [ ] Decide whether the live `/review-mr !1` findings are good enough, or whether the
      planted flaws need retuning in `patches/mr1-flaws.patch`.
