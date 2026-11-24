# CUI // SP-CTI
# SYNTHETIC benchmark case for icx-ent-01 (planted, labelled in ../labels.yaml). Never deployed or imported by ICDEV.
import base64
import os
import pickle
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

import vb_cmdi  # noqa: E402
import vb_cmdi_hidden  # noqa: E402
import vb_cmdi_safe  # noqa: E402
import vb_pickle  # noqa: E402


class GreetTest(unittest.TestCase):
    def test_greets(self):
        self.assertEqual(vb_cmdi.greet("world"), "hello world")
        self.assertEqual(vb_cmdi_safe.greet("world"), "hello world")
        self.assertEqual(vb_cmdi_hidden.greet("world"), 0)

    def test_prefs(self):
        blob = base64.b64encode(pickle.dumps({"theme": "dark"})).decode()
        self.assertEqual(vb_pickle.load_prefs(blob), {"theme": "dark"})


if __name__ == "__main__":
    unittest.main()
