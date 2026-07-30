---
name: rework-mr
description: Work through the review feedback on a GitLab MR that YOU authored — retrieve the open discussion threads, discuss each topic to a plan (recommendation-first for trivial ones, grilling-style for complex ones), then fix, fixup-and-push, and draft a thread reply per topic. Also shows a status table of the open threads — a full overview or just what needs you — on demand. This is for reworking your own MR in response to reviewers, NOT for reviewing someone else's MR (use review-branch for that). Use when the user says "rework my MR", "address the review comments", "show me the open GitLab threads and grill me on them", "work through the MR feedback", "status of the MR / where do the review threads stand", or invokes /rework-mr.
---

# Rework MR

Reworking **your own** MR against reviewer feedback. glab-only. `SD=~/.claude/skills/rework-mr/scripts`.

## ⛔ Read this first — it is the whole skill

**The user cannot see your tool calls or their output** — those are collapsed. Your chat
message is their *only* window. If a script prints a table and you don't put it in your
message, the user sees nothing. So:

1. When a `threads.py` command prints something, **paste that output verbatim into your
   reply** — markdown table, blockquote and all. Do **not** summarize it, shorten it,
   re-type it, or wrap it in a code fence. Reproduce it exactly.
2. **One topic at a time.** After presenting the current topic, **STOP and wait**. Never
   mention, preview, or recommend anything about other topics. On t24? Don't write "t25/t26…".
3. **Grilling changes NO code — none, ever, not even a trivial one-liner.** You grill *every*
   open topic to an agreed plan first; only then (Phase 3) do you touch code. During grilling
   you agree on *what* to do and record it — you never apply it. **The phrase "OK to apply?"
   is banned here** — it invites a yes and you'd wrongly edit. Ask "Agreed?" and, on yes,
   record the plan and move to the next topic. If the user says "just do it / apply now",
   tell them code comes after all topics are planned.

A reply that has no pasted table, touches more than one topic, or edits code is wrong — redo it.

## The opener — your first reply, exactly this

Do the prep silently (it prints nothing the user needs):

```bash
python3 $SD/threads.py sync                        # fetch + reconcile
python3 $SD/threads.py bodies                      # first + last note of each open thread
python3 $SD/threads.py set <t> --summary "…"       # one concise line per open topic (user's language)
python3 $SD/threads.py merge <into> <other...>     # only if two threads raise the SAME point
```

**Classify status semantically — don't trust who spoke last.** A thread defaults to `open`
(work for you). Mark it `waiting` **only if, reading the notes, YOU already fully addressed
it** — pushed a fix or gave a complete answer and it genuinely needs only the reviewer now.
If your last note merely acknowledged or refined the point ("stimmt", "guter Punkt", "mach
ich") with the work still to do, it stays `open`. `bodies` shows your last note so you can tell.

```bash
python3 $SD/threads.py set <t> --state waiting     # only for a truly-addressed thread
```

**Now research the first open topic — silently, before `present`.** `bodies` already gave you
its reviewer comment, so you can read the code and work out the trade-off now. Do this first
so that when you run `present` it is the **last** thing in your context, right before you type
your reply — this is what stops the table getting dropped.

Then, as your **final** action before replying, run `present`:

```bash
python3 $SD/threads.py present      # run LAST — its output must lead your reply
```

`present` outputs the overview table + a separator + the first open topic's reviewer comment.
Your reply is built in this exact order:

1. **The entire `present` output, verbatim, as the very first thing** — MR title line, GFM
   table, separator, blockquoted comment and all. Nothing precedes it.
2. Then, for that one topic only: **2–4 lines** of research (what the code does + the real
   trade-off, citing `file:line`; plain prose, no "Code (…):" prefix).
3. Then: **trivial** → show the concrete change (fenced code block, as illustration of the
   plan — you are NOT applying it) and ask **"Agreed?"**; **non-trivial** → alternatives + a
   recommendation + **one** question.

Then **STOP**. No code edits. Nothing about the other topics.

**Postcondition — check before sending.** Your message's first line must be the `present` MR
title line (`**MR !…**`) and the table must follow. If your draft opens with your own research
prose instead, you dropped the table — **redo it, table first.** (This is the exact failure the
skill exists to prevent: `present` succeeds, then research tool calls push it out of mind and
the reply starts with prose.)

The output is markdown the chat renders (bold title, GFM table, `code` locations, blockquoted
comment). Status: `○ open` (your turn — default) · `◐ waiting` (you fully addressed it; only
the reviewer's action is left — set semantically) · `● done` (reviewer resolved).

## Resuming — topics already planned

Plans persist in the state file across sessions. **Before grilling, check for existing
decisions:**

```bash
python3 $SD/threads.py plans
```

If open topics already carry a decision/plan (a prior grilling session, maybe after a
`git` revert or a restart), **do not re-grill them** — tell the user what's already planned
and go **straight to Phase 3**, implementing them one at a time. Grill only the open topics
that have *no* plan yet. Re-confirm a plan only if the reviewer's ask changed since.

## Next topics

Once the user agrees on the current topic, **record the plan (no code yet)** and open the
next one. Same rule as the opener: research the next topic silently first, then run `quote`
**last**, and lead your reply with its verbatim output before any prose.

```bash
python3 $SD/threads.py set <t> --decision "…" --plan "…"
python3 $SD/threads.py quote <next-t>      # run LAST — paste its output verbatim, first thing in your reply
```

**Postcondition:** the reply must open with the `quote` comment block, not your research prose.
If it opens with prose, you dropped the comment — redo it.

Outcomes: **fix** · **reply-only** (reviewer wrong / no improvement) · **push-back** ·
**question** (just answer, with a snippet / concrete example). Keep a TODO item per topic.
**Grill every open topic to a plan before writing any code.** Enter Phase 3 only then.

## Status-only requests

"Where do the threads stand" / "what's left" / "what do I need to work on" → just paste one
table, no grilling:

```bash
python3 $SD/threads.py sync        # overview (+ --all for resolved rows)
python3 $SD/threads.py todo        # only what needs you
```

## Phase 3 — Implement, strictly ONE topic at a time

As disciplined as the grilling loop. Full mechanics in [REFERENCE.md](REFERENCE.md).

**Hard rules — the previous version violated these:**

- Take **one** topic *all the way* — change → diff → ACK → fixup → push → diff URL → reply —
  **before you touch the next topic's code.** Never edit a second topic while one is in
  flight. Each thread gets its own push and its own `Fixed: <url>`.
- **Never batch topics into one diff/push on your own.** Consolidating several (e.g. all the
  "remove redundant comment" fixes) into a single diff is allowed **only when the user
  explicitly asks for it** — you propose, they decide.
- **Two stops per topic, each needs an explicit ACK:** after showing the diff (before *any*
  commit or push), and after drafting the reply (before posting/copying). Never commit,
  push, or post without that ACK.

Per topic:

1. Bug/problem → **failing test first** (`tdd` skill), then fix. (reply-only / push-back /
   question → skip to 6.) Clean code for the *merged* result — no "changed from before"
   comments, no iteration leftovers.
2. Light QA (unit/lint). Show the user `git diff`, **and state how it will be integrated** so
   the ACK is informed: blame the changed hunks *now* and name the target(s) —
   `→ fixup into <sha> ("<subject>")`, one per introducing commit (several if the change spans
   commits). **Fixup is the default; a new commit is the rare exception** — only for a
   fix/refactor to code the branch did *not* add (blame older than the branch point), as a
   separate real commit *before* the fixups; call that out explicitly. Ask **"ACK to fix up
   and push?"** — never just "commit" (that reads as a new commit). **STOP — wait for ACK.**
3. On ACK: capture the pre-push baseline (`diff-url.py baseline` → `set <t> --start-sha`),
   then `git commit --fixup=<sha>` for each named target.
4. `git rebase --autosquash`, **full QA** (hard gate), `git push --force-with-lease --force-if-includes`.
5. Topic diff URL: `diff-url.py url --start-sha <stored>` (never a commit URL — force-push
   rots it) → `set <t> --diff-url`.
6. Draft the reply (rules below). **STOP — wait for ACK**, then post via `glab` or copy via
   `clip.sh`. Once addressed, mark it: `set <t> --state waiting` (now genuinely waiting on the
   reviewer; a later reviewer note auto-clears it back to `open`).
7. Only now, the next topic.

## Reply draft rules

- **Language = the thread's language** (often German even in an English session).
- GitLab markdown, code in fences, identifiers in `backticks`, **no headings**, bullets only if they help. Short but concrete.
- Fixes: lead with `Fixed: <diff-url>`; explain only when needed (didn't follow exactly / did more / was complex).
- reply-only / push-back: explain the reasoning. question: answer with a snippet or concrete example.

## Prerequisites

`glab` authenticated; run on (or pass `--iid N` for) the MR branch. `python3`; macOS for `clip.sh`.
`threads.py` subcommands: sync·todo·present·bodies·plans·quote·set·merge·path.
`diff-url.py` (baseline·url), `clip.sh`. Run any with `-h`.
