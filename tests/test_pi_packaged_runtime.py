from pathlib import Path
import unittest
from unittest.mock import patch

from pi_coding_agent_client import _resolve_pi_invocation


class PiPackagedRuntimeTests(unittest.TestCase):
    @patch("pi_coding_agent_client.bundled_pi_invocation")
    def test_default_pi_uses_bundled_windows_runtime(self, bundled) -> None:
        bundled.return_value = (Path("C:/PatentAgent/pi-runtime/node.exe"), Path("C:/PatentAgent/pi-runtime/cli.js"))

        invocation = _resolve_pi_invocation("pi")

        self.assertEqual(
            ["C:/PatentAgent/pi-runtime/node.exe", "C:/PatentAgent/pi-runtime/cli.js"],
            invocation,
        )

    @patch("pi_coding_agent_client.bundled_pi_invocation", return_value=None)
    def test_explicit_pi_path_is_preserved(self, _bundled) -> None:
        self.assertEqual(["C:\\Tools\\pi.cmd"], _resolve_pi_invocation("C:\\Tools\\pi.cmd"))


if __name__ == "__main__":
    unittest.main()
