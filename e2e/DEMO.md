# Demo notes

Short, for the phone. Setup and fallbacks: [RUNBOOK.md](RUNBOOK.md).

## `/review-mr !1` — fresh MR, no comments yet

- go over the explainer
- post two comments:
  - on `mock-server.ts`, `origin: '*'` "seems insecure"
  - on `port: 3000` a question: "isn't this clashing with port 3000 above?"
- sidebar: open one, **Next**
- port: **Analyse** → fine → reply "nevermind" + resolve (GitLab)
- **My review is done** → **Explain** → **Merge into t1**
- the agent's topics:
  - `t1` CORS: **Follow up** (better comment than mine)
  - `t2` `loadDb` returns `null`: **Post** as is
  - `t3` un-awaited `persistDb`: likely a one-line *suggestion*, **Post**, show it
  - `t4` `/auth/me` `200 null`: **Copy**, post myself leaving out one half,
    **I posted my own** → agent checks, *edit*
  - `t5` `/healthcheck`: **Drop**

## `/review-mr !2` — my review, Alice replied and pushed

- `t1` `queryClient` lifetime → *Verdict: Settled*
  - diff in the sidebar, then **Show in hunk**
  - **Analyse more**
  - **Accept + resolve**
- `t2` `ensureQueryData` errors → *Verdict: Partly fixed*
  - Alice asked "404s or all?", pushed a catch for all
  - ask the agent (prompt) which option is best
  - draft follow-up: swallow only 404s, **Post**
- `t3` "fix to come": *Wait for author*
- **if time:** `t5`, Clara's thread → **Analyse**

## `/rework-mr !3` — my MR, Bob's three comments

- `t1` rename `queryKeys` → `commentKeys`: **Agree plan**
- `t2` cache bug, he offers 2 options
  - ask the agent which one (might already give one; we can prompt for reasons)
  - **Agree and reply** → **Post**, before any code
- `t3` `users`/`teams` too: push back, follow-up MR
  - **Reply** → **Post + resolve**
- Fixes:
  - `t1` **Fix it**
    - **Review in hunk** (no notes)
    - **Ack and push** (folded into both commits)
    - **“Fixed” + resolve**
  - `t2` **Fix it**
    - **Review in hunk** (ask question via note)
    - Check agent response
    - **Ack and push**
