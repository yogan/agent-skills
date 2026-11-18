"""A word-level diff of two texts, as the review cards show a proposed edit of a comment.

The result is a fenced block whose info string is `worddiff`: the new text, with what was
taken out written `[-like this-]` and what was put in `{+like this+}` (git's `--word-diff`
markers). The review-ui mod draws such a block with the removed words struck and the added
ones highlighted; read as plain text it still says the same.

Words, the spaces between them and punctuation are diffed as separate tokens, so a changed
comma marks the comma and not the word before it.
"""
import difflib
import re

TOKEN = re.compile(r"\s+|\w+|[^\w\s]")


def tokens(text):
    return TOKEN.findall(text or "")


def marked(old, new):
    """`new`, with the changes from `old` marked inline."""
    a, b = tokens(old), tokens(new)
    out = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if op == "equal":
            out.append("".join(a[i1:i2]))
            continue
        if op in ("delete", "replace"):
            out.append(f"[-{''.join(a[i1:i2])}-]")
        if op in ("insert", "replace"):
            out.append(f"{{+{''.join(b[j1:j2])}+}}")
    return "".join(out)


def block(old, new):
    """The fenced block a card carries."""
    return f"```worddiff\n{marked(old.strip(), new.strip())}\n```"


def blocks(old, new, code=None):
    """The proposed edit of a text that holds code blocks: its prose word by word, each a
    `worddiff` block, and its code blocks as code, in the new text's version.

    A fence inside a `worddiff` block closes it early: the rest of the edit fell out as
    loose markdown, and a line a card put after it (what a press did) into the open fence
    left behind. Code is not a text to mark word by word anyway: a changed range of a
    ```suggestion read as `suggestion{+:-0+1+}` above lines no longer highlighted.

    `code(content, info)`: a code block as the card shows it (a suggestion as the diff it
    makes); by default fenced as it is. Where the two texts do not hold the same run of
    prose and code, the prose is diffed as one, and the new text's code follows it."""
    from lib import fences

    def parts(text):
        return [(k, info, "\n".join(body).strip("\n")) for k, info, body in fences.segments(text.strip().splitlines())]
    a, b = parts(old), parts(new)
    if not any(k == "code" for k, _, _ in a + b):
        return block(old, new)
    draw = code or (lambda content, info: fences.fence(content, info))
    if [k for k, _, _ in a] != [k for k, _, _ in b]:
        def prose(ps):
            return "\n\n".join(x for k, _, x in ps if k == "text" and x.strip())
        return "\n\n".join([block(prose(a), prose(b)), *(draw(x, info) for k, info, x in b if k == "code")])
    out = []
    for (_, _, was), (kind, info, now) in zip(a, b):
        if kind == "code":
            out.append(draw(now, info))
        elif was.strip() or now.strip():
            out.append(block(was, now))
    return "\n\n".join(out)
