"""A proposed change as a unified diff that parses, from what an agent writes.

An agent proposing a small fix used to write its diff by hand, and a hand-written diff goes
wrong in ways that read fine to the agent and break the drawing: hunk counts that do not add
up, the diff wrapped in a fence of its own, unchanged lines skipped with `...`. A viewer that
holds a hunk to its header then draws the whole change as plain text.

So the agent names the lines it replaces and gives their new text, and the diff is built here
from the file as it is:

    @@ src/features/comments/api/query-keys.ts:1-3
    export const commentKeys = {
      all: ['comments'] as const,
      list: (discussionId: string) => [...commentKeys.all, discussionId] as const,

One `@@ <path>:<first>-<last>` (or `:<line>` for one line) per range, each followed by the
lines that take its place; none replaces the range with nothing. A diff written by hand is
still taken where it is a clean one: an outer fence is unwrapped and its hunk counts are set
from its lines (`fences.recount`), and anything else in it is refused with the reason.
"""
import difflib
import os
import re

from lib.fences import recount

RANGE_HEAD = re.compile(r"^@@ (\S+?):(\d+)(?:-(\d+))?\s*$")
FENCE = re.compile(r"^\s*(`{3,}|~{3,})[^\n]*\n(.*?)\n\s*\1\s*$", re.S)
DIFF_LINE = re.compile(r"^(?:[ +\-\\]|@@ |--- |\+\+\+ |diff --git |index |new file mode |deleted file mode |$)")
CONTEXT = 1


def of(root, text):
    """The change `text` stands for, as a unified diff: built from the named lines of the
    files under `root`, or the hand-written diff cleaned. Raises ValueError saying what is
    wrong, in words the agent can act on."""
    text = (text or "").strip("\n")
    if not text.strip():
        raise ValueError("the change is empty")
    m = FENCE.match(text)
    if m:
        text = m.group(2)
    first = next((ln for ln in text.splitlines() if ln.strip()), "")
    return from_ranges(root, text) if RANGE_HEAD.match(first) else clean(text)


def from_ranges(root, text):
    """The diff for `@@ path:first-last` blocks, each replacing those lines of the file."""
    blocks, cur = [], None
    for ln in text.splitlines():
        m = RANGE_HEAD.match(ln)
        if m:
            cur = [m.group(1), int(m.group(2)), int(m.group(3) or m.group(2)), []]
            blocks.append(cur)
        else:
            cur[3].append(ln)
    out = []
    by_file = {}
    for path, lo, hi, new in blocks:
        by_file.setdefault(path, []).append((lo, hi, new))
    for path, ranges in by_file.items():
        full = os.path.join(root, path)
        if not os.path.isfile(full):
            raise ValueError(f"{path} is not a file in the repository (paths are from its root)")
        with open(full, encoding="utf-8", errors="replace") as f:
            old = f.read().splitlines()
        lines = list(old)
        # Replaced from the bottom up, so each range's numbers are the file's own.
        for lo, hi, new in sorted(ranges, key=lambda r: r[0], reverse=True):
            if not 1 <= lo <= hi <= len(old):
                raise ValueError(f"{path}:{lo}-{hi} is outside the file, which has {len(old)} lines")
            lines[lo - 1:hi] = new
        out += difflib.unified_diff(old, lines, f"a/{path}", f"b/{path}", n=CONTEXT, lineterm="")
    if not out:
        raise ValueError("the new lines are the lines there already: nothing changes")
    return "\n".join(out) + "\n"


def clean(text):
    """A hand-written unified diff, its hunk counts set from its lines; refused where a line
    in it is none of a diff's."""
    if not any(ln.startswith("@@") for ln in text.splitlines()):
        raise ValueError("the change is neither a unified diff nor `@@ <path>:<first>-<last>` "
                         "blocks of new lines")
    bad = [ln for ln in text.splitlines() if not DIFF_LINE.match(ln)]
    if bad:
        raise ValueError(f"the change has lines that are not a diff's, such as {bad[0]!r}: "
                         "no `...` to skip lines, no fence or prose inside it. Give the new "
                         "lines instead, as `@@ <path>:<first>-<last>` blocks, and the diff is "
                         "built from the file")
    # A blank line in a hand-written hunk is an empty context line that lost its space.
    return recount("\n".join(ln if ln else " " for ln in text.splitlines())) + "\n"
