import importlib.util
import math
from pathlib import Path
import struct
import tempfile
import unittest
import zipfile

from workers.geometry.adapters import DepthPrediction
from workers.geometry.camera import Camera, IDENTITY_4
from workers.geometry.errors import GeometryError
from workers.geometry.exports import validate_obj, validate_ply, write_bundle, write_numerical_bundle, write_obj, write_ply
from workers.geometry.geometry import reconstruct


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.camera = Camera(3, 3, 2, 2, 1, 1)
        self.rgb = bytes(range(27))
        self.surface = reconstruct([2.] * 9, self.rgb, self.camera)

    def test_binary_ply_round_trip_colors_and_bounds(self):
        path = self.root / "pointcloud.ply"
        write_ply(path, self.surface)
        validate_ply(path, 9)
        data = path.read_bytes()
        header, body = data.split(b"end_header\n", 1)
        self.assertIn(b"element vertex 9", header)
        self.assertNotIn(b"element face", header)
        # Read the public layout without using the exporter's packing helper.
        decoded = list(struct.iter_unpack("<3d3B", body))
        for point, color, restored in zip(self.surface.vertices, self.surface.colors, decoded):
            self.assertEqual((*point, *color), restored)

    def test_obj_round_trip_counts_bounds_and_vertex_normals(self):
        path = self.root / "surface.obj"
        count, faces = write_obj(path, self.surface)
        validate_obj(path, count, faces)
        lines = path.read_text().splitlines()
        vertices = [tuple(map(float, line.split()[1:])) for line in lines if line.startswith("v ")]
        self.assertEqual(vertices, self.surface.vertices)
        self.assertEqual(len([line for line in lines if line.startswith("vn ")]), 9)
        self.assertEqual(len([line for line in lines if line.startswith("f ")]), 8)
        self.assertNotIn("mtllib", path.read_text())

    def test_corrupt_ply_is_rejected(self):
        path = self.root / "pointcloud.ply"
        write_ply(path, self.surface)
        data = path.read_bytes()
        path.write_bytes(data[:-1])
        with self.assertRaises(GeometryError):
            validate_ply(path, 9)
        header, body = data.split(b"end_header\n", 1)
        path.write_bytes(header + b"end_header\n" + struct.pack("<d", math.nan) + body[8:])
        with self.assertRaises(GeometryError):
            validate_ply(path, 9)

    def test_invalid_obj_indices_and_nan_are_rejected(self):
        path = self.root / "surface.obj"
        write_obj(path, self.surface)
        original = path.read_text()
        for corrupt in (original.replace("//1", "//999"), original.replace("v -1", "v nan", 1)):
            path.write_text(corrupt)
            with self.assertRaises(GeometryError):
                validate_obj(path, 9, 8)

    def test_export_files_never_overwrite_existing_content(self):
        path = self.root / "existing.ply"
        path.write_bytes(b"preserve")
        with self.assertRaises(FileExistsError):
            write_ply(path, self.surface)
        self.assertEqual(path.read_bytes(), b"preserve")

    def test_bundle_has_only_relative_paths_and_manifest(self):
        ply = self.root / "pointcloud.ply"
        write_ply(ply, self.surface)
        pending = self.root / ".manifest.pending.json"
        pending.write_text('{"testOnly": true}')
        bundle = self.root / "export.zip"
        write_bundle(bundle, [ply], pending)
        with zipfile.ZipFile(bundle) as archive:
            self.assertEqual(set(archive.namelist()), {"pointcloud.ply", "manifest.json"})
            self.assertEqual(archive.read("pointcloud.ply"), ply.read_bytes())
            self.assertIsNone(archive.testzip())

    @unittest.skipUnless(importlib.util.find_spec("numpy"), "NumPy not installed; numerical NPZ round-trip pending")
    def test_npz_has_numeric_arrays_and_preserves_raw_invalid_depth(self):
        import numpy as np
        native = np.full((3, 3), 2., dtype=np.float32)
        native[0, 0] = np.nan
        surface = reconstruct(native.reshape(-1), self.rgb, self.camera)
        prediction = DepthPrediction(native, np.ones_like(native), self.camera,
                                     [list(r) for r in IDENTITY_4], "optical_axis_z", {}, {})
        path = self.root / "depth.npz"
        write_numerical_bundle(path, prediction, surface, self.rgb)
        with np.load(path, allow_pickle=False) as restored:
            self.assertTrue(np.isnan(restored["model_native_depth"][0, 0]))
            self.assertFalse(restored["valid_mask"][0, 0])
            self.assertEqual(restored["optical_axis_depth"][0, 0], 0)
            self.assertEqual(restored["rgb"].tobytes(), self.rgb)
            self.assertTrue(all(restored[key].dtype.kind in "fibu" for key in restored.files))

    @unittest.skipUnless(importlib.util.find_spec("trimesh"), "trimesh not installed; independent importer check pending")
    def test_independent_obj_importer(self):
        import trimesh
        path = self.root / "surface.obj"
        write_obj(path, self.surface)
        imported = trimesh.load(path, process=False, force="mesh")
        self.assertEqual(len(imported.vertices), 9)
        self.assertEqual(len(imported.faces), 8)
        self.assertFalse(imported.is_watertight)
        self.assertEqual(imported.bounds.tolist(), [[-1., -1., -2.], [1., 1., -2.]])
