# tmux-tab.py — agent sessions named after what they are about

Two things, from one hook: inside tmux, a Claude Code session's window is named after the
session (below); and in any terminal, tmux or not, the shell says what a session was once it
quits ([The line after a session quits](#the-line-after-a-session-quits)).

## The tmux window

A session's window gets a robot icon and a short name for what the session is about, at most
18 characters after the icon, so many windows fit the tab bar:

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
review-mr shows 󱉻 and rework-mr 󱢛, each with the MR's short title, whoever named the window.
The session itself gets the full name, `󱉻 !1234 Title of the MR`, on its next prompt: only a
hook's output can name a session, so the skill leaves the name in
`~/.cache/agent-skills/session-name/<pane>` and the
`UserPromptSubmit` hook hands it over. The tab keeps the skill's icon until the session ends
(`SessionEnd`), also once the MR is merged, closed or cleaned up locally.
split-handoff names the windows it opens the same way (`󱙺 <title>`).

A diff viewer window a skill opens shows 󰢪 and what it holds now, renamed on every load:
the short title of the topic whose diff or fix it shows (review-mr, rework-mr), or of the
commit's subject (self-review), and `uncommitted` for the uncommitted change. Until that
short title is known, the window shows a plain fact instead, never the name of what it held
before: the topic's id (`󰢪 t3`), or the commit's place (`󰢪 commit 2/5`). A viewer window you
renamed by hand keeps your name, and nothing gives a viewer window a name back when the
session ends.

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

Two more. The first is the skills' takeover, awaited (its output names the session), so it
looks for a waiting name before it starts anything. The second, at quit, runs outside tmux
too, for [the line after a session quits](#the-line-after-a-session-quits), and there does
nothing else:

```json
"UserPromptSubmit": { "type": "command",
  "command": "[ -n \"$TMUX_PANE\" ] && [ -e \"${XDG_CACHE_HOME:-$HOME/.cache}/agent-skills/session-name/${TMUX_PANE#%}\" ] && [ -e ~/.claude/hooks/tmux-tab.py ] || exit 0; exec python3 ~/.claude/hooks/tmux-tab.py prompt" }
"SessionEnd": { "type": "command",
  "command": "test -e ~/.claude/hooks/tmux-tab.py || exit 0; exec python3 ~/.claude/hooks/tmux-tab.py end" }
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

## The line after a session quits

Claude Code's last words in the shell are `Resume this session with: claude --resume <id>`,
which do not say what the session was about. The shell adds one dimmed line under them, in
tmux or not:

```
󱉻 !1234 Title of the MR · fix/the-branch
```

the session's title as the /resume list shows it, then the branch it ran on. Inside tmux the
title is the one the status line last handed over for that session in that pane (a skill's
full name, else Claude Code's generated title or your `/rename`); outside tmux, and where the
status line handed none over, it is the last one the session's transcript recorded (your
`/rename`, else the generated title). A session with no title leaves no line.

The `SessionEnd` hook leaves it as a note, `${XDG_CACHE_HOME:-~/.cache}/agent-skills/last-session/<terminal>`:
that line, then the session id. `<terminal>` is the terminal device Claude Code and the shell
share, as the shell's `tty` names it without `/dev/` and with every character other than a
letter, digit, `.`, `_` or `-` turned into `-` (`ttys014` on macOS, `pts-3` for `/dev/pts/3`
on Linux): a tmux pane's own, or the terminal window's. The hook asks `ps` for the terminal of
the Claude Code process it runs for (`$CLAUDE_PID`), one process of about 7ms on a Mac whose
security software inspects each, written before the hook returns, so it is there when the
shell's prompt comes back. A Claude Code with no terminal of its own (an IDE's, one started
by a script) leaves no note, not even where whatever started it has one. A `claude -p` or
`claude mcp list` run in a session's shell has no title, so it never overwrites the note the
session itself leaves; a second session in a tmux split is another terminal, with a note of
its own. The code is [`last_session.py`](last_session.py).

The shell's part prints the note at the first prompt after it appears, and only where the
command that just finished was `claude` (also `command claude`, `VAR=x claude`, or an
abbreviation that expands to it) and the note is less than a minute old; it removes the note
either way, so one left by a crash never shows up later under something else. **Without a
shell part, the hook only writes the note and nothing prints.** The `SessionEnd` hook in
[Install](#install) is needed too, outside tmux as well.

Each shell's part costs a check for one file per prompt, and the name of the shell's terminal once,
when the shell starts (fish and bash run `tty` for it; zsh knows it as `$TTY`). A shell with
no terminal prints nothing. fish, as a file in `~/.config/fish/conf.d/` (e.g.
`claude-last-session.fish`):

```fish
status is-interactive; or return
set -l tty (tty 2>/dev/null); or return
set -l cache $XDG_CACHE_HOME
test -n "$cache"; or set cache ~/.cache
set -g __claude_last_session_note $cache/agent-skills/last-session/(string replace -r '^/dev/' '' -- $tty | string replace -ra '[^A-Za-z0-9._-]' -)

function __claude_last_session --on-event fish_postexec
    set -l note $__claude_last_session_note
    test -f $note; or return
    if string match -qr '^\s*(command\s+)?(\w+=\S*\s+)*claude(\s|$)' -- $argv[1]
        set -l age (path mtime --relative -- $note 2>/dev/null)
        if test -n "$age"; and test "$age" -lt 60
            read -l line <$note
            and test -n "$line"
            and printf '%s%s%s\n' (set_color brblack) $line (set_color normal)
        end
    end
    command rm -f -- $note 2>/dev/null
end
```

zsh, in `~/.zshrc`:

```zsh
autoload -Uz add-zsh-hook
_claude_last_cmd=
_claude_last_preexec() { _claude_last_cmd=$1 }
_claude_last_precmd() {
  local cmd=$_claude_last_cmd; _claude_last_cmd=
  [[ -n $TTY ]] || return 0
  local name=${TTY#/dev/}
  local note=${XDG_CACHE_HOME:-$HOME/.cache}/agent-skills/last-session/${name//[^A-Za-z0-9._-]/-}
  [[ -f $note ]] || return 0
  local -a fresh=( $note(Nms-60) ) line
  if [[ -n $fresh && $cmd =~ '^[[:space:]]*(command[[:space:]]+)?([[:alnum:]_]+=[^[:space:]]*[[:space:]]+)*claude([[:space:]]|$)' ]]; then
    IFS= read -r line < $note && [[ -n $line ]] && print -r -- $'\e[90m'"$line"$'\e[0m'
  fi
  rm -f -- $note 2>/dev/null
}
add-zsh-hook preexec _claude_last_preexec
add-zsh-hook precmd _claude_last_precmd
```

bash, in `~/.bashrc` (the command comes from the history, so it needs history on):

```bash
__claude_last_tty=$(tty 2>/dev/null) || __claude_last_tty=
__claude_last_session() {
  [[ -n $__claude_last_tty ]] || return 0
  local name=${__claude_last_tty#/dev/} cmd line
  local note=${XDG_CACHE_HOME:-$HOME/.cache}/agent-skills/last-session/${name//[^A-Za-z0-9._-]/-}
  [[ -f $note ]] || return 0
  cmd=$(HISTTIMEFORMAT= history 1); cmd=${cmd#*[0-9] }
  local re='^[[:space:]]*(command[[:space:]]+)?([[:alnum:]_]+=[^[:space:]]*[[:space:]]+)*claude([[:space:]]|$)'
  if [[ $cmd =~ $re && -n $(find "$note" -mmin -1 2>/dev/null) ]]; then
    IFS= read -r line < "$note" && [[ -n $line ]] && printf '\e[90m%s\e[0m\n' "$line"
  fi
  rm -f -- "$note" 2>/dev/null
}
PROMPT_COMMAND="__claude_last_session${PROMPT_COMMAND:+; $PROMPT_COMMAND}"
```

## Tests

`python3 lib/test_tab.py`: tmux is a fake holding one window, the model is never asked.
`python3 hooks/test_last_session.py`: the note at quit, with a fake process table for `ps`. Code
that would name a window from inside a test (the skills' `present`) does nothing where
`AGENT_TAB_OFF` is set: `run_tests.py`, the skills' test files and the e2e rig's verify runs
set it, so no test renames the window it runs in or calls the model.
