"""The scenarios `drive.py` runs. Each is one claude session, start to finish.

The `stage-*` ones build the talk-day windows: claude started, the opening line typed, nothing
sent. The rest check something and need nothing typed by a person. `rig` ones need the local
GitLab up and reset (`./tmux-demo.sh --check` passes); the others run anywhere.

An expectation names what a person would look for on the screen, not how the skill gets there:
a check on wording the model chooses breaks whenever the model words it differently, and then
the run says nothing about the skill.
"""
import os
import re
from pathlib import Path

from drive import Check, Click, Expect, Handover, Panel, Prompt, Scenario, Type

# What the mod's cards look like on screen, against what a pasted view looks like: a card is
# a rounded box at the screen's left edge; a table the model pasted is drawn in the reply,
# indented under its `⏺`, outside any box.
CARD = Expect(r"^╭─")
NOT_PASTED = Expect(r"^  (?:[┌├└]─|► \d+ \|)", absent=True)   # a table, or a marked code line

# The mod, from this checkout. A verify run loads it with --plugin-dir; a showcase runs on
# what the person has installed, so it is given no mods.
# `E2E_MOD` loads another copy of it instead, for a before/after against an older version.
MOD = os.environ.get("E2E_MOD") or str(Path(__file__).resolve().parent.parent / "mods" / "review-ui")

# The MR as a whole changing while the rework runs: approved by its reviewer, merged.
NEWS = f"python3 {Path(__file__).resolve().parent / 'news.py'}"
APPROVED = "\U000f0514"        # the heading's thumbs-up: who approved is the News box's to say


# A step's button is a chip: its icon and label on a fill, between rounded caps. Found by its
# label and the cap after it, so "Post" is not "Post + resolve".
CAP = "\ue0b4"


def button(*labels):
    """A pattern for these buttons, in this order: on one line, or on the next where a card
    too narrow for the row wraps it (the sidebar open beside it does that)."""
    return r"[^\n]*?(?:\n[^\n]*?)?".join(re.escape(f" {x} {CAP}") for x in labels)


def press(label, **kw):
    """Click the button with this label, on its label."""
    return Click(f" {label} {CAP}", offset=1, **kw)


def current(t):
    """The topic the sidebar highlights as current; a card's own title may be scrolled off."""
    return rf"^│?▌[^\n]*? {t} "

# MR !3's third thread asks for more than the MR is about, and says so: the agent proposes
# to decline it, the card drafts that reply with one press, and it is posted at once.
THREADS = "glab api 'projects/demo%2Fbulletproof-react/merge_requests/3/discussions'"


def thread_on(path, show):
    """A check printing `show` (Python, over the thread `d`) for MR !3's thread on `path`.
    GitLab's own notes are left out of `d["notes"]`: "changed this line in version 3 of the
    diff" lands in a thread whenever a push touches its line, which a fix may or may not."""
    return (f"{THREADS} | python3 -c 'import json,sys; d=[x for x in json.load(sys.stdin) if x[\"notes\"][0]"
            f".get(\"position\",{{}}).get(\"new_path\",\"\").endswith(\"{path}\")][0]; "
            f"d[\"notes\"]=[n for n in d[\"notes\"] if not n.get(\"system\")]; print({show})'")


REPLY_INSTEAD = [
    Expect(current("t3"), region="panel", wait=180),
    Expect(button('Reply'), region="screen", wait=180),
    press('Reply', turn=True, timeout=600),
    Expect(button('Post + resolve'), region="screen"),
    press('Post'),
    Check(thread_on("discussions/api/query-keys.ts", 'len(d["notes"]), d["notes"][-1]["author"]["username"]'),
          r"^2 frank$"),
]

REVIEW1 = "~/src/agent-skills-demo-review-mr1"
REVIEW2 = "~/src/agent-skills-demo-review-mr2"
WORK = "~/src/agent-skills-demo"
SCROLL = "~/src/agent-skills-demo-scroll"     # MR !4's checkout (fixture.py's MR4_DIR)
# A scrollbar's thumb as the sidebar draws it: a full cell, or one filled from the bottom.
THUMB = Expect(r"[█▁▂▃▄▅▆▇]", region="panel", wait=30)
# How many of MR !4's topics carry a proposal, asked until all do or 14 minutes have passed.
EVERY_PROPOSAL = ("python3 -c \"import json,os,time\n"
                  "p=os.path.expanduser('~/.claude/rework-mr/demo-bulletproof-react--mr4/topics.json')\n"
                  "for _ in range(168):\n"
                  " ts=json.load(open(p))['topics']; n=sum(1 for t in ts if t.get('proposal'))\n"
                  " if n==len(ts): break\n"
                  " time.sleep(5)\n"
                  "print(n,'of',len(ts))\"")

# MR !3 worked through to the merge; a part of it is a scenario of its own (`try-pushed-fix`).
REWORK_FIX = [
            Prompt("/rework-mr !3", timeout=600),
            *[step for t in ("t1", "t2") for step in (
                Expect(current(t), region="panel", wait=30),
                Expect(button('Agree plan'), region="screen", wait=240),
                press('Agree plan'))],
            *REPLY_INSTEAD,
            Expect(current("t1"), region="panel", wait=30),
            # t1, the rename: made by the model, pushed on a press, then "Fixed" and resolved.
            press('Fix it', turn=True, timeout=600),
            Expect(button('Ack and push'), region="screen"),
            # Beside the sidebar, the diff viewer's key is the line over the fix's files, not the card's.
            Expect(r"Review in hunk", region="panel"),
            Expect(r"Changes\s+\S+ \d+ files? ", region="screen"),
            # The demo repository's one check, as the fixture records it: a quick source check
            # that passes. Passed checks go unsaid on the card; its push button says it.
            Check("cat .git/rework-mr-checks.json", r'check-source\.mjs'),
            Expect(r"Checks (?:failed|not run)", region="screen", absent=True),
            # The rename spans both commits, so the push hands it back to the model for one
            # fixup per commit; a fix inside one commit is the script's alone.
            press('Ack and push', turn=True, timeout=600),
            # The push did only that: what goes into the thread is the next card's.
            Expect(button('“Fixed” + resolve', '“Fixed”', 'Reply…'), region="screen", wait=60),
            press('“Fixed” + resolve'),
            Expect(current("t2"), region="panel", wait=120),
            Check("git fetch -q && git rev-parse HEAD @{u} | uniq | wc -l", r"^\s*1$"),
            Check("git rev-list --count origin/main..@{u}", r"^2$"),   # squashed, no fixup left
            Check("glab api 'projects/demo%2Fbulletproof-react/merge_requests/3/discussions'"
                  " | python3 -c 'import json,sys; d=[x for x in json.load(sys.stdin) if x[\"notes\"][0]"
                  ".get(\"position\",{}).get(\"new_path\",\"\").endswith(\"comments/api/query-keys.ts\")][0];"
                  " print(d[\"notes\"][0][\"resolved\"], d[\"notes\"][-1][\"body\"][:6])'",
                  r"^True Fixed"),
            # t2, the cache bug: pushed by the script alone, then a reply drafted from a line of
            # the user's and posted from its card, which leaves resolving to the reviewer.
            press('Fix it', turn=True, timeout=600),
            press('Ack and push'),
            Expect(button('Reply…'), region="screen", wait=60),
            press('Reply…'),
            Prompt("say which of the two options it took", timeout=600),
            # Copy stands beside the draft's heading, Post in the buttons under it.
            Expect(button('Copy'), region="screen"),
            Expect(button('Post'), region="screen"),
            Expect(button('Post + resolve'), region="screen", absent=True),
            press('Post'),
            # Posted, and the last topic of the fix pass: nothing is current any more.
            Expect(current("t2"), region="panel", absent=True, wait=60),
            # Opened again from the list, waiting on its reviewer: its card is the fix's steps to
            # the reply, whom it waits on, and the pushed fix to look at. No round runs.
            Click("\U000f09e4 t2 ", offset=3),
            Expect(r"Waits on Miriam", region="screen", wait=30),
            # Beside the sidebar, its diff viewer's key is the line over the fix's files.
            Expect(r"Show in hunk", region="panel"),
            Expect(button('Next'), region="screen", absent=True),
            # Left again (the ✕ on its file's line): back to the overview, which says the merge.
            Click("\U000f0156"),
            Check(thread_on("updateDiscussion.ts", 'len(d["notes"]), d["notes"][-1]["author"]["username"]'),
                  r"^2 frank$"),
            # The MR merged meanwhile: the sidebar says so once the update check has run.
            Check(f"{NEWS} merge", r"merged"),
            Expect(r"[Mm]erged", region="panel", wait=240),
]

# The same MR, with t1 asked for more after its push, t2's push between: t1's second fix is a
# push of its own, its card lists both, the sidebar picks between them, and its "Fixed" reply
# links the new push alone. A part of it is a scenario of its own (`try-pushed-twice`).
POSTED_T2 = next(i for i, x in enumerate(REWORK_FIX)
                 if isinstance(x, Expect) and x.pattern == current("t2") and x.absent) + 1
REWORK_TWICE = [
            *REWORK_FIX[:POSTED_T2],
            Check(f"{NEWS} more", r"asked for more"),
            # The update check brings t1 back with the reviewer's word; opened from the list.
            Expect(r"\bt1\b", region="panel", wait=240),
            Click(" t1 ", offset=1, region="panel"),
            Expect(button('Agree plan'), region="screen", wait=240),
            press('Agree plan'),
            press('Fix it', turn=True, timeout=600),
            Expect(button('Ack and push'), region="screen"),
            # A doc comment in one commit: the script's push alone.
            press('Ack and push'),
            Expect(button('“Fixed” + resolve'), region="screen", wait=60),
            # Two fixes, t2's push between them: the card lists both, the sidebar shows the
            # latest and steps back to the first.
            Expect(r"Fix pushed · 2 pushes", region="screen"),
            Expect(r"push 2 of 2", region="panel"),
]
TRY_TWICE = len(REWORK_TWICE)
REWORK_TWICE += [
            Click("\U000f0bb9", region="panel"),      # md-pan_left: the push before
            Expect(r"push 1 of 2", region="panel", wait=30),
            press('“Fixed” + resolve'),
            # Its thread holds two "Fixed" replies, each with one link, and not the same one.
            Check(thread_on("comments/api/query-keys.ts",
                            '(lambda f: (len(set(f)), sum(b.count("diff_id=") for b in f)))'
                            '([n["body"] for n in d["notes"] if n["body"].startswith("Fixed")])'),
                  r"^\(2, 2\)$"),
]

SCENARIOS = [
    Scenario(
        "smoke", "the driver itself: start claude, send a prompt, read the answer",
        cwd=None, steps=[
            Prompt("Reply with exactly the word PONG and nothing else."),
            Expect(r"^⏺ PONG$"),   # the reply, not the prompt that asked for it
        ]),

    Scenario(
        "review-1", "MR !1, first pass: explainer link, five topics imported", REVIEW1,
        needs_rig=True, mods=[MOD], steps=[
            Prompt("/review-mr !1", timeout=600),
            Expect(r"explainer-mr1\.html"),
            NOT_PASTED,
            # The topics wait for the user's own review: no topic shown, none in the panel.
            Expect(r"Agent review done"),
            Expect(r"CORS", absent=True),
            Expect(r"added 5 findings", absent=True),   # not even how many there are
            Expect(button('My review is done'), region="screen"),
            # The sidebar opens by itself, with the user's own comments, not the topics.
            Panel(True),
            Expect(r"Your comments on GitLab", region="panel"),
            press('My review is done'),   # none of the user's own: the first topic, all five listed
            Expect(current("t1"), region="panel", wait=30),
            CARD,
            Expect(r"^│ \S+ Analysis +│", region="screen"),   # the stored analysis, no turn needed
            Expect(r"anchor check", absent=True),
            Expect(r"\bt5\b", region="panel"),     # the panel, still open, lists them now
        ]),
    Scenario(
        "rereview-2", "MR !2, re-review: what arrived waits in the sidebar's inbox", REVIEW2,
        needs_rig=True, mods=[MOD], steps=[
            Prompt("/review-mr !2", timeout=600),
            NOT_PASTED,
            # The author's replies and pushes since the last review are the inbox's, not
            # the chat's: the sidebar opens with them, and the topics they touch.
            Panel(True),
            Expect(r"\bSince \d\d:\d\d\b|\bNews\b", region="panel"),
            Expect(r"\bt1\b", region="panel"),
            Expect(r"\bt4\b", region="panel"),
        ]),
    Scenario(
        "rework-3", "MR !3, rework: three reviewer threads", WORK,
        needs_rig=True, mods=[MOD], steps=[
            Prompt("/rework-mr !3", timeout=600),
            Expect(r"\bt1\b"),
            CARD, NOT_PASTED,
            Panel(True),                # opened by itself
            Expect(r"\bt3\b", region="panel"),
        ]),

    Scenario(
        "rework-rounds", "MR !3: the plan pass, two plans agreed with a press and a reply instead of the third", WORK,
        needs_rig=True, mods=[MOD], steps=[
            # The reviewer has approved meanwhile; the sidebar's heading says so once the
            # update check has run, which it does on its first tick of the review.
            Check(f"{NEWS} approve", r"approved"),
            Prompt("/rework-mr !3", timeout=600),
            CARD, NOT_PASTED,
            # The opener's card carries the agent's proposal: its analysis, its plan, a press.
            Expect(current("t1"), region="panel"),
            Expect(r"Recommendation", region="screen"),
            Expect(button('Agree plan'), region="screen"),
            Panel(True),
            Expect(APPROVED, region="panel", wait=60),
            press('Agree plan'),
            # t2's proposal is the background agent's: its card needs no turn of the model's.
            Expect(current("t2"), region="panel", wait=180),
            Expect(button('Agree plan'), region="screen", wait=180),
            press('Agree plan'),
            # t3 asks for more than this MR: a reply in place of a plan, posted at once.
            *REPLY_INSTEAD,
            # Every topic planned: the fix pass, from the top.
            Expect(current("t1"), region="panel", wait=30),
            Expect(button('Fix it'), region="screen"),
        ]),

    Scenario(
        "rework-fix", "MR !3: a fix pushed and resolved, a fix pushed and replied to, a reply instead of a fix, the merge", WORK,
        needs_rig=True, mods=[MOD], steps=[*REWORK_FIX]),

    Scenario(
        "try-pushed-fix", "MR !3 up to t1's pushed fix in four files, for a person to try (--keep)", WORK,
        needs_rig=True, mods=[MOD], recording="rework-fix", steps=REWORK_FIX[:21]),

    Scenario(
        "rework-twice", "MR !3: t1 fixed again after t2's push, two pushes on its card, the sidebar and its reply", WORK,
        needs_rig=True, mods=[MOD], steps=[*REWORK_TWICE]),

    Scenario(
        "try-pushed-twice", "MR !3 up to t1's second push, t2's between, for a person to try (--keep)", WORK,
        needs_rig=True, mods=[MOD], recording="rework-twice", steps=REWORK_TWICE[:TRY_TWICE]),

    Scenario(
        "try-scrolling", "MR !4: every sidebar area with more than it shows, beside ones that fit, for a person to try (--keep)",
        SCROLL, needs_rig=True, mods=[MOD], steps=[
            Prompt("/rework-mr !4", timeout=600),
            Panel(True),
            Expect(current("t1"), region="panel", wait=60),
            # Thirteen topics in a quarter of the sidebar: the list has its scrollbar.
            THUMB,
            # The background agent proposes a plan for each other topic: waited for, so the
            # session left to try has every card's proposal, and the recording all of them.
            Check(EVERY_PROPOSAL, r"^13 of 13$", timeout=900),
            # Its last reply after the last proposal: a recording cut before it has the agent
            # ask a replay for one more reply than it holds.
            Expect(r"general-purpose", region="screen", absent=True, wait=180),
        ]),

    Scenario(
        "review-buttons", "MR !1: drop, draft and post from the band, nothing typed", REVIEW1,
        needs_rig=True, mods=[MOD], steps=[
            Prompt("/review-mr !1", timeout=600),
            press('My review is done'),   # no own review: the first topic
            Expect(current("t1"), region="panel", wait=30),
            Expect(button('Comment', 'Drop'), region="screen"),
            press('Drop'),
            Expect(current("t2"), region="panel", wait=30),
            Expect(button('Comment'), region="screen"),
            Expect(r"^╭─"),
            press('Comment', turn=True),   # the one press here that needs the model
            # Copy stands beside the draft's heading, Post in the buttons under it.
            Expect(button('Copy'), region="screen"),
            Expect(button('Post'), region="screen"),
            press('Post'),
            Check("glab api 'projects/demo%2Fbulletproof-react/merge_requests/1/discussions'",
                  r'"new_path":"[^"]*db\.ts"'),
        ]),

    Scenario(
        "review-own", "MR !1: your own comment is matched to the agent's topic", REVIEW1,
        needs_rig=True, mods=[MOD], steps=[
            Prompt("/review-mr !1", timeout=600),
            # The user's own review, posted straight to GitLab: one comment making t1's point.
            Check("glab api -X POST 'projects/demo%2Fbulletproof-react/merge_requests/1/discussions'"
                  " -f body=\"Is the CORS setup in the mock server right? Looks odd.\"",
                  r'"id"'),
            press('My review is done', turn=True, timeout=600),
            Expect(button('Merge into t1', 'Keep separate'), region="screen"),
            press('Merge into t1'),
            # t1 is your comment now; it comes up once more for what the agent adds to it.
            Expect(button('Follow up', 'Nothing to add'), region="screen", wait=120),
            press('Follow up', turn=True),
            # Copy stands beside the draft's heading, Post in the buttons under it.
            Expect(button('Copy'), region="screen"),
            Expect(button('Post'), region="screen"),
            press('Post'),                 # a reply below the user's own comment
            Check("glab api 'projects/demo%2Fbulletproof-react/merge_requests/1/discussions'"
                  " | python3 -c 'import json,sys; print(max(len(d[\"notes\"]) for d in json.load(sys.stdin)))'",
                  r"^2$"),
        ]),
    Scenario(
        "review-panel", "MR !2: the topic panel, closed and opened again with its buttons", REVIEW2,
        needs_rig=True, mods=[MOD], steps=[
            Prompt("/review-mr !2", timeout=600),
            # Where the mod draws, the opener's card is the current topic alone: the topic
            # table lives in the panel.
            Expect(r"State\s*│\s*Topic", absent=True),
            Panel(True),                # the sidebar opens by itself
            Expect(r"\bt1\b", region="panel"),
            Expect(r"\bt5\b", region="panel"),
            # Its own ✕ closes it; the band offers it back.
            Click("✕"),
            Panel(False),
            Click("Show sidebar"),
            Panel(True),
        ]),
    Scenario(
        "rework-panel", "MR !3: the panel shows the current topic's whole thread", WORK,
        needs_rig=True, mods=[MOD], steps=[
            Prompt("/rework-mr !3", timeout=600),
            Panel(True),                # opened by itself
            # The thread, as a comment box on the code it is on: the link icon, its author.
            Expect("\U000f03cc Miriam", region="panel"),
            Click("✕"),                 # its own close
            Panel(False),
        ]),

    # Talk day. Model and effort are per-session flags so the demo runs small and fast
    # without touching the settings real work uses; `tmux-demo.sh` passes the model. !3
    # gets more effort: it is the one segment that still reasons out and writes a fix.
    Scenario(
        "stage-review", "talk day: MR !1, first pass, typed and waiting", REVIEW1,
        window="review", args=["--effort", "low"], needs_rig=True,
        steps=[Type("/review-mr !1"), Handover()]),
    Scenario(
        "stage-rereview", "talk day: MR !2, re-review, typed and waiting", REVIEW2,
        window="rereview", args=["--effort", "low"], needs_rig=True,
        steps=[Type("/review-mr !2"), Handover()]),
    Scenario(
        "stage-rework", "talk day: MR !3, rework, typed and waiting", WORK,
        window="rework", args=["--effort", "medium"], needs_rig=True,
        steps=[Type("/rework-mr !3"), Handover()]),
]
