"""Conservative depth-grid geometry. No backside completion or hole filling."""

from dataclasses import dataclass
from math import isfinite, sqrt
from typing import Sequence

from .camera import Camera, T_VIEWER_FROM_CAMERA, transform_point
from .errors import GeometryError


@dataclass
class Surface:
    vertices: list
    colors: list
    pixels: list
    faces: list
    valid_mask: bytes
    candidate_faces: int
    rejected_faces: int
    coordinate_frame: str


def face_normal(a: Sequence, b: Sequence, c: Sequence) -> tuple:
    ab = [b[i] - a[i] for i in range(3)]
    ac = [c[i] - a[i] for i in range(3)]
    return (ab[1] * ac[2] - ab[2] * ac[1], ab[2] * ac[0] - ab[0] * ac[2],
            ab[0] * ac[1] - ab[1] * ac[0])


def reconstruct(depth: Sequence, rgb: bytes, camera: Camera, *,
                convention: str = "optical_axis_z", max_relative_edge: float = .1,
                frame: str = "viewer_y_up") -> Surface:
    count = camera.width * camera.height
    if count > 1008 * 1008:
        raise GeometryError("geometry_limit", "Working depth exceeds the Block 0 pixel limit.")
    if len(depth) != count or len(rgb) != count * 3:
        raise GeometryError("unaligned_prediction", "RGB, depth and intrinsics must describe the same pixels.")
    if frame not in ("camera_x_right_y_down_z_forward", "viewer_y_up"):
        raise GeometryError("invalid_frame", "Unknown export coordinate frame.")
    if convention not in ("optical_axis_z", "ray_distance"):
        raise GeometryError("unsupported_depth_convention", "Depth convention must be explicit before meshing.")
    if not isfinite(max_relative_edge) or not (0 < max_relative_edge <= 1):
        raise GeometryError("invalid_settings", "Relative depth edge threshold must be greater than 0 and at most 1.")

    vertices, colors, pixels, z_values = [], [], [], []
    index = [-1] * count
    valid = bytearray(count)
    for pixel, value in enumerate(depth):
        value = float(value)
        if not isfinite(value) or value <= 0:
            continue
        point = camera.back_project(pixel % camera.width, pixel // camera.width, value, convention)
        if not all(isfinite(v) and abs(v) <= 1e15 for v in point):
            continue
        index[pixel] = len(vertices)
        valid[pixel] = 1
        z_values.append(point[2])
        vertices.append(transform_point(point, T_VIEWER_FROM_CAMERA) if frame == "viewer_y_up" else point)
        colors.append(tuple(rgb[pixel * 3:pixel * 3 + 3]))
        pixels.append(pixel)
    if not vertices:
        raise GeometryError("no_valid_depth", "The model returned no usable positive finite depth samples.")

    faces = []
    candidates = 2 * (camera.width - 1) * (camera.height - 1)
    for v in range(camera.height - 1):
        for u in range(camera.width - 1):
            a = v * camera.width + u
            b, c, d = a + 1, a + camera.width, a + camera.width + 1
            # Camera-facing winding; the viewer transform has positive determinant.
            for grid_face in ((a, c, b), (b, c, d)):
                face = tuple(index[p] for p in grid_face)
                if min(face) < 0:
                    continue
                zs = [z_values[i] for i in face]
                # All three edges (including the diagonal) must pass. Scale invariant.
                if max(zs) - min(zs) > max_relative_edge * min(zs):
                    continue
                normal = face_normal(*(vertices[i] for i in face))
                if not all(map(isfinite, normal)) or sum(n * n for n in normal) <= 1e-30:
                    continue
                faces.append(face)
    if not faces:
        raise GeometryError("no_mesh_faces", "No supported partial surface remains. Review depth or the edge threshold.")
    return Surface(vertices, colors, pixels, faces, bytes(valid), candidates,
                   candidates - len(faces), frame)


def vertex_normals(surface: Surface) -> list:
    normals = [[0., 0., 0.] for _ in surface.vertices]
    for face in surface.faces:
        normal = face_normal(*(surface.vertices[i] for i in face))
        for i in face:
            for axis in range(3):
                normals[i][axis] += normal[axis]
    result = []
    for normal in normals:
        length = sqrt(sum(n * n for n in normal))
        # Isolated point-cloud samples are retained in PLY and removed in OBJ.
        result.append(tuple(n / length for n in normal) if length else (0., 0., 0.))
    return result


def mesh_vertices(surface: Surface) -> tuple:
    """Remove unused vertices from OBJ only, retaining the original PLY samples."""
    used = sorted({i for face in surface.faces for i in face})
    remap = {old: new for new, old in enumerate(used)}
    return used, [tuple(remap[i] for i in face) for face in surface.faces]


def bounds(vertices: Sequence) -> dict:
    if not vertices:
        raise GeometryError("empty_geometry", "Cannot compute bounds of an empty result.")
    return {"min": [min(p[i] for p in vertices) for i in range(3)],
            "max": [max(p[i] for p in vertices) for i in range(3)]}
