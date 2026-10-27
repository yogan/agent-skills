---
name: self-review
description: >-
  Walk the author through their own review of a pending change in Hunk, before any merge request exists — one commit at a time, or the uncommitted change — then answer each note they leave, amend agreed fixes straight into the commit under review, and move on once they ACK it. Author-led: the agent adds notes of its own only where asked to point something out. Use when the user wants to review their own change before committing or opening an MR, says "self-review" or "let me review my changes first", or invokes /self-review. NOT for reviewing someone else's MR (review-mr), a critique by the agent (review-branch), or feedback on an open MR (rework-mr).
---

# self-review

The author judges their own change, piece by piece, until they can follow it and stand
behind it. A **piece** — one commit, or the uncommitted change — sits in a
[Hunk](https://hunk.dev/) viewer window beside yours. The author writes **diff notes** on
its lines; you answer each one under it, fix what they agree to, show the piece again, and
move on only on their **ACK**. Their diff notes, their verdict: you answer inside them, and
start one only where they ask you to point something out.

```
implement → self-review → open the MR (author's own MR skill) → rework-mr
```

A diff note here is the author's note together with everything written under it. Say
"diff note", never "thread": that word belongs to a merge request, and none exists yet.

Every step runs through one script. Below, `sr` stands for
`python3 ~/.claude/skills/self-review/scripts/selfreview.py`; run bare, it lists its
commands. It needs `tmux` and `hunk`; without either, say so and stop.

## 1. Plan

`sr plan` lists the branch's commits from its merge-base with the default branch, oldest
first and numbered, plus whether there are uncommitted changes. Commits are addressed by
that number throughout — amending rewrites hashes, never positions.

- **Commits only:** review 1 to N in order.
- **Uncommitted only:** review the uncommitted change (`work`).
- **Both:** fixes are amended from the working tree, so reviewing commits needs a clean
  tree. Ask the author once: commit the uncommitted work first (it is reviewed as the last
  commit), or review it first on its own, then commit it and continue with the earlier
  commits.
- **Neither:** nothing to review — say so and stop.

State the plan in one line, then start.

## 2. The loop, per piece

1. **Show.** `sr show N` (or `sr show work`). It opens the window beside yours the first
   time and reloads it after that, and fails rather than point at a window that does not
   hold exactly this diff. Tell the author in one line which piece is up (`commit 2 of 5:
   "<subject>"`, window name), and that they say **done** when they have written their diff
   notes. The window's own help (`?`) shows its keys; a note is `c`, a reply `R`.
2. **Read.** On "done": `sr notes` prints every diff note still awaiting an answer — its
   number (`#2`), `file:line`, and what the author wrote since you last answered.
   - **None awaiting**, after the author has looked at the latest state of the piece: that
     is their **ACK** — go to 5.
3. **Answer, every diff note.** Discuss in chat, one at a time where they need it: answer
   the question, or recommend the fix. Then put the gist in the window under the note, so
   it reads as answered where they wrote it: `sr reply <number> "<one or two sentences>"`,
   with the number `sr notes` printed. Make no edit until the author has agreed to that
   fix. A diff note is done when it is answered and any fix in it is agreed or declined.
4. **Fix.** Make the agreed changes and run the project's checks. Then:
   - a commit: `sr amend N` folds the working tree into commit N — in place for the newest,
     as a fixup squashed in by rebase for an older one — and shows commit N again;
   - the uncommitted change: `sr show work` shows it again.

   A diff note whose code the fix moved or rewrote moves with it, and the reload prints a
   line for each one it moved: below the code that replaced the noted lines, shifted with
   lines added or removed above them, or where deleted code used to be. Say in one line
   what changed, and pass on every move and anything left behind, so the author finds
   their diff notes. Ask them to look again and say done. Back to 2 — reading the numbers
   afresh from `sr notes`, since a moved diff note gets a new one.
5. **ACK.** `sr ack` clears the window's diff notes — it refuses while any awaits an
   answer. Then the next piece, from 1.

When every piece is ACKed: `sr close`, and point to the next step — open the MR with the
project's own MR skill.

## Pointing something out

The one time you start a diff note: when the author asks you to point at something ("point
out…", "show me where…"), and only at the places they asked about — never as a review of
your own. `sr point <file> <line> "<text>"` (`--old` for a removed line) puts your note on
that line of the piece in the window; the author toggles your notes with `a`, and `}` jumps
to the next one. Only lines the piece's diff shows can carry a note: a place outside it is
refused, so name that one in the chat instead. Say in chat which notes you left. A reply the
author writes under one comes back through `sr notes` like any other.

## Rules the script holds you to

Each refusal is deliberate; resolve its cause, never work around it.

- **Diff notes live only in the open window.** Hunk saves nothing to disk: quitting the
  window (`q`) loses them. Tell the author to leave it open until you have read; `sr close`
  refuses while one awaits an answer.
- **The window does not move on under diff notes.** They re-anchor by line number, so
  `sr show` refuses to load a different piece while the window holds any. Reloading the
  same piece keeps them; that is how the author checks a fix against what they wrote.
- **A moved diff note is a copy.** Hunk cannot move a note, nor create one as the author's,
  so a moved diff note is re-created and the original removed. Its first note is labelled
  `you (moved from R356–383)`; the author can reply to it but no longer edit or delete it.
- **Every file of the piece must be in the window.** An extension can hide files —
  hunk-mark-read hides those marked read — and Hunk lists no diff notes on a hidden file,
  so "none awaiting" would be a false ACK. `notes`, `ack`, `close` and `show` refuse while
  files are hidden and name them: tell the author to show hidden files again and say done,
  then read again.
- **Commits need a clean tree** (`sr show N`), because everything in the tree is folded into
  the commit by `sr amend`.
- **A fix that conflicts with a later commit** is rolled back: the branch is untouched and
  the fix is back in the tree, unstaged. Tell the author, and agree whether the fix belongs
  in the later commit instead or the conflict is resolved by hand.
- **Never push.** If the reviewed commit was already on the branch's upstream, `sr amend`
  says so: publishing the result needs a force-push, which is the author's to run.
