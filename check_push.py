#!/usr/bin/env python3
"""The push level: everything the top commit has to pass before it is pushed, run once.

    python3 check_push.py                  # check HEAD against its upstream, record the pass
    python3 check_push.py --scenarios all  # every recorded scenario, whatever the change
    python3 check_push.py --scenarios none # no scenario (say so when you report)
    python3 check_push.py --jobs 1         # the scenarios one after another (default: 3 at once)
    python3 check_push.py --dry-run        # say what would run, run nothing
    python3 check_push.py --again          # run it all again on a tree that passed (to re-measure)
    python3 check_push.py --verify SHA...  # the pre-push hook: has each of these passed?

The repo's checks come in three levels, so that the expensive ones are paid once and not in
every loop (CLAUDE.md, "Testing"):

  * **loop**   — `run_tests.py --changed`, and the one scenario a change shows in;
  * **commit** — the commit hook, and `check_commits.py` after a rewrite: ruff, private words,
    the tests each commit's own change reaches;
  * **push**   — this: the commit level for every unpushed commit, the whole unit suite on the
    top commit on the Mac itself (no test box), and the recorded scenarios the change can
    reach (`e2e/drive.py verify`).

A pass is recorded against the top commit's TREE, so an amend that changes nothing, or a
second run, is free, and so is one that only folds in the scenario times re-recorded from it
(`e2e/timing_baseline.json`, alone: with any other file it is a new tree like any other); and after an amend that does change something, the scenarios are picked
from what changed since the last tree that passed. `.githooks/pre-push` refuses a push whose
commit has no recorded pass (`git push --no-verify` overrides it, as for any hook).

The scenarios run several at a time (`JOBS`): each has a copy of the rig that is its alone
(`e2e/drive.py`, `copy_of`), so they share nothing but the machine and the one GitLab. A
scenario's output is printed whole when it ends.

Each scenario that passes is noted against the tree as it passes, so a run that failed on one
of them does not pay for the others again: the next run of the same tree says which have
passed already and runs the rest. The same tree is the same code, and a scenario plays a
recording, so a pass stays a pass; any commit or amend that changes the tree starts over.

It checks the committed tree, so it refuses to start with uncommitted changes: a pass would
otherwise be recorded for something else than what gets pushed. For the same reason it records
nothing, not even a scenario's pass, once the checkout has changed under it during the run.

After its scenarios it prints how long each took against `e2e/timing_baseline.json`: what
got slower or faster beyond noise, what is new or gone, and their wall clock. That informs and
never fails the push; it covers only the scenarios this run ran, and their times go into the
pass record, from which `python3 e2e/timing.py --update` re-records the baseline (its
docstring is the method).

Which scenarios a change reaches: a scenario reaches the skills whose slash command its steps
type; a skill's scripts reach the `lib/` modules they import. The mod, the e2e harness and the
paste-gate hook reach every scenario, but for the rig's parts a verify never runs (drawing
captures, the rig's one-time setup, the demo session); documentation, the repo's own tooling
and the skills no scenario runs reach none; and anything else reaches all of them, since a picker that skips the
scenario that would have failed is worse than a slow one.
"""
import json
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "e2e"))

import run_tests  # noqa: E402
import timing  # noqa: E402

from lib import parallel  # noqa: E402

RECORD = HERE / ".check_push_passed.json"
# Scenarios at the same time. Each is a real claude in tmux beside its stand-in model and the
# skills' scripts, and they all talk to one GitLab in a 4-CPU VM: three kept every scenario
# as fast as it runs alone, with the Mac's load where it was (see the commit that set this).
JOBS = 3
KEEP = 30           # recorded passes kept; a pass older than these is simply run again

# What the scenarios exercise, by path. Matched in order; the first match decides.
ALL, NONE = "all", "none"
DOC = run_tests.DOCS
# The repo's own tooling, and skills no scenario runs: nothing a session under test reads.
UNREACHED = (
    re.compile(r"^(?:run_tests|check_commits|check_push|measure_speed|measure_checks|"
               r"compare_figures|show_limits|fails_without|private_terms)\.py$"),
    re.compile(r"^(?:speed_baseline\.json|e2e/timing_baseline\.json|ruff\.toml|\.gitignore|"
               r"LICENSE)$"),
    re.compile(r"^(?:\.githooks|docs|prototypes)/"),
    re.compile(r"^skills/(?:visualize|explain-diff|split-handoff|self-review)/"),
    re.compile(r"(?:^|/)test_[^/]*\.py$"),
    # The rig's parts a verify never runs: drawing captures and adding up times afterwards,
    # the one-time setup of the rig, the demo session.
    re.compile(r"^e2e/(?:screenshot\.py|timing\.py|bootstrap\.py|tmux-demo\.sh)$"),
)
# Skills a scenario reaches through another one: review-mr hands work to these.
VIA = {"explain-branch": "review-mr", "review-branch": "review-mr"}


def git(*args):
    r = subprocess.run(["git", *args], cwd=HERE, capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


def scenarios():
    """(name, skills it types, mods it loads, recording it plays) for every scenario a verify
    runs: the ones handing over to a person are showcase only. Nor one that is the start of
    another, playing the same recording through the first of its steps: kept for trying out
    (`--keep`), it checks nothing the whole one does not."""
    import drive
    import scenarios as sc_mod
    out = []
    for sc in sc_mod.SCENARIOS:
        if any(isinstance(s, drive.Handover) for s in sc.steps):
            continue
        if any(o is not sc and len(o.steps) > len(sc.steps)
               and (o.recording or o.name) == (sc.recording or sc.name)
               and list(o.steps[:len(sc.steps)]) == list(sc.steps)
               for o in sc_mod.SCENARIOS):
            continue
        typed = {m.group(1) for s in sc.steps if isinstance(s, drive.Prompt)
                 for m in [re.match(r"/([\w-]+)", s.text)] if m}
        out.append((sc.name, typed, bool(sc.mods), sc.recording or sc.name))
    return out


def skill_modules():
    """Each skill -> every repo Python file its scripts reach through imports."""
    every = set(run_tests.repo_files())
    graph = run_tests.import_graph(every)
    reach = {}
    for skill in (HERE / "skills").iterdir():
        todo = [p for p in every if skill / "scripts" in p.parents]
        seen = set(todo)
        while todo:
            for nxt in graph.get(todo.pop(), ()):
                if nxt not in seen:
                    seen.add(nxt)
                    todo.append(nxt)
        reach[skill.name] = {str(p.relative_to(HERE)) for p in seen}
    return reach


def reached(paths, scens, reach):
    """The scenario names a change to `paths` can affect, in the order they are defined."""
    names = [n for n, _, _, _ in scens]
    hit = set()
    for path in paths:
        if Path(path).suffix.lower() in DOC and not path.startswith("skills/"):
            continue
        if any(rx.search(path) for rx in UNREACHED):
            continue
        m = re.match(r"^e2e/recordings/([\w-]+)\.json$", path)
        if m:
            hit |= {n for n, _, _, rec in scens if rec == m.group(1)}
            continue
        if path.startswith(("e2e/", "hooks/", ".claude-plugin/")):
            return names
        if path.startswith("mods/"):
            hit |= {n for n, _, mods, _ in scens if mods}
            continue
        m = re.match(r"^skills/([\w-]+)/", path)
        if m:
            skill = VIA.get(m.group(1), m.group(1))
            hit |= {n for n, typed, _, _ in scens if skill in typed}
            continue
        if path.startswith("lib/"):
            skills = {s for s, files in reach.items() if path in files}
            if not skills and path.endswith(".py"):
                continue                      # a lib module no skill script reaches
            if not skills:
                return names                  # a lib data file: cannot tell who reads it
            hit |= {n for n, typed, _, _ in scens
                    if typed & {VIA.get(s, s) for s in skills}}
            continue
        return names                          # anything else: cannot tell, so everything
    return [n for n in names if n in hit]


def load_record():
    try:
        return json.loads(RECORD.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"passed": []}


def passed_trees():
    return {p["tree"] for p in load_record()["passed"]}


# The one file a tree may differ in from a tree that passed and still count as passed: the
# scenario times re-recorded from that very pass, folded into the commit afterwards. Nothing
# runs it and nothing reads it but the comparison, so re-checking would test the same code.
TIMES_ONLY = "e2e/timing_baseline.json"


def has_passed(tree, known=None):
    """Whether `tree` passed, or differs from a tree that did only in TIMES_ONLY."""
    known = passed_trees() if known is None else known
    if tree in known:
        return True
    return any(git("diff", "--name-only", "--no-renames", t, tree) == TIMES_ONLY for t in known)


def starting_point(base):
    """(revision, why) the scenarios are picked from: the newest tree that passed, else `base`.
    Not a pass told to run no scenarios (`--scenarios none`): it lets its own tree be pushed,
    but checked none of what changed, and picking from it would skip those scenarios for good."""
    for p in reversed(load_record()["passed"]):
        if (p.get("ran") or {}).get("mode") == "none":
            continue
        if git("cat-file", "-t", p["tree"]) == "tree":
            return p["tree"], f"since the last tree that passed ({p['commit'][:12]})"
    return base, f"since {base}"


def tree_of(rev):
    return git("rev-parse", f"{rev}^{{tree}}")


def scenarios_passed(tree):
    """The scenarios a run that did not get through has passed for this tree already."""
    part = load_record().get("partial") or {}
    return set(part.get("scenarios", [])) if part.get("tree") == tree else set()


def record_scenario(tree, name):
    """Note one scenario's pass for `tree`; what was noted for another tree is dropped."""
    rec = load_record()
    rec["partial"] = {"tree": tree, "scenarios": sorted(scenarios_passed(tree) | {name})}
    RECORD.write_text(json.dumps(rec, indent=1), encoding="utf-8")


def record_pass(tree, commit, ran):
    rec = load_record()
    rec.pop("partial", None)
    rec["passed"] = [p for p in rec["passed"] if p["tree"] != tree][-(KEEP - 1):] + [
        {"tree": tree, "commit": commit, "at": time.strftime("%Y-%m-%d %H:%M"), "ran": ran}]
    RECORD.write_text(json.dumps(rec, indent=1), encoding="utf-8")


def verify(shas):
    """For the pre-push hook: 0 when every commit to be pushed has a recorded pass."""
    known = passed_trees()
    missing = [s for s in shas if set(s) != {"0"} and not has_passed(tree_of(s), known)]
    if missing:
        print(f"pre-push: {', '.join(s[:12] for s in missing)} has not passed the push level.\n"
              "  run: python3 check_push.py    (git push --no-verify skips this check)",
              file=sys.stderr)
        return 1
    return 0


def dirty():
    return git("status", "--porcelain", "--untracked-files=normal")


def moved(tree):
    """Whether the checkout is no longer `tree`, committed and clean, as it was at the start.

    Everything here runs against the working tree, so an edit, checkout or fold by another
    session during the run means what passed is not `tree`; recording it would let the
    pre-push hook through for code nobody tested. Asked again before every record."""
    return tree_of("HEAD") != tree or bool(dirty())


def run(step, argv):
    print(f"\n==> {step}: {' '.join(argv)}", flush=True)
    started = time.monotonic()
    r = subprocess.run(argv, cwd=HERE)
    print(f"    {step}: {'ok' if r.returncode == 0 else 'FAILED'} in "
          f"{time.monotonic() - started:.0f}s", flush=True)
    return r.returncode == 0


def timed(runs, todo, passing, wall, jobs, existing):
    """What this run's scenarios took, for the pass record, after printing it against the
    baseline. Only the ones it ran and that passed; the wall clock only when every one of them
    passed, since a failure cuts it short. Time never fails the push, so neither may this."""
    try:
        parts = timing.fastest(runs) if runs.is_dir() else {}
        took = {"scenarios": {n: parts[n] for n, good in zip(todo, passing) if good and n in parts},
                "ran": list(todo), "jobs": jobs, "wall": round(wall, 1) if all(passing) else None}
        print("\n" + timing.comparison(timing.load_baseline(), took, existing,
                                       now=timing.machine()), flush=True)
        return took
    except Exception as err:                          # noqa: BLE001 — informs, never fails
        print(f"\n    scenario time: not compared ({type(err).__name__}: {err})", flush=True)
        return None


def main(argv):
    if argv[:1] == ["--verify"]:
        return verify(argv[1:])
    mode = "auto"
    if "--scenarios" in argv:
        mode = argv[argv.index("--scenarios") + 1]
    dry = "--dry-run" in argv

    if dirty() and not dry:
        print("check_push: the working tree has uncommitted changes; commit or stash them first, "
              "so what passes is what gets pushed.", file=sys.stderr)
        return 2
    head, tree = git("rev-parse", "HEAD"), tree_of("HEAD")
    # `--again`: every check of a tree that passed run once more, for numbers taken anew (a
    # time baseline whose first run paid for something once, like a rebuilt rig).
    again = "--again" in argv
    if has_passed(tree) and not dry and not again:
        print(f"check_push: {head[:12]}'s tree has already passed; nothing to run.")
        return 0
    base = git("rev-parse", "--abbrev-ref", "@{u}") or "origin/main"
    since, why = starting_point(base)
    # Both paths of a rename: a module moved out of a skill's reach still changed that skill.
    changed = (git("diff", "--name-only", "--no-renames", since, "HEAD") or "").split()
    scens = scenarios()
    if mode == "all":
        picked = [n for n, _, _, _ in scens]
    elif mode == "none":
        picked = []
    else:
        picked = reached(changed, scens, skill_modules())
    print(f"check_push: {head[:12]}, {len(changed)} changed file(s) {why}; "
          f"scenarios: {', '.join(picked) or 'none'}")
    if dry:
        return 0

    ok = run("every unpushed commit, commit level", [sys.executable, "check_commits.py", base])
    # On the Mac, every file: the test box is Linux, and this is the one run in the real thing.
    ok = run("the whole unit suite, on the Mac", [sys.executable, "run_tests.py", "--host"]) and ok
    # Each on its own, noted as it passes: see the docstring.
    before, started = set() if again else scenarios_passed(tree), time.monotonic()
    for name in picked:
        if name in before:
            print(f"\n==> scenario {name}: passed for this tree already, not run again")
    todo = [n for n in picked if n not in before]
    noting = threading.Lock()
    # This run's captures in a folder of their own, so its times are read from its runs alone.
    runs = HERE / "e2e" / ".runs" / f"push-{time.strftime('%Y%m%d-%H%M%S')}"

    def scenario(name):
        r = subprocess.run([sys.executable, "e2e/drive.py", "verify", name, "--out", str(runs)],
                           cwd=HERE, capture_output=True, text=True)
        with noting:
            print(f"\n==> scenario {name}: {'ok' if r.returncode == 0 else 'FAILED'}\n"
                  + (r.stdout + r.stderr).rstrip(), flush=True)
            if r.returncode == 0 and not moved(tree):
                record_scenario(tree, name)
        return r.returncode == 0

    jobs = int(argv[argv.index("--jobs") + 1]) if "--jobs" in argv else JOBS
    passing = parallel.each(scenario, todo, workers=jobs)
    ok = all(passing) and ok
    took = None
    if picked:
        wall = time.monotonic() - started
        print(f"\n    {len(picked)} scenario(s), {len(before & set(picked))} of them passed before, "
              f"{jobs} at a time: {wall:.0f}s", flush=True)
        if todo:
            took = timed(runs, todo, passing, wall, jobs, [n for n, *_ in scens])
    if not ok:
        print("\ncheck_push: FAILED — nothing recorded")
        return 1
    if moved(tree):
        print("\ncheck_push: the checkout changed while the checks ran, so what passed is not "
              f"{head[:12]}'s tree — nothing recorded; run it again once nothing else is editing")
        return 1
    record_pass(tree, head, {"scenarios": picked, "since": why, "mode": mode,
                             **({"timing": took} if took else {})})
    print(f"\ncheck_push: {head[:12]} passed; recorded, the pre-push hook will let it through")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
