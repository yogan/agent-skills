# paste-gate.py — the paste-enforcement `Stop` hook

One hook, shared by [`review-mr`](../skills/review-mr) and [`rework-mr`](../skills/rework-mr).
It makes sure that when a skill prints a block for you — an overview table, a quoted topic
with its code, a drafted reply, a diff before a force-push ACK — that block actually
reaches your chat message instead of being paraphrased away.

## Why it exists

Claude Code **collapses tool output**: the chat message is your only window. Both skills
lean on that — a command renders a finished block and the skill instructs the model to
paste it verbatim as its whole message. The model reliably drops it: it decides what to
paste, makes one more tool call (research, a `git blame`, a Read) before writing the
message, and that intervening call pushes the block out of mind. The reply opens with the
model's own prose, or jumps straight to the trailing question or ACK, and the promised
content is never shown. You get "topic t2 needs you" — no table, no `file:line`, no code,
and sometimes an ACK request for a fixup and force-push you cannot see.

Documentation does not fix this. In `review-mr` it was tried three times (a "paste
verbatim" rule at the top of SKILL.md, a single `resume` command so there was no sequence
to skip, then an explicit rule 0 naming the failure) and the model still paraphrased. So
it is enforced: the hook inspects the finished turn and, if a gated command ran but its
output is missing from the visible message, it **blocks the turn and makes the model
re-send the full block**.

Without the hook both skills still work — you just won't see the overview, the comment,
the code, the diff or the draft on many of the turns where the decision is yours.

## Install

**This needs a manual edit to `~/.claude/settings.json`** — skills cannot register their
own hooks. Link the engine once, then register it with one spec argument per installed
skill:

```bash
mkdir -p ~/.claude/hooks
ln -sfn ~/src/agent-skills/hooks/paste-gate.py ~/.claude/hooks/paste-gate.py
```

```jsonc
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 ~/.claude/hooks/paste-gate.py ~/.claude/skills/review-mr/scripts/paste-gates.json ~/.claude/skills/rework-mr/scripts/paste-gates.json"
          }
        ]
      }
    ]
  }
}
```

Use absolute paths if `~` is not expanded in your setup. A spec path that does not exist
is skipped silently — that is how "this skill is not installed" is expressed, so you can
leave both arguments in place and install the skills independently. Restart Claude Code so
the hook loads.

## How it behaves

- **Scoped:** only acts on a turn where a gated command actually ran and produced real
  output, or where a `forbidden`/`required` pattern hits. Every other turn — and every
  session that never touches these skills — passes straight through.
- **Loop-safe:** `stop_hook_active` forces at most one retry per reply, so it never spins.
- **Fails open:** any error (unreadable transcript, malformed spec, a bug in the engine)
  allows the stop. It can never wedge a session.
- **Marker-free:** nothing is added to the pasted block, so your reply stays clean. A gate
  matches a Bash invocation to *its own* paired tool result by `tool_use` id, which is why
  merely reading or grepping a skill's source — SKILL.md and these specs mention every
  gated command by name — does not trip it.
- **Patient:** the transcript is written asynchronously (a block was once observed 348 ms
  after the message was produced), so a turn that looks like a violation is re-read with a
  widening delay before it is blocked. Only turns that are genuinely about to be blocked
  pay the wait.

## Gate specs

The engine is skill-agnostic; what to enforce is data, in each skill's
`scripts/paste-gates.json`. Three rule kinds, all optional:

```jsonc
{
  "skill": "review-mr",              // namespaces gate keys across specs
  "gates": [                         // a command ran → its output must be in the message
    {
      "key": "resume",               // referenced by `required` rules
      "cmd": "findings\\.py\\s+resume\\b",   // regex, matched against the Bash command
      "signature": ["**MR !", "| State |"],  // literal strings a SUCCESSFUL run prints
      "reason": "…what the model must do to fix it…"
    }
  ],
  "forbidden": [                     // must never appear in a visible message
    { "text": "^[>\\s]*```suggestion", "flags": "m", "reason": "…" }
  ],
  "required": [                      // may only appear once a gate has fired this turn
    { "text": "^[\\s>*_`]*ACK to fix up and push", "flags": "m",
      "gate": "diff-view", "reason": "…" }
  ]
}
```

- `flags` is a string of `m`/`i`/`s` (`re.M`/`re.I`/`re.S`).
- `signature` only separates a real block from an error or empty result — the actual
  enforcement is the line-by-line check that the run's own output text reappears in the
  message, so a reply that merely *talks about* the markers cannot satisfy a gate.
- Any `note` key is ignored by the engine; the specs use it for the evidence behind a rule
  (JSON has no comments).
- `forbidden` and `required` are the inverse checks: they catch the model **composing** a
  block itself, for which no command runs at all and so no gate can see it. Keep their
  patterns distinctive and line-anchored — a phrase that legitimate prose might contain
  will block correct turns. (`rework-mr` deliberately has no `required` rule for
  `change-preview`'s `Agreed?` for exactly that reason: the non-trivial branch ends with a
  plain discussion question, which can legitimately be phrased that way.)

Caveat when hacking on the skills themselves: `forbidden`/`required` patterns match your
*visible message*, including when you are quoting a skill's own wording back at the user.
Both current rules are line-anchored so that mid-sentence mentions are fine, but a
message that reproduces one of those lines verbatim, at a line start, will be blocked.

The same goes for `gates`, one step further: merely reading or grepping a script is safe
(the gate needs the command's own tool result to carry its output signature), but *running*
one to see what it prints — developing it, verifying a fix — is indistinguishable from
running it for the user, so the turn gets blocked for not pasting the output. It happened
while writing `change-preview.sh`'s new fence handling, and there is deliberately no
heuristic against it: any rule loose enough to exempt "I was only testing" would be loose
enough to exempt the real failure. The loop guard makes it cost exactly one retry, so the
answer is to say what happened and move on, not to contort the message into satisfying it.

### Adding a skill

Ship a `paste-gates.json` next to its scripts and add the path to the hook command. No
engine change, no second hook entry.

## Tests

```bash
python3 hooks/test_paste_gate.py
```

Stdlib only, ~25 s — every case that ends in a block pays the engine's own re-read delay,
which is the point of it.

Each case is either the hook's contract or a bug found in production: the paraphrase, the
interleaved-but-complete paste that used to false-block, the stale re-render, the `isMeta`
row that used to cut the turn short, the source grep that must not trip a gate, the odd
`tool_result` shapes, and the fail-open paths. The hook runs as a subprocess against a
synthetic transcript, because that is exactly its contract: transcript JSONL plus stdin
JSON in, `{"decision":"block"}` or silence out.

One case is about the design's own blind spot: a malformed spec is dropped silently, so
`TestShippedSpecs` asserts that both shipped specs actually load, with the gate keys they
are supposed to have. Without it, a typo in a regex would disable a skill's enforcement
without a sound.
