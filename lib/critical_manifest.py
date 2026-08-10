"""The critical-lines manifest: a trailing, non-visible payload gated commands (in both
review-mr's findings.py and rework-mr's threads.py) append to their own stdout, so
hooks/paste-gate.py's Stop hook can tell which lines of a rendered block must never be
silently dropped — a table row, a line inside a fenced code block — without re-parsing
the rendered Markdown itself. See hooks/README.md's "The critical-lines manifest"
section for the full mechanism and why the producer, not the hook, is the source of
truth for this.

Shared here (not duplicated per skill) because it is pure and has no coupling to either
skill's state shape — the two implementations were byte-identical modulo a comment
before this move.
"""
import json

_critical = []


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


def manifest():
    """Trailing, non-visible payload for a gated command's stdout: paste-gate.py splits
    this off before checking what the model pasted, so it is never something the model
    is asked to reproduce. Empty when nothing this run built was critical — no marker at
    all beats an empty one, since SKILL.md and paste-gates.json treat a bare mention of
    the marker text as something that must never reach a visible reply."""
    if not _critical:
        return ""
    return "\n\n<!-- paste-gate:critical\n" + json.dumps(_critical) + "\n-->"
