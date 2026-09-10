"""Interchange exports, with read-back validation before publishing a manifest."""

from math import isfinite
from pathlib import Path
import struct
import zipfile

from .adapters import DepthPrediction
from .camera import IDENTITY_4, T_VIEWER_FROM_CAMERA
from .errors import GeometryError
from .geometry import Surface, mesh_vertices, vertex_normals

PLY_POINT = struct.Struct("<dddBBB")


def write_ply(path: Path, surface: Surface) -> None:
    header = (
        "ply\nformat binary_little_endian 1.0\n"
        "comment single_image_estimated; partial visible surface; scale unknown\n"
        f"element vertex {len(surface.vertices)}\n"
        "property double x\nproperty double y\nproperty double z\n"
        "property uchar red\nproperty uchar green\nproperty uchar blue\nend_header\n"
    )
    with path.open("xb") as stream:
        stream.write(header.encode("ascii"))
        for point, color in zip(surface.vertices, surface.colors):
            stream.write(PLY_POINT.pack(*point, *color))


def validate_ply(path: Path, expected_count: int) -> None:
    with path.open("rb") as stream:
        header = []
        for _ in range(20):
            line = stream.readline(256).decode("ascii").rstrip("\n")
            header.append(line)
            if line == "end_header":
                break
        required = ["property double x", "property double y", "property double z",
                    "property uchar red", "property uchar green", "property uchar blue", "end_header"]
        if (header[:2] != ["ply", "format binary_little_endian 1.0"]
                or f"element vertex {expected_count}" not in header or header[-7:] != required
                or expected_count <= 0):
            raise GeometryError("invalid_export", "PLY header or point count is invalid.")
        if path.stat().st_size - stream.tell() != expected_count * PLY_POINT.size:
            raise GeometryError("invalid_export", "PLY payload size does not match its point count.")
        for _ in range(expected_count):
            point = PLY_POINT.unpack(stream.read(PLY_POINT.size))
            if not all(isfinite(v) for v in point[:3]):
                raise GeometryError("invalid_export", "PLY contains non-finite coordinates.")


def write_obj(path: Path, surface: Surface) -> tuple:
    used, faces = mesh_vertices(surface)
    normals = vertex_normals(surface)
    with path.open("x", encoding="ascii", newline="\n") as stream:
        stream.write("# Estimated visible surface; arbitrary units; intentionally open\n")
        stream.write("o estimated_visible_surface\n")
        for i in used:
            stream.write("v " + " ".join(format(v, ".17g") for v in surface.vertices[i]) + "\n")
        for i in used:
            stream.write("vn " + " ".join(format(v, ".17g") for v in normals[i]) + "\n")
        for face in faces:
            stream.write("f " + " ".join(f"{i + 1}//{i + 1}" for i in face) + "\n")
    return len(used), len(faces)


def validate_obj(path: Path, expected_vertices: int, expected_faces: int) -> None:
    vertices, normals, faces = 0, 0, 0
    try:
        with path.open(encoding="ascii") as stream:
            for line in stream:
                fields = line.split()
                if not fields or fields[0] == "#" or fields[0] == "o":
                    continue
                if fields[0] in ("v", "vn"):
                    if len(fields) != 4 or not all(isfinite(float(v)) for v in fields[1:]):
                        raise ValueError("non-finite vertex/normal")
                    vertices += fields[0] == "v"
                    normals += fields[0] == "vn"
                elif fields[0] == "f":
                    if len(fields) != 4:
                        raise ValueError("non-triangle face")
                    face = [tuple(map(int, field.split("//"))) for field in fields[1:]]
                    if (any(len(pair) != 2 or pair[0] < 1 or pair[0] > vertices
                            or pair[1] < 1 or pair[1] > normals for pair in face)
                            or len({pair[0] for pair in face}) != 3):
                        raise ValueError("invalid face index")
                    faces += 1
                else:
                    raise ValueError("unsupported OBJ record")
    except (ValueError, UnicodeError) as exc:
        raise GeometryError("invalid_export", f"OBJ failed read-back validation: {exc}.") from exc
    if vertices != expected_vertices or normals != vertices or faces != expected_faces or faces < 1:
        raise GeometryError("invalid_export", "OBJ point/normal/face counts do not match the manifest.")


def write_numerical_bundle(path: Path, prediction: DepthPrediction, surface: Surface, rgb: bytes) -> list:
    try:
        import numpy as np
    except ImportError as exc:
        raise GeometryError("missing_dependency", "NumPy is required for numerical NPZ export.") from exc
    height, width = prediction.camera.height, prediction.camera.width
    native = np.asarray(prediction.native_depth)
    if native.shape != (height, width) or native.dtype.kind != "f":
        raise GeometryError("unexpected_prediction", "Native depth must be a floating point H by W array.")
    valid = np.frombuffer(surface.valid_mask, dtype=np.uint8).reshape(height, width).astype(bool)
    derived = native.astype(np.float64, copy=True)
    if prediction.depth_convention == "ray_distance":
        for v in range(height):
            for u in range(width):
                ray = prediction.camera.ray(u, v)
                derived[v, u] /= sum(x * x for x in ray) ** .5
    elif prediction.depth_convention != "optical_axis_z":
        raise GeometryError("unsupported_depth_convention", "Numerical export requires a known depth convention.")
    # Invalid native samples are preserved; derived zeros must be read with valid_mask.
    derived[~valid] = 0
    arrays = {
        "model_native_depth": native,
        "optical_axis_depth": derived,
        "valid_mask": valid,
        "rgb": np.frombuffer(rgb, dtype=np.uint8).reshape(height, width, 3),
        "K_working": np.asarray(prediction.camera.matrix(), dtype=np.float64),
        "T_model_camera_from_world": np.asarray(prediction.T_model_camera_from_world, dtype=np.float64),
        "T_reconstruction_from_camera": np.asarray(IDENTITY_4, dtype=np.float64),
        "T_export_from_reconstruction": np.asarray(
            T_VIEWER_FROM_CAMERA if surface.coordinate_frame == "viewer_y_up" else IDENTITY_4, dtype=np.float64),
        "point_pixel_indices": np.asarray(surface.pixels, dtype=np.int64),
    }
    if prediction.confidence is not None:
        conf = np.asarray(prediction.confidence)
        if conf.shape != native.shape or conf.dtype.kind != "f":
            raise GeometryError("unexpected_prediction", "Confidence array does not align with depth.")
        arrays["model_native_confidence"] = conf
    if any(array.dtype.kind not in "fbiu" for array in arrays.values()):
        raise GeometryError("invalid_export", "NPZ must contain numeric/bool arrays only.")
    with path.open("xb") as stream:
        np.savez_compressed(stream, **arrays)
    # Reading with allow_pickle=False is a publication check, not just a convention.
    with np.load(path, allow_pickle=False) as saved:
        if set(saved.files) != set(arrays):
            raise GeometryError("invalid_export", "NPZ arrays are missing.")
        for name, original in arrays.items():
            restored = saved[name]
            if (restored.dtype != original.dtype or restored.shape != original.shape
                    or not np.array_equal(restored, original, equal_nan=True)):
                raise GeometryError("invalid_export", f"NPZ array {name} failed read-back validation.")
    return sorted(arrays)


def write_bundle(path: Path, files: list, manifest_path: Path) -> None:
    with zipfile.ZipFile(path, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
        for file in files:
            archive.write(file, arcname=file.name)
        archive.write(manifest_path, arcname="manifest.json")
    with zipfile.ZipFile(path) as archive:
        if archive.testzip() is not None:
            raise GeometryError("invalid_export", "Export bundle failed CRC validation.")
