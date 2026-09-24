---
name: rework-mr
description: Work through the review feedback on a GitLab MR that YOU authored — retrieve the open discussion threads, discuss each topic to a plan (recommendation-first for trivial ones, grilling-style for complex ones), then fix, fixup-and-push, and draft a thread reply per topic. Also shows a status table of the open threads — a full overview or just what needs you — on demand. This is for reworking your own MR in response to reviewers, NOT for reviewing someone else's MR (use review-branch for that). Use when the user says "rework my MR", "address the review comments", "show me the open GitLab threads and grill me on them", "work through the MR feedback", "status of the MR / where do the review threads stand", or invokes /rework-mr.
---

# Rework MR

Reworking **your own** MR against reviewer feedback. glab-only. `SD=~/.claude/skills/rework-mr/scripts`.

## ⛔ Read this first — it is the whole skill

**Tool-output visibility varies by client and user settings.** Your chat message must stand
on its own: the user must not have to expand or reveal a tool call to see the table, code,
comment, diff, or question they are being asked to act on. So:

1. When a `threads.py` command prints something, **paste that output verbatim into your
   reply** — markdown table, blockquote and all. Do **not** summarize it, shorten it,
   re-type it, or wrap it in a code fence. Reproduce it exactly.
   **Run a user-facing rendering command in its own tool call.** Never chain setup before
   `present`/`todo`/`quote`/`diff-view.sh`/`reply-view`/`change-view`: setup output would
   then become part of the same tool result and can leak into the pasted block. Finish
   setup first, then run the rendering command alone as the final action before replying.
2. **One topic at a time.** After presenting the current topic, **STOP and wait**. Never
   mention, preview, or recommend anything about other topics. On t24? Don't write "t25/t26…".
3. **Never re-paste a topic's context while you sit on that topic.** Its header, the code the
   comment is anchored to and the thread are pasted when the topic comes up (`present`, `quote`)
   and again when its reply block first appears (`reply-view`) — and not once more after that.
   While the user keeps you on it — a shorter draft, a different tone, another argument —
   **re-show only what changed**: `reply-view <t> --refine` prints the reworded draft, its
   thread URL and the action prompt, and leaves the context out. Code and a thread the user is
   still looking at bury the one thing they asked to see. **`--refine` is safe to pass whenever
   you are re-showing** — if the thread or the code moved since (a reviewer note a `sync` picked
   up, a re-anchored comment), or the topic was never shown in full, it renders in full anyway
   and says why. Rule 1 is unchanged: paste whatever the command you ran printed, in full.
4. **Grilling changes NO code — none, ever, not even a trivial one-liner.** You grill *every*
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
python3 $SD/threads.py set <t> --summary "…"       # one short ENGLISH line per unfinished topic
python3 $SD/threads.py merge <into> <other...>     # only if two threads raise the SAME point
```

**Every topic that is not `done` needs a summary, and it is yours to write.** It is the
one-line title the user reads in the table and in every topic heading, so it has to say
*what the point is* — not the first 70 characters of the reviewer's comment, which is what
they get when you skip this (and which, for a comment that opens with a ```suggestion
block, is unreadable). `bodies` gives you every open thread's text in one call; summarise
from that. **In English, always** — the reviewer's language governs one thing only, the body
of a reply you post into their thread. A topic with no summary is marked `✍️ needs summary`
in the table and a summary that reads as German is called out under it; the `Stop` hook
refuses to let either reach the user, so this is not optional.

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
so that when you run `present` it is the **last** thing you read before you type your reply —
this is what stops the table getting dropped.

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
semantically) · `● done` (reviewer resolved). Settable with `--state`: `open` and
`waiting` only — `reply-pending`/`done` are derived (`sync` computes them).

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
code the comment is anchored to, then the reviewer's note and every reply on it — not your
research prose. If it opens with prose, you dropped the comment — redo it. (A `Stop` hook
enforces this too.)

**And it must contain nothing beyond that block and a short recommendation**: no code change,
no diff, no fixup target, no ACK request. Opening a topic and fixing it in one turn — which is
what "move to the next topic" invites after a `n` — hands the user a change to approve for a
comment they have not read yet. Implementation starts only once they have seen the comment and
agreed a plan (step 1).

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

A table is also where a missing summary shows up. If rows come back marked
`✍️ needs summary` — a thread that arrived since your last sync, or a session that never
wrote them — author those first (`bodies`, then `set <t> --summary "…"`), re-run, and paste
that. The hook blocks the table otherwise, and rightly: a status answer whose Summary column
is somebody else's truncated sentence tells the user nothing.

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
   verbatim as the rest of your message, then STOP**: first a short summary — **what the
   reviewer asked for, by name, and what you did about it** ("Robin asked for X; rewrote
   `Foo.bar()` to … and added two tests") — then the fixup target(s) in 1–2 lines, then the
   diff-view block (what changed + the script's own closing ACK question, whose exact
   wording names the answers other than an ACK — paste it, never retype it). Never
   just "commit" (that reads as a new commit).
   The summary is prose about the *reasoning*, never a retelling of the diffstat — the block
   below it already says which files and how many lines. Keep it to the size of the change:
   where the fix is short and exactly what was asked, one line saying so ("Applied exactly as
   suggested.") is the whole summary, and for a reply-only topic there is nothing to summarise.
   **The diff itself goes to a diff viewer in its own tmux window**, and the block names the
   window; the user reads it there. It falls back to an inline fenced diff on its own when
   there is no viewer to use — you do not choose between them and do not mention the
   mechanism, you just paste what the command printed.
   You may anchor **at most 3** short notes in that window with
   `--note FILE:LINE:TEXT` — and only where the change **deviates from the agreed plan, does
   more than was asked, or is not obvious from the diff.** Default to none: a note per hunk
   buries the diff it is annotating. The fixup target goes in your prose, not in a note.
   (The repeated bug here was showing the diff, then
   blaming/naming targets afterward — the `git blame` call in between pushed the diff out of
   mind by the time the message was written. A `Stop` hook enforces the diff-view block actually
   reaching the user, the same way it does for `present`/`quote`/`reply-view`/`change-preview` —
   and it blocks an ACK request that has no `diff-view.sh` run behind it at all.)
3. On ACK — **first, always, before anything else: `python3 $SD/threads.py hunk-notes`.**
   The user reviews the diff in the viewer window and may answer by leaving notes on the
   lines themselves instead of typing them at you. **Any output at all means do NOT push:**
   answer each note, change what needs changing, and go back to step 2. No output is the
   common case and means go ahead. Run it whatever the user typed — "ack", "check the
   notes", anything — so there is nothing for them to remember.
   **Reading a note takes it out of the window** — it has now been asked and answered, and
   leaving it there would both re-report it forever (blocking every later push) and strand
   it on a line the fix has since moved. So **that output is your only copy**: address every
   line of it in this turn. The window is likewise cleared of *your* notes each time a diff
   goes up, so what is anchored in it always belongs to the diff currently loaded.
   Then capture the pre-push baseline (`diff-url.py baseline` → `set <t> --start-sha`),
   and `git commit --fixup=<sha>` for each named target.
4. `git rebase --autosquash`, **full QA** (hard gate), `git push --force-with-lease --force-if-includes`.
   Then `python3 $SD/threads.py hunk-close` — the rebase left the tree clean, so the viewer
   window is now showing a diff that no longer exists while the conversation moves on. It
   closes itself silently; the only time it says anything is when a note arrived in the
   meantime, and then the window stays open and that note is the next thing to deal with.
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
      This full block is the topic's **first** reply view. Every later one on the same topic —
      after the user asks for a shorter or differently-argued draft — is
      `reply-view <t> --refine`, which prints the draft, the thread URL and the prompt and
      nothing else (rule 3 at the top); same paste discipline, same `Stop` hook. If it comes
      back with the whole block plus a line saying the context changed, that is it telling you
      the thread or the code moved — paste that, it is what the user needs to see.
   c. **Wait for the user, then interpret their reply:**
      - **`c`** (or "copy") → copy to clipboard.
      - **`p`** (or "post") → post it (the one allowed write).
      - **`n`** (or "next") → the topic is already handled (they replied by hand, or it's
        resolved): mark it `set <t> --state waiting`, then open the next topic **the way
        every topic is opened — `quote <next-t>`, pasted as your whole message, then STOP**
        (see "Next topics"). "Next" names the next *comment to show*, never the next fix to
        start: a topic whose comment the user has not been shown cannot be discussed, let
        alone agreed, and a diff arriving before it is a change they never asked for.
      - **anything else** → they're discussing. There is no `d` command: treat any non-`c`/`p`/`n`
        message as feedback — engage with it, refine the draft, store it again with
        `set <t> --reply -`, re-run **`reply-view <t> --refine`**, paste that block. Never post
        unprompted. `--refine` is what keeps an iteration from re-pasting the topic's context,
        which the user already has on screen: answer in a line or two if the feedback needs an
        answer, then the block — nothing else.
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
7. Only now, the next topic — and it starts at `quote`, never at code (see "Next topics").

## Reply draft rules

- **Language: the thread's, for the reply BODY only** (often German even in an English
  session). **Everything else is English, always** — every `--summary`, so the table and every
  topic heading, the scaffolding around the draft (labels, the thread-URL line, the action
  prompt) and your own prose. The one other non-English text is a *quoted* note, which the
  scripts reproduce verbatim: never translate a reviewer's words while showing them back.
  `threads.py` says so itself when a summary reads as German, and the paste gate will not let
  that view reach the user. (review-mr states the same rule the same way.)
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

`glab` authenticated; run on (or pass `--iid N` for) the MR branch. `python3`. (`clip.sh` copies to the clipboard via macOS `pbcopy`; where that is missing the
copy is skipped — the draft is in the chat message and `p` posts through `glab` regardless.)
`threads.py` subcommands: sync·todo·present·bodies·plans·quote·url·reply·reply-view [--refine]·set·merge·path·hunk-notes·hunk-close
(plus `change-view`/`diff-view`, the bodies of the two .sh views below).
**Optional: `hunk` (a terminal diff viewer) inside `tmux`.** With both, a topic's working
diff opens in a tmux window of its own — placed directly right of the one you are talking
in — instead of filling the chat; `hunk-notes` reads back what the user wrote on the lines
there, and `hunk-close` closes the window once the push lands. Without either, `diff-view.sh`
prints the diff inline exactly as before — nothing to configure, and nothing else changes.
`diff-url.py` (baseline·url), `clip.sh` (guards + copies), `guard-reply.sh` (topic-handle gate
for the clipboard path),
`change-view` (trivial-topic change illustration piped in, one paste — write the change with
its own ```diff fence and it stays highlighted; `--for <path>` sets the language for a non-diff
snippet; `change-preview.sh` is the same block from a file), `diff-view.sh` (working diff
before the fixup+push ACK, one paste). Run any with `-h`.
**Claude Code setup:** register the shared `Stop` hook (`hooks/paste-gate.py` + this skill's
`scripts/paste-gates.json`) in `settings.json` — see [README.md](README.md). OpenCode needs no
hook; script output stays clean there, but the exact-output rules above still apply because
tool details can be collapsed or hidden.
