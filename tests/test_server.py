from __future__ import annotations

import unittest
from unittest.mock import patch

from app import server


class ServerTests(unittest.TestCase):
    def test_already_running_requires_exact_application_identity_and_version(self) -> None:
        valid = {
            "ok": True,
            "application": server.APPLICATION_ID,
            "version": server.APP_VERSION,
        }
        with patch.object(server, "_health_payload", return_value=valid):
            self.assertTrue(server._already_running("127.0.0.1", 8765))

        for invalid in (
            {**valid, "application":"another-app"},
            {**valid, "version":server.APP_VERSION - 1},
            {"ok":True, "version":server.APP_VERSION},
            None,
        ):
            with self.subTest(payload=invalid), patch.object(
                server, "_health_payload", return_value=invalid
            ):
                self.assertFalse(server._already_running("127.0.0.1", 8765))


if __name__ == "__main__":
    unittest.main()
