"""The line a Claude Code session leaves its terminal's shell when it quits: what it was.

Claude Code's last words in the shell are `Resume this session with: claude --resume <id>`,
which do not say what the session was about. Its SessionEnd hook (`hooks/tmux-tab.py end`)
calls `leave_note` first, before anything slow, and the shell prints the note under those
words when its prompt comes back; the shell's part is in hooks/tmux-tab.md.

The note is keyed by the terminal both sides share: the shell asks `tty`, the hook asks for
the controlling terminal of the Claude Code process it runs for. Inside tmux that is the
pane's, outside it the terminal window's, so one scheme serves both.
"""
import json
import os
import subprocess


def _cache(env):
    return os.path.join(env.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "agent-skills")


def _transcript_title(path, tail=1 << 18):
    """The session's title as its transcript last recorded it: a `/rename` or a skill's name
    (`custom-title`) over the one Claude Code generated (`ai-title`). Both are written again
    and again, so the end of a long transcript holds them; a `claude -p` has neither."""
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - tail))
            lines = fh.read().decode("utf-8", "replace").splitlines()
    except (OSError, TypeError, ValueError):
        return ""
    found = {}
    for line in reversed(lines):
        for kind, field in (("custom-title", "customTitle"), ("ai-title", "aiTitle")):
            if kind not in found and f'"{kind}"' in line:      # parse only what may be one
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if isinstance(entry, dict) and entry.get("type") == kind:
                    found[kind] = str(entry.get(field) or "").strip()
    return found.get("custom-title") or found.get("ai-title") or ""


def session_title(env, data):
    """The session's title as the /resume list shows it: inside tmux the one the status line
    last handed over for this session in this pane (its note in tab-seen, see `lib/tab.py`'s
    SEEN), else the transcript's. Empty for a `claude -p` or `claude mcp list` run in the
    session's shell: no status line runs for it, and its transcript holds no title."""
    sid, pane = data.get("session_id") or "", env.get("TMUX_PANE") or ""
    if sid and pane:
        try:
            with open(os.path.join(_cache(env), "tab-seen", sid + pane.replace("%", "-"))) as fh:
                title = fh.read().strip()
            if title:
                return title
        except OSError:
            pass
    return _transcript_title(data.get("transcript_path"))


def branch(cwd):
    """The branch checked out at `cwd` (a short commit id where detached), read from the
    files, since a git process costs too much of the time a quitting session gives its hook."""
    d = os.path.abspath(cwd or ".")
    while True:
        dot = os.path.join(d, ".git")
        if os.path.exists(dot):
            break
        if os.path.dirname(d) == d:
            return ""
        d = os.path.dirname(d)
    try:
        if os.path.isfile(dot):                       # a worktree's or submodule's pointer
            with open(dot) as fh:
                gitdir = fh.read().strip().removeprefix("gitdir:").strip()
            dot = os.path.join(d, gitdir)
        with open(os.path.join(dot, "HEAD")) as fh:
            head = fh.read().strip()
    except OSError:
        return ""
    return head.removeprefix("ref: refs/heads/") if head.startswith("ref: ") else head[:7]


def _ps(pid):
    """(parent pid, terminal) of `pid` as ps reports them; the terminal is `??` (macOS) or `?`
    (Linux) where the process has none. One process for one pid: about 7ms on a Mac whose
    security software inspects every process, where listing them all costs about 45ms."""
    out = subprocess.run(["ps", "-o", "ppid=", "-o", "tty=", "-p", str(pid)],
                         capture_output=True, text=True, timeout=1).stdout.split()
    return (int(out[0]), out[1]) if len(out) == 2 and out[0].isdigit() else (0, "")


def _real(tty):
    return bool(tty) and tty.strip("?-") != ""


def terminal(env, ps=None, hops=3):
    """The controlling terminal of the Claude Code process the hook runs for, as ps names it
    (`ttys014`, `pts/3`), or "" where it has none (an IDE's, a pipeline's). Claude Code sets
    CLAUDE_PID to its own pid: that process's terminal, and never a parent's, which would be
    a terminal Claude Code was only started from. Without it, the hook's nearest ancestor that
    has one: Claude Code starts its hooks without a terminal, through `sh -c`."""
    ps, pid = ps or _ps, env.get("CLAUDE_PID", "")
    try:
        if pid.isdigit():
            tty = ps(int(pid))[1]
            return tty if _real(tty) else ""
        pid = os.getppid()
        for _ in range(hops):
            ppid, tty = ps(pid)
            if _real(tty):
                return tty
            if ppid <= 1:
                break
            pid = ppid
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    return ""


def note_name(tty):
    """The file name a terminal's note has, from either side's name for it: the shell's
    `/dev/ttys014` and ps's `ttys014` both give `ttys014`, `/dev/pts/3` and `pts/3` give
    `pts-3`. Every character but a letter, digit, `.`, `_` or `-` becomes `-`; the shell
    snippets in hooks/tmux-tab.md do the same."""
    name = tty.removeprefix("/dev/")
    return "".join(c if c.isascii() and (c.isalnum() or c in "._-") else "-" for c in name)


def note_path(env, tty):
    return os.path.join(_cache(env), "last-session", note_name(tty))


def leave_note(env, data, ps=None):
    """What the session that just quit was, for its terminal's shell to print under Claude
    Code's own "Resume this session with": `<title> · <branch>` on the first line, the session
    id on the second. Written per terminal, so a second session in a split leaves its own
    shell its own line, and only where the session has a title and a terminal: a `claude -p`
    run in the session's shell has no title, so it never overwrites what the session itself
    will leave. Never fails."""
    try:
        title = session_title(env, data)
        if not title:
            return False
        tty = terminal(env, ps)
        if not tty:
            return False
        line = " · ".join(x for x in (title, branch(data.get("cwd"))) if x)
        path = note_path(env, tty)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.{os.getpid()}"
        with open(tmp, "w") as fh:
            fh.write(f"{' '.join(line.split())}\n{data.get('session_id') or ''}\n")
        os.replace(tmp, path)
        return True
    except Exception:  # noqa: BLE001 — a note is never worth a failed quit
        return False
