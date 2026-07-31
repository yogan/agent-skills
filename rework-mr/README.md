# rework-mr

Work through the review feedback on a GitLab MR **you authored**: pull the open
discussion threads, plan each topic with you, then fix → fixup-and-push → draft a
reply per topic. See [SKILL.md](SKILL.md) for the full flow and
[REFERENCE.md](REFERENCE.md) for the Phase-3 mechanics.

## Prerequisites

- `glab` authenticated (`glab auth status`), run on or `--iid N` for the MR branch.
- `python3`; macOS for `clip.sh` (uses `pbcopy`).

## Required setup — the reply-view Stop hook

The reply step shows the whole thread + your drafted reply + the thread URL by
pasting the output of `threads.py reply-view <t>` into chat. Claude Code
**collapses tool output**, so that block only reaches you if the model pastes it
— and the model reliably *drops* it, surfacing just the trailing `c`/`p`/`n`
prompt. `scripts/stop-hook.py` enforces the paste: it inspects the finished turn
and, if `reply-view` ran but its block is missing from the visible message,
**blocks the turn and makes the model re-send the full block**.

Without this hook the skill still works, but on many turns you won't see the
thread or the draft before choosing an action. **Install it once:**

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

- **Scoped:** only acts on a turn where `reply-view` actually ran — every other
  turn (and every non-rework session) passes straight through.
- **Loop-safe:** uses `stop_hook_active` to force at most one retry, so it never
  spins.
- **Fails open:** any error (bad transcript, parse failure) → allows the stop; it
  never wedges a session.

It's a good idea to keep the hook narrow: it's harmless globally because it does
nothing unless a `reply-view` block is present in the turn.

## Scripts

- `threads.py` — fetch/reconcile threads, render tables & the reply block. Run
  `threads.py -h` for subcommands (`sync·todo·present·bodies·plans·quote·url·reply-view·set·merge·path`).
- `reply-view <t>` — the one-paste reply block (thread + draft + URL + prompt);
  also refuses a draft containing an internal topic handle (`t5`…).
- `diff-url.py` — stable per-topic diff URL across force-pushes.
- `clip.sh` — copy a reply body to the clipboard (guards topic handles first).
- `guard-reply.sh` — the topic-handle gate, reused by `clip.sh` and the post step.
- `stop-hook.py` — the Stop hook above.
