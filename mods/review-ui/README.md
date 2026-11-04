# review-ui

A Claude Code mod for the `review-mr` and `rework-mr` skills. Each view the skills' scripts
print (the overview table, a topic with its code and thread, a drafted comment, a diff) is
drawn as a card under the tool call that printed it, exactly as printed. The model no longer
pastes views into its reply, so its messages shrink to its judgment and the question.

How it fits together:

- `hooks/views.ts` decides which commands print a view and what of their output is the view.
  A view of a topic other than the current one is research and gets no card.
- The model reads one extra line at the end of such output, `[review-ui] Shown to the user as
  a card.`; both skills' `SKILL.md` say what it means. The line states a fact and gives no
  order, because the model rightly ignores instructions inside tool output.
- `hooks/paste-gate.py` stands down for a session in which that line ended a tool result.

Without the mod (OpenCode, or Claude Code without it) nothing changes: the skills paste their
views as before.

## Install

At the prompt of a Claude Code session:

```
/plugin install review-ui --marketplace yogan/agent-skills
```

From a clone you keep, so edits to it take effect after `/reload-plugins`:

```sh
claude plugin marketplace add ~/src/agent-skills
claude plugin install review-ui@yogan-agent-skills
```

## Developing

```sh
claude plugin validate mods/review-ui
claude plugin test mods/review-ui          # every surface, no model
python3 e2e/drive.py verify rework-3       # a real session on the local GitLab (e2e/README.md)
```
