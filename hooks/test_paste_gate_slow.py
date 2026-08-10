#!/usr/bin/env python3
"""Tests for the paste-enforcement Stop hook (`paste-gate.py`) — the slow ones.

Run: `python3 hooks/test_paste_gate_slow.py` (stdlib only, no deps), or the runner's
`--slow` flag. NOT part of the default `test_*.py` sweep — see the runner script for
why, and see test_paste_gate.py's module docstring for the split rationale.

Every case here ends in a BLOCK. The hook re-reads the transcript with a widening delay
before committing to a block (hooks/README.md's "Patient" — the transcript is written
asynchronously, so a turn that looks like a violation gets a real second look first).
Measured: each of these costs ~1.85s; the ~26 "allow" cases in test_paste_gate.py cost
under 0.15s each. That delay can't be mocked from here — the hook runs as a genuine
subprocess (see test_paste_gate.py's own note on why), so there's no reaching into its
process to fake time.sleep.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import unittest  # noqa: E402

from test_paste_gate import (DIFF_VIEW_OUT, QUOTE_OUT, RESUME_CRITICAL,  # noqa: E402
                             RESUME_OUT, REVIEW_SPEC, REWORK_QUOTE_OUT,
                             HookCase, assistant_text, bash_call, row,
                             tool_result, user_prompt, with_manifest)


class TestGates(HookCase):
    def test_leaked_manifest_marker_blocks(self):
        """The producer's trailing critical-lines manifest exists purely for this hook to
        read — it must never reach the user, whatever else is true about the rest of the
        message. Pasting the tool result raw (rather than the intended verbatim-minus-
        manifest block) would leak it."""
        manifest = with_manifest(RESUME_OUT, RESUME_CRITICAL)
        self.assertBlocked([
            user_prompt(),
            bash_call("u1", "python3 $SD/findings.py resume --iid 123"),
            tool_result("u1", manifest),
            assistant_text(manifest),
        ], contains="paste-gate:critical")

    def test_paraphrase_blocks(self):
        self.assertBlocked([
            user_prompt(),
            bash_call("u1", "python3 $SD/findings.py resume --iid 123"),
            tool_result("u1", RESUME_OUT),
            assistant_text("Picking up where we left off — the author pushed twice and "
                           "topic t1 still needs you. Shall I show it?"),
        ], contains="findings.py resume")

    def test_prose_glued_onto_code_line_blocks(self):
        """A sentence spliced onto the tail of a fenced code line, with no newline in
        between, must not be treated as 'line present' just because the original text is
        still a contiguous substring of the now-longer corrupted line. Observed in
        production: the model's own transition sentence ("I dropped t1 since you're
        accepting that trade-off.") landed mid-fence, glued onto a code line, and the
        gate — checking substring-of-the-whole-blob rather than exact-line membership —
        let the garbled paste through uncaught."""
        corrupted = RESUME_OUT.replace(
            "    resp = self._send(req)",
            "    resp = self._send(req)I dropped t1 since you're accepting that trade-off.")
        self.assertBlocked([
            user_prompt(),
            bash_call("u1", "python3 $SD/findings.py resume --iid 123"),
            tool_result("u1", RESUME_OUT),
            assistant_text(corrupted),
        ], contains="findings.py resume")

    def test_one_dropped_table_row_blocks(self):
        """Unlike a dropped preamble line, a dropped TABLE ROW is exactly the failure this
        hook exists to prevent: a whole finding silently disappears from the overview,
        with nothing about the message looking wrong on its own. No tolerance, even though
        it is only one line."""
        body = [ln for ln in RESUME_OUT.strip().splitlines()
                if not ln.startswith("| ○ open |")]
        self.assertBlocked([
            user_prompt(),
            bash_call("u1", "python3 $SD/findings.py resume --iid 123"),
            tool_result("u1", with_manifest(RESUME_OUT, RESUME_CRITICAL)),
            assistant_text("\n".join(body)),
        ], contains="findings.py resume")

    def test_one_dropped_code_line_blocks(self):
        """A single line dropped from inside a fenced code block is the source the user is
        meant to judge the finding against — no tolerance, same as a table row."""
        body = [ln for ln in RESUME_OUT.strip().splitlines()
                if "resp = self._send(req)" not in ln]
        self.assertBlocked([
            user_prompt(),
            bash_call("u1", "python3 $SD/findings.py resume --iid 123"),
            tool_result("u1", with_manifest(RESUME_OUT, RESUME_CRITICAL)),
            assistant_text("\n".join(body)),
        ], contains="findings.py resume")

    def test_collapsed_duplicate_critical_line_blocks(self):
        """A block can legitimately contain two IDENTICAL critical lines — this is, in
        fact, exactly the original production report that started this round of fixes: a
        reviewed file with `app.include_router(config.router)` registered twice by
        mistake. If the model's paste silently collapses the duplicate down to one
        occurrence — a very plausible "cleanup" for an LLM to make unprompted — a
        set-membership check ("is this text present somewhere") is blind to it: the text
        IS present, just with the wrong multiplicity, so nothing looks missing. Surfaced
        by a differential fuzz between the pre- and post-difflib-refactor
        implementations (dev-time only, not a repo dependency) as a genuine improvement,
        not a behavior change to guard against — difflib's positional alignment catches
        this where the old set-based check could not."""
        dup_out = ("**2 push(es) since your last review** (tip `abc123def456`)\n\n"
                  "**MR !123** — Add rate limiting\n\n"
                  "| State | Topic | Location |\n"
                  "|---|---|---|\n"
                  "| open | duplicate router registration | backend/idp/main.py:44 |\n\n"
                  "```python\n"
                  "app.include_router(health.router)\n"
                  "app.include_router(config.router)\n"
                  "app.include_router(config.router)\n"
                  "```")
        collapsed = dup_out.replace(
            "app.include_router(config.router)\napp.include_router(config.router)",
            "app.include_router(config.router)")
        manifest = with_manifest(dup_out, [
            "| open | duplicate router registration | backend/idp/main.py:44 |",
            "app.include_router(health.router)",
            "app.include_router(config.router)",
        ])
        self.assertBlocked([
            user_prompt(),
            bash_call("u1", "python3 $SD/findings.py resume --iid 123"),
            tool_result("u1", manifest),
            assistant_text(collapsed),
        ], contains="findings.py resume")

    def test_two_dropped_lines_block(self):
        kept = [ln for ln in RESUME_OUT.strip().splitlines()
                if "unbounded retry loop" not in ln and "missing timeout" not in ln]
        self.assertBlocked([
            user_prompt(),
            bash_call("u1", "python3 $SD/findings.py resume --iid 123"),
            tool_result("u1", RESUME_OUT),
            assistant_text("\n".join(kept)),
        ])

    def test_stale_rerender_still_requires_the_newest(self):
        newer = QUOTE_OUT.replace("Dieser `except` verschluckt jeden Fehler.",
                                  "Bitte den Fehler loggen statt zu verschlucken.")
        self.assertBlocked([
            user_prompt(),
            bash_call("u1", "python3 $SD/findings.py quote t4 --iid 123"),
            tool_result("u1", QUOTE_OUT),
            bash_call("u2", 'python3 $SD/findings.py set t4 --draft "Bitte den Fehler loggen"'),
            tool_result("u2", newer),
            assistant_text("Draft updated for t4 — ok to post?"),
        ], contains="findings.py quote")

    def test_meta_row_does_not_cut_the_turn(self):
        """isMeta rows (skill load, system-reminder) carry a text block but do not start a
        new turn — if they did, `start` would jump past the gated call."""
        self.assertBlocked([
            user_prompt(),
            bash_call("u1", "python3 $SD/findings.py resume --iid 123"),
            tool_result("u1", RESUME_OUT),
            row("user", isMeta=True, message={"role": "user", "content": [
                {"type": "text", "text": "<system-reminder>skill loaded</system-reminder>"}]}),
            assistant_text("t1 needs you."),
        ])

    def test_string_result_shape(self):
        """tool_result content is sometimes a bare string rather than a block list."""
        self.assertBlocked([
            user_prompt(),
            bash_call("u1", "python3 $SD/findings.py resume --iid 123"),
            tool_result("u1", RESUME_OUT, as_blocks=False),
            assistant_text("t1 needs you."),
        ])

    def test_dict_result_shape_does_not_false_allow(self):
        """An unexpected content shape must not degrade to a silent allow — `in` against a
        non-string would just always miss."""
        rows = [
            user_prompt(),
            bash_call("u1", "python3 $SD/findings.py resume --iid 123"),
            row("user", message={"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "u1",
                 "content": {"stdout": RESUME_OUT}}]}),
            assistant_text("t1 needs you."),
        ]
        self.assertBlocked(rows)


class TestForbidden(HookCase):
    def test_raw_suggestion_fence_blocks(self):
        self.assertBlocked([
            user_prompt(),
            assistant_text("Draft for t4:\n\n```suggestion\nlogger.exception(exc)\n```"),
        ], contains="raw ```suggestion fence")

    def test_blockquoted_suggestion_fence_blocks(self):
        """`[>\\s]*` not `\\s*` — the model hid it inside a blockquote once."""
        self.assertBlocked([
            user_prompt(),
            assistant_text("> ```suggestion\n> logger.exception(exc)\n> ```"),
        ])


class TestRequired(HookCase):
    def test_ack_without_diff_view_blocks(self):
        self.assertBlocked([
            user_prompt(),
            bash_call("u1", "git diff"),
            tool_result("u1", "diff --git a/src/client.py b/src/client.py"),
            assistant_text("Fixed t3 by bounding the loop.\n\nACK to fix up and push?"),
        ], contains="without running `diff-view.sh`")

    def test_dropped_diff_view_blocks_on_the_gate_not_the_required_rule(self):
        self.assertBlocked([
            user_prompt(),
            bash_call("u1", "$SD/diff-view.sh t3"),
            tool_result("u1", DIFF_VIEW_OUT),
            assistant_text("Bounded the loop. ACK to fix up and push?"),
        ], contains="You ran `diff-view.sh`")


class TestFailOpen(HookCase):
    def test_leak_blocks_even_when_stop_hook_active(self):
        """Observed in production, on a real MR review: the model dropped `present`'s
        output, got blocked once for THAT (a different violation), and "fixed" it on
        retry by pasting the raw tool result wholesale — leaking the manifest as a side
        effect. That retry is exactly the turn where stop_hook_active is true, so the
        general loop guard would otherwise let this specific, brand-new violation
        through with no recourse — it was never the thing being retried for."""
        manifest = with_manifest(RESUME_OUT, RESUME_CRITICAL)
        self.assertBlocked([
            user_prompt(),
            bash_call("u1", "python3 $SD/findings.py resume --iid 123"),
            tool_result("u1", manifest),
            assistant_text(manifest),
        ], contains="paste-gate:critical", stop_hook_active=True)

    def test_other_skills_spec_still_enforces(self):
        """A missing spec must not disable the ones that ARE installed."""
        self.assertBlocked([
            user_prompt(),
            bash_call("u1", "python3 $SD/findings.py resume --iid 123"),
            tool_result("u1", RESUME_OUT),
            assistant_text("t1 needs you."),
        ], specs=("/nonexistent/paste-gates.json", REVIEW_SPEC))


class TestBothSkills(HookCase):
    def test_change_preview_manifest_critical_line_blocks(self):
        """`rework-mr`'s `change-preview` gate, exercised end to end with a producer
        manifest: paste-gate.py no longer parses fence widths itself at all (that
        concern — including `fence()`'s own widening for a fence containing embedded
        ``` runs, at whatever width — moved entirely to threads.py, see its own
        `test_diff_view_signature_survives_a_widened_fence` and the multi-embed test
        added alongside it). All this hook does now is trust the manifest a gated
        command appends: a line the producer declared critical gets no drop tolerance,
        however deeply it sat inside a widened fence when threads.py built it."""
        change_out = ("**Change (t9):** bound the retry loop\n\n"
                      "````python\n"
                      "# before\n"
                      "while True:\n"
                      "    resp = self._send(req)\n\n"
                      "# illustrated fix, add a comment block like:\n"
                      "```\n"
                      "retries are now bounded\n"
                      "```\n"
                      "for _ in range(MAX_RETRIES):\n"
                      "    resp = self._send(req)\n"
                      "````\n\n"
                      "Agreed?")
        body = [ln for ln in change_out.splitlines() if "for _ in range" not in ln]
        manifest = with_manifest(change_out, [
            "while True:", "resp = self._send(req)", "retries are now bounded",
            "for _ in range(MAX_RETRIES):",
        ])
        self.assertBlocked([
            user_prompt(),
            bash_call("u1", "$SD/change-preview.sh t9"),
            tool_result("u1", manifest),
            assistant_text("\n".join(body)),
        ], contains="change-preview.sh")

    def test_keys_do_not_collide_across_skills(self):
        """Both skills have a `quote` gate. Namespacing keeps one from superseding the
        other in the per-key reduction."""
        self.assertBlocked([
            user_prompt(),
            bash_call("u1", "python3 $SD/threads.py quote t7 --iid 5"),
            tool_result("u1", REWORK_QUOTE_OUT),
            bash_call("u2", "python3 $SD/findings.py quote t4 --iid 123"),
            tool_result("u2", QUOTE_OUT),
            assistant_text(QUOTE_OUT),          # review-mr's pasted, rework-mr's dropped
        ], contains="threads.py quote")

    def test_rework_present_gate(self):
        out = RESUME_OUT.split("---", 1)[1]
        self.assertBlocked([
            user_prompt(),
            bash_call("u1", "python3 $SD/threads.py present --iid 5"),
            tool_result("u1", out),
            assistant_text("t1 is up first — the retry loop."),
        ], contains="threads.py present")


if __name__ == "__main__":
    unittest.main(verbosity=2)
