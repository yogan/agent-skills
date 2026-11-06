"""Which tmux pane this agent runs in, also after Claude Code moved it to the background.

A session inside tmux normally carries `TMUX` and `TMUX_PANE`. A background session does
not: pressing ← ("for agents") or `/bg` moves the conversation into a host process under
Claude Code's daemon, which deletes terminal-identity variables from its environment, and
from then on every shell the agent runs comes from there. The process tree is no help
either: the daemon is shared by all background sessions and detaches itself from the
terminal that started it, so a session's ancestry never passes through the pane it is
watched from.

What does survive is Claude Code's own record of its sessions, one JSON file per process
in ~/.claude/sessions/. After ←, the `claude` in the pane keeps running as the viewer,
and its record keeps the pane (`"tmux": "<session>:@<window>.%<pane>"`) and names the
session it moved to the background (`"parkedJobId"`), which the background session's own
record carries as `"jobId"`. That pair is the link used here, and only while tmux still
shows that same `claude` in that pane.

A session opened again from the agents view (enter on it) is shown by a `claude attach
<job>` in the pane instead, which writes no record at all. Its command line names the job,
so a pane whose processes include exactly one such client is the second link.

What the link cannot tell: the record names the session the pane PARKED, not the one it
shows now. Switching that pane to another session in the overview leaves it unchanged, so
a window may then open beside a pane that shows a different session. There is no record
of the latter at all; the daemon keeps it in memory. And after `/bg`, or once the pane's
`claude` has exited, there is no viewer record left, so there is no pane:
`background_hint` is what to tell the user then.

The records are Claude Code internals, not an interface. Everything here reads them
defensively and degrades to "no pane", which every caller already handles as "not in
tmux".
"""
import glob
import json
import os
import subprocess

SESSIONS = os.path.join(os.path.expanduser("~"), ".claude", "sessions")


def _run(argv):
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return 1, ""
    return p.returncode, p.stdout


def _records():
    """Every session record Claude Code has written, skipping any that cannot be read: a
    record is rewritten in place while its session runs."""
    out = []
    for path in glob.glob(os.path.join(SESSIONS, "*.json")):
        try:
            with open(path) as fh:
                rec = json.load(fh)
        except (OSError, ValueError):
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def _own_record(env, records):
    """This session's record: the file of the process `CLAUDE_PID` names, or failing that
    the one with this session's id. Whether a session runs in the background is only
    known from here: `CLAUDE_CODE_SESSION_KIND` is set on the host process but does not
    reach the agent's shells."""
    pid, sid = env.get("CLAUDE_PID"), env.get("CLAUDE_CODE_SESSION_ID")
    for rec in records:
        if (pid and str(rec.get("pid")) == pid) or (sid and rec.get("sessionId") == sid):
            return rec
    return None


def background_job(env=None, records=None):
    """This background session's job id (`claude stop`/`attach` take it), or "" for a
    session that is not in the background."""
    env = os.environ if env is None else env
    rec = _own_record(env, _records() if records is None else records)
    return str(rec["jobId"]) if rec and rec.get("kind") == "bg" and rec.get("jobId") else ""


def in_background(env=None, records=None):
    env = os.environ if env is None else env
    return bool(background_job(env, records))


def _parents(run):
    rc, out = run(["ps", "-A", "-o", "pid=", "-o", "ppid="])
    parents = {}
    for line in out.splitlines() if rc == 0 else ():
        fields = line.split()
        if len(fields) == 2 and fields[0].isdigit() and fields[1].isdigit():
            parents[int(fields[0])] = int(fields[1])
    return parents


def _runs_in(pane, pid, run):
    """Whether tmux's `pane` still holds process `pid` (the pane's own process or one of
    its descendants). A record outlives a client that crashed, and pane ids and pids are
    both reused, so the record alone does not prove where anything is."""
    rc, out = run(["tmux", "display-message", "-p", "-t", pane, "#{pane_pid}"])
    if rc != 0 or not out.strip().isdigit():
        return False
    pane_pid, parents, seen = int(out.strip()), _parents(run), set()
    while pid and pid not in seen:
        if pid == pane_pid:
            return True
        seen.add(pid)
        pid = parents.get(pid)
    return False


def own_pane(env=None, run=None, records=None):
    """The id (`%12`) of the tmux pane this agent is shown in, or "".

    `run(argv) -> (returncode, stdout)` executes the tmux and ps lookups, so a caller that
    routes every subprocess through one function of its own (and its tests) can pass it.
    """
    env = os.environ if env is None else env
    if env.get("TMUX_PANE"):
        return env["TMUX_PANE"]
    records = _records() if records is None else records
    job = background_job(env, records)
    if not job:
        return ""
    run = run or _run
    viewers = [r for r in records if r.get("kind") == "interactive"
               and str(r.get("parkedJobId") or "") == job and r.get("tmux")]
    if len(viewers) != 1:
        return _attached_pane(job, env.get("CLAUDE_CODE_SESSION_ID") or "", run)
    pane = str(viewers[0]["tmux"]).rpartition(".")[2]
    pid = viewers[0].get("pid")
    if not pane.startswith("%") or not isinstance(pid, int):
        return ""
    return pane if _runs_in(pane, pid, run) else ""


def _attached_pane(job, sid, run):
    """The pane a `claude attach <job>` runs in, for the session with that job id (or
    session id), or "" unless exactly one such client runs, inside a pane."""
    rc, out = run(["ps", "-A", "-o", "pid=", "-o", "ppid=", "-o", "command="])
    if rc != 0:
        return ""
    parents, clients = {}, []
    for line in out.splitlines():
        fields = line.split()
        if len(fields) < 2 or not (fields[0].isdigit() and fields[1].isdigit()):
            continue
        pid, args = int(fields[0]), fields[2:]
        parents[pid] = int(fields[1])
        names = [os.path.basename(a) for a in args]
        at = names.index("attach") if "attach" in names else -1
        if 0 < at < len(args) - 1 and "claude" in names[:at] and args[at + 1] in (job, sid):
            clients.append(pid)
    if len(clients) != 1:
        return ""
    rc, out = run(["tmux", "list-panes", "-a", "-F", "#{pane_id} #{pane_pid}"])
    panes = {int(b): a for a, _, b in (ln.strip().partition(" ") for ln in out.splitlines() if rc == 0)
             if a.startswith("%") and b.isdigit()}
    pid, seen = clients[0], set()
    while pid and pid not in seen:
        if pid in panes:
            return panes[pid]
        seen.add(pid)
        pid = parents.get(pid)
    return ""


def background_hint(env=None, records=None):
    """Why a background session has no pane, and how to get one back, for the user."""
    env = os.environ if env is None else env
    job = background_job(env, records) or "<id>"
    sid = env.get("CLAUDE_CODE_SESSION_ID") or job
    return (f"this session runs in the background (← for agents, or /bg, moved it there) "
            f"and Claude Code does not say which tmux pane shows it, so there is no pane to "
            f"open a window beside. "
            f"To bring it back to the foreground: `claude stop {job}`, then "
            f"`claude --resume {sid}` in a tmux pane")
