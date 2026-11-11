"""GitLab markdown as the user reads it: code fenced so it highlights, and a reviewer's
comment quoted whole, the code they pasted included — a suggestion as a diff.

Shared because showing a comment is the same job whichever skill does it — review-mr
shows the thread a follow-up replies into, rework-mr the threads you are answering — and
a copy per skill drifts: one learns to re-fence a ```suggestion and the other goes on
printing it raw. Nothing here knows either skill's state shape.
"""

import datetime
import difflib
import os
import re

from lib import critical_manifest
from lib.mr_common import first_name

FENCE_BY_EXT = {
    ".ts": "ts",
    ".tsx": "tsx",
    ".js": "js",
    ".jsx": "jsx",
    ".mjs": "js",
    ".py": "python",
    ".rb": "ruby",
    ".go": "go",
    ".java": "java",
    ".kt": "kotlin",
    ".rs": "rust",
    ".php": "php",
    ".cs": "csharp",
    ".sh": "bash",
    ".env": "bash",
    ".yml": "yaml",
    ".yaml": "yaml",
    ".json": "json",
    ".sql": "sql",
    ".css": "css",
    ".scss": "scss",
    ".html": "html",
    ".vue": "vue",
    ".svelte": "svelte",
    ".md": "markdown",
}
FENCE_RE = re.compile(r"^(\s*)(`{3,})(.*)$")
#  git's own headers. `index` is matched with its sha shape, not as a bare word: a code
#  snippet starting a line with `index = 0` is not a diff.
DIFF_HINT = re.compile(r"^(@@ |--- |\+\+\+ |diff --git |index [0-9a-f]{4,}\.\.)")


def lang_for(path):
    """Fence language for a path, or "" — an untagged fence renders flat, and the
    highlighting is the single most useful thing in a code block."""
    return FENCE_BY_EXT.get(os.path.splitext(path or "")[1].lower(), "")


def looks_like_diff(text):
    """True when the text is a unified diff, so it can be fenced as ```diff and render
    coloured. Strict on purpose: EVERY non-blank line must carry a diff prefix (space, +,
    -, @, \\) and at least one must be a +/- change. A before/after code snippet — which
    wants the file's own language, not diff — has lines starting with letters and fails
    here, and prose does too."""
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    if not lines:
        return False
    if any(DIFF_HINT.match(ln) for ln in lines):
        return True
    if not all(ln[:1] in " +-@\\" for ln in lines):
        return False
    return any(ln[:1] in "+-" for ln in lines)


HUNK_HEAD = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")


def recount(diff):
    """`diff` with every hunk header's line counts taken from the hunk itself.

    A diff written by hand (an agent's proposed change) often miscounts them, and a viewer
    that holds a hunk to its header then refuses the whole diff: the review-ui mod's engine
    draws one whose counts do not add up as plain code, uncoloured. The start lines are
    kept, as only the writer knows where the change is, and a header that holds is left as it
    was written."""
    lines = (diff or "").split("\n")
    out, head, old, new = [], None, 0, 0

    def close():
        if head is not None:
            m = HUNK_HEAD.match(out[head])
            said = (int(m.group(2) or 1), int(m.group(4) or 1))
            if said != (old, new):     # a header that holds is left as it was written
                out[head] = f"@@ -{m.group(1)},{old} +{m.group(3)},{new} @@{m.group(5)}"

    for ln in lines:
        if HUNK_HEAD.match(ln):
            close()
            out.append(ln)
            head, old, new = len(out) - 1, 0, 0
            continue
        if head is not None and ln[:1] in (" ", "-", "+") and not ln.startswith(("--- ", "+++ ")):
            old += ln[:1] != "+"
            new += ln[:1] != "-"
        elif head is not None and ln.startswith(("--- ", "diff ")):
            close()
            head = None
        out.append(ln)
    close()
    return "\n".join(out)


def fence(text, lang=""):
    """`text` in a fence long enough that nothing inside can close it early.

    CommonMark closes a fence only on a run of at least as many backticks, so a block
    containing ``` needs four. This is what kept biting `change-preview.sh`: it wrapped the
    model's illustration — which itself contained a ```diff block — in a bare ```, so the
    inner fence closed the outer one, the diff rendered as flat text, and everything after
    it leaked out of the block.

    Backtick runs are measured after LEADING WHITESPACE, not at column 0: inside a diff
    every line carries a ` `/`+`/`-` prefix, so a fence in a diffed markdown file arrives
    as "` ```"` — indented, still a valid closer, and invisible to a `^```` scan.

    Every code-bearing render in both skills routes fenced content through here — the code
    a comment is on, a change illustration, a working or topic diff — so marking each
    content line as critical HERE, once, covers all of them instead of needing the same
    call at every site. Code shown inside a quote (a comment's, a draft's) goes through
    `quoted_fence` instead, which marks the quoted lines the message actually carries.
    """
    runs = []
    for ln in (text or "").splitlines():
        s = ln.lstrip()
        if s.startswith("```"):
            runs.append(len(s) - len(s.lstrip("`")))
    bar = "`" * max([3] + [r + 1 for r in runs])
    for ln in (text or "").rstrip(chr(10)).splitlines():
        critical_manifest.mark(ln)
    return f"{bar}{lang}\n{(text or '').rstrip(chr(10))}\n{bar}"


def segments(lines):
    """Split lines into [(kind, info, body)] with kind in {"text", "code"}.

    An unterminated fence is treated as running to the end — the caller re-emits a closing
    one, which is what keeps a model's forgotten ``` from swallowing the rest of the block
    (the `Agreed?` line included).
    """
    segs, buf, i = [], [], 0
    while i < len(lines):
        m = FENCE_RE.match(lines[i])
        if not m:
            buf.append(lines[i])
            i += 1
            continue
        if buf:
            segs.append(("text", "", buf))
            buf = []
        bars, info = m.group(2), m.group(3)
        body, i = [], i + 1
        close = re.compile(r"^\s*`{%d,}\s*$" % len(bars))
        # A fence set in (under a list item, say) sets its lines in by as much: that much
        # comes off, as CommonMark reads it, and the code's own indentation stays.
        lead = len(m.group(1))
        while i < len(lines) and not close.match(lines[i]):
            ln = lines[i]
            body.append(ln[min(lead, len(ln) - len(ln.lstrip(" "))):])
            i += 1
        i += 1  # skip the closing fence, if any
        segs.append(("code", info.strip(), body))
    if buf:
        segs.append(("text", "", buf))
    return segs


SUGGESTION_INFO = re.compile(r"^suggestion(?::-(\d+)\+(\d+))?$")
INDENT_CODE = re.compile(r"^(?: {4,}|\t+)\S")  # markdown counts a tab as 4 spaces
DEDENT = re.compile(r"^(?: {4}|\t)")
LIST_ITEM = re.compile(r"^\s*([-*+]|\d+[.)])\s")


def diff_info(path):
    """The info string of a ```diff block of `path`'s lines, as lib/code_view.py's
    `diff_fence` writes it: the file, so the review-ui mod highlights it as that file."""
    return f"diff path={path}" if path else "diff"


def suggestion_diff(content, info, anchor, source):
    """A GitLab ```suggestion as a diff against the lines it replaces, the way GitLab shows
    it — or None when those lines are not known, and the block is shown as plain code.

    `suggestion:-A+B` replaces the A lines above the anchor through the B below. `source`
    is the file as the comment saw it (a list of lines), because only that version holds
    the lines the offsets count from; the working tree may have moved on. Lines the
    suggestion keeps show as context, so a one-line change inside a three-line range reads
    as one. An empty suggestion — "delete these lines" — is all removals.

    It is one hunk with its `@@` line, so it carries the line numbers it applies to, and
    the review-ui mod draws it as it draws every other diff (`diff_info`).
    """
    m = SUGGESTION_INFO.match(info or "")
    if not m or source is None or anchor is None:
        return None
    try:
        n, above, below = int(anchor), int(m.group(1) or 0), int(m.group(2) or 0)
    except (TypeError, ValueError):
        return None
    if not 1 <= n <= len(source):
        return None
    first = max(1, n - above)
    old = source[first - 1:min(len(source), n + below)]
    new = content.splitlines() if content.strip() else []
    out = [f"@@ -{first},{len(old)} +{first if new else first - 1},{len(new)} @@"]
    ops = difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes()
    for tag, i1, i2, j1, j2 in ops:
        if tag == "equal":
            out += [" " + ln for ln in old[i1:i2]]
        else:
            out += ["-" + ln for ln in old[i1:i2]] + ["+" + ln for ln in new[j1:j2]]
    return "\n".join(out)


# How alike a line beside a suggestion's range and a line of its replacement must be for the
# replacement to read as a rewrite of it: a heading reworded, a sentence corrected.
REWRITE_RATIO = 0.6
# How far beyond the range such a line is looked for.
REWRITE_REACH = 4


def suggestion_reach(content, info, anchor, source):
    """The range a GitLab ```suggestion should replace where the one it names falls short,
    as `(first, last, info)` in the file's line numbers; None where it holds or cannot be
    told.

    A suggestion replaces exactly the lines its range names (the comment's line alone, with
    no `:-A+B`), and everything else stays. A range that misses a line its replacement
    rewrites leaves the old line standing beside the new one, and a range of blank lines
    only rewrites nothing. Both read fine in the draft and break the file once applied.
    A line beside the range counts as rewritten where it is much like a line of the
    replacement; blank lines between it and the range are taken in with it."""
    m = SUGGESTION_INFO.match(info or "")
    if not m or source is None or anchor is None or not content.strip():
        return None
    try:
        n, above, below = int(anchor), int(m.group(1) or 0), int(m.group(2) or 0)
    except (TypeError, ValueError):
        return None
    if not 1 <= n <= len(source):
        return None
    first, last = max(1, n - above), min(len(source), n + below)
    new = [ln.strip() for ln in content.splitlines() if ln.strip()]

    def rewritten(ln):
        return ln.strip() and any(difflib.SequenceMatcher(None, ln.strip(), x).ratio() >= REWRITE_RATIO
                                  for x in new)

    lo, hi = first, last
    for k in range(1, REWRITE_REACH + 1):
        if first - k >= 1 and rewritten(source[first - k - 1]):
            lo = first - k
        if last + k <= len(source) and rewritten(source[last + k - 1]):
            hi = last + k
    if (lo, hi) == (first, last) and any(ln.strip() for ln in source[first - 1:last]):
        return None
    if (lo, hi) == (first, last):
        return None        # blank lines only, and nothing beside them it rewrites: unknown
    return lo, hi, f"suggestion:-{n - lo}+{hi - n}"


def _indented_runs(seg):
    """Split a prose run into [(kind, lines)] where kind is "text" or "code".

    A 4-space indented block is markdown's other code block, and reviewers use it as often
    as a fence. A blank line stays inside the block only when code surrounds it, and a run
    hanging under a list item is left as prose — that indentation is list continuation, not
    code.
    """

    def before(i):
        """Index of the nearest non-blank line above `i`, or None."""
        return next((j for j in range(i - 1, -1, -1) if seg[j].strip()), None)

    code = [bool(INDENT_CODE.match(ln)) for ln in seg]
    for i, ln in enumerate(seg):  # blanks: only inside a block
        if ln.strip():
            continue
        nxt = next((j for j in range(i + 1, len(seg)) if seg[j].strip()), None)
        prev = before(i)
        code[i] = prev is not None and nxt is not None and code[prev] and code[nxt]
    i = 0
    while i < len(seg):  # list continuation is not code
        if not code[i]:
            i += 1
            continue
        start = i
        while i < len(seg) and code[i]:
            i += 1
        prev = before(start)
        if prev is not None and LIST_ITEM.match(seg[prev]):
            for j in range(start, i):
                code[j] = False

    runs, buf, kind = [], [], None
    for ln, is_code in zip(seg, code):
        k = "code" if is_code else "text"
        if buf and k != kind:
            runs.append((kind, buf))
            buf = []
        kind = k
        buf.append(ln)
    if buf:
        runs.append((kind, buf))
    return [
        (k, [DEDENT.sub("", ln) if k == "code" else ln for ln in v]) for k, v in runs
    ]


def block_lang(content, info, path=None):
    """The language a code block is SHOWN in, never the one it is posted with.

    A block's own language is kept. A ```suggestion loses its marker — no highlighter knows
    it as a language, so it would render as grey text — and gets the file's language
    instead. An untagged block gets `diff` when it is one, else the file's language. The
    text that goes to GitLab keeps ```suggestion; that is what makes it one-click-apply.
    """
    if info and not SUGGESTION_INFO.match(info):
        return info
    return "diff" if looks_like_diff(content) else lang_for(path)


def code_block(content, info, path=None):
    """One fenced block as it is shown, in `block_lang`."""
    return fence(content, block_lang(content, info, path))


def quoted_fence(content, lang=""):
    """`content` fenced (see `fence`) and blockquoted, as lines — a code block that sits
    inside a quote with the prose around it, and renders highlighted there.

    Marked critical in its QUOTED form: that is what the message carries, and the paste
    gate compares the message against it. `fence`'s own marks, of the unquoted lines, are
    discarded.
    """
    with critical_manifest.suspended():
        block = fence(content, lang)
    lines = [f"> {ln}".rstrip() for ln in block.splitlines()]
    for ln in lines[1:-1]:                        # the content, not the fence bars
        if ln != ">":
            critical_manifest.mark(ln)
    return lines


# The link to a comment on GitLab, as a glyph rather than the URL: one cell, drawn as a link
# by the card, so each comment can be opened without a row of blue addresses
# (Nerd Fonts md-open_in_new).
LINK_ICON = "\U000f03cc"


# What a comment of the user's own is signed with where the review-ui mod draws: the same word
# everywhere, so their own comments can be picked out at a glance (the mod colours it).
YOU = "You"


# A line a markdown paragraph keeps on a line of its own: a list item, a quote, a heading,
# a table row, or the line after a hard break (two trailing spaces or a backslash).
_KEEPS_LINE = re.compile(r"^\s*(?:[-*+]\s|\d+[.)]\s|>|#|\|)")


def unwrap(lines):
    """A comment's prose with the author's own line wrapping undone: the lines of one
    paragraph joined, so the card wraps them to its own width. Blank lines, list items,
    quotes, headings and table rows keep their breaks."""
    out = []
    for ln in lines:
        prev = out[-1] if out else ""
        if (prev.strip() and ln.strip() and not _KEEPS_LINE.match(ln)
                and not prev.endswith(("  ", "\\"))):
            out[-1] = f"{prev.rstrip()} {ln.strip()}"
        else:
            out.append(ln)
    return out


def when_of(value):
    """A comment's time as a card heads it, in local time: `Nov 5, 11:38`, as the sidebar
    writes it; None where it is not known."""
    try:
        t = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00")) if value else None
    except ValueError:
        t = None
    if not t:
        return None
    t = t.astimezone()
    return f"{t.strftime('%b')} {t.day}, {t.strftime('%H:%M')}"


def note_md(name, body, path=None, anchor=None, source=None, compact=False, url=None, mine=False,
            when=None):
    """One thread comment, blockquoted whole — its code included, re-fenced so it renders.

    Reviewers paste code — a ```suggestion block, or an indented snippet — and both come
    out flat as they are: `suggestion` is an info string no highlighter knows, and an
    indented block never carries a language. So each is fenced with the file's own
    language, inside the quote with the prose around it, so the comment reads as one.
    A suggestion becomes a diff against the lines it replaces when `source` (the file as
    the comment saw it) is given — see `suggestion_diff`.
    """
    # The link leads, the name after it: every comment reads the same way, the glyph first.
    head = (f"[{LINK_ICON}]({url}) " if url else "") + f"**{YOU if mine else first_name(name)}**"
    # `compact`, for a card (the review-ui mod's): the head carries the comment's time and
    # the text follows it directly, its author's own line wrapping undone so the card
    # wraps it to its width.
    out = [f"> {head}" + (f" _{when}_" if compact and when else "")]
    pending = [compact]

    def quote(lines):
        while lines and not lines[0].strip():
            lines = lines[1:]
        while lines and not lines[-1].strip():
            lines = lines[:-1]
        if lines:
            if compact:
                lines = unwrap(lines)
            if not pending[0]:
                out.append(">")
            pending[0] = False
            out.extend(f"> {ln}".rstrip() if ln.strip() else ">" for ln in lines)

    def code(content, info):
        diff = suggestion_diff(content, info, anchor, source)
        if diff is None and not content.strip():  # an empty suggestion, nothing to diff
            return
        if diff is None:
            out.extend([">"] + quoted_fence(content, block_lang(content, info, path)))
        else:
            out.extend([">"] + quoted_fence(diff, diff_info(path)))

    for kind, info, seg in segments((body or "").strip().splitlines() or [""]):
        if kind == "code":
            code("\n".join(seg), info)
            continue
        for sub_kind, sub in _indented_runs(seg):
            if sub_kind == "code":
                code("\n".join(sub).strip("\n"), "")
            else:
                quote(sub)
    return "\n".join(out)
