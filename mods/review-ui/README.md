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

- rework-mr gets the same: buttons for each step (Agree plan for the plan the agent proposed;
  Ack and push, Review in hunk for a fix made; Post, Post and resolve for a reply),
  rounds through a plan pass and then a fix pass, the inbox, and a background agent that
  prepares a plan for each reviewer's new thread or reply. A fix shows in the sidebar; the
  diff viewer opens on its button alone, and comes first where the agent that made the fix
  says it differs from the plan agreed. A fix is shown once the repository's checks passed on it (its hooks
  run ahead of time, the fast local checks of the parts it touches); Push is then the script's, with no model turn and no
  check run again on the same files. A fix that goes into more than one commit, or makes a
  new one, is handed to the model to commit, and the press goes on from its commits.
- Above the prompt, a band of buttons for the current step (review-mr), the recommended
  answer first and drawn as the primary, Explain after it wherever something is decided
  (Explain further once pressed): Draft and Drop for a new topic; Post, Copy, I posted my own for a draft; for an answer the agent judged, its
  verdict's own answer first: Accept & resolve, Not good enough… and Analyse more, or Send
  follow-up, Accept anyway and Analyse more; Next topic, apart at the row's end, where another waits; for another reviewer's thread, which the user watches, Seen and
  Reply…, with Merge into tN first where the agent advised merging; for one of
  your own comments the agent matched, its recommendation (Merge into tN or Keep separate)
  first among the answers. They sit on the newest card, where a click presses one; a step
  with no card of its own keeps them in the band, where ctrl+x tab, then Tab and Enter,
  presses one too; no button has a hotkey. A button that needs no thought runs the skill's script itself, with no
  model turn, brings up the next card and leaves the model a note of what was decided; the
  others hand the model a turn (`hooks/review.ts`). A new topic's card carries the agent's
  analysis, stored with the finding, so deciding on it needs no turn either.
- What arrives while the review waits (an author's answer, a push that changed the lines a
  comment is on, another reviewer's thread, a comment of your own) never enters the chat by
  itself: the update check marks its topic in the sidebar and lists it in the sidebar's inbox,
  and an agent of the mod's own judges it in the background, so the topic's card carries the
  judgment by the time you get to it. Opening the inbox (`o`) or a topic starts a round
  through everything that needs you; Next topic moves on to the next of them. Away from the session (another app in front, another tmux window), a notification
  says what arrived.
- In a review, the review scripts' calls that print no view are left out of the chat, as the
  views are drawn as cards; ctrl+o shows them.
- Posting is the person's press alone. Post runs `findings.py post` with the version of the
  draft its card showed, and the script refuses if the draft changed since. The mod refuses
  the model's own Bash calls of `post`.
- The model shows a topic with the `show_topic` tool, which the mod answers with the card.
- A panel beside the chat, the sidebar, is areas under the MR's heading, each under a line
  that carries its name and its own keys: the topics (`hooks/panel.ts`) in groups a click
  folds (in review-mr to do, in the order a round visits them, set aside, waiting for author,
  from other reviewers, closed, folded at first), the legend's key in the line; then the
  current item's file with its thread under the lines it is on (or a thread with no file, or
  the inbox between rounds); then the fix's other files, with the diff viewer's key, and in
  rework-mr the comments on the MR itself. Every area but the file's has a height it grows
  to (the list a quarter of the pane, a folded area five rows, opened a third) and scrolls
  inside itself past it, by its arrows at its right corners or the wheel over it; the file
  takes the rest, so the sidebar fits its pane. It opens by itself; its ✕ (or ctrl+x x
  while it has the keys) closes it, and the band's Show sidebar button brings it back.

- In a session the mod draws, the scripts render for it (`lib/ui.py`): one state vocabulary
  for both skills with its icons, Nerd Font icons for severity, kind and source, and an opener
  that is the current topic alone, since the panel has the table. The mod marks the session
  for them with an empty file in `~/.claude/review-ui/sessions/`.

Without the mod (OpenCode, or Claude Code without it) nothing changes: the skills paste their
views as before.

The icons are [Nerd Fonts](https://www.nerdfonts.com/) glyphs: a terminal without one
shows empty boxes in their place (Ghostty, kitty and WezTerm have them built in).

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
python3 mods/test_mods.py                  # tests, type check and validation, as the suite runs them
claude plugin test mods/review-ui          # the tests alone: every surface, no model
python3 e2e/drive.py verify rework-3       # a real session on the local GitLab, on a real model
```

The type check needs the engine's types in `.claude-plugin/types/`, which git does not carry:
they are copied in from the plugin-authoring skill, and a fresh worktree has none until they
are copied there (`check_commits.py` does that for its worktrees).

### What the engine does that its types do not say

Each of these was found the expensive way, in a session; the types file is 17,000 lines and
says none of them.

- An organization's policy plugin can sit above every mod and pass `skill.prompt` and
  `prompt.compose` on unchanged, so a mod cannot rewrite a skill's text or the system prompt
  there. What the model must know goes into SKILL.md, or into a line of the tool output it
  reads (the card note).
- `console.log` in a hook prints nothing in `claude plugin test`. In the test kit, the mod's
  stored state is not reachable (`$.state` is absent), there is no clock unless the test
  registers one, and only the engine sets a spawned agent's id, so an agent's end cannot be
  reached from a test: test the pure function that decides instead (`buttonsFor`).
- A JSX callback parameter named `h` shadows the element factory: the type check passes and
  the hook throws at render. Every `Markdown` key in one card must be unique.
- A key chord bound in the `Global` context takes over its first key everywhere (ctrl+x
  stopped deleting in the session overview); bind in `Chat`.
- A pane is reachable from the keyboard only when opened with `focus: true`. Consecutive shell
  calls fold into one `ToolGroup`, drawn through its own `ui.render` hook. The engine's
  "Prompt dropped by a hook" line cannot be changed by a mod.
- A background agent's report reaches the main loop as a prompt of `origin.kind` `peer`,
  marked as a hand-back.
- A running session keeps the SKILL.md it loaded: a script's arguments must stay usable by
  the older text, and a prompt the mod sends carries the whole contract it relies on.

### What mirrors this mod elsewhere

A change here that moves one of these moves the other in the same commit:

- the view commands (`VIEW_COMMANDS`, `hooks/views.ts`) and the gate specs
  (`skills/*/scripts/paste-gates.json`);
- the chips' caps (`CHIP_CAPS`, `hooks/register.tsx`) and the e2e scenarios' `CAP`
  (`e2e/scenarios.py`), which find buttons by them;
- the panel's icons (`hooks/panel.ts`) and `lib/test_ui.py`'s `ModMirrorTest`.
