import math
import unittest

from workers.geometry.camera import Camera, T_VIEWER_FROM_CAMERA, invert_rigid, transform_point
from workers.geometry.errors import GeometryError
from workers.geometry.geometry import face_normal


class CameraTests(unittest.TestCase):
    def setUp(self):
        self.camera = Camera(640, 480, 520., 500., 319.5, 239.5, 3.)

    def test_plane_projects_and_back_projects(self):
        for point in ((-1., -.5, 2.), (0., 0., 2.), (.75, .6, 2.)):
            pixel = self.camera.project(point)
            recovered = self.camera.back_project(*pixel, point[2])
            for expected, actual in zip(point, recovered):
                self.assertAlmostEqual(expected, actual, places=12)

    def test_cube_corners_round_trip(self):
        for x in (-.5, .5):
            for y in (-.5, .5):
                for z in (2., 3.):
                    pixel = self.camera.project((x, y, z))
                    point = self.camera.back_project(*pixel, z)
                    for expected, actual in zip((x, y, z), point):
                        self.assertAlmostEqual(expected, actual, places=12)

    def test_ray_distance_is_not_optical_axis_depth(self):
        point = (1., 1., 2.)
        pixel = self.camera.project(point)
        distance = math.sqrt(6.)
        recovered = self.camera.back_project(*pixel, distance, "ray_distance")
        for a, b in zip(point, recovered):
            self.assertAlmostEqual(a, b, places=12)
        wrong = self.camera.back_project(*pixel, distance, "optical_axis_z")
        self.assertNotAlmostEqual(wrong[2], 2.)

    def test_invalid_depth_and_disparity_refused(self):
        for depth in (0, -1, math.inf, math.nan):
            with self.assertRaises(GeometryError):
                self.camera.back_project(0, 0, depth)
        with self.assertRaises(GeometryError):
            self.camera.back_project(0, 0, 1., "inverse_depth")

    def test_wrong_intrinsics_are_detectable_against_known_fixture(self):
        point = (.8, -.4, 2.)
        pixel = self.camera.project(point)
        wrong = Camera(640, 480, 260., 250., 319.5, 239.5, 1.5)
        recovered = wrong.back_project(*pixel, point[2])
        self.assertGreater(math.dist(point, recovered), .5)

    def test_invalid_camera_matrices_fail(self):
        for matrix in (
            [[0, 0, 0], [0, 1, 0], [0, 0, 1]],
            [[1, 0, 0], [0, 1, 0], [0, 0, 2]],
            [[1, 0, 0], [1, 1, 0], [0, 0, 1]],
            [[math.nan, 0, 0], [0, 1, 0], [0, 0, 1]],
            [[1, 0], [0, 1]],
        ):
            with self.subTest(matrix=matrix), self.assertRaises(GeometryError):
                Camera.from_matrix(matrix, 640, 480)

    def test_pixel_center_resize_preserves_rays(self):
        resized = self.camera.resized(320, 240)
        original_uv = (511., 277.)
        resized_uv = tuple((x + .5) * .5 - .5 for x in original_uv)
        for a, b in zip(self.camera.ray(*original_uv), resized.ray(*resized_uv)):
            self.assertAlmostEqual(a, b, places=12)

    def test_pose_inverse_and_viewer_camera_transform(self):
        pose = [[0, -1, 0, 4], [1, 0, 0, 5], [0, 0, 1, 6], [0, 0, 0, 1]]
        point = (.25, -.5, 2.)
        recovered = transform_point(transform_point(point, pose), invert_rigid(pose))
        for a, b in zip(point, recovered):
            self.assertAlmostEqual(a, b)
        self.assertEqual(transform_point((2, 3, 4), T_VIEWER_FROM_CAMERA), (2, -3, -4))
        self.assertEqual(invert_rigid(T_VIEWER_FROM_CAMERA), [list(r) for r in T_VIEWER_FROM_CAMERA])

    def test_viewer_transform_preserves_winding_and_normals(self):
        triangle = [(-1, -1, 2), (-1, 1, 2), (1, -1, 2)]
        normal = face_normal(*triangle)
        transformed = [transform_point(point, T_VIEWER_FROM_CAMERA) for point in triangle]
        self.assertEqual(face_normal(*transformed), transform_point(normal, T_VIEWER_FROM_CAMERA))
        self.assertGreater(face_normal(*transformed)[2], 0)

    def test_reflection_is_not_a_rigid_camera_rotation(self):
        with self.assertRaises(GeometryError):
            invert_rigid([[1, 0, 0, 0], [0, -1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]])
