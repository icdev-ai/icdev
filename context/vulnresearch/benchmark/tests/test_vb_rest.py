# CUI // SP-CTI
# SYNTHETIC benchmark case for icx-ent-01 (planted, labelled in ../labels.yaml). Never deployed or imported by ICDEV.
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

import vb_eval  # noqa: E402
import vb_eval_safe  # noqa: E402
import vb_parser  # noqa: E402
import vb_parser_safe  # noqa: E402
import vb_sqli  # noqa: E402
import vb_sqli_safe  # noqa: E402
import vb_traversal  # noqa: E402
import vb_traversal_safe  # noqa: E402


class BehaviourTest(unittest.TestCase):
    def test_calc(self):
        self.assertEqual(vb_eval.calc("1+1"), 2)
        self.assertEqual(vb_eval_safe.calc("[1, 2]"), [1, 2])

    def test_parse(self):
        self.assertEqual(vb_parser.parse_record("R11,a"), "a")
        self.assertEqual(vb_parser_safe.parse_record("R11,a"), "a")
        with self.assertRaises(ValueError):
            vb_parser_safe.parse_record("R19")

    def test_users(self):
        self.assertEqual(vb_sqli.find_user("alice"), [("alice",)])
        self.assertEqual(vb_sqli_safe.find_user("bob"), [("bob",)])

    def test_docs(self):
        self.assertIn("synthetic", vb_traversal.read_doc("readme.txt"))
        self.assertIn("synthetic", vb_traversal_safe.read_doc("readme.txt"))


if __name__ == "__main__":
    unittest.main()
