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
3. **This skill NEVER writes to GitLab.** It does not post comments and does not
   resolve threads — *you* do that in the UI, so the tone is yours. The skill only
   *reads* (discussions, branch tip) and *drafts* text for you to copy. If you ever
   feel the urge to `glab api … -X POST`, stop — that's the user's job.
4. **No code changes, ever.** You review; you don't fix. (The author fixes.)

A reply with no pasted table, that touches more than one topic, or that posts/resolves
on GitLab is wrong — redo it.

**Name people by first name.** GitLab names look like `Doe, Jane - AB12345`; always
refer to the author as `Jane`. The table header already renders the short name (from
`findings.py`) — reuse that, never the raw `Lastname, Firstname - ID` or the bare account id.

## Setup — do this silently, then present

Resolve the MR (from `123` / `!123`, or infer from the branch) and the review worktree:

```bash
python3 $SD/findings.py worktree            # stored review-worktree path for this repo, if any
git worktree list                           # else pick the one whose path looks like a review worktree
python3 $SD/findings.py worktree --set <path>   # persist your choice (ask once if ambiguous / none)
```

If no review worktree exists, offer to create one (`git worktree add`). **All git work
happens in that worktree** — never disturb the user's current checkout. In it, **once**:

```bash
git -C <wt> fetch <remote>                  # refuse if the worktree is dirty; warn and stop
git -C <wt> checkout <mr-branch>            # or: glab mr checkout <iid> -R <project>
python3 $SD/findings.py set-head            # mark the current tip as your reviewed baseline
```

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
python3 $SD/findings.py import /tmp/seed.json
python3 $SD/findings.py present             # paste verbatim as the top of your reply
```

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

## The re-review loop

"any news?" / "check the MR" / "status" / "what's left" → the ongoing cycle. Full mechanics in
[REFERENCE.md](REFERENCE.md). In short:

```bash
python3 $SD/findings.py sync            # reconcile threads + one-line push banner; paste it
python3 $SD/findings.py updates         # "any updates?": pushes since baseline, diffs + topics touched
python3 $SD/findings.py todo            # only what needs you (✎ + ◐)
```

`updates` lists each push since your baseline with a compare URL, diffstat, and the topics its
files touch — add your prose summary of *what* changed on top. `sync` also auto-surfaces
threads you didn't open: a peer reviewer's (💬) or the author's own (🖊️), so they land in your
lists and can be `merge`d with your findings.

For each `◐ needs-ack` (author replied and/or resolved), present it **one at a time** with a
short summary of what the author did and — for a real fix — offer the diff:

```bash
python3 $SD/findings.py diff <t>        # compare URL (from the topic's baseline) + inline git cmd
```

small → run the inline `git -C <wt> diff …` and show it; big → paste the compare URL and offer
to `open` it. On the user's word:

```bash
python3 $SD/findings.py set <t> --state acked            # ● you're satisfied
python3 $SD/findings.py set <t> --state wontfix --ticket …   # ⊘ agreed not to fix / deferred
# not satisfied → draft a follow-up reply (Phase-2 rules); topic stays open
```

**An author resolving a thread is NOT a close — only your ack is.** After you've reviewed a
push, `set-head` to move the baseline. Always end a check with what's still left (`todo`).

## Status glyphs

`✎ draft` (post it) · `○ open` (author's turn) · `◐ needs-ack` (your turn) · `● acked` ·
`⊘ wontfix`. Kind: 🔴🟠🟡🔵 issue by severity · ❓ question · 💚 praise · ⚪ severity not set
yet. Source: 🤖 llm · 👤 you · 👥 both · 💬 peer reviewer · 🖊️ author (inbound threads).

## Prerequisites

`glab` authenticated; a review worktree (or willingness to create one); run inside the target
repo. `python3`; macOS for `clip.sh`. Build blocks: the `explain-branch` and `review-branch`
skills installed. `findings.py` subcommands: sync·todo·present·updates·bodies·quote·diff·candidates·import·
add·set·drop·merge·link·head·set-head·worktree·path (run any with `-h`).
