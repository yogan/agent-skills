#!/usr/bin/env python3
"""Run every test_*.py in the repo and report one aggregate result.

    python3 run_tests.py               # fast tests only (the default), in parallel
    python3 run_tests.py --changed     # only what your uncommitted edits can affect
    python3 run_tests.py --changed-since=REV   # only what differs from REV can affect
    python3 run_tests.py compact spec  # only files whose path contains one of these
    python3 run_tests.py --slow        # fast + slow. TWO MINUTES. See below.
    python3 run_tests.py -j1           # serial, for when a parallel failure is confusing
    python3 run_tests.py --host        # every test on the Mac: no test box (the push level)

Most test files run in the test box, a Linux container (`testbox.py` says which and why), at
the same time as the rest run here; `--host` runs them all here, which is what the push level
does before anything is pushed.

`--changed` reads the working tree against HEAD, resolves each changed module through the
repo's own import graph, and runs every test that reaches it transitively. It declines and
runs everything whenever it cannot map a changed file (a document nothing names selects
nothing instead), because a selector that quietly skips the test that would have failed is
worse than a slow one. It is the loop and commit levels' test step; the whole suite runs once
per top commit, before a push (`check_push.py`).

Why this exists: `python3 -m unittest discover` only finds tests under directories that
have an __init__.py. Only lib/ does — hooks/ and each skill's scripts/ don't, since
they're not Python packages, they're standalone CLI scripts — so discover silently runs
a fraction of the suite with no error. This runner doesn't rely on package discovery at
all: it just finds every test_*.py by filename and runs each one exactly the way
CLAUDE.md already tells you to run a single file (`python3 <path>`), one subprocess per
file, so a file's own sys.path bootstrap is exercised for real too.

**Every run prints seconds per file, slowest first.** That is not a nicety. This suite has
two files that cost minutes rather than seconds, and for a long time nothing said so — so
`--slow` got run in an edit-test loop where it had no business being, and one session spent
about forty of its seventy-six minutes waiting for the same six-minute file five times.
A cost you cannot see is a cost you repeat.

The `*_slow.py` split, and what each one actually costs:

  * `lib/diagram/test_place_slow.py` — ~2 minutes. Every assertion is a real callout-placement
    search: dozens of d2 compiles, each measured in a real browser. Nothing here can be faked
    down without testing the fake instead. Run it when you touch `place.py`, the harness
    geometry, or anything that moves a callout — not otherwise.
  * `hooks/test_paste_gate_slow.py` — ~36 seconds of real `time.sleep()` in a subprocess that
    cannot be mocked from the test.

So: iterate with a filter (`run_tests.py compact` is well under a second), run the plain fast
suite before you call something done, and run `--slow` once at the end if you touched what it
covers. Not in a loop. Ever.
"""
import ast
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import testbox

REPO_ROOT = Path(__file__).resolve().parent
RAN_RE = re.compile(r"Ran (\d+) tests?")

# Seconds above which a file is called out in the summary. Roughly "you would notice this in
# an edit-test loop": the whole fast suite in parallel is about this, so any single file at
# this cost is the thing setting the pace.
SLOW_ENOUGH_TO_MENTION = 5.0

# Per-file seconds from the last run, so the next one knows what to shard. Gitignored: it is
# a cache of this machine's timings, not a fact about the repo.
TIMINGS = REPO_ROOT / ".run_tests_timings.json"
# The last passing run, so an unchanged repo is not tested twice. See `_fingerprint`.
PASSED = REPO_ROOT / ".run_tests_passed.json"

# Seconds above which a file is split into one job per test class. Comfortably above the
# handful of files in the 5-8s range, so only the genuinely dominant ones are split.
SHARD_OVER = 9.0


def find_test_files(include_slow, patterns):
    for path in sorted(REPO_ROOT.rglob("test_*.py")):
        if "__pycache__" in path.parts:
            continue
        if path.name.endswith("_slow.py") and not include_slow:
            continue
        rel = str(path.relative_to(REPO_ROOT))
        if patterns and not any(p in rel for p in patterns):
            continue
        yield path


# What the session running the suite says about itself, which no test may read by accident:
# whether the review-ui mod draws here comes from the session id and a mark file (lib/ui.py),
# so a suite run from a marked session once failed five files nowhere else.
SESSION_ENV = re.compile(r"^(?:CLAUDE_CODE_SESSION_ID|REVIEW_UI_\w+)$")


# A folder for the tests' temp files, named in this gitignored file: on a machine whose security
# scanners skip some folder, the throwaway repos, worktrees and browser profiles the tests make
# go there instead of the system temp folder, which the scanners inspect file by file. Measured
# on such a machine: the whole suite about 11% faster. Without the file, nothing changes.
TMPDIR_FILE = REPO_ROOT / ".run_tests_tmpdir"


def test_tmpdir():
    """The folder `.run_tests_tmpdir` names, made if missing; None without the file."""
    try:
        folder = Path(os.path.expanduser(TMPDIR_FILE.read_text(encoding="utf-8").strip()))
    except OSError:
        return None
    if not folder.is_absolute():
        return None
    folder.mkdir(parents=True, exist_ok=True)
    return folder


# No test may reach the person's real GitLab. A `glab` that refuses goes first on every test's
# PATH, and the test that called it fails, naming the call: a test that left GitLab unstubbed
# talked to the real instance with the real login, and hung the whole run whenever that call
# hung. A test that puts a fake `glab` of its own on its PATH finds that one instead. The test
# box has no `glab` at all, so this is also the Mac answering as the box does.
NO_GLAB = REPO_ROOT / ".cache" / "no-glab"
REACHED_ENV = "AGENT_SKILLS_GLAB_REACHED"
NO_GLAB_SCRIPT = f"""#!/bin/sh
echo "$*" >> "${REACHED_ENV}"
echo "a test reached the real glab: stub it" >&2
exit 1
"""


def no_glab():
    """The folder holding the refusing `glab`, written where missing or out of date."""
    stub = NO_GLAB / "glab"
    try:
        if stub.read_text(encoding="utf-8") == NO_GLAB_SCRIPT:
            return NO_GLAB
    except OSError:
        pass
    NO_GLAB.mkdir(parents=True, exist_ok=True)
    stub.write_text(NO_GLAB_SCRIPT, encoding="utf-8")
    stub.chmod(0o755)
    return NO_GLAB


def run_one(job, tmpdir=None):
    path, klass = job
    argv = [sys.executable, str(path)] + ([klass] if klass else [])
    env = {k: v for k, v in os.environ.items() if not SESSION_ENV.match(k)}
    if tmpdir:
        env["TMPDIR"] = f"{tmpdir}/"
    env["PATH"] = f"{no_glab()}{os.pathsep}{env.get('PATH', '')}"
    fd, reached = tempfile.mkstemp(prefix="glab-reached-", dir=tmpdir)
    os.close(fd)
    env[REACHED_ENV] = reached
    try:
        started = time.monotonic()
        proc = subprocess.run(argv, capture_output=True, text=True, env=env)
        seconds = time.monotonic() - started
        with open(reached, encoding="utf-8") as fh:
            calls = fh.read().splitlines()
    finally:
        os.unlink(reached)
    if calls:
        proc = subprocess.CompletedProcess(
            proc.args, proc.returncode or 1, proc.stdout,
            proc.stderr + "\nreached the real glab, which no test may: "
            + "; ".join(f"glab {c}" for c in calls) + "\n")
    return job, proc, seconds


def _classes(path):
    """The TestCase class names in `path`, for sharding it across the pool.

    Restricted to the `Test*` naming convention every file here follows: a shard is run as
    `python3 <file> <ClassName>`, and handing unittest the name of a plain helper class fails
    the whole shard for no reason.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return []
    return [n.name for n in tree.body
            if isinstance(n, ast.ClassDef) and n.name.startswith("Test")]


def _fingerprint(mode):
    """What a pass was a pass OF: every non-ignored file, plus the two tools that lay it out.

    Git decides what counts, so the ignore rules are not duplicated here and a build artefact
    can never fingerprint as source. The d2 and node versions are in it because most of this
    suite is a claim about what THEY produce — an upgraded d2 with unchanged source is a
    different repo as far as these tests are concerned.

    Returns None when git cannot answer, and a None fingerprint is never cached and never
    honoured: not knowing what changed is not the same as knowing nothing did.

    Three lists, because no single one of them sees the whole working tree. `ls-files -s`
    names the INDEX blob of every tracked file, so it covers anything committed or staged
    and is blind to an edit that has not been staged — which is the normal state of an edit
    loop, and is how a whole suite once got skipped as "nothing has changed" with eight
    modified files in the tree. `diff --name-only` is exactly that missing set, hashed by
    content like the untracked files are.
    """
    listed = []
    for cmd in (["git", "ls-files", "-s"],
                ["git", "ls-files", "--others", "--exclude-standard"],
                ["git", "diff", "--name-only"]):
        proc = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO_ROOT)
        if proc.returncode != 0:
            return None
        listed.append(proc.stdout)
    digest = hashlib.sha256(listed[0].encode())
    # Untracked files have no blob in the index, and an unstaged edit's index blob is the
    # stale one, so both are hashed by content here.
    for rel in sorted(line for group in listed[1:]
                      for line in group.split("\n") if line.strip()):
        path = REPO_ROOT / rel
        digest.update(rel.encode())
        try:
            digest.update(path.read_bytes())
        except FileNotFoundError:
            # A tracked file deleted but not yet committed. Digesting the fact keeps the
            # cache working across a normal deletion; returning None here would instead
            # disable it for the whole repo until the deletion was committed.
            digest.update(b"<deleted>")
        except OSError:
            return None
    for tool in (["d2", "--version"], ["node", "--version"]):
        proc = subprocess.run(tool, capture_output=True, text=True)
        digest.update((proc.stdout if proc.returncode == 0 else "missing").encode())
    return f"{mode}:{digest.hexdigest()}"


def _cached_pass(fingerprint):
    """The record of an identical run that passed TODAY, or None.

    Bounded to the day on purpose. A cache is a claim about the past, and `gates/__init__.py`
    is emphatic that a check which did not run must never report success — the fingerprint
    covers source and the two tool versions and cannot cover a browser that updated overnight
    or a font that changed. A day is the smallest bound that still kills the case this exists
    for: the same suite twice within one sitting.
    """
    if not fingerprint:
        return None
    try:
        record = json.loads(PASSED.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if record.get("fingerprint") != fingerprint:
        return None
    if record.get("day") != time.strftime("%Y-%m-%d"):
        return None
    return record


def record_timings(earlier, per_file):
    """The timings to keep: `earlier` with this run's files' seconds replacing theirs."""
    return {**earlier, **{r: round(a["seconds"], 2) for r, a in per_file.items()}}


def _load_timings():
    try:
        return json.loads(TIMINGS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _jobs(files, timings):
    """One subprocess job per file, except slow files, which get one per test class.

    The pool parallelises across FILES, which does nothing for a suite whose cost is
    concentrated in one of them: `test_visualize.py` is 41 tests of about a second each —
    no hot spot to optimise, just per-test overhead 41 times — and it alone set the pace for
    every diagram change. unittest takes a class name on the command line, so a slow file can
    be split at no cost to the tests themselves.

    Which files are slow is read from the previous run rather than guessed. A file with one
    class cannot be split, and splitting a fast one would only pay interpreter startup per
    shard, so the threshold keeps this to the handful that earn it.
    """
    jobs = []
    for path in files:
        rel = str(path.relative_to(REPO_ROOT))
        classes = _classes(path)
        if timings.get(rel, 0) > SHARD_OVER and len(classes) > 1:
            jobs += [(path, name) for name in classes]
        else:
            jobs.append((path, None))
    return jobs


def _rel(path):
    return str(Path(path).relative_to(REPO_ROOT))


def box_jobs(jobs):
    """The jobs the test box takes (testbox.py), with a split file counted by its classes."""
    return [j for j in _split_for_box(jobs) if testbox.in_box(_rel(j[0]), j[1])] \
        if testbox.usable() else []


def _split_for_box(jobs):
    """A file only some of whose classes run in the box is run class by class, always."""
    out = []
    for path, klass in jobs:
        if klass is None and _rel(path) in testbox.SPLIT:
            out += [(path, name) for name in _classes(path)]
        else:
            out.append((path, klass))
    return out


def _module_index(files):
    """Every importable name in the repo -> the file it resolves to.

    Two naming schemes, because the repo has two kinds of Python. `lib/` is a package and is
    imported by its dotted path. A skill's `scripts/` is not a package — those are standalone
    CLI scripts that import their neighbours by bare name — so a bare name is registered per
    directory, and resolution prefers the importer's own directory before falling back.
    """
    index = {}
    for path in files:
        rel = path.relative_to(REPO_ROOT)
        if rel.parts[0] == "lib":
            dotted = ".".join(rel.with_suffix("").parts)
            index[dotted] = path
            # A package is imported by its directory name, so `lib.diagram.gates` has to
            # resolve to gates/__init__.py — without this, editing a package's __init__
            # selected nothing at all.
            if path.name == "__init__.py":
                index[dotted.rsplit(".__init__", 1)[0]] = path
        index[(path.parent, path.stem)] = path
    return index


def _conventional_test(path, files):
    """The `test_<module>.py` beside `path`, if the repo's naming convention has one.

    Needed because two things break the import graph. A CLI script is named with a hyphen
    (`paste-gate.py`, `resolve-target.py`) and its test loads it through importlib rather than
    `import`, so no edge exists to follow; and a module can be exercised by a test that never
    imports it by name. The convention is what ties them, so the convention is an edge.
    """
    candidate = path.with_name("test_" + path.stem.replace("-", "_") + ".py")
    return candidate if candidate in files else None


def _imports(path, index):
    """The repo files `path` imports directly, resolved through `index`.

    Relative imports have to be handled explicitly, and getting that wrong is not a cosmetic
    miss: `from . import palette` parses with `module=None`, so skipping those dropped nearly
    every edge inside `lib/diagram` and the selector silently under-ran. Under-selection is
    the one failure mode this whole feature must not have.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return set()
    out = set()

    def resolve(name, base):
        # `from lib.diagram import render` arrives as both "lib.diagram" and
        # "lib.diagram.render"; whichever resolves is the real one, and both are cheap to try.
        if name in index:
            out.add(index[name])
        if (base, name.split(".")[0]) in index:
            out.add(index[(base, name.split(".")[0])])

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            base = path.parent
            for _ in range(max(0, node.level - 1)):
                base = base.parent
            if node.module:
                resolve(node.module, base)
                for alias in node.names:
                    resolve(f"{node.module}.{alias.name}", base)
                    if node.level:
                        # `from .gates import size` -> gates/size.py
                        sub = base / node.module.replace(".", "/")
                        resolve_sub = (sub, alias.name)
                        if resolve_sub in index:
                            out.add(index[resolve_sub])
            else:
                # `from . import palette`
                for alias in node.names:
                    resolve(alias.name, base)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                resolve(alias.name, path.parent)
    return out - {path}


def import_graph(files):
    """Each of `files` -> the repo files it reaches in one step: its imports, and for a test,
    the module its name says it covers. Parsing every file is most of `_dependents`' cost, so
    a caller asking about many changes builds this once and passes it in."""
    index = _module_index(files)
    deps = {path: _imports(path, index) for path in files}
    for path in files:
        own = _conventional_test(path, files)
        if own:
            deps[own].add(path)
    return deps


def _dependents(changed, files, graph=None):
    """Test files that reach any of `changed`, directly or through other repo modules."""
    deps = graph if graph is not None else import_graph(files)
    reached = set(changed)
    while True:
        grown = {p for p, uses in deps.items() if uses & reached} | reached
        if grown == reached:
            return {p for p in reached if p.name.startswith("test_")}
        reached = grown


def changed_files(base="HEAD"):
    """Everything this working tree has touched relative to `base`, tracked or not."""
    out = set()
    for cmd in (["git", "diff", "--name-only", base],
                ["git", "ls-files", "--others", "--exclude-standard"]):
        proc = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO_ROOT)
        if proc.returncode == 0:
            out.update(line for line in proc.stdout.split("\n") if line.strip())
    return out


def _readers(rel, files):
    """The Python files a changed non-Python file reaches: every one that names it (a skill's
    SKILL.md, a script's JSON spec, a shell view the tests run), and for a file of a mod, the
    bridge that runs the mod's own tests. The names are read from the source, so a reader
    added later is found without anyone listing it here; a file nothing names is read by no
    test this selector can see, and declines. A name matches whole, so `SKILL.md` is found and
    over-selects, which costs seconds; no test here reaches a file by a glob of its kind,
    which would read it without naming it."""
    parts = Path(rel).parts
    out = set()
    if len(parts) > 2 and parts[0] == "mods":
        bridge = REPO_ROOT / "mods" / "test_mods.py"
        if bridge in files:
            out.add(bridge)
    name = re.compile(r"(?<![\w.-])" + re.escape(Path(rel).name) + r"(?![\w-])")
    for path in files:
        try:
            if name.search(path.read_text(encoding="utf-8", errors="replace")):
                out.add(path)
        except OSError:
            continue
    return out


# Documents: prose and pictures for people. One a test names (a SKILL.md, an icon table) selects
# that test like any other file; one nothing names is read by no test and selects nothing, rather
# than declining into the whole suite on every README edit.
DOCS = {".md", ".png", ".jpg", ".jpeg", ".gif", ".webp"}


def select_changed(files, base="HEAD"):
    """(test files to run, note) for `--changed`, or (None, why) to fall back to everything.

    Deliberately conservative: this is a way to make the edit loop cheap, NOT a substitute for
    the whole suite before a push. A non-Python file counts as a change to every Python
    file that names it (`_readers`); anything it cannot reason about — a file nothing names,
    a changed file it cannot resolve — makes it decline and run everything, because a
    selector that quietly skips the test that would have failed is worse than a slow one.
    """
    raw = changed_files(base)
    if not raw:
        # An EMPTY selection, not a refusal. "There are no edits" and "I cannot map an edit"
        # are different answers and only the second one deserves the whole suite: this used to
        # conflate them, so asking what a clean tree affects cost 125 seconds to be told
        # "everything", which is the one answer that cannot be true.
        return set(), f"nothing changed against {base}"
    changed, unknown = set(), []
    for rel in raw:
        path = (REPO_ROOT / rel).resolve()
        if path.suffix == ".py" and path in files:
            changed.add(path)
        elif path.suffix == ".js" and "diagram" in path.parts:
            # measure.js is the browser side of the diagram gates; nothing imports it, so
            # attribute it to the module that shells out to it.
            browser = REPO_ROOT / "lib" / "diagram" / "browser.py"
            if browser in files:
                changed.add(browser)
        else:
            readers = _readers(rel, files)
            if not readers and path.suffix.lower() not in DOCS:
                unknown.append(rel)
            changed |= readers
    if unknown:
        return None, f"{len(unknown)} changed file(s) it cannot map ({unknown[0]}, ...)"
    return _dependents(changed, files), f"{len(changed)} changed module(s)"


FAILED_RE = re.compile(r"^(FAIL|ERROR): (\S+) \(([^)]*)\)", re.M)


def failed_tests(output):
    """Each failed test in unittest's output, with the first line of what it raised."""
    out = []
    blocks = re.split(r"^={50,}\n", output, flags=re.M)
    for block in blocks:
        m = FAILED_RE.match(block)
        if not m:
            continue
        raised = [ln for ln in block.splitlines() if re.match(r"^\w+(?:\.\w+)*(?:Error|Exception|Exit)\b", ln)]
        what = raised[-1].strip() if raised else ""
        out.append(f"{m.group(1)} {'.'.join(m.group(3).split('.')[-2:])}" + (f" — {what[:160]}" if what else ""))
    return out


def main():
    args = sys.argv[1:]
    include_slow = "--slow" in args
    only_changed = "--changed" in args
    base = "HEAD"
    for arg in args:
        if arg.startswith("--changed-since="):
            only_changed, base = True, arg.split("=", 1)[1]
    force = "--force" in args
    workers = 8
    patterns = []
    for arg in args:
        if arg in ("--slow", "--changed", "--force", "--host") or arg.startswith("--changed-since="):
            continue
        if arg.startswith("-j"):
            try:
                workers = max(1, int(arg[2:] or 1))
            except ValueError:
                print(f"not a worker count: {arg} (use -j4)", file=sys.stderr)
                return 1
        else:
            patterns.append(arg)

    files = list(find_test_files(include_slow, patterns))
    if not files:
        print(f"no test_*.py files matched {patterns or 'anything'}", file=sys.stderr)
        return 1

    selected = None
    if only_changed:
        every = [p for p in REPO_ROOT.rglob("*.py") if "__pycache__" not in p.parts]
        selected, note = select_changed(set(every), base)
        if selected is None:
            print(f"--changed: running everything ({note})")
        else:
            files = [p for p in files if p in selected]
            print(f"--changed: {len(files)} of {len(selected)} affected test file(s) "
                  f"from {note}")
            if not files:
                print("nothing to run (the whole suite runs before a push: check_push.py)")
                return 0

    # Only a WHOLE run is cached, and only a passing one. A filtered or narrowed `--changed`
    # run proves less than it looks like it does, and caching it would let a green from four
    # files stand in for the suite. A `--changed` run that declined IS the whole run, so it
    # reads and writes the record too: the hook's run after a full pass is then free.
    whole = not patterns and selected is None
    on_host = "--host" in args
    fingerprint = _fingerprint(("slow" if include_slow else "fast") + ("-host" if on_host else "")) \
        if whole else None
    if not force:
        record = _cached_pass(fingerprint)
        if record:
            print(f"skipped: {record['files']} file(s), {record['tests']} tests passed at "
                  f"{record['at']} today, and nothing has changed since — not source, not d2, "
                  f"not node.\nrun `python3 run_tests.py --force` to run them anyway.")
            return 0

    # Threads, not processes: each one only waits on a subprocess, so the GIL is never the
    # constraint. Serial, the fast suite is the sum of 26 files; in parallel it is the longest
    # one. Files that drive a browser run concurrently too — Chrome instances are independent.
    timings = _load_timings()
    jobs = _jobs(files, timings)
    wall = time.monotonic()
    tmpdir = test_tmpdir()
    boxed = [] if on_host else box_jobs(jobs)
    if boxed and not testbox.up():
        boxed = []                                # no box to be had: everything on the Mac
    if boxed:
        jobs = _split_for_box(jobs)
        boxed = [j for j in jobs if testbox.in_box(_rel(j[0]), j[1])]
    here = [j for j in jobs if j not in boxed]
    box_out = []
    with ThreadPoolExecutor(max_workers=workers + 1) as pool:
        box_run = pool.submit(lambda: testbox.run([(_rel(p), k) for p, k in boxed], REPO_ROOT))
        results = list(pool.map(lambda job: run_one(job, tmpdir), here))
        box_out = box_run.result()
    if boxed and box_out is None:                 # the box failed to answer: run them here
        results += [run_one(job, tmpdir) for job in boxed]
    elif boxed:
        results += [((REPO_ROOT / rel, klass),
                     subprocess.CompletedProcess([rel], rc, out, err), seconds)
                    for rel, klass, rc, out, err, seconds in box_out]
    wall = time.monotonic() - wall

    # Shards are reported as the one file they came from: the split is a scheduling detail,
    # and eight lines saying `test_visualize.py [SomeClass]` would bury the other 26 files.
    per_file = {}
    failures = []
    total_tests = 0
    for (path, klass), proc, seconds in results:
        rel = str(path.relative_to(REPO_ROOT))
        m = RAN_RE.search(proc.stdout + proc.stderr)
        ok = proc.returncode == 0
        agg = per_file.setdefault(rel, {"seconds": 0.0, "wall": 0.0, "tests": 0, "ok": True,
                                        "shards": 0})
        agg["seconds"] += seconds                 # summed: what the file costs to run
        agg["wall"] = max(agg["wall"], seconds)   # longest shard: what it costs in parallel
        agg["shards"] += 1
        agg["tests"] += int(m.group(1)) if m else 0
        agg["ok"] &= ok
        if m:
            total_tests += int(m.group(1))
        if not ok:
            failures.append((f"{rel} [{klass}]" if klass else rel, proc.stdout, proc.stderr))

    for rel in sorted(per_file):
        agg = per_file[rel]
        split = f"  ({agg['shards']} shards)" if agg["shards"] > 1 else ""
        print(f"{'ok  ' if agg['ok'] else 'FAIL'}  {agg['wall']:6.1f}s  {rel}  "
              f"({agg['tests']} tests){split}")

    # Merged into what earlier runs recorded: a filtered or `--changed` run times only the
    # files it ran, and writing just those used to drop every other file's seconds, so the next
    # whole run split none of the slow files and waited on them whole.
    try:
        TIMINGS.write_text(json.dumps(record_timings(timings, per_file), indent=0),
                           encoding="utf-8")
    except OSError:
        pass                                      # a missing cache only costs one slow run

    print()
    worst = sorted(per_file.items(), key=lambda kv: -kv[1]["wall"])[:3]
    listed = ", ".join(f"{r} {a['wall']:.0f}s" for r, a in worst
                       if a["wall"] >= SLOW_ENOUGH_TO_MENTION)
    if listed:
        print(f"slowest: {listed}")

    if failures:
        for rel, stdout, stderr in failures:
            print(f"=== {rel} ===")
            print(stdout + stderr)
        # Last, so a `tail` of the run says which tests failed and how, without a second run.
        print("failed:")
        for rel, stdout, stderr in failures:
            for line in failed_tests(stdout + stderr) or ["(no test named: the file itself failed)"]:
                print(f"  {rel}: {line}")
        print(f"FAILED: {len(failures)}/{len(jobs)} job(s), {total_tests} tests attempted "
              f"in {wall:.0f}s")
        return 1

    if fingerprint:
        try:
            PASSED.write_text(json.dumps(
                {"fingerprint": fingerprint, "day": time.strftime("%Y-%m-%d"),
                 "at": time.strftime("%H:%M"), "files": len(files), "tests": total_tests}),
                encoding="utf-8")
        except OSError:
            pass                                  # a missing cache only costs one full run

    print(f"OK: {len(files)} file(s), {total_tests} tests in {wall:.0f}s"
          + ("" if include_slow else " (fast only — see the docstring before you add --slow)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
