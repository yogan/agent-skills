#!/usr/bin/env python3
"""Keep the owner's private work out of this public repo: every commit's added lines and its
message are checked against a list of words kept outside the repo.

    python3 private_terms.py check --staged          # the pre-commit hook
    python3 private_terms.py check --message <file>  # the commit-msg hook
    python3 private_terms.py check <rev>             # a commit already made (check_commits.py)
    python3 private_terms.py list
    python3 private_terms.py add block|watch <word>...   # only with the owner's yes
    python3 private_terms.py drop <word>...              # only with the owner's yes
    python3 private_terms.py where <word>...             # how often each is in the repo now

Two lists, because a word is one of two kinds:

- **block**: never in this repo (the private project's name, its services, colleagues'
  names). A hit refuses the commit and names the line.
- **watch**: fine on its own, but a hint that something was copied from the private project
  (its domain's words). A hit lets the commit through and prints the lines, to be looked at:
  invented test data, or lifted?

The list is `~/.config/agent-skills/private-terms`, or the file `git config
agent-skills.private-terms` names. Without it the check says so and passes: it cannot know
what is private. A word matches whole and in any case, so `qux` does not flag `quxes`;
one ending in `-` is a prefix, for ticket and document ids (`abc-` flags `ABC-123`).
Only lines a commit adds are checked: a word already in the repo flags when a line holding
it is written again, not before.

Before this, each session grepped its diff with words it typed by hand, different each time,
and one pass found a colleague's real name in a fixture only because that session happened to
think of it.
"""
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT = os.path.expanduser("~/.config/agent-skills/private-terms")
KINDS = ("block", "watch")
HEADER = """\
# Words to keep out of the agent-skills repo (see private_terms.py there). One per line,
# matched whole and in any case (one ending in `-` as a prefix). Kept outside the repo on purpose: never commit this file.
"""


def git(*args):
    r = subprocess.run(["git", *args], cwd=HERE, capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else ""


def list_path():
    return os.path.expanduser(git("config", "--get", "agent-skills.private-terms").strip()
                              or DEFAULT)


def load(path=None):
    """{"block": [...], "watch": [...]} from the list file, or None when there is none."""
    path = path or list_path()
    if not os.path.isfile(path):
        return None
    terms, kind = {k: [] for k in KINDS}, None
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            m = re.fullmatch(r"\[(\w+)\]", line)
            if m:
                kind = m.group(1) if m.group(1) in KINDS else None
            elif kind:
                terms[kind].append(line)
    return terms


def save(terms, path=None):
    path = path or list_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    body = HEADER + "".join(f"\n[{k}]\n" + "".join(f"{t}\n" for t in terms[k]) for k in KINDS)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)


def pattern(term):
    """A word matches whole; one ending in `-` is a prefix (`abc-` matches `ABC-123`)."""
    tail = "" if term.endswith("-") else r"(?![\w-])"
    return re.compile(r"(?<![\w-])" + re.escape(term) + tail, re.I)


def added_lines(diff):
    """(file, line number, text) for every line `diff` adds."""
    out, path, n = [], None, 0
    for line in diff.splitlines():
        if line.startswith("+++ "):
            path = line[4:].split("\t")[0]
            path = path[2:] if path.startswith("b/") else path
        elif line.startswith("@@"):
            m = re.search(r"\+(\d+)", line)
            n = int(m.group(1)) if m else 0
        elif line.startswith("+") and path:
            out.append((path, n, line[1:]))
            n += 1
        elif not line.startswith("-"):
            n += 1
    return out


def hits(lines, terms):
    """(kind, term, where, text) for each listed word in `lines` of (where, text)."""
    found = []
    for kind in KINDS:
        for term in terms[kind]:
            rx = pattern(term)
            found += [(kind, term, where, text) for where, text in lines if rx.search(text)]
    return found


def report(found):
    """Print the hits; 1 when any blocks."""
    blocks = [h for h in found if h[0] == "block"]
    for kind, term, where, text in found:
        print(f"{where}: {kind} word \"{term}\": {text.strip()[:160]}")
    if blocks:
        print(f"refused: {len(blocks)} line(s) hold a word of the block list. Replace them with "
              "invented names; a word that should not be on the list is the owner's to drop "
              "(`private_terms.py drop`).")
        return 1
    if found:
        print("watch words only: look at those lines. Invented test data passes; anything lifted "
              "from private work comes out. A word that keeps flagging fine text is the owner's "
              "to drop.")
    return 0


def check(argv):
    terms = load()
    if terms is None:
        print(f"private_terms: no list at {list_path()}, nothing checked", file=sys.stderr)
        return 0
    if argv[:1] == ["--staged"]:
        lines = [(f"{p}:{n}", t) for p, n, t in
                 added_lines(git("diff", "--cached", "--no-color", "--src-prefix=a/",
                                 "--dst-prefix=b/", "-U0"))]
    elif argv[:1] == ["--message"] and len(argv) == 2:
        with open(argv[1], encoding="utf-8") as fh:
            lines = [(f"commit message:{i}", t) for i, t in enumerate(fh.read().splitlines(), 1)
                     if not t.startswith("#")]
    elif len(argv) == 1:
        rev = argv[0]
        lines = [(f"{rev[:8]} {p}:{n}", t) for p, n, t in
                 added_lines(git("show", "--no-color", "--format=", "--src-prefix=a/",
                                 "--dst-prefix=b/", "-U0", rev))]
        lines += [(f"{rev[:8]} message:{i}", t) for i, t in
                  enumerate(git("log", "-1", "--format=%B", rev).splitlines(), 1)]
    else:
        print(__doc__.strip().splitlines()[3].strip(), file=sys.stderr)
        return 2
    return report(hits(lines, terms))


def main(argv):
    cmd, rest = (argv[0], argv[1:]) if argv else ("", [])
    if cmd == "check":
        return check(rest)
    if cmd == "list":
        terms = load()
        if terms is None:
            print(f"no list at {list_path()}")
            return 0
        for kind in KINDS:
            print(f"{kind}: " + (", ".join(terms[kind]) or "—"))
        return 0
    if cmd == "add" and len(rest) >= 2 and rest[0] in KINDS:
        terms = load() or {k: [] for k in KINDS}
        for word in rest[1:]:
            for kind in KINDS:                       # a word is on one list, the newest
                terms[kind] = [t for t in terms[kind] if t.lower() != word.lower()]
            terms[rest[0]].append(word)
        save(terms)
        print(f"{rest[0]}: added {', '.join(rest[1:])} ({list_path()})")
        return 0
    if cmd == "drop" and rest:
        terms = load() or {k: [] for k in KINDS}
        gone = {w.lower() for w in rest}
        for kind in KINDS:
            terms[kind] = [t for t in terms[kind] if t.lower() not in gone]
        save(terms)
        print(f"dropped {', '.join(rest)} ({list_path()})")
        return 0
    if cmd == "where" and rest:
        tracked = git("ls-files").split("\n")
        texts = {}
        for path in filter(None, tracked):
            try:
                with open(os.path.join(HERE, path), encoding="utf-8", errors="replace") as fh:
                    texts[path] = fh.read()
            except OSError:
                continue
        for word in rest:
            rx = pattern(word)
            files = [p for p, t in texts.items() if rx.search(t)]
            print(f"{word}: in {len(files)} file(s)" + (f": {', '.join(files[:5])}" if files else ""))
        return 0
    print(__doc__.strip().splitlines()[2].strip(), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
