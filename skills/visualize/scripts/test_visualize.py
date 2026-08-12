#!/usr/bin/env python3
"""Tests for visualize.py, the CLI over lib/diagram.

The library has its own tests; these cover what only the CLI decides — that a standalone
run OPENS what it made, that an SVG run does not (a bare SVG is a broken preview, not a
worse one), that a gate failure is loud without swallowing the output, and that a missing
browser is reported as a gate that could not run rather than as a pass.

The subprocess tests need d2; they skip visibly without it.

Run: `python3 skills/visualize/scripts/test_visualize.py`
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "visualize.py")
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, HERE)
sys.path.insert(0, _REPO_ROOT)

import visualize as V  # noqa: E402

from lib.diagram import render  # noqa: E402
from lib.diagram.examples import STATE  # noqa: E402

HAVE_D2 = render.d2_version() is not None


def read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def run(*args, spec=None):
    """Run the CLI in a subprocess; returns (returncode, stdout, stderr)."""
    with tempfile.TemporaryDirectory() as tmp:
        argv = [sys.executable, SCRIPT]
        if spec is not None:
            path = os.path.join(tmp, "spec.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(spec, handle)
            argv.append(path)
        argv += list(args)
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=300)
        return proc.returncode, proc.stdout, proc.stderr


class TestSlugify(unittest.TestCase):
    def test_it_lowercases_and_hyphenates(self):
        self.assertEqual(V.slugify("Presence Gateway In Context"),
                         "presence-gateway-in-context")

    def test_punctuation_collapses(self):
        self.assertEqual(V.slugify("users → documents (1:n)"), "users-documents-1-n")

    def test_it_is_capped(self):
        """The slug lands in a filename AND as a prefix on every id in the SVG."""
        slug = V.slugify("x" * 200)
        self.assertLessEqual(len(slug), V.SLUG_MAX)

    def test_a_cap_never_leaves_a_trailing_hyphen(self):
        self.assertFalse(V.slugify("a b " * 40).endswith("-"))

    def test_an_unusable_title_still_yields_a_name(self):
        self.assertEqual(V.slugify("!!!"), "diagram")
        self.assertEqual(V.slugify(""), "diagram")


class TestCssMode(unittest.TestCase):
    """`--format css` is a question about the library, answerable with no spec at all."""

    def test_it_prints_the_host_css_without_a_spec(self):
        code, out, err = run("--format", "css")
        self.assertEqual(code, 0, err)
        self.assertIn("--d-callout-bg", out)
        self.assertIn(".d2-callout", out)

    def test_it_includes_the_paragraph_reset_the_callout_needs(self):
        _, out, _ = run("--format", "css")
        self.assertIn("margin:0", out)

    def test_a_missing_spec_is_an_error_for_every_other_format(self):
        code, _, err = run("--format", "embed")
        self.assertNotEqual(code, 0)
        self.assertIn("spec is required", err)


class TestSpecErrors(unittest.TestCase):
    def test_invalid_json_is_reported_clearly(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            handle.write("{not json")
            path = handle.name
        try:
            code, _, err = run(path)
        finally:
            os.unlink(path)
        self.assertNotEqual(code, 0)
        self.assertIn("not valid JSON", err)

    def test_a_missing_file_is_reported(self):
        code, _, err = run("/nonexistent/spec.json")
        self.assertNotEqual(code, 0)
        self.assertIn("could not read", err)

    def test_a_dangling_edge_is_rejected_before_any_rendering(self):
        bad = {"kind": "state", "states": [{"id": "a", "role": "working"}],
               "transitions": [{"from": "a", "to": "typo"}]}
        code, _, err = run("--no-open", "--no-place", spec=bad)
        self.assertNotEqual(code, 0)
        self.assertIn("typo", err)

    def test_a_content_limit_produces_a_warning_not_a_failure(self):
        wordy = json.loads(json.dumps(STATE))
        wordy["states"][3]["note"] = "this note is far too many words to fit in a callout"
        code, _, err = run("--no-open", "--no-place", spec=wordy)
        self.assertIn("warning:", err)
        self.assertIn("note is", err)


@unittest.skipUnless(HAVE_D2, "d2 is not installed (brew install d2)")
class TestStandaloneImage(unittest.TestCase):
    """The default output: a file that works on its own, with no page cooperating."""

    def test_it_writes_and_reports_an_svg(self):
        code, out, err = run("--no-open", "--no-place", spec=STATE)
        path = out.strip()
        self.assertEqual(code, 0, err)
        self.assertTrue(path.endswith(".svg"), path)
        self.assertTrue(os.path.exists(path))

    def test_the_colours_are_baked_not_var_references(self):
        """An undefined custom property makes the browser drop the attribute entirely, so a
        var()-based SVG opened directly renders as unpainted shapes."""
        _, out, _ = run("--no-open", "--no-place", spec=STATE)
        self.assertNotIn("var(--d-", read(out.strip()))

    def canvas_fill(self, svg):
        """The root background rect's fill — the first rect d2 emits, covering the canvas."""
        import re
        first = re.search(r"<rect[^>]*>", svg).group(0)
        return re.search(r'fill="([^"]*)"', first).group(1)

    def test_the_canvas_is_painted(self):
        """Transparent would composite the drawing onto whatever the viewer uses, leaving the
        muted edge labels without their contrast."""
        _, out, _ = run("--no-open", "--no-place", spec=STATE)
        self.assertIn(self.canvas_fill(read(out.strip())), ("#ffffff", "#16181d"))

    def test_the_light_canvas_is_pure_white_not_the_page_colour(self):
        """#fafaf8 is the explainer PAGE's colour. A file is not on that page — on a browser's
        white it reads as a faintly grey slab, which is what prompted this."""
        _, out, _ = run("--no-open", "--no-place", spec=STATE)
        self.assertEqual(self.canvas_fill(read(out.strip())), "#ffffff")

    def test_dark_theme_bakes_the_other_way(self):
        _, out, _ = run("--no-open", "--no-place", "--theme", "dark", spec=STATE)
        svg = read(out.strip())
        self.assertEqual(self.canvas_fill(svg), "#16181d")
        self.assertNotIn("var(--d-", svg)

    def test_the_dark_canvas_differs_from_a_table_body(self):
        """#1f2229 is what a table body is filled with; the same value as the canvas would
        swallow every table on the drawing."""
        _, out, _ = run("--no-open", "--no-place", "--theme", "dark", spec=STATE)
        self.assertNotEqual(self.canvas_fill(read(out.strip())), "#1f2229")

    def test_the_callout_css_travels_inside_the_file(self):
        """Callout text is HTML in a foreignObject and d2 ships no paragraph reset for it."""
        _, out, _ = run("--no-open", "--no-place", spec=STATE)
        svg = read(out.strip())
        self.assertIn("<style>", svg)
        self.assertIn("margin:0", svg)

    def test_the_inline_style_is_cdata_wrapped(self):
        """A <style> in SVG is XML: a comment mentioning <svg> was enough to make the whole
        file a parse error that every browser reports as a tag mismatch."""
        _, out, _ = run("--no-open", "--no-place", spec=STATE)
        self.assertIn("<![CDATA[", read(out.strip()))

    def test_an_explicit_output_path_is_honoured(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "out.svg")
            code, out, err = run("--no-open", "--no-place", "-o", target, spec=STATE)
            self.assertEqual(code, 0, err)
            self.assertEqual(out.strip(), target)

    def test_the_gate_table_goes_to_stderr_so_stdout_is_just_the_path(self):
        """Callers read stdout for the path; the report must not pollute it."""
        _, out, err = run("--no-open", "--no-place", spec=STATE)
        self.assertEqual(len(out.strip().splitlines()), 1)
        self.assertIn("checks pass", err)

    def test_the_standalone_gates_skip_the_page_rules(self):
        """No page means no width or viewport to fit — and applying them anyway is what
        pushed an author into splitting a perfectly legible wide diagram."""
        wide = {"kind": "er", "tables": [
            {"id": f"t{i}", "role": "store",
             "columns": [{"name": "id", "type": "uuid", "key": "pk"}]} for i in range(9)]}
        code, _, err = run("--no-open", "--no-place", spec=wide)
        self.assertEqual(code, 0, err)
        self.assertNotIn("TALL", err)

    def test_theming_is_not_gated_on_a_baked_image(self):
        """There are no vars left to follow a toggle; render.standalone checks mappability
        itself, before baking."""
        _, _, err = run("--no-open", "--no-place", spec=STATE)
        self.assertNotIn("theming", err)

    def test_contrast_is_checked_for_the_baked_theme_only(self):
        """Checking both would measure dark colours against the light page background and
        report a confident failure about a combination that cannot occur."""
        _, _, err = run("--no-open", "--no-place", spec=STATE)
        row = next(line for line in err.splitlines() if "contrast" in line)
        self.assertIn("l ", row)
        self.assertNotIn("d ", row)

    def test_no_gates_skips_them(self):
        _, _, err = run("--no-open", "--no-place", "--no-gates", spec=STATE)
        self.assertNotIn("checks pass", err)


@unittest.skipUnless(HAVE_D2, "d2 is not installed (brew install d2)")
class TestEmbedMode(unittest.TestCase):
    """The other direction: a themeable SVG for a page that supplies the vars and CSS."""

    def test_it_writes_a_themeable_svg(self):
        code, out, err = run("--format", "embed", "--no-place", spec=STATE)
        self.assertEqual(code, 0, err)
        path = out.strip()
        self.assertTrue(path.endswith(".svg"))
        self.assertTrue(read(path).startswith("<svg"))

    def test_it_always_warns_that_the_host_page_must_supply_css(self):
        """The most likely confusion this tool can cause, and never the SVG's fault."""
        _, _, err = run("--format", "embed", "--no-place", spec=STATE)
        self.assertIn("--format css", err)

    def test_the_svg_is_themeable_rather_than_baked(self):
        _, out, _ = run("--format", "embed", "--no-place", spec=STATE)
        self.assertIn("var(--d-", read(out.strip()))


@unittest.skipUnless(HAVE_D2, "needs d2")
class TestOpening(unittest.TestCase):
    """Standalone means the user sees the diagram without having to run a second command.

    Calls main() in-process with open_file() substituted, which is the only way to observe
    whether it would have opened something without actually launching a browser.
    """

    def setUp(self):
        self.opened = []
        self.real_open = V.open_file
        self.real_argv = sys.argv
        self.real_stdin = sys.stdin
        V.open_file = self.opened.append

    def tearDown(self):
        V.open_file = self.real_open
        sys.argv = self.real_argv
        sys.stdin = self.real_stdin

    def call(self, target, *extra):
        """Run main() reading the spec from stdin, writing to `target`."""
        import io
        from contextlib import redirect_stdout
        sys.argv = ["visualize.py", "-", "--no-place", "--no-gates", "-o", target, *extra]
        sys.stdin = io.StringIO(json.dumps(STATE))
        with redirect_stdout(io.StringIO()):
            return V.main()

    def test_a_standalone_run_opens_what_it_made(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "out.svg")
            self.assertEqual(self.call(target), 0)
            self.assertEqual(self.opened, [target])

    def test_no_open_suppresses_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.call(os.path.join(tmp, "out.svg"), "--no-open")
            self.assertEqual(self.opened, [])

    def test_an_embed_run_never_opens(self):
        """An embed SVG on its own shows clipped labels and unpainted shapes — opening it
        would be showing the user a broken artefact and calling it a preview."""
        with tempfile.TemporaryDirectory() as tmp:
            self.call(os.path.join(tmp, "out.svg"), "--format", "embed")
            self.assertEqual(self.opened, [])

    def test_the_spec_can_come_from_stdin(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "out.svg")
            self.assertEqual(self.call(target), 0)
            self.assertIn("<svg", read(target))


class TestBlockedGatesAreNotPasses(unittest.TestCase):
    def setUp(self):
        self.real = V.browser.available
        self.real_reqs = V.browser.requirements

    def tearDown(self):
        V.browser.available = self.real
        V.browser.requirements = self.real_reqs

    @unittest.skipUnless(HAVE_D2, "needs d2")
    def test_a_missing_browser_is_reported_as_a_gate_that_could_not_run(self):
        V.browser.available = lambda: False
        V.browser.requirements = lambda: ["node is not on PATH"]
        svg = render.render(STATE, name="state")
        results, blocked = V.run_gates(svg, "state")
        self.assertTrue(all(r.ok for r in results))
        self.assertEqual(len(blocked), 1)
        self.assertIn("clipping", blocked[0])
        self.assertIn("not a clean bill of health", blocked[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
