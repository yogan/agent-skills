---
name: review-mr
description: Review someone ELSE's GitLab MR end to end — optionally generate an explainer first (explain-branch), seed findings with review-branch, curate them with you one topic at a time, and draft concise German review comments you post yourself. Then run a persistent re-review loop: on each check it reconciles the live threads (author replies, resolutions) and branch pushes, flags what the author addressed, and gates every close behind YOUR ack. Read-only against GitLab — it never posts or resolves; you do that in the UI for full tone control. This is for reviewing another person's MR, NOT reworking your own (use rework-mr for that) and NOT a local-only critique (use review-branch for that). Use when the user says "review MR !123", "review !123", "review this MR", or invokes /review-mr.
---

# Review MR

Reviewing **someone else's** GitLab MR. glab-only, **read-only** against GitLab.
`SD=~/.claude/skills/review-mr/scripts`.

## ⛔ Read this first — it is the whole skill

**The user cannot see your tool calls or their output** — those are collapsed.
Your chat message is their *only* window. So:

1. When a `findings.py` command prints a table/quote, **paste it verbatim into your
   reply** — table, blockquote and all. Never summarize, re-type, or wrap it in a
   code fence. Any heading you write above an overview table **must carry `MR !<num>`**
   (the table's own `MR !<num> — <title>` header already does — don't drop it); if you'd
   write no heading, the full `MR !<num>: <title>` is a fine standalone one.
2. **One topic at a time.** Present the current topic, then **STOP and wait**. Never
   mention, preview, or recommend anything about other topics.
3. **The skill does not comment or resolve on GitLab.** It never posts comments and
   never resolves threads — *you* do that in the UI, so the tone is yours. It only
   *reads* (discussions, branch tip, merge/approval status) and *drafts* text for you
   to copy. **The one exception:** approving/unapproving the MR — `glab mr approve` /
   `glab mr revoke` — which you may run **only on the user's explicit ACK** (see
   *Merge readiness*). Never any other `glab … -X POST`.
4. **No code changes, ever.** You review; you don't fix. (The author fixes.)

A reply with no pasted table, that touches more than one topic, that comments/resolves
on GitLab, or that approves without an explicit ACK is wrong — redo it.

**Name people by first name.** GitLab names look like `Doe, Jane - AB12345`; always
refer to the author as `Jane`. The table header already renders the short name (from
`findings.py`) — reuse that, never the raw `Lastname, Firstname - ID` or the bare account id.

## Setup — do this silently, then present

**You're given the MR number** (`534` from `/review-mr 534`) — **use it as `--iid` on every
`findings.py` call** and don't rely on branch inference: you may be launched in a detached or
unrelated worktree, and a bare `glab mr view` / `findings.py` there resolves the wrong thing.
First find the review worktree (this is repo-level; run from anywhere inside the repo):

```bash
python3 $SD/findings.py worktree            # stored review-worktree path for this repo, if any
git worktree list                           # else pick the one whose path looks like a review worktree
python3 $SD/findings.py worktree --set <path>   # persist your choice (ask once if ambiguous / none)
```

If none exists, offer to create one (`git worktree add --detach <path>` — no branch). **Then `cd` into the worktree and run
everything — glab *and* findings.py — from there.** Both auto-resolve the project from that
worktree's `origin`, so you never name it by hand. **Do NOT guess the repo** (no
`glab mr view -R "$(glab repo view …)"` — it misfires into 404s); and `findings.py` takes
`--iid`, never `-R`. The review worktree is **disposable**, so hard-reset it onto the MR tip.
Check out **detached** — nothing here reads the local branch name (sync/detection run off
`--iid` + the GitLab API), so a detached HEAD keeps your local branch list clean:

```bash
cd <wt>                                     # the review worktree
git fetch origin
git checkout -f --detach origin/<mr-branch>   # detached: lands exactly on the tip, no local branch created; -f discards worktree cruft — pause only if <wt> holds work that looks intentionally yours
```

Fresh vs resume: the state file exists iff you've reviewed this MR before —

```bash
test -f "$(python3 $SD/findings.py path --iid <n>)" && echo RESUME || echo FRESH
```

**FRESH only** — mark the current tip as your baseline so later pushes are measured from here:

```bash
python3 $SD/findings.py set-head --iid <n>  # ⚠️ first run ONLY
```

**RESUME** — do NOT `set-head` now (it would erase the "pushes since last review" detection).
Jump to *Resuming*, below.

**Explainer (opt-in, parallel).** Unless the user said "no explain(er)", decide by size —
changed LOC **excluding tests**; below ~40 → skip (it's a short change). Otherwise launch
`explain-branch` as a **background subagent** pointed at the checked-out worktree, and move
on immediately (it opens the HTML when ready). It must only *read* git (no checkout/fetch —
that already happened) so it can't race review-branch.

**Seed the findings.** Run the `review-branch` skill against the worktree (foreground). It
returns a prioritized critique. Turn each finding into a JSON object and import them:

```jsonc
// /tmp/seed.json — one object per review-branch finding
[{"kind":"issue","severity":"high","source":"llm","file":"src/x.ts","line":42,
  "summary":"one line in the user's words"}]
```
```bash
python3 $SD/findings.py import /tmp/seed.json --iid <n>
python3 $SD/findings.py present --iid <n>    # paste verbatim as the top of your reply
```

⚠️ **`--iid <n>` on these too** — `import`/`present` are as iid-sensitive as any other
call. A bare `cd <wt>` from an earlier command does **not** persist (each shell call resets
cwd to the repo root, usually *your own* branch), so without `--iid` these silently resolve to
the wrong MR and land findings in the wrong state file.

`present` = the overview table + the first topic that needs you. Then enter Phase 1.

## Phase 1 — Curate (no drafts yet)

Walk the seeded findings **one at a time**. For each: 2–4 lines of research (what the code
does + the real trade-off, citing `file:line`), then let the user reshape the *list*:

```bash
python3 $SD/findings.py set <t> --severity … --summary … --source …   # reclassify / reword
python3 $SD/findings.py drop <t>                                       # not worth raising
python3 $SD/findings.py merge <into> <other…>                         # same point (→ source 👥)
python3 $SD/findings.py add --file … --line … --summary … --source human   # the user's own find
```

The user reviews the code elsewhere meanwhile and may **post some comments by hand** before
you finish (they're slower than you). That's fine — you fold those in during the loop
(`candidates` → `link`, below). Curate the whole list before drafting anything.

## Phase 2 — Draft comments (you draft, the user posts)

Still **one topic at a time**. The user may want to discuss a finding first — doubt it, ask
for a concrete example, refine wording — possibly over several turns. Finish the topic
(draft accepted / self-posted / skipped) **before** moving on.

Draft rules — see [REFERENCE.md](REFERENCE.md). In short: **German, informal *du*, as short
as possible**; a ```suggestion block for a line-precise fix; identifiers in backticks; no
headings. Show the draft as GitLab-compatible markdown in your reply, and offer the clipboard:

```bash
python3 $SD/findings.py set <t> --draft "…"     # store the accepted draft
$SD/clip.sh <(python3 $SD/findings.py quote <t>)   # or copy for pasting into GitLab
```

The user posts it in the GitLab UI. **You never post.**

## After the user has posted — link threads

When the user says they've posted (or on the next check), reconcile their live threads to
your topics. **Never auto-link — the user confirms each match:**

```bash
python3 $SD/findings.py candidates      # your GitLab threads not yet linked to a topic
python3 $SD/findings.py link <t> <discussion_id>   # on the user's OK → topic flips ✎ → ○
```

A linked topic captures a `start_sha` baseline, so later you can show exactly what the author
changed for it (across force-pushes).

## Resuming an in-progress review

A state file exists (the common case — a review spans days). Fetch + checkout in the worktree
(above, **no set-head**). Your opener must contain, in this order — **all pasted verbatim**:

```bash
python3 $SD/findings.py updates         # 1. pushes since your baseline (syncs internally)
python3 $SD/findings.py present         # 2. the overview table + the first topic that needs you
```

**1 — `updates`.** Paste verbatim, then annotate — don't collapse it into prose. Each push is a
`- **push N:** <url>` line with a nested `  - ` detail (diffstat + topics touched, or a rebase
label). Add your **one-line summary as a further `  - ` sub-bullet**:

```
- **push 1:** <url>
  - 3 files, +20 −8 · touches t1, t3, t5
  - the five fixes, one commit — matches each finding
- **push 2:** <url>
  - ↻ rebase onto latest main — messages unchanged, nothing to re-review
```

A **⚠️ mixed rebase** line (real changes folded into a rebase) — **call it out loudly**.

**2 — `present`.** Paste verbatim: the **overview table** (your map of every topic's state) plus
the first topic needing you. **The overview table is mandatory in the opener — never drop it**
(it's the user's only view of where all topics stand). Then walk the needs-ack topics one at a
time from there. Advance the baseline (`set-head`) at the end, once you've digested the pushes.

## The re-review loop

"any news?" / "check the MR" / "status" / "what's left" → the ongoing cycle. Full mechanics in
[REFERENCE.md](REFERENCE.md). In short:

```bash
python3 $SD/findings.py sync            # reconcile threads + one-line push banner; paste it
python3 $SD/findings.py updates         # pushes since baseline: URLs + diffstats/rebase + topics
python3 $SD/findings.py todo            # only what needs you (✎ + ◐)
```

`updates` — **paste verbatim**, then one summary sentence per push (see *Resuming*). `sync` also
auto-surfaces threads you didn't open: a peer reviewer's (💬) or the author's own (🖊️), so they
land in your lists and can be `merge`d with your findings.

For each `◐ needs-ack` (author replied and/or resolved), present it **one at a time**. **Read
the thread first — it is the source of truth for what was agreed:**

```bash
python3 $SD/findings.py quote <t>       # the full thread: your point + the author's reply
```

**Judge against what the thread agreed, not against the finding's original one-line summary.**
A point is often *down-scoped in discussion* — e.g. you said "ein `onError` wäre schön, muss
aber nicht in diesem MR", the author replied "schreib ich als TODO hin" and resolved. Then the
bar is **the TODO, not the full fix** — re-deriving the original defect from the code and
declaring it "not fixed" is wrong. If the thread agreed to defer (a TODO / follow-up ticket)
and the author did that → recommend **ack** (or `⊘ wontfix` if you want a ticket tracked), not a
re-litigation.

Use the diff only to **confirm the agreed change landed**:

```bash
python3 $SD/findings.py diff <t>        # the author's change for THIS topic (server-side)
```

`diff <t>` shows the topic file's change since you posted it — **inline when small**, else just
the compare URL to `open`. It's server-side (force-push-safe), so it works even when the baseline
sha is long gone locally. Paste whatever it returns. On the user's word:

```bash
python3 $SD/findings.py set <t> --state acked            # ● you're satisfied
python3 $SD/findings.py set <t> --state wontfix --ticket …   # ⊘ agreed not to fix / deferred
# not satisfied → draft a follow-up reply (Phase-2 rules); topic stays open
```

**An author resolving a thread is NOT a close — only your ack is.** After you've reviewed a
push, `set-head` to move the baseline. Always end a check with what's still left (`todo`).

## Merge readiness & approval

Every overview (`sync`/`present`) now carries a footer line — **Approvals** (count + whether
*you* approved) and **Merge** (GitLab's `detailed_merge_status`, in plain words, with whose turn
it is: `needs rebase`/`CI failing`/`conflicts` → the author; `threads unresolved` → resolve on
GitLab). `python3 $SD/findings.py status` prints just this block on demand.

The footer also carries the **approve/revoke nudge**, and it's the only place you act on GitLab:

- **Approve** — offered *only* when every topic is closed on your side **and** GitLab reports all
  threads resolved. On the user's explicit ACK, run `glab mr approve <iid>` (from the worktree,
  so the project auto-resolves). If they've closed everything locally but GitLab still shows
  unresolved threads, say so — don't nudge to approve yet.
- After approving, if the merge is still blocked, **state the blocker and whose turn** (e.g.
  "approved — merge still needs a rebase, author's turn"). Don't draft a nudge message.
- **Revoke** — if a re-review turns up something after you'd approved, offer `glab mr revoke
  <iid>`, again only on explicit ACK.

Never approve/revoke without that ACK; never comment or resolve (still your job in the UI).

## Status glyphs

`✎ draft` (post it) · `○ open` (author's turn) · `◐ needs-ack` (your turn) · `● acked` ·
`⊘ wontfix`. A trailing **`✓`** on `◐ needs-ack` = the GitLab thread is already resolved (by
whoever toggled it) — that's separate from *your* ack, which is the only thing that closes the
topic here. Kind: 🔴🟠🟡🔵 issue by severity · ❓ question · 💚 praise · ⚪ severity not set
yet. Source: 🤖 llm · 👤 you · 👥 both/merged · 💬 peer reviewer · 🖊️ author (inbound threads).

## Prerequisites

`glab` authenticated; a review worktree (or willingness to create one); run inside the target
repo. `python3`; macOS for `clip.sh`. Build blocks: the `explain-branch` and `review-branch`
skills installed. `findings.py` subcommands: sync·todo·present·status·updates·bodies·quote·diff·candidates·
import·add·set·drop·merge·link·head·set-head·worktree·path (run any with `-h`).
