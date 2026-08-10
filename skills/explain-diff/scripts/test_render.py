#!/usr/bin/env python3
"""Tests for explain-diff's render.py.

Run: `python3 skills/explain-diff/scripts/test_render.py` (stdlib only; the
TestRenderDiagram class additionally needs Graphviz's `dot` on PATH and is skipped
automatically when it isn't).

Regression tests are grounded in real bugs from this file's git history (see each
docstring for the commit); everything else covers the main documented behavior of the
pure formatting functions and the top-level render() assembly.
"""
import os
import shutil
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import render as R  # noqa: E402


class TestFormatLabelSegment(unittest.TestCase):
    def test_plain_text_is_just_escaped(self):
        self.assertEqual(R._format_label_segment("a < b"), "a &lt; b")

    def test_ellipsis_is_normalized(self):
        self.assertIn("…", R._format_label_segment("wait..."))
        self.assertNotIn("...", R._format_label_segment("wait..."))

    def test_code_span_gets_menlo_font(self):
        out = R._format_label_segment("call `foo`")
        self.assertIn('<FONT FACE="Menlo">foo</FONT>', out)

    def test_code_span_content_is_escaped(self):
        out = R._format_label_segment("`a<b>`")
        self.assertIn("a&lt;b&gt;", out)

    def test_tight_punctuation_after_short_code_span_gets_a_spacer(self):
        """d205803: "`Router`, `Title`, ..." rendered with the punctuation overlapping
        the code span's last glyph — Graphviz's width estimate for a FONT-FACE run runs
        narrow. A thin space entity (&#8201;) after the span closes the gap."""
        out = R._format_label_segment("`Router`, next")
        self.assertIn("</FONT>&#8201;, next", out)

    def test_long_code_span_before_a_real_space_still_gets_spacers(self):
        """98ac3f1: the original fix (a single thin space, and only when no whitespace
        already followed) left long code spans followed by a real space unfixed —
        "`documents` row" rendered as "documentsrow" once the width underestimate, which
        scales with run length, exceeded the space glyph's own width. Spacers must be
        inserted unconditionally, scaled to the code span's length."""
        out = R._format_label_segment("`documents` row")
        # scaled: len("documents") // 8 == 1 thin space, still present before "row"
        self.assertIn("</FONT>&#8201; row", out)

    def test_spacer_count_scales_with_code_span_length(self):
        short = R._format_label_segment("`ab` x")
        long = R._format_label_segment("`abcdefghijklmnop` x")
        self.assertLess(short.count("&#8201;"), long.count("&#8201;"))


class TestFormatMeta(unittest.TestCase):
    def test_markdown_link_becomes_a_tag(self):
        out = R.format_meta("[MR !123](https://example.com/123)")
        self.assertIn('<a href="https://example.com/123" target="_blank"', out)
        self.assertIn(">MR !123</a>", out)

    def test_backtick_becomes_code(self):
        self.assertEqual(R.format_meta("`branch`"), "<code>branch</code>")

    def test_plain_text_is_escaped(self):
        self.assertEqual(R.format_meta("a < b"), "a &lt; b")

    def test_line_break_after_mr_link_drops_the_dangling_separator(self):
        """84f8f11 + 7b97c2f, two-stage bug: a long MR title made the subtitle line wrap
        wherever it happened to fit width-wise, so a deliberate <br> was forced right
        after the MR link instead. The FIRST fix kept the "·" separator, which then
        dangled alone at the start of line 2 with nothing before it — the SECOND fix
        dropped it. Both parts must hold: the break is inserted, and no bare "·" survives
        immediately after it, while the separator before "commit" (later in the string,
        same line) is untouched."""
        text = "[MR !123](https://example.com/123) · `feat/thing` · commit `abcd123`"
        out = R.format_meta(text)
        self.assertIn("</a><br><code>feat/thing</code>", out)
        self.assertNotIn("<br>·", out)
        # the separator between branch and commit, later on line 2, must survive
        self.assertIn("<code>feat/thing</code> · commit <code>abcd123</code>", out)

    def test_no_link_present_is_unaffected(self):
        """branch:/commit: specs have no link and must stay on one line — the <br>
        insertion is keyed off a literal </a>, so text with no link can't match it."""
        out = R.format_meta("`feat/thing` · commit `abcd123`")
        self.assertNotIn("<br>", out)


class TestFormatDiffstat(unittest.TestCase):
    def test_singular_file(self):
        self.assertIn("1 file", R.format_diffstat({"files": 1}))
        self.assertNotIn("1 files", R.format_diffstat({"files": 1}))

    def test_plural_files(self):
        self.assertIn("3 files", R.format_diffstat({"files": 3}))

    def test_zero_insertions_omits_the_span(self):
        """Matching GitLab's own behavior — a zero count isn't shown at all, not "+0"."""
        out = R.format_diffstat({"files": 2, "insertions": 0, "deletions": 5})
        self.assertNotIn("+0", out)
        self.assertIn("−5", out)

    def test_zero_deletions_omits_the_span(self):
        out = R.format_diffstat({"files": 2, "insertions": 5, "deletions": 0})
        self.assertNotIn("−0", out)
        self.assertIn("+5", out)

    def test_all_present(self):
        out = R.format_diffstat({"files": 27, "insertions": 736, "deletions": 19})
        self.assertIn("27 files", out)
        self.assertIn("+736", out)
        self.assertIn("−19", out)


class TestFormatCommitByline(unittest.TestCase):
    def test_with_url_links_the_subject(self):
        out = R.format_commit_byline({"hash": "a1b2c3d4", "subject": "fix: drop legacy-auth-adapter",
                                      "url": "https://example.com/commit/a1b2c3d4"})
        self.assertIn('<a href="https://example.com/commit/a1b2c3d4"', out)
        self.assertIn(">fix: drop legacy-auth-adapter</a>", out)
        self.assertIn("<code>a1b2c3d4</code>", out)

    def test_without_url_subject_is_plain_text(self):
        """No resolvable commit page (e.g. no MR context) — renders as plain text, not
        a link to nowhere."""
        out = R.format_commit_byline({"hash": "abc1234", "subject": "a fix"})
        self.assertNotIn("<a href", out)
        self.assertIn("a fix", out)

    def test_diffstat_appended_when_present(self):
        out = R.format_commit_byline({"hash": "abc1234", "subject": "a fix",
                                      "diffstat": {"files": 1, "insertions": 2}})
        self.assertIn("1 file", out)
        self.assertIn("+2", out)

    def test_no_diffstat_when_absent(self):
        out = R.format_commit_byline({"hash": "abc1234", "subject": "a fix"})
        self.assertNotIn("file", out)

    def test_subject_is_escaped(self):
        out = R.format_commit_byline({"hash": "abc1234", "subject": "a <script> fix"})
        self.assertIn("&lt;script&gt;", out)


class TestFormatInline(unittest.TestCase):
    def test_code_span_and_escaping(self):
        self.assertEqual(R.format_inline("a `b<c>` d"), "a <code>b&lt;c&gt;</code> d")


class TestSlugify(unittest.TestCase):
    def test_lowercases_and_hyphenates(self):
        self.assertEqual(R.slugify("Fix Retry Loop!"), "fix-retry-loop")

    def test_strips_leading_trailing_hyphens(self):
        self.assertEqual(R.slugify("--already hyphenated--"), "already-hyphenated")


class TestCollectAllQuizQuestions(unittest.TestCase):
    def test_combines_top_level_and_section_quizzes(self):
        spec = {
            "quiz": [{"question": "top"}],
            "sections": [
                {"id": "a", "quiz": [{"question": "sec-a"}]},
                {"id": "b"},
            ],
        }
        questions = R.collect_all_quiz_questions(spec)
        self.assertEqual([q["question"] for q in questions], ["top", "sec-a"])

    def test_empty_spec_yields_no_questions(self):
        self.assertEqual(R.collect_all_quiz_questions({}), [])


class TestCheckLengthBias(unittest.TestCase):
    def _quiz(self, n, biased):
        """`n` questions; `biased` of them have the correct option uniquely longest."""
        quiz = []
        for i in range(n):
            if i < biased:
                options = [{"text": "short", "correct": False},
                          {"text": "the correct and much longer answer", "correct": True}]
            else:
                options = [{"text": "same len a", "correct": False},
                          {"text": "same len b", "correct": True}]
            quiz.append({"question": f"q{i}", "options": options})
        return quiz

    def test_empty_quiz_passes(self):
        R.check_length_bias([])  # must not raise

    def test_no_bias_passes(self):
        R.check_length_bias(self._quiz(6, biased=0))

    def test_bias_within_the_one_third_allowance_passes(self):
        R.check_length_bias(self._quiz(6, biased=2))

    def test_bias_beyond_the_allowance_exits(self):
        with self.assertRaises(SystemExit):
            R.check_length_bias(self._quiz(6, biased=3))


class TestRenderDiagramsInHtml(unittest.TestCase):
    """render_diagram (the part that shells out to `dot`) is mocked here so these test
    the token-substitution/wrapper-collapsing logic in isolation — see TestRenderDiagram
    for real Graphviz invocation."""

    def setUp(self):
        self._orig = R.render_diagram
        R.render_diagram = lambda d: f"<svg>{d['nodes'][0]['id']}</svg>"

    def tearDown(self):
        R.render_diagram = self._orig

    def test_replaces_token_with_diagram_div(self):
        out = R.render_diagrams_in_html("<p>{{diagram:flow}}</p>",
                                        {"flow": {"nodes": [{"id": "a"}]}})
        self.assertIn('<div class="diagram diagram-embed"', out)
        self.assertIn("<svg>a</svg>", out)

    def test_unknown_diagram_key_raises(self):
        with self.assertRaises(KeyError):
            R.render_diagrams_in_html("{{diagram:missing}}", {})

    def test_collapses_redundant_pre_merge_wrapper(self):
        """A content spec still wrapping the token in its own `<div class="diagram">`
        (the pre-merge convention) would otherwise double up the card — collapse it so
        an out-of-date spec doesn't render two nested border/padding rings."""
        html_in = '<div class="diagram">{{diagram:flow}}</div>'
        out = R.render_diagrams_in_html(html_in, {"flow": {"nodes": [{"id": "a"}]}})
        self.assertEqual(out.count('<div class="diagram'), 1)


@unittest.skipUnless(shutil.which("dot"), "Graphviz's `dot` is not on PATH")
class TestRenderDiagram(unittest.TestCase):
    def test_produces_an_svg_with_the_node_label(self):
        svg = R.render_diagram({
            "nodes": [{"id": "a", "label": "Client"}, {"id": "b", "label": "Server"}],
            "edges": [["a", "b", "request"]],
        })
        self.assertTrue(svg.startswith("<svg"))
        self.assertIn("Client", svg)
        self.assertIn("Server", svg)

    def test_fail_node_gets_no_special_content_change(self):
        """A "fail" node only changes fill/border color (asserted structurally elsewhere
        via CSS); this just confirms the node still renders without erroring."""
        svg = R.render_diagram({
            "nodes": [{"id": "a", "label": "Client"}, {"id": "b", "label": "Timeout", "fail": True}],
            "edges": [["a", "b"]],
        })
        self.assertIn("Timeout", svg)


class TestRender(unittest.TestCase):
    """render()'s overall assembly — a smoke test over a realistic spec, not an
    exhaustive HTML structure check."""

    def _spec(self, **overrides):
        spec = {
            "title": "Fix the retry loop",
            "subtitle": "[MR !123](https://example.com/123) · `fix/retry` · commit `abcd123`",
            "sections": [{"id": "background", "heading": "Background", "html": "<p>Why.</p>"}],
            "quiz": [{"question": "q", "options": [{"text": "a", "correct": True},
                                                    {"text": "b", "correct": False}]}],
        }
        spec.update(overrides)
        return spec

    def test_full_page_contains_title_subtitle_sections_and_quiz(self):
        out = R.render(self._spec())
        self.assertIn("<title>Fix the retry loop</title>", out)
        self.assertIn(">MR !123</a>", out)
        self.assertIn('<h2 id="background">Background</h2>', out)
        self.assertIn('<h2 id="quiz">Quiz</h2>', out)

    def test_diffstat_appended_without_subtitle_has_no_leading_separator(self):
        out = R.render(self._spec(subtitle="", diffstat={"files": 3, "insertions": 10}))
        self.assertIn("3 files", out)
        self.assertNotIn("· 3 files", out)

    def test_diffstat_appended_after_subtitle_has_a_separator(self):
        out = R.render(self._spec(diffstat={"files": 3, "insertions": 10}))
        self.assertIn("· 3 files", out)

    def test_no_subtitle_and_no_diffstat_omits_the_subtitle_paragraph(self):
        out = R.render(self._spec(subtitle=""))
        self.assertNotIn('margin-top:-.5rem;"></p>', out)

    def test_section_commit_byline_is_rendered(self):
        out = R.render(self._spec(sections=[{
            "id": "code", "heading": "Code walkthrough", "html": "<p>...</p>",
            "commit": {"hash": "a1b2c3d4", "subject": "fix: drop legacy-auth-adapter"},
        }]))
        self.assertIn("commit <code>a1b2c3d4</code>", out)

    def test_quiz_free_spec_omits_quiz_section(self):
        out = R.render(self._spec(quiz=[]))
        self.assertNotIn('<h2 id="quiz">', out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
