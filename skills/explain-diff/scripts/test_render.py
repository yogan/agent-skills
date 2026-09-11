#!/usr/bin/env python3
"""Tests for explain-diff's render.py.

Run: `python3 skills/explain-diff/scripts/test_render.py` (stdlib only; the TestD2Diagrams
class additionally needs `d2` on PATH and is skipped automatically when it isn't).

Regression tests are grounded in real bugs from this file's git history (see each
docstring for the commit); everything else covers the main documented behavior of the
pure formatting functions and the top-level render() assembly.
"""
import ast
import contextlib
import io
import os
import shutil
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import render as R  # noqa: E402
from lib.diagram import browser as _browser  # noqa: E402
from lib.diagram.figure import Figure as _Figure  # noqa: E402
from lib.diagram.gates import size as _size  # noqa: E402


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
        out = R.format_commit_byline({"hash": "a1b2c3d4", "subject": "fix: drop the legacy auth adapter",
                                      "url": "https://example.com/commit/a1b2c3d4"})
        self.assertIn('<a href="https://example.com/commit/a1b2c3d4"', out)
        self.assertIn(">fix: drop the legacy auth adapter</a>", out)
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

    def test_an_already_encoded_entity_is_not_encoded_again(self):
        self.assertEqual(R.format_inline("a &amp; b"), "a &amp; b")

    def test_both_spellings_of_one_character_converge(self):
        self.assertEqual(R.format_inline("a & b"), R.format_inline("a &amp; b"))

    def test_without_code_spans_the_backticks_are_dropped_not_shown(self):
        """For a destination that cannot hold an element (the <title> element), backticks are
        markup the reader was never meant to see."""
        self.assertEqual(R.format_inline("the `--retry` flag", code_spans=False), "the --retry flag")


class TestTheFieldContract(unittest.TestCase):
    """FIELD_CONTRACT answers "is this field prose or markup?" once, for every field. It is
    only worth having if it cannot fall behind the renderer, so this reads every spec field
    render.py actually touches straight out of its syntax tree and holds the two sets equal.
    Adding a spec field then fails here until it has been classified — which is the whole
    point: the bug this table exists to stop was an undocumented treatment being guessed."""

    # Every string-keyed read in render.py is a spec field today, with no exceptions. Should
    # one appear — a module-level dict of this file's own, subscripted by a literal — name it
    # here rather than weakening the check.
    KEYS_THAT_ARE_NOT_SPEC_FIELDS = frozenset()

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(HERE, "render.py"), encoding="utf-8") as f:
            source = f.read()
        keys = set()
        for node in ast.walk(ast.parse(source)):
            # `spec["title"]`, `s["heading"]`, …
            if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) \
                    and isinstance(node.slice.value, str):
                keys.add(node.slice.value)
            # `spec.get("subtitle")`, …
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                    and node.func.attr == "get" and node.args \
                    and isinstance(node.args[0], ast.Constant) \
                    and isinstance(node.args[0].value, str):
                keys.add(node.args[0].value)
            # `"kind" not in diagram` — a field can be read by testing for it, and one read
            # only that way would otherwise slip past unclassified.
            elif isinstance(node, ast.Compare) and isinstance(node.left, ast.Constant) \
                    and isinstance(node.left.value, str) \
                    and any(isinstance(op, (ast.In, ast.NotIn)) for op in node.ops):
                keys.add(node.left.value)
        cls.read = keys - cls.KEYS_THAT_ARE_NOT_SPEC_FIELDS

    def test_every_field_the_renderer_reads_is_classified(self):
        self.assertEqual(self.read - set(R.FIELD_CONTRACT), set())

    def test_every_classified_field_is_one_the_renderer_reads(self):
        """A stale entry is as misleading as a missing one — it documents a field that no
        longer exists as though a spec could still carry it."""
        self.assertEqual(set(R.FIELD_CONTRACT) - self.read, set())


class TestWalkSpecFields(unittest.TestCase):
    def test_it_finds_a_field_wherever_it_hangs(self):
        """A quiz question reads the same on the document as on a chapter, so the walk is
        structural rather than a list of the places one is known to appear."""
        spec = {"quiz": [{"question": "top"}],
                "sections": [{"id": "c1", "quiz": [{"question": "chapter"}]}]}
        questions = [f.value for f in R.walk_spec_fields(spec) if f.name == "question"]
        self.assertEqual(sorted(questions), ["chapter", "top"])

    def test_a_field_is_reported_with_the_dict_it_came_from(self):
        spec = {"sections": [{"id": "c1", "heading": "Chapter 1"}]}
        heading = next(f for f in R.walk_spec_fields(spec) if f.name == "heading")
        self.assertEqual(heading.path, "spec.sections[0].heading")
        self.assertEqual(heading.parent["id"], "c1")

    def test_a_diagram_spec_is_not_walked_into(self):
        """Its field names are lib/diagram's vocabulary — a state's "label" is not one of
        this file's fields and must not be judged against this table."""
        spec = {"diagrams": {"flow": {"kind": "state",
                                      "states": [{"id": "a", "label": "<b>a</b>"}]}}}
        names = {f.name for f in R.walk_spec_fields(spec)}
        self.assertEqual(names, {"diagrams"})


class TestCheckPlainTextFields(unittest.TestCase):
    """The gate: a heading carrying markup is refused before anything renders. Its shape
    mirrors check_length_bias below — one explanatory message on stderr, then exit 1."""

    def _refusal(self, spec):
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()) as err:
                R.check_plain_text_fields(spec)
        return err.getvalue()

    def test_a_spec_written_to_the_contract_passes(self):
        R.check_plain_text_fields({
            "title": "Making `backoff` mandatory",
            "subtitle": "[MR !123](https://example.com/123) · commit `abcd123`",
            "sections": [{"id": "c1", "heading": "Chapter 1: The `backoff` helper",
                          "html": "<p>Real <code>markup</code> belongs here.</p>"}],
        })

    def test_a_tag_in_a_heading_is_refused_and_the_field_named(self):
        """The reported bug: a chapter heading authored with a <code> tag reached the
        page as angle brackets, in more than one chapter of the same document."""
        err = self._refusal({"sections": [
            {"id": "chapter-1", "heading": "Chapter 1: The <code>backoff</code> helper"}]})
        self.assertIn("spec.sections[0].heading", err)
        self.assertIn('id "chapter-1"', err)

    def test_the_refusal_shows_the_backtick_form_to_write_instead(self):
        err = self._refusal({"title": "Declaring <code>backoff</code> on every retry"})
        self.assertIn("Declaring `backoff` on every retry", err)

    def test_the_double_encoded_spelling_is_caught_too(self):
        """A heading is judged on its unescaped text, so "&lt;code&gt;" is the same defect as
        "<code>" — both reach the reader as angle brackets."""
        err = self._refusal({"sections": [
            {"id": "c1", "heading": "The &lt;code&gt;backoff&lt;/code&gt; helper"}]})
        self.assertIn("The `backoff` helper", err)

    def test_a_tag_it_cannot_rewrite_is_reported_without_a_suggestion(self):
        """Backticks replace <code> exactly and nothing else, and inventing an equivalent for
        a <b> would teach the author that markup in a heading is negotiable."""
        err = self._refusal({"sections": [{"id": "c1", "heading": "A <b>bold</b> claim"}]})
        self.assertIn("A <b>bold</b> claim", err)
        self.assertNotIn("→", err)

    def test_every_offender_is_reported_in_one_pass(self):
        """The author is an agent regenerating the whole spec: naming all of them costs one
        retry, naming the first costs a retry each."""
        err = self._refusal({"title": "The <code>backoff</code> helpers",
                             "sections": [{"id": "c1", "heading": "Chapter 1: <code>backoff</code>"},
                                          {"id": "c4", "heading": "Chapter 4: <code>backoff</code>"}]})
        self.assertIn("3 field(s)", err)
        self.assertIn('id "c1"', err)
        self.assertIn('id "c4"', err)

    def test_a_tag_in_quiz_text_is_allowed(self):
        """PROSE is permissive on purpose: a question about HTML escaping quotes a tag, and
        escaping it to visible text is exactly what the author wanted."""
        R.check_plain_text_fields({"quiz": [{
            "question": "What does <script> render as here?",
            "options": [{"text": "<b>text</b>", "correct": True}]}]})

    def test_a_tag_in_a_diagram_label_is_not_this_gate_s_business(self):
        R.check_plain_text_fields({"diagrams": {"flow": {
            "kind": "state", "states": [{"id": "a", "label": "<b>a</b>"}]}}})

    def test_markup_in_a_section_s_html_is_the_whole_point_of_that_field(self):
        R.check_plain_text_fields({"sections": [
            {"id": "c1", "heading": "Chapter 1", "html": "<p>A <code>tag</code> here.</p>"}]})


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


@unittest.skipUnless(_browser.available(), "no browser to measure the page in")
class TestThePageIsAsWideAsTheGateBelieves(unittest.TestCase):
    """The one test that would have caught the 55px error, and the reason it lives here.

    `gates/size.AVAIL_W` is how much drawing room a diagram gets on THIS page, and for a long
    time it was a number someone had worked out on paper: 777px, derived by subtracting the body's
    own padding, which `max-width` on a content-box element never included, and forgetting the
    card's border. Nothing failed, because being pessimistic about width only ever made the
    renderer wrap and shrink harder than it had to — six edge labels across both corpora were
    folded to survive a column that was never that narrow.

    Arithmetic cannot check arithmetic. So this renders a real explainer document, puts a block
    element where the drawing goes, and asks Chrome how wide it came out. Change the body width,
    the card padding, the border or the root font size and this fails with the number to use.
    """

    def test_the_card_gives_a_drawing_exactly_avail_w(self):
        doc = R.render({"title": "Geometry", "sections": [
            {"id": "a", "heading": "H",
             # A block child fills its parent's CONTENT box, which is the drawing room. Not an
             # <svg>: that would be sized by the very rules under test.
             "html": '<div class="diagram diagram-embed"><div data-w></div></div>'}]})
        measured, = _browser.text_widths(doc)
        self.assertAlmostEqual(
            measured, _size.AVAIL_W, places=1,
            msg=f"the real page gives a drawing {measured}px, gates/size.AVAIL_W says "
                f"{_size.AVAIL_W}px — the page design moved, so re-derive it and re-capture "
                "both corpora")


class TestBothDiagramContainers(unittest.TestCase):
    """This page renders a diagram in two places — the inline card and the click-to-enlarge
    overlay — and `lib/diagram`'s rules are scoped per container. The overlay went without them
    for as long as it existed, so an enlarged figure drew every callout as an empty box: exactly
    the clip those rules document, in the one view a reader opens to read a callout properly."""

    def test_the_lightbox_gets_the_callout_rules_too(self):
        self.assertIn(".diagram-lightbox-content foreignObject", R.CSS)
        self.assertIn(".diagram-lightbox-content .d2-callout", R.CSS)

    def test_the_lightbox_does_not_get_the_fitting_rule(self):
        """`max-width:100%` there caps the enlargement back to the inline width, so the overlay
        must keep sizing the <svg> itself."""
        overlay = [line for line in R.CSS.splitlines()
                   if ".diagram-lightbox-content" in line and "max-width" in line]
        self.assertEqual(overlay, [])

    def test_the_card_still_gets_the_fitting_rule(self):
        self.assertIn(".diagram svg{max-width:100%", R.CSS)

    def test_both_containers_follow_the_dark_toggle_and_the_system_preference(self):
        """Emitted under both selectors for each container: a first-time reader on a dark machine
        never touched the toggle, so a rule bound to `[data-theme=dark]` alone misses them."""
        for selector in ('[data-theme="dark"]', ':root:not([data-theme="light"])'):
            for scope in (".diagram", ".diagram-lightbox-content"):
                self.assertIn(f"{selector} {scope} .d2-callout", R.CSS)


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

    def test_pre_encoded_heading_is_not_escaped_twice(self):
        """The reported bug: a heading written as "&amp;" reached the page as "&amp;amp;",
        in the <h2> and in the table-of-contents link alike."""
        spec = self._spec(
            sections=[{"id": "intro", "heading": "Background &amp; intuition", "html": "<p>x</p>"}]
        )
        out = R.render(spec)
        self.assertIn('<h2 id="intro">Background &amp; intuition</h2>', out)
        self.assertIn('<a href="#intro">Background &amp; intuition</a>', out)
        self.assertNotIn("&amp;amp;", out)

    def test_plain_ampersand_in_heading_is_escaped_once(self):
        """The spelling the contract asks for has to give the same page as the one above."""
        spec = self._spec(
            sections=[{"id": "intro", "heading": "Background & intuition", "html": "<p>x</p>"}]
        )
        out = R.render(spec)
        self.assertIn('<h2 id="intro">Background &amp; intuition</h2>', out)
        self.assertNotIn("&amp;amp;", out)

    def test_title_is_not_escaped_twice(self):
        out = R.render(self._spec(title="Retry &amp; backoff"))
        self.assertIn("<title>Retry &amp; backoff</title>", out)
        self.assertIn("<h1>Retry &amp; backoff</h1>", out)
        self.assertNotIn("&amp;amp;", out)

    def test_backticks_in_a_heading_become_code(self):
        """The other half of the reported bug: the author wanted an identifier set apart in a
        chapter heading and wrote a <code> tag, because backticks did nothing here. They do
        now, which is also what explain-branch's documented chapter-heading example writes."""
        spec = self._spec(sections=[{"id": "chapter-1", "html": "<p>x</p>",
                                     "heading": "Chapter 1: The `backoff` helper"}])
        out = R.render(spec)
        self.assertIn('<h2 id="chapter-1">Chapter 1: The <code>backoff</code> helper</h2>', out)
        self.assertIn("Chapter 1: The <code>backoff</code> helper</a>", out)

    def test_the_browser_tab_gets_the_title_without_the_element(self):
        """<title>'s content is read as text by the HTML parser, so an element in it would
        show up as angle brackets on the tab — the same prose goes there with its backticks
        dropped, while the <h1> on the page gets the real <code>."""
        out = R.render(self._spec(title="Making `backoff` mandatory"))
        self.assertIn("<title>Making backoff mandatory</title>", out)
        self.assertIn("<h1>Making <code>backoff</code> mandatory</h1>", out)

    def test_a_heading_that_got_past_the_gate_is_still_inert(self):
        """check_plain_text_fields refuses this spec, but render() is reachable on its own
        (and is what the tests here call), so unescaping must not turn it into a hole: a real
        tag in a heading stays text either way."""
        spec = self._spec(
            sections=[{"id": "intro", "heading": "<script>alert(1)</script>", "html": "<p>x</p>"}]
        )
        out = R.render(spec)
        self.assertNotIn("<script>alert(1)</script>", out)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", out)

    def test_a_script_tag_in_quiz_text_is_inert(self):
        """Quiz text is deliberately permissive about markup — a question may quote HTML —
        so the escaping is the only thing keeping it from executing."""
        out = R.render(self._spec(quiz=[{
            "question": "What does <script>alert(1)</script> render as?",
            "options": [{"text": "<b>text</b>", "correct": True},
                        {"text": "nothing at all", "correct": False}]}]))
        self.assertNotIn("<script>alert(1)</script>", out)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", out)
        self.assertIn("&lt;b&gt;text&lt;/b&gt;", out)

    def test_a_section_id_is_escaped_into_its_attribute(self):
        """It lands in an id="" and an href="#", so a quote in it would end the attribute."""
        out = R.render(self._spec(
            sections=[{"id": 'a"b', "heading": "Background", "html": "<p>x</p>"}]))
        self.assertIn('<h2 id="a&quot;b">', out)
        self.assertIn('<a href="#a&quot;b">', out)

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
            "commit": {"hash": "a1b2c3d4", "subject": "fix: drop the legacy auth adapter"},
        }]))
        self.assertIn("commit <code>a1b2c3d4</code>", out)

    def test_quiz_free_spec_omits_quiz_section(self):
        out = R.render(self._spec(quiz=[]))
        self.assertNotIn('<h2 id="quiz">', out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
