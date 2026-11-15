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
# How long a check that only looks keeps asking GitLab for what the press before it posts.
SOON = 30
# `$P`: the project of the rig a scenario runs on, as the API takes it (drive.py's `rig_env`).
THREADS = 'glab api "projects/$P/merge_requests/3/discussions"'


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
          r"^2 frank$", wait=SOON),
]

REVIEW1 = "~/src/agent-skills-demo-review-mr1"
REVIEW2 = "~/src/agent-skills-demo-review-mr2"
WORK = "~/src/agent-skills-demo"
SCROLL = "~/src/agent-skills-demo-scroll"     # MR !4's checkout (fixture.py's MR4_DIR)
# A scrollbar's thumb as the sidebar draws it: a full cell, or one filled from the bottom.
THUMB = Expect(r"[█▁▂▃▄▅▆▇]", region="panel", wait=30)
# How many of MR !4's topics carry a proposal, asked until all do or 14 minutes have passed.
EVERY_PROPOSAL = ("python3 -c \"import json,os,time\n"
                  "p=os.path.expanduser('~/.claude/rework-mr/$SLUG--mr4/topics.json')\n"
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
            Check('glab api "projects/$P/merge_requests/3/discussions"'
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
            # The reviewer settles the threads that wait on them and approves: every topic is
            # settled, and the box takes the MR to its end, each step a press with no turn.
            Check(f"{NEWS} settle", r"resolved its threads"),
            Expect(button('Merge'), region="panel", wait=240),
            press('Merge', region="panel"),
            Expect(r"You merged the MR", region="panel", wait=60),
            Expect(button('Clean up locally'), region="panel"),
            Check('glab api "projects/$P/merge_requests/3"'
                  " | python3 -c 'import json,sys; print(json.load(sys.stdin)[\"state\"])'", r"^merged$"),
            press('Clean up locally', region="panel"),
            Expect(r"All cleaned up\. You can close this session\.", region="panel", wait=60),
            Check("git branch --show-current", r"^main$"),
]

# The end of MR !3 past a plain merge, staged from outside on `rework-fix`'s recording once the
# box offers the merge: no step after that needs the model. The rig has no runner, so a
# pipeline is a status `news.py` posts on the MR's head.
SETTLED = next(i for i, x in enumerate(REWORK_FIX)
               if isinstance(x, Check) and x.command == f"{NEWS} settle") + 2
# The sidebar's marks of the MR's pipeline, beside its title (panel.ts's MR_ICON).
PIPELINE_RUNNING, PIPELINE_FAILED = "\U000f0996", "\U000f110a"
REWORK_PIPELINE = [
            *REWORK_FIX[:SETTLED],
            # A failed pipeline is said whatever else stands, with the press that asks why.
            Check(f"{NEWS} pipeline failed", r"pipeline failed"),
            Expect(r"Pipeline failed", region="panel", wait=120),
            Expect(button('Analyze CI failure'), region="panel"),
            Expect(PIPELINE_FAILED, region="panel"),
            # Running again: the merge waits for it, on a press.
            Check(f"{NEWS} pipeline running", r"pipeline running"),
            Expect(button('Set to auto-merge'), region="panel", wait=120),
            Expect(PIPELINE_RUNNING, region="panel"),
            press('Set to auto-merge', region="panel"),
            Expect(r"Waits for the pipeline", region="panel", wait=60),
            # It passes, and GitLab merges it.
            Check(f"{NEWS} pipeline success", r"pipeline success"),
            Expect(r"You merged the MR", region="panel", wait=120),
            Expect(button('Clean up locally'), region="panel"),
]
# GitLab refusing the merge at the press: made a draft just before it, as a person might on
# GitLab while the box still offers the merge. The merge request is made again before every
# run, so the draft goes with it.
REWORK_REFUSED = [
            *REWORK_FIX[:SETTLED],
            Check('glab api -X PUT "projects/$P/merge_requests/3" -f title="Draft: not yet"',
                  r'"draft":true'),
            press('Merge', region="panel"),
            Expect(r"Merge failed", region="panel", wait=60),
            Expect(button('Analyze failed merge'), region="panel"),
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
            Expect(r"You pushed a fix · 2 pushes", region="screen"),
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
                  r"^\(2, 2\)$", wait=SOON),
]

# The same MR, the plan pass ended in the chat, as real runs went: with t3's plan typed in
# (a decline that leaves a TODO, so a fix), t3 stays current with Fix it, and the model either
# waits for it (`rework-mind`) or goes on to t1's fix unasked (`rework-ahead`). Each
# scenario's recording is a real run that went its way.
DROPPED = "git for-each-ref --format='%(refname)' refs/rework-mr/"
MIND_START = [
            Prompt("/rework-mr !3", timeout=600),
            *REWORK_FIX[1:7],                         # t1's and t2's plans agreed
            Expect(current("t3"), region="panel", wait=180),
            Expect(button('Reply'), region="screen", wait=180),
            Prompt("t3: decline it for this MR, but leave a TODO above discussionKeys saying the "
                   "users and teams features still build their keys inline. That is agreed.",
                   timeout=900),
]
# t3 from its Fix it: the TODO pushed, then the decline in its thread, not a "Fixed".
T3_FIXED = [
            press('Fix it', turn=True, timeout=600),
            press('Ack and push', turn=True, timeout=600),
            Expect(button('Reply…'), region="screen", wait=60),
            press('Reply…'),
            Prompt("decline it as out of scope here; the TODO marks it for a follow-up", timeout=600),
            Expect(button('Post'), region="screen"),
            press('Post'),
            Check("git fetch -q && git show @{u}:src/features/discussions/api/query-keys.ts | grep -c TODO",
                  r"^[1-9]", wait=SOON),
            Check(thread_on("discussions/api/query-keys.ts", 'len(d["notes"]), d["notes"][-1]["author"]["username"]'),
                  r"^2 frank$", wait=SOON),
]
T1_RESOLVED = Check(thread_on("comments/api/query-keys.ts", 'd["notes"][0]["resolved"]'), r"^True$", wait=SOON)

REWORK_AHEAD = [
            *MIND_START,
            # t1's fix made while t3 was current: shown as t1's, ready to push, its card saying
            # which topic the person was on, with the choice to keep it for later.
            Expect(current("t1"), region="panel", wait=60),
            Expect(button('Ack and push'), region="screen", wait=60),
            Expect(r"Made while you were on t3", region="screen"),
            press('Put aside'),
            # Back to t3, the working tree as it was, the fix kept in git.
            Expect(current("t3"), region="panel", wait=60),
            Expect(button('Fix it'), region="screen", wait=60),
            Check("git status --porcelain | wc -l", r"^\s*0$"),
            Check(DROPPED, r"refs/rework-mr/mr3/t1/dropped1/after"),
            *T3_FIXED,
            # t1 next: its fix back in the working tree, by itself. t3's push moved the files
            # on, so its checks run again before it is pushed.
            Expect(current("t1"), region="panel", wait=60),
            Expect(r"The fix you put aside", region="screen", wait=60),
            Check("git status --porcelain | wc -l", r"^\s*4$"),
            press('Run the checks', turn=True, timeout=600),
            press('Ack and push', turn=True, timeout=600),
            Expect(button('“Fixed” + resolve'), region="screen", wait=60),
            press('“Fixed” + resolve'),
            Expect(current("t2"), region="panel", wait=120),
            T1_RESOLVED,
            Check("git fetch -q && git rev-parse HEAD @{u} | uniq | wc -l", r"^\s*1$"),
            Check("git rev-list --count origin/main..@{u}", r"^2$"),   # both folded in
]
REWORK_MIND = [
            *MIND_START,
            # It waited: t3 is current, its fix the person's to start.
            Expect(current("t3"), region="panel", wait=60),
            *T3_FIXED,
            # t1: the rename, pushed and "Fixed".
            Expect(current("t1"), region="panel", wait=60),
            press('Fix it', turn=True, timeout=600),
            press('Ack and push', turn=True, timeout=600),
            Expect(button('“Fixed” + resolve'), region="screen", wait=60),
            press('“Fixed” + resolve'),
            T1_RESOLVED,
            # t2: made, then not wanted after all, before it is pushed.
            Expect(current("t2"), region="panel", wait=120),
            press('Fix it', turn=True, timeout=600),
            Expect(button('Ack and push'), region="screen"),
            Prompt("Stop, I don't want this fix after all: drop it, and draft a reply asking "
                   "Miriam which of her two options she prefers.", timeout=600),
            Expect(button('Post', 'Post + resolve'), region="screen", wait=60),
            Check("git status --porcelain | wc -l", r"^\s*0$"),      # the working tree as it was
            Check(DROPPED, r"refs/rework-mr/mr3/t2/dropped1/after"),   # the fix, kept in git
            Check("git fetch -q && git rev-parse HEAD @{u} | uniq | wc -l", r"^\s*1$"),
            press('Post'),
            # A question to the reviewer: t2 waits on them, and the pass is through.
            Expect(current("t2"), region="panel", absent=True, wait=60),
            Check(thread_on("updateDiscussion.ts", 'len(d["notes"]), d["notes"][-1]["author"]["username"]'),
                  r"^2 frank$"),
            # The reviewer doubts t1's pushed rename; the person keeps it.
            Check(f"{NEWS} doubt", r"doubts the fix"),
            Expect(r"\bt1\b", region="panel", wait=240),
            Click(" t1 ", offset=1, region="panel"),
            Expect(f"(?:{button('Agree plan')}|{button('Reply')})", region="screen", wait=240),
            Prompt("t1: keep the rename and push back: aliasing on every import is what the rename "
                   "saves. Draft the reply.", timeout=600),
            # A defended fix: a reply, never resolving, and no second "Fixed".
            Expect(button('Post'), region="screen", wait=60),
            Expect(button('Post + resolve'), region="screen", absent=True),
            Expect(button('“Fixed”'), region="screen", absent=True),
            press('Post'),
            Check(thread_on("comments/api/query-keys.ts",
                            'sum(n["body"].startswith("Fixed") for n in d["notes"]), d["notes"][-1]["author"]["username"]'),
                  r"^1 frank$", wait=SOON),
            Check("git rev-list --count origin/main..@{u}", r"^2$"),   # the TODO folded in too
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
            # Where the mod draws, the opener's card is the current topic alone: the topic
            # table lives in the sidebar.
            Expect(r"State\s*│\s*Topic", absent=True),
            # The author's replies and pushes since the last review are the inbox's, not
            # the chat's: the sidebar opens with them, and the topics they touch.
            Panel(True),
            Expect(r"\bSince \d\d:\d\d\b|\bNews\b", region="panel"),
            Expect(r"\bt1\b", region="panel"),
            Expect(r"\bt4\b", region="panel"),
            Expect(r"\bt5\b", region="panel"),
            # Its own ✕ closes it; the band offers it back.
            Click("✕"),
            Panel(False),
            Click("Show sidebar"),
            Panel(True),
            # The author's push that changed t1's lines is its fix, found from the MR's
            # versions: on its card with the push's link, its files in the sidebar.
            Click(" t1 ", offset=1, region="panel"),
            Expect(r"Alex pushed a fix · .*diff", region="screen", wait=60),
            Expect(r"Show in hunk", region="panel"),
        ]),
    Scenario(
        "rework-3", "MR !3, rework: three reviewer threads", WORK,
        needs_rig=True, mods=[MOD], steps=[
            Prompt("/rework-mr !3", timeout=600),
            Expect(r"\bt1\b"),
            CARD, NOT_PASTED,
            Panel(True),                # opened by itself
            Expect(r"\bt3\b", region="panel"),
            # The current topic's thread, as a comment box on the code it is on: the link
            # icon, its author.
            Expect("\U000f03cc Miriam", region="panel"),
            Click("✕"),                 # its own close
            Panel(False),
        ]),

    Scenario(
        "rework-rounds", "MR !3: the plan pass, two plans agreed with a press and a reply instead of the third", WORK,
        needs_rig=True, mods=[MOD], fast_check=True, steps=[
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
        needs_rig=True, mods=[MOD], fast_check=True, steps=[*REWORK_FIX]),

    Scenario(
        "rework-pipeline", "MR !3 settled: a failed pipeline, auto-merge while one runs, merged when it passes", WORK,
        needs_rig=True, mods=[MOD], fast_check=True, recording="rework-fix", steps=REWORK_PIPELINE),

    Scenario(
        "rework-refused", "MR !3 settled: GitLab refuses the merge, and the box says so", WORK,
        needs_rig=True, mods=[MOD], fast_check=True, recording="rework-fix", steps=REWORK_REFUSED),

    Scenario(
        "try-pushed-fix", "MR !3 up to t1's pushed fix in four files, for a person to try (--keep)", WORK,
        needs_rig=True, mods=[MOD], fast_check=True, recording="rework-fix", steps=REWORK_FIX[:21]),

    Scenario(
        "rework-twice", "MR !3: t1 fixed again after t2's push, two pushes on its card, the sidebar and its reply", WORK,
        needs_rig=True, mods=[MOD], fast_check=True, steps=[*REWORK_TWICE]),

    Scenario(
        "try-pushed-twice", "MR !3 up to t1's second push, t2's between, for a person to try (--keep)", WORK,
        needs_rig=True, mods=[MOD], fast_check=True, recording="rework-twice", steps=REWORK_TWICE[:TRY_TWICE]),

    Scenario(
        "rework-mind", "MR !3: a decline that leaves a TODO, a fix dropped for a question, a pushed fix defended", WORK,
        needs_rig=True, mods=[MOD], fast_check=True, steps=[*REWORK_MIND]),

    Scenario(
        "rework-ahead", "MR !3: t1's fix made while t3 was current, put aside, back by itself after t3's push", WORK,
        needs_rig=True, mods=[MOD], fast_check=True, steps=[*REWORK_AHEAD]),

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
            # The sidebar moves to the next topic before the Drop press has drawn its card.
            Expect(button('Comment'), region="screen", wait=30),
            Expect(r"^╭─"),
            press('Comment', turn=True),   # the one press here that needs the model
            # Copy stands beside the draft's heading, Post in the buttons under it.
            Expect(button('Copy'), region="screen"),
            Expect(button('Post'), region="screen"),
            press('Post'),
            Check('glab api "projects/$P/merge_requests/1/discussions"',
                  r'"new_path":"[^"]*db\.ts"', wait=SOON),
        ]),

    Scenario(
        "review-approve", "MR !1: every topic dropped by a press, nothing waits, approved from the box", REVIEW1,
        needs_rig=True, mods=[MOD], recording="review-buttons", steps=[
            Prompt("/review-mr !1", timeout=600),
            press('My review is done'),
            *[step for t in ("t1", "t2", "t3", "t4", "t5") for step in (
                Expect(current(t), region="panel", wait=30),
                Expect(button('Drop'), region="screen", wait=30),
                press('Drop'))],
            # Nothing waits on the author: the box's last line is the reviewer's approval.
            Expect(button('Approve'), region="panel", wait=60),
            press('Approve', region="panel"),
            Expect(r"Approved by you", region="panel", wait=60),
            Check('glab api "projects/$P/merge_requests/1/approvals"'
                  " | python3 -c 'import json,sys; print([a[\"user\"][\"username\"] for a in json.load(sys.stdin)[\"approved_by\"]])'",
                  r"frank", wait=30),
        ]),
    Scenario(
        "review-own","MR !1: your own comment is matched to the agent's topic", REVIEW1,
        needs_rig=True, mods=[MOD], steps=[
            Prompt("/review-mr !1", timeout=600),
            # The user's own review, posted straight to GitLab: one comment making t1's point.
            Check('glab api -X POST "projects/$P/merge_requests/1/discussions"'
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
            Check('glab api "projects/$P/merge_requests/1/discussions"'
                  " | python3 -c 'import json,sys; print(max(len(d[\"notes\"]) for d in json.load(sys.stdin)))'",
                  r"^2$", wait=SOON),
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
