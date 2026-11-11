"""How the two MR skills render where the review-ui mod (mods/review-ui) draws their views.

Without the mod, both skills print markdown for the model to paste, and that output stays
exactly as it was: OpenCode, and Claude Code without the mod, rely on it. Where the mod is
loaded it marks the session, and the views change for the person looking at the cards:

- the overview table leaves the opener, since the mod's panel lists the topics;
- one state vocabulary for both skills, each state with its icon, the icon standing for
  the topic (the `◈` marker goes from topic rows and headers);
- severity, kind and source as Nerd Font icons instead of emoji.

**How a script knows.** The mod writes an empty file named after the session id into
`MARKS` when the session starts, and Claude Code hands every shell it runs that id as
`CLAUDE_CODE_SESSION_ID`. A view rendered in a marked session is drawn by the mod. Nothing
else is consulted, so a session the mod never saw renders as before. `REVIEW_UI_DRAWN` (`1`
or `0`) overrides the answer, for tests and unusual launchers.

The icons are Nerd Fonts v3 Material Design glyphs, chosen by name and written here as their
codepoints as `glyphnames.json` (v3.5.1) gives them. Look a new one up by name there
(`python3 lib/glyphs.py <name>`, or `--search <word>`); a
codepoint typed from memory is the wrong glyph more often than not.
"""
import json
import os
import re

MARKS = os.path.expanduser("~/.claude/review-ui/sessions")


def drawn():
    """Whether the review-ui mod draws this session's views."""
    override = os.environ.get("REVIEW_UI_DRAWN")
    if override is not None:
        return override == "1"
    sid = os.environ.get("CLAUDE_CODE_SESSION_ID")
    return bool(sid) and os.path.exists(os.path.join(MARKS, sid))


def record(view):
    """Leave the mod the view just printed, in the session's mark: which review it belongs
    to, the topic it shows and its draft's version.

    The same facts ride in the view's manifest, but the model sometimes filters a view's
    output before reading it (a `grep -v` against the manifest has been seen), and a mod that
    then loses track of the review loses its band and its panel. A file the model never
    reads cannot be filtered. Silent where the mod does not draw, and never fatal.

    Only into a mark the mod made: where views are drawn by the `REVIEW_UI_DRAWN` override
    (a test, a stand-in run from some other session's shell), a note written here would make
    that session take up a review it never ran.
    """
    sid = os.environ.get("CLAUDE_CODE_SESSION_ID")
    if not sid or os.environ.get("REVIEW_UI_DRAWN") is not None:
        return
    if not os.path.exists(os.path.join(MARKS, sid)):
        return
    try:
        with open(os.path.join(MARKS, sid), "w") as fh:
            json.dump(view, fh)
    except OSError:
        pass


# One vocabulary for a topic's state in both skills, each word meaning one thing: whose
# move it is and how far the topic got. "Current" is not a state: it is the topic being
# decided, a highlight wherever topics are listed.
#   new       nobody has answered it yet, and it is your move
#   draft     your words are written and waiting to be posted
#   waiting   you said your piece; it is the other side's move
#   answered  the other side replied; your move again
#   done      settled
#   wontfix   settled as not to be fixed
#   dropped   not raised after all (review-mr only)
STATE_ICON = {
    "new": "\U000f0182",        # md-comment_outline
    "draft": "\U000f12c4",      # md-comment_edit_outline
    "waiting": "\U000f09e4",    # md-comment_arrow_right_outline
    "answered": "\U000f09e1",   # md-comment_arrow_left
    "done": "\U000f017f",       # md-comment_check
    "wontfix": "\U000f15e0",    # md-comment_minus_outline
    "dropped": "\U000f0187",    # md-comment_remove_outline
}
STATE_WORD = {"new": "new", "draft": "draft", "waiting": "waiting", "answered": "answered",
              "done": "done", "wontfix": "won't fix", "dropped": "dropped"}
CLOSED = ("done", "wontfix", "dropped")

# Severity and kind share one slot: only an issue has a severity. The shape alone reads, so
# it survives where no colour is drawn.
SEVERITY_ICON = {
    "critical": "\U000f0dbc",   # md-chevron_triple_up
    "high": "\U000f013f",       # md-chevron_double_up
    "medium": "\U000f0143",     # md-chevron_up
    "low": "\U000f0140",        # md-chevron_down
}
NO_SEVERITY = "\U000f0374"      # md-minus: an issue whose severity is not set yet
KIND_ICON = {
    "question": "\U000f0625",   # md-help_circle_outline
    "praise": "\U000f194b",     # md-hand_clap: not the thumbs-up, which is Ack and an approval
}

SOURCE_ICON = {
    "llm": "\U000f167a",        # md-robot_outline
    "human": "\U000f0013",      # md-account_outline
    "both": "\U000f000f",       # md-account_multiple_outline
    "author": "\U000f0ffb",     # md-account_edit_outline
    "peer": "\U000f05cb",       # md-account_voice
}
SOURCE_WORDS = {"llm": "found by the model", "human": "found by you",
                "both": "found by you and the model", "author": "opened by the MR's author",
                "peer": "opened by another reviewer"}


# The headings of the agent's own judgment on a card: md-text_search for its analysis,
# md-lightbulb_on_outline for what it recommends.
ANALYSIS_ICON = "\U000f13b8"
RECOMMENDATION_ICON = "\U000f06e9"
# Wherever it starts: on a line of its own, or at the end of the analysis's last paragraph.
_RECOMMENDATION = re.compile(r"(?:^|(?<=[.!?:)`])[ \t]+)\*\*Recommendation:?\*\*:?[ \t]*(.*)$", re.M)


def analysis_heading():
    return f"**{ANALYSIS_ICON} Analysis**"


# The parts of a card for a topic the author answered: what the comment asked for
# (md-format_quote_open_outline), what the author did since (md-account_wrench_outline), and the
# agent's verdict (md-gavel). A draft to post heads itself with the
# draft state's icon (md-comment_edit_outline), on every card.
ASKED_ICON = "\U000f11a7"
DID_ICON = "\U000f189b"
VERDICT_ICON = "\U000f029b"
DRAFT_ICON = STATE_ICON["draft"]
# A thread its author or another reviewer resolved on GitLab (md-check_circle_outline, the
# sidebar's mark for it too): said apart, as it does not close the topic here.
RESOLVED_ICON = "\U000f05e1"
# rework-mr's fix card: a fix that differs from the plan agreed, headed as a warning
# (md-alert_outline), so it is seen before the push.
DIFFERS_ICON = "\U000f002a"
# rework-mr's fix card: the commits a fix goes into, one rewritten by a fixup
# (md-source_commit_local) or a new one (md-source_commit_next_local). The review-ui mod
# draws a line led by either as a commit: the hash as code, the subject dim and italic.
REWRITTEN_ICON = "\U000f071b"
NEW_COMMIT_ICON = "\U000f071c"
# rework-mr's reply card: the fix as pushed, headed with the push's own icon (md-upload, the
# review-ui mod's for its push buttons and the line a push ends a card on).
PUSHED_ICON = "\U000f0552"


def heading_md(icon, words):
    """A card part's heading, alone on its line, as the analysis heading is."""
    return f"**{icon} {words}**"


def recommendation_md(advice):
    """A recommendation as a card shows it, as the analysis is: its heading alone on a line,
    the advice under it as a sentence of its own (a card draws a heading directly on what
    it heads)."""
    advice = advice.strip()
    return f"**{RECOMMENDATION_ICON} Recommendation**\n\n{advice[:1].upper()}{advice[1:]}"


def without_recommendation(text):
    """`text` without its `**Recommendation:** advice` paragraph: for a comment already
    posted, whose advice the user has followed."""
    return _RECOMMENDATION.sub("", text).strip()


def with_recommendation(text):
    """`text` with an inline `**Recommendation:** advice` paragraph, as the model writes
    one, turned into `recommendation_md`'s shape."""
    return _RECOMMENDATION.sub(lambda m: ("" if m.start() == 0 or text[m.start() - 1] == "\n"
                                          else "\n\n") + recommendation_md(m[1]), text)


def kind_icon(kind, severity):
    """The one slot severity and kind share: what the topic is, or how bad an issue is."""
    if kind in KIND_ICON:
        return KIND_ICON[kind]
    return SEVERITY_ICON.get(severity) or NO_SEVERITY


def state_cell(state):
    """A state as a table cell reads it: its icon and its word."""
    return f"{STATE_ICON[state]} {STATE_WORD[state]}"
