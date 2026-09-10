import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from workers.geometry.adapters.da3 import DA3Adapter, model_lock, verify_checkpoint, verify_engine_installation
from workers.geometry.errors import GeometryError


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_missing_model_has_no_download_fallback(self):
        with self.assertRaisesRegex(GeometryError, "Nothing is downloaded"):
            verify_checkpoint(self.root / "absent")

    def test_wrong_model_architecture_and_lfs_pointer_fail(self):
        (self.root / "config.json").write_text('{"model_name":"da3-giant"}')
        (self.root / "model.safetensors").write_bytes(b"version https://git-lfs.github.com/spec/v1")
        with self.assertRaisesRegex(GeometryError, "Only the pinned"):
            verify_checkpoint(self.root)
        (self.root / "config.json").write_text('{"model_name":"da3-small"}')
        with self.assertRaisesRegex(GeometryError, "Git LFS pointer"):
            verify_checkpoint(self.root)

    def test_checkpoint_integrity_uses_actual_bytes(self):
        # Tiny test-only checksum fixture, never passed to DA3 or made available through the CLI.
        payload = b"test-only checksum content, not model weights"
        lock = {**model_lock(), "weightsByteSize": len(payload), "weightsSha256": hashlib.sha256(payload).hexdigest()}
        (self.root / "config.json").write_text('{"model_name":"da3-small"}')
        weights = self.root / "model.safetensors"
        weights.write_bytes(payload)
        with patch("workers.geometry.adapters.da3.model_lock", return_value=lock):
            result = verify_checkpoint(self.root)
            self.assertEqual(result["weightsSha256"], lock["weightsSha256"])
            weights.write_bytes(b"x" * len(payload))
            with self.assertRaises(GeometryError):
                verify_checkpoint(self.root)

    def test_unpinned_or_editable_backend_is_refused(self):
        distribution = Mock(version="0.0.0")
        for value in ({}, {"dir_info": {"editable": True}}, {"vcs_info": {"commit_id": "0" * 40}}):
            distribution.read_text.return_value = json.dumps(value)
            with patch("importlib.metadata.distribution", return_value=distribution), self.assertRaises(GeometryError):
                verify_engine_installation()
        distribution.read_text.return_value = json.dumps({"vcs_info": {"commit_id": model_lock()["sourceCommit"]}})
        with patch("importlib.metadata.distribution", return_value=distribution):
            self.assertEqual(verify_engine_installation()["sourceCommit"], model_lock()["sourceCommit"])

    def test_missing_dependencies_fail_before_importing_the_model(self):
        with patch("workers.geometry.adapters.da3.missing_dependencies", return_value=["torch"]):
            with self.assertRaisesRegex(GeometryError, "no installation was attempted"):
                DA3Adapter(self.root).predict(None)
