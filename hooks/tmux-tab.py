#!/usr/bin/env python3
"""A Claude Code session's tmux window named after what the session is about (`lib/tab.py`).

    start         the SessionStart hook: the window gets the robot and the session's name
                  (`--name`), else its repository's, where nobody else named the window
    title <text>  the status line, once the session's title changed (generated, or
                  `/rename`): the window gets its short form, at once where it fits or
                  a model shortened it before, else from a background `shorten`
    shorten [--icon <icon>] [--hold] <text>
                  that background run: the smallest model's short form, cached per title,
                  put in the tab where that title is still the one wanted there
    prompt        the UserPromptSubmit hook: names the session as a skill asked
                  (`tab.take`), in its output; settings.json starts it only where a name
                  waits for this pane
    end           the SessionEnd hook: leaves the terminal's shell a note of what the
                  session was (`hooks/last_session.py`), in tmux or not; then, in tmux, a
                  skill's hold on the tab ends, and a window this named gets back what it
                  had before Claude Code started in it, from a detached copy of itself,
                  since a slow SessionEnd hook is cancelled at quit

`start` and `end` act on the window only for the Claude Code process it belongs to: a
`claude` run inside a session's pane fires them too, with its own session and pid.

Linked from ~/.claude/hooks/tmux-tab.py; settings.json and the status line call it only
where that link exists, so a machine without this repository runs as before. Outside tmux
it only leaves that note; inside the shortener's own `claude -p` it does nothing.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path[:0] = [HERE, os.path.dirname(HERE)]              # last_session beside it, and lib


def repo_name(cwd):
    """The checkout's folder name (a worktree's own), else the folder's."""
    from lib.git import git
    top = ""
    try:
        top = (git("rev-parse", "--show-toplevel", cwd=cwd, check=False) or "").strip()
    except Exception:  # noqa: BLE001 — no git, not a checkout: the folder names it
        pass
    return os.path.basename(top or os.path.abspath(cwd or "."))


def _detach():
    """Fork: True in the hook, which returns at once; False in the copy that carries on, in a
    session of its own, so the hook being cancelled does not end it. Neither holds the
    hook's pipes from before the fork on: Claude Code reads them to their end, and a copy
    slow to be scheduled would keep them open."""
    null = os.open(os.devnull, os.O_RDWR)
    for fd in (0, 1, 2):
        os.dup2(null, fd)
    if os.fork():
        return True
    os.setsid()
    return False


def main(argv, env=None, stdin=None, detach=False):
    env = os.environ if env is None else env
    pane = env.get("TMUX_PANE")
    if not argv:
        return 0
    cmd = argv[0]
    data = {}
    if cmd in ("start", "end"):
        try:
            data = json.load(stdin or sys.stdin)
        except ValueError:
            pass
    # Claude Code gives its SessionEnd hooks 1.5s together while it quits, and cancels one
    # still running then; on a loaded Mac whose security software inspects every process,
    # each process started costs a large part of that. So the hook returns before anything
    # that costs but the note: before the imports below, any tmux call, or another Python.
    quitting = cmd == "end" and data.get("reason") != "clear"
    # The note the shell prints once Claude Code is gone, in any terminal, so before the
    # check for tmux, and before the fork: the detached copy may not have run by the time
    # the prompt comes back. Not where lib/tab.py's OFF_ENV (tests) or SHORTENER_ENV is set.
    if quitting and not env.get("AGENT_TAB_OFF") and not env.get("AGENT_TAB_SHORTENER"):
        import last_session
        last_session.leave_note(env, data)
    if not pane and cmd != "shorten":     # outside tmux; `shorten` may only fill the cache
        return 0
    if cmd in ("start", "end"):
        # The Claude Code process the hook runs for: it sets CLAUDE_PID to its own pid, also
        # in a `claude` started inside another session's pane, which fires these hooks too.
        pid = env.get("CLAUDE_PID", "")
        pid = int(pid) if pid.isdigit() else os.getppid()
    if quitting and detach and _detach():
        return 0
    from lib import tab
    if env.get(tab.SHORTENER_ENV) or env.get(tab.OFF_ENV):
        return 0
    if cmd in ("start", "end") and tab.another_owns(pane, pid):
        return 0
    if cmd == "start":
        if data.get("source", "startup") not in ("startup", "resume"):
            return 0
        tab.own(pane, pid)
        tab.show(pane, tab.AGENT_ICON, data.get("session_title") or repo_name(data.get("cwd")))
    elif cmd == "title" and len(argv) > 1:
        tab.prune_seen()
        tab.show(pane, tab.AGENT_ICON, argv[1])
    elif cmd == "end":
        # A /clear ends one session and starts the next in the same process: nothing to undo.
        if data.get("reason") == "clear":
            return 0
        tab.restore(pane)
    elif cmd == "prompt":
        # The name a skill asked for: only a hook's output can name the session.
        name = tab.take_session_name(pane)
        if name:
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                                     "sessionTitle": name}}))
    elif cmd == "shorten":
        icon, keep, rest = tab.AGENT_ICON, False, argv[1:]
        while rest[:1] in (["--icon"], ["--hold"]):
            if rest[0] == "--icon" and len(rest) > 1:
                icon, rest = rest[1], rest[2:]
            else:
                keep, rest = True, rest[1:]
        if not rest:
            return 0
        title = rest[0]
        short = tab.ask_model(title)
        if short:
            tab.remember(title, short)
        tab.finish(pane, icon, title, short, keep)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:], detach=True))
