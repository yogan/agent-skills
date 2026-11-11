"""The size of a diff, written the same way by every skill: ` 28 files +1136 −1046`.

The file-diff icon (Nerd Fonts `oct-file_diff`, glyphnames.json v3.5.1), the file count,
then the added and removed lines, a space apart, no slashes and no code spans. Rows of
them one under another pad each part to the longest, so the counts line up. A line
holding it in this exact form is what the review-ui mod recognises (`hooks/views.ts`'s
`DIFF_SIZE`, which mirrors this) and draws with the counts in green and red: markdown
cannot colour, so the text itself carries no colour and reads the same where nothing
draws it.

`counts` is the two numbers alone, for a row whose file is named beside it.
"""
ICON = "\uf4d2"


def counts(added, removed, width=0):
    """`width`: the added count padded to that many digits, so the removed counts of rows
    above one another line up."""
    return f"{'+' + str(added):<{width + 1}} −{removed}"


def text(files, added, removed):
    return f"{ICON} {files} file{'' if files == 1 else 's'} {counts(added, removed)}"


def of_stat(stat):
    """The size of a `hunk.diff_stat` result: (path, added, removed) per file."""
    return text(len(stat), sum(a for _, a, _ in stat), sum(r for _, _, r in stat))


# The same icon for a page in a browser, which has no Nerd Font: GitHub's Octicons
# `file-diff-16` (MIT), the glyph Nerd Fonts took `oct-file_diff` from, in the text's colour.
ICON_SVG = ('<svg viewBox="0 0 16 16" width="1em" height="1em" fill="currentColor" '
            'aria-hidden="true" style="vertical-align:-0.125em">'
            '<path d="M1 1.75C1 .784 1.784 0 2.75 0h7.586c.464 0 .909.184 1.237.513l2.914 2.914c.329.328.513.773.513 1.237v9.586A1.75 1.75 0 0 1 13.25 16H2.75A1.75 1.75 0 0 1 1 14.25Zm1.75-.25a.25.25 0 0 0-.25.25v12.5c0 .138.112.25.25.25h10.5a.25.25 0 0 0 .25-.25V4.664a.25.25 0 0 0-.073-.177l-2.914-2.914a.25.25 0 0 0-.177-.073ZM8 3.25a.75.75 0 0 1 .75.75v1.5h1.5a.75.75 0 0 1 0 1.5h-1.5v1.5a.75.75 0 0 1-1.5 0V7h-1.5a.75.75 0 0 1 0-1.5h1.5V4A.75.75 0 0 1 8 3.25Zm-3 8a.75.75 0 0 1 .75-.75h4.5a.75.75 0 0 1 0 1.5h-4.5a.75.75 0 0 1-.75-.75Z"/></svg>')
