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
a trivial topic's change illustration (`change-preview.sh`). Claude Code
**collapses tool output**, so each of these only reaches you if the model
pastes it — and the model reliably *drops* it: it decides what to paste,
makes another tool call (research, a `git blame`, a Read) before writing the
message, and that pushes the pasted content out of mind, so the reply starts
with the model's own prose, or jumps straight to the trailing question/ACK,
with the promised block never actually shown. `scripts/stop-hook.py` enforces
the paste for all six: it inspects the finished turn and, if one of them ran
but its output is missing from the visible message, **blocks the turn and
makes the model re-send the full block**. (`threads.py sync` is deliberately
excluded — it also runs as silent, intentionally-unshown prep in the opener,
so gating it would false-block on that unrelated call; use `todo` for a
status-only reply instead, which has no such double meaning.)

Without this hook the skill still works, but on many turns you won't see the
overview, the reviewer's comment, the diff, the draft, or the change before
choosing an action or ACKing a push. **Install it once:**

Add a `Stop` hook to `~/.claude/settings.json` (merge into any existing `hooks`):

```jsonc
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 ~/.claude/skills/rework-mr/scripts/stop-hook.py"
          }
        ]
      }
    ]
  }
}
```

Use the path where this skill is installed (commonly
`~/.claude/skills/rework-mr/scripts/stop-hook.py`; if the skill lives elsewhere,
point at that copy). Restart Claude Code so the hook loads.

### How it behaves

- **Scoped:** only acts on a turn where one of the gated commands (`present`,
  `todo`, `quote`, `diff-view.sh`, `reply-view`, `change-preview.sh`) actually
  ran and produced real output — every other turn (and every non-rework
  session) passes straight through.
- **Loop-safe:** uses `stop_hook_active` to force at most one retry, so it never
  spins.
- **Fails open:** any error (bad transcript, parse failure) → allows the stop; it
  never wedges a session.

It's a good idea to keep the hook narrow: it's harmless globally because it does
nothing unless one of those blocks is present in the turn.

## Scripts

- `threads.py` — fetch/reconcile threads, render tables & the reply block. Run
  `threads.py -h` for subcommands (`sync·todo·present·bodies·plans·quote·url·reply-view·set·merge·path`).
- `reply-view <t>` — the one-paste reply block (thread + draft + URL + prompt);
  also refuses a draft containing an internal topic handle (`t5`…).
- `change-preview.sh <t> <file>` — the one-paste trivial-topic change illustration
  (fenced code + `Agreed?`); the file is the proposed change, not applied.
- `diff-view.sh <t> [-- git-diff-args...]` — the one-paste working diff shown
  before the fixup+push ACK (fenced diff + `ACK to fix up and push?`).
- `diff-url.py` — stable per-topic diff URL across force-pushes.
- `clip.sh` — copy a reply body to the clipboard (guards topic handles first).
- `guard-reply.sh` — the topic-handle gate, reused by `clip.sh` and the post step.
- `stop-hook.py` — the Stop hook above.
