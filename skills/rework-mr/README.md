# rework-mr

Work through the review feedback on a GitLab MR **you authored**: pull the open
discussion threads, plan each topic with you, then fix → fixup-and-push → draft a
reply per topic. See [SKILL.md](SKILL.md) for the full flow and
[REFERENCE.md](REFERENCE.md) for the Phase-3 mechanics.

## Prerequisites

- `glab` authenticated (`glab auth status`), run on or `--iid N` for the MR branch.
- `python3`; macOS for `clip.sh` (uses `pbcopy`).

## Required setup — the paste-enforcement Stop hook

Several steps show you something by pasting a script's output into chat: the
opener's overview table + first topic's comment (`present`), the status-only
answer (`todo`), the next topic's comment (`quote`), the working diff shown
before a fixup+push ACK (`diff-view.sh`), the reply block (`reply-view`), and
a trivial topic's change illustration (`change-view`). Claude Code
**collapses tool output**, so each of these only reaches you if the model
pastes it — and the model reliably *drops* it: it decides what to paste,
makes another tool call (research, a `git blame`, a Read) before writing the
message, and that pushes the pasted content out of mind, so the reply starts
with the model's own prose, or jumps straight to the trailing question/ACK,
with the promised block never actually shown.

A `Stop` hook enforces the paste for all six, plus one inverse rule: asking for
the fixup+push ACK without having run `diff-view.sh` at all is blocked too.
Without the hook the skill still works, but on many turns you won't see the
overview, the reviewer's comment, the diff, the draft, or the change before
choosing an action or ACKing a push.

The hook is shared with `review-mr` and installed once, outside the skill:
**[`hooks/README.md`](../../hooks/README.md)** has the install snippet and how it
behaves. What this skill gates lives in
[`scripts/paste-gates.json`](scripts/paste-gates.json) — including why
`threads.py sync` is deliberately *not* gated (it also runs as silent,
intentionally-unshown prep in the opener, so gating it would false-block on that
unrelated call; use `todo` for a status-only reply, which has no such double
meaning).

## Scripts

- `threads.py` — fetch/reconcile threads, render tables & the reply block. Run
  `threads.py -h` for subcommands
  (`sync·todo·present·bodies·plans·quote·url·reply·reply-view·set·merge·path·change-view·diff-view`).
- `quote <t>` — a topic in full: the code the reviewer's comment is anchored to (read
  from the exact blob the comment hangs on, so the line numbers are the reviewer's),
  then the whole thread.
- `set <t> --reply -` / `reply <t>` — store a reply body (from a quoted heredoc, so the
  shell cannot expand it) and print it back. The draft lives in the state file, not in a
  scratch `reply-<t>.md`: no protected-path write prompt, and the internal-topic-handle
  guard (`t5`…) sits inside `reply`, so the post path cannot skip it.
- `reply-view <t>` — the one-paste reply block (code + thread + draft + URL + prompt);
  the draft's prose is blockquoted, its fenced code left at line start so it keeps its
  highlighting.
- `change-view <t>` — the one-paste trivial-topic change illustration (fenced code +
  `Agreed?`), read from stdin: piped rather than written to a file, since a heredoc into
  `~/.claude/` trips Claude Code's protected-path prompt on every topic. Content that
  already carries its own ```diff block is passed through rather than wrapped again, so it
  stays syntax-highlighted. `change-preview.sh <t> <file> [--for <path>]` renders the same
  block from a file.
- `diff-view.sh <t> [-- git-diff-args...]` — the one-paste working diff shown
  before the fixup+push ACK (fenced diff + `ACK to fix up and push?`).
- `diff-url.py` — stable per-topic diff URL across force-pushes.
- `clip.sh` — copy a reply body to the clipboard (guards topic handles first).
- `guard-reply.sh` — the topic-handle gate for the clipboard path, reused by `clip.sh`.
  (`reply` enforces the same rule inside the script, so the post path needs no shell
  guard at all.)
- `paste-gates.json` — what the shared Stop hook enforces for this skill.
