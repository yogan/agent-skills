#!/usr/bin/env python3
"""Tests for mock_model.py: cutting a recording from transcript lines, and answering from it."""
import json
import os
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


class Recording(unittest.TestCase):
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

    def test_a_runs_own_skills_folder_is_kept_as_the_repos(self):
        run = "{SRC}/agent-skills/e2e/.runs/20261112-213646-try-scrolling/skills-plugin/skills/rework-mr/scripts"
        self.assertEqual(M.to_placeholders({"command": f"python3 {run}/threads.py"}),
                         {"command": "python3 {SRC}/agent-skills/skills/rework-mr/scripts/threads.py"})

    def test_a_prompt_reads_the_same_with_the_engines_reminders_around_it(self):
        self.assertEqual(M.human(reminded("Do  the\nthing")), "Do the thing")


class Answering(unittest.TestCase):
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


class RunMadeIds(unittest.TestCase):
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

    def test_words_and_numbers_are_not_ids(self):
        self.assertEqual(M.ID.findall("added a facade, 1287 lines, deadbeef"), [])


class Offered(unittest.TestCase):
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


class Wire(unittest.TestCase):
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
