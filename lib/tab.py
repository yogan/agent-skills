"""The names of the tmux windows agents run in: an icon, then a short title.

A tab holds at most `MAX` characters after its icon, so a tab bar with many windows does not
scroll. Which icon says what runs there (the owner's tmux config draws the states: a filled
icon while the window wants the user, a confused robot while a prompt waits):

    AGENT   a Claude Code session, named after its title (`--name`, `/rename`, or the one
            Claude Code generates), or its repository until it has one
    REVIEW  the session while /review-mr works through an MR
    REWORK  the session while /rework-mr works through one
    VIEWER  a diff viewer window a skill opened

A window is renamed only where nobody else named it: one tmux still names after its process
(`automatic-rename`, a fresh window), or one whose name is still the last this module gave it
(`@agent_tab_set`). A window the user renamed by hand keeps their name. While a skill holds
the tab (`@agent_tab_skill`, `hold`/`release`), the session's title waits behind it. When the
session ends, a window this module named gets back what it had before (`restore`). The tab
belongs to the Claude Code process that started in the window (`@agent_tab_owner`): another
`claude` run inside it while that one lives (`claude mcp list` from a session's own shell)
neither names the window nor gives its name back.

A tab never shows a title cut mid-thought: a title goes up at once only where it fits, or a
model shortened it before; otherwise the tab keeps its name until the model's short form is
there (`show`), and the cut is only what a failed model call leaves.

Every function degrades to doing nothing outside tmux, or where tmux refuses.
"""
import json
import os
import re
import subprocess
import sys
import time

AGENT_ICON = "\U000f167a"     # md-robot_outline
REVIEW_ICON = "\U000f127b"    # md-account_eye_outline
REWORK_ICON = "\U000f189b"    # md-account_wrench_outline
VIEWER_ICON = "\U000f08aa"    # md-file_compare
MAX = 18

SET_OPT = "@agent_tab_set"      # the name this module last gave the window
SKILL_OPT = "@agent_tab_skill"  # the name a skill's hold replaced, while it holds the tab
TMUX_NAMED = "(tmux)"           # ...where that was tmux's own naming after the process
BEFORE_OPT = "@agent_tab_before"  # what the window was called before this module named it
WANT_OPT = "@agent_tab_want"    # the title whose short form the model is writing
OWNER_OPT = "@agent_tab_owner"  # the pid of the Claude Code process the window's tab belongs to

CACHE = os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"),
                     "agent-skills", "tab-titles.json")
# Set on the background `claude -p` that shortens a title, so its own hooks leave tabs alone.
SHORTENER_ENV = "AGENT_TAB_SHORTENER"
# Set by the test runner (and the skills' test files): code under test that would name the
# window it runs in (the developer's own) or ask the model does neither.
OFF_ENV = "AGENT_TAB_OFF"

# What a title carries that a tab has no room for: a conventional-commit type and scope
# (`feat(ui):`), a ticket id (`ABC-123`), brackets around either.
_PREFIX = re.compile(r"^\s*[a-z]+(?:\([^)]*\))?!?:\s*", re.I)
_TICKET = re.compile(r"\[?\b[A-Z][A-Z0-9]+-\d+\b\]?:?")


def cut(title, limit=MAX):
    """`title` in at most `limit` characters: its prefix and ticket ids dropped, then whole
    words from its end, and any word left at the end that is only a symbol (`Threads /`); a
    first word longer than that ends in an ellipsis."""
    t = " ".join(_TICKET.sub(" ", _PREFIX.sub("", title or "")).split()).strip(" -:")
    if len(t) <= limit:
        return t
    out = ""
    for w in t.split():
        nxt = f"{out} {w}" if out else w
        if len(nxt) > limit:
            break
        out = nxt
    kept = out.split()
    while kept and not re.search(r"\w", kept[-1]):
        kept.pop()
    return " ".join(kept).rstrip(" -:,") or t[:limit - 1] + "…"


def _run(argv):
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return 1, ""
    return p.returncode, p.stdout


def _get(pane, fmt, run):
    rc, out = run(["tmux", "display-message", "-p", "-t", pane, fmt])
    return out.rstrip("\n") if rc == 0 else None


def current(pane, run=None):
    """The name of `pane`'s window, or None."""
    run = run or _run
    return _get(pane, "#{window_name}", run)


def _mine(pane, run):
    """Whether the window of `pane` is free for this module to name, and no skill holds it."""
    state = _get(pane, f"#{{automatic-rename}}\t#{{window_name}}\t#{{{SET_OPT}}}\t#{{{SKILL_OPT}}}", run)
    if state is None:
        return False
    auto, name, last, held = (state.split("\t") + ["", "", "", ""])[:4]
    return not held and (auto == "1" or (last and name == last))


def _rename(pane, name, run):
    rc, _ = run(["tmux", "rename-window", "-t", pane, name])
    if rc == 0:
        run(["tmux", "set-option", "-w", "-t", pane, SET_OPT, name])
    return rc == 0


def label(icon, title):
    return f"{icon} {cut(title)}"


def name(pane, icon, title, run=None):
    """Give `pane`'s window `icon` and `title` (cut to fit), where it is free to name.
    True where it was renamed."""
    run = run or _run
    if not pane or not title or not _mine(pane, run):
        return False
    if not _get(pane, f"#{{{BEFORE_OPT}}}", run):
        # Named here for the first time: free only where tmux was naming it after its process.
        run(["tmux", "set-option", "-w", "-t", pane, BEFORE_OPT, TMUX_NAMED])
    return _rename(pane, label(icon, title), run)


def hold(pane, icon, title, run=None):
    """A skill takes `pane`'s tab over, whoever named it: its old name is kept for `release`
    (unless a hold is already on, which keeps the first one's)."""
    run = run or _run
    if not pane or not title:
        return False
    if not _get(pane, f"#{{{SKILL_OPT}}}", run):
        old = _get(pane, "#{window_name}", run)
        auto = _get(pane, "#{automatic-rename}", run)
        if old is None:
            return False
        # A window tmux was naming gets that back: its old name is the process it ran.
        run(["tmux", "set-option", "-w", "-t", pane, SKILL_OPT, TMUX_NAMED if auto == "1" else old])
    rc, _ = run(["tmux", "rename-window", "-t", pane, label(icon, title)])
    return rc == 0


def release(pane, run=None):
    """The skill's hold ends: the window gets back the name it had before."""
    run = run or _run
    old = _get(pane, f"#{{{SKILL_OPT}}}", run) if pane else None
    if not old:
        return False
    run(["tmux", "set-option", "-wu", "-t", pane, SKILL_OPT])
    if old == TMUX_NAMED:
        run(["tmux", "set-option", "-w", "-t", pane, "automatic-rename", "on"])
    else:
        run(["tmux", "rename-window", "-t", pane, old])
    return True


def restore(pane, run=None):
    """The session ends: a skill's hold ends, and a window still carrying the name this
    module gave it gets back what it had before (tmux's own naming, for a fresh window)."""
    run = run or _run
    if not pane:
        return False
    release(pane, run)
    state = _get(pane, f"#{{window_name}}\t#{{{SET_OPT}}}\t#{{{BEFORE_OPT}}}", run)
    if state is None:
        return False
    now, last, before = (state.split("\t") + ["", "", ""])[:3]
    for opt in (SET_OPT, BEFORE_OPT, WANT_OPT, OWNER_OPT):
        run(["tmux", "set-option", "-wu", "-t", pane, opt])
    if not (last and now == last and before):
        return False                  # renamed by hand since: the user's name stays
    if before == TMUX_NAMED:
        run(["tmux", "set-option", "-w", "-t", pane, "automatic-rename", "on"])
    else:
        run(["tmux", "rename-window", "-t", pane, before])
    return True


def _alive(pid):
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except (OSError, ValueError, OverflowError):
        return False
    return True


def another_owns(pane, pid, run=None):
    """Whether `pane`'s window belongs to a Claude Code process other than `pid` that still
    runs: a `claude` started inside a session's own pane (`claude mcp list`, `claude -p`), or
    a second session in a split of the window. Its start and end leave the tab alone."""
    run = run or _run
    owner = _get(pane, f"#{{{OWNER_OPT}}}", run) if pane else None
    return bool(owner and owner.isdigit() and int(owner) != pid and _alive(int(owner)))


def own(pane, pid, run=None):
    """`pane`'s window belongs to the Claude Code process `pid` from now on."""
    run = run or _run
    run(["tmux", "set-option", "-w", "-t", pane, OWNER_OPT, str(pid)])


def held(pane, run=None):
    """Whether a skill holds `pane`'s tab."""
    run = run or _run
    return bool(pane and _get(pane, f"#{{{SKILL_OPT}}}", run))


HOOK = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hooks", "tmux-tab.py")


def spawn(argv, env=None):
    """`argv` detached, so neither a hook, the status line nor a skill waits for the model."""
    subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True, env=env)


def ready(title):
    """The short form of `title` a tab can show now: the model's where it wrote one before,
    the title itself where it fits whole; None while only a cut would do."""
    t = " ".join((title or "").split())
    return cached(title) or (t if t and cut(t) == t else None)


def _shorten_later(pane, icon, title, keep):
    """The model writes `title`'s short form in the background, and `finish` puts it in
    `pane`'s tab; with no pane, it is only kept for the next time."""
    if os.environ.get(OFF_ENV):
        return
    env = {k: v for k, v in os.environ.items() if k != "TMUX_PANE"}
    if pane:
        env["TMUX_PANE"] = pane
    spawn([sys.executable, HOOK, "shorten", "--icon", icon] + (["--hold"] if keep else []) + [title], env)


def show(pane, icon, title, keep=False, run=None):
    """`title` in `pane`'s tab, as soon as its short form is known: at once where it is
    `ready`, else once the model wrote it. `keep`: a skill's hold, whoever named the window;
    else only where the window is free to name. True where it was renamed now."""
    run = run or _run
    if not pane or not title or (not keep and not _mine(pane, run)):
        return False
    short = ready(title)
    if short:
        run(["tmux", "set-option", "-wu", "-t", pane, WANT_OPT])
        return hold(pane, icon, short, run) if keep else name(pane, icon, short, run)
    # The newest title asked for wins: a model late with an older one finds this changed.
    run(["tmux", "set-option", "-w", "-t", pane, WANT_OPT, title])
    _shorten_later(pane, icon, title, keep)
    return False


def finish(pane, icon, title, short, keep=False, run=None):
    """The model's short form of `title` (or None where it failed: the cut instead) into
    `pane`'s tab, where that title is still the one wanted there."""
    run = run or _run
    if not pane or _get(pane, f"#{{{WANT_OPT}}}", run) != title:
        return False
    run(["tmux", "set-option", "-wu", "-t", pane, WANT_OPT])
    short = short or cut(title)
    return hold(pane, icon, short, run) if keep else name(pane, icon, short, run)


def own_pane():
    """The pane this agent runs in, also from a background session (lib/tmux.py)."""
    try:
        from lib import tmux
        return tmux.own_pane(os.environ)
    except Exception:  # noqa: BLE001 — a tab name is never worth a failure
        return ""


# A session's name, waiting for its next prompt: only a hook can name a Claude Code session
# (`sessionTitle`), and settings.json's UserPromptSubmit hook looks here, one file per pane,
# before it starts anything.
NAMES = os.path.join(os.path.dirname(CACHE), "session-name")


def _name_file(pane):
    return os.path.join(NAMES, pane.lstrip("%"))


def ask_session_name(pane, name):
    """Name `pane`'s session `name` on its next prompt; the tab keeps its own short title."""
    os.makedirs(NAMES, exist_ok=True)
    with open(_name_file(pane), "w") as fh:
        fh.write(name)


def take_session_name(pane):
    """The name waiting for `pane`'s session, taken (it is applied once), or None."""
    try:
        with open(_name_file(pane)) as fh:
            name = fh.read().strip()
        os.remove(_name_file(pane))
        return name or None
    except OSError:
        return None


def take(icon, title, pane=None, session=None):
    """A skill's hold on its session's tab for an MR (`icon` and the MR's short title), from
    its own process, and `session`, a readable name for the session itself, on its next
    prompt. Nothing outside tmux; never fails."""
    if os.environ.get(OFF_ENV):
        return
    try:
        pane = own_pane() if pane is None else pane
        if pane and title:
            show(pane, icon, title, keep=True)
            if session:
                ask_session_name(pane, session)
    except Exception:  # noqa: BLE001
        pass


def take_mr(icon, iid, title):
    """`take` for a skill working through MR `iid`, from each command a session opens with:
    the tab shows `icon` and the MR's short title, the session gets `<icon> !<iid> <title>`
    on its next prompt. Given back when the MR's work ends, or the session does."""
    take(icon, title or f"!{iid}", session=f"{icon} !{iid} {title or ''}".rstrip())


def give_back(pane=None):
    """The skill's hold ends (the MR is merged, closed or cleaned up). Never fails."""
    if os.environ.get(OFF_ENV):
        return
    try:
        release(own_pane() if pane is None else pane)
    except Exception:  # noqa: BLE001
        pass


def viewer_label(title, fallback=None):
    """A diff viewer window's name, needed at once: its icon and the short title of what it
    shows where that is `ready`, else `fallback` (the MR's number) while the model writes
    one for the next window."""
    short = ready(title)
    if not short:
        _shorten_later("", VIEWER_ICON, title, False)
    return label(VIEWER_ICON, short or fallback or cut(title))


def _load():
    try:
        with open(CACHE) as fh:
            got = json.load(fh)
        return got if isinstance(got, dict) else {}
    except (OSError, ValueError):
        return {}


def cached(title):
    """The short title a model wrote for `title` earlier, or None."""
    return _load().get(title)


def remember(title, short):
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    # Read, add and write back under a lock: two shortenings finishing together (a status
    # line's and a viewer's) each wrote the file as they had read it, and one title was lost.
    with open(f"{CACHE}.lock", "a") as lock:
        try:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX)
        except (ImportError, OSError):
            pass                          # no locking here: as before, the last write wins
        got = _load()
        got[title] = short
        tmp = f"{CACHE}.{os.getpid()}"
        with open(tmp, "w") as fh:
            json.dump(got, fh, ensure_ascii=False, indent=0)
        os.replace(tmp, CACHE)


PROMPT = ("Shorten a coding session's title into a terminal tab label. Rules: {max} characters "
          "at most, counting spaces; one or two lowercase words; keep the most specific words, "
          "drop verbs like add/fix/update when space is short; no punctuation or quotes. "
          "Examples: 'Add feature flag for offline viewing mode' -> offline flag; 'Refactor "
          "the payment retry backoff logic' -> retry backoff. Answer with the label alone. "
          "Title: {title}")


def ask_model(title, run=None, timeout=60):
    """A short title for `title` from the smallest model, in a `claude -p` that loads no
    settings (so none of the user's hooks), no MCP servers and no tools, and saves no
    session. The model may think aloud first, so its answer is its last line. One that
    breaks the rules (too long, a word that is only a symbol) is asked for once more, saying
    why; None where it fails, so a cut is never kept as the model's answer."""
    prompt = PROMPT.format(max=MAX, title=title)
    for _ in range(2):
        argv = ["claude", "-p", "--model", "haiku", "--setting-sources", "", "--strict-mcp-config",
                "--tools", "", "--no-session-persistence", "--disable-slash-commands", prompt]
        try:
            p = (run or subprocess.run)(argv, capture_output=True, text=True, timeout=timeout,
                                        env={**os.environ, SHORTENER_ENV: "1"}, cwd=os.path.expanduser("~"))
        except (OSError, subprocess.SubprocessError):
            return None
        lines = [ln for ln in (p.stdout or "").splitlines() if ln.strip()]
        if p.returncode != 0 or not lines:
            return None
        words = " ".join(lines[-1].strip().strip("'\"`.").split())
        if len(words) <= MAX and all(re.search(r"\w", w) for w in words.split()):
            return words
        prompt = (f"{PROMPT.format(max=MAX, title=title)}\nYour last answer, '{words}', broke the "
                  f"rules: it was {len(words)} characters, or had punctuation. Answer again.")
    return None


# Where the status line notes, per session and pane, the title it last handed over, so it
# starts this module only when the title changed (~/.claude/hooks/statusline.js).
SEEN = os.path.join(os.path.dirname(CACHE), "tab-seen")


def prune_seen(root=None, keep_days=30):
    """The status line's notes on sessions not heard from in `keep_days` go."""
    root = root or SEEN
    now = time.time()
    try:
        names = os.listdir(root)
    except OSError:
        return
    for f in names:
        p = os.path.join(root, f)
        try:
            if now - os.path.getmtime(p) > keep_days * 86400:
                os.remove(p)
        except OSError:
            pass
