import math
import unittest

from workers.geometry.camera import Camera
from workers.geometry.errors import GeometryError
from workers.geometry.geometry import bounds, face_normal, mesh_vertices, reconstruct, vertex_normals


class GeometryTests(unittest.TestCase):
    def setUp(self):
        self.camera = Camera(3, 3, 2., 2., 1., 1.)
        self.rgb = bytes(range(27))

    def test_known_plane_counts_colors_bounds_and_open_edges(self):
        result = reconstruct([2.] * 9, self.rgb, self.camera)
        self.assertEqual(len(result.vertices), 9)
        self.assertEqual(len(result.faces), 8)
        self.assertEqual(result.colors[4], (12, 13, 14))
        self.assertEqual(result.vertices[4], (0., 0., -2.))
        self.assertEqual(bounds(result.vertices), {"min": [-1., -1., -2.], "max": [1., 1., -2.]})
        edges = {}
        for a, b, c in result.faces:
            for edge in ((a, b), (b, c), (c, a)):
                key = tuple(sorted(edge))
                edges[key] = edges.get(key, 0) + 1
        self.assertEqual(sum(count == 1 for count in edges.values()), 8)
        self.assertTrue(all(count in (1, 2) for count in edges.values()))
        self.assertTrue(all(normal == (0., 0., 1.) for normal in vertex_normals(result)))

    def test_invalid_samples_and_their_faces_are_removed(self):
        depth = [2.] * 9
        depth[0], depth[2], depth[8] = math.nan, math.inf, -1.
        result = reconstruct(depth, self.rgb, self.camera)
        self.assertEqual(result.pixels, [1, 3, 4, 5, 6, 7])
        self.assertEqual(result.valid_mask, bytes((0, 1, 0, 1, 1, 1, 1, 1, 0)))
        self.assertTrue(all(0 <= index < 6 for face in result.faces for index in face))
        self.assertTrue(all(math.isfinite(v) for p in result.vertices for v in p))

    def test_mesh_does_not_bridge_depth_step(self):
        depth = [1., 1., 10.] * 3
        result = reconstruct(depth, self.rgb, self.camera)
        self.assertEqual(len(result.faces), 4)
        self.assertTrue(all(len({depth[result.pixels[i]] for i in face}) == 1 for face in result.faces))

    def test_diagonal_edge_is_checked(self):
        camera = Camera(2, 2, 2, 2, .5, .5)
        # Adjacent steps can pass while the diagonal exceeds 10 percent.
        with self.assertRaises(GeometryError):
            reconstruct([1.09, 1., 1.18, 1.09], bytes(12), camera)

    def test_relative_threshold_is_invariant_to_uniform_scale(self):
        depth = [1., 1.01, 3.] * 3
        a = reconstruct(depth, self.rgb, self.camera)
        b = reconstruct([d * 12 for d in depth], self.rgb, self.camera)
        self.assertEqual(a.faces, b.faces)

    def test_ray_distance_converts_before_discontinuity_checks(self):
        distances = [2 * math.sqrt(sum(x * x for x in self.camera.ray(u, v)))
                     for v in range(3) for u in range(3)]
        result = reconstruct(distances, self.rgb, self.camera, convention="ray_distance")
        self.assertEqual(len(result.faces), 8)
        for point in result.vertices:
            self.assertAlmostEqual(point[2], -2.)

    def test_obj_compaction_retains_all_ply_points(self):
        result = reconstruct([1., 1., 10.] * 3, self.rgb, self.camera)
        used, faces = mesh_vertices(result)
        self.assertEqual(len(result.vertices), 9)
        self.assertEqual(len(used), 6)
        self.assertTrue(all(0 <= i < len(used) for face in faces for i in face))

    def test_empty_depth_does_not_create_fake_points(self):
        with self.assertRaisesRegex(GeometryError, "no usable"):
            reconstruct([0.] * 9, self.rgb, self.camera)

    def test_invalid_settings_or_alignment_fail(self):
        for setting in ({"max_relative_edge": 0}, {"max_relative_edge": math.nan},
                        {"frame": "left_handed"}, {"convention": "colorized_depth"}):
            with self.subTest(setting=setting), self.assertRaises(GeometryError):
                reconstruct([2.] * 9, self.rgb, self.camera, **setting)
        with self.assertRaises(GeometryError):
            reconstruct([2.] * 8, self.rgb, self.camera)

    def test_camera_frame_is_camera_facing(self):
        result = reconstruct([2.] * 9, self.rgb, self.camera, frame="camera_x_right_y_down_z_forward")
        self.assertTrue(all(face_normal(*(result.vertices[i] for i in face))[2] < 0 for face in result.faces))
