#!/usr/bin/env python3
"""Tests for mock_model.py: cutting a recording from transcript lines, and answering from it."""
import json
import os
import tempfile
import sys
import unittest
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mock_model as M  # noqa: E402


def user(text, meta=False):
    return {"type": "user", "isMeta": meta, "message": {"role": "user", "content": text}}


def result(tid):
    return {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": tid, "content": "done"}]}}


def said(mid, *blocks, model="claude-x"):
    return {"type": "assistant", "message": {"id": mid, "model": model, "content": list(blocks)}}


def text(t):
    return {"type": "text", "text": t}


def call(tid, command):
    return {"type": "tool_use", "id": tid, "name": "Bash", "input": {"command": command},
            "caller": {"type": "direct"}}


LINES = [
    user("<command-name>/rework-mr</command-name> <command-args>!3</command-args>"),
    user("Base directory for this skill: the skill's text", meta=True),
    said("m1", {"type": "thinking", "thinking": "hm", "signature": "x"}),
    said("m1", call("tu1", "python3 threads.py sync")),     # the same reply, its next block
    result("tu1"),
    said("m2", text("Two topics.")),
    {"type": "system", "subtype": "whatever"},
    user("[review-ui] The user pressed a button: t1: agreed the proposed plan."),
    said("m3", call("tu2", "python3 threads.py agree t1")),
    result("tu2"),
    said("m4", text("API Error"), model="<synthetic>"),
    said("m5", text("Agreed.")),
]


def request(*messages, tools=True, stream=False):
    return {"model": "claude-x", "stream": stream, "messages": list(messages),
            "tools": [{"name": "Bash"}] if tools else []}


def u(content):
    return {"role": "user", "content": content}


def a(*blocks):
    return {"role": "assistant", "content": list(blocks)}


def reminded(words):
    return [text("<system-reminder>\ncontext the engine adds\n</system-reminder>"), text(words)]


class TestRecording(unittest.TestCase):
    def test_replies_in_order_one_per_message_id(self):
        replies = M.conversation(LINES)
        self.assertEqual([[b["type"] for b in r["content"]] for r in replies],
                         [["tool_use"], ["text"], ["tool_use"], ["text"]])

    def test_thinking_the_engines_extra_fields_and_synthetic_replies_are_left_out(self):
        replies = M.conversation(LINES)
        self.assertEqual(replies[0]["content"],
                         [{"type": "tool_use", "id": "tu1", "name": "Bash",
                           "input": {"command": "python3 threads.py sync"}}])
        self.assertNotIn("API Error", json.dumps(replies))

    def test_a_reply_that_opens_a_turn_names_its_prompts(self):
        replies = M.conversation(LINES)
        self.assertEqual(replies[0]["prompts"][0][:22], "<command-name>/rework-")
        self.assertIn("Base directory", replies[0]["prompts"][1])
        self.assertNotIn("prompts", replies[1])
        self.assertIn("agreed the proposed plan", replies[2]["prompts"][0])

    def test_folders_are_kept_as_placeholders_and_put_back_when_played(self):
        home = os.path.expanduser("~")
        stored = M.to_placeholders({"command": f"cat {home}/notes.txt"})
        self.assertEqual(stored, {"command": "cat {HOME}/notes.txt"})
        self.assertEqual(M.from_placeholders(stored), {"command": f"cat {home}/notes.txt"})

    def test_a_copy_of_the_rig_plays_a_recording_in_its_own_checkouts(self):
        # Recorded in the demo's checkouts, played in a scenario's copy: its checkouts take
        # their place, and this repo's own folder beside them stays where it is.
        copy = tempfile.mkdtemp()
        self.addCleanup(os.rmdir, copy)
        self.addCleanup(setattr, M, "CHECKOUTS", M.CHECKOUTS)
        M.CHECKOUTS = copy
        real = os.path.realpath(copy)
        recorded = {"command": "cd {SRC}/agent-skills-demo-review-mr2 && python3 {SRC}/agent-skills/e2e/news.py"}
        played = M.from_placeholders(recorded)["command"]
        self.assertTrue(played.startswith(f"cd {real}/agent-skills-demo-review-mr2 && "), played)
        self.assertNotIn(real, played.split("&&")[1])
        self.assertNotIn("{SRC}", played)
        # And what such a run says or records reads as the recording does.
        self.assertEqual(M.to_placeholders({"command": played}), recorded)

    def test_a_runs_own_skills_folder_is_kept_as_the_repos(self):
        run = "{SRC}/agent-skills/e2e/.runs/20261112-213646-try-scrolling/skills-plugin/skills/rework-mr/scripts"
        self.assertEqual(M.to_placeholders({"command": f"python3 {run}/threads.py"}),
                         {"command": "python3 {SRC}/agent-skills/skills/rework-mr/scripts/threads.py"})

    def test_a_run_kept_elsewhere_has_its_skills_folder_kept_as_the_repos_too(self):
        """A run with `--out` in a scratchpad recorded the skills as a scratchpad path, which
        names a folder no replay has."""
        run = ("/private/tmp/claude-501/-Users-someone-src/0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0/"
               "scratchpad/runs/20261117-154317-rework-twice/skills-plugin/skills/rework-mr/scripts")
        self.assertEqual(M.to_placeholders({"command": f"SD={run}; python3 $SD/threads.py"}),
                         {"command": f"SD={M._repo_skills()}rework-mr/scripts; python3 $SD/threads.py"})
        self.assertTrue(M._repo_skills().startswith("{SRC}/"))

    def test_a_sessions_scratchpad_is_kept_as_a_placeholder_and_each_play_gets_its_own(self):
        pad = "/private/tmp/claude-501/-Users-someone-src-agent-skills-demo/" \
              "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0/scratchpad"
        stored = M.to_placeholders({"command": f"cat > {pad}/p.py <<'X'\nX\npython3 {pad}/p.py"})
        self.assertEqual(stored["command"], "cat > {SCRATCHPAD}/p.py <<'X'\nX\npython3 {SCRATCHPAD}/p.py")
        plays = []
        for _ in range(2):          # two plays at once: each in a folder of its own, made for it
            here = tempfile.mkdtemp()
            self.addCleanup(os.rmdir, here)
            self.addCleanup(setattr, M, "SCRATCH", M.SCRATCH)
            M.SCRATCH = os.path.join(here, "scratchpad")
            self.addCleanup(os.rmdir, M.SCRATCH)
            plays.append(M.from_placeholders(stored)["command"])
            self.assertTrue(os.path.isdir(M.SCRATCH))
            self.assertIn(f"python3 {M.SCRATCH}/p.py", plays[-1])
        self.assertNotEqual(plays[0], plays[1])

    def test_the_engines_nudge_after_an_empty_reply_is_left_out(self):
        """A reply of thinking alone made the engine ask again; a replay never gives one, so
        the recorded nudge stood as a prompt no replay sends."""
        lines = [user("go"), said("m1", call("tu1", "ls")), result("tu1"),
                 said("m2", {"type": "thinking", "thinking": "hm", "signature": "x"}),
                 user("[Your previous response had no visible output. Please continue."),
                 said("m3", text("Done."))]
        replies = M.conversation(lines)
        self.assertEqual(replies[-1], {"content": [text("Done.")]})

    def test_a_prompt_reads_the_same_with_the_engines_reminders_around_it(self):
        self.assertEqual(M.human(reminded("Do  the\nthing")), "Do the thing")


class TestAnswering(unittest.TestCase):
    def setUp(self):
        main = M.conversation(LINES)
        agent = M.conversation([user("Judge the news on MR !3"), said("j1", text("judged"))])
        self.mock = M.Mock({"streams": [
            {"name": "main", "first": main[0]["prompts"][0], "replies": main},
            {"name": "agent: judge", "first": agent[0]["prompts"][0], "replies": agent}]})
        self.opening = u(reminded("<command-name>/rework-mr</command-name> <command-args>!3</command-args>"))

    def test_the_first_reply_to_the_first_prompt(self):
        self.assertEqual(self.mock.answer(request(self.opening))[0]["id"], "tu1")
        self.assertEqual(self.mock.misses, [])

    def test_the_reply_after_a_tool_result_is_the_next_one(self):
        got = self.mock.answer(request(self.opening, a(call("tu1", "x")), u([{"type": "tool_result"}])))
        self.assertEqual(got, [text("Two topics.")])

    def test_a_button_press_gets_the_reply_recorded_for_it(self):
        got = self.mock.answer(request(
            self.opening, a(call("tu1", "x")), u([{"type": "tool_result"}]), a(text("Two topics.")),
            u(reminded("[review-ui] The user pressed a button: t1: agreed the proposed plan."))))
        self.assertEqual(got[0]["id"], "tu2")
        self.assertEqual(self.mock.misses, [])

    def test_a_prompt_that_changed_since_the_recording_is_off_script(self):
        got = self.mock.answer(request(
            self.opening, a(call("tu1", "x")), u([{"type": "tool_result"}]), a(text("Two topics.")),
            u(reminded("[review-ui] The user pressed a button: t2: dropped."))))
        self.assertTrue(got[0]["text"].startswith(M.OFF))
        self.assertIn("this time the prompt is", self.mock.misses[0])

    def test_more_replies_than_recorded_is_off_script(self):
        history = [self.opening] + [a(text("x")), u([{"type": "tool_result"}])] * 4
        self.mock.answer(request(*history))
        self.assertIn("reply 5 asked for, 4 recorded", self.mock.misses[0])

    def test_two_agents_briefed_alike_are_told_apart_by_their_whole_brief(self):
        brief = "Prepare each topic below in the background. " * 10
        streams = [{"name": f"agent: {t}", "first": (brief + t)[:M.SIG],
                    "opening": M._numbers(f"{brief}{t}: note 9"), "replies": [{"content": [text(t)]}]}
                   for t in ("t1 naming", "t2 the cache")]
        mock = M.Mock({"streams": streams})
        # Each asked for as a run makes it, its note numbered anew.
        self.assertEqual(mock.answer(request(u(brief + "t2 the cache: note 4711"))), [text("t2 the cache")])
        self.assertEqual(mock.answer(request(u(brief + "t1 naming: note 4712"))), [text("t1 naming")])

    def test_a_background_agent_gets_its_own_conversation(self):
        self.assertEqual(self.mock.answer(request(u("Judge the news on MR !3"))), [text("judged")])

    def test_an_unknown_conversation_is_off_script(self):
        self.mock.answer(request(u("Something nobody recorded")))
        self.assertIn("does not have", self.mock.misses[0])

    def test_the_engines_own_request_on_a_conversation_is_not_a_reply(self):
        got = self.mock.answer(request(self.opening, a(call("tu1", "x")),
                                       u("[SUGGESTION MODE: Suggest what the user might type next.]")))
        self.assertEqual(got, [text("")])
        self.assertEqual(self.mock.misses, [])

    def test_a_side_request_with_no_tools_gets_a_neutral_answer(self):
        self.assertEqual(self.mock.answer(request(u("title this"), tools=False)), [text("ok")])
        self.assertEqual(self.mock.misses, [])


class TestModCalls(unittest.TestCase):
    """The mod's own model call has no tools and no transcript: the mod keeps it in a file,
    the recording takes it from there, and a replay answers it by its prompt."""

    HEAD = "News arrived on GitLab merge request !2, which the user is reviewing. " * 4

    def recorded(self, *calls):
        with tempfile.TemporaryDirectory() as d:
            session, kept = os.path.join(d, "s.jsonl"), os.path.join(d, "calls.json")
            with open(session, "w") as fh:
                fh.write("\n".join(json.dumps(x) for x in LINES) + "\n")
            with open(kept, "w") as fh:
                json.dump([{"prompt": p, "reply": r} for p, r in calls], fh)
            return M.record(session, calls=kept)

    def ask(self, mock, prompt):
        return mock.answer(request(u(prompt), tools=False))[0]["text"]

    def test_a_call_is_answered_with_its_recorded_reply(self):
        rec = self.recorded((self.HEAD + "## t1", '{"t1": {"verdict": "settled"}}'))
        mock = M.Mock(rec)
        self.assertEqual(json.loads(self.ask(mock, self.HEAD + "## t1")), {"t1": {"verdict": "settled"}})
        self.assertEqual(self.ask(mock, "title this"), "ok")                  # the engine's own
        self.assertEqual(mock.misses, [])

    def test_items_asked_in_other_batches_than_recorded_are_all_answered(self):
        mock = M.Mock(self.recorded((self.HEAD + "## t1", '{"t1": "a"}'),
                                    (self.HEAD + "## t3 ## t4", '{"t3": "b", "t4": "c"}')))
        self.assertEqual(json.loads(self.ask(mock, self.HEAD + "## t1 ## t3")),
                         {"t1": "a", "t3": "b", "t4": "c"})

    def test_the_nearest_call_wins_where_two_judged_the_same_item(self):
        mock = M.Mock(self.recorded((self.HEAD + "## t1 Alex answered: done", '{"t1": "first"}'),
                                    (self.HEAD + "## t1 Alex answered: not yet, more to come", '{"t1": "second"}')))
        self.assertEqual(json.loads(self.ask(mock, self.HEAD + "## t1 Alex answered: not yet, more to come")),
                         {"t1": "second"})

    def test_an_id_the_run_made_is_followed_into_the_reply(self):
        old, new = "a1b2c3d4e5f6a7b8", "f9e8d7c6b5a4f3e2"
        mock = M.Mock(self.recorded((self.HEAD + f"## {old}: a comment", f'{{"{old}": {{"into": null}}}}')))
        self.assertEqual(json.loads(self.ask(mock, self.HEAD + f"## {new}: a comment")), {new: {"into": None}})


class TestRunMadeIds(unittest.TestCase):
    """A comment posted or a commit made in the run gets an id of its own each time."""

    LINES = [
        user("go"),
        said("m1", call("tu1", "glab post")),
        {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "tu1", "content": "posted 13bc7efd0 on line 12"}]}},
        said("m2", call("tu2", "python3 findings.py propose 13bc7efd0 --into t1")),
    ]

    def setUp(self):
        self.mock = M.Mock({"streams": [{"name": "main", "first": "go",
                                         "replies": M.conversation(self.LINES),
                                         "ids": M.seen_ids(self.LINES)}]})

    def history(self, output):
        return request(u("go"), a(call("tu1", "glab post")),
                       u([{"type": "tool_result", "tool_use_id": "tu1", "content": output}]))

    def test_the_recording_notes_the_ids_each_call_showed(self):
        self.assertEqual(M.seen_ids(self.LINES), {"tu1": ["13bc7efd0"]})

    def test_a_later_call_uses_the_id_this_run_made(self):
        got = self.mock.answer(self.history("posted 19752f950 on line 12"))
        self.assertEqual(got[0]["input"]["command"], "python3 findings.py propose 19752f950 --into t1")

    def test_an_id_that_came_out_the_same_stays(self):
        got = self.mock.answer(self.history("posted 13bc7efd0 on line 12"))
        self.assertEqual(got[0]["input"]["command"], "python3 findings.py propose 13bc7efd0 --into t1")

    def test_a_token_in_a_press_is_the_one_this_press_made(self):
        lines = [user("go"), said("m1", text("ready")),
                 user("Ack and push t1 with --token c0f21fa7"),
                 said("m2", call("tu1", "python3 threads.py push t1 --token c0f21fa7"))]
        mock = M.Mock({"streams": [{"name": "main", "first": "go", "replies": M.conversation(lines),
                                    "ids": M.seen_ids(lines)}]})
        got = mock.answer(request(u("go"), a(text("ready")), u("Ack and push t1 with --token 9ab31e07")))
        self.assertEqual(got[0]["input"]["command"], "python3 threads.py push t1 --token 9ab31e07")

    def test_a_press_s_token_is_followed_beside_a_hash_that_came_out_all_digits(self):
        """A replay pushed with the recording's token: the hand-off also named a commit whose
        short hash this run came out all digits, so the ids did not line up."""
        lines = [user("go"), said("m1", text("ready")),
                 user("fixup=b21b636 and fixup=1c2d3e4, then push t1 --token c0f21fa7"),
                 said("m2", call("tu1", "python3 threads.py push t1 --token c0f21fa7"))]
        mock = M.Mock({"streams": [{"name": "main", "first": "go", "replies": M.conversation(lines),
                                    "ids": M.seen_ids(lines)}]})
        got = mock.answer(request(u("go"), a(text("ready")),
                                  u("fixup=b21b636 and fixup=0325159, then push t1 --token 19bd333a")))
        self.assertEqual(got[0]["input"]["command"], "python3 threads.py push t1 --token 19bd333a")

    def test_a_press_s_token_is_followed_where_the_recording_s_hash_was_all_digits(self):
        """The other way round: the recording's hand-off named a commit whose hash was all
        digits, this run's has letters, and the token was not followed."""
        lines = [user("go"), said("m1", text("ready")),
                 user("fixup=423637e and fixup=8464471, then push t1 --token a052219e"),
                 said("m2", call("tu1", "git commit --fixup=8464471 && threads.py push t1 --token a052219e"))]
        mock = M.Mock({"streams": [{"name": "main", "first": "go", "replies": M.conversation(lines),
                                    "ids": M.seen_ids(lines)}]})
        got = mock.answer(request(u("go"), a(text("ready")),
                                  u("fixup=5dc8b01 and fixup=9e3b868, then push t1 --token b1ee19e4")))
        self.assertEqual(got[0]["input"]["command"], "git commit --fixup=9e3b868 && threads.py push t1 --token b1ee19e4")

    def test_a_hash_that_came_out_all_digits_is_still_followed(self):
        lines = [user("go"), said("m1", call("tu1", "git log")),
                 {"type": "user", "message": {"role": "user", "content": [
                     {"type": "tool_result", "tool_use_id": "tu1", "content": "index 3826373..fbee0f2\n3c0b4b4 feat"}]}},
                 said("m2", call("tu2", "git commit --fixup=3c0b4b4"))]
        mock = M.Mock({"streams": [{"name": "main", "first": "go", "replies": M.conversation(lines),
                                    "ids": M.seen_ids(lines), "hexes": M.seen_ids(lines, M.HEX)}]})
        got = mock.answer(request(u("go"), a(call("tu1", "git log")), u([{"type": "tool_result", "tool_use_id": "tu1",
                                                                           "content": "index 3826373..fbee0f2\n7998180 feat"}])))
        self.assertEqual(got[0]["input"]["command"], "git commit --fixup=7998180")

    def test_a_hash_all_digits_in_the_recording_and_now_is_followed_by_its_full_hash(self):
        """Both short hashes all digits at once (one run in ~25): neither is an id, and the
        recording's stale one went into the fixup. The recording's full hash of it says it is
        a commit; a count beside it, which no full hash starts with, stays as it is."""
        full = "84644714ef9cf35f90ae4b982e9e703e9046d92e"
        lines = [user("go"), said("m1", call("tu0", "git rev-parse HEAD")),
                 {"type": "user", "message": {"role": "user", "content": [
                     {"type": "tool_result", "tool_use_id": "tu0", "content": full}]}},
                 said("m2", call("tu1", "git log")),
                 {"type": "user", "message": {"role": "user", "content": [
                     {"type": "tool_result", "tool_use_id": "tu1", "content": "8464471 feat\n1234567 bytes"}]}},
                 said("m3", call("tu2", "git commit --fixup=8464471 # 1234567 bytes"))]
        mock = M.Mock({"streams": [{"name": "main", "first": "go", "replies": M.conversation(lines),
                                    "ids": M.seen_ids(lines), "hexes": M.seen_ids(lines, M.HEX)}]})
        got = mock.answer(request(
            u("go"), a(call("tu0", "git rev-parse HEAD")),
            u([{"type": "tool_result", "tool_use_id": "tu0", "content": "2190839" + "a" * 33}]),
            a(call("tu1", "git log")),
            u([{"type": "tool_result", "tool_use_id": "tu1", "content": "2190839 feat\n7654321 bytes"}])))
        self.assertEqual(got[0]["input"]["command"], "git commit --fixup=2190839 # 1234567 bytes")

    def test_a_full_hash_followed_is_followed_in_its_short_form_too(self):
        """The recording saw two commits' full hashes (`git show --stat`) and wrote them short
        into a fixup: the run's commits were followed in full, never in the short form, and
        the fixup named a commit this run does not have."""
        then, now = "789884cc0227791933fa0bd8850dc9ae25126b95", "57cb575deceb1870669f15e4c42d5f7ff4058d07"
        lines = [user("go"), said("m1", call("tu1", "git show --stat HEAD")),
                 {"type": "user", "message": {"role": "user", "content": [
                     {"type": "tool_result", "tool_use_id": "tu1", "content": f"commit {then}\n feat"}]}},
                 said("m2", call("tu2", f"git commit --fixup={then[:7]} && git show {then}"))]
        mock = M.Mock({"streams": [{"name": "main", "first": "go", "replies": M.conversation(lines),
                                    "ids": M.seen_ids(lines), "hexes": M.seen_ids(lines, M.HEX)}]})
        got = mock.answer(request(u("go"), a(call("tu1", "git show --stat HEAD")),
                                  u([{"type": "tool_result", "tool_use_id": "tu1", "content": f"commit {now}\n feat"}])))
        self.assertEqual(got[0]["input"]["command"], f"git commit --fixup={now[:7]} && git show {now}")

    def test_an_older_recording_follows_a_hash_that_came_out_all_digits_too(self):
        """A recording made before `hexes` holds only ids with a letter in them: a run whose
        new commit's short hash was all digits showed none, and the stale hash went on."""
        got = self.mock.answer(self.history("posted 196752950 on line 12"))
        self.assertEqual(got[0]["input"]["command"], "python3 findings.py propose 196752950 --into t1")

    def test_words_and_numbers_are_not_ids(self):
        self.assertEqual(M.ID.findall("added a facade, 1287 lines, deadbeef"), [])


class TestOffered(unittest.TestCase):
    def test_a_hand_back_the_run_does_not_offer_becomes_the_agents_last_words(self):
        mock = M.Mock({"streams": []})
        got = mock.offered([{"type": "tool_use", "id": "h", "name": M.HANDBACK,
                             "input": {"message": "stored t2"}}], {"tools": [{"name": "Bash"}]})
        self.assertEqual(got, [text("stored t2")])

    def test_another_tool_the_run_does_not_offer_goes_as_recorded(self):
        mock = M.Mock({"streams": []})
        self.assertEqual(mock.offered([call("x", "ls")], {"tools": [{"name": "Read"}]}),
                         [call("x", "ls")])
        self.assertEqual(mock.misses, [])

    def test_a_home_folder_in_an_output_is_no_id(self):
        home = os.path.expanduser("~")
        self.assertEqual(M.seen_ids([{"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t", "content": f"{home}/x 13bc7efd0"}]}}]),
            {"t": ["13bc7efd0"]})


class TestWire(unittest.TestCase):
    def setUp(self):
        self.mock = M.Mock({"streams": [{"name": "main", "first": "ping", "replies": [
            {"prompts": ["ping"], "content": [call("tu1", "echo hi")]}]}]})
        self.server = M.serve(self.mock)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1/messages?beta=true"

    def post(self, body):
        req = urllib.request.Request(self.url, data=json.dumps(body).encode(),
                                     headers={"content-type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.headers.get("content-type"), r.read().decode()

    def test_a_streamed_reply_carries_the_tool_call_and_stops_for_it(self):
        kind, raw = self.post(request(u("ping"), stream=True))
        self.assertEqual(kind, "text/event-stream")
        data = [json.loads(line[6:]) for line in raw.splitlines() if line.startswith("data: ")]
        self.assertEqual(data[0]["type"], "message_start")
        self.assertEqual(json.loads(data[2]["delta"]["partial_json"]), {"command": "echo hi"})
        self.assertEqual(data[-2]["delta"]["stop_reason"], "tool_use")

    def test_an_unstreamed_reply_is_one_message(self):
        _, raw = self.post(request(u("ping")))
        self.assertEqual(json.loads(raw)["content"][0]["name"], "Bash")


if __name__ == "__main__":
    unittest.main()
