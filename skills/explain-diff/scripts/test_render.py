#!/usr/bin/env python3
"""Tests for explain-diff's render.py.

Run: `python3 skills/explain-diff/scripts/test_render.py` (stdlib only; the TestD2Diagrams
class additionally needs `d2` on PATH and is skipped automatically when it isn't).

Regression tests are grounded in real bugs from this file's git history (see each
docstring for the commit); everything else covers the main documented behavior of the
pure formatting functions and the top-level render() assembly.
"""
import contextlib
import io
import os
import shutil
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import render as R  # noqa: E402
from lib.diagram.figure import Figure as _Figure  # noqa: E402


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
        pairs = R.collect_all_quiz_questions(spec)
        self.assertEqual([q["question"] for _, q in pairs], ["top", "sec-a"])

    def test_each_question_is_paired_with_where_it_lives(self):
        """So the length-bias error can say `chapter-3 #2` instead of a document-wide index
        that has to be counted back by hand across four sections' arrays."""
        spec = {"quiz": [{"question": "top"}],
                "sections": [{"id": "chapter-3", "quiz": [{"question": "c"}]}]}
        self.assertEqual([where for where, _ in R.collect_all_quiz_questions(spec)],
                         ["quiz", "chapter-3"])

    def test_empty_spec_yields_no_questions(self):
        self.assertEqual(R.collect_all_quiz_questions({}), [])


class TestCheckLengthBias(unittest.TestCase):
    def _quiz(self, n, biased, where="quiz"):
        """`n` questions; `biased` of them have the correct option uniquely longest."""
        quiz = []
        for i in range(n):
            if i < biased:
                options = [{"text": "short", "correct": False},
                          {"text": "the correct and much longer answer", "correct": True}]
            else:
                options = [{"text": "same len a", "correct": False},
                          {"text": "same len b", "correct": True}]
            quiz.append((where, {"question": f"q{i}", "options": options}))
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

    def test_the_error_names_the_section_and_the_index_within_it(self):
        quiz = self._quiz(3, biased=3, where="chapter-2") + self._quiz(3, 0, where="chapter-3")
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()) as err:
                R.check_length_bias(quiz)
        self.assertIn("chapter-2 #1, chapter-2 #2, chapter-2 #3", err.getvalue())


class TestRenderDiagramsInHtml(unittest.TestCase):
    """render_diagram (the part that shells out to `d2`) is mocked here so these test the
    token-substitution/wrapper-collapsing logic in isolation — see TestD2Diagrams for the
    real thing."""

    def setUp(self):
        self._orig = R.render_diagram
        R.render_diagram = lambda d, name="diagram": f"<svg>{d['nodes'][0]['id']}</svg>"
        # Module-level cache, so it has to be cleared between tests or one test's stubbed
        # output leaks into the next.
        R._DIAGRAM_CACHE.clear()

    def tearDown(self):
        R.render_diagram = self._orig
        R._DIAGRAM_CACHE.clear()

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


class TestDiagramRouting(unittest.TestCase):
    """There is one engine now, and a spec without a "kind" is an error rather than a
    silent fallback to something that draws a different-looking picture."""

    def setUp(self):
        self.calls = []
        self._d2 = R.render_d2_diagram
        R.render_d2_diagram = lambda d, name: self.calls.append(("d2", name)) or "<svg/>"
        R._DIAGRAM_CACHE.clear()

    def tearDown(self):
        R.render_d2_diagram = self._d2
        R._DIAGRAM_CACHE.clear()

    def test_a_kind_goes_to_d2(self):
        R.render_diagram({"kind": "state", "states": [], "transitions": []}, name="s")
        self.assertEqual(self.calls, [("d2", "s")])

    def test_a_spec_without_a_kind_is_rejected_with_a_pointer_to_the_reference(self):
        with self.assertRaises(KeyError) as caught:
            R.render_diagram({"nodes": [{"id": "a"}], "edges": []}, name="legacy")
        message = str(caught.exception)
        self.assertIn("Graphviz path has been removed", message)
        self.assertIn("REFERENCE.md", message)

    def test_the_diagram_key_is_passed_through_as_the_name(self):
        """It namespaces the SVG's ids, so two diagrams on one page cannot collide."""
        R.render_diagrams_in_html("{{diagram:handshake}}",
                                  {"handshake": {"kind": "state", "states": [],
                                                 "transitions": []}})
        self.assertEqual(self.calls, [("d2", "handshake")])

    def test_a_diagram_referenced_twice_is_rendered_once(self):
        """The placement pass costs seconds per callout, so this matters."""
        spec = {"one": {"kind": "state", "states": [], "transitions": []}}
        R.render_diagrams_in_html("{{diagram:one}} then {{diagram:one}}", spec)
        self.assertEqual(len(self.calls), 1)


@unittest.skipUnless(shutil.which("d2"), "d2 is not installed (brew install d2)")
class TestD2Diagrams(unittest.TestCase):
    """The real D2 path, end to end. Placement is skipped — it needs a browser and seconds —
    and lib/diagram's own tests cover it."""

    SEQUENCE = {
        "kind": "sequence",
        "participants": [{"id": "editor", "label": "Editor", "role": "client"},
                         {"id": "gw", "label": "Gateway", "role": "svc",
                          "note": "new service"}],
        "messages": [{"from": "editor", "to": "gw", "label": "WS upgrade"}],
    }

    def setUp(self):
        R._DIAGRAM_GATE_PROBLEMS.clear()
        R._DIAGRAM_ADVICE.clear()
        R._DIAGRAM_CACHE.clear()
        self._draw = R._diagram_figure.draw
        # Placement is what costs seconds; everything else about the real path is cheap.
        R._diagram_figure.draw = lambda specs, **kw: self._draw(
            specs, **{**kw, "place_callouts": False})

    def tearDown(self):
        R._diagram_figure.draw = self._draw
        R._DIAGRAM_GATE_PROBLEMS.clear()
        R._DIAGRAM_ADVICE.clear()
        R._DIAGRAM_CACHE.clear()

    def test_it_renders_a_kind_dot_could_not_draw_at_all(self):
        """`dot` reorders lifeline columns to minimise edge crossings; that is why D2 won."""
        svg = R.render_diagram(self.SEQUENCE, name="seq")
        self.assertTrue(svg.startswith("<svg"))
        self.assertIn("Gateway", svg)

    def test_colours_are_css_vars_so_the_diagram_follows_the_page_toggle(self):
        self.assertIn("var(--d-", R.render_diagram(self.SEQUENCE, name="seq"))

    def test_ids_are_namespaced_with_the_diagram_key(self):
        self.assertIn('id="seq-', R.render_diagram(self.SEQUENCE, name="seq"))

    def test_a_note_becomes_a_tagged_callout(self):
        self.assertIn("d2-callout", R.render_diagram(self.SEQUENCE, name="seq"))

    def test_a_clean_diagram_reports_nothing(self):
        plain = {"kind": "state",
                 "states": [{"id": "live", "role": "steady"},
                            {"id": "closed", "role": "terminal"}],
                 "transitions": [{"from": "live", "to": "closed", "label": "user leaves"}]}
        R.render_diagram(plain, name="plain")
        self.assertEqual(R._DIAGRAM_GATE_PROBLEMS, [])

    def test_an_oversized_diagram_is_reported_but_still_renders(self):
        tall = {"kind": "state",
                "states": [{"id": f"s{i}", "label": f"state number {i}", "role": "working"}
                           for i in range(14)],
                "transitions": [{"from": f"s{i}", "to": f"s{i+1}"} for i in range(13)]}
        svg = R.render_diagram(tall, name="tall")
        self.assertTrue(svg.startswith("<svg"))
        self.assertTrue(any("tall" in p.lower() for p in R._DIAGRAM_GATE_PROBLEMS),
                        R._DIAGRAM_GATE_PROBLEMS)

    def test_a_bad_spec_raises_rather_than_drawing_a_stray_box(self):
        from lib.diagram.spec import SpecError
        with self.assertRaises(SpecError):
            R.render_diagram({"kind": "state", "states": [{"id": "a", "role": "working"}],
                              "transitions": [{"from": "a", "to": "typo"}]}, name="x")


class TestPrepareDiagrams(unittest.TestCase):
    """What this file is responsible for once drawing moved behind `lib.diagram.figure.draw`:
    handing it the right set, once, and putting what comes back under the right heading.

    Nothing here knows what a gate is, which is the point — that used to be four gate imports
    and a hand-written list of which ones apply. See lib/diagram/figure.py.
    """

    DIAGRAMS = {"flow": {"kind": "state", "states": [], "transitions": []},
                "tables": {"kind": "state", "states": [], "transitions": []},
                "unused": {"kind": "state", "states": [], "transitions": []}}

    def setUp(self):
        self.calls = []
        self._draw = R._diagram_figure.draw
        R._diagram_figure.draw = self.spy
        R._DIAGRAM_GATE_PROBLEMS.clear()
        R._DIAGRAM_ADVICE.clear()
        R._DIAGRAM_CACHE.clear()

    def tearDown(self):
        R._diagram_figure.draw = self._draw
        R._DIAGRAM_GATE_PROBLEMS.clear()
        R._DIAGRAM_ADVICE.clear()
        R._DIAGRAM_CACHE.clear()

    def spy(self, specs, **kw):
        self.calls.append((sorted(specs), kw))
        return [_Figure(name, f"<svg id='{name}'/>", [], [], self.problems.get(name, []),
                        self.advice.get(name, []), self.blocked.get(name, []))
                for name in specs]

    problems: dict = {}
    advice: dict = {}
    blocked: dict = {}

    def _spec(self, *html):
        return {"title": "t", "diagrams": self.DIAGRAMS,
                "sections": [{"id": f"s{i}", "heading": "H", "html": h}
                             for i, h in enumerate(html)]}

    def test_every_referenced_diagram_is_drawn_in_one_call(self):
        """Across sections, not per section: the browser work batches over a document, and it
        can only do that if it is handed the whole set at once."""
        R.prepare_diagrams(self._spec("<p>{{diagram:flow}}</p>", "<p>{{diagram:tables}}</p>"))
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0][0], ["flow", "tables"])

    def test_a_diagram_the_document_never_references_is_not_drawn(self):
        """A spec may carry one it ended up not using, and placing a callout costs seconds."""
        R.prepare_diagrams(self._spec("<p>{{diagram:flow}}</p>"))
        self.assertEqual(self.calls[0][0], ["flow"])

    def test_it_asks_for_the_embedded_target(self):
        """The one thing this file states, because it is the one thing it knows: the figure is
        going into a page that ships the CSS and follows its own toggle."""
        R.prepare_diagrams(self._spec("{{diagram:flow}}"))
        self.assertEqual(self.calls[0][1].get("target"), "embed")

    def test_a_document_with_no_diagrams_draws_nothing(self):
        R.prepare_diagrams({"title": "t", "sections": [{"id": "a", "heading": "H",
                                                        "html": "<p>no figures</p>"}]})
        self.assertEqual(self.calls, [])

    def test_problems_and_blocked_gates_go_to_the_same_channel(self):
        """Both mean "something is wrong with a figure in this page"; a reader of stderr does
        not care that one is a verdict and the other is a gate that could not reach one."""
        self.problems = {"flow": ["flow: HIDDEN TEXT 88px²"]}
        self.blocked = {"flow": ["flow: clipping could not run"]}
        try:
            R.prepare_diagrams(self._spec("{{diagram:flow}}"))
            self.assertEqual(sorted(R._DIAGRAM_GATE_PROBLEMS),
                             ["flow: HIDDEN TEXT 88px²", "flow: clipping could not run"])
            self.assertEqual(R._DIAGRAM_ADVICE, [])
        finally:
            self.problems = self.blocked = {}

    def test_advice_goes_to_its_own_channel(self):
        self.advice = {"flow": ["flow: no state is marked `start: true`"]}
        try:
            R.prepare_diagrams(self._spec("{{diagram:flow}}"))
            self.assertEqual(R._DIAGRAM_ADVICE, ["flow: no state is marked `start: true`"])
            self.assertEqual(R._DIAGRAM_GATE_PROBLEMS, [])
        finally:
            self.advice = {}

    def test_what_it_drew_is_what_the_token_expands_to(self):
        spec = self._spec("<p>{{diagram:flow}}</p>")
        R.prepare_diagrams(spec)
        out = R.render_diagrams_in_html(spec["sections"][0]["html"], self.DIAGRAMS)
        self.assertIn("<svg id='flow'/>", out)
        self.assertEqual(len(self.calls), 1, "expanding a token must not draw it again")


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
