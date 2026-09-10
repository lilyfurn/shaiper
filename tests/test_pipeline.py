from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from workers.geometry.__main__ import main
from workers.geometry.errors import GeometryError
from workers.geometry.pipeline import RunSettings, run


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_existing_directory_is_not_modified(self):
        output = self.root / "previous run"
        output.mkdir()
        protected = output / "manifest.json"
        protected.write_text("preserve existing result")
        with self.assertRaisesRegex(GeometryError, "already exists"):
            run(self.root / "absent.png", output, Mock(), RunSettings())
        self.assertEqual(protected.read_text(), "preserve existing result")
        self.assertEqual(list(output.iterdir()), [protected])

    def test_invalid_setting_fails_before_creating_output(self):
        output = self.root / "run"
        with self.assertRaises(GeometryError):
            run(self.root / "absent.png", output, Mock(), RunSettings(max_edge=512))
        self.assertFalse(output.exists())

    def test_preprocessing_failure_is_recorded_without_manifest(self):
        output = self.root / "run"
        with self.assertRaises(GeometryError):
            run(self.root / "absent.png", output, Mock(), RunSettings())
        failure = json.loads((output / "failure.json").read_text())
        self.assertEqual(failure["stage"], "preprocess")
        self.assertEqual(failure["geometryGate"], "not_passed")
        self.assertFalse((output / "manifest.json").exists())

    @unittest.skipUnless(importlib.util.find_spec("PIL"), "Pillow not installed")
    def test_model_failure_and_cancellation_do_not_fall_back(self):
        from PIL import Image
        source = self.root / "synthetic test image.png"
        Image.new("RGB", (42, 42), (12, 34, 56)).save(source)
        for index, error in enumerate((GeometryError("test_model_unavailable", "test-only failure"), KeyboardInterrupt())):
            adapter = Mock()
            adapter.predict.side_effect = error
            output = self.root / f"run-{index}"
            with self.assertRaises(type(error)):
                run(source, output, adapter, RunSettings())
            failure = json.loads((output / "failure.json").read_text())
            self.assertEqual(failure["stage"], "estimateDepth")
            self.assertEqual(adapter.predict.call_count, 1)
            self.assertFalse((output / "manifest.json").exists())
            self.assertFalse((output / "pointcloud.ply").exists())

    def test_doctor_reports_missing_dependencies_without_attempting_inference(self):
        stdout = io.StringIO()
        with patch("workers.geometry.__main__.missing_dependencies", return_value=["torch", "depth_anything_3"]):
            with redirect_stdout(stdout):
                result = main(["doctor"])
        report = json.loads(stdout.getvalue())
        self.assertEqual(result, 2)
        self.assertEqual(report["status"], "blocked")
        self.assertFalse(report["inferenceRun"])

    def test_cli_supports_paths_with_spaces_and_actionable_errors(self):
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            result = main(["run", "--image", str(self.root / "missing image.jpg"),
                           "--model-dir", str(self.root / "missing model"),
                           "--output", str(self.root / "output folder")])
        self.assertEqual(result, 2)
        self.assertIn('"code": "input_not_found"', stderr.getvalue())

    @unittest.skipUnless(importlib.util.find_spec("numpy"), "NumPy not installed; complete synthetic pipeline check pending")
    def test_synthetic_pipeline_exports_but_does_not_pass_geometry_gate(self):
        # Dependency injection is exclusively in this test. The CLI only constructs DA3Adapter.
        import numpy as np
        from PIL import Image
        from workers.geometry.adapters import DepthPrediction
        from workers.geometry.camera import Camera, IDENTITY_4
        from workers.geometry.contracts import validate_contract, verify_files

        fixture = json.loads((Path(__file__).parent / "fixtures" / "manifest.synthetic.v1.json").read_text())
        source = self.root / "synthetic test.png"
        Image.new("RGB", (42, 42), (30, 80, 130)).save(source)
        adapter = Mock()
        adapter.predict.return_value = DepthPrediction(
            np.full((42, 42), 2., dtype=np.float32), None, Camera(42, 42, 42, 42, 20.5, 20.5),
            [list(row) for row in IDENTITY_4], "optical_axis_z", fixture["engine"], {},
        )
        output = self.root / "run"
        manifest = run(source, output, adapter, RunSettings(device="cpu"))
        validate_contract(manifest)
        verify_files(output, manifest["files"])
        self.assertTrue((output / "export.zip").is_file())
        self.assertEqual(manifest["quality"]["independentValidation"], "not_performed")
        self.assertIsNone(manifest["metersPerUnit"])
        self.assertEqual(manifest["geometry"]["pointCloud"]["pointCount"], 42 * 42)
        self.assertEqual(manifest["geometry"]["partialMesh"]["faceCount"], 2 * 41 * 41)
        # An interrupt after the manifest rename must not turn a committed result
        # into a failed job. This is still a synthetic plumbing check only.
        from workers.geometry.pipeline import publish

        def interrupt_after_commit(directory, metadata):
            publish(directory, metadata)
            raise KeyboardInterrupt()

        raced_output = self.root / "publication-race"
        with patch("workers.geometry.pipeline.publish", side_effect=interrupt_after_commit):
            committed = run(source, raced_output, adapter, RunSettings(device="cpu"))
        self.assertEqual(committed["artifactId"], json.loads((raced_output / "manifest.json").read_text())["artifactId"])
        self.assertFalse((raced_output / "failure.json").exists())
