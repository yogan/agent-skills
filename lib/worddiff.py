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
