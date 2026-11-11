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


if __name__ == "__main__":
    unittest.main()
