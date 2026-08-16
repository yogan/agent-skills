Guidance for working on this repo's own code. Not for a skill's runtime behavior — that's
`SKILL.md`/`REFERENCE.md` inside each skill — and not project setup — that's
[README.md](README.md).

## This repository is public

MIT licensed, and readable by anyone. **Nothing from a proprietary or commercial codebase may
land in it** — not in source, tests, fixtures, docs, examples or **commit messages**.

Testing a skill against real private work is fine and is how most of this was measured. What
must not cross over is anything identifying: real commit hashes and subject lines, ticket ids,
internal API, header, service, field or diagram names, a library choice tied to a real change.
Invent the identifiers instead. A measurement is ours and can be quoted — "8% of a label was
covered, three anchors hid nothing" — while the *name* of the thing measured is not.

Commit messages matter most, because a file can be edited later and history cannot. Grep the
diff before committing.

## Working agreement

How work runs here, whatever it touches. One topic at a time:

1. Fix the reported flaw on the example(s) it was reported on — all of them if the report
   named several.
2. Prove those are fixed, by the cheapest means that could disprove it.
3. **Only then** check the other example(s) of the same kind. If one is broken, fix it
   against that example alone, then re-verify the set.
4. **Only then** judge whether the change can realistically affect other kinds, and check
   those only if it realistically can.
5. Show the result in the form it can be judged in, and wait. Feedback sends you back to 1;
   an ack moves to 6.
6. Run the full quality checks. A commit is never created with a failing test.
7. Commit, then the next topic.

The full suite belongs *after* the ack, not before showing — otherwise it is paid for on
work that may be rejected.

**Verifying.** Verify the claim you are about to make, by the cheapest means that could
disprove it: a measurement beats a render, a render beats a full capture. Never present a
visual result you have not looked at yourself. Say what you did not check.

Diagnose the same way. Before proposing a cause, measure the thing's actual state against the
state it was allowed to be in — where it is against where it was permitted to be, what it
scored against what the alternatives scored. A session once guessed three causes for one
defect, built two of them and reverted both; the measurement that found the real one took a
minute and could have been the first move.

**Showing.** What "show" means depends on what changed, and only the first of these has
tooling:

- **A rendered figure** — one before/after image per topic, opened, with unchanged cases
  named rather than shown. `compare_figures.py` builds it.
- **A generated document** (the `explain-*` skills) — open the document and say what to look
  at in it. There is no before/after tooling here; if a comparison is worth building, that is
  its own topic, not something to improvise mid-change.
- **A skill's behaviour inside a session** (`review-mr`, `rework-mr`, hooks) — there is no
  artefact to show, and you cannot test it yourself: the change only proves out in a fresh
  session. Say exactly what to run and what should be different from last time, then wait.
  The ack comes after the owner has run it, so step 2's "prove it" is unit tests plus a
  precise claim about the new behaviour.

No intermediate results — show when you believe you are done. **Opening something is the LAST
step, not a step.** Look at intermediate renders yourself; the owner's screen is not a
scratchpad, and an image opened mid-investigation is one more window they have to close. Where
an image genuinely settles a question mid-topic, open exactly one and say so.

Whatever is opened, the message that goes with it carries, near the end:

- **the file name**, so it can be told apart from whatever else is on screen;
- **what should be better in it**, as a short list;
- **what is worse, or was traded away**, on the same list — a compromise the owner has to find
  for themselves reads as one you hoped they would not.

And every claim on that list is re-measured against the artefact being opened. A conclusion
drawn from an earlier variant does not carry: the same lever moved twice in one session, and a
"win" that was real in the second capture was reported off the fourth, where it had been undone.

**Committing.** Never commit or push without an explicit go-ahead for that specific action;
consent does not carry to the next one. Do not raise committing before the owner has
responded to what you showed them. Mid-session the owner may switch to "keep working, commits
when I say" — then stop asking after each chunk and wait to be asked.

**Reporting.** Short, high level, from a usage perspective; technical detail to a minimum.
Always report anyway: architectural changes, shortcuts, hacks, compromises, significant
changes to performance (test suite or runtime), and anything you could not deliver.

**Name a measurement, never just its number.** "The 10px floor", "the 777px column", "the 880px
body" are meaningless to anyone not currently inside the file they come from — 10px of *what
text*, 777px of *what*, and in which of the two targets? A number offered for a decision carries
what it measures, on which target, and for which element, the first time it appears in a
conversation, and again whenever the thread has moved on. Not:

> it needs rung 24, where the text is 9.4px against a 10px floor

but:

> it needs rung 24, and there it is 1079px wide — too wide for the 777px of drawing room inside
> a card on the explainer page, so the browser scales it to 0.72 and the node labels come out at
> 9.4px against the 10px floor for primary text

The same applies to a number in a comment or a docstring: `size.check` claimed "~11px, ~10px for
an edge label" for years while the constants said 10, 9 and 7.5, and nobody could tell because
none of the three said which text it governed. **A bare number is a question the reader has to
ask back**, and the answer is rarely one they can look up quickly.

**And never in the code's own metaphor.** The internal names are fine where they are defined and
useless anywhere else. "Rung 24" is the worst offender here and is banned outright: say *24px of
spacing between an edge and the box it points at*. `ELK_EDGE_LADDER` and `ELK_SPACING_LADDER` are
lists of px values for two ELK options, so a "rung" is a pixel count with a name — give the count
and what it spaces. Same for anything else whose meaning lives in one file: name the real thing
and its unit, not the picture the code uses to keep track of it.

**Asking.** When the wish is not clear, grill before starting. Never ask about implementation
choices — use best practice. Do ask when the options lead to visibly different outcomes, when
a wish cannot be met without a compromise, or when it is risky. Non-trivial changes get
discussed; trivial ones just get done.

**Scope.** Keep the structure the owner chose: when something does not fit, adjust the
arrangement rather than proposing to change what is being built. A rule stated about one case
applies to all of them — fix the class of defect, not the instance.

**As you go.** Comments carry what the code cannot say — the reasoning and the constraint,
described generally, not a story or a number from one case. Delete what has become stale or
wrong rather than leaving it beside the new thing.

**Independent work runs at the same time.** A typical developer machine has cores to spare and
nearly everything expensive here is a subprocess — a d2 compile, a node measurement, a Chrome
launch — so a serial loop over independent items is wasted wall clock, in the repo's own code
and in a throwaway script alike. `lib/parallel.py` is the helper and says where it does and does not
apply; `run_tests.py` has always run its files this way.

The two rules that keep it honest:

- **Fan out at the innermost place that has independent work**, not at every level. Nesting
  pools puts more subprocesses on the machine than it has cores and spends the win on
  contention.
- **Order is not optional.** Results are zipped back against the inputs that produced them
  almost everywhere here, so `parallel.each` preserves it — and a batch split across workers
  reassembles in the order it was given.

A sequential dependency is not a fan-out: the spacing ladders decide whether to try the next
rung FROM the result of this one, and rendering all rungs speculatively burns cores on answers
that are usually thrown away.

## Layout

- `skills/<name>/` — one skill each, independently symlink-installable (see README.md).
  `scripts/` holds its Python implementation.
- `hooks/` — cross-skill runtime infra (the paste-gate Stop hook). See
  [hooks/README.md](hooks/README.md).
- `lib/` — Python shared repo-wide (skill scripts and `e2e/` both import from it).
  Only for logic that's provably identical across its consumers, modulo a trivial
  parameter — see below.
- `lib/diagram/` — the diagram renderer (D2), shared by the explainers and `visualize`. One
  module in it, `route.py`, moves a line the engine drew; everything else measures. Its
  entry point, the rules its output must satisfy and the loop for changing it are in
  [lib/diagram/README.md](lib/diagram/README.md). The one place in `lib/` that isn't pure
  Python: `lib/diagram/js/` holds a Node script because measuring a rendered diagram needs a
  real browser. The Python side decides; the JS only measures.
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

To run everything: `python3 run_tests.py` (~125s, most of it browser launches and d2). While
editing, run less:

- `python3 run_tests.py --changed` — resolves your uncommitted edits through the repo's import
  graph and runs every test that reaches them. Touching `lib/gitlab.py` runs 4 files in 0.4s;
  touching `lib/diagram/palette.py` runs 14, because that is genuinely how far it reaches. It
  **declines and runs everything** whenever it cannot map a changed file, since a selector that
  quietly skips the failing test is worse than a slow one. `test_run_tests.py` exists to pin
  that, and every case in it is a real under-selection that was once shipped. With a CLEAN tree
  it runs nothing — no edits, nothing they can affect.
- `python3 run_tests.py compact` — plain substring filter on the path, when you know what you
  want. This is the one that keeps an edit loop cheap: selection cuts CPU, not wall clock,
  because one slow file sets the pace whenever it is reachable.

**A whole run that already passed today is skipped, and says so.** `run_tests.py` records a
passing full run against a fingerprint of every non-ignored file plus the `d2` and `node`
versions, and exits early on an identical one. It is bounded to the day, because the
fingerprint cannot see a browser that updated overnight — the same reason `gates/__init__.py`
insists a check that did not run must not report success. `--force` runs them anyway. If you
are surprised that nothing ran, that is what happened.

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
| `lib/diagram/test_place_slow.py` | ~100s alone, ~3m inside a full `--slow` run | every assertion is a real callout-placement search — 64 d2 compiles per two-callout diagram, each measured in a real browser. Faking it would test the fake. The compiles run concurrently now, which is why it costs so much more when the rest of the suite is competing for the same cores. |
| `hooks/test_paste_gate_slow.py` | ~36s | real `time.sleep()` in a subprocess, unmockable from the test. |

Run `--slow` **once**, at the end, and only if you touched `place.py`, the harness geometry,
callout anchoring, or the paste gate. Otherwise the plain fast suite is the gate. Every run
prints seconds per file and names the slowest three, so a regression in test cost is visible
without anyone having to go looking for it — that reporting exists because a session once spent
40 of its 76 minutes re-running `--slow` five times over minor edits.
