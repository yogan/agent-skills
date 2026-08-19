#!/usr/bin/env python3
"""Unit tests for `measure_speed.py` — everything except actually measuring anything.

Nothing here starts a browser or compiles a diagram: the measuring is the part that cannot be
tested cheaply, and the part that goes wrong is the arithmetic around it — what counts as a
change, what counts as the same machine, and whether a report still builds when there is no
baseline to compare against.
"""
import contextlib
import io
import json
import re
import os
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import measure_speed as speed                                  # noqa: E402


def machine(**overrides):
    base = {"system": "Darwin", "arch": "arm64", "cores": 12, "python": "3.14.7",
            "node": "v22.0.0", "d2": "v0.6.0", "workers": 12}
    base.update(overrides)
    return base


def run(seconds, **extra):
    row = {"seconds": seconds, "compiles": 10, "launches": 2, "pages": 8}
    row.update(extra)
    return row


def record(**scenarios):
    return {"recorded": "2026-01-01", "machine": machine(),
            "scenarios": {key: run(value) if isinstance(value, (int, float)) else value
                          for key, value in scenarios.items()}}


class TestVerdict(unittest.TestCase):
    def test_a_large_drop_is_better(self):
        word, change = speed.verdict(50.0, 100.0)
        self.assertEqual(word, "better")
        self.assertAlmostEqual(change, -0.5)

    def test_a_large_rise_is_worse(self):
        self.assertEqual(speed.verdict(150.0, 100.0)[0], "worse")

    def test_movement_inside_the_noise_band_is_the_same(self):
        for now in (100 * (1 + speed.SAME_WITHIN / 2), 100 * (1 - speed.SAME_WITHIN / 2)):
            self.assertEqual(speed.verdict(now, 100.0)[0], "same")

    def test_the_band_is_exclusive_at_its_edge(self):
        """Exactly at the threshold counts as a change, so `same` never swallows one."""
        self.assertEqual(speed.verdict(100 * (1 + speed.SAME_WITHIN), 100.0)[0], "worse")

    def test_no_baseline_is_new_rather_than_better(self):
        self.assertEqual(speed.verdict(12.0, None)[0], "new")
        self.assertEqual(speed.verdict(12.0, 0)[0], "new")

    def test_a_missing_current_value_is_not_a_win(self):
        self.assertEqual(speed.verdict(None, 100.0)[0], "new")

    def test_higher_is_better_inverts_the_words(self):
        self.assertEqual(speed.verdict(50.0, 100.0, lower_is_better=False)[0], "worse")
        self.assertEqual(speed.verdict(150.0, 100.0, lower_is_better=False)[0], "better")


class TestSameMachine(unittest.TestCase):
    def test_identical_machines_do_not_drift(self):
        self.assertEqual(speed.same_machine(machine(), machine()), [])

    def test_a_different_core_count_drifts(self):
        self.assertEqual(speed.same_machine(machine(), machine(cores=8)), ["cores"])

    def test_every_deciding_field_is_checked(self):
        for field in speed.DECIDING:
            with self.subTest(field=field):
                other = machine(**{field: "changed"})
                self.assertIn(field, speed.same_machine(machine(), other))

    def test_a_python_patch_release_does_not_count(self):
        """The renderer's cost is d2, Chrome and cores — not the interpreter's patch level."""
        self.assertEqual(speed.same_machine(machine(), machine(python="3.14.9")), [])

    def test_a_missing_baseline_drifts_on_everything(self):
        self.assertEqual(speed.same_machine(machine(), None), list(speed.DECIDING))


class TestCoreUsage(unittest.TestCase):
    """Stated as the share of the machine USED, so high is unambiguously good."""

    def probe_with(self, spans):
        probe = speed.Probe()
        probe.spans = spans
        return probe

    def test_one_subprocess_on_four_cores_is_a_quarter(self):
        self.assertAlmostEqual(self.probe_with([(0, 10)]).core_usage(0, 10, cores=4), 0.25)

    def test_every_core_busy_throughout_is_full(self):
        probe = self.probe_with([(0, 10)] * 4)
        self.assertAlmostEqual(probe.core_usage(0, 10, cores=4), 1.0)

    def test_busy_for_half_the_window_is_half_of_that(self):
        self.assertAlmostEqual(self.probe_with([(0, 5)] * 4).core_usage(0, 10, cores=4), 0.5)

    def test_nothing_running_is_zero(self):
        self.assertAlmostEqual(self.probe_with([]).core_usage(0, 10, cores=4), 0.0)

    def test_an_empty_window_does_not_divide_by_zero(self):
        self.assertEqual(self.probe_with([(0, 1)]).core_usage(5, 5, cores=4), 0.0)

    def test_more_processes_than_cores_is_still_a_full_machine(self):
        """Oversubscribing does not mean more than 100% of a machine was used."""
        probe = self.probe_with([(0, 10)] * 20)
        self.assertAlmostEqual(probe.core_usage(0, 10, cores=4), 1.0)

    def test_work_running_past_the_window_is_not_counted_beyond_it(self):
        self.assertAlmostEqual(self.probe_with([(0, 20)]).core_usage(0, 10, cores=4), 0.25)


class TestProbeRestoresWhatItPatched(unittest.TestCase):
    def test_the_wrapped_functions_come_back(self):
        """A probe that leaked its wrapper would make every later measurement count twice."""
        from lib.diagram import browser, render
        before = (render.compile_source, browser._measure, browser.text_widths,
                  browser.rasterise)
        with speed.Probe():
            self.assertIsNot(render.compile_source, before[0])
        after = (render.compile_source, browser._measure, browser.text_widths,
                 browser.rasterise)
        self.assertEqual(before, after)

    def test_it_restores_the_concurrency_helper_too(self):
        from lib import parallel
        before = parallel.slot
        with speed.Probe():
            self.assertIsNot(parallel.slot, before)
        self.assertIs(parallel.slot, before)

    def test_a_span_excludes_the_time_spent_queueing(self):
        """Two runs held apart by the cap must not read as two things running at once.

        Timed at the call site rather than at the slot, the second of these would appear to
        start while the first was still going, and a queued machine would read as a busy one.
        """
        import threading as _threading
        from lib import parallel
        original = parallel._SLOTS
        parallel._SLOTS = _threading.BoundedSemaphore(1)      # force them to queue
        try:
            with speed.Probe() as probe:
                def hold():
                    with parallel.slot():
                        time.sleep(0.05)
                threads = [_threading.Thread(target=hold) for _ in range(2)]
                for t in threads:
                    t.start()
                for t in threads:
                    t.join()
        finally:
            parallel._SLOTS = original
        self.assertEqual(len(probe.spans), 2)
        (a_start, a_end), (b_start, b_end) = sorted(probe.spans)
        self.assertGreaterEqual(b_start, a_end - 0.005)

    def test_it_restores_even_when_the_block_raises(self):
        from lib.diagram import render
        before = render.compile_source
        with self.assertRaises(ValueError):
            with speed.Probe():
                raise ValueError("boom")
        self.assertIs(render.compile_source, before)


class TestRows(unittest.TestCase):
    def test_every_job_is_reported(self):
        keys = [row["key"] for row in speed.rows(record(), record())]
        for scenario in speed.SCENARIOS:
            self.assertIn(scenario["key"], keys)

    def test_the_machine_costs_are_separate_from_the_jobs(self):
        """They belong to the closing section, not the table of jobs."""
        job_keys = [row["key"] for row in speed.rows(record(), record())]
        self.assertNotIn("launch_floor", job_keys)
        cost_keys = [row["key"] for row in speed.costs(record(), record())]
        self.assertEqual(cost_keys, ["launch_floor", "page_cost"])

    def test_no_job_label_uses_an_internal_word(self):
        """CONTEXT.md is binding on anything a person reads, and this table is read."""
        banned = ("corpus", "corpora", "figure", "callout", "gate", "d2", "rung", "ladder")
        for row in speed.rows(record(), record()):
            text = f"{row['label']} {row['what']}".lower()
            for word in banned:
                with self.subTest(row=row["key"], word=word):
                    self.assertNotIn(word, text)

    def test_the_report_never_shows_an_internal_count_name(self):
        """`compiles`/`launches`/`pages` are the data's names, not the reader's.

        "measurement" singular is deliberately NOT banned: it also means a whole recorded
        benchmark run — "against the last measurement, taken 18 Aug" — which is a different
        thing from the per-drawing count and is the right word for it.
        """
        spec = speed.build(record(corpus=run(40.0, core_usage=0.3)), record(corpus=80.0), [])
        page = " ".join(s["html"] for s in spec["sections"])
        for word in ("compiles", "pages", "draw attempt", "measurements"):
            with self.subTest(word=word):
                self.assertNotIn(word, page)
        self.assertIn("layout candidates", page)
        self.assertIn("inspections", page)

    def test_a_faster_run_reads_as_better(self):
        current = record(corpus=40.0)
        baseline = record(corpus=80.0)
        row = next(r for r in speed.rows(current, baseline) if r["key"] == "corpus")
        self.assertEqual(row["verdict"], "better")
        self.assertEqual((row["now"], row["before"]), (40.0, 80.0))

    def test_counts_are_carried_with_their_baseline(self):
        current = record(corpus=run(40.0, compiles=193))
        baseline = record(corpus=run(80.0, compiles=68))
        row = next(r for r in speed.rows(current, baseline) if r["key"] == "corpus")
        self.assertEqual(row["counts"]["compiles"], (193, 68))

    def test_it_survives_having_no_baseline_at_all(self):
        reported = speed.rows(record(corpus=40.0), None)
        row = next(r for r in reported if r["key"] == "corpus")
        self.assertEqual(row["verdict"], "new")
        self.assertIsNone(row["before"])


class TestHeadline(unittest.TestCase):
    """The one sentence at the top, and the colour it is printed in."""

    def headline(self, current, baseline):
        return speed.headline(speed.rows(current, baseline))

    def test_the_full_check_decides_the_colour_not_the_tally(self):
        """Two parts faster while the whole job got slower must not read as good news.

        This is a real defect that shipped: 2 better against 1 worse painted the verdict
        green while the sentence beside it said a full check was 22% slower.
        """
        current = record(one_figure=5.0, ten_drawings=5.0, corpus=90.0, clipping_gate=1.0)
        baseline = record(one_figure=10.0, ten_drawings=10.0, corpus=72.0, clipping_gate=1.0)
        word, sentence = self.headline(current, baseline)
        self.assertEqual(word, "worse")
        self.assertIn("slower", sentence)

    def test_a_faster_full_check_reads_as_better(self):
        current = record(one_figure=9.0, corpus=43.0)
        baseline = record(one_figure=13.0, corpus=72.0)
        self.assertEqual(self.headline(current, baseline)[0], "better")

    def test_nothing_moving_says_so(self):
        word, sentence = self.headline(record(corpus=40.0), record(corpus=40.0))
        self.assertEqual(word, "same")
        self.assertIn("None of the four jobs moved", sentence)

    def test_it_never_says_a_bare_count_with_no_noun(self):
        """"4 faster" is a number with no unit; it has to say 4 of what."""
        current, baseline = speed.demo_run()
        sentence = speed.headline(speed.rows(current, baseline))[1]
        self.assertNotRegex(sentence, r"^\d+ (faster|slower)")
        self.assertIn("jobs", sentence)

    def test_it_counts_each_outcome(self):
        current, baseline = speed.demo_run()
        sentence = speed.headline(speed.rows(current, baseline))[1]
        self.assertIn("faster", sentence)
        self.assertIn("slower", sentence)


class TestReport(unittest.TestCase):
    def build(self, current=None, baseline=None, drifted=()):
        return speed.build(current or record(corpus=40.0, one_figure=9.0),
                           baseline, list(drifted))

    def section(self, spec, section_id):
        """By id, never by position — the opening section only exists when there is one."""
        return next(s["html"] for s in spec["sections"] if s["id"] == section_id)

    def test_it_builds_four_sections(self):
        spec = self.build(baseline=record(corpus=80.0, one_figure=13.0))
        self.assertEqual([s["id"] for s in spec["sections"]],
                         ["summary", "jobs", "work", "why"])

    def test_the_summary_carries_the_stats_even_with_no_prose_to_show(self):
        """It is the only place the outcome is stated now that the subtitle is gone."""
        html = self.section(self.build(baseline=record(corpus=80.0)), "summary")
        self.assertIn("last measurement", html)
        self.assertIn("jobs", html)

    def test_there_is_no_subtitle(self):
        self.assertNotIn("subtitle", self.build(baseline=record(corpus=80.0)))

    def test_both_times_and_counts_are_shown_without_a_switcher(self):
        spec = self.build(baseline=record(corpus=80.0))
        self.assertIn("before", self.section(spec, "jobs"))
        self.assertIn("layout candidates", self.section(spec, "work"))
        self.assertNotIn("data-v=", self.section(spec, "work"))

    def test_a_drifted_machine_is_warned_about_in_the_page(self):
        html = self.section(self.build(drifted=["cores"]), "why")
        self.assertIn("different machine", html)
        self.assertIn("cores", html)

    def test_no_warning_when_the_machine_matches(self):
        self.assertNotIn("different machine", self.section(self.build(), "why"))

    def test_it_builds_with_no_baseline(self):
        self.assertIn("no baseline", self.section(self.build(baseline=None), "why"))

    def test_an_unstable_count_is_shown_rather_than_hidden(self):
        current = record(corpus=run(40.0, unstable=["(1, 2, 3)", "(1, 2, 4)"]))
        html = self.section(speed.build(current, record(corpus=40.0), []), "jobs")
        self.assertIn("cannot be trusted", html)

    def test_what_changed_opens_the_report_and_how_closes_it(self):
        """The two texts must not land in the same place, or the report says it twice."""
        current = dict(record(corpus=40.0), summary="two diagrams were added",
                       why="arrows are traced once")
        spec = speed.build(current, record(corpus=80.0), [])
        self.assertIn("two diagrams were added", self.section(spec, "summary"))
        self.assertNotIn("two diagrams were added", self.section(spec, "why"))
        self.assertIn("arrows are traced once", self.section(spec, "why"))

    def test_grown_work_is_called_out_as_expected_rather_than_a_fault(self):
        current = record(corpus=run(90.0, compiles=231))
        baseline = record(corpus=run(72.0, compiles=193))
        html = self.section(speed.build(current, baseline, []), "why")
        self.assertIn("different amount of work", html)
        self.assertIn("not a regression", html)

    def test_identical_work_says_so(self):
        html = self.section(speed.build(record(corpus=40.0), record(corpus=80.0), []), "why")
        self.assertIn("exactly the same work", html)

    def test_a_changed_count_is_neutral_not_coloured(self):
        """More work is not 'worse'; only the closing section may judge it."""
        current = record(corpus=run(40.0, compiles=231))
        baseline = record(corpus=run(80.0, compiles=193))
        html = self.section(speed.build(current, baseline, []), "work")
        self.assertIn('class="chip moved">\u2191 was 193', html)
        self.assertNotIn('class="chip worse">', html)

    def usage_chip(self, now_seconds, before_seconds, now_usage, before_usage):
        current = record(corpus=run(now_seconds, core_usage=now_usage))
        baseline = record(corpus=run(before_seconds, core_usage=before_usage))
        html = self.section(speed.build(current, baseline, []), "jobs")
        found = re.search(r'<span class="umove"><span class="chip (\w+)">', html)
        return found.group(1) if found else None

    def test_a_wobble_in_core_usage_is_not_reported_at_all(self):
        """It is derived from the timings, so it cannot be steadier than they are."""
        self.assertIsNone(self.usage_chip(40.0, 40.0, 0.52, 0.54))

    def test_using_more_of_the_machine_is_always_an_improvement(self):
        self.assertEqual(self.usage_chip(40.0, 80.0, 0.30, 0.16), "better")

    def test_using_less_is_not_a_fault_when_the_job_got_faster(self):
        """It fell on a job that got 28% faster; red there called a win a regression."""
        self.assertEqual(self.usage_chip(40.0, 80.0, 0.30, 0.62), "moved")

    def test_using_less_without_getting_faster_is_a_fault(self):
        self.assertEqual(self.usage_chip(80.0, 80.0, 0.30, 0.62), "worse")

    def test_a_job_with_no_core_usage_figure_still_renders(self):
        """An older recording has no such figure, and the row must survive without one."""
        html = self.section(self.build(baseline=record(corpus=80.0)), "jobs")
        self.assertIn("One diagram", html)
        self.assertNotIn('class="uval"', html)

    def test_a_job_worth_acting_on_is_marked_in_the_row(self):
        """The note lives in a tooltip, so without a marker nothing says it is worth opening.

        Driven by a scenario made up here rather than by whichever one happens to carry an
        opportunity today: there are none at the moment, and a test that asserted otherwise
        would have to be edited every time one is found or taken.
        """
        marked = dict(speed.SCENARIOS[0], opportunity="a win nobody has taken")
        original = speed.SCENARIOS[:]
        speed.SCENARIOS[0] = marked
        try:
            current = record(**{s["key"]: run(40.0, core_usage=0.3) for s in speed.SCENARIOS})
            html = self.section(speed.build(current, record(corpus=80.0), []), "jobs")
        finally:
            speed.SCENARIOS[:] = original
        self.assertEqual(html.count('class="flag"'), 1)
        self.assertIn("a win nobody has taken", html)

    def test_no_job_is_marked_when_none_has_an_opportunity(self):
        current = record(**{s["key"]: run(40.0, core_usage=0.3) for s in speed.SCENARIOS})
        html = self.section(speed.build(current, record(corpus=80.0), []), "jobs")
        self.assertEqual(html.count('class="flag"'),
                         sum(1 for s in speed.SCENARIOS if s.get("opportunity")))

    def test_the_change_in_core_usage_carries_its_unit(self):
        """"was 62" is not a quantity; the reader has to guess what it counts."""
        current = record(corpus=run(40.0, core_usage=0.30))
        baseline = record(corpus=run(80.0, core_usage=0.62))
        html = self.section(speed.build(current, baseline, []), "jobs")
        self.assertIn("was 62%", html)

    def test_the_terms_are_collapsed_by_default(self):
        """Definitions are for looking up, not for reading past."""
        spec = self.build(baseline=record(corpus=80.0))
        for section_id in ("jobs", "work"):
            with self.subTest(section=section_id):
                html = self.section(spec, section_id)
                self.assertIn('<details class="terms">', html)
                self.assertNotIn('<details class="terms" open>', html)

    def test_the_outcome_is_emphasised_rather_than_the_whole_paragraph_coloured(self):
        html = self.section(self.build(baseline=record(corpus=80.0)), "summary")
        self.assertIn('<strong class="hl better">', html)
        self.assertNotIn('class="opening stat better"', html)

    def test_fewer_browser_starts_reads_as_an_improvement(self):
        """The one count with a direction: a browser costs the same however little it does."""
        current = record(corpus=run(40.0, launches=27))
        baseline = record(corpus=run(80.0, launches=39))
        html = self.section(speed.build(current, baseline, []), "work")
        self.assertIn('class="chip better">\u2193 was 39', html)

    def test_the_demo_report_says_it_is_invented(self):
        current, baseline = speed.demo_run()
        marked = self.section(speed.build(current, baseline, [], demo=True), "jobs")
        self.assertIn("EXAMPLE REPORT", marked)
        self.assertNotIn("EXAMPLE REPORT",
                         self.section(speed.build(current, baseline, []), "jobs"))

    def test_the_demo_covers_all_three_outcomes(self):
        """A layout only ever seen with good news hides whether bad news is legible."""
        current, baseline = speed.demo_run()
        verdicts = {row["verdict"] for row in speed.rows(current, baseline)}
        self.assertEqual(verdicts, {"better", "worse", "same"})

    def test_numbers_render_without_a_value(self):
        self.assertEqual(speed.number(None), "—")
        self.assertEqual(speed.number(1.234, "s", 2), "1.23s")

    def test_a_chip_shows_the_percentage_it_moved(self):
        self.assertIn("-50%", speed.chip(*speed.verdict(50.0, 100.0)))
        self.assertIn("~same", speed.chip("same", 0.01))


class TestBaselineFile(unittest.TestCase):
    """Round-trips through a real file, and removes exactly the one it made."""

    def setUp(self):
        handle, self.path = tempfile.mkstemp(prefix="test_measure_speed_", suffix=".json")
        os.close(handle)
        os.unlink(self.path)

    def tearDown(self):
        if os.path.exists(self.path):
            os.unlink(self.path)

    def test_a_missing_baseline_reads_as_none(self):
        self.assertIsNone(speed.load_baseline(self.path))

    def test_what_is_saved_is_what_comes_back(self):
        current = record(corpus=42.0)
        speed.save_baseline(current, self.path)
        self.assertEqual(speed.load_baseline(self.path), current)

    def test_it_is_written_sorted_so_a_diff_stays_readable(self):
        speed.save_baseline(record(corpus=42.0), self.path)
        with open(self.path, encoding="utf-8") as handle:
            body = handle.read()
        self.assertEqual(body, json.dumps(json.loads(body), indent=2, sort_keys=True) + "\n")

    def test_the_committed_baseline_is_loadable_and_complete(self):
        """The one in the repo, so a broken commit of it fails here rather than mid-run."""
        committed = speed.load_baseline()
        if committed is None:
            self.skipTest("no baseline recorded yet")
        self.assertIn("machine", committed)
        for scenario in speed.SCENARIOS:
            self.assertIn(scenario["key"], committed["scenarios"])


class TestCli(unittest.TestCase):
    def test_update_refuses_a_quick_run(self):
        with self.assertRaises(SystemExit):
            speed.main(["--update", "--quick", "--no-open"])

    def test_it_refuses_before_measuring_rather_than_after(self):
        """Four minutes of measuring must not precede an argument error."""
        called = []
        original = speed.measure
        speed.measure = lambda **kwargs: called.append(1)
        try:
            with self.assertRaises(SystemExit):
                speed.main(["--update", "--quick", "--no-open"])
        finally:
            speed.measure = original
        self.assertEqual(called, [])

    def test_update_refuses_a_rebuilt_run(self):
        """A baseline records what the machine did now, never what a saved file says it did."""
        with self.assertRaises(SystemExit):
            speed.main(["--update", "--from", "whatever.json", "--no-open"])

    def test_rebuilding_from_a_saved_run_measures_nothing(self):
        handle, path = tempfile.mkstemp(prefix="test_measure_speed_run_", suffix=".json")
        os.close(handle)
        self.addCleanup(lambda: os.path.exists(path) and os.unlink(path))
        speed.write_json(record(corpus=40.0), path)
        called = []
        stubs = {"measure": lambda **k: called.append(1),
                 "write": lambda *a, **k: "report"}
        original = {name: getattr(speed, name) for name in stubs}
        for name, stub in stubs.items():
            setattr(speed, name, stub)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                speed.main(["--from", path, "--no-open"])
        finally:
            for name, real in original.items():
                setattr(speed, name, real)
        self.assertEqual(called, [])

    def test_the_first_recording_needs_no_force(self):
        """With no baseline there is nothing to have drifted FROM — see `main`.

        `write` is stubbed along with the rest: its real output path is the one a genuine run
        uses, so a test that let it run would leave a file it cannot prove is its own.
        """
        saved, spec = [], record(corpus=1.0)
        # `write_json` among them: it saves every run to a fixed path in /tmp that a real run
        # also uses, so leaving it live would leak a file this test cannot prove is its own.
        stubs = {"load_baseline": lambda *a, **k: None,
                 "measure": lambda **k: spec,
                 "save_baseline": lambda current, *a, **k: saved.append(current) or "baseline",
                 "write_json": lambda *a, **k: "run.json",
                 "write": lambda *a, **k: "report"}
        original = {name: getattr(speed, name) for name in stubs}
        for name, stub in stubs.items():
            setattr(speed, name, stub)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                speed.main(["--update", "--no-open"])
        finally:
            for name, real in original.items():
                setattr(speed, name, real)
        self.assertEqual(saved, [spec])


if __name__ == "__main__":
    unittest.main(verbosity=2)
