from __future__ import annotations

import unittest

from scripts.init_db import _describe_postgres_target


class InitDbScriptTests(unittest.TestCase):
    def test_describe_postgres_target_redacts_credentials(self) -> None:
        label = _describe_postgres_target("postgresql://user:secret@example.com:5432/second_brain")
        self.assertEqual(label, "example.com/second_brain")
        self.assertNotIn("secret", label)
        self.assertNotIn("user", label)


if __name__ == "__main__":
    unittest.main()
