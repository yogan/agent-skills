#!/usr/bin/env python3
"""Change the rig's merge requests from outside while a session works on them, as the other
people on them would: for trying the review-ui mod's News box and rounds by hand, and for the
scenarios' checks.

MR !1, which you review; Alex (author-bot) is its author:

    python3 e2e/news.py reply   # Alex answers your thread on /auth/me (auth.ts): fix to come
    python3 e2e/news.py push    # Alex pushes the missing await in comments.ts, with no word
    python3 e2e/news.py fix     # Alex pushes the /auth/me fix and says in its thread it is fixed
    python3 e2e/news.py both    # Alex answers your thread on loadDb (db.ts) and pushes its fix
                                # at the same time: a reply and a push in one go

`reply`, `fix` and `both` answer YOUR thread on that point. Where you have none (none of yours
on that file is about it), they first post one as you, with the point the review's own topic
makes, and say so: the answer then always fits the comment it answers.

MR !2, which you re-review; main moves under it:

    python3 e2e/news.py main        # Chidi (peer-bot) lands a docs change on main
    python3 e2e/news.py rebase-fix  # Alex rebases !2 onto main and pushes one commit that fixes
                                    # t2 (the prefetch's errors) well and t4 (the repeated key)
                                    # only partly, and answers on t2 alone

MR !3, your own; Miriam (reviewer-bot) reviews it:

    python3 e2e/news.py approve     # Miriam approves it
    python3 e2e/news.py settle      # Miriam resolves its open threads and approves it
    python3 e2e/news.py pipeline running|success|failed   # a pipeline on its head
    python3 e2e/news.py more        # Miriam reopens the naming thread and asks for more
    python3 e2e/news.py doubt       # Miriam reopens it and doubts the pushed rename
    python3 e2e/news.py suggest     # Miriam applies a suggestion of hers on GitLab: a commit
                                    # on !3's branch that your checkout does not have
    python3 e2e/news.py merge       # you merge it

MR !5, which you re-review beside two other reviewers; Alex is its author:

    python3 e2e/news.py fixes-on-top  # Alex pushes two fix commits on top at once: the first
                                      # fixes your thread on the relative date (format.ts) and
                                      # changes three files besides, the second your thread on
                                      # the comment's date and Miriam's on the same line
                                      # (CommentsList.tsx), with no word in either thread
    python3 e2e/news.py peer-resolve  # Chidi (peer-bot) resolves his own thread on dayjs.ts

Which commands exclude each other:

- Each runs once per rig. Run again, it refuses and changes nothing.
- `reply` refuses after `fix`: it promises a fix that is pushed already.
- `rebase-fix` needs `main` first: without it there is nothing to rebase onto.
- `approve` refuses where Miriam approved already (`settle` approves too).
- `more` and `doubt` go in either order; after `merge`, nothing on !3 does.
- `suggest` changes a file no topic of !3 is about, so a fix pushed before or after it is
  still the fix; a push made after it stops until the commit is taken in.
- Everything else goes in any order: `both` touches nothing the others do, nor do
  `fixes-on-top` and `peer-resolve` each other's thread.

Every command checks everything first and only then changes anything, so a refused command
leaves the rig as it was. Never resets anything; `fixture.py` (or `tmux-demo.sh --force`) is
the way back to the start. Note that after `main`, `fixture.py --mr N` makes the whole rig
again (main is no longer where a reset leaves it).

On a scenario's copy of the rig, the environment names it, as for `fixture.py`:
`E2E_PROJECT=demo/e2e-<scenario> E2E_SRC=~/src/agent-skills-e2e/<scenario> python3 news.py …`.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fixture as fx  # noqa: E402
from lib.cli import die  # noqa: E402

AGAIN = ("Each command runs once per rig; `fixture.py` (or `tmux-demo.sh --force`) starts over. "
         "Which commands exclude each other: `python3 e2e/news.py help`.")


class Refused(Exception):
    """A check failed: nothing has been changed."""


# ---------------------------------------------------------------- MR !1

IID = 1
AUTH_FILE = "src/testing/mocks/handlers/auth.ts"
COMMENTS_FILE = "src/testing/mocks/handlers/comments.ts"
DB_FILE = "src/testing/mocks/db.ts"

UNAWAITED = "        persistDb('comment');\n"
# /auth/me as the MR leaves it, and as the comment asks for it: 401 without a user, like every
# other handler, and no catch that can no longer be reached.
AUTH_ME = """    try {
      const { user } = requireAuth(cookies);
      return HttpResponse.json(user);
    } catch (error: any) {
      return HttpResponse.json(
        { message: error?.message || 'Server Error' },
        { status: 500 },
      );
    }
  }),
];
"""
AUTH_ME_FIXED = """    const { user, error } = requireAuth(cookies);
    if (error) {
      return HttpResponse.json({ message: error }, { status: 401 });
    }
    return HttpResponse.json(user);
  }),
];
"""
# loadDb as the MR leaves it, and fixed: a file that is there but unreadable fails loudly
# instead of handing its callers a null they dereference.
LOAD_DB = """        console.error('Error loading mocked DB:', error);
        return null;
"""
LOAD_DB_FIXED = """        throw new Error(`Cannot load the mocked DB from ${dbFilePath}: ${error?.message}`);
"""

# The points `reply`, `fix` and `both` answer, each as the review's own topic makes it: the
# file, the line the comment goes on where it has to be posted, what a comment about this
# point says (any comment of yours on the file matching it is answered), and the comment.
AUTH = {
    "file": AUTH_FILE, "needle": "const { user } = requireAuth(cookies);",
    "about": r"auth/me|401|\bnull\b|\bcatch\b",
    "body": ("`/auth/me` is the only handler that still answers 200 with a `null` user when "
             "nobody is logged in; every other one answers 401. And `requireAuth` no longer "
             "throws, so the catch below can never run. Could it answer 401 like the rest, "
             "without the catch?"),
}
DB = {
    "file": DB_FILE, "needle": "return null;",
    "about": r"loadDb|\bnull\b|throw",
    "body": ("When the file is there but cannot be read or parsed, `loadDb` logs and returns "
             "`null`, and both callers (`persistDb`, `initializeDb`) use the result straight "
             "away: the real error turns into a TypeError one call later. Could it throw "
             "instead?"),
}

REPLY = ("Good point: /auth/me now answers 401 when there is no user, like the other "
         "handlers, and the dead catch is gone. Pushing it in a minute.")
FIXED_REPLY = "It is fixed now: /auth/me answers 401 without a user, and the dead catch is gone."
DB_REPLY = ("Right, a broken file should fail where it is read. loadDb throws now, with the "
            "file and the reason in the message; only a missing file still starts an empty DB.")


# ---------------------------------------------------------------- reading, never changing


def ensure_build():
    """The throwaway clone the commits are made in (fixture.py's), made where it is missing."""
    if not os.path.isdir(fx.BUILD):
        fx.ensure_cache()
        fx.fresh_build()


def tip(branch, user="root"):
    """The commit `branch` is at on GitLab now, fetched into the build clone (nothing on
    GitLab changes)."""
    ensure_build()
    ref = f"refs/news/{branch}"
    fx.git("fetch", "--quiet", "--force", fx.push_url(user), f"+refs/heads/{branch}:{ref}",
           cwd=fx.BUILD)
    return fx.git("rev-parse", ref, cwd=fx.BUILD).stdout.strip()


def file_at(sha, path):
    return fx.git("show", f"{sha}:{path}", cwd=fx.BUILD).stdout


def discussions(iid):
    return fx.api(f"projects/{fx.project_path_enc()}/merge_requests/{iid}/discussions?per_page=100")


def first_note(d):
    return (d.get("notes") or [{}])[0]


def fitting_thread(discs, user, path, about):
    """`user`'s thread on `path` whose first comment is about the point `about` matches, or
    None: a thread of theirs on the file about something else is not one an answer fits."""
    for d in discs:
        n = first_note(d)
        if ((n.get("author") or {}).get("username") == user
                and (n.get("position") or {}).get("new_path") == path
                and re.search(about, n.get("body") or "", re.I)):
            return d
    return None


def thread_on(discs, path):
    """The thread whose first comment is on `path`, or None."""
    return next((d for d in discs if (first_note(d).get("position") or {}).get("new_path") == path), None)


def said(d, user, text):
    """Whether `user` has already written `text` in thread `d`."""
    return any((n.get("author") or {}).get("username") == user and n.get("body", "").strip() == text.strip()
               for n in d.get("notes") or [])


# ---------------------------------------------------------------- plans
#
# A command is checked whole before anything is done: it returns its plan, a list of
# (what is done, how), and only a plan that came back whole is carried out. A step's `how`
# takes the plan's shared dict, where an earlier step leaves what a later one needs (the
# thread a reply goes into, once it is posted).


def answer(iid, point, text, who, cmd):
    """The steps that answer your thread on `point` with `text` as `who`: posting that thread
    first, as you, where none of yours is about it. Refused where it was answered so already."""
    discs = discussions(iid)
    d = fitting_thread(discs, "frank", point["file"], point["about"])
    name = fx.IDENT[who][0].split(", ")[1].split(" ")[0]
    if d and said(d, who, text):
        raise Refused(f"{name} has answered your thread on {point['file']} with this already: "
                      f"`{cmd}` ran before.")
    steps = []
    if d:
        steps.append(("", lambda s: s.update(thread=d["id"])))
    else:
        def post(s):
            s["thread"] = fx.post_thread(iid, "frank", point["file"], point["needle"], point["body"])["id"]
        steps.append((f"!{iid}: posted a thread of yours on {point['file']} first (none of yours "
                      "there was about this point)", post))
    steps.append((f"!{iid}: {who} replied in your thread on {point['file']}",
                   lambda s: fx.reply(iid, s["thread"], who, text)))
    return steps


def change(iid, branch, who, edits, message, cmd, force=False, onto=None):
    """The steps of a commit by `who` on `branch` (MR !`iid`'s) that makes `edits` ({file: (old, new)}),
    pushed: refused where the branch has it already. `onto`: (new base, old base) to rebase
    the branch onto first, pushed with force."""
    sha = tip(branch, who)
    for path, (old, _new) in edits.items():
        if old not in file_at(sha, path):
            raise Refused(f"{path} is changed on {branch} already: `{cmd}` ran before.")

    def push(_s):
        b = fx.BUILD
        fx.git("checkout", "--quiet", "--force", "--detach", sha, cwd=b)
        if onto:
            r = fx.git("rebase", "--quiet", "--onto", onto[0], onto[1], cwd=b, user=who, check=False)
            if r.returncode:
                fx.git("rebase", "--abort", cwd=b, check=False)
                die(f"the rebase of {branch} onto main conflicted:\n{r.stderr.strip()}")
        for path, (old, new) in edits.items():
            full = os.path.join(b, path)
            with open(full) as f:
                text = f.read()
            with open(full, "w") as f:
                f.write(text.replace(old, new, 1))
        fx.git("commit", "--quiet", "-am", message, cwd=b, user=who)
        fx.git("push", "--quiet", *(["--force"] if force else []), fx.push_url(who),
               f"HEAD:refs/heads/{branch}", cwd=b)
    files = ", ".join(os.path.basename(p) for p in edits)
    what = "rebased onto main and pushed" if onto else "pushed"
    return [(f"!{iid}: {who} {what} '{message}' ({files})", push)]


def plan_reply():
    sha = tip(fx.MR1_BRANCH, "author-bot")
    if AUTH_ME not in file_at(sha, AUTH_FILE):
        raise Refused("/auth/me is fixed on !1 already (`fix` ran): a reply that promises the "
                      "fix would come after it.")
    return answer(IID, AUTH, REPLY, "author-bot", "reply")


def plan_push():
    return change(IID, fx.MR1_BRANCH, "author-bot",
                  {COMMENTS_FILE: (UNAWAITED, "        await persistDb('comment');\n")},
                  "await the comment-delete persistDb", "push")


def plan_fix():
    """Your thread first where it has to be posted, then the push, then the word in it."""
    pushed = change(IID, fx.MR1_BRANCH, "author-bot", {AUTH_FILE: (AUTH_ME, AUTH_ME_FIXED)},
                    "answer 401 from /auth/me without a user", "fix")
    said_ = answer(IID, AUTH, FIXED_REPLY, "author-bot", "fix")
    return said_[:-1] + pushed + said_[-1:]


def plan_both():
    pushed = change(IID, fx.MR1_BRANCH, "author-bot", {DB_FILE: (LOAD_DB, LOAD_DB_FIXED)},
                    "throw from loadDb when the mocked DB cannot be read", "both")
    said_ = answer(IID, DB, DB_REPLY, "author-bot", "both")
    return said_[:-1] + pushed + said_[-1:]


# ---------------------------------------------------------------- MR !2 and main

IID2 = 2
README_LINE = "\nRun `npm run check` before pushing: it runs the quick source check.\n"
PROTECTED = "src/routes/protected.tsx"
LIST_FILE = "src/features/discussions/api/getDiscussions.ts"
CATCH = "await client.ensureQueryData(getDiscussionQueryOptions(params.discussionId)).catch(() => null);"
# t2 fixed well: only a missing discussion becomes null, anything else fails the route.
CATCH_FIXED = ("await client\n"
               "              .ensureQueryData(getDiscussionQueryOptions(params.discussionId))\n"
               "              .catch((error) => {\n"
               "                // Only a missing discussion is the view's to show; anything else fails the route.\n"
               "                if (error?.response?.status !== 404) throw error;\n"
               "                return null;\n"
               "              });")
# t4 fixed partly: useDiscussions takes the key from the options, but still repeats their
# queryFn, and useDiscussion repeats ['discussion', id] as before.
LIST_KEY = "    ...config,\n    queryKey: ['discussions'],\n"
LIST_KEY_FIXED = "    ...config,\n    queryKey: getDiscussionsQueryOptions().queryKey,\n"
T2 = {"file": PROTECTED, "about": r"prefetch fails"}
T2_REPLY = "Done: only a 404 becomes null now; anything else is rethrown and fails the route."


def plan_main():
    sha = tip("main")
    if README_LINE.strip() in file_at(sha, "README.md"):
        raise Refused("main has Chidi's change already: `main` ran before.")

    def land(_s):
        b = fx.BUILD
        fx.git("checkout", "--quiet", "--force", "--detach", sha, cwd=b)
        with open(os.path.join(b, "README.md"), "a") as f:
            f.write(README_LINE)
        fx.git("commit", "--quiet", "-am", "docs: mention the quick source check", cwd=b, user="peer-bot")
        # main is protected: only a maintainer pushes to it. The commit stays Chidi's.
        fx.git("push", "--quiet", fx.push_url("root"), "HEAD:refs/heads/main", cwd=b)
        fx.info(f"main moved to {fx.git('rev-parse', 'HEAD', cwd=b).stdout.strip()[:8]} (peer-bot)")
    return [("", land)]


def plan_rebase_fix():
    mr2 = tip(fx.MR2_BRANCH, "author-bot")
    main = tip("main")
    base = fx.git("merge-base", mr2, main, cwd=fx.BUILD).stdout.strip()
    pushed = change(IID2, fx.MR2_BRANCH, "author-bot",
                    {PROTECTED: (CATCH, CATCH_FIXED), LIST_FILE: (LIST_KEY, LIST_KEY_FIXED)},
                    "rethrow all but 404 in the discussion prefetch, share the list key",
                    "rebase-fix", force=True, onto=(main, base))
    if base == main:
        raise Refused("main has not moved since !2 branched off it: run `news.py main` first, so "
                      "the rebase has something to rebase onto.")
    d = fitting_thread(discussions(IID2), "frank", T2["file"], T2["about"])
    if not d:
        raise Refused(f"no thread of yours on {PROTECTED} asking what happens when the prefetch "
                      "fails: !2 is not as fixture.py seeds it.")
    return pushed + [(f"!{IID2}: author-bot replied on t2 (the prefetch's errors) alone",
                      lambda _s: fx.reply(IID2, d["id"], "author-bot", T2_REPLY))]


# ---------------------------------------------------------------- MR !3

REWORK_IID = 3


def mr3():
    return fx.api(f"projects/{fx.project_path_enc()}/merge_requests/{REWORK_IID}")


def open_mr3(cmd):
    if (mr := mr3())["state"] != "opened":
        raise Refused(f"!{REWORK_IID} is {mr['state']}: `{cmd}` changes an open MR only.")
    return mr


def approved_by_miriam():
    a = fx.api(f"projects/{fx.project_path_enc()}/merge_requests/{REWORK_IID}/approvals")
    return any(x["user"]["username"] == "reviewer-bot" for x in a.get("approved_by") or [])


def approve_step():
    return ("", lambda _s: (fx.api(f"projects/{fx.project_path_enc()}/merge_requests/{REWORK_IID}/approve",
                                   "POST", {}, user="reviewer-bot"),
                            fx.info(f"!{REWORK_IID}: reviewer-bot approved it")))


def plan_approve():
    open_mr3("approve")
    if approved_by_miriam():
        raise Refused(f"Miriam has approved !{REWORK_IID} already (`approve` or `settle` ran).")
    return [approve_step()]


def plan_settle():
    """Miriam resolves every thread still open on MR !3, then approves it: every topic
    settled, and the MR ready for the Done box's Merge."""
    open_mr3("settle")
    enc = fx.project_path_enc()
    still = [d["id"] for d in discussions(REWORK_IID)
             if first_note(d).get("resolvable") and not first_note(d).get("resolved")]
    approved = approved_by_miriam()
    if not still and approved:
        raise Refused(f"!{REWORK_IID} has no open thread and Miriam approved it: `settle` ran before.")

    def resolve(_s):
        for did in still:
            fx.api(f"projects/{enc}/merge_requests/{REWORK_IID}/discussions/{did}?resolved=true",
                   "PUT", {}, user="reviewer-bot")
    return [("", resolve), *([] if approved else [approve_step()]),
            (f"!{REWORK_IID}: reviewer-bot resolved its threads", lambda _s: None)]


PIPELINE = ("running", "success", "failed")


def plan_pipeline(status):
    """A pipeline on MR !3's head in `status`, from an external commit status: the rig has no
    runner, and GitLab makes a pipeline of a status all the same."""
    mr = open_mr3("pipeline")

    def post(_s):
        fx.api(f"projects/{fx.project_path_enc()}/statuses/{mr['sha']}", "POST",
               {"state": status, "name": "ci", "ref": mr["source_branch"]})
    return [(f"!{REWORK_IID}: pipeline {status}", post)]


def plan_merge():
    open_mr3("merge")
    return [(f"!{REWORK_IID}: frank merged it", lambda _s: fx.api(
        f"projects/{fx.project_path_enc()}/merge_requests/{REWORK_IID}/merge", "PUT", {}, user="frank"))]


# The naming thread once its rename is pushed: one more small change, so the topic is fixed
# and pushed a second time, with another topic's push between the two.
MORE_ON = "src/features/comments/api/query-keys.ts"
MORE = ("Thanks, that reads much better. One small thing while you're in there: could you put "
        "a one-line doc comment above `commentKeys`, saying which queries it keys?")


# The same thread, its rename pushed, and the reviewer no longer sure of it: a pushed fix the
# author keeps, with a reply and no second "Fixed".
DOUBT = ("Thinking about it more, I'm not sure about the rename after all: every feature could "
         "keep exporting `queryKeys` and the files importing two of them alias on import. "
         "Should we go back to `queryKeys`?")


def plan_more(text, cmd):
    """Miriam's follow-up in MR !3's thread on MORE_ON, reopened if resolved."""
    open_mr3(cmd)
    d = thread_on(discussions(REWORK_IID), MORE_ON)
    if not d:
        raise Refused(f"no thread on {MORE_ON} in MR !{REWORK_IID}")
    if said(d, "reviewer-bot", text):
        raise Refused(f"Miriam has written this in the thread on {MORE_ON} already: `{cmd}` ran before.")
    enc = fx.project_path_enc()

    def post(_s):
        fx.api(f"projects/{enc}/merge_requests/{REWORK_IID}/discussions/{d['id']}?resolved=false",
               "PUT", {}, user="reviewer-bot")
        fx.reply(REWORK_IID, d["id"], "reviewer-bot", text)
    return [(f"!{REWORK_IID}: reviewer-bot {'asked for more' if text == MORE else 'doubts the fix'} "
             f"on {MORE_ON}", post)]


# A suggestion applied on GitLab: a commit on the branch the checkout does not have, which a
# push from the checkout must not overwrite. On a file none of !3's topics is about.
SUGGEST_ON = "src/features/discussions/api/deleteDiscussion.ts"
SUGGESTED = ("        title: 'Discussion Deleted',\n", "        title: 'Discussion deleted',\n")


def plan_suggest():
    open_mr3("suggest")
    return change(REWORK_IID, fx.MR3_BRANCH, "reviewer-bot", {SUGGEST_ON: SUGGESTED},
                  "Apply 1 suggestion(s) to 1 file(s)", "suggest")


# ---------------------------------------------------------------- MR !5

IID5 = 5

# Alex's two fix commits on top, pushed at once: the first answers your thread on the
# relative date and touches three files besides; the second answers your thread on the
# comment's date, and Miriam's on the same line, in a file the first leaves alone.
RELATIVE_FIXED = ('// "3 days ago", from `now`: a list that stays open passes one that moves on.\n'
                  "export const formatRelative = (date: number, now: number = Date.now()) =>\n"
                  "  dayjs(date).from(now);")
USE_NOW = "src/hooks/useNow.ts"
USE_NOW_TEXT = """import { useEffect, useState } from 'react';

// The time now, moved on every `everyMs`: for anything that says how long ago something was.
export const useNow = (everyMs = 60_000) => {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), everyMs);
    return () => window.clearInterval(id);
  }, [everyMs]);
  return now;
};
"""
LIST_NOW = {  # each list: its hook import and `now`, and the cell passing it
    "import { formatDate, formatRelative } from '@/utils/format';\n":
        "import { useNow } from '@/hooks/useNow';\nimport { formatDate, formatRelative } from '@/utils/format';\n",
    "{formatRelative(createdAt)}": "{formatRelative(createdAt, now)}",
}
LISTS = {fx.MR5_DISCUSSIONS: "  const discussionsQuery = useDiscussions();\n",
         fx.MR5_USERS: "  const usersQuery = useUsers();\n"}
COMMENT_DATE_FIXED = """                <time
                  className="text-xs font-semibold"
                  dateTime={new Date(comment.createdAt).toISOString()}
                  title={formatDate(comment.createdAt)}
                >
                  {formatRelative(comment.createdAt)}
                </time>
"""
COMMENTS_IMPORT = ("import { formatRelative } from '@/utils/format';\n",
                   "import { formatDate, formatRelative } from '@/utils/format';\n")
FIX_COMMITS = (
    ("move relative dates on once a minute", {
        fx.MR5_FORMAT: [(fx.MR5_RELATIVE, RELATIVE_FIXED),
                        ('// "3 days ago": short enough for a table cell.\n', "")],
        USE_NOW: [("", USE_NOW_TEXT)],
        **{path: [*LIST_NOW.items(), (hook, hook + "  const now = useNow();\n")]
           for path, hook in LISTS.items()}}),
    ("keep the exact date of a comment on hover", {
        fx.MR5_COMMENTS: [COMMENTS_IMPORT, (fx.MR5_COMMENT_DATE, COMMENT_DATE_FIXED)]}),
)
# Chidi's thread that he resolves himself: the first of the other reviewers' (fixture.py).
RESOLVED_PEER = fx.MR5_PEER_THREADS[0]


def plan_fixes_on_top():
    """Alex's FIX_COMMITS, each a commit of its own on !5's branch, pushed together as one
    fast-forward push. Refused where the branch has them already."""
    branch = fx.MR5_BRANCH
    sha = tip(branch, "author-bot")
    for path, old in ((fx.MR5_FORMAT, fx.MR5_RELATIVE), (fx.MR5_COMMENTS, fx.MR5_COMMENT_DATE)):
        if old not in file_at(sha, path):
            raise Refused(f"{path} is changed on {branch} already: `fixes-on-top` ran before.")

    def push(_s):
        b = fx.BUILD
        fx.git("checkout", "--quiet", "--force", "--detach", sha, cwd=b)
        for message, edits in FIX_COMMITS:
            for path, steps in edits.items():
                full = os.path.join(b, path)
                os.makedirs(os.path.dirname(full), exist_ok=True)
                text = open(full).read() if os.path.exists(full) else ""
                for old, new in steps:
                    if old and text.count(old) != 1:
                        die(f"{path}: expected one {old.strip()[:60]!r} on {branch}")
                    text = text.replace(old, new, 1) if old else new
                with open(full, "w") as f:
                    f.write(text)
            fx.git("add", *edits, cwd=b)
            fx.git("commit", "--quiet", "-m", message, cwd=b, user="author-bot")
        fx.git("push", "--quiet", fx.push_url("author-bot"), f"HEAD:refs/heads/{branch}", cwd=b)
    said = "', '".join(m for m, _ in FIX_COMMITS)
    return [(f"!{IID5}: author-bot pushed two commits on top: '{said}'", push)]


def plan_peer_resolve():
    """Chidi resolves his own thread on RESOLVED_PEER's file: it closes by itself for you."""
    p = RESOLVED_PEER
    d = next((d for d in discussions(IID5)
              if (first_note(d).get("author") or {}).get("username") == p["user"]
              and (first_note(d).get("position") or {}).get("new_path") == p["file"]), None)
    if not d:
        raise Refused(f"no thread of {p['user']}'s on {p['file']} in !{IID5}: it is not as "
                      "fixture.py seeds it.")
    if first_note(d).get("resolved"):
        raise Refused(f"Chidi's thread on {p['file']} is resolved already: `peer-resolve` ran before.")
    enc = fx.project_path_enc()
    return [(f"!{IID5}: {p['user']} resolved their thread on {p['file']}",
             lambda _s: fx.api(f"projects/{enc}/merge_requests/{IID5}/discussions/{d['id']}?resolved=true",
                               "PUT", {}, user=p["user"]))]


# ---------------------------------------------------------------- the commands

COMMANDS = {
    "reply": plan_reply, "push": plan_push, "fix": plan_fix, "both": plan_both,
    "main": plan_main, "rebase-fix": plan_rebase_fix,
    "approve": plan_approve, "settle": plan_settle, "merge": plan_merge,
    "more": lambda: plan_more(MORE, "more"), "doubt": lambda: plan_more(DOUBT, "doubt"),
    "suggest": plan_suggest,
    "fixes-on-top": plan_fixes_on_top, "peer-resolve": plan_peer_resolve,
}


def carry_out(plan):
    """Each step of a plan that came back whole, in order, saying what it did."""
    shared = {}
    for what, how in plan:
        how(shared)
        if what:
            fx.info(what)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    what = argv[0] if argv else ""
    if what == "pipeline" and len(argv) > 1 and argv[1] in PIPELINE:
        build = lambda: plan_pipeline(argv[1])  # noqa: E731
    elif what in COMMANDS:
        build = COMMANDS[what]
    else:
        print(__doc__.strip())
        return 0 if what in ("help", "-h", "--help") else 2
    fx.E = fx.env()
    try:
        plan = build()
    except Refused as why:
        die(f"{what}: {why} Nothing was changed.\n{AGAIN}")
    carry_out(plan)
    return 0


if __name__ == "__main__":
    sys.exit(main())
