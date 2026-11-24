import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib import posted                                 # noqa: E402

URL = "https://gl/g/p/-/merge_requests/3#note_"


def state():
    notes = [{"body": "Which option?", "mine": False, "url": URL + "1"},
             {"body": "Option A, the fix follows.", "mine": True, "url": URL + "2"},
             {"body": "Fixed: link", "mine": True, "url": URL + "3"}]
    return {"threads": {"d1": {"notes": notes}}, "topics": []}, {"id": "t2", "thread_ids": ["d1"]}


class TestPosted(unittest.TestCase):
    def test_the_posted_note_is_the_first_or_the_newest_of_the_user_s(self):
        s, t = state()
        self.assertEqual(posted.own_note(s, t, last=False)[1]["url"], URL + "2")
        self.assertEqual(posted.own_note(s, t, last=True)[1]["url"], URL + "3")

    def test_the_verdict_and_the_correction_are_stored_with_the_note_they_edit(self):
        s, t = state()
        posted.store_differs(s, t, "It drops which option. ", "Option B, the fix follows.", last=False)
        d = t["post_differs"]
        self.assertEqual((d["discussion"], d["note"], d["fix"]), ("d1", "2", "Option B, the fix follows.\n"))
        self.assertEqual(d["posted"], "Option A, the fix follows.")

    def test_a_correction_starts_from_the_text_gitlab_holds_not_the_shown_one(self):
        s, t = state()
        s["threads"]["d1"]["notes"][1].update(body="[a](https://gl/g/p/uploads/1/a)", source="[a](/uploads/1/a)")
        posted.store_differs(s, t, "v", "fix", last=False)
        self.assertEqual(t["post_differs"]["posted"], "[a](/uploads/1/a)")

    def test_only_a_note_posted_after_the_draft_counts(self):
        s, t = state()
        for n, at in zip(s["threads"]["d1"]["notes"], ("2026-11-10T16:00:00Z", "2026-11-10T16:10:00Z", "2026-11-10T16:20:00Z")):
            n["at"] = at
        # Pressed before posting: the comments of theirs are all older than the draft.
        self.assertEqual(posted.own_note(s, t, last=True, since="2026-11-10T16:30:00+00:00"), (None, None))
        with self.assertRaises(ValueError):
            posted.store_differs(s, t, "v", "fix", last=True, since="2026-11-10T16:30:00+00:00")
        # Posted after it: that one, and never the older one before it.
        self.assertEqual(posted.own_note(s, t, last=False, since="2026-11-10T16:15:00+00:00")[1]["url"], URL + "3")

    def test_nothing_posted_is_nothing_to_correct(self):
        s, t = state()
        s["threads"]["d1"]["notes"] = s["threads"]["d1"]["notes"][:1]
        with self.assertRaises(ValueError):
            posted.store_differs(s, t, "v", "fix", last=True)

    def test_the_card_shows_the_verdict_then_the_edit_word_by_word(self):
        s, t = state()
        posted.store_differs(s, t, "It drops which option.", "Option B, the fix follows.", last=False)
        md = posted.card_md(t, "reply")
        self.assertIn("Your posted reply differs**\n\nIt drops which option.", md)
        self.assertIn("```worddiff\nOption [-A-]{+B+}, the fix follows.\n```", md)

    def test_a_line_after_the_edit_stays_out_of_its_code(self):
        """The card ends on what a press did, after the edit: a fence left open by a code
        block inside the word diff swallowed it, drawn as raw markdown."""
        s, t = state()
        was = "Same?\n\n```suggestion\nreturn x\n```"
        s["threads"]["d1"]["notes"][1]["body"] = was
        posted.store_differs(s, t, "Needs the range.", was.replace("suggestion", "suggestion:-0+1"), last=False)
        md = posted.card_md(t, "reply") + "\n\n13:28 You edited it · [comment](https://gl/n)"
        self.assertEqual(sum(1 for ln in md.splitlines() if ln.startswith("```")) % 2, 0)
        self.assertNotIn("{+", md)
        self.assertTrue(md.splitlines()[-2] == "" and md.splitlines()[-3] == "```")

    def test_apply_edits_that_note_on_gitlab(self):
        s, t = state()
        posted.store_differs(s, t, "v", "Option B.", last=False)
        api = mock.Mock()
        self.assertEqual(posted.apply(api, {"enc": "g%2Fp"}, 3, t), URL + "2")
        api.assert_called_once_with("projects/g%2Fp/merge_requests/3/discussions/d1/notes/2",
                                    {"body": "Option B.\n"}, method="PUT")


if __name__ == "__main__":
    unittest.main()
