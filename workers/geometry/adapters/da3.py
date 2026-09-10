"""A local-only, pinned DA3 Small adapter. No downloads, provider calls or fallback."""

import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import time

from . import DepthPrediction
from ..camera import Camera, invert_rigid
from ..errors import GeometryError
from ..images import PreparedImage
from ..io_utils import sha256_file

LOCK_PATH = Path(__file__).resolve().parents[1] / "da3-small.lock.json"


def model_lock() -> dict:
    return json.loads(LOCK_PATH.read_text(encoding="utf-8"))


def missing_dependencies() -> list:
    return [name for name in ("numpy", "PIL", "torch", "depth_anything_3", "jsonschema")
            if importlib.util.find_spec(name) is None]


def verify_checkpoint(model_dir: Path) -> dict:
    lock = model_lock()
    if not model_dir.is_dir():
        raise GeometryError("checkpoint_missing", "Provide a local DA3 Small snapshot with config.json and model.safetensors. Nothing is downloaded automatically.")
    config_path, weights_path = model_dir / "config.json", model_dir / lock["weightsFile"]
    if not config_path.is_file() or not weights_path.is_file():
        raise GeometryError("checkpoint_missing", "The local snapshot needs config.json and model.safetensors.")
    if config_path.stat().st_size > 64 * 1024:
        raise GeometryError("invalid_checkpoint", "Model config exceeds the 64 KiB limit.")
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise GeometryError("invalid_checkpoint", "Model config is not valid UTF-8 JSON.") from exc
    if not isinstance(config, dict) or config.get("model_name") != lock["modelName"]:
        raise GeometryError("wrong_checkpoint", "Only the pinned DA3 Small model is supported by this adapter.")
    if (weights_path.stat().st_size != lock["weightsByteSize"]
            or sha256_file(weights_path) != lock["weightsSha256"]):
        raise GeometryError("checkpoint_checksum", "Model weights do not match the pinned DA3 Small snapshot. Check for a Git LFS pointer or incomplete download.")
    return {**lock, "configSha256": sha256_file(config_path)}


def verify_engine_installation() -> dict:
    """Require the reviewed VCS commit rather than trusting upstream's 0.0.0 version."""
    try:
        distribution = importlib.metadata.distribution("depth-anything-3")
        direct = json.loads(distribution.read_text("direct_url.json") or "{}")
    except (importlib.metadata.PackageNotFoundError, ValueError) as exc:
        raise GeometryError("engine_version_unverified", "Install the pinned VCS requirement in requirements-da3.txt in your Windows environment.") from exc
    commit = direct.get("vcs_info", {}).get("commit_id")
    if commit != model_lock()["sourceCommit"]:
        raise GeometryError("engine_version_unverified", "DA3 must be installed from the exact commit in requirements-da3.txt; editable or unversioned installs are not accepted.")
    return {"packageVersion": distribution.version, "sourceCommit": commit}


class DA3Adapter:
    def __init__(self, model_dir: Path, device: str = "cuda"):
        self.model_dir = model_dir.resolve()
        if device not in ("cpu", "cuda"):
            raise GeometryError("invalid_device", "Choose cpu or cuda explicitly.")
        self.device = device

    def predict(self, image: PreparedImage) -> DepthPrediction:
        missing = missing_dependencies()
        if missing:
            raise GeometryError("missing_dependency", "Missing runtime dependencies: " + ", ".join(missing) + ". Prepare them in Windows; no installation was attempted.")
        checkpoint = verify_checkpoint(self.model_dir)
        installation = verify_engine_installation()
        # Set before importing Hub/PyTorch/DA3, and use a checked existing directory.
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        try:
            import numpy as np
            import torch
            from depth_anything_3.api import DepthAnything3
        except (ImportError, OSError) as exc:
            raise GeometryError("backend_import_failed", f"DA3 dependencies could not load ({type(exc).__name__}: {exc}). Check the Windows PyTorch/torchvision/xformers combination.") from exc

        if self.device == "cuda" and not torch.cuda.is_available():
            raise GeometryError("cuda_unavailable", "CUDA is unavailable in this Python environment. Configure Windows CUDA PyTorch or deliberately select --device cpu.")
        device = torch.device(self.device)
        if self.device == "cuda":
            torch.cuda.reset_peak_memory_stats(device)
        start = time.perf_counter()
        try:
            model = DepthAnything3.from_pretrained(str(self.model_dir), local_files_only=True)
            model = model.to(device=device).eval()
            if self.device == "cuda":
                torch.cuda.synchronize(device)
            load_seconds = time.perf_counter() - start
            infer_start = time.perf_counter()
            with torch.inference_mode():
                prediction = model.inference(
                    [str(image.path)], process_res=max(image.width, image.height),
                    process_res_method="upper_bound_resize", export_dir=None,
                    infer_gs=False, use_ray_pose=False,
                )
            if self.device == "cuda":
                torch.cuda.synchronize(device)
            inference_seconds = time.perf_counter() - infer_start
        except torch.cuda.OutOfMemoryError as exc:
            raise GeometryError("out_of_memory", "DA3 exhausted GPU memory. Free memory or explicitly rerun with a smaller --max-edge; no automatic retry was made.") from exc
        except Exception as exc:
            raise GeometryError("inference_failed", f"DA3 failed ({type(exc).__name__}: {exc}). No substitute geometry was produced.") from exc

        depth = np.asarray(prediction.depth)
        intrinsics = np.asarray(prediction.intrinsics)
        processed = np.asarray(prediction.processed_images)
        if (depth.shape != (1, image.height, image.width) or depth.dtype.kind != "f"
                or intrinsics.shape != (1, 3, 3)
                or processed.shape != (1, image.height, image.width, 3)
                or processed.dtype != np.uint8):
            raise GeometryError("unexpected_prediction", "DA3 output shapes/dtypes differ from the pinned adapter contract.")
        original_rgb = np.frombuffer(image.rgb, dtype=np.uint8).reshape(image.height, image.width, 3)
        # Normalization/de-normalization can truncate one uint8 level. Geometric
        # resizing, crops, flips or reordering are refused rather than guessed.
        if np.max(np.abs(processed[0].astype(np.int16) - original_rgb.astype(np.int16))) > 1:
            raise GeometryError("unaligned_prediction", "DA3 changed the working pixels unexpectedly; refusing to sample misaligned colors.")
        confidence = None if prediction.conf is None else np.asarray(prediction.conf)
        if confidence is not None:
            if confidence.shape != depth.shape or confidence.dtype.kind != "f":
                raise GeometryError("unexpected_prediction", "Confidence must align with the numerical depth grid.")
            confidence = confidence[0].copy()
        extrinsics = np.asarray(prediction.extrinsics)
        if extrinsics.shape not in ((1, 3, 4), (1, 4, 4)):
            raise GeometryError("unexpected_prediction", "DA3 did not return a world-to-camera pose.")
        pose = extrinsics[0].tolist()
        if len(pose) == 3:
            pose.append([0, 0, 0, 1])
        invert_rigid(pose)
        metrics = {
            "modelLoadSeconds": load_seconds, "inferenceSeconds": inference_seconds,
            "device": self.device, "deviceName": torch.cuda.get_device_name(device) if self.device == "cuda" else "cpu",
            "peakCudaAllocatedBytes": int(torch.cuda.max_memory_allocated(device)) if self.device == "cuda" else None,
            "peakCudaReservedBytes": int(torch.cuda.max_memory_reserved(device)) if self.device == "cuda" else None,
        }
        engine = {
            "name": "depth-anything-3", **installation,
            "modelId": checkpoint["modelId"], "modelRevision": checkpoint["modelRevision"],
            "weightsSha256": checkpoint["weightsSha256"], "configSha256": checkpoint["configSha256"],
            "license": checkpoint["license"], "containerDigest": None,
            "torchVersion": str(torch.__version__), "numpyVersion": str(np.__version__),
        }
        return DepthPrediction(depth[0].copy(), confidence,
                               Camera.from_matrix(intrinsics[0].tolist(), image.width, image.height),
                               pose, checkpoint["depthConvention"], engine, metrics)
