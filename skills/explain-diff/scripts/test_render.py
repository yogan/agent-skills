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
    """The real D2 path, end to end. Placement is skipped (it needs a browser and seconds);
    lib/diagram's own tests cover it."""

    SEQUENCE = {
        "kind": "sequence",
        "participants": [{"id": "editor", "label": "Editor", "role": "client"},
                         {"id": "gw", "label": "Gateway", "role": "svc",
                          "note": "new service", "near": "bottom-right"}],
        "messages": [{"from": "editor", "to": "gw", "label": "WS upgrade"}],
    }

    def setUp(self):
        R._DIAGRAM_GATE_PROBLEMS.clear()
        R._DIAGRAM_CACHE.clear()
        self._avail = R._diagram_browser.available
        R._diagram_browser.available = lambda: False

    def tearDown(self):
        R._diagram_browser.available = self._avail
        R._DIAGRAM_GATE_PROBLEMS.clear()
        R._DIAGRAM_CACHE.clear()

    def test_it_renders_a_kind_dot_could_not_draw_at_all(self):
        """`dot` reorders lifeline columns to minimise edge crossings; that is why D2 won."""
        svg = R.render_diagram(self.SEQUENCE, name="seq")
        self.assertTrue(svg.startswith("<svg"))
        self.assertIn("Gateway", svg)

    def test_colours_are_css_vars_so_the_diagram_follows_the_page_toggle(self):
        svg = R.render_diagram(self.SEQUENCE, name="seq")
        self.assertIn("var(--d-", svg)

    def test_ids_are_namespaced_with_the_diagram_key(self):
        svg = R.render_diagram(self.SEQUENCE, name="seq")
        self.assertIn('id="seq-', svg)

    def test_a_note_becomes_a_tagged_callout(self):
        svg = R.render_diagram(self.SEQUENCE, name="seq")
        self.assertIn("d2-callout", svg)

    def test_gates_run_and_a_clean_diagram_reports_nothing(self):
        """No note, so nothing needs placing and nothing should be reported."""
        plain = {"kind": "state",
                 "states": [{"id": "live", "role": "steady"},
                            {"id": "closed", "role": "terminal"}],
                 "transitions": [{"from": "live", "to": "closed", "label": "user leaves"}]}
        R.render_diagram(plain, name="plain")
        self.assertEqual(R._DIAGRAM_GATE_PROBLEMS, [])

    def test_an_unmeasured_callout_without_a_browser_is_reported(self):
        """D2 reserves no canvas room for a callout, so an unmeasured anchor may well be
        clipped. Staying quiet about that would be reporting a pass we did not verify."""
        R.render_diagram(self.SEQUENCE, name="seq")
        self.assertTrue(any("no browser" in p for p in R._DIAGRAM_GATE_PROBLEMS),
                        R._DIAGRAM_GATE_PROBLEMS)

    def test_a_diagram_with_no_notes_needs_no_browser_and_says_nothing_about_one(self):
        plain = {"kind": "state",
                 "states": [{"id": "a", "role": "working"}, {"id": "b", "role": "working"}],
                 "transitions": [{"from": "a", "to": "b"}]}
        R.render_diagram(plain, name="plain")
        self.assertFalse(any("browser" in p for p in R._DIAGRAM_GATE_PROBLEMS))

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


class TestDiagramAdvice(unittest.TestCase):
    """`lib.diagram.spec.content_warnings` is how the author finds out that a spec is
    editorially off — a bare ER ratio, eight sequence steps, an ASCII arrow in a label. It
    used to be collected by the visualize CLI only, so an explainer's diagrams were never
    advised at all."""

    def setUp(self):
        R._DIAGRAM_ADVICE.clear()
        R._DIAGRAM_GATE_PROBLEMS.clear()
        R._DIAGRAM_CACHE.clear()
        self._render = R._diagram_render.render
        self._avail = R._diagram_browser.available
        # The advice is computed from the spec, so neither d2 nor a browser is involved.
        R._diagram_render.render = lambda spec, name="diagram", **kw: "<svg/>"
        R._diagram_browser.available = lambda: False

    def tearDown(self):
        R._diagram_render.render = self._render
        R._diagram_browser.available = self._avail
        R._DIAGRAM_ADVICE.clear()
        R._DIAGRAM_GATE_PROBLEMS.clear()
        R._DIAGRAM_CACHE.clear()

    def test_a_bare_er_ratio_is_advised_against(self):
        R.render_diagram({
            "kind": "er",
            "tables": [{"id": "users", "role": "store",
                        "columns": [{"name": "id", "type": "uuid", "key": "pk"}]},
                       {"id": "docs", "role": "store",
                        "columns": [{"name": "owner_id", "type": "uuid", "key": "fk"}]}],
            "edges": [{"from": "docs.owner_id", "to": "users.id", "label": "n : 1"}],
        }, name="schema")
        self.assertTrue(any("name the entities" in a for a in R._DIAGRAM_ADVICE),
                        R._DIAGRAM_ADVICE)
        self.assertTrue(all(a.startswith("schema: ") for a in R._DIAGRAM_ADVICE),
                        R._DIAGRAM_ADVICE)

    def test_advice_is_kept_apart_from_the_gate_problems(self):
        """One list would make "wide" read as loudly as "clipped"."""
        R.render_diagram({
            "kind": "state",
            "states": [{"id": f"s{i}", "role": "working"} for i in range(8)],
            "transitions": [{"from": f"s{i}", "to": f"s{i + 1}", "label": "next"}
                            for i in range(7)],
        }, name="lifecycle")
        self.assertTrue(any("8 states" in a for a in R._DIAGRAM_ADVICE), R._DIAGRAM_ADVICE)
        self.assertFalse(any("8 states" in p for p in R._DIAGRAM_GATE_PROBLEMS),
                         R._DIAGRAM_GATE_PROBLEMS)

    def test_a_clean_spec_says_nothing(self):
        R.render_diagram({
            "kind": "state",
            "states": [{"id": "live", "role": "steady"}, {"id": "closed", "role": "terminal"}],
            "transitions": [{"from": "live", "to": "closed", "label": "user leaves"}],
        }, name="plain")
        self.assertEqual(R._DIAGRAM_ADVICE, [])

    def test_a_malformed_spec_raises_a_spec_error_not_a_key_error(self):
        """content_warnings() indexes fields it has not checked, so validating first is what
        makes the message say which field is wrong."""
        from lib.diagram.spec import SpecError
        with self.assertRaises(SpecError):
            R.render_diagram({"kind": "state", "states": [{"role": "working"}],
                              "transitions": []}, name="nameless")


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
