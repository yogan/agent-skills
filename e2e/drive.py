#!/usr/bin/env python3
"""Drive a real Claude Code session in tmux through a scenario, to check it or to show it.

    python3 drive.py list
    python3 drive.py verify   <scenario|file.py> [--model haiku] [--out DIR] [--keep]
    python3 drive.py showcase <scenario> [...]   [--model M] [--pace SECONDS] [--session NAME]

**verify** runs each scenario in a detached tmux session of a fixed size, on Haiku unless told
otherwise, with the user's own settings left out (`--setting-sources project,local`) so a Stop
hook of theirs cannot change what is measured. After every step it saves the screen as text and
with colours, checks what the step expects, and on the first miss exits non-zero naming the
scenario and the step. The captures land in `.runs/<time>-<scenario>/`.

**showcase** runs the same scenarios in a window of your own tmux, with your settings and your
model, for somebody to watch. By default it waits for Enter in the terminal the driver runs in
before each step, so the pace is yours; `--pace N` runs it unattended, N seconds between steps.
A scenario that ends in `Handover` leaves claude running with its last line typed: that is
how the talk-day session is built (`tmux-demo.sh`).

Scenarios are in `scenarios.py`; a path to a `.py` file defining `SCENARIOS` runs that instead,
for a one-off probe that does not belong in the repo.

Things learned the hard way, so they stay learned:

* `disableAllHooks` switches off mods as well. Leaving the user's settings out is what keeps
  their Stop hooks out of a run — and their skills, CLAUDE.md and permission rules with them,
  which is why a verify run brings the repo's skills in as a plugin and carries its own
  allow list.
* A chord is sent as tmux key names with no Enter after it (`C-x Tab`). Sending it as text
  types the letters into the prompt.
* Whether the panel is open is judged by its divider: a vertical rule down most of the screen
  at the same column. Its header is no use, since a bottom-of-screen capture cuts it off.
* A TUI still booting drops every key sent at it, silently. Wait for the prompt box first.
"""
import argparse
import dataclasses
import datetime
import importlib.util
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
from pathlib import Path

E2E = Path(__file__).resolve().parent
RUNS = E2E / ".runs"
BUSY = "esc to interrupt"
VERIFY_SIZE = (160, 50)

# What a verify run may do without a permission prompt. Wide enough for both skills to run
# their scripts and glab, and nothing else; a call outside it stops at the prompt, and the
# next expectation fails with the prompt in its capture, which names the missing rule.
VERIFY_ALLOW = (
    "Bash(python3 *)", "Bash(glab *)", "Bash(git *)", "Bash(cd *)", "Bash(ls *)",
    "Read", "Grep", "Glob", "Skill",
)


# ── steps ────────────────────────────────────────────────────────────────────

@dataclasses.dataclass
class Step:
    def label(self):
        return type(self).__name__.lower()


@dataclasses.dataclass
class Prompt(Step):
    """Type a line and press Enter, then wait for the turn to end."""
    text: str
    timeout: int = 300

    def label(self):
        return f"prompt {self.text[:40]!r}"


@dataclasses.dataclass
class Type(Step):
    """Type a line and leave it in the prompt, unsent."""
    text: str

    def label(self):
        return f"type {self.text[:40]!r}"


@dataclasses.dataclass
class Keys(Step):
    """Send tmux key names (`C-x Tab`, `Down`, `2`, `Enter`) and wait for the screen to settle.

    A key that starts a turn (Enter on a button that submits a prompt) is waited out like a
    prompt: set `turn=True`.
    """
    keys: str
    turn: bool = False
    timeout: int = 300

    def label(self):
        return f"keys {self.keys}"


@dataclasses.dataclass
class Click(Step):
    """Click the first place on screen showing `text` (a mouse press and release there)."""
    text: str
    offset: int = 0     # columns right of where `text` starts, to hit one glyph of it

    def label(self):
        return f"click {self.text!r}"


@dataclasses.dataclass
class Expect(Step):
    """The screen shows `pattern` (a regex), or with `absent=True` does not."""
    pattern: str
    absent: bool = False
    region: str = "screen"      # screen | panel | main (the screen left of the panel)

    def label(self):
        return f"expect {'no ' if self.absent else ''}{self.pattern!r} in {self.region}"


@dataclasses.dataclass
class Panel(Step):
    """The panel is open (True) or closed (False)."""
    open: bool

    def label(self):
        return f"panel {'open' if self.open else 'closed'}"


@dataclasses.dataclass
class Say(Step):
    """A caption: printed to the driver's terminal in a showcase, logged in a verify."""
    text: str

    def label(self):
        return f"say {self.text[:40]!r}"


@dataclasses.dataclass
class Handover(Step):
    """End here and leave claude running, for a person to take over (showcase only)."""


@dataclasses.dataclass
class Scenario:
    name: str
    about: str
    cwd: str | None             # may start with ~; None: a fresh empty folder, removed after
    steps: list
    mods: list = dataclasses.field(default_factory=list)       # plugin folders, ~ allowed
    window: str = ""            # showcase window name; defaults to the scenario's name
    args: list = dataclasses.field(default_factory=list)       # extra claude arguments
    allow: tuple = VERIFY_ALLOW
    needs_rig: bool = False     # the local GitLab must be up and reset (tmux-demo.sh --check)


class StepFailed(Exception):
    pass


# ── the screen ───────────────────────────────────────────────────────────────

def cell_width(ch):
    """Columns a character takes in the terminal: 2 for wide East Asian forms and most emoji,
    0 for combining marks, else 1. Nerd Font glyphs sit in the Private Use Area and take 1."""
    if unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def columns_of(line, needle):
    """Display column (0-based) of each occurrence of `needle` in `line`."""
    out, col, i = [], 0, 0
    while i < len(line):
        if line.startswith(needle, i):
            out.append(col)
        col += cell_width(line[i])
        i += 1
    return out


def find_text(screen, text):
    """(row, column), both 0-based, of the first occurrence of `text` on screen, or None."""
    for row, line in enumerate(screen.split("\n")):
        cols = columns_of(line, text)
        if cols:
            return row, cols[0]
    return None


def has_prompt_box(screen):
    """Claude's prompt is drawn: a `❯` line directly under a horizontal rule. A shell prompt
    may carry a `❯` too, but not under a rule, so a window still at its shell does not count."""
    lines = screen.split("\n")
    return any(line.lstrip().startswith("❯") and "─" * 20 in above
               for above, line in zip(lines, lines[1:]))


DIVIDERS = "│┃║▕▏|"


def divider_column(screen, share=0.6):
    """The column of the panel's divider, or None when no panel is open.

    A docked panel is separated from the transcript by a vertical rule that runs down most of
    the screen at one column. A card in the transcript has rules too, but only over its own
    rows, and the prompt box's sides sit at the screen's edges, so the test is: a column away
    from both edges holding a rule on at least `share` of the rows.
    """
    lines = screen.rstrip("\n").split("\n")
    if not lines:
        return None
    width = max(sum(cell_width(c) for c in line) for line in lines)
    counts = {}
    for line in lines:
        col = 0
        for ch in line:
            if ch in DIVIDERS:
                counts[col] = counts.get(col, 0) + 1
            col += cell_width(ch)
    best = [c for c, n in counts.items() if n >= share * len(lines) and 4 < c < width - 4]
    return min(best) if best else None


def region_of(screen, region):
    if region == "screen":
        return screen
    col = divider_column(screen)
    if col is None:
        return "" if region == "panel" else screen
    out = []
    for line in screen.split("\n"):
        acc, cut = 0, len(line)
        for i, ch in enumerate(line):
            if acc >= col:
                cut = i
                break
            acc += cell_width(ch)
        out.append(line[cut + 1:] if region == "panel" else line[:cut])
    return "\n".join(out)


# ── tmux ─────────────────────────────────────────────────────────────────────

def tmux(*args, check=True):
    r = subprocess.run(["tmux", *args], capture_output=True, text=True)
    if check and r.returncode:
        raise RuntimeError(f"tmux {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout


class Pane:
    """One claude, in a tmux pane addressed by `target`."""

    def __init__(self, target, log=print):
        self.target = target
        self.log = log

    def screen(self, ansi=False):
        """The visible screen only: what the person sees, and what a step judges."""
        return tmux("capture-pane", "-p", "-t", self.target, *(["-e"] if ansi else []))

    def history(self, ansi=False):
        return tmux("capture-pane", "-p", "-J", "-S", "-2000", "-t", self.target,
                    *(["-e"] if ansi else []))

    def keys(self, *names):
        tmux("send-keys", "-t", self.target, *names)

    def type(self, text):
        tmux("send-keys", "-t", self.target, "-l", text)

    def click(self, row, col):
        """A left press and release at (row, col), 0-based, as an SGR mouse report.

        Sent as raw bytes (`-H`), so tmux hands the application exactly what a terminal would
        for a real click, whatever tmux's own mouse setting.
        """
        seq = f"\x1b[<0;{col + 1};{row + 1}M\x1b[<0;{col + 1};{row + 1}m"
        tmux("send-keys", "-t", self.target, "-H", *(f"{b:02x}" for b in seq.encode()))

    def wait_ready(self, timeout=90):
        """Wait for the prompt box, answering the trust-this-folder question on the way.

        That question's default is "No, exit": an Enter meant for the prompt quits claude, so
        it is answered here, once, with its second option.
        """
        end, answered = time.time() + timeout, False
        while time.time() < end:
            s = self.screen()
            if "trust this folder" in s:
                if not answered:
                    self.keys("Down")
                    self.keys("Enter")
                    answered = True
            elif has_prompt_box(s):
                return self.settle(timeout=max(5, end - time.time()))
            time.sleep(0.5)
        raise StepFailed(f"claude did not reach its prompt in {timeout}s")

    def settle(self, timeout=60, quiet=1.5):
        """Wait until the screen has not changed for `quiet` seconds and no turn is running."""
        end, last, since = time.time() + timeout, None, time.time()
        while time.time() < end:
            now = self.screen()
            if now != last:
                last, since = now, time.time()
            elif BUSY not in now and time.time() - since >= quiet:
                return now
            time.sleep(0.3)
        raise StepFailed(f"the screen did not settle in {timeout}s")

    def wait_turn(self, timeout):
        """Wait for a turn to start and end. A turn that never shows a spinner (a command
        the mod answers itself) counts as ended once the screen settles."""
        end = time.time() + 8
        while time.time() < end and BUSY not in self.screen():
            time.sleep(0.3)
        return self.settle(timeout=timeout, quiet=3)


# ── running ──────────────────────────────────────────────────────────────────

def expand(p):
    return os.path.expanduser(p)


def workdir(sc):
    """The scenario's folder, and a cleanup for it: a fresh one when the scenario names none,
    removed afterwards, since a test removes exactly what it created."""
    if sc.cwd:
        return expand(sc.cwd), lambda: None
    d = tempfile.mkdtemp(prefix=f"drive-{sc.name}-")
    return d, lambda: shutil.rmtree(d, ignore_errors=True)


def skills_plugin(out):
    """A plugin folder holding this repo's skills, for a run without the user's settings.

    Leaving the user's settings out leaves out everything else of theirs too, their skills
    folder included, so `/review-mr` would be an unknown command. The repo's own skills come
    back in as a plugin instead; `/review-mr` still resolves, as the short name of
    `/agent-skills:review-mr`. Their scripts are found where the skills say, through the
    `~/.claude/skills` links, which are files on disk and need no setting.
    """
    d = out / "skills-plugin"
    (d / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (d / ".claude-plugin" / "plugin.json").write_text(
        '{"name": "agent-skills", "version": "0.0.0", "description": "this repo\'s skills"}\n')
    link = d / "skills"
    if not link.exists():
        link.symlink_to(E2E.parent / "skills")
    return d


def claude_command(sc, model, verify, plugins=()):
    argv = ["claude"]
    for m in [*plugins, *sc.mods]:
        argv += ["--plugin-dir", expand(m)]
    if model:
        argv += ["--model", model]
    if verify:
        argv += ["--setting-sources", "project,local"]
        if sc.allow:
            argv += ["--allowedTools", ",".join(sc.allow)]
    argv += sc.args
    return shlex.join(argv)


def run_step(pane, step, verify):
    if isinstance(step, Prompt):
        pane.type(step.text)
        time.sleep(0.3)
        pane.keys("Enter")
        pane.wait_turn(step.timeout)
    elif isinstance(step, Type):
        pane.type(step.text)
        pane.settle()
    elif isinstance(step, Keys):
        pane.keys(*step.keys.split())
        if step.turn:
            pane.wait_turn(step.timeout)
        else:
            time.sleep(0.5)
            pane.settle()
    elif isinstance(step, Click):
        at = find_text(pane.screen(), step.text)
        if at is None:
            raise StepFailed(f"nothing on screen reads {step.text!r}")
        pane.click(at[0], at[1] + step.offset)
        time.sleep(0.5)
        pane.settle()
    elif isinstance(step, Expect):
        text = region_of(pane.screen(), step.region)
        hit = re.search(step.pattern, text, re.M)
        if bool(hit) == step.absent:
            raise StepFailed(f"{'found' if hit else 'did not find'} {step.pattern!r} in the {step.region}")
    elif isinstance(step, Panel):
        col = divider_column(pane.screen())
        if (col is not None) != step.open:
            raise StepFailed(f"the panel is {'open' if col is not None else 'closed'}")
    elif isinstance(step, Say):
        pane.log(f"   ·  {step.text}")
    else:
        raise StepFailed(f"not a step this driver knows: {step!r}")


def save(pane, out, n, step):
    stem = out / f"{n:02d}-{re.sub(r'[^a-z0-9]+', '-', step.label().lower()).strip('-')[:50]}"
    stem.with_suffix(".txt").write_text(pane.screen())
    stem.with_suffix(".ansi").write_text(pane.screen(ansi=True))


def reset_rig(out):
    """Put the local GitLab and the demo checkouts back in their known state (~30 s).

    Every rig scenario starts from here: a review run writes the skill's state and posts to
    the merge request, so the next run would otherwise start somewhere else."""
    r = subprocess.run([sys.executable, str(E2E / "fixture.py")], capture_output=True, text=True)
    (out / "reset.log").write_text(r.stdout + r.stderr)
    if r.returncode:
        raise StepFailed(f"fixture.py failed, see {out / 'reset.log'}")


def verify(sc, model, out_root, keep, reset):
    out = out_root / f"{datetime.datetime.now():%Y%m%d-%H%M%S}-{sc.name}"
    out.mkdir(parents=True)
    if sc.needs_rig and reset:
        try:
            reset_rig(out)
        except StepFailed as err:
            print(f"── {sc.name}\n   FAIL reset: {err}")
            return False
    session = f"drive-{sc.name}"
    tmux("kill-session", "-t", session, check=False)
    w, h = VERIFY_SIZE
    cwd, cleanup = workdir(sc)
    tmux("new-session", "-d", "-s", session, "-x", str(w), "-y", str(h), "-c", cwd,
         claude_command(sc, model, verify=True, plugins=[skills_plugin(out)]))
    pane = Pane(session)
    print(f"── {sc.name}: {sc.about}")
    n, step, t0 = 0, None, time.time()
    try:
        pane.wait_ready()
        for n, step in enumerate(sc.steps, 1):
            if isinstance(step, Handover):
                break
            t = time.time()
            run_step(pane, step, verify=True)
            save(pane, out, n, step)
            print(f"   ok  {n:2d} {step.label()}  ({time.time() - t:.1f}s)")
    except (StepFailed, RuntimeError) as err:
        save(pane, out, n, step or Say("start"))
        (out / "history.txt").write_text(pane.history())
        print(f"   FAIL {n:2d} {step.label() if step else 'start'}: {err}")
        print(f"        captures: {out}")
        return False
    finally:
        if not (out / "history.txt").exists():
            (out / "history.txt").write_text(pane.history())
        if keep:
            print(f"   kept: tmux attach -t {session}")
        else:
            tmux("kill-session", "-t", session, check=False)
            cleanup()
    print(f"   passed in {time.time() - t0:.0f}s · captures: {out}")
    return True


def showcase(sc, model, session, pace):
    """One window in `session` (created when missing), steps paced by Enter or `pace`."""
    window = sc.window or sc.name
    cwd, _ = workdir(sc)        # a showcase folder outlives the driver: claude stays in it
    # claude runs inside the window's shell, so the window survives claude exiting and the
    # person can start it again where it was.
    if subprocess.run(["tmux", "has-session", "-t", session], capture_output=True).returncode:
        tmux("new-session", "-d", "-s", session, "-n", window, "-c", cwd)
    else:
        tmux("new-window", "-d", "-t", f"{session}:", "-n", window, "-c", cwd)
    pane = Pane(f"{session}:{window}")
    pane.type("clear; " + claude_command(sc, model, verify=False))
    pane.keys("Enter")
    print(f"── {sc.name}: {sc.about}  (window {session}:{window})")
    pane.wait_ready()
    for n, step in enumerate(sc.steps, 1):
        if isinstance(step, Handover):
            print(f"   {n:2d} handed over — claude is running in {session}:{window}")
            return True
        if not isinstance(step, (Expect, Panel)):
            if pace is None:
                input(f"   {n:2d} {step.label()}  [Enter] ")
            else:
                print(f"   {n:2d} {step.label()}")
                time.sleep(pace)
        try:
            run_step(pane, step, verify=False)
        except StepFailed as err:
            print(f"   !! {n:2d} {step.label()}: {err}")
    return True


def load_scenarios(name):
    # A scenario file imports its steps from `drive`; run as a script this module is
    # `__main__`, and a second copy's classes would fail every isinstance check above.
    sys.modules.setdefault("drive", sys.modules[__name__])
    if name.endswith(".py"):
        spec = importlib.util.spec_from_file_location("adhoc_scenarios", name)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return list(mod.SCENARIOS)
    sys.path.insert(0, str(E2E))
    import scenarios
    found = [s for s in scenarios.SCENARIOS if s.name == name]
    if not found:
        raise SystemExit(f"no scenario {name!r} (try: drive.py list)")
    return found


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="mode", required=True)
    sub.add_parser("list")
    v = sub.add_parser("verify")
    v.add_argument("scenarios", nargs="+")
    v.add_argument("--model", default="haiku")
    v.add_argument("--out", type=Path, default=RUNS)
    v.add_argument("--keep", action="store_true", help="leave the tmux session up afterwards")
    v.add_argument("--no-reset", dest="reset", action="store_false",
                   help="run a rig scenario on the rig as it is, without fixture.py first")
    s = sub.add_parser("showcase")
    s.add_argument("scenarios", nargs="+")
    s.add_argument("--model", default=None, help="default: your own")
    s.add_argument("--pace", type=float, default=None, help="seconds between steps; default: Enter")
    s.add_argument("--session", default=None, help="default: the tmux session you are in")
    a = ap.parse_args(argv)

    if a.mode == "list":
        sys.path.insert(0, str(E2E))
        import scenarios
        for sc in scenarios.SCENARIOS:
            print(f"{sc.name:14} {'rig ' if sc.needs_rig else '    '}{sc.about}")
        return 0

    picked = [sc for name in a.scenarios for sc in load_scenarios(name)]
    if a.mode == "verify":
        ok = all([verify(sc, a.model, a.out, a.keep, a.reset) for sc in picked])
        return 0 if ok else 1

    session = a.session or (tmux("display-message", "-p", "#S").strip() if os.environ.get("TMUX") else None)
    if not session:
        raise SystemExit("showcase needs --session NAME when not run inside tmux")
    for sc in picked:
        showcase(sc, a.model, session, a.pace)
    return 0


if __name__ == "__main__":
    sys.exit(main())
