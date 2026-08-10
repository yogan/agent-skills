#!/usr/bin/env python3
"""Rebuild the demo/E2E fixture from scratch: project, branches, MRs, threads, state.

Idempotent and state-deriving — safe to run twice, and it derives what exists rather
than assuming a clean slate. Target: well under a minute, fully offline.

    python3 fixture.py                 # everything
    python3 fixture.py --only mr1      # one stage (during development)
    python3 fixture.py --keep-project  # skip the delete/recreate (faster iteration)

Why it deletes and recreates the *project* rather than just the branches: that is what
restarts MR IIDs at 1, so !1/!2/!3 always match the run-of-show and the speaker notes
never rot.

Upstream code comes from a bare mirror in .cache/ (created on first run), so a reset
never needs the network.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import http.client
import urllib.error
import urllib.parse
import urllib.request

# Repo root, 2 levels up from e2e/fixture.py — needed so `lib/` is importable.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from lib.cli import die  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.path.join(HERE, ".env.local")
CACHE = os.path.join(HERE, ".cache", "bulletproof-react.git")
PATCHES = os.path.join(HERE, "patches")
BUILD = os.path.join(HERE, ".cache", "build")

UPSTREAM = "https://github.com/alan2207/bulletproof-react.git"
BASE = "74c76128"          # "validate env variables", 2024-05-03 — parent of e74349ee
WORKDIR = os.path.expanduser("~/src/agent-skills-demo")

# Fixed dates so commit SHAs are identical on every reset — docs can cite them.
DATES = {
    "mr1": "2024-05-04T09:12:00+02:00",
    "mr2": "2024-05-06T14:31:00+02:00",
    "mr3": "2024-05-07T10:05:00+02:00",
}
IDENT = {
    "author-bot": ("Novak, Alex - CD67890", "author-bot@e2e.test"),
    "reviewer-bot": ("Haas, Miriam - EF13579", "reviewer-bot@e2e.test"),
    "peer-bot": ("Okonkwo, Chidi - GH24680", "peer-bot@e2e.test"),
    "frank": ("Blendinger, Frank - AB12345", "frank@e2e.test"),
}


def step(msg):
    print(f"==> {msg}", flush=True)


def info(msg):
    print(f"    {msg}", flush=True)


def env():
    if not os.path.exists(ENV_FILE):
        die(f"{ENV_FILE} missing — run bootstrap.py first")
    out = {}
    with open(ENV_FILE) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k] = v
    return out


E = None            # populated in main()


def token(user):
    key = f"E2E_TOKEN_{user.replace('-', '_').upper()}"
    t = E.get(key)
    if not t:
        die(f"no token for {user} in {ENV_FILE} — re-run bootstrap.py")
    return t


class ApiError(RuntimeError):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code = code


def api(path, method="GET", body=None, user="root", raw_query="", tries=4):
    """Retries transient failures. GitLab on a memory-tight VM answers the occasional
    502/503 or drops a connection, and a fixture that dies on one hiccup is not a
    reliable reset. 4xx is never retried — that is a real answer."""
    url = f"{E['E2E_GITLAB_URL']}/api/v4/{path.lstrip('/')}{raw_query}"
    for attempt in range(1, tries + 1):
        req = urllib.request.Request(
            url, method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"PRIVATE-TOKEN": token(user), "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                payload = r.read().decode()
                return json.loads(payload) if payload else None
        except urllib.error.HTTPError as e:
            detail = e.read().decode()[:300]
            if e.code < 500 or attempt == tries:
                raise ApiError(e.code, f"{method} {path} -> {e.code}: {detail}") from None
        except (urllib.error.URLError, http.client.HTTPException,
                ConnectionError, TimeoutError) as e:
            # RemoteDisconnected is an http.client.HTTPException, NOT a URLError, so a
            # URLError-only net lets a dropped connection kill the whole reset.
            if attempt == tries:
                raise ApiError(0, f"{method} {path} -> {type(e).__name__}: {e}") from None
        time.sleep(2 * attempt)
        info(f"retry {attempt}/{tries - 1}: {method} {path}")


def git(*args, cwd=None, user=None, date=None, check=True):
    """Run git. `user` sets author+committer so GitLab attributes the commit to that
    account (matched by email); `date` pins both dates so SHAs are reproducible."""
    envv = dict(os.environ)
    if user:
        name, mail = IDENT[user]
        envv.update(GIT_AUTHOR_NAME=name, GIT_AUTHOR_EMAIL=mail,
                    GIT_COMMITTER_NAME=name, GIT_COMMITTER_EMAIL=mail)
    if date:
        envv.update(GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
    r = subprocess.run(["git", *args], cwd=cwd, env=envv,
                       capture_output=True, text=True)
    if check and r.returncode != 0:
        die(f"git {' '.join(args)} failed:\n{r.stderr.strip() or r.stdout.strip()}")
    return r


def push_url(user):
    host = E["E2E_GITLAB_HOST"]
    return f"http://{user}:{token(user)}@{host}/{E['E2E_PROJECT']}.git"


def push_branch(user, branch, force=True):
    """Push, then wait until the API can see the branch.

    Creating an MR immediately after the push fails with `source_branch does not
    exist`: the ref is in the repository but the API has not caught up yet. Observed
    on MR !2, while MR !1 got away with it purely because more work happened in
    between — i.e. a race that would surface at the worst possible moment.
    """
    args = ["push", "--quiet"] + (["--force"] if force else []) + \
        [push_url(user), f"{branch}:{branch}"]
    git(*args, cwd=BUILD)
    enc = urllib.parse.quote(branch, safe="")
    for _ in range(30):
        try:
            api(f"projects/{project_path_enc()}/repository/branches/{enc}")
            return
        except ApiError as e:
            if e.code != 404:
                raise
        time.sleep(1)
    die(f"branch {branch} not visible via the API after 30 s")


# ---------------------------------------------------------------- cache


def ensure_cache():
    if os.path.isdir(CACHE):
        if git("rev-parse", "--verify", f"{BASE}^{{commit}}", cwd=CACHE,
               check=False).returncode == 0:
            return
        die(f"{CACHE} exists but lacks {BASE} — delete it and re-run")
    step("cloning upstream mirror (once; every later reset is offline)")
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    git("clone", "--bare", "--quiet", UPSTREAM, CACHE)


# ---------------------------------------------------------------- project


def project_path_enc():
    return E["E2E_PROJECT"].replace("/", "%2F")


def reset_project():
    """Two-phase delete, and neither phase is optional:

    1. `DELETE /projects/:id` only *marks* the project (GitLab 19 has adjourned deletion
       on by default) and **renames** it to `<path>-deletion_scheduled-<id>`.
    2. `DELETE ...?permanently_remove=true&full_path=<the RENAMED path>` actually purges
       it. Passing the original path fails with "`full_path` is incorrect".

    The liveness poll must key on the project **id**, not the path: GitLab leaves a
    redirect route behind, so a GET on the old path keeps returning 200 for the
    marked project and a path-based poll never terminates.
    """
    step("recreating the project (restarts MR IIDs at 1)")
    try:
        p = api(f"projects/{project_path_enc()}")
    except ApiError as e:
        # ONLY a 404 means "absent". Treating every error as absent silently skips the
        # delete and then the create fails with "has already been taken" — which is
        # exactly what happened once, from a single transient GET failure.
        if e.code != 404:
            die(f"cannot determine whether the project exists: {e}")
        p = None
        info("no existing project")
    if p:
        pid = p["id"]
        api(f"projects/{pid}", "DELETE")
        renamed = api(f"projects/{pid}")["path_with_namespace"]
        api(f"projects/{pid}", "DELETE",
            raw_query=f"?permanently_remove=true&full_path={renamed}")
        for _ in range(60):
            time.sleep(2)
            try:
                api(f"projects/{pid}")
            except ApiError as e:
                if e.code == 404:
                    break
        else:
            die(f"project {pid} still present after 120 s")
        info(f"purged project {pid}")

    group = E["E2E_PROJECT"].split("/")[0]
    gid = next((g["id"] for g in api(f"groups?search={group}") or []
                if g["path"] == group), None)
    if gid is None:
        die(f"group {group} missing — re-run bootstrap.py")
    p = api("projects", "POST", {
        "name": E["E2E_PROJECT"].split("/")[1], "path": E["E2E_PROJECT"].split("/")[1],
        "namespace_id": gid, "default_branch": "main", "visibility": "internal",
        "initialize_with_readme": False, "merge_requests_enabled": True,
        "issues_enabled": False, "wiki_enabled": False, "snippets_enabled": False,
        "container_registry_enabled": False, "packages_enabled": False,
        "builds_access_level": "disabled",
        "only_allow_merge_if_all_discussions_are_resolved": False,
    })
    for user, level in (("frank", 40), ("author-bot", 30),
                        ("reviewer-bot", 30), ("peer-bot", 30)):
        uid = api(f"users?username={user}")[0]["id"]
        api(f"projects/{p['id']}/members", "POST",
            {"user_id": uid, "access_level": level})
    info(f"project {p['path_with_namespace']} (id {p['id']})")
    return p["id"]


# ---------------------------------------------------------------- build tree


def fresh_build():
    """A throwaway clone of the mirror where the branches get constructed."""
    if os.path.isdir(BUILD):
        shutil.rmtree(BUILD)
    git("clone", "--quiet", CACHE, BUILD)
    git("config", "user.email", "fixture@e2e.test", cwd=BUILD)
    git("config", "user.name", "fixture", cwd=BUILD)
    return BUILD


def push_main():
    step(f"pushing main @ {BASE}")
    git("checkout", "--quiet", "-B", "main", BASE, cwd=BUILD)
    # As frank: main is a protected branch, and a Developer (the bots) may not push the
    # initial commit to one. frank is Maintainer, and it is the realistic attribution.
    git("push", "--quiet", "--force", push_url("frank"), "main:main", cwd=BUILD)
    head = git("rev-parse", "HEAD", cwd=BUILD).stdout.strip()
    info(f"main = {head[:12]}")
    return head


def apply_patch(name):
    """-p1 explicitly: patches here are stored in standard a/…, b/… form, but a user
    with `diff.noprefix=true` in their git config generates prefix-less diffs, and then
    the default strip level silently eats the leading `src/`. Pinning both sides removes
    that trap — regenerate patches with `git -c diff.noprefix=false diff`."""
    path = os.path.join(PATCHES, name)
    if not os.path.exists(path):
        die(f"missing patch {path}")
    git("apply", "--index", "-p1", path, cwd=BUILD)


# ---------------------------------------------------------------- MR !1


# MR !1 is upstream PR #175 ("standalone mock server"), replayed commit for commit.
# Two REAL commits with real messages, ~139 LOC of app code across 10 files: enough for
# explain-branch to produce chapters (it skips the explainer below ~40 LOC excluding
# tests) while still being reviewable inside a 9-minute segment.
#
# It cannot target `main`: the PR sits later on upstream master than our base, so a diff
# against main would drag in every unrelated master change in between. Hence its own
# pinned target branch.
MR1_PR_MERGE = "5ecd1d1b"          # Merge pull request #175
MR1_PR_BASE = "e8181b4"            # = MR1_PR_MERGE^1, the PR's own base
MR1_PR_COMMITS = ["e19da4d", "8ef3fa3"]      # oldest first
MR1_TARGET = "release/2024-06"
MR1_BRANCH = "feat/standalone-mock-server"
MR1_TITLE = "Run the mock API as a standalone server"
MR1_DESC = """The MSW handlers only existed inside the browser bundle, so Playwright had
no API to talk to unless the app was running with mocks enabled.

- `mock-server.ts` serves the same handlers over HTTP via `@mswjs/http-middleware`
- the mock DB persists to a JSON file when running under Node, and to `localStorage`
  in the browser
- e2e runs point at the standalone server through `.env.example-e2e`

Target is `release/2024-06`.
"""


# The upstream change is one commit; a real MR of this size would not be. Re-cut into
# three by concern so `explain-branch` produces its chapter-per-commit story instead of
# falling back to the flat single-commit format — that story is the whole reason to open
# the talk with an explainer. Same final tree either way, so MR !1's diff is unchanged.
def push_mr1_target():
    """The pinned target branch for MR !1 — the PR's own upstream base."""
    step(f"pushing {MR1_TARGET} @ {MR1_PR_BASE}")
    git("checkout", "--quiet", "-B", MR1_TARGET, MR1_PR_BASE, cwd=BUILD)
    push_branch("frank", MR1_TARGET)


def build_mr1():
    """Upstream PR #175, replayed commit for commit onto its own base — so each
    cherry-pick is guaranteed conflict-free (that is exactly what happened
    historically). Commits are re-authored as author-bot so the MR author and its
    commits agree; messages stay upstream's. The planted flaws fold into the last
    commit, keeping the diff native-looking."""
    step("MR !1 — standalone mock server (real upstream PR #175 + planted flaws)")
    git("checkout", "--quiet", "-B", MR1_BRANCH, MR1_PR_BASE, cwd=BUILD)
    for n, sha in enumerate(MR1_PR_COMMITS):
        r = git("cherry-pick", "--no-commit", sha, cwd=BUILD, check=False)
        if r.returncode != 0:
            die(f"cherry-pick of {sha} conflicted — PR #175 no longer applies to "
                f"{MR1_PR_BASE}, which is the PR's own base and should be "
                f"conflict-free by construction; re-verify the SHAs above")
        subject = git("log", "-1", "--format=%s", sha, cwd=BUILD).stdout.strip()
        if n == len(MR1_PR_COMMITS) - 1:
            apply_patch("mr1-flaws.patch")        # folded in, so it reads as native
        git("commit", "--quiet", "-m", subject, cwd=BUILD, user="author-bot",
            date=DATES["mr1"].replace("09:12", f"09:{12 + n * 9}"))
    push_branch("author-bot", MR1_BRANCH)
    return create_mr("author-bot", MR1_BRANCH, MR1_TITLE, MR1_DESC,
                     expect_iid=1, target=MR1_TARGET)


# ---------------------------------------------------------------- MRs


MR2_BRANCH = "feat/route-level-prefetch"
MR2_TITLE = "Prefetch route data before rendering"
MR2_DESC = """Discussion routes started their queries only after the component mounted, so
every navigation flashed a spinner. The route now warms the cache first.

- a shared query client that loaders can reach outside React
- query-options factories for the discussions endpoints
- a loader on the discussions route
"""

# Your review comments, as if posted last week. Each anchors on a line of the FIRST
# version, so the author's later pushes leave them genuinely outdated — which is what
# the re-review has to reconcile.
#
# summary/severity mirror what you would have recorded locally; `state` is what the
# author does about it, and that is what the demo reads:
#   fixed    -> replied AND fixed in a push  -> ◐ needs-ack, `diff <t>` shows the change
#   asked    -> replied with a question      -> ◐ needs-ack, your turn to answer
#   promised -> replied "will do", no code   -> ◐ needs-ack, but `diff <t>` shows nothing
#   silent   -> no reply at all              -> ○ open, author's turn
MR2_THREADS = [
    {"key": "client-scope", "state": "fixed",
     "file": "src/lib/query-client.ts", "needle": "export const queryClient = new QueryClient({",
     "severity": "high", "summary": "module-scope query client leaks cache across tests/sessions",
     "body": """This client is created at module scope, so it lives for the whole lifetime of
the module — every test file that imports anything reaching this shares one cache,
and after a logout the next user starts with the previous user's data still cached.

Before this MR the client came from `useState` in `AppProvider`, which avoided both.

Could we keep a factory (`createQueryClient()`) and hand the loaders a per-session
instance instead?""",
     "reply": """Good catch — the cross-test leak is real, I hit it in the loader test.

Reworked: `query-client.ts` now exports `createQueryClient()` plus a lazy
`getQueryClient()` for the session, and the loaders call `getQueryClient()`.
Tests build their own client."""},

    {"key": "loader-errors", "state": "asked",
     "file": "src/routes/protected.tsx", "needle": "loader: async ({ params }",
     "severity": "medium", "summary": "loader has no error handling — a failed prefetch blocks the route",
     "body": """What happens when the prefetch fails? `ensureQueryData` rejects, and a rejecting
loader means react-router renders the error element instead of the route — so one
slow or 404-ing discussion takes out the whole discussions section.

Is that intended, or should the loader swallow failures and let the components show
their own error states?""",
     "reply": """Intended for the list — if `/discussions` is down there is nothing to render.

For the single discussion I agree it is wrong. Before I change it: do you want the
loader to swallow *all* errors there, or only 404s? Swallowing everything hides a
500 until the component re-requests it."""},

    {"key": "stale-time", "state": "promised",
     "file": "src/features/discussions/api/getDiscussion.ts",
     "needle": "export const getDiscussionQueryOptions = (discussionId: string) => ({",
     "severity": "low", "summary": "query options carry no staleTime, so the loader refetches on every navigation",
     "body": """These options set no `staleTime`, and the global config does not either. With
`ensureQueryData` that means every navigation into a discussion refetches it, so the
prefetch buys a warm render but costs an extra request each time.

A small `staleTime` (30 s?) would make the prefetch actually pay off.""",
     "reply": "Fair point, will do — I'd rather set it globally in `queryConfig` though, in a follow-up MR."},

    {"key": "options-duplication", "state": "silent",
     "file": "src/features/discussions/api/getDiscussions.ts",
     "needle": "export const getDiscussionsQueryOptions = () => ({",
     "severity": "medium", "summary": "query key duplicated between the options factory and useDiscussions",
     "body": """The key `['discussions']` now exists here *and* in `useDiscussions` below. They
have to stay in sync by hand, and if they drift the loader warms one cache entry while
the hook reads another — a prefetch that silently does nothing.

Can `useDiscussions` consume these options instead of repeating the key?""",
     "reply": None},
]

MR2_PEER_THREAD = {
    "file": "src/routes/protected.tsx", "needle": "import { queryClient } from '@/lib/query-client';",
    "body": """Nit from my side: this import couples the route table to a concrete client
instance. If we ever render two independent apps in one page (we do in Storybook), the
route table pulls in a client nobody asked for.""",
}


MR3_BRANCH = "feat/query-key-factories"
MR3_TITLE = "Extract react-query keys into per-feature factories"
MR3_DESC = """The raw key arrays (`['comments', discussionId]`, `['discussions']`, …) were
repeated across every hook, so renaming one meant hunting string literals.

- `query-keys.ts` per feature, one factory each
- every discussions/comments hook uses the factory
- `updateDiscussion` refetches the discussions namespace after a successful update

No behaviour change intended.
"""


def build_mr3():
    """Authored on the pinned base (see README, "Why it is built this way"), two commits
    so /rework-mr has something to fixup into. Pushed as frank — *your* MR in the demo."""
    step("MR !3 — query-key factories (your own MR, for /rework-mr)")
    git("checkout", "--quiet", "-B", MR3_BRANCH, BASE, cwd=BUILD)
    series = sorted(os.listdir(os.path.join(PATCHES, "mr3")))
    git("am", "--quiet", "--committer-date-is-author-date",
        *[os.path.join(PATCHES, "mr3", p) for p in series], cwd=BUILD)
    push_branch("frank", MR3_BRANCH)
    n = len(series)
    info(f"{n} commits on {MR3_BRANCH}")
    return create_mr("frank", MR3_BRANCH, MR3_TITLE, MR3_DESC,
                     reviewers=("reviewer-bot",), expect_iid=3)


def create_mr(user, branch, title, description, reviewers=("frank",),
              expect_iid=None, target="main"):
    # `source_branch does not exist` right after a push is a cache-coherency artefact:
    # GitLab keeps branch names in its own cache, and the branches API (which
    # push_branch already waits on) is not the same read path as MR validation. So the
    # wait is necessary but not sufficient — this call has to retry too.
    mr = None
    for attempt in range(1, 16):
        try:
            mr = api(f"projects/{project_path_enc()}/merge_requests", "POST", {
                "source_branch": branch, "target_branch": target,
                "title": title, "description": description,
                "remove_source_branch": True,
            }, user=user)
            break
        except ApiError as e:
            if e.code != 400 or "source_branch" not in str(e):
                raise
            if attempt == 15:
                die(f"GitLab never saw branch {branch} for MR creation: {e}")
            time.sleep(2)
    info(f"created after {attempt} attempt(s)" if attempt > 1 else "created")
    if reviewers:
        ids = [api(f"users?username={r}")[0]["id"] for r in reviewers]
        try:
            api(f"projects/{project_path_enc()}/merge_requests/{mr['iid']}", "PUT",
                {"reviewer_ids": ids}, user=user)
        except RuntimeError as e:                 # reviewers are cosmetic, never fatal
            info(f"could not set reviewers on !{mr['iid']}: {e}")
    info(f"!{mr['iid']} {title}  (by {user})")
    if expect_iid and mr["iid"] != expect_iid:
        die(f"expected MR !{expect_iid} but GitLab assigned !{mr['iid']} — the creation "
            f"order in main() defines the demo numbering; fix it there")
    return mr


def locate(iid, path, needle):
    """(new_line, old_path) for the first ADDED line containing `needle`.

    Anchoring by content, not by a hardcoded line number: if a patch is ever retuned, a
    number silently points at the wrong line (or fails) while a needle either finds the
    right line or fails loudly.

    `old_path` comes back as None for an added file — see post_thread().
    """
    for f in api(f"projects/{project_path_enc()}/merge_requests/{iid}/diffs?per_page=100"):
        if f["new_path"] != path:
            continue
        old_path = None if f.get("new_file") else f.get("old_path") or path
        new_line = 0
        for line in (f.get("diff") or "").splitlines():
            if line.startswith("@@"):
                # @@ -a,b +c,d @@ — c is the first line number on the new side
                new_line = int(line.split("+", 1)[1].split(",", 1)[0].split(" ", 1)[0]) - 1
                continue
            if line.startswith("-"):
                continue
            new_line += 1                       # context and additions advance the new side
            if line.startswith("+") and needle in line:
                return new_line, old_path
        die(f"!{iid}: no added line containing {needle!r} in {path}")
    die(f"!{iid}: {path} is not in the diff")


def post_thread(iid, user, path, needle, body):
    """A diff-anchored discussion. Plain MR notes would not do: both skills reason per
    diff line, and `diff <t>` compares the author's change for that position.

    For an ADDED file `old_path` must be omitted entirely — sending the new path there
    (it has no base-side counterpart) makes GitLab answer 500, not 400.
    """
    mr = api(f"projects/{project_path_enc()}/merge_requests/{iid}")
    refs = mr["diff_refs"]
    new_line, old_path = locate(iid, path, needle)
    position = {
        "position_type": "text",
        "base_sha": refs["base_sha"], "start_sha": refs["start_sha"],
        "head_sha": refs["head_sha"],
        "new_path": path, "new_line": new_line,
    }
    if old_path:
        position["old_path"] = old_path
    d = api(f"projects/{project_path_enc()}/merge_requests/{iid}/discussions", "POST",
            {"body": body, "position": position}, user=user)
    info(f"!{iid} thread by {user} on {path}:{d['notes'][0]['position']['new_line']}")
    return d


def reply(iid, discussion_id, user, body):
    api(f"projects/{project_path_enc()}/merge_requests/{iid}/discussions/"
        f"{discussion_id}/notes", "POST", {"body": body}, user=user)


def wait_for_diff(iid, tries=30):
    """MR diffs are generated asynchronously. Anchoring a discussion to a diff line
    before the first version exists fails, so wait for it."""
    for _ in range(tries):
        vs = api(f"projects/{project_path_enc()}/merge_requests/{iid}/versions")
        if vs:
            return vs[0]
        time.sleep(2)
    die(f"MR !{iid} has no diff version after {tries * 2} s")


# ---------------------------------------------------------------- MR !2


def mr2_patch(n):
    series = sorted(os.listdir(os.path.join(PATCHES, "mr2")))
    return os.path.join(PATCHES, "mr2", series[n])


def build_mr2():
    """Only the FIRST version is pushed here. The fix commits land later, after the
    threads and the local baseline exist — see seed_mr2()."""
    step("MR !2 — route-level prefetch (pre-seeded re-review target)")
    git("checkout", "--quiet", "-B", MR2_BRANCH, BASE, cwd=BUILD)
    git("am", "--quiet", "--committer-date-is-author-date", mr2_patch(0), cwd=BUILD)
    push_branch("author-bot", MR2_BRANCH)
    return create_mr("author-bot", MR2_BRANCH, MR2_TITLE, MR2_DESC, expect_iid=2)


def review_worktree(branch):
    """/review-mr works out of a dedicated worktree so your own checkout is untouched.
    The pointer is repo-wide (one path per project), so both reviewed MRs share it."""
    path = WORKDIR + "-review"
    if os.path.isdir(path):
        git("worktree", "remove", "--force", path, cwd=WORKDIR, check=False)
        shutil.rmtree(path, ignore_errors=True)
    git("fetch", "--quiet", "origin", branch, cwd=WORKDIR)
    # --detach, matching what /review-mr itself does (SKILL.md: "Check out detached —
    # nothing here reads the local branch name"). Creating a named branch instead would
    # leave a stray local branch and could collide with the main checkout later.
    git("worktree", "add", "--quiet", "--force", "--detach", path,
        f"origin/{branch}", cwd=WORKDIR)
    return path


def findings(worktree, *args):
    sd = os.path.expanduser("~/.claude/skills/review-mr/scripts/findings.py")
    r = subprocess.run(["python3", sd, *args], cwd=worktree,
                       capture_output=True, text=True)
    if r.returncode != 0:
        die(f"findings.py {' '.join(args)} failed:\n{r.stderr.strip() or r.stdout.strip()}")
    return r.stdout.strip()


def seed_mr2(iid):
    """The whole point of this stage: the re-review state is *not* hand-written, it
    emerges from real operations performed in the right order.

    1. post your threads on version 1 (as frank) and the peer's thread
    2. record them locally through the skill's own CLI (import → link), so no internal
       schema is duplicated here — if a refactor breaks `link`, this fails loudly
    3. `set-head` while the worktree still sits on version 1 — it takes no SHA, it
       marks the current tip, so ordering alone creates the baseline
    4. only then push the author's two fixes, so `updates` has two pushes to report
    5. finally the author's replies, which can now cite the real SHAs
    """
    step("MR !2 — seeding the review conversation")
    # Repo-wide draft language. Must happen after the state wipe, which removes the
    # whole ~/.claude/review-mr/<slug>/ directory including this file.
    wt0 = review_worktree(MR2_BRANCH)
    findings(wt0, "lang", "--set", "en")
    info("draft language set to en")
    posted = []
    for t in MR2_THREADS:
        d = post_thread(iid, "frank", t["file"], t["needle"], t["body"])
        posted.append((t, d["id"]))
    peer = post_thread(iid, "peer-bot", MR2_PEER_THREAD["file"],
                       MR2_PEER_THREAD["needle"], MR2_PEER_THREAD["body"])
    info(f"peer thread {peer['id'][:12]}")

    wt = wt0
    findings(wt, "worktree", "--set", wt)
    seed = [{"kind": "issue", "severity": t["severity"], "source": "both",
             "summary": t["summary"], "file": t["file"], "draft": t["body"]}
            for t, _ in posted]
    seed_file = os.path.join(BUILD, "mr2-seed.json")
    with open(seed_file, "w") as f:
        json.dump(seed, f)
    out = findings(wt, "import", seed_file, "--iid", str(iid))
    info(out)
    # Extract the bare handles rather than splitting the string: `import` renders topic
    # refs for humans (`◈ t1, ◈ t2`), and that decoration is free to change — it did,
    # and a `.replace(" ", "")` split then produced `◈t1`, which `link` cannot resolve.
    tids = re.findall(r"t\d+", out.rsplit(":", 1)[1])
    if len(tids) != len(posted):
        die(f"import reported {len(tids)} topics, expected {len(posted)}: {out!r}")
    for tid, (_t, did) in zip(tids, posted):
        findings(wt, "link", tid, did, "--iid", str(iid))
    info(f"linked {len(tids)} topics to their threads")
    findings(wt, "set-head", "--iid", str(iid))
    info("baseline recorded at version 1")

    step("MR !2 — author pushes two fixes")
    # This stage runs last (it needs the re-cloned working copy for the review
    # worktree), so the build tree is wherever the MR !3 stage left it. Get back on
    # MR !2's branch before applying its fixes.
    git("checkout", "--quiet", MR2_BRANCH, cwd=BUILD)
    git("am", "--quiet", "--committer-date-is-author-date", mr2_patch(1), cwd=BUILD)
    push_branch("author-bot", MR2_BRANCH, force=False)
    git("am", "--quiet", "--committer-date-is-author-date", mr2_patch(2), cwd=BUILD)
    push_branch("author-bot", MR2_BRANCH, force=False)
    info("two pushes on top of the reviewed baseline")

    step("MR !2 — author replies")
    for t, did in posted:
        if t["reply"]:
            reply(iid, did, "author-bot", t["reply"])
            info(f"replied ({t['state']}) on {t['key']}")


# ---------------------------------------------------------------- MR !3 threads

# Deliberately mixed difficulty: /rework-mr goes recommendation-first on a trivial
# topic and grilling-style on a hard one, and that contrast is the thing worth showing.
# All three are genuine consequences of the authored diff, not filler.
MR3_THREADS = [
    # trivial → recommendation-first
    ("src/features/comments/api/query-keys.ts", "export const queryKeys = {",
     """Naming: this one exports `queryKeys`, while the discussions factory exports
`discussionKeys`. Once both are imported in the same file, `queryKeys` reads as
"all query keys" rather than "the comment ones".

Can we settle on `commentKeys` / `discussionKeys`?"""),

    # hard → grilling: a real cache bug that the refactor makes visible
    ("src/features/discussions/api/updateDiscussion.ts", "queryKey: discussionKeys.all,",
     """This changes behaviour, and I think it breaks the update flow.

`discussionKeys.all` is `['discussions']`, but the detail query is keyed
`['discussion', id]` — *singular*. The two share no prefix, so refetching `all`
no longer refetches the discussion we just updated: the detail view keeps showing
the optimistic value until something else invalidates it.

Before this commit the code refetched `['discussion', data.id]`, which was correct.

I see two ways out and I'm not sure which we want:

1. unify the shape to `['discussions', id]` for details, so `all` really is a
   prefix — but that touches every place that reads a detail key
2. keep the shapes and refetch both keys explicitly here

What do you think?"""),

    # small, real asymmetry — gives the overview a third topic cheaply
    ("src/features/discussions/api/query-keys.ts", "export const discussionKeys = {",
     """Asymmetry with the comments factory: that one has a `list()` helper, this one
only has `all`. `getDiscussions` uses `all` directly as the list key, which works,
but it means "all discussions data" and "the discussions list" are the same key —
so a future detail-invalidation can't help but hit the list too.

Worth adding `list()` here for symmetry?"""),
]


def seed_mr3_threads(iid):
    step("MR !3 — seeding reviewer threads")
    for path, needle, body in MR3_THREADS:
        post_thread(iid, "reviewer-bot", path, needle, body)


# ---------------------------------------------------------------- local clone


def reclone_workdir():
    """A stale local branch or a leftover review worktree is exactly the kind of thing
    that bites during a live demo, so the working copy is rebuilt from scratch too."""
    step(f"re-cloning the working copy at {WORKDIR}")
    if os.path.isdir(WORKDIR):
        shutil.rmtree(WORKDIR)
    host = E["E2E_GITLAB_HOST"]
    git("clone", "--quiet", f"http://{host}/{E['E2E_PROJECT']}.git", WORKDIR)
    git("config", "user.name", IDENT["frank"][0], cwd=WORKDIR)
    git("config", "user.email", IDENT["frank"][1], cwd=WORKDIR)
    # push/pull as frank without prompting — /rework-mr force-pushes on every fixup
    git("remote", "set-url", "origin", push_url("frank"), cwd=WORKDIR)
    # Land on MR !3's branch: it is your own MR, so this is where /rework-mr expects to
    # be, and a bare `glab mr view` here then resolves the right MR.
    git("fetch", "--quiet", "origin", MR3_BRANCH, cwd=WORKDIR)
    git("checkout", "--quiet", "-B", MR3_BRANCH, f"origin/{MR3_BRANCH}", cwd=WORKDIR)
    git("branch", f"--set-upstream-to=origin/{MR3_BRANCH}", MR3_BRANCH, cwd=WORKDIR,
        check=False)
    info(f"cloned, on {MR3_BRANCH}")


def wipe_skill_state():
    """Skill state lives outside both git and GitLab, so a project delete and a
    re-clone leave it behind. A stale findings.json would make the live run resume
    mid-review, and the stored worktree pointer would dangle at a deleted path."""
    step("wiping local skill state")
    slug = E["E2E_PROJECT"].replace("/", "-").replace(".", "-")
    for root in ("review-mr", "rework-mr"):
        base = os.path.expanduser(f"~/.claude/{root}")
        if not os.path.isdir(base):
            continue
        for name in sorted(os.listdir(base)):
            if name == slug or name.startswith(f"{slug}--mr"):
                shutil.rmtree(os.path.join(base, name))
                info(f"removed ~/.claude/{root}/{name}")


# ---------------------------------------------------------------- driver


STAGES = ("project", "main", "mr1", "mr2", "mr3", "clone", "state")


def main():
    global E
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", choices=STAGES, action="append",
                    help="run only these stages (repeatable)")
    ap.add_argument("--keep-project", action="store_true",
                    help="do not delete/recreate the project (faster iteration)")
    args = ap.parse_args()
    E = env()
    want = set(args.only or STAGES)

    ensure_cache()
    started = time.time()
    if "project" in want and not args.keep_project:
        reset_project()
    fresh_build()
    if "main" in want:
        push_main()
    if "mr1" in want:
        push_mr1_target()
        mr1 = build_mr1()
        wait_for_diff(mr1["iid"])
    if "mr2" in want:
        mr2 = build_mr2()
        wait_for_diff(mr2["iid"])
    if "mr3" in want:
        mr3 = build_mr3()
        wait_for_diff(mr3["iid"])
        seed_mr3_threads(mr3["iid"])
    # MR !2's conversation is seeded last: it re-clones nothing, but it does need the
    # working copy (for the review worktree), which the clone stage rebuilds.
    if "clone" in want:
        reclone_workdir()
    if "state" in want:
        wipe_skill_state()
    if "mr2" in want:
        seed_mr2(2)
    step(f"fixture ready in {time.time() - started:.0f} s")
    print(f"    {E['E2E_GITLAB_URL']}/{E['E2E_PROJECT']}/-/merge_requests")


if __name__ == "__main__":
    main()
