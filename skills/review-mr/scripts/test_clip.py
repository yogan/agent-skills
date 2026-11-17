#!/usr/bin/env python3
"""clip.sh where there is no pbcopy (anything but macOS): the copy is skipped, as the skill's
docs say, rather than the command failing."""
import os
import shutil
import subprocess
import tempfile
import unittest

CLIP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "clip.sh")


class TestClip(unittest.TestCase):
    def test_without_pbcopy_the_copy_is_skipped(self):
        with tempfile.TemporaryDirectory() as bin_dir:
            # Everything the script and its guard run, and no pbcopy.
            for tool in ("bash", "cat", "dirname", "mktemp", "rm", "python3"):
                os.symlink(shutil.which(tool), os.path.join(bin_dir, tool))
            r = subprocess.run(["bash", CLIP], input="Thanks, done.\n", text=True,
                               capture_output=True, env={"PATH": bin_dir})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("copy skipped", r.stderr)


if __name__ == "__main__":
    unittest.main()
