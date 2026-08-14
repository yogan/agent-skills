Guidance for working on this repo's own code. Not for a skill's runtime behavior — that's
`SKILL.md`/`REFERENCE.md` inside each skill — and not project setup — that's
[README.md](README.md).

## Layout

- `skills/<name>/` — one skill each, independently symlink-installable (see README.md).
  `scripts/` holds its Python implementation.
- `hooks/` — cross-skill runtime infra (the paste-gate Stop hook). See
  [hooks/README.md](hooks/README.md).
- `lib/` — Python shared repo-wide (skill scripts and `e2e/` both import from it).
  Only for logic that's provably identical across its consumers, modulo a trivial
  parameter — see below.
- `lib/diagram/` — the diagram renderer (D2), shared by the explainers and `visualize`.
  The one place in `lib/` that isn't pure Python: `lib/diagram/js/` holds a Node script
  because measuring a rendered diagram needs a real browser — see that module's docstring
  for why there's no substitute. The Python side decides; the JS only measures.

  **`figure.draw()` is the entry point. A skill calls that and nothing else.** It says what
  the picture is FOR (`target="embed"` for a host page, `"file"` for a standalone image) and
  gets back the SVG plus what is wrong with it. Which gates that implies, whether the layer
  spacing needs escalating, where each callout goes, how many browser launches it takes — all
  of it is behind that call. A skill that imports `gates`, `place` or `spec` directly is
  re-deciding something `figure` already decided: both skills used to, they disagreed about
  which gates apply, and the one `explain-diff` omitted was the only one that can see a label
  buried under a callout. `render` is fair game for `HOST_CSS`/`page_css` — that is what a
  host page must ship, which really is the caller's business.
- `e2e/` — local GitLab demo/test rig for `review-mr`/`rework-mr` (see `e2e/README.md`).

## Sharing vs. duplication

`skills/review-mr/scripts/findings.py` and `skills/rework-mr/scripts/threads.py`
implement related but distinct workflows (reviewing someone else's MR vs. working
through your own), and some of their functions are genuinely identical while others
only look similar but encode a different per-skill state machine.

- **Identical modulo a trivial parameter** → belongs in `lib/`, imported by both.
- **Diverges because the underlying domain differs** (e.g. topic status vocabularies) →
  stays duplicated, with a `# Mirrors <other file>'s <name>` comment pointing at its
  counterpart, so a change to one prompts a check of the other.

Don't force the second kind into `lib/` — that trades duplication for a worse problem:
per-skill conditionals inside code meant to be shared.

## Testing

Plain `unittest`, colocated as `test_<module>.py` next to the module it covers. Run one
directly, e.g.:

    python3 skills/rework-mr/scripts/test_threads.py

To run everything: `python3 run_tests.py` (~30s). While editing, run less:

- `python3 run_tests.py --changed` — resolves your uncommitted edits through the repo's import
  graph and runs every test that reaches them. Touching `lib/gitlab.py` runs 4 files in 0.4s;
  touching `lib/diagram/palette.py` runs 14, because that is genuinely how far it reaches. It
  **declines and runs everything** whenever it cannot map a changed file, since a selector that
  quietly skips the failing test is worse than a slow one. `test_run_tests.py` exists to pin
  that, and every case in it is a real under-selection that was once shipped.
- `python3 run_tests.py compact` — plain substring filter on the path, when you know what you
  want.

Neither replaces the full fast suite before you commit. Don't reach for `python3 -m unittest
discover`: it only finds tests under directories with an `__init__.py`, which is just `lib/`
here, so it silently runs a fraction of the suite with no error.

### What a test may do to the filesystem

Some tests here write real files, and that is not a smell: `test_visualize.py` covers a CLI
whose whole contract is "write a file and print its path". Every *library* unit test writes
nothing at all, which is why they run in tenths of a second. The rule is not "don't write",
it is:

> a test removes exactly the files it created, and never a pattern.

Both halves have been broken. `test_findings.py` called `tempfile.mkdtemp()` and removed
nothing, leaking three directories per suite run until 460 had accumulated. And the tempting
fix for the other half — sweeping `/tmp/*diagram*` — would delete real `visualize.py` output,
since the CLI writes there by design and its files are indistinguishable from a test's unless
the test gives itself a name of its own. That is why the one test covering the default output
path uses a title no real diagram would have: the file it deletes is provably its own.

Real skill runs are NOT tidied up. `/tmp/<date>-diagram-<slug>.svg` is somebody's diagram.

`test_repo_hygiene.py` enforces both halves, statically and by measuring.

**`--slow` costs about two minutes. Do not put it in an edit-test loop.** A `*_slow.py` file
is one whose cost is irreducible, and there are two, which is the thing to know before typing
the flag:

| file | cost | why it cannot be faster |
|---|---|---|
| `lib/diagram/test_place_slow.py` | ~2m | every assertion is a real callout-placement search — 64 d2 compiles per two-callout diagram, each measured in a real browser. Faking it would test the fake. |
| `hooks/test_paste_gate_slow.py` | ~36s | real `time.sleep()` in a subprocess, unmockable from the test. |

Run `--slow` **once**, at the end, and only if you touched `place.py`, the harness geometry,
callout anchoring, or the paste gate. Otherwise the plain fast suite is the gate. Every run
prints seconds per file and names the slowest three, so a regression in test cost is visible
without anyone having to go looking for it — that reporting exists because a session once spent
40 of its 76 minutes re-running `--slow` five times over minor edits.
