"""Synchronous numerical adapter contract; job transport is a later work block."""

from dataclasses import dataclass
from typing import Any, Protocol

from ..camera import Camera
from ..images import PreparedImage


@dataclass
class DepthPrediction:
    # Arrays remain model-native numerical data; the pipeline never uses a preview PNG as depth.
    native_depth: Any
    confidence: Any
    camera: Camera
    T_model_camera_from_world: list
    depth_convention: str
    engine: dict
    metrics: dict


class DepthAdapter(Protocol):
    def predict(self, image: PreparedImage) -> DepthPrediction:
        ...
