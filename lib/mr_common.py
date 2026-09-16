"""Small rendering/identity/state helpers shared between review-mr's findings.py and
rework-mr's threads.py — genuinely identical apart from a trivial parameter, unlike the
two skills' state machines and rendering logic, which differ for real domain reasons
and stay duplicated (see CLAUDE.md's "Sharing vs. duplication")."""
import json
import os
import re

TOPIC_ICON = "◈"
MR_LEVEL = "MR-level"

_FENCE_OPEN = re.compile(r"`{3,}")
_FENCE_CLOSE = re.compile(r"(`{3,})[ \t]*$")
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)
_TABLE_RULE = re.compile(r"^[ \t]*\|?[ \t]*:?-{2,}:?[ \t]*(\|[ \t]*:?-{2,}:?[ \t]*)*\|?$")
_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
# A quoted list item carries both markers ("> - point"), so the blockquote part repeats
# and the heading/bullet part follows it. Every piece is optional: the sub then matches
# empty at a plain line's start, which costs nothing.
_LINE_PREFIX = re.compile(
    r"^[ \t]*(?:>+[ \t]*)*(?:#{1,6}[ \t]+|(?:[-*+]|\d+[.)])[ \t]+)?", re.M)
_EMPHASIS = re.compile(r"(\*{1,3}|~{1,2})(?=\S)(.+?)(?<=\S)\1", re.S)
# An underscore INSIDE a word is literal, per CommonMark — and the reason the rule has to
# say so here is that `snake_case` identifiers and URLs are all over a reviewer's comment:
# a single pass with `_` treated like `*` turned `#note_1 … merge_requests` into
# `#note1 … mergerequests`.
_UNDERSCORE_EMPHASIS = re.compile(r"(?<!\w)(_{1,3})(?=\S)(.+?)(?<=\S)\1(?!\w)", re.S)
_BARE_SUGGESTION = re.compile(r"^\s*suggestion(?::-\d+\+\d+)?\s*$", re.M)


def _drop_fenced(text):
    """Every fenced block gone, nesting and all.

    A regex cannot do this: a closing fence must be at least as long as the one that
    opened it, so the ```` block in a comment that quotes a ``` block ends at the LONGER
    marker — and a pattern that stops at the first bare-backtick line swallowed the prose
    after it instead. An unterminated fence runs to the end of the text, which is the
    common case in a comment somebody pasted code into and never closed. (threads.py's
    `_segments` scans for the same reason, on the rendering side.)
    """
    kept, fence = [], None
    for line in (text or "").splitlines():
        stripped = line.lstrip()
        if fence is None:
            m = _FENCE_OPEN.match(stripped)
            if m:
                fence = len(m.group(0))
            else:
                kept.append(line)
            continue
        m = _FENCE_CLOSE.match(stripped)
        if m and len(m.group(1)) >= fence:
            fence = None
    return "\n".join(kept)


def plain_text(md):
    """Markdown flattened to one readable line's worth of prose.

    For text that is DISPLAYED where markup cannot render or would mislead — a table
    cell, a one-line heading. A reviewer's comment is markdown, and pasting its first 70
    characters raw puts things like ```suggestion:-6+0 or ~~struck-through~~ where a
    title belongs: unreadable, and in a table cell a stray fence breaks the row.

    Fenced code goes entirely rather than being unwrapped: a suggestion block is the
    reviewer's proposed replacement, never a description of their point, and the prose
    around it is what names the topic. When a comment is nothing but a suggestion there
    is no prose to find, and the caller gets an empty string to say so rather than a
    line of somebody else's code.
    """
    text = _drop_fenced(_HTML_COMMENT.sub(" ", md or ""))
    text = _BARE_SUGGESTION.sub(" ", text)     # a suggestion whose fence GitLab ate
    text = _IMAGE.sub(r"\1", text)
    text = _LINK.sub(r"\1", text)
    text = _LINE_PREFIX.sub("", text)
    text = _EMPHASIS.sub(r"\2", text)
    text = _UNDERSCORE_EMPHASIS.sub(r"\2", text)
    # A markdown table flattens to pipe soup, and those pipes then have to be escaped
    # again by whatever cell this lands in. Its rule line carries nothing at all.
    text = "\n".join("" if _TABLE_RULE.match(ln) else ln.replace("|", " ")
                     for ln in text.splitlines())
    return " ".join(text.replace("`", "").split())


# Words that give a language away: function words, plus the inflections of a handful
# of everyday verbs and adjectives, which is what a summary is actually made of. An
# English summary legitimately names a foreign identifier, path or string literal, so
# code spans are stripped and two DISTINCT words have to hit before anything is said.
# A language with no list here is simply never checked — see `reads_as`.
#
# Two rules keep this from firing on English prose: nothing shorter than three letters
# (`an`, `am`, `in`, `so`, `da` are all English too), and nothing that is also an
# English word (`also`, `war`, `falls`, `man`, `will`, `mine`, `hier` are the ones that
# had to be left out).
LANG_GIVEAWAYS = {
    "de": {"aber", "alle", "allem", "allen", "aller", "auch", "auf", "aus", "beide",
           "beiden", "beim", "bereits", "bleibt", "brauchen", "braucht", "dabei",
           "damit", "dann", "dass", "dazu", "dem", "den", "denen", "denn", "der",
           "deren", "des", "dessen", "die", "diese", "diesem", "diesen", "dieser",
           "doch", "dort", "durch", "eher", "eigene", "eigenen", "eigener", "ein",
           "eine", "einem", "einen", "einer", "eines", "etwa", "fehlen", "fehlend",
           "fehlende", "fehlenden", "fehlt", "für", "ganz", "geben", "gegen",
           "geändert", "geaendert", "gehört", "gemacht", "gibt", "gleiche",
           "gleichen", "haben", "hart", "hat", "hatte", "hatten", "hinter", "ihre",
           "ihren", "immer", "innerhalb", "ist", "jede", "jeden", "jeder", "jedes",
           "jedoch", "jeweils", "kann", "kein", "keine", "keinem", "keinen",
           "keiner", "komplett", "können", "könnte", "laufen", "liegt", "lässt",
           "läuft", "machen", "macht", "mehr", "mit", "muss", "müssen", "müsste",
           "nach", "neue", "neuem", "neuen", "neuer", "neues", "nicht", "noch",
           "nur", "obwohl", "oder", "ohne", "sagen", "sagt", "schon", "sehr",
           "sein", "seine", "seinen", "seiner", "selbst", "sind", "soll", "sollen",
           "sollte", "sollten", "sonst", "sowie", "statt", "steht", "trotz", "über",
           "und", "unter", "viele", "vom", "von", "vor", "waren", "wegen", "weil",
           "weiter", "welche", "welcher", "wenn", "werden", "wieder", "wird",
           "wurde", "wurden", "zwar", "zwischen"},
}
DEFAULT_LANG = "de"
_CODE_SPAN = re.compile(r"`[^`]*`")
_WORDS = re.compile(r"[^\W\d_]{3,}")


def reads_as(text, lang):
    """Whether `text` reads as `lang` rather than as English.

    Conservative by construction: it only knows languages `LANG_GIVEAWAYS` has a word
    list for, it ignores anything in backticks, and it needs two distinct function
    words — a single "die" or "von" in an English line is not evidence. Both skills use
    it on an AUTHORED summary only; a raw thread quote is in the commenter's language by
    design and says nothing about whether anybody wrote a title.
    """
    words = LANG_GIVEAWAYS.get((lang or DEFAULT_LANG).lower())
    if not words or not text:
        return False
    prose = _CODE_SPAN.sub(" ", text)
    # An acronym is never a function word, and several lowercase straight into the
    # list: MIT, DES, AUS, DEM. A German word at the start of a sentence is only
    # title-case, so dropping the all-caps tokens costs nothing.
    hits = {w.lower() for w in _WORDS.findall(prose)
            if not w.isupper() and w.lower() in words}
    return len(hits) >= 2


def load(path):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return None


def save(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def topic_for(state, tid):
    return next((t for t in state["topics"] if t["id"] == tid), None)


def num(tid):
    """The numeric part of a topic id (`t3` -> 3) — for sorting, and for picking the
    next free id."""
    return int("".join(c for c in tid if c.isdigit()) or 0)


def tref(tid):
    """A topic id as the user reads it — always carrying the topic icon, so `t3` never
    turns up bare in rendered output and is never mistaken for a GitLab thread id.

    Deliberately NOT used for: CLI examples (which must stay copy-pasteable), `die()`
    diagnostics about a topic that does not exist, and thread/discussion ids, which are
    not topics.
    """
    return f"{TOPIC_ICON} {tid}"


def first_name(name):
    """'Doe, Jane - AB12345' -> 'Jane'; 'Jane Doe' -> 'Jane'."""
    n = (name or "").strip()
    if " - " in n:                      # strip trailing " - <ACCOUNT-ID>"
        n = n.rsplit(" - ", 1)[0].strip()
    if "," in n:                        # "Lastname, Firstname"
        n = n.split(",", 1)[1].strip()
    parts = n.split()
    return parts[0] if parts else (name or "")


def short_summary(state, t, width=64):
    """Falls back to the first-thread body when no authored `summary` exists yet — a
    topic just surfaced from a live GitLab discussion, in whatever language and wording
    the commenter used, truncated mid-word by `width`.

    The fallback is flattened through `plain_text`: it is a reviewer's markdown, and a
    fence or a strikethrough where a title belongs is unreadable (and breaks a table
    row). A comment that is nothing but a suggestion flattens to nothing, and says so
    rather than showing a line of the reviewer's proposed code as if it were a title.

    Either way the fallback is a stand-in for display, never a title: callers that render
    a topic as a heading (the table, `quote`) must also flag that nobody has written one,
    rather than treating this string as a finished summary.
    """
    text = t.get("summary")
    if not text:
        thr = [state["threads"].get(x, {}) for x in t["thread_ids"]]
        text = plain_text(thr[0].get("body") if thr else "") or "(no prose — code only)"
    text = " ".join((text or "").split())
    return text[: width - 1] + "…" if len(text) > width else text


def loc_md(loc):
    """A topic's `file:line` as a person reads it — in a table cell, in a topic
    heading, anywhere a location is shown.

    A topic with no location is normal, not missing data: a point about the merge
    request itself — its title, its description, a test nobody's diff adds — has no
    diff line for GitLab to hang a comment on, so it is posted on the MR. Wrapping
    that empty string in backticks renders as a literal, meaningless `` in every
    client that styles this output, so the empty case gets the word instead.
    """
    return f"`{loc}`" if loc else f"_{MR_LEVEL}_"


def state_file(root, slug, iid, filename):
    """`root/<slug>--mr<iid>/<filename>`, creating the directory if needed. `root` and
    `filename` are per-skill (different state roots, different file names); the shape
    is what's shared."""
    d = os.path.join(root, f"{slug}--mr{iid}")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, filename)
