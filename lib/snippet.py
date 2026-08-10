"""Detecting a fixed-line-count code snippet that starts mid multi-line string/comment.

Both review-mr's findings.py (code_snippet) and rework-mr's threads.py
(render_code_context) show a few lines of a source file around an anchor line, fenced
for display. A window sliced by raw line count has no notion of syntax state: when it
happens to start right after a docstring/comment/template-literal opened above it, the
ONLY delimiter inside the window is the (correct) CLOSING one — but a syntax highlighter
given just the snippet, with no memory of anything before it, reads that as an OPENING
one instead. Everything after it then renders as an unterminated string, and everything
before it (still genuinely inside the construct, just not visibly so) renders as if it
were top-level code — which is how a docstring sentence containing ordinary words like
"and"/"any"/"set" ends up with keywords lit up.

Shared here because it's pure — no coupling to either skill's state shape — and because
duplicating it once already cost a real bug: the fix landed in findings.py first, and
would have silently stayed missing from threads.py's own snippet renderer.
"""

MAX_BACKTRACK = 30


def open_construct(lines, before):
    """(marker, start_line) if line `before` (1-based) opens strictly inside an
    unterminated triple-quoted string, backtick string (JS/TS template literal, Go raw
    string), or /* */ block comment (including /** */ JSDoc) carried over from an
    earlier line, else None.

    Scanning from the top of the file is the only way to know which reading is right,
    since that state isn't visible from the window alone.

    A backslash always consumes the character after it, whatever it is, so an escaped
    backtick/quote inside a template literal or string doesn't get misread as its real
    closer (`` `foo \\` bar` `` is one still-open template literal, not one closed after
    "foo "). This is still a heuristic scan, not a real lexer: a backtick or quote
    sitting inside a `${...}` interpolation expression — which can itself contain
    strings, or even a nested template literal — is out of scope. That needs a real JS
    parser, which doesn't exist in the stdlib; fully correct handling isn't worth
    chasing here (see this file's own module docstring on why paste-gate.py stopped
    doing exactly that kind of re-parsing).
    """
    state = None
    for i in range(before - 1):
        ln = lines[i]
        j = 0
        while j < len(ln):
            if ln[j] == "\\":
                j += 2          # skip the escaped character too, whatever it is
                continue
            if state is None:
                if ln.startswith('"""', j) or ln.startswith("'''", j):
                    state = (ln[j:j + 3], i + 1)
                    j += 3
                    continue
                if ln.startswith("/*", j):
                    state = ("/*", i + 1)
                    j += 2
                    continue
                if ln.startswith("`", j):
                    state = ("`", i + 1)
                    j += 1
                    continue
            else:
                closer = "*/" if state[0] == "/*" else state[0]
                if ln.startswith(closer, j):
                    state = None
                    j += len(closer)
                    continue
            j += 1
    return state
