"""GitLab markdown as the user reads it: code fenced so it highlights, and a reviewer's
comment with the code they pasted lifted out of its quote.

Shared because showing a comment is the same job whichever skill does it — review-mr
shows the thread a follow-up replies into, rework-mr the threads you are answering — and
a copy per skill drifts: one learns to re-fence a ```suggestion and the other goes on
printing it raw. Nothing here knows either skill's state shape.
"""

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

    Every code-bearing render in rework-mr, and review-mr's thread notes, route fenced
    content through here — the code the comment is on, a reviewer's own quoted
    suggestion, a change illustration, a working diff, a drafted reply's code — so marking
    each content line as critical HERE, once, covers all of them instead of needing the
    same call at every site.
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
        while i < len(lines) and not close.match(lines[i]):
            body.append(lines[i])
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


def suggestion_caption(info, anchor):
    """Label for a GitLab ```suggestion block, which loses its marker when re-fenced.

    `suggestion:-A+B` means "replace the A lines above the anchor through the B below", so
    the caption can name the lines the reviewer wants replaced — the one thing the raw
    `:-0+0` never told anybody.
    """
    m = SUGGESTION_INFO.match(info or "")
    if not m:
        return None
    if anchor is None:
        return "_suggested replacement:_"
    try:
        a = max(1, int(anchor) - int(m.group(1) or 0))
        b = max(a, int(anchor) + int(m.group(2) or 0))
    except (TypeError, ValueError):
        return "_suggested replacement:_"
    return (
        f"_suggested replacement for {'line' if a == b else 'lines'} "
        f"{a if a == b else f'{a}–{b}'}:_"
    )


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


def code_block(content, info, path=None, anchor=None):
    """One fenced block as it is SHOWN, never as it is posted.

    A block's own language is kept. A ```suggestion loses its marker — no highlighter knows
    it as a language, so it would render as grey text — and gets the file's language and a
    caption naming the lines it replaces instead. An untagged block gets `diff` when it is
    one, else the file's language. The text that goes to GitLab keeps ```suggestion; that
    is what makes it one-click-apply.
    """
    cap = suggestion_caption(info, anchor)
    lang = (
        info
        if info and not SUGGESTION_INFO.match(info)
        else ("diff" if looks_like_diff(content) else lang_for(path))
    )
    return (f"{cap}\n\n" if cap else "") + fence(content, lang)


def note_md(name, body, path=None, anchor=None):
    """One thread note: prose blockquoted, its code lifted out of the quote and fenced.

    Reviewers paste code — a ```suggestion block, or an indented snippet. Left
    inside the `> ` quote both render flat: `> ```suggestion:-0+0` is a fence with an info
    string no highlighter knows, and an indented block never carries a language at all. That
    is the reviewer's proposed code rendered as grey text. Lifting it to line start and
    re-fencing with the file's own language is the whole point of showing the note.
    """
    out = [f"> **{first_name(name)}**"]

    def quote(lines):
        while lines and not lines[0].strip():
            lines = lines[1:]
        while lines and not lines[-1].strip():
            lines = lines[:-1]
        if lines:
            # `>` continues the quote we are already in; a blank line separates prose from a
            # fence we just lifted out of it (a `>` there renders as an empty quoted line).
            out.append(">" if out[-1].startswith(">") else "")
            out.extend(f"> {ln}".rstrip() if ln.strip() else ">" for ln in lines)

    def code(content, info):
        if not content.strip():  # an empty suggestion is not worth a block
            return
        out.extend(["", code_block(content, info, path, anchor)])

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
