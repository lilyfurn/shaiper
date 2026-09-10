import copy
import json
import math
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from workers.geometry.contracts import REQUIRED_FILES, schema, validate_contract, verify_files
from workers.geometry.errors import GeometryError
from workers.geometry.io_utils import sha256_file
from workers.geometry.camera import Camera, IDENTITY_4
from workers.geometry.pipeline import camera_metadata, publish

FIXTURE = Path(__file__).parent / "fixtures" / "manifest.synthetic.v1.json"


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads(FIXTURE.read_text())

    def test_schema_and_shared_fixture_are_valid(self):
        from jsonschema import Draft7Validator
        Draft7Validator.check_schema(schema())
        validate_contract(self.manifest)

    def test_unknown_scale_cannot_claim_meters_or_measurement_accuracy(self):
        for path, value in (("metersPerUnit", 1.), ("scaleStatus", "reference_scaled"),
                            ("provenance", "multiview_reconstructed")):
            invalid = copy.deepcopy(self.manifest)
            invalid[path] = value
            with self.subTest(path=path), self.assertRaises(GeometryError):
                validate_contract(invalid)
        self.manifest["quality"]["accuracy"] = .95
        with self.assertRaises(GeometryError):
            validate_contract(self.manifest)

    def test_nonfinite_and_unpromised_geometry_fail(self):
        self.manifest["geometry"]["pointCloud"]["bounds"]["min"][0] = math.nan
        with self.assertRaises(GeometryError):
            validate_contract(self.manifest)
        self.manifest = json.loads(FIXTURE.read_text())
        self.manifest["geometry"]["partialMesh"]["kind"] = "point_cloud"
        with self.assertRaises(GeometryError):
            validate_contract(self.manifest)

    def test_generated_camera_sidecar_matches_shared_definition(self):
        image = SimpleNamespace(source=self.manifest["sourceImages"][0], width=42, height=42)
        prediction = SimpleNamespace(camera=Camera(42, 42, 42, 42, 20.5, 20.5),
                                     depth_convention="optical_axis_z", T_model_camera_from_world=IDENTITY_4)
        camera = camera_metadata(image, prediction, "viewer_y_up")
        validate_contract(camera, "cameraMetadata")
        self.assertEqual(camera["intrinsicsSource"], "model_estimated")
        self.assertFalse(camera["modelPoseUsedForGeometry"])
        self.assertIsNone(camera["metersPerUnit"])

    def test_missing_provenance_or_path_traversal_is_refused(self):
        del self.manifest["provenance"]
        with self.assertRaises(GeometryError):
            validate_contract(self.manifest)
        self.manifest = json.loads(FIXTURE.read_text())
        self.manifest["files"][0]["name"] = "../private.png"
        with self.assertRaises(GeometryError):
            validate_contract(self.manifest)


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.manifest = json.loads(FIXTURE.read_text())
        self.manifest["files"] = []
        # Test-only file bytes exercise integrity/atomic publication, not inference or formats.
        for name, file_format in REQUIRED_FILES.items():
            path = self.root / name
            path.write_bytes(b"test-only integrity fixture: " + name.encode())
            self.manifest["files"].append({"name": name, "format": file_format,
                                           "byteSize": path.stat().st_size, "sha256": sha256_file(path)})

    def test_all_promised_files_precede_manifest(self):
        publish(self.root, self.manifest)
        self.assertTrue((self.root / "manifest.json").is_file())
        self.assertTrue((self.root / "export.zip").is_file())
        self.assertTrue((self.root / "export.zip.sha256").is_file())
        self.assertFalse((self.root / ".manifest.pending.json").exists())

    def test_tampered_artifact_cannot_publish_success(self):
        (self.root / "pointcloud.ply").write_bytes(b"altered")
        with self.assertRaises(GeometryError):
            publish(self.root, self.manifest)
        self.assertFalse((self.root / "manifest.json").exists())

    def test_missing_artifact_cannot_publish_success(self):
        (self.root / "surface.obj").unlink()
        with self.assertRaises(GeometryError):
            publish(self.root, self.manifest)
        self.assertFalse((self.root / "manifest.json").exists())

    def test_bundle_failure_leaves_no_success_manifest(self):
        with patch("workers.geometry.pipeline.write_bundle", side_effect=OSError("test disk full")):
            with self.assertRaises(OSError):
                publish(self.root, self.manifest)
        self.assertFalse((self.root / "manifest.json").exists())

    def test_duplicate_manifest_entries_are_refused(self):
        self.manifest["files"][-1] = self.manifest["files"][0]
        with self.assertRaises(GeometryError):
            verify_files(self.root, self.manifest["files"])

    def test_published_manifest_cannot_be_replaced(self):
        publish(self.root, self.manifest)
        original = (self.root / "manifest.json").read_bytes()
        with self.assertRaisesRegex(GeometryError, "immutable"):
            publish(self.root, self.manifest)
        self.assertEqual((self.root / "manifest.json").read_bytes(), original)
