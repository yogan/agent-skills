import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib import change                                 # noqa: E402

KEYS = "export const queryKeys = {\n  all: ['c'] as const,\n  list: (id) => [...queryKeys.all, id] as const,\n};\n"


class TestChange(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="test-change-")
        self.addCleanup(shutil.rmtree, self.root)
        os.makedirs(os.path.join(self.root, "src"))
        with open(os.path.join(self.root, "src", "keys.ts"), "w") as f:
            f.write(KEYS)

    def test_new_lines_for_a_range_become_a_diff_of_the_file(self):
        d = change.of(self.root, "@@ src/keys.ts:1-3\nexport const commentKeys = {\n  all: ['c'] as const,\n"
                                 "  list: (id) => [...commentKeys.all, id] as const,\n")
        self.assertIn("--- a/src/keys.ts\n+++ b/src/keys.ts\n", d)
        self.assertIn("-export const queryKeys = {\n+export const commentKeys = {\n", d)
        self.assertIn("+  list: (id) => [...commentKeys.all, id] as const,\n", d)
        self.assertNotIn("...\n", d)

    def test_a_clean_hand_written_diff_is_kept_its_counts_set(self):
        d = change.of(self.root, "```diff\n--- a/src/keys.ts\n+++ b/src/keys.ts\n@@ -1,1 +1,1 @@\n-a\n+b\n c\n```")
        self.assertIn("@@ -1,2 +1,2 @@", d)
        self.assertNotIn("```", d)

    def test_a_diff_that_skips_lines_is_refused_with_the_way_out(self):
        with self.assertRaisesRegex(ValueError, r"'\.\.\.'.*@@ <path>:<first>-<last>"):
            change.of(self.root, "--- a/src/keys.ts\n+++ b/src/keys.ts\n@@ -1,2 +1,2 @@\n-a\n+b\n...\n-c\n+d")

    def test_a_range_outside_the_file_is_refused(self):
        with self.assertRaisesRegex(ValueError, "outside the file, which has 4 lines"):
            change.of(self.root, "@@ src/keys.ts:3-9\nx")

    def test_nothing_changed_is_refused(self):
        with self.assertRaisesRegex(ValueError, "nothing changes"):
            change.of(self.root, "@@ src/keys.ts:2\n  all: ['c'] as const,")


if __name__ == "__main__":
    unittest.main()
