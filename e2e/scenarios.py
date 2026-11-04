"""The scenarios `drive.py` runs. Each is one claude session, start to finish.

The `stage-*` ones build the talk-day windows: claude started, the opening line typed, nothing
sent. The rest check something and need nothing typed by a person. `rig` ones need the local
GitLab up and reset (`./tmux-demo.sh --check` passes); the others run anywhere.

An expectation names what a person would look for on the screen, not how the skill gets there:
a check on wording the model chooses breaks whenever the model words it differently, and then
the run says nothing about the skill.
"""
from pathlib import Path

from drive import Expect, Handover, Prompt, Scenario, Type

# What the mod's cards look like on screen, against what a pasted view looks like: a card is
# a rounded box at the screen's left edge; a table the model pasted is drawn in the reply,
# indented under its `⏺`, outside any box.
CARD = Expect(r"^╭─")
NOT_PASTED = Expect(r"^  [┌├└]─", absent=True)

# The mod, from this checkout. A verify run loads it with --plugin-dir; a showcase runs on
# what the person has installed, so it is given no mods.
MOD = str(Path(__file__).resolve().parent.parent / "mods" / "review-ui")

REVIEW1 = "~/src/agent-skills-demo-review-mr1"
REVIEW2 = "~/src/agent-skills-demo-review-mr2"
WORK = "~/src/agent-skills-demo"

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
            Expect(r"\bt5\b"),
            CARD, NOT_PASTED,
        ]),
    Scenario(
        "rereview-2", "MR !2, re-review: the topics that need your ack", REVIEW2,
        needs_rig=True, mods=[MOD], steps=[
            Prompt("/review-mr !2", timeout=600),
            Expect(r"\bt1\b"),
            Expect(r"\bt4\b"),
            CARD, NOT_PASTED,
        ]),
    Scenario(
        "rework-3", "MR !3, rework: three reviewer threads", WORK,
        needs_rig=True, mods=[MOD], steps=[
            Prompt("/rework-mr !3", timeout=600),
            Expect(r"\bt1\b"),
            Expect(r"\bt3\b"),
            CARD, NOT_PASTED,
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
