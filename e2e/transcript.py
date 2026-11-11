#!/usr/bin/env python3
"""What a Claude Code session did, read from its transcript: one line per prompt, reply,
tool call, result head and engine note (a prompt a hook dropped, a Stop hook that held the
turn), in order, with the time.

    python3 e2e/transcript.py <session .jsonl | folder the session ran in> [--since HH:MM]
                              [--full]

A folder means the newest session that ran there (Claude Code keeps a session's transcript
under ~/.claude/projects/, in a folder named after the session's working directory). `--since`
starts at that time of day; `--full` prints results whole instead of their first lines.
`drive.py` writes it beside a failed run's captures.

Every session that diagnosed a driven run used to write a parser for this anew, a dozen in
one session; the screen capture says what was drawn, this says what ran.
"""
import json
import os
import re
import sys

PROJECTS = os.path.expanduser("~/.claude/projects")
HEAD = 300          # characters of a tool result shown without --full


def session_file(target):
    """The .jsonl itself, or the newest one of the sessions that ran in folder `target`."""
    if target.endswith(".jsonl"):
        return target
    # Named after the folder's real path: a session started through a link (`~/src` to where
    # the checkouts are) is filed under where the link points.
    folder = os.path.join(PROJECTS, re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(target)))
    runs = [os.path.join(folder, f) for f in os.listdir(folder) if f.endswith(".jsonl")] \
        if os.path.isdir(folder) else []
    return max(runs, key=os.path.getmtime) if runs else None


def _cut(text, full, head=HEAD):
    text = text.strip()
    if full:
        return text
    text = " ⏎ ".join(ln.strip() for ln in text.splitlines() if ln.strip())
    return text if len(text) <= head else text[:head] + f" …[+{len(text) - head}]"


def _text(content):
    if isinstance(content, str):
        return content
    return "\n".join(b.get("text", "") for b in content or [] if isinstance(b, dict))


def lines(path, since=None, full=False):
    """The session at `path`, one line per event, oldest first."""
    out = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            try:
                e = json.loads(raw)
            except ValueError:
                continue
            at = (e.get("timestamp") or "")[11:16]
            if since and at and at < since:
                continue
            who = "  agent " if e.get("isSidechain") else ""
            kind = e.get("type")
            if kind == "system":
                if e.get("subtype") == "informational" and e.get("content"):
                    out.append(f"[{at}] {who}NOTE  {e['content']}")
                elif e.get("subtype") == "stop_hook_summary" and (
                        e.get("preventedContinuation") or e.get("hookErrors")):
                    out.append(f"[{at}] {who}STOP HOOK  held the turn: "
                               f"{e.get('stopReason') or e.get('hookErrors')}")
                continue
            if kind not in ("user", "assistant"):
                continue
            msg = e.get("message") or {}
            content = msg.get("content")
            if isinstance(content, str):
                content = [{"type": "text", "text": content}]
            for b in content or []:
                t = b.get("type")
                if t == "text" and b.get("text", "").strip():
                    if kind == "user" and b["text"].lstrip().startswith("<system-reminder>"):
                        continue
                    if e.get("isMeta"):         # a skill's text, loaded: its name is enough
                        out.append(f"[{at}] {who}LOADED: {_cut(b['text'], False, 80)}")
                        continue
                    label = "SAY" if kind == "assistant" else "PROMPT"
                    origin = (e.get("origin") or {}).get("kind")
                    out.append(f"[{at}] {who}{label}{f' ({origin})' if origin else ''}: "
                               f"{_cut(b['text'], full)}")
                elif t == "tool_use":
                    inp = b.get("input") or {}
                    what = inp.get("command") or inp.get("file_path") or json.dumps(inp)[:200]
                    out.append(f"[{at}] {who}CALL {b.get('name')}: {_cut(what, full, 400)}")
                elif t == "tool_result":
                    tag = "ERROR" if b.get("is_error") else "RESULT"
                    out.append(f"[{at}] {who}  {tag}: {_cut(_text(b.get('content')), full)}")
    return out


def main(argv):
    if not argv or argv[0].startswith("-"):
        print(__doc__.strip().splitlines()[3].strip(), file=sys.stderr)
        return 2
    since = argv[argv.index("--since") + 1] if "--since" in argv else None
    path = session_file(argv[0])
    if not path:
        print(f"no session ran in {argv[0]}", file=sys.stderr)
        return 1
    print(f"# {path}")
    print("\n".join(lines(path, since, "--full" in argv)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
