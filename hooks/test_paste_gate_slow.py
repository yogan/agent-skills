#!/usr/bin/env python3
"""Tests for the paste-enforcement Stop hook (`paste-gate.py`) — the slow ones.

Run: `python3 hooks/test_paste_gate_slow.py` (stdlib only, no deps), or the runner's
`--slow` flag. NOT part of the default `test_*.py` sweep — see the runner script for
why, and see test_paste_gate.py's module docstring for the split rationale.

Every case here pays the hook's widening re-read delay (hooks/README.md's "Patient" —
the transcript is written asynchronously, so a verdict gets a real second look before it
is committed to). Nearly all of them because they end in a BLOCK; the one allow here is
a leak check clearing, which is retried while ABSENT and so pays the same delay to prove
nothing arrived late. Measured: ~2s per case, 36 in ~72s, against 39 cases in ~6s in
test_paste_gate.py. That delay can't be mocked from here — the hook runs as a genuine
subprocess (see test_paste_gate.py's own note on why), so there's no reaching into its
process to fake time.sleep.
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import unittest  # noqa: E402

from test_paste_gate import (DIFF_VIEW_OUT, DIFF_VIEW_POINTER_OUT, HOOK,  # noqa: E402
                             POINTER_CRITICAL, QA_NOISE, QUOTE_OUT, RESUME_CRITICAL,
                             RESUME_OUT, REVIEW_SPEC, REWORK_QUOTE_OUT, REWORK_SPEC,
                             HookCase, assistant_text, bash_call, row,
                             tool_result, user_prompt, with_legacy_manifest,
                             with_manifest)


class TestGates(HookCase):
    def test_a_reader_in_a_pipe_after_a_real_invocation_still_gates(self):
        """The guard for `_invocation_text`: dropping a command's file-READING segments
        must not drop the invocation they are piped onto. `… | head -40` is still a real
        render, and has to be pasted. (Its allow-side twin — reading a gated script beside
        its own spec — is in the fast file.)"""
        self.assertBlocked([
            user_prompt(),
            bash_call("u1", "python3 $SD/findings.py resume --iid 123 | head -40"),
            tool_result("u1", RESUME_OUT),
            assistant_text("Resumed — t1 is still open."),
        ], contains="findings.py resume")

    def test_a_quoted_script_path_still_gates(self):
        """Quoting the path — the defensive habit for paths with spaces — parked a closing
        quote between the script and its subcommand, so `threads\\.py\\s+quote` could not
        match and that gate silently never fired. Found by an adversarial sweep rather than
        in production, which is the point: a false block announces itself, a gate that
        never fires is invisible."""
        self.assertBlocked([
            user_prompt(),
            bash_call("u1", 'python3 "$SD/threads.py" quote t7'),
            tool_result("u1", REWORK_QUOTE_OUT),
            assistant_text("t7 is about the config being loaded twice."),
        ], contains="threads.py quote")

    def test_leaked_manifest_marker_blocks(self):
        """The producer's trailing block manifest exists purely for this hook to
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

    def test_a_leak_in_an_already_sent_message_does_not_block_the_retry(self):
        """The two engine-level checks keep looking after they have fired once, so they
        must judge only what a retry can still change. Claude Code has already SHOWN the
        earlier message; there is no reply the model could write that would remove text
        from it, so judging the whole turn pins the block on permanently — every
        subsequent reply in that turn is refused for a leak nobody can reach. Found by
        wedging a real session, while explaining this very mechanism: the explanation
        quoted the marker with a payload under it, which is a leak by the pattern's own
        definition.

        An allow, not a block, and still here rather than in the fast file: a leak that
        has not shown up YET may still be about to (the transcript lags), so this one is
        retried while ABSENT and pays the full widening delay before it clears."""
        leak = with_manifest(RESUME_OUT, RESUME_CRITICAL)
        self.assertAllowed([
            user_prompt(),
            bash_call("u1", "python3 $SD/findings.py resume --iid 123"),
            tool_result("u1", leak),
            assistant_text(leak),                      # sent, blocked, unreachable now
            row("user", isMeta=True, message={"role": "user", "content":
                "Stop hook feedback:\nYour message contains an internal manifest block."}),
            row("system", subtype="stop_hook_summary", hookCount=3),
            assistant_text("Sorry — that was the payload, quoted. " + RESUME_OUT),
        ], stop_hook_active=True)

    def test_a_leak_in_the_retry_itself_still_blocks(self):
        """The other half: scoping to the retryable window must not blind the check to a
        leak the model introduces ON the retry, which is the exact production case the
        loop-guard exemption exists for."""
        leak = with_manifest(RESUME_OUT, RESUME_CRITICAL)
        self.assertBlocked([
            user_prompt(),
            bash_call("u1", "python3 $SD/findings.py resume --iid 123"),
            tool_result("u1", leak),
            assistant_text("t1 needs you."),
            row("user", isMeta=True, message={"role": "user", "content":
                "Stop hook feedback:\nYou ran `findings.py resume` but your message…"}),
            row("system", subtype="stop_hook_summary", hookCount=3),
            assistant_text(leak),
        ], contains="paste-gate:critical", stop_hook_active=True)

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

    def test_the_block_reason_quotes_the_lines_it_found_wrong(self):
        """A gate's own text says what to do, never what was wrong — so a model whose
        paste was in fact fine has nothing to act on and re-sends the same message, which
        the loop guard then waves through. The evidence is what makes the block
        checkable, by the model and by whoever reads the transcript afterwards."""
        body = [ln for ln in RESUME_OUT.strip().splitlines()
                if not ln.startswith("| ○ open |")]
        reason = self.assertBlocked([
            user_prompt(),
            bash_call("u1", "python3 $SD/findings.py resume --iid 123"),
            tool_result("u1", with_manifest(RESUME_OUT, RESUME_CRITICAL)),
            assistant_text("\n".join(body)),
        ], contains="absent from your message")
        self.assertIn("| ○ open | ◈ **t1** — unbounded retry loop | `src/client.py:88` |",
                      reason)

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
                  "| open | duplicate router registration | backend/acme/main.py:44 |\n\n"
                  "```python\n"
                  "app.include_router(health.router)\n"
                  "app.include_router(config.router)\n"
                  "app.include_router(config.router)\n"
                  "```")
        collapsed = dup_out.replace(
            "app.include_router(config.router)\napp.include_router(config.router)",
            "app.include_router(config.router)")
        manifest = with_manifest(dup_out, [
            "| open | duplicate router registration | backend/acme/main.py:44 |",
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

    def test_the_block_is_still_enforced_when_something_ran_ahead_of_it(self):
        """The allow-side twin of this is in the fast file (TestBlockBoundaries): a linter
        chained ahead of the gated command is not the model's to paste. Trimming what the
        command did NOT print must not become trimming what it did — the same tool result,
        with the block paraphrased away, still blocks."""
        self.assertBlocked([
            user_prompt(),
            bash_call("u1", "uv run poe lint 2>&1|tail -1; $SD/diff-view.sh t3 2>&1"),
            tool_result("u1", with_manifest(DIFF_VIEW_POINTER_OUT, POINTER_CRITICAL,
                                            before=QA_NOISE)),
            assistant_text("Two files changed, lint is clean. ACK to fix up and push?"),
        ], contains="diff-view.sh")

    def test_a_legacy_list_payload_still_protects_its_critical_lines(self):
        """A skill a few commits behind the engine emits the older payload — a bare list,
        no `first`. It loses the trimming, not the protection: a dropped row still blocks."""
        pasted = "\n".join(ln for ln in DIFF_VIEW_POINTER_OUT.splitlines()
                           if not ln.startswith("src/retry.py:"))
        self.assertBlocked([
            user_prompt(),
            bash_call("u1", "$SD/diff-view.sh t3"),
            tool_result("u1", with_legacy_manifest(DIFF_VIEW_POINTER_OUT, POINTER_CRITICAL)),
            assistant_text("Bounded the retry.\n\n" + pasted),
        ], contains="diff-view.sh")

    def test_cwd_reset_noise_stripped_but_a_real_second_drop_still_blocks(self):
        """The Bash tool appends "Shell cwd was reset to …" whenever a gated command's own
        `cd` moves the shell — harness plumbing the model was never going to paste back,
        and invisible to the check (its allow-side twin is in the fast file). Invisible is
        not a second free pass, though: drop a genuine second line on top of it and the
        block still fires."""
        dropped = "- **push 1:** 3 files, +42/-7"
        body = [ln for ln in RESUME_OUT.strip().splitlines()[1:] if ln != dropped]
        self.assertBlocked([
            user_prompt(),
            bash_call("u1", "cd /some/wt && python3 $SD/findings.py resume --iid 123"),
            tool_result("u1", RESUME_OUT + "\nShell cwd was reset to /some/wt"),
            assistant_text("Two pushes since your last look.\n" + "\n".join(body)),
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
    def test_a_rendered_warning_on_its_own_line_still_blocks(self):
        """The guard for line-anchoring the two summary warnings: the anchor frees prose
        that merely QUOTES a warning (fast file), but a pasted view whose heading really
        carries one is the failure these rules exist for — blockquoted and indented
        included, which is what the `[\\s>*_`]*` prefix covers."""
        self.assertBlocked([
            user_prompt(),
            assistant_text(
                "**MR !123** — Add rate limiting\n\n"
                "> ⚠️ **needs an English summary** — `t4` has no authored `summary`.\n"),
        ], contains="summary")

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

    def test_unresolved_needs_title_table_row_blocks(self):
        """A topic adopt_inbound surfaced from a live thread (yours or a peer's) carries
        no authored `summary` — render_table marks it with this exact glyph+label instead
        of silently showing the raw, un-summarized quote as if it were a title."""
        self.assertBlocked([
            user_prompt(),
            assistant_text(
                "| ○ open | ◈ **t9** | ⚪ | 👤 | `token.py:40` "
                "| ✍️ _needs summary:_ Sollten wir hier nicht X machen? |"),
        ], contains="needs_title")

    def test_unresolved_needs_title_quote_header_blocks(self):
        self.assertBlocked([
            user_prompt(),
            assistant_text(
                "◈ **t9** · `token.py:40` · _drafts de · rest en_\n\n"
                "⚠️ **needs an English summary** — `t9` has no authored `summary` yet"),
        ], contains="needs_title")

    def test_summary_in_the_draft_language_blocks(self):
        """`drafts de` governs the comment body only. A table whose summaries came
        out in the draft language must not reach the user — findings.py flags it and
        this rule refuses to show the flagged view."""
        self.assertBlocked([
            user_prompt(),
            assistant_text(
                "| ✎ draft | ◈ **t2** | 🟠 | 🤖 | `a.json:12` | `zone_code` ist "
                "in keinem Schema required |\n\n"
                "⚠️ **summaries must be English** — ◈ t2 read as de."),
        ], contains="always English")


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

    def test_dropping_the_line_that_names_the_window_blocks(self):
        """The head line is the only one saying where the diff is. Before it was declared
        critical, losing exactly that line passed under the one-dropped-line tolerance and
        left the user approving a force-push against a window nothing named."""
        pasted = "\n".join(DIFF_VIEW_POINTER_OUT.splitlines()[1:])
        self.assertBlocked([
            user_prompt(),
            bash_call("u1", "$SD/diff-view.sh t3"),
            tool_result("u1", with_manifest(DIFF_VIEW_POINTER_OUT, POINTER_CRITICAL)),
            assistant_text("Bounded the retry.\n\n" + pasted),
        ])


class TestRequiredIllustration(HookCase):
    """Reproducing the block at a line start blocks, even when only illustrating it, and
    that is the accepted cost of the rule working at all.

    It was briefly exempted by also anchoring the phrase to the END of the message, on the
    theory that a real ask is always the last line. The two tests below are why that had to
    go: the exemption let every improvised ask through — and an improvising model is the
    only kind this rule ever sees — while still blocking an illustration that happened to
    end the message. It bought nothing and disarmed the rule. Per hooks/README.md, the
    answer to a false block here is to say what happened and move on; the loop guard makes
    it cost exactly one retry. (The mid-sentence mention that must still be allowed is in
    the fast file.)
    """

    def test_an_improvised_ask_with_a_closing_line_still_blocks(self):
        """The shape the end-anchor let through. Verified against this hook: identical
        input was ALLOWED while that anchor was in place."""
        self.assertBlocked([
            user_prompt(),
            assistant_text("Fixed t3 by bounding the loop.\n\n"
                           "ACK to fix up and push?\n\n"
                           "I'll run the full suite right after the rebase."),
        ], contains="fixup+push ACK")

    def test_an_ask_in_an_earlier_block_of_the_turn_still_blocks(self):
        """The hook joins every assistant block of a turn into one string, so an ask
        followed by another tool call and a sign-off escaped the end-anchor too — which is
        the exact sequence SKILL.md documents as the recurring production bug."""
        self.assertBlocked([
            user_prompt(),
            assistant_text("Fixed t3.\n\nACK to fix up and push?"),
            bash_call("u1", "git blame -L 10,20 src/a.ts"),
            tool_result("u1", "abc1234 (Ada 2026-01-01 10) const x = 1\n"),
            assistant_text("Blame says commit 2."),
        ], contains="fixup+push ACK")


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

    def test_leak_caught_despite_delayed_transcript_flush(self):
        """A leak must be caught even if it hasn't hit disk yet when the hook's first
        read happens — not just once it's already there. Production miss: the retry
        loop gave up the instant its first check found nothing, instead of waiting to
        see if a leak was still being flushed. Modeled by writing everything up to the
        gated tool_result up front, starting the hook, then appending the leaking
        message from a background thread once the hook is already running."""
        manifest = with_manifest(RESUME_OUT, RESUME_CRITICAL)
        rows = [
            user_prompt(),
            bash_call("u1", "python3 $SD/findings.py resume --iid 123"),
            tool_result("u1", manifest),
        ]
        leak_row = assistant_text(manifest)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "transcript.jsonl")
            with open(path, "w") as fh:
                for r in rows:
                    fh.write(json.dumps(r) + "\n")

            proc = subprocess.Popen(
                [sys.executable, HOOK, REVIEW_SPEC, REWORK_SPEC],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True)
            proc.stdin.write(json.dumps({"transcript_path": path}))
            proc.stdin.close()

            def append_late():
                time.sleep(0.15)
                with open(path, "a") as fh:
                    fh.write(json.dumps(leak_row) + "\n")

            t = threading.Thread(target=append_late)
            t.start()
            out, err = proc.communicate(timeout=10)
            t.join()

        self.assertEqual(proc.returncode, 0, err)
        self.assertEqual(err, "", err)
        self.assertNotEqual(out.strip(), "", "expected a block, got allow")
        decision = json.loads(out)
        self.assertEqual(decision["decision"], "block")
        self.assertIn("paste-gate:critical", decision["reason"])

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
