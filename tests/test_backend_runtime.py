import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import backend_runtime


class BackendRuntimeTests(unittest.TestCase):
    def test_rejects_backend_from_different_application_build(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            descriptor = Path(directory) / "backend.json"
            descriptor.write_text(
                json.dumps(
                    {
                        "url": "http://127.0.0.1:5000",
                        "pid": 123,
                        "runtime_id": "old-build",
                    }
                ),
                encoding="utf-8",
            )
            with (
                patch.object(backend_runtime, "BACKEND_DESCRIPTOR", descriptor),
                patch.object(backend_runtime, "runtime_identity", return_value="new-build"),
                patch.object(backend_runtime, "_health") as health,
            ):
                self.assertIsNone(backend_runtime.discover_backend())
                health.assert_not_called()

    def test_reuses_backend_from_same_application_build(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            descriptor = Path(directory) / "backend.json"
            descriptor.write_text(
                json.dumps(
                    {
                        "url": "http://127.0.0.1:5003",
                        "pid": 456,
                        "runtime_id": "current-build",
                    }
                ),
                encoding="utf-8",
            )
            with (
                patch.object(backend_runtime, "BACKEND_DESCRIPTOR", descriptor),
                patch.object(backend_runtime, "runtime_identity", return_value="current-build"),
                patch.object(
                    backend_runtime,
                    "_health",
                    return_value={"ok": True, "runtime_id": "current-build"},
                ),
            ):
                endpoint = backend_runtime.discover_backend()

            self.assertIsNotNone(endpoint)
            self.assertEqual("http://127.0.0.1:5003", endpoint.url)
            self.assertEqual(456, endpoint.pid)

    def test_retires_verified_backend_from_older_build(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            descriptor = Path(directory) / "backend.json"
            descriptor.write_text(
                json.dumps(
                    {
                        "url": "http://127.0.0.1:5004",
                        "pid": 789,
                        "runtime_id": "old-build",
                    }
                ),
                encoding="utf-8",
            )
            with (
                patch.object(backend_runtime, "BACKEND_DESCRIPTOR", descriptor),
                patch.object(backend_runtime, "runtime_identity", return_value="new-build"),
                patch.object(
                    backend_runtime,
                    "_health",
                    side_effect=[
                        {"ok": True, "service": "patent-agent", "pid": 789},
                        None,
                    ],
                ),
                patch.object(os, "kill") as kill,
            ):
                retired = backend_runtime.retire_backend_from_other_build()

            self.assertTrue(retired)
            kill.assert_called_once_with(789, backend_runtime.signal.SIGTERM)


if __name__ == "__main__":
    unittest.main()
