# Talk runbook

45 minutes covered all of it, questions included. Setup and rationale: [README.md](README.md).

This is the full version, for setting the demo up and for when something breaks. On stage the
presenter reads [DEMO.md](DEMO.md): the same run of show, short enough for a phone. The two
describe what the fixture builds, so a change to the demo MRs (`fixture.py`, `patches/`,
`artifacts/`) updates both.

## Before you start

```sh
cd ~/src/agent-skills/e2e && ./tmux-demo.sh    # verify the rig, reset if needed, build tmux
tmux attach -t agentic-review-skills-demo      # claude is running, each line typed — don't press Enter yet
cd ~/src/agent-skills-slides && npm run dev    # F = present, P = notes + timer. 15 → presses
```

Browser: log in as `frank` (password in `.env.local`, `E2E_PASSWORD`), one tab per MR —
`http://gitlab.test/demo/bulletproof-react/-/merge_requests/1`, `…/2`, `…/3`.

Slides in **Safari**, fullscreen; GitLab and the explainer in **Edge**, on the desktop. The demo
windows' `open` (`demo-bin/open`) sends every page to Edge, so clicking the explainer link
never touches Safari: sent to the slides' own browser, even into its other window, a page took
the slides out of fullscreen. `DEMO_BROWSER` names another app.

A non-zero exit from `tmux-demo.sh` means do not start. Never `docker compose down` — a cold
GitLab boot is 3-5 min.

## Run of show

| | Where | Do |
|---|---|---|
| 1 | slide 1 · title | show of hands; why MR review hurts |
| 2 | slide 2 · `/review-mr` | press Enter in the `review` window; talk the five rows |
| 3 | terminal · **!1** | click the explainer link on the card → the agent's five findings stay hidden until your own review is in: post two comments of your own in GitLab, the sidebar lists them → open one (its card), **Next**, **Analyse** the port question → reply and resolve it in GitLab (✓ resolved, left out of the matching) → **My review is done** → Merge into t1 or Keep separate for the CORS comment → the topics one by one: **Drop** one, **Comment** on one → **Post** |
| 4 | terminal · **!2** | the sidebar opens on **News** (Alice pushed twice, answered, Clara opened a thread) → **t1** the clean fix: its card shows Alice's push, **Show in hunk** → Accept + resolve → **t2** only partly fixed: Alice asked "404s or all errors?" and swallowed all of them anyway → draft the follow-up, Post → t3 "fix to come", nothing to do yet |
| 5 | slide 3 · `/rework-mr` | hat switch; five rows |
| 6 | terminal · **!3** | plans first: t1 the rename, **Agree plan** → t2 the cache bug: ask the agent which of Bob's two options, then **Agree and reply** and **Post** before any code → t3 scope creep: push back, a follow-up MR, **Reply**, **Post** → then the fixes: t1 **Fix it** → **Review in hunk** opens the diff in its own window, annotate a line there → **Ack and push** → **“Fixed” + resolve** → t2 **Fix it** → **Ack and push** |
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
- **On !1, your own review comes first.** The agent's findings stay hidden until you press My
  review is done, so they cannot steer you. Post a comment yourself in GitLab meanwhile; it shows
  in the sidebar, and once you are done the agent offers to merge it into its matching topic or
  keep it separate. Nothing is posted except by your press: the moment to say why the skill is
  read-only.
- **On !2, the agent reads the code, not the reply.** t1 is the easy case: Alice said "reworked
  it", and her push really fixes both halves of the comment (tests get their own client, logout
  drops the session's), so it is accepted and resolved in one press. t2 is the opposite: Alice
  asked whether to swallow only 404s or all errors, then pushed a catch that swallows all of
  them. The card judges it partly fixed and drafts the reply that says so.
- **On !3, the fix opens in a window of its own** — after **Fix it**, the card lists the change
  and the sidebar its files; **Review in hunk** opens a diff viewer beside the chat, not a wall of
  diff in the transcript. The plans are agreed for all topics first, the fixes come after. Switch
  to the viewer, then **answer on a line of the diff itself** ("why not the list key too?"): the
  note goes back to the agent, which has to address it before it is allowed to push. **Ack and
  push** folds the fix into the commit it belongs to and force-pushes. This is the part no other
  tool does — show it rather than describing it.
- **On !3, t3 asks for more than the MR is about** — moving `users` and `teams` to key
  factories too. The agent proposes declining it: one press drafts the reply in the thread's
  language, and it is posted before any code.
- **On !3, t2 is a real cache bug** — `discussionKeys.all` is `['discussions']` while detail
  queries are `['discussion', id]`. No shared prefix, so invalidating `all` never refreshes the
  detail query the mutation just updated. Bob offers two ways out and asks which;
  **Agree and reply** tells him the choice before any code, and the fix follows.

## If it breaks

| Symptom | Move |
|---|---|
| !1 starts a subagent or a `review-branch` run | a prepared input did not resolve — Esc, then `./tmux-demo.sh --force` |
| the explainer link 404s | `open -a "Microsoft Edge" ~/src/agent-skills/e2e/artifacts/explainer-mr1.html` |
| 502 on every page | `docker --context colima-gitlab exec e2e-gitlab gitlab-ctl restart puma`, wait ~60 s |
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
