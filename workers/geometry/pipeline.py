"""Local job execution. A successful manifest is the final publication marker."""

from dataclasses import dataclass
from datetime import datetime, timezone
import math
from pathlib import Path
import time
import uuid

from .adapters import DepthAdapter
from .benchmark import environment, markdown_report, peak_process_memory
from .camera import IDENTITY_4, T_VIEWER_FROM_CAMERA, invert_rigid
from .contracts import REQUIRED_FILES, validate_contract, verify_files
from .errors import GeometryError
from .exports import (validate_obj, validate_ply, write_bundle, write_numerical_bundle, write_obj, write_ply)
from .geometry import bounds, reconstruct
from .images import prepare_image
from .io_utils import sha256_file, write_json


@dataclass(frozen=True)
class RunSettings:
    max_edge: int = 504
    max_relative_edge: float = .1
    frame: str = "viewer_y_up"
    device: str = "cuda"

    def validate(self):
        if (self.max_edge < 140 or self.max_edge > 1008 or self.max_edge % 14
                or not math.isfinite(self.max_relative_edge) or not 0 < self.max_relative_edge <= 1
                or self.frame not in ("viewer_y_up", "camera_x_right_y_down_z_forward")
                or self.device not in ("cpu", "cuda")):
            raise GeometryError("invalid_settings", "Invalid resolution, edge threshold, coordinate frame or device.")


def camera_metadata(image, prediction, frame: str) -> dict:
    transform = T_VIEWER_FROM_CAMERA if frame == "viewer_y_up" else IDENTITY_4
    return {
        "schemaVersion": "1.0.0", "imageId": image.source["imageId"],
        "resolution": [image.width, image.height], "K_working": prediction.camera.matrix(),
        "intrinsicsSource": "model_estimated", "distortion": "unmodeled_no_undistortion",
        "depthConvention": prediction.depth_convention, "depthScale": "relative",
        "scaleStatus": "unknown", "metersPerUnit": None,
        "reconstructionFrame": "camera_x_right_y_down_z_forward", "exportFrame": frame,
        "T_reconstruction_from_camera": IDENTITY_4, "T_export_from_reconstruction": transform,
        "T_model_camera_from_world": prediction.T_model_camera_from_world,
        "T_model_world_from_camera": invert_rigid(prediction.T_model_camera_from_world),
        "modelPoseUsedForGeometry": False, "poseSource": "single_camera_identity",
        "preprocessing": image.source["preprocessing"],
    }


def publish(directory: Path, manifest: dict) -> None:
    """Build and verify the bundle before atomically making success visible."""
    if (directory / "manifest.json").exists():
        raise GeometryError("output_exists", "A published manifest is immutable.")
    validate_contract(manifest)
    verify_files(directory, manifest["files"])
    pending = directory / ".manifest.pending.json"
    write_json(pending, manifest)
    write_bundle(directory / "export.zip", [directory / name for name in REQUIRED_FILES], pending)
    with (directory / "export.zip.sha256").open("x", encoding="ascii", newline="\n") as stream:
        stream.write(sha256_file(directory / "export.zip") + "  export.zip\n")
    pending.replace(directory / "manifest.json")


def run(input_path: Path, output_dir: Path, adapter: DepthAdapter,
        settings: RunSettings, on_stage=None) -> dict:
    settings.validate()
    # Reserving the directory with mkdir is exclusive on both Windows and POSIX.
    # Existing runs, including failed attempts, are never overwritten.
    output_dir = output_dir.resolve()
    try:
        output_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise GeometryError("output_exists", "Output already exists. Choose a new run directory; previous results are immutable.") from exc
    job_id, artifact_id = uuid.uuid4().hex, uuid.uuid4().hex
    manifest = None
    started = time.perf_counter()
    stage, stage_started, timings = "preprocess", started, {}

    def advance(name):
        nonlocal stage, stage_started
        now = time.perf_counter()
        timings[stage + "Seconds"] = now - stage_started
        stage, stage_started = name, now
        if on_stage:
            on_stage(name, job_id)

    try:
        if on_stage:
            on_stage(stage, job_id)
        image = prepare_image(input_path, output_dir / "working.png", settings.max_edge)
        advance("estimateDepth")
        prediction = adapter.predict(image)
        if (prediction.camera.width, prediction.camera.height) != (image.width, image.height):
            raise GeometryError("unaligned_prediction", "Prediction intrinsics refer to a different image size.")
        advance("geometry")
        surface = reconstruct(prediction.native_depth.reshape(-1), image.rgb, prediction.camera,
                              convention=prediction.depth_convention,
                              max_relative_edge=settings.max_relative_edge, frame=settings.frame)
        camera = camera_metadata(image, prediction, settings.frame)
        validate_contract(camera, "cameraMetadata")
        advance("exportValidation")
        write_json(output_dir / "camera.json", camera)
        write_ply(output_dir / "pointcloud.ply", surface)
        mesh_count, face_count = write_obj(output_dir / "surface.obj", surface)
        arrays = write_numerical_bundle(output_dir / "depth.npz", prediction, surface, image.rgb)
        validate_ply(output_dir / "pointcloud.ply", len(surface.vertices))
        validate_obj(output_dir / "surface.obj", mesh_count, face_count)
        advance("package")
        metrics = {
            **timings, **prediction.metrics, **peak_process_memory(),
            "elapsedSecondsBeforePackaging": time.perf_counter() - started,
            "pointCount": len(surface.vertices), "meshVertexCount": mesh_count,
            "faceCount": face_count, "invalidDepthSamples": len(surface.valid_mask) - len(surface.vertices),
            "rejectedFaces": surface.rejected_faces,
            "physicalAccuracy": None, "machineColdStartSeconds": None, "peakTotalDeviceVramBytes": None,
        }
        report = {
            "schemaVersion": "1.0.0", "jobId": job_id, "exportValidation": "passed",
            "geometryGate": "pending_independent_inspection", "sourceImageSha256": image.source["sha256"],
            "environment": environment(), "metrics": metrics, "numericalArrays": arrays,
            "independentViewer": None, "visibleFailures": None, "referenceMeasurements": None,
            "artifactByteSizes": {name: (output_dir / name).stat().st_size for name in REQUIRED_FILES
                                  if name not in ("benchmark.json", "benchmark.md")},
        }
        write_json(output_dir / "benchmark.json", report)
        with (output_dir / "benchmark.md").open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(markdown_report(report))
        warnings = image.warnings + [
            "Estimated visible surface from one photograph; unseen surfaces were not reconstructed.",
            "Physical scale is unknown. Coordinates are arbitrary units; metersPerUnit is null.",
            "Camera intrinsics are estimated; lens distortion is unmodeled.",
            "Model confidence, when present, is not a probability of geometric accuracy.",
            "OBJ is an open, geometry-only mesh; appearance is retained in the colored PLY.",
        ]
        records = [{"name": name, "format": file_format, "byteSize": (output_dir / name).stat().st_size,
                    "sha256": sha256_file(output_dir / name)} for name, file_format in REQUIRED_FILES.items()]
        mesh_bounds = bounds([surface.vertices[i] for i in {index for face in surface.faces for index in face}])
        manifest = {
            "schemaVersion": "1.0.0", "artifactId": artifact_id, "jobId": job_id,
            "createdAt": datetime.now(timezone.utc).isoformat(), "sourceImages": [image.source],
            "engine": prediction.engine,
            "settings": {"maxEdge": settings.max_edge, "maxRelativeEdge": settings.max_relative_edge,
                         "device": settings.device, "useRayPose": False},
            "provenance": "single_image_estimated", "scaleStatus": "unknown", "metersPerUnit": None,
            "depthConvention": prediction.depth_convention, "cameraFile": "camera.json",
            "geometry": {
                "pointCloud": {"file": "pointcloud.ply", "kind": "point_cloud", "pointCount": len(surface.vertices), "bounds": bounds(surface.vertices)},
                "partialMesh": {"file": "surface.obj", "kind": "triangle_mesh", "vertexCount": mesh_count, "faceCount": face_count, "bounds": mesh_bounds},
                "coordinateFrame": settings.frame,
                "physicalUnits": "arbitrary", "openSurface": True,
            },
            "transformHistory": [{"name": "T_export_from_reconstruction", "matrix": camera["T_export_from_reconstruction"], "normalized": False}],
            "operations": {
                "invalidDepthRejected": True, "confidenceFilter": None, "userMask": None,
                "mesh": {"algorithm": "depth_grid", "maxRelativeDepthEdge": settings.max_relative_edge, "holeFilling": False},
                "samplingStride": 1, "decimation": None,
            },
            "quality": {"validDepthFraction": len(surface.vertices) / len(surface.valid_mask),
                        "rejectedFaceFraction": surface.rejected_faces / surface.candidate_faces,
                        "modelConfidenceAvailable": prediction.confidence is not None,
                        "accuracy": None, "independentValidation": "not_performed"},
            "files": records, "warnings": warnings,
        }
        publish(output_dir, manifest)
        return manifest
    except BaseException as exc:
        # The rename is the commit point. An interrupt immediately afterwards
        # cannot retract a validated bundle or create a contradictory failure.
        if manifest is not None and (output_dir / "manifest.json").is_file():
            return manifest
        error = exc.as_dict() if isinstance(exc, GeometryError) else {
            "code": "cancelled" if isinstance(exc, KeyboardInterrupt) else "worker_failed",
            "message": f"{type(exc).__name__}: {exc}",
        }
        # Best effort only: keep the original cause if the filesystem is full.
        try:
            write_json(output_dir / "failure.json", {
                "schemaVersion": "1.0.0", "jobId": job_id,
                "status": "cancelled" if isinstance(exc, KeyboardInterrupt) else "failed",
                "stage": stage, "error": error, "elapsedSeconds": time.perf_counter() - started,
                "geometryGate": "not_passed",
            })
        except OSError:
            pass
        raise
