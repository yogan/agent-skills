"""The block manifest: a trailing, non-visible payload gated commands (in both
review-mr's findings.py and rework-mr's threads.py) append to their own stdout, so
hooks/paste-gate.py's Stop hook knows two things it cannot reliably work out for itself —
where the block it must enforce STARTS inside the tool result, and which of its lines must
never be silently dropped (a table row, a line inside a fenced code block). See
hooks/README.md's "The block manifest" section for the full mechanism and why the
producer, not the hook, is the source of truth for both.

Shared here (not duplicated per skill) because it is pure and has no coupling to either
skill's state shape — the two implementations were byte-identical modulo a comment
before this move.
"""

import contextlib
import json
import os

_critical = []


def _paste_gate_enabled():
    """Whether stdout needs metadata for Claude Code's paste-enforcement hook.

    OpenCode shows Bash output, so this internal payload would be visible there. Other
    clients retain the established output unless explicitly overridden; only Claude Code
    and OpenCode are detected. The override supports tests and unusual launchers.
    """
    override = os.environ.get("AGENT_SKILLS_PASTE_GATE")
    if override is not None:
        return override == "1"
    return not bool(os.environ.get("OPENCODE"))


@contextlib.contextmanager
def suspended():
    """Marks made inside the block are discarded.

    For a render produced to be COMPARED rather than shown: both skills re-render a
    topic's context to see whether it still matches what the user was shown, and a
    throwaway render's code lines must not end up in the manifest — the hook would then
    demand lines that are nowhere in the message and block a correct reply.
    """
    global _critical
    outer, _critical = _critical, []
    try:
        yield
    finally:
        _critical = outer


def mark(line):
    """Record `line` (stripped) as critical and return it unchanged, so a call can wrap
    a line's construction in place: `out.append(mark(f"| {...} |"))`."""
    _critical.append(line.strip())
    return line


def reset():
    """The CLI never needs this — every invocation is a fresh interpreter, so `_critical`
    starts empty on its own. A test PROCESS calls the render functions many times across
    many cases, though, and would otherwise see critical lines pile up across unrelated
    tests."""
    _critical.clear()


def current():
    """A snapshot of what's been marked so far. Exists for tests to assert against
    without reaching into the private `_critical` list directly."""
    return list(_critical)


def with_manifest(block):
    """`block`, plus its trailing manifest when Claude Code's paste gate needs it.

    Every gated command goes through here rather than concatenating a payload of its own,
    because the manifest has to describe THIS block and nothing else, and the print site
    is the last place that still knows which text that is.

    The payload carries two things:

    `first` — the block's opening line, so the hook can find where the block begins inside
    a tool result that may hold more than the block. A gated command is not always alone
    in its Bash call: the model chains a formatter or a linter ahead of it (the skill asks
    for silent QA immediately before showing the diff, so combining them into one call is
    the obvious reading), and `2>&1` folds stderr in as well. Those extra lines used to
    count as lines the model had dropped, and two of them were enough to force a retry on
    a message that had pasted the block perfectly — observed on a real MR rework, twice in
    one session, with nothing the model could have done about it.

    `critical` — the lines that must never be silently dropped, whatever they look like
    syntactically. The trailing end needs no equivalent: the marker itself is the boundary.

    Emitted even when nothing was marked critical, unlike the older payload this replaces:
    `first` alone earns it, and a block with no critical lines is exactly as prone to
    being preceded by somebody else's output as any other.
    """
    if not block.strip() or not _paste_gate_enabled():
        return block
    first = next(ln.strip() for ln in block.splitlines() if ln.strip())
    payload = {"first": first, "critical": list(_critical)}
    return block + "\n\n<!-- paste-gate:critical\n" + json.dumps(payload) + "\n-->"
