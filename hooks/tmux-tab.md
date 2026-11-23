# tmux-tab.py — agent windows named after their session

Inside tmux, a Claude Code session's window gets a robot icon and a short name for what the
session is about, at most 18 characters after the icon, so many windows fit the tab bar:

- at start, the session's `--name`, else its repository's folder name;
- once the session has a title (the one Claude Code generates, or your `/rename`), that
  title where it fits; a longer one waits, the tab unchanged, for a two-word title the
  smallest model writes a few seconds later (`claude -p` with no settings, hooks, MCP servers
  or tools; one call per title, cached in `~/.cache/agent-skills/tab-titles.json`). A tab
  never shows a title cut mid-thought, unless the model could not be asked.

A window you named yourself is never renamed, and neither is one renamed by hand since. When
Claude Code quits, a window it named gets back what it had before: a fresh window goes back
to tmux naming it after its process. The window belongs to the Claude Code process that
started in it: a `claude` run inside it while that one lives (`claude mcp list` or `claude -p`
from the session's own shell, which fire `SessionStart`/`SessionEnd` in that pane too), or a
second session in a split of the window, neither names it nor gives its name back. After a
`/clear` the same process goes on, so the window stays its. The icons and the logic are in
[`lib/tab.py`](../lib/tab.py).

The skills that work through an MR take their session's tab over while they do (`tab.take`):
review-mr shows 󱉻 and rework-mr 󱢛, each with the MR's short title, whoever named the window,
and their diff viewer windows show 󰢪 with the same title. The session itself gets the full
name, `󱉻 !1234 Title of the MR`, on its next prompt: only a hook's output can name a session,
so the skill leaves the name in `~/.cache/agent-skills/session-name/<pane>` and the
`UserPromptSubmit` hook hands it over. The tab keeps the skill's icon until the session ends
(`SessionEnd`), also once the MR is merged, closed or cleaned up locally. Their
viewers show the MR's number until its short title is known.
split-handoff names the windows it opens the same way (`󱙺 <title>`).

## Install

Everything outside this repository checks for the link first, so a machine without the
repository runs as before:

```bash
ln -sfn ~/src/agent-skills/hooks/tmux-tab.py ~/.claude/hooks/tmux-tab.py
```

`~/.claude/settings.json`, a `SessionStart` hook:

```json
{ "type": "command", "async": true,
  "command": "test -e ~/.claude/hooks/tmux-tab.py || exit 0; exec python3 ~/.claude/hooks/tmux-tab.py start" }
```

Two more, for the skills' takeover. The first is awaited (its output names the session), so
it looks for a waiting name before it starts anything:

```json
"UserPromptSubmit": { "type": "command",
  "command": "[ -n \"$TMUX_PANE\" ] && [ -e \"${XDG_CACHE_HOME:-$HOME/.cache}/agent-skills/session-name/${TMUX_PANE#%}\" ] && [ -e ~/.claude/hooks/tmux-tab.py ] || exit 0; exec python3 ~/.claude/hooks/tmux-tab.py prompt" }
"SessionEnd": { "type": "command",
  "command": "[ -n \"$TMUX_PANE\" ] && [ -e ~/.claude/hooks/tmux-tab.py ] || exit 0; exec python3 ~/.claude/hooks/tmux-tab.py end" }
```

The title reaches a hook nowhere else: only the status line command sees it
(`session_name`). Yours hands it over when it changed, without waiting:

```js
// in the status line's stdin handler, with `input` the JSON Claude Code sent
const title = data.session_name, pane = process.env.TMUX_PANE;
// once per change of title for this session in this pane (a note in
// ~/.cache/agent-skills/tab-seen/<session_id><pane>), then:
spawn('python3', [script, 'title', title], { detached: true, stdio: 'ignore' }).unref();
```

## The icon's states

tmux draws them, from the window's name and two marks; nothing here runs for it. In
`~/.tmux.conf` a window whose name starts with an agent icon shows its filled twin while its
bell is on (the session finished and wants you) instead of the bell icon. While a prompt
waits for an answer, a session's robot turns confused, when one-line `async` hooks set
`@agent_ask` on the window (`PermissionRequest`, `PreToolUse` on `AskUserQuestion`, the
permission and elicitation `Notification`s) and clear it (`PostToolUse`,
`PostToolUseFailure`, `PermissionDenied`, `UserPromptSubmit`, `ElicitationResult`, and
`Stop`, the one awaited, so a turn never ends on a stale mark). They are background hooks
because one runs after every tool call: a waited-for shell and tmux call cost a turn about
27ms per tool call on a Mac with process inspection, against nothing in the background.

## Tests

`python3 lib/test_tab.py`: tmux is a fake holding one window, the model is never asked. Code
that would name a window from inside a test (the skills' `present`) does nothing where
`AGENT_TAB_OFF` is set: `run_tests.py`, the skills' test files and the e2e rig's verify runs
set it, so no test renames the window it runs in or calls the model.
