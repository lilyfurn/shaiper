"""Run with `python -m workers.geometry` from the repository root."""

import argparse
import json
from pathlib import Path
import sys

from .adapters.da3 import (DA3Adapter, missing_dependencies, model_lock,
                           verify_checkpoint, verify_engine_installation)
from .benchmark import environment
from .errors import GeometryError
from .pipeline import RunSettings, run


def emit(value: dict, *, stream=None):
    print(json.dumps(value, allow_nan=False), file=stream or sys.stdout, flush=True)


def doctor(model_dir: Path | None, device: str) -> int:
    blockers = []
    missing = missing_dependencies()
    if missing:
        blockers.append({"code": "missing_dependency", "message": "Missing: " + ", ".join(missing)})
    if model_dir is None:
        blockers.append({"code": "checkpoint_not_provided", "message": "Supply --model-dir pointing to the local pinned DA3 Small snapshot."})
    else:
        try:
            verify_checkpoint(model_dir)
        except (GeometryError, OSError) as exc:
            blockers.append(exc.as_dict() if isinstance(exc, GeometryError) else {"code": "checkpoint_io", "message": str(exc)})
    if "depth_anything_3" not in missing:
        try:
            verify_engine_installation()
        except GeometryError as exc:
            blockers.append(exc.as_dict())
    if "torch" not in missing:
        try:
            import torch
            if device == "cuda" and not torch.cuda.is_available():
                blockers.append({"code": "cuda_unavailable", "message": "CUDA is unavailable in this Python environment."})
        except (ImportError, OSError) as exc:
            blockers.append({"code": "torch_import_failed", "message": str(exc)})
    emit({"status": "blocked" if blockers else "ready_for_inference_attempt", "blockers": blockers,
          "environment": environment(), "selectedModel": model_lock(), "device": device,
          "inferenceRun": False, "geometryGate": "not_evaluated"})
    return 2 if blockers else 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Shaiper Block 0: real single-photo estimated geometry, local and offline.")
    commands = root.add_subparsers(dest="command", required=True)
    check = commands.add_parser("doctor", help="Check local dependencies and checkpoint; never install or download.")
    check.add_argument("--model-dir", type=Path)
    check.add_argument("--device", choices=("cuda", "cpu"), default="cuda")
    process = commands.add_parser("run", help="Export colored PLY, partial OBJ, numerical NPZ and provenance from one photo.")
    process.add_argument("--image", type=Path, required=True, help="Existing JPEG/PNG photograph.")
    process.add_argument("--output", type=Path, required=True, help="New run directory; existing paths are refused.")
    process.add_argument("--model-dir", type=Path, required=True, help="Local pinned DA3 Small snapshot, not a Hub ID.")
    process.add_argument("--device", choices=("cuda", "cpu"), default="cuda")
    process.add_argument("--max-edge", type=int, default=504, help="Working size limit: a multiple of 14, 140 to 1008 (default 504).")
    process.add_argument("--max-relative-edge", type=float, default=.1, help="Reject mesh depth jumps above this fraction (default 0.1).")
    process.add_argument("--frame", choices=("viewer", "camera"), default="viewer")
    return root


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "doctor":
            return doctor(args.model_dir, args.device)
        settings = RunSettings(
            max_edge=args.max_edge, max_relative_edge=args.max_relative_edge,
            frame="viewer_y_up" if args.frame == "viewer" else "camera_x_right_y_down_z_forward",
            device=args.device,
        )
        adapter = DA3Adapter(args.model_dir, args.device)
        manifest = run(args.image, args.output, adapter, settings,
                       on_stage=lambda stage, job: emit({"jobId": job, "stage": stage}, stream=sys.stderr))
        emit({"status": "succeeded", "jobId": manifest["jobId"], "artifactId": manifest["artifactId"],
              "manifest": str(args.output.resolve() / "manifest.json"),
              "bundle": str(args.output.resolve() / "export.zip"),
              "provenance": manifest["provenance"], "scaleStatus": "unknown",
              "geometryGate": "pending_independent_inspection"})
        return 0
    except GeometryError as exc:
        emit({"status": "failed", "error": exc.as_dict()}, stream=sys.stderr)
        return 2
    except KeyboardInterrupt:
        emit({"status": "cancelled", "error": {"code": "cancelled", "message": "Interrupted. Check the output manifest to see whether publication had already completed."}}, stream=sys.stderr)
        return 130
    except Exception as exc:
        emit({"status": "failed", "error": {"code": "worker_failed", "message": f"{type(exc).__name__}: {exc}"}}, stream=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
