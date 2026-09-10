"""Camera math in a right-handed frame: x right, y down, z forward.

Integer (u, v) coordinates denote pixel centers. No physical units are assumed.
These routines intentionally need no inference libraries, so exact fixtures can
exercise the same math on machines that do not have PyTorch or a GPU.
"""

from dataclasses import dataclass
from math import isfinite, sqrt
from typing import Sequence

from .errors import GeometryError

IDENTITY_4 = ((1, 0, 0, 0), (0, 1, 0, 0), (0, 0, 1, 0), (0, 0, 0, 1))
# Rotation by pi about x, determinant +1. Camera forward becomes viewer -z.
T_VIEWER_FROM_CAMERA = ((1, 0, 0, 0), (0, -1, 0, 0), (0, 0, -1, 0), (0, 0, 0, 1))


@dataclass(frozen=True)
class Camera:
    width: int
    height: int
    fx: float
    fy: float
    cx: float
    cy: float
    skew: float = 0.0

    def __post_init__(self):
        values = (self.fx, self.fy, self.cx, self.cy, self.skew)
        if (self.width < 2 or self.height < 2 or not all(map(isfinite, values))
                or self.fx <= 0 or self.fy <= 0):
            raise GeometryError("invalid_camera", "Intrinsics need finite values and positive focal lengths.")
        if not (-self.width <= self.cx <= 2 * self.width
                and -self.height <= self.cy <= 2 * self.height):
            raise GeometryError("invalid_camera", "Principal point is inconsistent with the working image.")

    @classmethod
    def from_matrix(cls, matrix: Sequence, width: int, height: int):
        if len(matrix) != 3 or any(len(row) != 3 for row in matrix):
            raise GeometryError("invalid_camera", "Expected a 3 by 3 camera matrix.")
        k = [[float(v) for v in row] for row in matrix]
        if (not all(isfinite(v) for row in k for v in row)
                or abs(k[1][0]) > 1e-6 or abs(k[2][0]) > 1e-6
                or abs(k[2][1]) > 1e-6 or abs(k[2][2] - 1) > 1e-6):
            raise GeometryError("invalid_camera", "Expected upper-triangular pinhole intrinsics in pixels.")
        return cls(width, height, k[0][0], k[1][1], k[0][2], k[1][2], k[0][1])

    def matrix(self) -> list:
        return [[self.fx, self.skew, self.cx], [0, self.fy, self.cy], [0, 0, 1]]

    def ray(self, u: float, v: float) -> tuple:
        y = (v - self.cy) / self.fy
        return ((u - self.cx - self.skew * y) / self.fx, y, 1.0)

    def back_project(self, u: float, v: float, depth: float,
                     convention: str = "optical_axis_z") -> tuple:
        if not all(map(isfinite, (u, v, depth))) or depth <= 0:
            raise GeometryError("invalid_depth", "Back-projection requires positive finite depth and pixels.")
        ray = self.ray(u, v)
        if convention == "ray_distance":
            depth /= sqrt(sum(value * value for value in ray))
        elif convention != "optical_axis_z":
            raise GeometryError("unsupported_depth_convention", "Inverse/relative disparity needs an explicit adapter.")
        return tuple(depth * value for value in ray)

    def project(self, point: Sequence) -> tuple:
        x, y, z = point
        if not all(map(isfinite, point)) or z <= 0:
            raise GeometryError("invalid_point", "Projection requires a finite point in front of the camera.")
        return (self.fx * x / z + self.skew * y / z + self.cx, self.fy * y / z + self.cy)

    def resized(self, width: int, height: int):
        """Resize with pixel-center alignment: u' = sx * (u + .5) - .5."""
        sx, sy = width / self.width, height / self.height
        return Camera(width, height, self.fx * sx, self.fy * sy,
                      (self.cx + .5) * sx - .5, (self.cy + .5) * sy - .5, self.skew * sx)


def transform_point(point: Sequence, matrix: Sequence) -> tuple:
    return tuple(sum(matrix[i][j] * point[j] for j in range(3)) + matrix[i][3] for i in range(3))


def invert_rigid(matrix: Sequence) -> list:
    """Validate and invert a camera-to-world or world-to-camera rigid transform."""
    if len(matrix) != 4 or any(len(row) != 4 for row in matrix):
        raise GeometryError("invalid_pose", "Expected an explicit 4 by 4 rigid transform.")
    if not all(isfinite(v) for row in matrix for v in row):
        raise GeometryError("invalid_pose", "Pose contains non-finite values.")
    if any(abs(matrix[3][j] - IDENTITY_4[3][j]) > 1e-5 for j in range(4)):
        raise GeometryError("invalid_pose", "Pose has an invalid homogeneous row.")
    r = [row[:3] for row in matrix[:3]]
    for i in range(3):
        for j in range(3):
            if abs(sum(r[i][k] * r[j][k] for k in range(3)) - (i == j)) > 1e-3:
                raise GeometryError("invalid_pose", "Pose rotation is not orthonormal.")
    det = (r[0][0] * (r[1][1] * r[2][2] - r[1][2] * r[2][1])
           - r[0][1] * (r[1][0] * r[2][2] - r[1][2] * r[2][0])
           + r[0][2] * (r[1][0] * r[2][1] - r[1][1] * r[2][0]))
    if abs(det - 1) > 1e-3:
        raise GeometryError("invalid_pose", "Pose must preserve handedness.")
    inverse = [[float(r[j][i]) for j in range(3)] + [0.0] for i in range(3)]
    for i in range(3):
        inverse[i][3] = -sum(inverse[i][j] * matrix[j][3] for j in range(3))
    return inverse + [[0, 0, 0, 1]]


def orientation_transform(orientation: int, width: int, height: int) -> tuple:
    """EXIF raw-to-oriented pixel transform, including mirrored orientations."""
    transforms = {
        1: ((1, 0, 0), (0, 1, 0), (0, 0, 1)),
        2: ((-1, 0, width - 1), (0, 1, 0), (0, 0, 1)),
        3: ((-1, 0, width - 1), (0, -1, height - 1), (0, 0, 1)),
        4: ((1, 0, 0), (0, -1, height - 1), (0, 0, 1)),
        5: ((0, 1, 0), (1, 0, 0), (0, 0, 1)),
        6: ((0, -1, height - 1), (1, 0, 0), (0, 0, 1)),
        7: ((0, -1, height - 1), (-1, 0, width - 1), (0, 0, 1)),
        8: ((0, 1, 0), (-1, 0, width - 1), (0, 0, 1)),
    }
    if orientation not in transforms:
        raise GeometryError("invalid_orientation", "EXIF orientation must be in the range 1 through 8.")
    size = (height, width) if orientation >= 5 else (width, height)
    return transforms[orientation], size
