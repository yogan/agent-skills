# Talk runbook

45 minutes covered all of it, questions included. Setup and rationale: [README.md](README.md).

## Before you start

```sh
cd ~/src/agent-skills/e2e && ./tmux-demo.sh    # verify the rig, reset if needed, build tmux
tmux attach -t agentic-review-skills-demo      # claude is running, each line typed — don't press Enter yet
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
| 2 | slide 2 · `/review-mr` | press Enter in the `review` window; talk the five rows |
| 3 | terminal · **!1** | click the explainer link → **overview table, 5 topics** → curate 2 → post a comment of your own in the UI, then `sync` → draft one → paste and post |
| 4 | terminal · **!2** | `sync` → `updates` (2 compare URLs) → `diff t1` → ack t1 → t2 the author's question → t3 claimed-but-not-done |
| 5 | slide 3 · `/rework-mr` | hat switch; five rows |
| 6 | terminal · **!3** | `sync` → t1 trivial → t2 the cache bug → fix, **diff opens in its own window** → annotate a line there → fixup + force-push → `reply-view` → paste one reply, let it post the other |
| 7 | slide 4 · outro | don't oversell it; questions |

!1 and !2 have a worktree each, so the two segments are independent — run them in any order,
go back to one after the other, leave both open.

!1 generates nothing — the explainer and the five topics are frozen in `e2e/artifacts/`, so the
segment is two lookups and an import, ~30 s. Fill the time with talk.

Each window's line is the bare command. Don't add instructions to it: everything they used to
answer is prepared in the rig now, and telling the skill to stop early makes it truncate a
block it has to paste whole.

## Four things to say

- **!1 is a real MR** — `bulletproof-react` PR #175, replayed commit for commit. `cors({ origin:
  '*', credentials: true })` and the un-awaited `persistDb('comment')` are **planted**; `loadDb()`
  returning `null`, `/auth/me` answering `200 null` and the uncalled `/healthcheck` are
  **upstream's own**. Dwell on CORS; the missing `await` shows it reading carefully.
- **On !1, post a comment yourself in the GitLab UI** while the skill works — a nit on a file no
  draft touches — then `sync`. It appears as a 👤 topic, unprompted, and is tracked from then on.
  That is the moment to say why the skill is read-only.
- **On !2, `diff t1`** — the author said "reworked it"; the skill shows the actual change instead
  of making you trust the reply.
- **On !3, the fix opens in a window of its own** — a diff viewer beside the chat, not a wall
  of diff in the transcript. It appears once the agent has made the change, which is after the
  topics have been agreed, not per topic. Switch to it, then **answer on a line of the diff
  itself** ("why not the list key too?"): the note goes back to the agent, which has to address
  it before it is allowed to push. The window closes itself once the push lands. This is the
  part no other tool does — show it rather than describing it.
- **On !3, t2 is a real cache bug** — `discussionKeys.all` is `['discussions']` while detail
  queries are `['discussion', id]`. No shared prefix, so invalidating `all` never refreshes the
  detail query the mutation just updated.

## If it breaks

| Symptom | Move |
|---|---|
| !1 starts a subagent or a `review-branch` run | a prepared input did not resolve — Esc, then `./tmux-demo.sh --force` |
| the explainer link 404s | `open ~/src/agent-skills/e2e/artifacts/explainer-mr1.html` |
| 502 on every page | `docker exec e2e-gitlab gitlab-ctl restart puma`, wait ~60 s |
| state looks wrong | `./tmux-demo.sh --force` — ~30 s. Kill the tmux session first: the re-clone leaves its panes on deleted directories |
| GitLab feels slow | it is memory (README, "VM memory"). Do not restart it mid-talk |
| running late | protect `!2` — pre-seeded, cheap, and the part nobody else has |

Seeding !1 by hand, if it comes to that — run it **from the review worktree**, which is where
`findings.py` resolves the project from:

```sh
cd ~/src/agent-skills-demo-review-mr1
python3 ~/.claude/skills/review-mr/scripts/findings.py \
  import ~/src/agent-skills/e2e/artifacts/seed-mr1.json --iid 1
```
