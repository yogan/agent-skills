import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib import worddiff                               # noqa: E402


class TestMarked(unittest.TestCase):
    def test_a_changed_word_is_taken_out_and_put_in(self):
        self.assertEqual(worddiff.marked("we take option A", "we take option B"),
                         "we take option [-A-]{+B+}")

    def test_an_added_sentence_is_put_in_alone(self):
        self.assertEqual(worddiff.marked("Done.", "Done. The cache stays warm."),
                         "Done.{+ The cache stays warm.+}")

    def test_punctuation_is_its_own_token(self):
        self.assertEqual(worddiff.marked("yes, fixed", "yes; fixed"), "yes[-,-]{+;+} fixed")

    def test_unchanged_text_is_returned_as_it_is(self):
        self.assertEqual(worddiff.marked("same\ntext", "same\ntext"), "same\ntext")

    def test_the_block_is_fenced_as_worddiff(self):
        self.assertEqual(worddiff.block(" a \n", "b"), "```worddiff\n[-a-]{+b+}\n```")


class TestBlocks(unittest.TestCase):
    """A text with a code block in it: the prose word by word, the code as code."""
    WAS = "Could this one do the same?\n\n```suggestion\n    return x\n```"

    def test_a_suggestion_s_range_changed_is_a_suggestion_block_not_a_marked_fence(self):
        md = worddiff.blocks(self.WAS, self.WAS.replace("```suggestion", "```suggestion:-0+1"))
        self.assertEqual(md, "```worddiff\nCould this one do the same?\n```\n\n"
                             "```suggestion:-0+1\n    return x\n```")

    def test_no_fence_stands_inside_a_worddiff_block(self):
        md = worddiff.blocks(self.WAS + "\n\nThanks!", self.WAS.replace("x", "y") + "\n\nThanks a lot!")
        inside = None
        for line in md.splitlines():
            if inside:
                self.assertFalse(line.startswith("```") and line != "```", line)
                inside = line != "```"
            elif line == "```worddiff":
                inside = True
        self.assertIn("Thanks{+ a lot+}!", md)
        self.assertIn("    return y", md)

    def test_code_blocks_are_drawn_as_the_caller_shows_code(self):
        md = worddiff.blocks(self.WAS, self.WAS, code=lambda content, info: f"<{info}:{content.strip()}>")
        self.assertTrue(md.endswith("<suggestion:return x>"))

    def test_a_code_block_added_follows_the_prose(self):
        md = worddiff.blocks("Use y.", "Use x.\n\n```ts\nx()\n```")
        self.assertEqual(md, "```worddiff\nUse [-y-]{+x+}.\n```\n\n```ts\nx()\n```")

    def test_prose_alone_is_one_block_as_before(self):
        self.assertEqual(worddiff.blocks("a", "b"), worddiff.block("a", "b"))


if __name__ == "__main__":
    unittest.main()
