"""A repository's checks, run once per fix, before anyone sees it, and never again at push.

A fix is checked when it is made: the repository's own commit and push hooks, run ahead of
time, and the fast local checks of each part of the repository it touches (a frontend, a
backend: lint, types, unit tests). Slow suites, end-to-end tests above all, are CI's: a fix
should reach the person in a short loop. The push that follows commits and pushes without
them, but only where the files it commits are exactly the files the checks ran on, and the
checks are the ones that ran. Anything else stops the push and says why.

What a repository's hooks do is the agent's reading of them (a hook framework's config, a
script), so it is worked out once and kept: the record names, per hook, what it does and the
command that runs it ahead of time, and per part of the repository the paths it covers and
the command that checks it. Beside it, the hashes of the
files it was read from (the hooks git runs and the files the record names), so a changed hook
reads as a stale record instead of being trusted.

A reading can be wrong, the classic case a hook limited to the files a commit touches, which
run ahead of time checks nothing. So a new record is trusted only after one push that ran
the real hooks as well and found nothing the early run missed; until then, a push runs them.

The record lives in the repository's git folder: never committed, shared by every worktree of
the clone, gone with it.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time

# The hooks `--no-verify` skips: the ones this module runs ahead of time, or itself.
GATED = ("pre-commit", "commit-msg", "pre-push")

# Files a hook setup is commonly read from, offered to the agent as a start.
LIKELY = ("lefthook.yml", "lefthook.yaml", ".lefthook.yml", ".lefthook.yaml",
          "lefthook-local.yml", ".pre-commit-config.yaml", ".husky", "package.json",
          "pyproject.toml", "Makefile", "justfile", ".gitlab-ci.yml")

RECORD = "rework-mr-checks.json"
TAIL = 40           # lines of a failing check's output kept for the card and the agent
PASSES = 3          # runs of the hooks until the files they rewrite stop changing


class Stop(Exception):
    """A push or a check that cannot go on; the message says why and what to do."""


def git(root, *args, env=None, stdin=None):
    return subprocess.run(["git", "-C", root, *args], capture_output=True, text=True,
                          env=env, input=stdin)


def _out(root, *args):
    r = git(root, *args)
    return r.stdout.strip() if r.returncode == 0 else None


def _path(root, p):
    return p if os.path.isabs(p) else os.path.join(root, p)


def record_path(root):
    return os.path.join(_path(root, _out(root, "rev-parse", "--git-common-dir")), RECORD)


def hooks_dir(root):
    return _path(root, _out(root, "rev-parse", "--git-path", "hooks"))


def present_hooks(root):
    """{event: path} of the gated hooks git would run here."""
    d = hooks_dir(root)
    out = {}
    for ev in GATED:
        p = os.path.join(d, ev)
        if os.path.isfile(p) and os.access(p, os.X_OK):
            out[ev] = p
    return out


def _hash(path):
    """A file's hash, a folder's over its files, None for neither."""
    if os.path.isdir(path):
        h = hashlib.sha256()
        for base, dirs, files in sorted(os.walk(path)):
            dirs.sort()
            for f in sorted(files):
                h.update(f.encode())
                h.update((_hash(os.path.join(base, f)) or "").encode())
        return h.hexdigest()[:16]
    try:
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()[:16]
    except OSError:
        return None


def fingerprint(root, based_on):
    return {
        "hooks_path": _out(root, "config", "--get", "core.hooksPath"),
        "hooks": {ev: _hash(p) for ev, p in present_hooks(root).items()},
        "files": {p: _hash(_path(root, p)) for p in sorted(set(based_on))},
    }


def digest(record):
    """Which record a check ran under: its readings and what they were read from."""
    keep = {k: record.get(k) for k in ("hooks", "tests", "fingerprint")}
    return hashlib.sha256(json.dumps(keep, sort_keys=True).encode()).hexdigest()[:16]


def load(root):
    try:
        with open(record_path(root)) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _write(root, record):
    p = record_path(root)
    tmp = p + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(record, fh, indent=2)
    os.replace(tmp, p)


def status(root):
    """(record, None) for a record that still holds, else (record or None, why not)."""
    rec = load(root)
    if not rec:
        return None, "no record of this repository's checks yet"
    if rec.get("wrong"):
        return rec, f"the record was found wrong: {rec['wrong']}"
    old = rec.get("fingerprint") or {}
    now = fingerprint(root, list((old.get("files") or {})))
    if now["hooks_path"] != old.get("hooks_path"):
        return rec, "core.hooksPath changed"
    for ev in sorted(set(now["hooks"]) | set(old.get("hooks") or {})):
        a, b = (old.get("hooks") or {}).get(ev), now["hooks"].get(ev)
        if a != b:
            return rec, (f"a {ev} hook was added" if a is None else
                         f"the {ev} hook was removed" if b is None else f"the {ev} hook changed")
    for p, h in sorted((old.get("files") or {}).items()):
        if now["files"].get(p) != h:
            return rec, f"{p} changed"
    return rec, None


def store(root, record):
    """Keep the agent's reading of the checks, with the hashes of what it was read from.
    Raises ValueError for a record that leaves a hook out or is malformed."""
    hooks = record.get("hooks")
    tests = record.get("tests")
    if not isinstance(hooks, list) or not isinstance(tests, list):
        raise ValueError('the record needs "hooks" and "tests", both lists')
    named = {}
    for h in hooks:
        ev = h.get("event")
        if ev not in GATED:
            raise ValueError(f"hook event {ev!r}: only {', '.join(GATED)} are recorded")
        if not (h.get("does") or "").strip():
            raise ValueError(f'{ev}: "does" says what the hook checks or changes')
        if not isinstance(h.get("rewrites"), bool):
            raise ValueError(f'{ev}: "rewrites" is true where the hook changes files')
        if h.get("early") is not None and not str(h["early"]).strip():
            raise ValueError(f'{ev}: "early" is a command, or null')
        if h.get("covered") and h.get("early"):
            raise ValueError(f'{ev}: a hook the tests cover has no early run of its own')
        named[ev] = h
    missing = sorted(set(present_hooks(root)) - set(named))
    if missing:
        raise ValueError(f"no reading of the {', '.join(missing)} hook here")
    extra = sorted(set(named) - set(present_hooks(root)))
    if extra:
        raise ValueError(f"git runs no {', '.join(extra)} hook here, so a commit runs none: "
                         "leave it out of the record. A hook framework's config with nothing "
                         "installed into git (no core.hooksPath, nothing in .git/hooks) is no "
                         "hook of this checkout")
    parts = []
    for t in tests:
        name, paths, cmd = t.get("name"), t.get("paths"), t.get("run")
        if not (name and str(name).strip() and cmd and str(cmd).strip()):
            raise ValueError('each test part has a "name" and a "run" command')
        if not isinstance(paths, list) or not paths or not all(isinstance(x, str) and x for x in paths):
            raise ValueError(f'{name}: "paths" lists the globs the part covers ("**" for all)')
        parts.append({"name": name, "paths": paths, "run": cmd, "does": t.get("does") or ""})
    based_on = [p for p in record.get("based_on") or [] if p]
    out = {"hooks": [named[ev] for ev in GATED if ev in named],
           "tests": parts,
           "fingerprint": fingerprint(root, based_on),
           "trusted": False, "stored": int(time.time())}
    _write(root, out)
    return out


def mark_wrong(root, why):
    rec = load(root)
    if rec:
        rec["wrong"] = why
        _write(root, rec)


def trust(root):
    rec = load(root)
    if rec and not rec.get("trusted"):
        rec["trusted"] = True
        _write(root, rec)


def brief(root, why):
    """What the agent reads to write the record."""
    hooks = present_hooks(root)
    likely = [p for p in LIKELY if os.path.exists(os.path.join(root, p))]
    hp = _out(root, "config", "--get", "core.hooksPath")
    lines = [f"Checks: {why}. Work out once what a commit and a push of this repository check, "
             "and store it; it is kept until one of the files it is based on changes.",
             "Record the checks the project defines, whether or not their tools are installed "
             "in this checkout, and install nothing yourself: a part whose tools are missing "
             "fails its run and the fix card says so. Then tell the user what is missing and "
             "the command that sets it up (the project's install step), and let them decide.",
             ""]
    if hooks:
        lines.append("Hooks git runs here" + (f" (core.hooksPath = {hp})" if hp else "") + ":")
        lines += [f"  {ev}: {p}" for ev, p in hooks.items()]
    else:
        lines.append("No pre-commit, commit-msg or pre-push hook here.")
    if likely:
        lines += ["", "Files that commonly define hooks and tests, present here: " + ", ".join(likely)]
    lines += [
        "",
        "Read each hook and the config it runs. Then store the reading:",
        "",
        "  python3 $SD/threads.py checks --set - <<'CHECKS_EOF'",
        '  {"hooks": [{"event": "pre-commit", "does": "<what it checks or changes>",',
        '              "rewrites": <true where it changes files>, "early": "<command>",',
        '              "covered": <true where the tests below do all it checks>}, …],',
        '   "tests": [{"name": "<a part of the repository, e.g. frontend>", "paths": ["<glob>", …],',
        '              "run": "<its fast local checks, from the root>", "does": "<what they are>"}, …],',
        '   "based_on": ["<every file the reading came from>", …]}',
        "  CHECKS_EOF",
        "",
        "- One entry per hook listed above, none for a hook that is not there.",
        "- `early` runs the hook's checks before the commit exists, with the fix staged, and "
        "must check everything the real hook would at commit or push. `git hook run "
        "pre-commit` is right for a pre-commit hook that checks the staged files. A hook "
        "limited to the files of the pushed commits (a glob, `{push_files}`) skips, run early, "
        "the parts the fix touches: give the command that runs it on the fix's files, which "
        f"${FIX_FILES} holds one per line (lefthook: `lefthook run pre-push $(printf -- "
        f"'--file %s ' ${FIX_FILES})`), or on all files. Null where a hook can only run at its moment (commit-msg "
        "needs the message): it then runs then.",
        "- `tests`: per part of the repository with checks of its own, the project's fast local "
        "checks for it: lint, format, types, unit tests, a build where it is quick. Take the "
        "variant the project offers without end-to-end tests where it has one; slow suites "
        "are CI's, and a fix should reach the person in minutes. `paths` are globs (`**` "
        "crosses folders). A fix runs the parts whose paths it touches, and every part where "
        "it touches a file no part covers. An empty list where there is nothing to run.",
        "- Nothing runs twice: where a part's checks already do all a hook does, mark the "
        "hook `covered`, with no `early`; where they do some of it, `early` may leave that out.",
        "- `based_on`: the configs and scripts the hooks and tests are read from (the hooks "
        "themselves are hashed already). A change to any of them means reading again.",
    ]
    return "\n".join(lines)


def _clean_env():
    """The environment without git's per-repository variables, which a hook or a test that
    builds its own repositories must not inherit."""
    env = dict(os.environ)
    for k in ("GIT_DIR", "GIT_INDEX_FILE", "GIT_WORK_TREE", "GIT_OBJECT_DIRECTORY",
              "GIT_PREFIX", "GIT_COMMON_DIR"):
        env.pop(k, None)
    return env


def _tools_env(root, **extra):
    """The environment a repository's checks and hooks run in: `_clean_env`, with the
    repository's own `node_modules/.bin` first on PATH where it has one, as `npm run` gives
    a package's scripts. A JavaScript project's hook manager and linters live there, not on
    the session's PATH, so an early command such as `lefthook run pre-push` was not found:
    the person's terminal may have it on PATH, the agent's shell does not."""
    env = {**_clean_env(), **extra}
    bin_dir = os.path.join(root, "node_modules", ".bin")
    if os.path.isdir(bin_dir):
        env["PATH"] = bin_dir + os.pathsep + env.get("PATH", "")
    return env


def stage(root, new=()):
    """The fix staged: every tracked change, and the new files it adds."""
    r = git(root, "add", "-u")
    if r.returncode == 0 and new:
        r = git(root, "add", "--", *new)
    if r.returncode != 0:
        raise Stop(f"could not stage the fix: {r.stderr.strip()}")


def snapshot(root, new=()):
    """The tree a commit of the fix would have, read without touching the index."""
    index = _path(root, _out(root, "rev-parse", "--git-path", "index"))
    with tempfile.TemporaryDirectory() as d:
        tmp = os.path.join(d, "index")
        if os.path.exists(index):
            shutil.copy(index, tmp)
        env = {**_clean_env(), "GIT_INDEX_FILE": tmp}
        for args in (["add", "-u"], ["add", "--", *new] if new else None):
            if args and git(root, *args, env=env).returncode != 0:
                return None
        r = git(root, "write-tree", env=env)
        return r.stdout.strip() if r.returncode == 0 else None


def untracked(root):
    out = _out(root, "ls-files", "--others", "--exclude-standard") or ""
    return [p for p in out.splitlines() if p]


# The files the fix changes, one per line, for an early command to limit itself to what the
# real hook would check on the push of the fix.
FIX_FILES = "REWORK_FIX_FILES"


def _run(root, cmd):
    files = _out(root, "diff", "--cached", "--name-only", "HEAD") or ""
    r = subprocess.run(["sh", "-c", cmd], cwd=root, capture_output=True, text=True,
                       env=_tools_env(root, **{FIX_FILES: files}))
    text = (r.stdout + r.stderr).rstrip()
    return r.returncode == 0, "\n".join(text.splitlines()[-TAIL:])


def _glob(pattern):
    """A path glob as a regex: `**` crosses folders, `*` and `?` stay within one."""
    out, i = "", 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out, i = out + "(?:.*/)?", i + 3
        elif pattern.startswith("**", i):
            out, i = out + ".*", i + 2
        elif pattern[i] == "*":
            out, i = out + "[^/]*", i + 1
        elif pattern[i] == "?":
            out, i = out + "[^/]", i + 1
        else:
            out, i = out + re.escape(pattern[i]), i + 1
    return re.compile(out + r"\Z")


def parts_for(record, files):
    """The test parts a fix of `files` runs: those whose paths it touches, and all of them
    where it touches a file no part covers (a root config can affect every part)."""
    parts = record.get("tests") or []
    def covers(p, f):
        return any(_glob(g).match(f) for g in p["paths"])
    if any(not any(covers(p, f) for p in parts) for f in files):
        return parts
    return [p for p in parts if any(covers(p, f) for f in files)]


def ahead(hook):
    """Whether a hook's checks ran when the fix was made: its early run, or the tests'."""
    return bool(hook.get("early") or hook.get("covered"))


def run(root, record, new=()):
    """Run the checks on the fix: the early commands of the hooks that change files, again
    until they stop changing any, then the other hooks' and the checks of the parts of the
    repository it touches, once. Returns a
    stamp: `{"ok", "tree", "record", "ran", "new"}`, with `"failed"` and `"output"` where
    one failed."""
    new = list(new)
    stamp = {"ok": False, "tree": None, "record": digest(record), "ran": [], "new": new}
    stage(root, new)
    early = [h for h in record["hooks"] if h.get("early")]
    for _ in range(PASSES):
        before = _out(root, "write-tree")
        for h in (h for h in early if h["rewrites"]):
            ok, out = _run(root, h["early"])
            if not ok:
                return {**stamp, "failed": f"{h['event']} hook", "output": out}
        stage(root, new)
        if _out(root, "write-tree") == before:
            break
    else:
        return {**stamp, "failed": "hooks", "output": f"the hooks still changed files after {PASSES} runs"}
    tree = _out(root, "write-tree")
    steps = [(f"{h['event']} hook", h["early"]) for h in early if not h["rewrites"]]
    files = [f for f in (_out(root, "diff", "--cached", "--name-only", "HEAD") or "").splitlines() if f]
    steps += [(f"{p['name']} checks", p["run"]) for p in parts_for(record, files)]
    ran = [f"{h['event']} hook" for h in early if h["rewrites"]]
    for name, cmd in steps:
        ok, out = _run(root, cmd)
        if not ok:
            return {**stamp, "ran": ran, "failed": name, "output": out}
        ran.append(name)
    stage(root, new)
    if _out(root, "write-tree") != tree:
        changed = _out(root, "diff", "--cached", "--name-only", tree) or ""
        return {**stamp, "ran": ran, "failed": "checks",
                "output": "files changed while the checks ran: " + ", ".join(changed.split())}
    return {**stamp, "ok": True, "tree": tree, "ran": ran}


def hook_of(record, event):
    return next((h for h in record["hooks"] if h["event"] == event), None)


def _hook(root, event, *args, stdin=None):
    """Run a hook as git would, unless there is none. (ok, output)."""
    if event not in present_hooks(root):
        return True, ""
    with tempfile.TemporaryDirectory() as d:
        feed = []
        if stdin is not None:
            with open(os.path.join(d, "stdin"), "w") as fh:
                fh.write(stdin)
            feed = [f"--to-stdin={os.path.join(d, 'stdin')}"]
        r = subprocess.run(["git", "-C", root, "hook", "run", "--ignore-missing", *feed, event,
                            "--", *args], capture_output=True, text=True, env=_tools_env(root))
    text = (r.stdout + r.stderr).rstrip()
    return r.returncode == 0, "\n".join(text.splitlines()[-TAIL:])


def _subject(root, sha):
    return _out(root, "log", "-1", "--format=%s", sha) or ""


def commit_fixup(root, record, stamp, target):
    """Commit the staged fix as a fixup of `target`. The hooks run ahead of time are left
    out where the record is trusted; what has no early run runs now. Raises Stop."""
    stage(root, stamp.get("new") or [])
    if _out(root, "write-tree") != stamp["tree"]:
        raise Stop("the files to commit are not the ones the checks ran on")
    trusted = record.get("trusted")
    pre = hook_of(record, "pre-commit")
    if pre and not (trusted and ahead(pre)):
        ok, out = _hook(root, "pre-commit")
        if not ok:
            if ahead(pre):
                mark_wrong(root, "the pre-commit hook failed at commit though its early run passed")
            raise Stop(f"the pre-commit hook failed:\n{out}")
        stage(root, stamp.get("new") or [])
        if _out(root, "write-tree") != stamp["tree"]:
            mark_wrong(root, "the pre-commit hook changed files its early run had left")
            raise Stop("the pre-commit hook changed files the checks had passed; nothing was committed")
    gitdir = _path(root, _out(root, "rev-parse", "--git-dir"))
    msg = os.path.join(gitdir, "REWORK_FIXUP_MSG")
    with open(msg, "w") as fh:
        fh.write(f"fixup! {_subject(root, target)}\n")
    try:
        ok, out = _hook(root, "commit-msg", msg)
    finally:
        os.remove(msg)
    if not ok:
        raise Stop(f"the commit-msg hook refused the fixup message:\n{out}")
    r = git(root, "commit", "--no-verify", "--quiet", f"--fixup={target}", env=_clean_env())
    if r.returncode != 0:
        raise Stop(f"git commit failed: {(r.stdout + r.stderr).strip()}")


def skips_commit_hooks(record):
    """Whether commits made outside `commit_fixup` may skip the repository's commit hooks:
    only where the record is trusted and its pre-commit hook, if any, ran ahead of time.
    Their messages are checked afterwards either way (`check_messages`)."""
    pre = hook_of(record, "pre-commit")
    return bool(record.get("trusted")) and (not pre or ahead(pre))


def check_messages(root, base):
    """Run the commit-msg hook on each commit's message in `base..HEAD`, as git would have
    at commit. Raises Stop on the first it refuses."""
    for sha in (_out(root, "rev-list", "--reverse", f"{base}..HEAD") or "").split():
        with tempfile.TemporaryDirectory() as d:
            msg = os.path.join(d, "COMMIT_EDITMSG")
            with open(msg, "w") as fh:
                fh.write(_out(root, "log", "-1", "--format=%B", sha) or "")
            ok, out = _hook(root, "commit-msg", msg)
        if not ok:
            raise Stop(f"the commit-msg hook refused the message of {sha[:8]} "
                       f"(\"{_subject(root, sha)}\"); nothing is pushed:\n{out}")


def autosquash(root, target, tree):
    """Squash the fixups into their commits. The tree must come out as `tree`."""
    base = f"{target}^" if _out(root, "rev-parse", "--verify", "--quiet", f"{target}^") else "--root"
    env = {**_clean_env(), "GIT_SEQUENCE_EDITOR": "true", "GIT_EDITOR": "true"}
    r = git(root, "rebase", "-i", "--autosquash", base, env=env)
    if r.returncode != 0:
        git(root, "rebase", "--abort")
        raise Stop("squashing the fixup into its commit conflicts; the fixup commit is made, "
                   f"nothing is pushed:\n{(r.stdout + r.stderr).strip()[-2000:]}")
    if _out(root, "rev-parse", "HEAD^{tree}") != tree:
        raise Stop("after squashing, the branch's files are not the ones checked; nothing is pushed")


def push(root, record, before):
    """Force-push the branch to its upstream, `before` the commit it was rewritten from. The
    pre-push hook runs here only where the record is not trusted yet or has no early run for
    it. Raises Stop."""
    branch = _out(root, "symbolic-ref", "--short", "HEAD")
    if not branch:
        raise Stop("HEAD is detached: check out the MR's branch to push")
    up = _out(root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    if not up or "/" not in up:
        raise Stop(f"{branch} has no upstream to push to")
    remote, rbranch = up.split("/", 1)
    theirs = _out(root, "rev-parse", "@{u}")
    # What --force-if-includes stands for, without the reflogs it reads: what the branch on
    # the remote holds (a reviewer's applied suggestion, say) is in the branch rewritten, and
    # the push replaces exactly that.
    if theirs and git(root, "merge-base", "--is-ancestor", theirs, before).returncode != 0:
        raise Stop(f"{up} has commits your branch does not (an applied suggestion?): fetch "
                   "and take them in first; nothing is pushed")
    pre = hook_of(record, "pre-push")
    if pre and not (record.get("trusted") and ahead(pre)):
        url = _out(root, "remote", "get-url", "--push", remote) or remote
        head = _out(root, "rev-parse", "HEAD")
        ok, out = _hook(root, "pre-push", remote, url,
                        stdin=f"refs/heads/{branch} {head} refs/heads/{rbranch} {theirs or '0' * 40}\n")
        if not ok:
            if ahead(pre):
                mark_wrong(root, "the pre-push hook failed at push though its early run passed")
            raise Stop(f"the pre-push hook failed; nothing is pushed:\n{out}")
    lease = f"--force-with-lease=refs/heads/{rbranch}:{theirs}" if theirs else "--force-with-lease"
    r = git(root, "push", "--no-verify", lease, "--quiet", remote, f"HEAD:refs/heads/{rbranch}",
            env=_clean_env())
    if r.returncode != 0:
        raise Stop(f"git push failed:\n{(r.stdout + r.stderr).strip()}")
