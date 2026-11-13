---
name: review-mr
description: >-
  Review someone ELSE's GitLab MR end to end — optionally generate an explainer first (explain-branch), seed findings with review-branch, curate them with you one topic at a time, and draft concise review comments you post yourself. Then run a persistent re-review loop: on each check it reconciles the live threads (author replies, resolutions) and branch pushes, flags what the author addressed, and gates every close behind YOUR ack. Never posts or resolves on its own: you post, in the UI or with the Post button where the review-ui mod is loaded, for full tone control. This is for reviewing another person's MR, NOT reworking your own (use rework-mr for that) and NOT a local-only critique (use review-branch for that). Use when the user says "review MR !123", "review !123", "review this MR", or invokes /review-mr.
---

# Review MR

Reviewing **someone else's** GitLab MR. glab-only; nothing reaches GitLab but what the user posts.
`SD=~/.claude/skills/review-mr/scripts`.

## ⛔ Read this first — it is the whole skill

**Tool-output visibility varies by client and user settings.** Your chat message must stand
on its own: the user must not have to expand or reveal a tool call to see the table, code,
comment, or question they are being asked to act on. So:

**A card is a paste already made.** When a view's output ends with the line
`[review-ui] Shown to the user as a card.`, the user sees that view whole, drawn under the
tool call: running the command was the paste. Wherever the rules below say *paste*, read
*run*. Your reply then carries only your judgment and the question, at most 3 lines per
topic, longer only when the user asks why: no view, and no part of one, in your message.
The overview table lives in the user's sidebar then, so an opener without one is the
expected opener; it is what the rules below mean by a pasted table. Output without that line
is pasted as below.

**Buttons answer your question.** Where cards are drawn, the newest card carries the buttons
of the current step (Skip explainer, Generate explainer; Comment, Drop; Post, Copy; Accept +
resolve, Later). A press reaches you as a `[review-ui] The user pressed a button: …` note:
that is their decision, made. Carry on from it. **Never mention the buttons**: not that there
are any, not what they are called, not what one does, not where they sit ("pick Skip or
Generate on the card", "press Post when you're happy"). The user sees them; a sentence about
them is noise, and the one thing in your reply they already know. Where you would ask the
question a button answers, end on your recommendation instead, or on what you just did. Show a
topic with the `show_topic` tool where you have it, in place of `quote <t> --focus`.

**Card text is markdown.** Whatever you store for a card to show (`--why`, `--text`) has every
identifier, path, value and `file:line` in backticks, so the card draws it as code.

**A card ends your reply.** The user decides on the card, so it is the last thing they read.
Whatever you have to say (the answer to the user's question, what you did and why) goes
before the call that shows the card, and the turn ends on the card. Text after it lands below
the user's decision, and a retelling of the card there only makes them scroll. Where a rule
below orders a pasted view first and your reasoning after it, a card turns that round: your
reasoning, then the card.

0. **Your first reply in a session is an opener, pasted whole.** Before anything else —
   no "picking up where we left off", no summary of what is left, no question:

   ```bash
   python3 $SD/findings.py resume --iid <n>    # state exists (it syncs internally)
   python3 $SD/findings.py present --iid <n>   # fresh review, after seeding
   ```

   Paste that output verbatim as the start of the reply. It carries the MR header, the
   overview table, the pushes since your baseline and the current topic —
   i.e. everything they need to orient. **If your reply names a topic (`t2`) without a
   pasted table above it, it is wrong: the user has no idea what `t2` is.** Do not run
   `sync`/`head`/`bodies` first and narrate from those; `resume` already includes them.
1. When a `findings.py` command prints a table/quote, **paste it verbatim into your
   reply** — table, blockquote and all. Never summarize, re-type, or wrap it in a
   code fence. Any heading you write above an overview table **must carry `MR !<num>`**
   (the table's own `MR !<num> — <title>` header already does — don't drop it); if you'd
   write no heading, the full `MR !<num>: <title>` is a fine standalone one.
   **A terseness directive does not override this.** A global "be brief / drop filler"
   instruction (a caveman-style output mode, a house style, an earlier "keep it short")
   applies to **your own prose**, never to pasted tool output. Compress your commentary to
   one line if you like — but the table, the quote and the code block go in whole. Dropping
   them to save space removes the only thing the user can act on.

   **Run a user-facing rendering command in its own tool call.** Never chain setup before
   `present`/`resume`/`todo`/`quote`/`updates`/`diff`: setup output would then become part of
   the same tool result and can leak into the pasted block. Finish setup first, then run the
   rendering command alone as the final action before replying.

   **This holds even when the output looks wrong to you.** If a rendered state
   contradicts what you expected (or the state file), paste the output anyway and add one
   line naming the discrepancy — never replace the table with your own prose, and never
   tell the user the tooling is stale or buggy unless you have actually read the code that
   produces the value. `topic_status()` derives state from GitLab plus your overlay, so a
   row that surprises you is usually a rule you have not read, not a bug.
2. **One topic at a time.** Present the current topic, then **STOP and wait**. Never
   mention, preview, or recommend anything about other topics.
3. **Never re-paste a topic's context while you sit on that topic.** The code the draft
   comment is about — or, for a follow-up, the notes of the thread it replies into — is
   pasted when the topic comes up, and not again after that. While the user keeps you on
   it — a shorter comment, a different tone, another argument — **re-show only what
   changed**: `--refine` (on `quote <t>`, and on the `set <t> --draft` echo) prints the
   header, the draft and its label, without the context. What the user is still looking at
   buries the one thing they asked to see. **`--refine` is safe to pass whenever you are
   re-showing** — if the code moved (an anchor fix) or the author added a note since, or the
   topic was never shown in full, it renders in full anyway and says why. Rule 1 is unchanged:
   paste whatever the command you ran printed, in full.
4. **You never write to GitLab.** No comments, no resolves, no approvals: the user posts,
   in the UI or with the Post button on a draft's card, so the tone and the sign-off
   stay theirs. You *read* (discussions, branch tip, pushes) and *draft* text. No
   `glab … -X POST` and no `findings.py post`, ever: that command is the button's.
5. **No code changes, ever.** You review; you don't fix. (The author fixes.)

A reply with no pasted table, that touches more than one topic, or that writes anything
to GitLab is wrong — redo it.

**Name people by first name, the one the scripts print.** GitLab names look like
`Lastname, Firstname - ID`; the scripts shorten them: `set-head` prints `MR author: <first
name>` before you write a single finding, and the table header carries it too. Use exactly
that name, in your words and in what you store (`why`, drafts); never a name you made up
or took from an example, never the raw GitLab form or the bare account id.

## Setup — do this silently, then present

**You're given the MR number** (`534` from `/review-mr 534`) — **use it as `--iid` on every
`findings.py` call** but `lang` (the language is the repo's, for every MR) and don't rely on
branch inference: you may be launched in a detached or
unrelated worktree, and a bare `glab mr view` / `findings.py` there resolves the wrong thing.

**Run setup in the order written, waiting for each step before starting the next.** Several
`findings.py` commands read and replace the same state file. In particular, checkout →
`set-head` → `import` is a dependency chain on a fresh review, not three parallel tool calls:
parallel `set-head` and `import` can each read the old state and the last writer silently
erase the other's update. Read-only lookups may run together only when no state-writing
command is in flight.

**Prune first.** Each MR gets its **own** review worktree (reviewing several MRs of the same
repo in parallel used to mean they all fought over one shared checkout). Old ones pile up
unless swept, so before touching this MR's own worktree, clean up any *other* one that's done:

```bash
python3 $SD/findings.py prune --iid <n>   # removes an OTHER merged/closed MR's worktree for this repo
```

This only ever deletes the git worktree, never a findings/state file, and only for an MR
GitLab reports merged or closed whose review has lain idle for a day (another session may
still be on it) — `--iid <n>` exempts this run's own MR even if it happens to already qualify. It's silent when it finds nothing; if it prints a line, mention it in passing.

Then find (or create) **this MR's own** worktree:

```bash
python3 $SD/findings.py worktree --iid <n>              # stored path for this MR, if any
git worktree list                                       # else look for one that already looks right
python3 $SD/findings.py worktree --iid <n> --set <path> # persist your choice (ask once if ambiguous / none)
```

If none exists, offer to create one (`git worktree add --detach <path>` — no branch); name it
so it's recognizable among several at a glance (e.g. fold the MR number into the path) — you
may have more than one checked out at once now. **Then `cd` into the worktree and run
everything — glab *and* findings.py — from there.** Both auto-resolve the project from that
worktree's `origin`, so you never name it by hand. **Do NOT guess the repo** (no
`glab mr view -R "$(glab repo view …)"` — it misfires into 404s); and `findings.py` takes
`--iid`, never `-R`.

**Now check it out — every run, no exceptions, however the path was obtained.** A stored
worktree path says *where* the checkout is, never *what is in it*: the worktree is shared
between this repo's MRs and left detached on whichever one was reviewed last. Skip this and
you review the previous MR's code while every command still succeeds and every table still
renders. The review worktree is **disposable**, so hard-reset it onto the MR tip. Check out
**detached** — nothing here reads the local branch name (sync/detection run off `--iid` + the
GitLab API), so a detached HEAD keeps your local branch list clean:

```bash
cd <wt>                                     # the review worktree
git fetch origin
git checkout -f --detach "origin/$(python3 $SD/findings.py branch --iid <n>)"   # detached: lands exactly on the tip, no local branch created; -f discards worktree cruft — pause only if <wt> holds work that looks intentionally yours
```

**Take the branch name from `branch --iid <n>`, never from the worktree.** It is the one
value with no local source of truth, and `git branch --show-current` in a detached worktree
answers with the *last* MR's branch — which is a wrong answer that looks like a right one.

Fresh vs resume: a review is resumed once its first pass got as far as importing the
findings. A state file alone does not say so (the explainer's choice and the baseline are
written before them, and a first pass can stop there):

```bash
python3 $SD/findings.py begun --iid <n>   # RESUME or FRESH
```

**FRESH only** — mark the current tip as your baseline so later pushes are measured from here:

```bash
python3 $SD/findings.py set-head --iid <n>  # ⚠️ first run ONLY
```

**RESUME** — do NOT `set-head` now (it would erase the "pushes since last review" detection).
Run the opener and paste it whole; that IS your first reply (see *Resuming* for what to
annotate afterwards):

```bash
python3 $SD/findings.py resume --iid <n>
```

**Explainer (the user's choice, asked first).** **Ask for a prepared one before anything
else** — it costs minutes to build and stays the same document until somebody pushes:

```bash
python3 $SD/findings.py explainer --iid <n>    # a path, or nothing
```

If that printed a path, **spawn nothing** — the explainer already exists for this exact tip,
and `present`/`resume` put its clickable `file://` link at the top of the opener for you, so
there is nothing extra to paste or to mention. If it printed nothing, there is none (a record
that has stopped applying says why on stderr).

If the user said in the invocation (or since) that they want one, generate it; if they said
"no explain(er)", skip it. Otherwise **assess, recommend, and let them decide**:

```bash
python3 $SD/findings.py explainer --iid <n> --assess    # files, lines outside tests, and the share that only moved where it is 20% or more
python3 $SD/findings.py explainer --iid <n> --recommend skip|generate --why "<one English sentence: what the change is>"
```

Read the diff's shape before you recommend (`git diff --stat`, the commit messages, a look at
the largest files). **Lean hard toward `skip`.** An explainer earns its minutes only where a
reader has to build a mental model the code does not hand them: a new subsystem or data flow,
a state machine or protocol, a non-obvious algorithm, an architecture change across modules.
Size alone is never the reason: a refactor that moves code (a high share that only moved), a
rename, a mechanical change, a dependency bump, tests or config, and a small feature in one
place are all `skip`, however many lines they touch.

`--recommend` prints the question as a card. **End your turn on it**, with no text of your
own: where the mod draws, the user answers on the card, and their answer reaches you as a
prompt to carry on from; without the mod, ask them in one line and wait. **An answer the user
types instead is recorded before you go on**, as the card's press would:
`python3 $SD/findings.py explainer --iid <n> --choose skip|generate`. On "generate", launch `explain-branch` as a **background subagent** pointed at the
checked-out worktree, and move on immediately (it opens the HTML when ready). It must only
*read* git (no checkout/fetch — that already happened) so it can't race review-branch.

The user's "generate" **is** their request for this subagent — a general "don't spawn
agents unless asked" rule does not suppress it. If a harness rule genuinely blocks the spawn,
say so in the opener rather than skipping silently.

**When the subagent comes back, record what it wrote** — that is what makes the next session on
this MR free instead of another few minutes:

```bash
python3 $SD/findings.py explainer --iid <n> --set "$FILE_PATH"
```

**Seed the findings.** **Ask for a prepared seed first**, for the same reason as the explainer —
a review-branch run costs a minute or more, and a seed recorded against this tip is the answer
it already gave:

```bash
python3 $SD/findings.py seed --iid <n>    # a path, or nothing
```

If that printed a path, import it and **skip review-branch entirely** — no
`REVIEW_BRANCH_BASE`, no subagent:

```bash
python3 $SD/findings.py import "$(python3 $SD/findings.py seed --iid <n>)" --iid <n>
python3 $SD/findings.py present --iid <n>    # paste verbatim as the top of your reply
```

Otherwise seed it the long way. First pin the diff scope to GitLab's own answer — **never** let
review-branch re-derive it locally here:

```bash
export REVIEW_BRANCH_BASE=$(python3 $SD/findings.py base --iid <n>)
```

review-branch's own base-detection (`git merge-base HEAD origin/main`) is right for a plain
`/review-branch` run on your own branch, but wrong for an MR: it assumes the target is always
`main`/`master` and that the locally known tip of it is fresh. Neither holds in general — an MR
can target another branch, or the local repo's knowledge of the target can sit behind where
this MR actually forked from — and either one silently sweeps unrelated, already-merged commits
into what looks like the MR's own diff. `REVIEW_BRANCH_BASE` makes review-branch use GitLab's
own merge-base verbatim instead.

Then run the `review-branch` skill against the worktree (foreground). It
returns a prioritized critique. Turn each finding into a JSON object and import them on
stdin, in a quoted heredoc: **never write them to a file** (the Write tool shows what it
writes, and the findings stay out of sight until the user's own review is in):

```jsonc
// one object per review-branch finding
[{"kind":"issue","severity":"high","source":"llm","file":"src/x.ts","line":42,
  "summary":"one short line, plain words, ENGLISH — see the language rule below",
  "why":"2–4 English sentences: what goes wrong, when, and why it matters.\n\n**Recommendation:** raise it / drop it, and the fix in a few words"}]
```

`why` is the **analysis** the topic's card shows: a card a button brings up has no turn of
yours beside it, so what the user decides Comment or Drop on is what you stored here. Write it
from the code review-branch read, concrete (`file:line`, the input that breaks it; card text
is markdown, above), and end it on a
`**Recommendation:**` paragraph. `set <t> --why "…"` rewrites it, and so does a stored view of
another reviewer's thread (below).
```bash
python3 $SD/findings.py import - --seed --iid <n> <<'SEED_EOF'   # --seed: a re-run is instant
[…the findings…]
SEED_EOF
python3 $SD/findings.py present --iid <n>    # paste verbatim as the top of your reply
```

⚠️ **`--iid <n>` on these too** — `import`/`present` are as iid-sensitive as any other
call. A bare `cd <wt>` from an earlier command does **not** persist (each shell call resets
cwd to the repo root, usually *your own* branch), so without `--iid` these silently resolve to
the wrong MR and land findings in the wrong state file.

`present` = the overview table + the current topic (below). Then enter Phase 1.

## The user's own review comes first (where cards are drawn)

On a fresh review where the review-ui mod draws, `present` says **Agent review done** and
shows no topic: the user reviews the MR in GitLab on their own first, so your topics do not
steer them, and no topic is current until they are done. The card says so and carries the
explainer link: end the turn on it, with no text of your own. Their press saying they are
done opens the topics by itself where they posted no comments of their own.

**Until they are done, nothing of your findings reaches the chat**: not what one says, not
which is "the bigger one", not how many there are, and not in a line while you work
("importing two findings now", "I found two points; they stay hidden"). From the first
`review-branch` call on, until `own-review` opens the topics, your words in the chat are
about the steps alone ("reading the diff", "seeding"), or there are none. A hint of a
finding steers the review the user is about to do on their own, which is what waiting is
for.

**"My review is done: pull in my comments"** reaches you as their prompt. Then:

```bash
python3 $SD/findings.py own-review done --iid <n>    # syncs; prints their comments not yet on a topic (none: the first topic)
```

Read each of those comments and compare it with the topics **by what it says**; the file and
line are a hint, never the test (one file holds unrelated points, and one point can sit in
two places). **Every** comment gets one of these; `own-review matched` refuses while one is
left:

```bash
# the same point as a topic: the user confirms or declines it with a press
python3 $SD/findings.py propose <discussion_id> --into <t> --summary "<English one-liner>" \
  --why "<what both say, and where they differ>" [--recommend separate|link] --iid <n>
# a point of its own: a topic at once, no question
python3 $SD/findings.py add --thread <discussion_id> --source human --file … --line … --summary "…" --iid <n>
```

`--why` is one or two English sentences the user decides on, and `--recommend` your answer
(merge by default; `link` when the comment is a topic's own draft, posted by hand): the comment's
card shows the comment, your reason and your recommendation, with that answer on the first
button. Then `python3 $SD/findings.py own-review matched --iid <n>` and end
the turn, with no text of your own: the card says everything. The first topic comes up by itself
once all are settled; with no proposal at all, `matched` prints it, already shown as a card.

**No comments of their own** is common: then `own-review done` matches nothing, and prints the
first topic's card itself, headed by a line that says there was nothing to match. That card is
your whole reply: end the turn on it, with no text of your own; the user reads the topic on it.

**Comments the user posts later**, once the topics run, are the mod's to handle: its update
check notes them, a background agent of its own matches them (`propose` / `add --thread`), and
the user answers each match with a press when their round reaches it.

**Another reviewer's thread, or the MR author's own,** becomes a topic by itself on a sync;
the mod's background agent gives it a title and a view (see *Rounds and verdicts*). The user
watches it: `Seen` marks it read, `Reply…` is theirs to write, and `Merge into tN` is there
where you advised it.

**"Explain"** reaches you as their prompt: compare the two in at most 5 lines, what
each says and what one covers that the other does not, and end on your recommendation. The
buttons stay up.

**A merge keeps your findings in play.** The user's comment becomes the topic's thread, and
the topic comes up again with `I merged my comment into <t>. What does your own analysis add
to it?`. Answer in at most 2 lines: what you have beyond their comment (a concrete fix, a
working alternative, a case they did not name), or that you have nothing to add. Their buttons
are `Follow up` and `Nothing to add`. A follow-up is a reply below their comment, drafted
like any other (`set <t> --draft "…"`, which shows it as a follow-up), short, and never a
restatement of what their comment already says.

## The current topic

Exactly one topic is **current** at a time — the one being decided. `findings.py` keeps it in
the state file, and every view tells the `Stop` hook which topic it shows, so only the current
topic's views must be pasted.

- **Opening a topic** — the next one, or the one the user names ("skip this, do t7 first") — is
  `quote <t> --focus --iid <n>`, pasted verbatim. Re-showing the current one is plain `quote <t>`
  (or `--refine`).
- **Any other topic is research.** `quote`/`diff --show` for a topic that is not current prints
  a `note:` saying so; use it to inform your answer and do not paste it. "Let's stop, I need to
  look into X first" needs nothing — the topic stays current until you come back to it.
- **It moves on by itself** when the topic stops needing the user — acked,
  posted and `link`ed — and `set <t> --draft` makes `t` current. `present`/`resume` show it, so
  a jump also survives into the next session.

## Phase 1 — Curate (no drafts yet)

Walk the seeded findings **one at a time**. For each: 2–4 lines of research (what the code
does + the real trade-off, citing `file:line`), then let the user reshape the *list*:

```bash
python3 $SD/findings.py set <t> --severity … --summary … --source … --iid <n>       # reclassify / reword
python3 $SD/findings.py drop <t> --iid <n>                                          # not worth raising (kept as dropped; `set <t> --state reset` undoes it)
python3 $SD/findings.py merge <into> <other…> --iid <n>                             # same point (→ source 👥)
python3 $SD/findings.py add --file … --line … --summary … --source human --iid <n>  # the user's own find
```

**A `--summary` is always English** — it is the one-line title of the topic, and it is what
the user reads in the table and in every topic heading. The `· drafts <lang> · rest en` marker
applies to the **body of a comment you draft for posting**, and to nothing else. `findings.py`
flags a summary that reads as the draft language, and the paste gate blocks the view.

A topic still marked `✍️ needs summary` has no authored summary — its table row would be the
raw thread quote. The table commands (`sync`/`todo`/`present`/`resume`) **refuse to render
while any topic they would show is in that state**: they exit with
`needs summary: t5 — read the threads (`bodies`), `set t5 --summary "..."`, then re-run`
instead of output. Author those first (`bodies` for the thread
text, then `set <t> --summary "…"`), or `drop`/`merge` the topic, and re-run. On Claude Code
the `Stop` hook additionally blocks a hand-built table carrying the marker; **OpenCode has no
hook — the refusal is what enforces the rule there**, and either way it is not optional: a
Summary cell that is somebody else's truncated sentence tells the user nothing.

### Comments the user posts in the UI while you work

Expected, not exceptional — they read the diff in the browser in parallel. Where cards are
drawn, they are matched as above ("Comments the user posts later"), and nothing here applies.
Otherwise `sync` handles it:

- **A thread on a file no ✎ draft covers** is **adopted automatically** as a topic, `source`
  👤 human, `○ open`. Nothing to ask: it appears in the table on the next `sync`, and from then
  on it is tracked like any other — you see the author's reply and whether it was addressed.
- **A thread on a file a ✎ draft *does* cover** stays out, because it might BE that draft,
  posted by hand. It shows up under `candidates`. Ask which it is, then **one** call:

```bash
python3 $SD/findings.py candidates --iid <n>                # your unlinked threads
python3 $SD/findings.py link <t> <discussion_id> --iid <n>  # it was that draft, posted
python3 $SD/findings.py add --thread <discussion_id> --source human \
    --file … --line … --summary … --iid <n>                 # it is a NEW point
```

**Never** `add` and then ask whether to link — adopting a posted comment is one intention, so
it is one call. `--thread` links it and captures the baseline at the same time.

**When the user says in their own words that they posted your draft** ("done", "posted"), link
it immediately with the id omitted — it resolves the one unlinked thread of theirs on that
topic's file by itself. A Post press posts and links in one step, and its note says so: there is
nothing to link after it.

```bash
python3 $SD/findings.py link <t> --iid <n>       # ✎ draft → ○ open
```

Where cards are drawn, a comment posted by the user's own hand is checked before the round
moves on, since they often reword it: `posted <t>` prints the draft and what they posted. Say in
two or three lines whether the posted text still says what it should, correctly and in a fitting
tone. Where it holds, run `posted <t> --ok`, which shows the next topic. Where it does not, store
your verdict (a line or two) with a corrected version of what they posted, as close to their
wording as it can be: `posted <t> --differs "<verdict>" --fix -` with the text in a quoted
heredoc. That shows its card, the changes marked word by word; editing the comment on GitLab
is the user's press there, never yours.

Skipping this is silent and expensive: the topic stays `✎ draft` while the comment is live on
GitLab, so the next session re-offers a draft that is already posted, and the author's reply is
never reconciled against it.

The user reviews the code elsewhere meanwhile and may **post some comments by hand** before
you finish (they're slower than you). That's fine — you fold those in during the loop
(`candidates` → `link`, below). Curate the whole list before drafting anything, except where
cards are drawn: there the user may draft a topic's comment as soon as it is curated, and the
draft is that topic's Phase 2, before the next topic.

## Phase 2 — Draft comments (you draft, the user posts)

Still **one topic at a time**. The user may want to discuss a finding first — doubt it, ask
for a concrete example, refine wording — possibly over several turns. Finish the topic
(draft accepted / self-posted / skipped) **before** moving on.

**Lead with the topic, never with your research.** Every turn about a topic opens with
`quote <t>` pasted verbatim (`--focus` when the topic is not yet current) — it names the topic, gives `file:line`, shows the code in
question and the draft. Only *then* your 2-4 lines of reasoning. Opening on a verdict
("Confirmed: …", "Yes, that's a real bug") is unreadable: the user cannot tell which finding
you mean, cannot see the code you are asserting things about, and has no `file:line` to open.
If you find yourself writing a conclusion before a pasted `quote`, reorder.

Draft rules — see [REFERENCE.md](REFERENCE.md). In short: **write the comment body in the
language named by the `drafts <lang>` marker** — on every `sync`/`todo`/`present` header and
a posted topic's `quote` header; an unposted topic carries it on its draft label (`Draft of
comment to post (de)`), and while it has no draft yet `quote`'s note to you names it. If you
cannot see it in the output in front of you, run
`python3 $SD/findings.py lang` rather than assuming (`de` means informal *du*). **The body is
the only thing in that language**: summaries, headings and your own prose stay English. **As
short as possible**; a ```suggestion block for a line-precise fix, its range
(`suggestion:-A+B`, A lines above the comment's line through B below) covering every line it
rewrites, as only those are replaced; identifiers in backticks; no headings. Show the draft as GitLab-compatible markdown in your reply (via `quote <t>`,
which also gives **where to open the thread** — `file:line` — and the surrounding code with
the line marked), and offer the clipboard:

```bash
python3 $SD/findings.py set <t> --draft "…" --iid <n>           # store the accepted draft (echoes the view)
python3 $SD/findings.py set <t> --draft "…" --refine --iid <n>  # a REWORDING of a draft already on screen
$SD/clip.sh <(python3 $SD/findings.py draft <t> --iid <n>)      # copy the comment BODY only, for pasting
```

`--refine` is rule 3 at the top: the second and every later draft for the same topic comes
back without the code, since the user is still looking at it and only the wording changed.
`quote <t> --refine` is the same render when you re-show without storing anything.

⚠️ Copy with **`draft <t>`**, never `quote <t>` — `quote` includes the meta header (topic
title + location) for *your* display; that must not land in the posted comment. `draft <t>`
emits the body alone.

The user posts it in the GitLab UI. **You never post.**

## After the user has posted — link threads

When the user says they've posted (or on the next check), reconcile their live threads to
your topics. **Never auto-link — the user confirms each match:**

```bash
python3 $SD/findings.py candidates --iid <n>                # your GitLab threads not yet linked to a topic
python3 $SD/findings.py link <t> <discussion_id> --iid <n>  # on the user's OK → topic flips ✎ → ○
```

A linked topic captures a `start_sha` baseline, so later you can show exactly what the author
changed for it (across force-pushes) — and so does a thread `sync` adopts on its own (see
REFERENCE.md's *Comments you posted yourself*).

## Resuming an in-progress review

`begun` says RESUME (the common case — a review spans days). Fetch + checkout in the worktree
(above, **no set-head**). Then **one command** produces your entire opener — paste it verbatim,
whole:

```bash
python3 $SD/findings.py resume --iid <n>  # pushes since your baseline + overview table + current topic
```

Where cards are drawn, `resume` shows no pushes: what arrived since the last review is in the
sidebar's inbox, where the user opens a round (*Rounds and verdicts*). With no topic current it
shows nothing at all; your opener is then one line, and it neither summarises the news nor
offers to start on a topic.

It is one call on purpose. As two steps ("run `updates`, then run `present`") the second gets
dropped once you start digging into the current topic, and the overview table — the user's only
view of where every topic stands — silently goes missing. If you catch yourself about to reply
without that table, you skipped it.

**1 — `updates`.** Paste verbatim, then annotate — don't collapse it into prose. Each push is a
`- **[push N](<url>):**` line with a nested `  - ` detail (diffstat + topics touched, or a rebase
label). Add your **one-line summary as a further `  - ` sub-bullet** — `diff <t>` for the topics
a push touches is what to write it from, never something to paste:

```
- **[push 1](<url>):**
  - `+20/−8` · 3 files · touches ◈ t1, ◈ t3, ◈ t5
  - the five fixes, one commit — matches each finding
- **push 2:** <url>
  - ↻ rebase onto latest main — no tracked topic's file differs, and commit messages
    are unchanged
```

A **⚠️** rebase line — a real change hidden in the rebase, caught either by content (it
touched a topic's tracked file, whatever the commit messages say) or, failing that, by a
new/edited commit message — **call it out loudly**.

**2 — the overview + current topic** (the part after the `---`). Paste verbatim: the **overview table** (your map of every topic's state) plus
the current topic — the first needing you, or the one a jump left current. **The overview table is mandatory in the opener — never drop it**
(it's the user's only view of where all topics stand). Then walk the needs-ack topics one at a
time from there.

The pasted first-topic block is **the start of the answer, not the answer**: your 2-4 lines of
judgment (what was found or agreed, why, with `file:line`) follow it, same rule as Phase 2's
"Lead with the topic". For a topic with no draft yet that means analysis FIRST — what the
finding is and why it matters — then **ask how they want to handle it** (recommendation-first
for the obvious call, grilling for the complex one); the draft is written and shown only once
agreed, in Phase 2. Never put a "write the draft" instruction in front of the user — `quote`
prints such a note on stderr, for you alone.

Advance the baseline (`set-head`) at the end, once you've digested the pushes.

## The re-review loop

"any news?" / "check the MR" / "status" / "what's left" → the ongoing cycle. Full mechanics in
[REFERENCE.md](REFERENCE.md). In short:

```bash
python3 $SD/findings.py sync --iid <n>     # reconcile threads + one-line push banner; paste it
python3 $SD/findings.py updates --iid <n>  # pushes since baseline: URLs + diffstats/rebase + topics
python3 $SD/findings.py todo --iid <n>     # only what needs you (✎ + ◐)
```

`updates` — **paste verbatim**, then one summary sentence per push (see *Resuming*). `sync` also
auto-surfaces threads you didn't open: a peer reviewer's (💬) or the author's own (🖊️), so they
land in your lists and can be `merge`d with your findings.

For each `◐ needs-ack` (author replied and/or resolved), present it **one at a time**. **Read
the thread first — it is the source of truth for what was agreed.** The opener already ends
with the current topic's full thread, so do not run `quote` for that topic again; continue
straight to its diff and judgment. Run `quote --focus` once when moving to each later topic:

```bash
python3 $SD/findings.py quote <t> --focus --iid <n>  # the full thread: your point + the author's reply
```

**Judge against what the thread agreed, not against the finding's original one-line summary.**
A point is often *down-scoped in discussion* — e.g. you said "ein `onError` wäre schön, muss
aber nicht in diesem MR", the author replied "schreib ich als TODO hin" and resolved. Then the
bar is **the TODO, not the full fix** — re-deriving the original defect from the code and
declaring it "not fixed" is wrong. If the thread agreed to defer (a TODO / follow-up ticket)
and the author did that → recommend **ack**, not a re-litigation.

Use the diff only to **confirm the agreed change landed** — and it is **your** input, not the
user's reading:

```bash
python3 $SD/findings.py diff <t> --iid <n>         # the author's change for THIS topic, for you
python3 $SD/findings.py diff <t> --show --iid <n>  # only when the user asks to see it
```

`diff <t>` prints the topic file's change since you posted it, server-side (force-push-safe, so
it works even when the baseline sha is long gone locally); a file that was only renamed says so.
**Do not paste it.** Tell the user in 2–4 bullets what changed — which push, what the author
did, whether it is what the thread agreed — then your recommendation, and offer the full diff.
**Never assemble a diff yourself** from `glab api`/`git` calls: `diff <t>` is already scoped to
the topic and survives force-pushes.

When the user asks to see it ("show me the diff"), run `diff <t> --show` and paste its output
verbatim. It puts the diff in your own viewer window when you run in tmux with `hunk` installed —
the output then names the window and lists each file's `+/−` counts — and inline otherwise
(just the compare URL when it is too big). On the user's word:

```bash
python3 $SD/findings.py set <t> --state acked --iid <n>               # ● you're satisfied, or agreed to leave it
# not satisfied → draft a follow-up reply (Phase-2 rules); topic stays open
```

**An author resolving a thread is NOT a close — only your ack is.** After you've reviewed a
push, `set-head` to move the baseline. Always end a check with what's still left (`todo`).

### Rounds and verdicts (where cards are drawn)

The mod's update check only takes notes: news (an author's answer, a push that changed the lines
a comment is on, another reviewer's thread, a comment of the user's own) marks its topic in the
sidebar's inbox and never becomes the current topic by itself. The user opens a **round** from the
inbox or a row; each decision moves to the next topic that needs them, and `Later` puts one off.
The news is judged before the user gets to it, by a background agent the mod starts with
`findings.py judge-brief`: its judgments are stored with each topic, so a card a press brings
up carries one.

A topic whose news could not be judged that way reaches you as the user's `Judge the news on
<t>` prompt. Read the whole thread (`bodies <t>`) and the change since the comment (`diff <t>`), store the
judgment, then show the topic (`show_topic`):

```bash
python3 $SD/findings.py verdict <t> settled --asked "<what the comment asked for>" --did "<what the author did>" --text "<why it holds>" --iid <n>
python3 $SD/findings.py verdict <t> more --case <case> --asked "<what the comment asked for>" --did "<what the author did>" --text "<what is still open, and the next step>" --iid <n>
python3 $SD/findings.py verdict <t> wait --asked "<what the comment asked for>" --did "<what the author did>" --text "<what they agreed to do>" --iid <n>
```

`wait` is for an author who agreed and has the change still to make: the topic goes back to
waiting on them, out of the user's round, until they push or write.

`--asked` is one English sentence: the problem the comment raised and what it asked for ("`save()`
drops its promise, so a failed write goes unnoticed; asked for an `await`."), the card's first
section, so what the author did reads against it; it stays with the topic, so it is given the first
time the topic is judged and left out after. `--did` is one English sentence ("Pushed the
`await` on the commented line; no reply."), shown above the verdict; `--text` is 1–3 English
sentences, code in all three in backticks. A `more` names its case, the card's verdict heading:
`not-fixed`, `partly-fixed`, `fix-wrong`, or `comment-wrong` when the author did as asked and the
comment itself was mistaken. Its text ends on one next step (a reply asking for X, accepting a
follow-up MR, …): **Send follow-up** hands that step back to you to draft. Another reviewer's thread is theirs to settle, and the user watches it: the judgment is
`peer <t> fyi|needs --why "…"` (with `--summary "…"` for a new one). `fyi` is the usual answer;
`needs` only when the user has something to add (a question to them: `--advice reply`; a point
that repeats one of their topics: `--advice merge --into <t>`), its `--why` ending on a
`**Recommendation:**` sentence. Nothing the user does there resolves it. The user's buttons follow the verdict, its own answer first: settled → `Accept &
resolve` (resolves the GitLab thread and closes the topic; theirs to press) / `Not good
enough…` (their reply, which you draft) / `Analyse more`; more → `Send follow-up` / `Wait for
author` / `Accept anyway` / `Analyse more`. On `Analyse more`, a verdict you change
prints a card of the change itself: leave the topic unshown, your reply carries the details. A verdict lasts until a new answer arrives, and so
does a follow-up draft: a newer answer, or `wait`, drops it.

## Status glyphs

`✎ draft` (post it) · `○ open` (author's turn) · `◐ needs-ack` (your turn) · `● acked`.
A trailing **`✓`** on `◐ needs-ack` = the GitLab thread is already resolved (by
whoever toggled it) — that's separate from *your* ack, which is the only thing that closes the
topic here. Kind: 🔴🟠🟡🔵 issue by severity · ❓ question · 💚 praise · ⚪ severity not set
yet. Source: 🤖 llm · 👤 you · 👥 both/merged · 💬 peer reviewer · 🖊️ author (inbound threads).

## Prerequisites

On Claude Code, register the shared `Stop` hook (`hooks/paste-gate.py` + this skill's
`scripts/paste-gates.json`) in `settings.json` — see the repo README. OpenCode needs no hook;
the script output stays clean there, but the exact-output rules above still apply because tool
details can be collapsed or hidden.
`glab` authenticated; a review worktree (or willingness to create one); run inside the target
repo. `python3`. (`clip.sh` copies to the clipboard via macOS `pbcopy`; where that is
missing the copy is skipped and nothing else changes.) Build blocks: the `explain-branch` and `review-branch`
skills installed. `findings.py` subcommands: sync·todo·present·resume·updates·bodies·quote·draft·diff·candidates·
import·add·set·drop·merge·link·posted·head·base·branch·set-head·worktree·explainer·seed·prune·path·begun·lang
(run any with `-h`).
