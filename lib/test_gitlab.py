#!/usr/bin/env python3
"""Tests for the shared GitLab/glab helpers — see gitlab.py's module docstring for why
this, unlike the two skills' own rendering/state logic, moved here wholesale.

Run: `python3 lib/test_gitlab.py` (stdlib only).
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib import gitlab as G

_real_run = G.run
_real_mr_object = G.mr_object


def fake_result(returncode=0, stdout="", stderr=""):
    class R:
        pass
    r = R()
    r.returncode, r.stdout, r.stderr = returncode, stdout, stderr
    return r


class TestParseRemote(unittest.TestCase):
    def test_ssh_style(self):
        self.assertEqual(G.parse_remote("git@gitlab.com:group/proj.git"),
                          ("gitlab.com", "group/proj"))

    def test_https_style(self):
        self.assertEqual(G.parse_remote("https://gitlab.com/group/proj.git"),
                          ("gitlab.com", "group/proj"))

    def test_https_with_embedded_credentials(self):
        self.assertEqual(G.parse_remote("https://user:tok@gitlab.example.com/g/p.git"),
                          ("gitlab.example.com", "g/p"))

    def test_without_dot_git_suffix(self):
        self.assertEqual(G.parse_remote("https://gitlab.com/group/proj"),
                          ("gitlab.com", "group/proj"))


class TestWebBase(unittest.TestCase):
    def test_extracts_the_project_base(self):
        self.assertEqual(
            G.web_base("https://gitlab.example.com/g/p/-/merge_requests/12"),
            "https://gitlab.example.com/g/p")

    def test_none_when_unusable(self):
        self.assertIsNone(G.web_base(None))
        self.assertIsNone(G.web_base("not a merge request url"))


class TestApi(unittest.TestCase):
    def test_parses_a_plain_json_response(self):
        G.run = lambda cmd: fake_result(stdout='{"iid": 1}')
        self.assertEqual(G.api("whatever"), {"iid": 1})

    def test_empty_response_is_an_empty_list(self):
        G.run = lambda cmd: fake_result(stdout="")
        self.assertEqual(G.api("whatever"), [])

    def test_paginated_response_joins_json_arrays(self):
        """--paginate concatenates one JSON array per page: "][" glues two pages'
        arrays together without a separating comma."""
        G.run = lambda cmd: fake_result(stdout="[1,2][3,4]")
        self.assertEqual(G.api("whatever", paginate=True), [1, 2, 3, 4])

    def test_a_bracket_pair_inside_a_comment_is_no_page_break(self):
        """Pages were joined by replacing every `][` in the text, a comment's own included."""
        G.run = lambda cmd: fake_result(stdout='[{"body": "use a[i][j]"}]\n[{"body": "x"}]')
        self.assertEqual(G.api("whatever", paginate=True),
                         [{"body": "use a[i][j]"}, {"body": "x"}])

    def test_failure_dies(self):
        G.run = lambda cmd: fake_result(returncode=1, stderr="boom")
        with self.assertRaises(SystemExit):
            G.api("whatever")


class TestReadsOncePerRun(unittest.TestCase):
    """A run asks GitLab once for what cannot change within it; a read after its own write is
    asked anew."""

    CTX = {"enc": "g%2Fp"}
    SHA1, SHA2 = "1" * 40, "2" * 40

    def setUp(self):
        G.forget()
        self.addCleanup(G.forget)
        # Other tests here put their own in place of these and leave them.
        G.mr_object = _real_mr_object
        self.addCleanup(setattr, G, "run", _real_run)
        self.asked = []

        def fake_run(cmd, input=None):
            self.asked.append(cmd[2] if cmd[2] != "-X" else "WRITE")
            return fake_result(stdout='{"diffs": [], "state": "opened"}')
        G.run = fake_run

    def count(self, part):
        return sum(part in e for e in self.asked)

    def test_the_mr_its_versions_a_compare_and_a_commit_are_asked_once(self):
        compare = f"projects/g%2Fp/repository/compare?from={self.SHA1}&to={self.SHA2}&straight=true"
        for _ in range(3):
            G.mr_object(self.CTX, 7)
            G.versions(self.CTX, 7)
            G.api(compare)
            G.api(f"projects/g%2Fp/repository/commits/{self.SHA2}")
            G.api("projects/g%2Fp/merge_requests/7/versions/3")
        self.assertEqual(len(self.asked), 5, self.asked)

    def test_what_others_change_is_asked_every_time(self):
        for _ in range(2):
            G.api("projects/g%2Fp/merge_requests/7/discussions?per_page=100", paginate=True)
            G.api("projects/g%2Fp/repository/compare?from=main&to=feat&straight=true")
            G.api(f"projects/g%2Fp/repository/commits/{self.SHA2}/statuses")
        self.assertEqual(len(self.asked), 6, self.asked)

    def test_after_a_write_the_mr_is_asked_again_and_a_compare_is_not(self):
        compare = f"projects/g%2Fp/repository/compare?from={self.SHA1}&to={self.SHA2}"
        G.mr_object(self.CTX, 7)
        G.versions(self.CTX, 7)
        G.api(compare)
        G.api_post("projects/g%2Fp/merge_requests/7/discussions/d1/notes", {"body": "x"})
        G.mr_object(self.CTX, 7)
        G.versions(self.CTX, 7)
        G.api(compare)
        self.assertEqual((self.asked.count("projects/g%2Fp/merge_requests/7"), self.count("/versions"),
                          self.count("compare")), (2, 2, 1), self.asked)

    def test_a_refused_write_counts_too(self):
        G.mr_object(self.CTX, 7)
        G.run = lambda cmd, input=None: (self.asked.append("WRITE"), fake_result(returncode=1, stdout="{}"))[1]
        G.api_try("projects/g%2Fp/merge_requests/7/merge", {}, method="PUT")
        G.run = lambda cmd, input=None: (self.asked.append(cmd[2]), fake_result(stdout="{}"))[1]
        G.mr_object(self.CTX, 7)
        self.assertEqual(self.asked.count("projects/g%2Fp/merge_requests/7"), 2)

    def test_fresh_asks_again_for_a_caller_waiting_on_gitlab(self):
        G.versions(self.CTX, 7)
        G.versions(self.CTX, 7, fresh=True)
        self.assertEqual(self.count("/versions"), 2)

    def test_each_caller_gets_its_own_copy(self):
        G.mr_object(self.CTX, 7)["state"] = "changed by one caller"
        self.assertEqual(G.mr_object(self.CTX, 7)["state"], "opened")


class TestTimeLimit(unittest.TestCase):
    """A GitLab call that never answers ends, as a failure that says so."""

    def setUp(self):
        import subprocess
        self.subprocess = subprocess

    def sleeper(self, seconds):
        import tempfile
        d = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, d)
        path = os.path.join(d, "glab")
        with open(path, "w") as fh:
            fh.write(f"#!/bin/sh\nsleep {seconds}\n")
        os.chmod(path, 0o755)
        return path

    def test_a_glab_call_that_runs_out_of_time_fails_saying_so(self):
        from unittest import mock
        glab = self.sleeper(5)
        real = self.subprocess.run
        with mock.patch.object(G, "GLAB_TIMEOUT", 0.3), \
                mock.patch.object(G.subprocess, "run", side_effect=lambda cmd, **kw: real([glab, *cmd[1:]], **kw)):
            r = _real_run(["glab", "api", "user"])
        self.assertEqual(r.returncode, G.TIMED_OUT)
        self.assertIn("did not answer within 0.3 seconds", r.stderr)

    def test_a_post_that_ran_out_of_time_says_it_may_have_gone_through(self):
        from unittest import mock
        with mock.patch.object(G, "run", return_value=self.subprocess.CompletedProcess([], G.TIMED_OUT, "", "late")), \
                mock.patch.object(G, "die", side_effect=SystemExit) as die, self.assertRaises(SystemExit):
            G.api_post("projects/x/notes", {"body": "b"})
        self.assertIn("may or may not have gone through", die.call_args.args[0])

    def test_git_has_no_limit(self):
        from unittest import mock
        with mock.patch.object(G.subprocess, "run") as run:
            _real_run(["git", "status"])
        self.assertIsNone(run.call_args.kwargs["timeout"])


class TestMrView(unittest.TestCase):
    def test_parses_json_on_success(self):
        G.run = lambda cmd: fake_result(stdout='{"iid": 7}')
        self.assertEqual(G.mr_view(), {"iid": 7})

    def test_dies_on_failure(self):
        G.run = lambda cmd: fake_result(returncode=1)
        with self.assertRaises(SystemExit):
            G.mr_view()


class TestCurrentUser(unittest.TestCase):
    def test_returns_username(self):
        G.run = lambda cmd: fake_result(stdout='{"username": "jdoe"}')
        self.assertEqual(G.current_user(), "jdoe")

    def test_none_on_failure(self):
        G.run = lambda cmd: fake_result(returncode=1)
        self.assertIsNone(G.current_user())

    def test_none_on_malformed_json(self):
        G.run = lambda cmd: fake_result(stdout="not json")
        self.assertIsNone(G.current_user())


class TestRememberUser(unittest.TestCase):
    def test_an_answer_is_kept(self):
        state = {}
        self.assertEqual(G.remember_user(state, "jdoe"), "jdoe")
        self.assertEqual(state["me"], "jdoe")

    def test_no_answer_gives_the_one_kept_before(self):
        state = {"me": "jdoe"}
        self.assertEqual(G.remember_user(state, None), "jdoe")
        self.assertEqual(state["me"], "jdoe")

    def test_no_answer_ever_is_none(self):
        self.assertIsNone(G.remember_user({}, None))


class TestMrHead(unittest.TestCase):
    def test_prefers_diff_refs_head_sha(self):
        G.mr_object = lambda ctx, iid: {"diff_refs": {"head_sha": "abc123"}, "sha": "old"}
        self.assertEqual(G.mr_head({}, 1), "abc123")

    def test_falls_back_to_bare_sha(self):
        G.mr_object = lambda ctx, iid: {"sha": "old"}
        self.assertEqual(G.mr_head({}, 1), "old")


class TestMrBase(unittest.TestCase):
    def test_reads_diff_refs_base_sha(self):
        G.mr_object = lambda ctx, iid: {"diff_refs": {"base_sha": "aabbc347"}}
        self.assertEqual(G.mr_base({}, 1), "aabbc347")

    def test_none_when_no_diff_refs(self):
        G.mr_object = lambda ctx, iid: {"sha": "old"}
        self.assertIsNone(G.mr_base({}, 1))


class TestVersions(unittest.TestCase):
    def test_hits_the_versions_endpoint_with_pagination(self):
        seen = {}

        def fake_run(cmd):
            seen["cmd"] = cmd
            return fake_result(stdout='[{"id": 2}, {"id": 1}]')

        G.run = fake_run
        out = G.versions({"enc": "g%2Fp"}, 7)
        self.assertEqual(out, [{"id": 2}, {"id": 1}])
        self.assertIn("--paginate", seen["cmd"])
        self.assertTrue(any("merge_requests/7/versions" in c for c in seen["cmd"]))



class TestTheEndOfTheMr(unittest.TestCase):
    """What the Done box's merge and approval read from GitLab and send to it."""

    def setUp(self):
        G.mr_object = _real_mr_object     # other tests here leave a stand-in behind

    def tearDown(self):
        G.run = _real_run

    def test_a_pipeline_is_running_until_it_passed_or_failed(self):
        of = lambda status: G.pipeline_of({"head_pipeline": {"status": status, "web_url": "u"}})  # noqa: E731
        self.assertEqual(of("running"), {"status": "running", "url": "u", "at": None})
        self.assertEqual(of("manual")["status"], "running")
        self.assertEqual(of("success")["status"], "passed")
        self.assertEqual(of("canceled")["status"], "failed")
        self.assertIsNone(G.pipeline_of({"head_pipeline": None}))

    def test_a_finished_pipeline_says_when_it_finished(self):
        """The News box's "The pipeline passed" stood untimed between timed lines."""
        p = {"status": "success", "web_url": "u", "finished_at": "2026-11-09T15:10:00Z"}
        self.assertEqual(G.pipeline_of({"head_pipeline": p})["at"], "2026-11-09T15:10:00Z")
        self.assertIsNone(G.pipeline_of({"head_pipeline": {**p, "status": "running"}})["at"])

    def test_gitlabs_reason_is_its_message(self):
        self.assertEqual(G.refusal_of(fake_result(1, '{"message":"405 Method Not Allowed"}', "glab: 405 (HTTP 405)")),
                         "405 Method Not Allowed")
        self.assertEqual(G.refusal_of(fake_result(1, '{"message":{"sha":["does not match"]}}')), "sha does not match")
        self.assertEqual(G.refusal_of(fake_result(1, "", "glab: connection refused\nmore")), "connection refused")

    def merged(self, mr):
        sent = []

        def fake_run(cmd, input=None):
            if "-X" in cmd:
                sent.append((cmd[cmd.index("-X") + 1], cmd[-3], json.loads(input)))
                return fake_result(stdout="{}")
            return fake_result(stdout=json.dumps(mr))
        G.run = fake_run
        return G.merge({"enc": "g%2Fp"}, 3), sent

    def test_merge_follows_the_mrs_own_settings_on_the_head_it_was_asked_on(self):
        (how, why), sent = self.merged({"sha": "abc", "squash": True, "force_remove_source_branch": True})
        self.assertEqual((how, why), ("merge", None))
        self.assertEqual(sent, [("PUT", "projects/g%2Fp/merge_requests/3/merge",
                                 {"sha": "abc", "squash": True, "should_remove_source_branch": True})])

    def test_a_running_pipeline_sets_auto_merge_instead(self):
        (how, _), sent = self.merged({"sha": "abc", "head_pipeline": {"status": "running"}})
        self.assertEqual(how, "auto")
        self.assertTrue(sent[0][2]["auto_merge"] and sent[0][2]["merge_when_pipeline_succeeds"])

    def test_a_refused_merge_says_gitlabs_reason(self):
        G.run = lambda cmd, input=None: (fake_result(1, '{"message":"Branch cannot be merged"}') if "-X" in cmd
                                         else fake_result(stdout='{"sha": "abc"}'))
        self.assertEqual(G.merge({"enc": "g%2Fp"}, 3), ("merge", "Branch cannot be merged"))

    def test_a_merge_refused_with_a_bare_http_status_says_what_is_missing(self):
        """A draft is refused with `405 Method Not Allowed`: the MR's own status says why."""
        def refused(status):
            mr = json.dumps({"sha": "abc", "detailed_merge_status": status})
            G.run = lambda cmd, input=None: (fake_result(1, '{"message":"405 Method Not Allowed"}') if "-X" in cmd
                                             else fake_result(stdout=mr))
            return G.merge({"enc": "g%2Fp"}, 3)[1]
        self.assertEqual(refused("draft_status"), "Still a draft")
        self.assertEqual(refused("need_rebase"), "Needs a rebase")
        # A status the end of the MR has no words for: GitLab's own, spaced.
        self.assertEqual(refused("merge_request_blocked"), "Merge request blocked")
        # Nothing more to say than GitLab's message: that, not nothing.
        self.assertEqual(refused("mergeable"), "405 Method Not Allowed")

    def test_gitlabs_own_message_stands_where_the_status_is_unknown_to_the_map(self):
        self.assertEqual(G.merge_refusal({"detailed_merge_status": "merge_time"}, "Merge after 10:00"),
                         "Merge after 10:00")


if __name__ == "__main__":
    unittest.main(verbosity=2)
