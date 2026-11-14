#!/usr/bin/env python3
"""Make MR !1 change while its review waits, as its author would: for trying the review-ui
mod's inbox and rounds against the local GitLab, with the demo open. And MR !3, frank's own,
as a whole: approved by its reviewer, then merged.

    python3 e2e/news.py reply   # author-bot answers frank's thread on auth.ts
    python3 e2e/news.py push    # author-bot pushes the missing await in comments.ts, no word
    python3 e2e/news.py both
    python3 e2e/news.py fix     # author-bot pushes the auth.ts fix and says it is fixed now
    python3 e2e/news.py approve # reviewer-bot approves MR !3, frank's own
    python3 e2e/news.py merge   # frank merges MR !3
    python3 e2e/news.py more    # reviewer-bot reopens MR !3's naming thread and asks for more
    python3 e2e/news.py doubt   # reviewer-bot reopens it and doubts the pushed rename

Each runs once per rig: a second `push` finds the fix already there and says so. Never
resets anything; `fixture.py` (or tmux-demo.sh --force) is the way back to the start.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fixture as fx  # noqa: E402
from lib.cli import die  # noqa: E402

IID = 1
REPLY_ON = "src/testing/mocks/handlers/auth.ts"
REPLY = ("Good point: /auth/me now answers 401 when there is no user, like the other "
         "handlers, and the dead catch is gone. Pushing it in a minute.")
FIX_FILE = "src/testing/mocks/handlers/comments.ts"
UNAWAITED = "        persistDb('comment');\n"
# /auth/me as the MR leaves it, and as frank's comment asks for it: 401 without a user, like
# every other handler, and no catch that can no longer be reached.
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
FIXED_REPLY = "It is fixed now: /auth/me answers 401 without a user, and the dead catch is gone."


def reply(text=REPLY):
    """author-bot's answer in frank's own thread on REPLY_ON."""
    discs = fx.api(f"projects/{fx.project_path_enc()}/merge_requests/{IID}/discussions?per_page=100")
    for d in discs:
        first = (d.get("notes") or [{}])[0]
        pos = first.get("position") or {}
        if (first.get("author") or {}).get("username") == "frank" and pos.get("new_path") == REPLY_ON:
            fx.reply(IID, d["id"], "author-bot", text)
            fx.info(f"!{IID}: author-bot replied in frank's thread on {REPLY_ON}")
            return
    die(f"no thread of frank's on {REPLY_ON} in MR !{IID}: post one in the demo first")


def push_change(file, old, new, message):
    """author-bot's commit on MR !1's branch, replacing `old` in `file` with `new`."""
    b, url = fx.BUILD, fx.push_url("author-bot")
    fx.git("fetch", "--quiet", url, fx.MR1_BRANCH, cwd=b)
    fx.git("checkout", "--quiet", "--detach", "FETCH_HEAD", cwd=b)
    path = os.path.join(b, file)
    with open(path) as f:
        text = f.read()
    if old not in text:
        die(f"{file} is changed there already: pushed before")
    with open(path, "w") as f:
        f.write(text.replace(old, new, 1))
    fx.git("commit", "--quiet", "-am", message, cwd=b, user="author-bot")
    fx.git("push", "--quiet", url, f"HEAD:refs/heads/{fx.MR1_BRANCH}", cwd=b)
    fx.info(f"!{IID}: author-bot pushed '{message}' ({file})")


def push():
    """author-bot's fix for the un-awaited persistDb call, with no word in the thread."""
    push_change(FIX_FILE, UNAWAITED, "        await persistDb('comment');\n",
                "await the comment-delete persistDb")


def fix():
    """author-bot's fix for /auth/me, then the word in frank's thread that it is fixed."""
    push_change(REPLY_ON, AUTH_ME, AUTH_ME_FIXED, "answer 401 from /auth/me without a user")
    reply(FIXED_REPLY)


REWORK_IID = 3


def approve():
    fx.api(f"projects/{fx.project_path_enc()}/merge_requests/{REWORK_IID}/approve", "POST",
           {}, user="reviewer-bot")
    fx.info(f"!{REWORK_IID}: reviewer-bot approved it")


def merge():
    fx.api(f"projects/{fx.project_path_enc()}/merge_requests/{REWORK_IID}/merge", "PUT",
           {}, user="frank")
    fx.info(f"!{REWORK_IID}: frank merged it")


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


def more(text=MORE):
    """reviewer-bot's follow-up in MR !3's thread on MORE_ON, reopened if resolved."""
    enc = fx.project_path_enc()
    discs = fx.api(f"projects/{enc}/merge_requests/{REWORK_IID}/discussions?per_page=100")
    for d in discs:
        pos = (d.get("notes") or [{}])[0].get("position") or {}
        if pos.get("new_path") == MORE_ON:
            fx.api(f"projects/{enc}/merge_requests/{REWORK_IID}/discussions/{d['id']}?resolved=false",
                   "PUT", {}, user="reviewer-bot")
            fx.reply(REWORK_IID, d["id"], "reviewer-bot", text)
            fx.info(f"!{REWORK_IID}: reviewer-bot {'asked for more' if text == MORE else 'doubts the fix'} on {MORE_ON}")
            return
    die(f"no thread on {MORE_ON} in MR !{REWORK_IID}")


def main():
    what = sys.argv[1] if len(sys.argv) > 1 else ""
    if what in ("approve", "merge", "more", "doubt"):
        fx.E = fx.env()
        return {"approve": approve, "merge": merge, "more": more,
                "doubt": lambda: more(DOUBT)}[what]()
    if what not in ("reply", "push", "both", "fix"):
        die(__doc__.strip())
    fx.E = fx.env()
    if what in ("reply", "both"):
        reply()
    if what in ("push", "both"):
        push()
    if what == "fix":
        fix()


if __name__ == "__main__":
    main()
