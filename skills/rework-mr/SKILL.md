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
   table, separator, the fenced code the comment sits on, blockquoted comment and all.
   Nothing precedes it.
2. Then, for that one topic only: **2–4 lines** of research (what the code does + the real
   trade-off, citing `file:line`; plain prose, no "Code (…):" prefix).
3. Then: **trivial** → illustrate the change (**not applying it**) via `change-view`, same
   one-paste discipline as `present`/`quote`/`reply-view` (the repeated bug here was saying
   "Trivial. Change:" and then never actually showing it — a Read or other tool call in between
   pushed it out of mind):
   ```bash
   python3 $SD/threads.py change-view <t> <<'CHANGE_EOF'
   <the concrete change, verbatim — a diff or before/after snippet, illustration only>
   CHANGE_EOF
   ```
   Piped, **not written to a file**: a heredoc into a file under `~/.claude/` trips Claude
   Code's protected-path prompt on every topic, and the illustration is one-shot — nothing
   reads it again. Add `--for <path>` after `<t>` to set the fence language for a snippet
   that isn't a diff. (`change-preview.sh <t> <file> [--for <path>]` renders the same block
   from a file, if you ever want one.)
   **Paste its ENTIRE output verbatim as the rest of your message, then STOP** — the fenced
   illustration and the final `Agreed?` are one block; do not describe the change instead of
   showing it, and do not add your own "Agreed?" after it. (A `Stop` hook enforces this — end
   the turn without the pasted block and it forces a redo.)
   **non-trivial** → alternatives + a recommendation + **one** question (no script needed; this
   is discussion, not something the model has to reproduce verbatim).

Then **STOP**. No code edits. Nothing about the other topics.

**Postcondition — check before sending.** Your message's first line must be the `present` MR
title line (`**MR !…**`) and the table must follow. If your draft opens with your own research
prose instead, you dropped the table — **redo it, table first.** (This is the exact failure the
skill exists to prevent: `present` succeeds, then research tool calls push it out of mind and
the reply starts with prose. A `Stop` hook enforces this — end the turn without the pasted
`present` output and it forces a redo — so just paste it.)

The output is markdown the chat renders (bold title, GFM table, `code` locations, blockquoted
comment). Status: `✎ reply-pending` (code already fixed **and pushed** — only the thread reply
is left; derived from a stored `diff_url`) · `○ open` (your turn, still needs the fix —
default) · `◐ waiting` (you fully addressed it; only the reviewer's action is left — set
semantically) · `● done` (reviewer resolved).

## Resuming — topics already planned

Plans persist in the state file across sessions. **Before grilling, check for existing
decisions:**

```bash
python3 $SD/threads.py plans
```

If open topics already carry a decision/plan (a prior grilling session, maybe after a
`git` revert or a restart), **do not re-grill them** — tell the user what's already planned
and go **straight to Phase 3**, one topic at a time. Grill only the open topics that have
*no* plan yet. Re-confirm a plan only if the reviewer's ask changed since.

**Route each planned topic by its status — don't blindly re-implement.** `plans` (and the
overview) mark a topic `✎ reply-pending` when its code was already fixed **and pushed** (a
`diff_url` is stored). **A `reply-pending` topic is DONE code-wise** — skip Phase 3 steps 1–5
and go straight to the **reply step (6)** for it: show the thread, draft the reply, show the
URL. **Never re-implement it** (you'd duplicate a pushed change). Only `○ open` planned topics
(no `diff_url`) get implemented from step 1. If a `reply-pending` topic's diff looks wrong or
the reviewer re-commented asking for more, confirm with the user before touching code.

## Next topics

Once the user agrees on the current topic, **record the plan (no code yet)** and open the
next one. Same rule as the opener: research the next topic silently first, then run `quote`
**last**, and lead your reply with its verbatim output before any prose.

```bash
python3 $SD/threads.py set <t> --decision "…" --plan "…"
python3 $SD/threads.py quote <next-t>      # run LAST — paste its output verbatim, first thing in your reply
```

**Postcondition:** the reply must open with the `quote` block — the topic header, the fenced
code the comment is anchored to, then the reviewer's note — not your research prose. If it opens
with prose, you dropped the comment — redo it. (A `Stop` hook enforces this too.)

`quote` renders the code from the exact blob the comment hangs on, so your research can cite what
the user is actually looking at. It follows the reviewer's own selection: a multi-line comment
("lines +12 to +22") shows that whole span marked `┃`, a single-line one shows `►` on the line with
a wider window around it. Code inside the reviewer's note — a ```suggestion block or an indented
snippet — is lifted out of the blockquote so it stays highlighted; a suggestion carries a caption
naming the lines it replaces, which is the offer you are accepting or declining. If it says the working tree has since diverged, the lines shown are the
reviewer's version, not the current file — say so rather than reasoning past it.

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

Paste the table verbatim, same rule as everywhere else. **`todo`'s output is Stop-hook enforced
like `present`/`quote`/`reply-view`/`change-preview`/`diff-view`; `sync`'s here is not** — `sync`
also runs silently in the opener's prep, so gating it there would false-block on that unrelated,
intentionally-unshown call. Prefer `todo` for a status-only reply when either works.

## Phase 3 — Implement, strictly ONE topic at a time

As disciplined as the grilling loop. Full mechanics in [REFERENCE.md](REFERENCE.md).

**Hard rules — the previous version violated these:**

- Take **one** topic *all the way* — change → blame → diff → ACK → fixup → push → diff URL →
  reply — **before you touch the next topic's code.** Never edit a second topic while one is in
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
2. Light QA (unit/lint) — silently. **Then, silently, blame the changed hunks** and name the
   target(s) — `→ fixup into <sha> ("<subject>")`, one per introducing commit (several if the
   change spans commits). **Fixup is the default; a new commit is the rare exception** — only
   for a fix/refactor to code the branch did *not* add (blame older than the branch point), as
   a separate real commit *before* the fixups; call that out explicitly. Only THEN, as your
   **final** action before replying, run `diff-view.sh <t>` and **paste its ENTIRE output
   verbatim as the rest of your message, then STOP**: state the fixup target(s) in 1–2 lines,
   followed by the diff-view block (the diff + the "ACK to fix up and push?" question). Never
   just "commit" (that reads as a new commit). (The repeated bug here was showing the diff, then
   blaming/naming targets afterward — the `git blame` call in between pushed the diff out of
   mind by the time the message was written. A `Stop` hook enforces the diff-view block actually
   reaching the user, the same way it does for `present`/`quote`/`reply-view`/`change-preview` —
   and it blocks an ACK request that has no `diff-view.sh` run behind it at all.)
3. On ACK: capture the pre-push baseline (`diff-url.py baseline` → `set <t> --start-sha`),
   then `git commit --fixup=<sha>` for each named target.
4. `git rebase --autosquash`, **full QA** (hard gate), `git push --force-with-lease --force-if-includes`.
5. Topic diff URL: `diff-url.py url --start-sha <stored>` (never a commit URL — force-push
   rots it) → `set <t> --diff-url`.
6. Reply — **one topic at a time; the `c`/`p`/`n` prompt is a hard STOP.** Do all
   of this for the current topic before touching the next. The thread + draft + URL + prompt are
   shown via **one** script command (`reply-view`) whose output you paste as your whole message —
   so none of them can be dropped. The repeated bug was hand-assembling these and forgetting the
   thread or the draft, or jumping to an interactive menu that swallowed them; a single pasted
   block with the prompt baked in fixes that.
   a. Compose the reply body (rules below) and write it — **raw, body only, no `>` prefixes** —
      to the draft file with a **quoted heredoc** (not the Write tool: it can't overwrite a file
      you haven't Read this session, so it fails on resume):
      ```bash
      python3 $SD/threads.py set <t> --reply - <<'REPLY_EOF'
      <the reply body, verbatim — NO leading "> " on any line>
      REPLY_EOF
      ```
      Stored in the state file, not in a scratch `.md`: a heredoc into `~/.claude/` prompts
      for a protected-path write on every topic, and the draft is per-topic state like the
      plan. The **quoted** heredoc (`<<'REPLY_EOF'`) is what keeps backticks and `$` in the
      body from being expanded by the shell — never pass a multi-line body as a
      `--reply "…"` argument.
   b. Run `threads.py reply-view <t>` and **paste its ENTIRE output verbatim as your whole
      message, then STOP** — it is the whole thread (original + every reply) + your drafted reply
      (blockquoted) + the thread URL + the `c`/`p`/`n` prompt, all in one block. The prompt is the
      last line, so **do not** add an `AskUserQuestion` menu or any other prompt — pasting the
      block *is* the ask. It also **refuses a draft with an internal topic handle** (`t<number>`)
      — if it errors, reword per the draft rules and re-run.
      **Postcondition:** your message *is* the `reply-view` output — it opens with the topic
      header and the fenced code, carries the reviewer's blockquoted note, has the
      `**Draft reply:**` block, and ends with the `c`/`p`/`n` prompt line.
      If any is missing, you dropped it — re-run and paste. Never replace it with a short stub
      like "t7 — reply ready", even when moving fast across topics. (A `Stop` hook enforces this —
      end the turn without the block and it forces a redo — so just paste it.)
   c. **Wait for the user, then interpret their reply:**
      - **`c`** (or "copy") → copy to clipboard.
      - **`p`** (or "post") → post it (the one allowed write).
      - **`n`** (or "next") → the topic is already handled (they replied by hand, or it's
        resolved): mark it `set <t> --state waiting` and move straight to the next topic.
      - **anything else** → they're discussing. There is no `d` command: treat any non-`c`/`p`/`n`
        message as feedback — engage with it, refine the draft, store it again with
        `set <t> --reply -`, re-run `reply-view`, paste the new block. Never post unprompted.
      ```bash
      python3 $SD/threads.py reply <t> | $SD/clip.sh     # c — Copy
      # p — Post (the one allowed write; <discussion_id> = the topic's thread_ids[0]):
      body=$(python3 $SD/threads.py reply <t>) && printf '%s\n' "$body" | \
      glab api projects/<enc>/merge_requests/<iid>/discussions/<discussion_id>/notes \
        -X POST -F body=@-
      ```
      `reply` prints the body only and **refuses one carrying an internal topic handle**
      (`t5`…), so the guard cannot be skipped. The `&&` matters: it stops a refused draft
      from reaching `glab` with an empty body.
   Only **Post** (or the user confirming they pasted it) counts as addressed — then mark it:
   `set <t> --state waiting` (now genuinely waiting on the reviewer; a later reviewer note
   auto-clears it back to `open`).
7. Only now, the next topic.

## Reply draft rules

- **Language = the thread's language** (often German even in an English session). The
  scaffolding *around* the draft (labels, the thread-URL line, the action prompt) stays in the
  **session language** — only the comment body uses the thread's language.
- **NEVER put this skill's internal topic handles (`t5`, `t6`, `t10`, …) in a draft** — they
  are the skill's own bookkeeping ids and mean nothing to a GitLab reader. To reference another
  discussion, link its thread URL (`threads.py url <other-t>`) or describe it in plain words
  ("in einem separaten Thread"), never "in t6". **Self-scan before you show the draft:** if the
  body contains any `t<number>`, rewrite it. This is enforced in three places — `set
  <t> --reply` refuses to store one, `reply <t>` refuses to print one (so the post pipeline
  gets nothing), and `clip.sh` runs `guard-reply.sh` — so an un-reworded draft cannot be
  copied or posted.
- GitLab markdown, code in fences, identifiers in `backticks`, **no headings**, bullets only if they help. Short but concrete.
- Fixes: lead with `Fixed: <diff-url>`; explain only when needed (didn't follow exactly / did more / was complex).
- reply-only / push-back: explain the reasoning. question: answer with a snippet or concrete example.

## Prerequisites

`glab` authenticated; run on (or pass `--iid N` for) the MR branch. `python3`; macOS for `clip.sh`.
`threads.py` subcommands: sync·todo·present·bodies·plans·quote·url·reply·reply-view·set·merge·path
(plus `change-view`/`diff-view`, the bodies of the two .sh views below).
`diff-url.py` (baseline·url), `clip.sh` (guards + copies), `guard-reply.sh` (topic-handle gate
for the clipboard path),
`change-view` (trivial-topic change illustration piped in, one paste — write the change with
its own ```diff fence and it stays highlighted; `--for <path>` sets the language for a non-diff
snippet; `change-preview.sh` is the same block from a file), `diff-view.sh` (working diff
before the fixup+push ACK, one paste). Run any with `-h`.
**Setup:** `present`/`todo`/`quote`/`diff-view.sh`/`reply-view`/`change-view` all need the
shared `Stop` hook (`hooks/paste-gate.py` + this skill's `scripts/paste-gates.json`) registered
in `settings.json` — see [README.md](README.md); without it their output often won't reach the
user.
