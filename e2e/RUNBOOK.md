# Talk runbook

**It ran ~60 min, not the 30 it was planned for** — and that was with everything going
smoothly. Ask for an hour, or drop a segment. Setup and rationale: [README.md](README.md).

## Before you start

```sh
cd ~/src/agent-skills/e2e && ./tmux-demo.sh    # verify the rig, reset only if needed, build tmux
tmux attach -t agentic-review-skills-demo      # Enter in each of the 3 windows to start claude
cd ~/src/agent-skills-slides && npm run dev    # F = present, P = notes + timer. 15 → presses
```

Browser: log in as `frank` (password in `.env.local`, `E2E_PASSWORD`), one tab per MR —
`http://gitlab.test/demo/bulletproof-react/-/merge_requests/1`, `…/2`, `…/3`.

A non-zero exit from `tmux-demo.sh` means do not start. Never `docker compose down` — a cold
GitLab boot is 3-5 min.

## Run of show

| | Where | Do |
|---|---|---|
| 1 | slide 1 · title | show of hands; why MR review hurts |
| 2 | slide 2 · `/review-mr` | **type the kickoff on arrival**, then talk the five rows while it runs (~4 min) |
| 3 | terminal · **!1** | explainer → **overview table = the money shot** → curate 2 topics → post a comment of your own in the UI, then `sync` → draft one → paste and post |
| 4 | terminal · **!2** | `sync` → `updates` (2 compare URLs) → `diff t1` → ack t1 → t2 the author's question → t3 claimed-but-not-done |
| 5 | slide 3 · `/rework-mr` | hat switch; five rows |
| 6 | terminal · **!3** | `sync` → t1 trivial → t2 the cache bug → fix → fixup + force-push → `reply-view` → paste one reply, let it post the other |
| 7 | slide 4 · outro | don't oversell it; questions |

Kickoff prompt for step 2 — front-loads every answer so it runs unattended:

```
/review-mr !1 — generate the explainer first, then seed findings with review-branch,
then show me the overview and stop. Don't ask me anything before the overview.
```

**Run `!1` before `!2`.** They share one worktree; `/review-mr` switches the checkout for you.

## Four things to say

- **!1 is a real MR** — `bulletproof-react` PR #175, replayed commit for commit. `cors({ origin:
  '*', credentials: true })` and one un-awaited `persistDb('comment')` are **planted**;
  `loadDb()` returning `null` while callers spread it, and the relative mock-DB path, are
  **genuinely upstream's**. Dwell on CORS; the missing `await` shows it reading carefully.
- **On !1, post a comment yourself in the GitLab UI** while the skill works — a nit on a file
  no draft touches — then `sync`. It appears as a 👤 topic, unprompted, and is tracked from
  then on. That is the moment to say why the skill is read-only.
- **On !2, `diff t1`** — the author said "reworked it"; the skill shows the actual change
  instead of making you trust the reply.
- **On !3, t2 is a real cache bug** — `discussionKeys.all` is `['discussions']` while detail
  queries are `['discussion', id]`. No shared prefix, so invalidating `all` never refreshes
  the detail query the mutation just updated.

Don't fight the finding count: show the whole table, say "I'll take two for time", and drive
the planted ones by filename.

## If it breaks

| Symptom | Move |
|---|---|
| the `review-branch` seed on !1 stalls past ~90 s | import the cached seed — 7 findings, same visible outcome, 2 s (command below) |
| `explain-branch` fails or drags | `open ~/src/agent-skills/e2e/backup/explainer-mr1.html` |
| 502 on every page | `docker exec e2e-gitlab gitlab-ctl restart puma`, wait ~60 s |
| state looks wrong | `./tmux-demo.sh --force` — 28 s. Kill the tmux session first: the re-clone leaves its panes on deleted directories |
| GitLab feels slow | it is memory (README, "VM memory"). Do not restart it mid-talk |
| running late | protect `!2` — pre-seeded, cheap, and the part nobody else has |

The cached seed, from the review worktree — `findings.py` resolves its project from `cwd`, so
the directory matters more than the flag:

```sh
cd ~/src/agent-skills-demo-review
python3 ~/.claude/skills/review-mr/scripts/findings.py \
  import ~/src/agent-skills/e2e/backup/cached-seed-mr1.json --iid 1
```

`backup/` is gitignored, so both fallbacks are local to this machine — on a new one, recapture
them from a rehearsal. **The cached seed has never actually been test-imported**, so treat it
as a best guess rather than a proven escape hatch.
