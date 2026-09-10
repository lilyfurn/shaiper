"""Validate Python output against the same JSON Schema future TypeScript consumes."""

import json
from pathlib import Path

from .errors import GeometryError
from .io_utils import sha256_file

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "packages" / "contracts" / "artifact-manifest.v1.schema.json"
REQUIRED_FILES = {"working.png": "png", "camera.json": "json", "depth.npz": "npz",
                  "pointcloud.ply": "ply", "surface.obj": "obj", "benchmark.json": "json", "benchmark.md": "markdown"}


def schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def validate_contract(value: dict, definition: str | None = None) -> None:
    try:
        from jsonschema import Draft7Validator, FormatChecker
    except ImportError as exc:
        raise GeometryError("missing_dependency", "jsonschema is required to validate artifacts before publication.") from exc
    contract = schema()
    if definition:
        contract = {"$schema": contract["$schema"], "$ref": f"#/definitions/{definition}", "definitions": contract["definitions"]}
    try:
        # Materialize tuples as JSON arrays, and catch NaN/Inf before validation.
        serializable = json.loads(json.dumps(value, allow_nan=False))
    except (ValueError, TypeError) as exc:
        raise GeometryError("invalid_contract", "Artifact metadata must be finite JSON data.") from exc
    validator = Draft7Validator(contract, format_checker=FormatChecker())
    error = next(validator.iter_errors(serializable), None)
    if error is not None:
        location = ".".join(str(part) for part in error.absolute_path) or "root"
        raise GeometryError("invalid_contract", f"Artifact contract failed at {location}: {error.message}")


def verify_files(directory: Path, records: list) -> None:
    if len(records) != len(REQUIRED_FILES) or {r["name"] for r in records} != set(REQUIRED_FILES):
        raise GeometryError("invalid_manifest_files", "Manifest needs each promised export exactly once.")
    for record in records:
        path = directory / record["name"]
        if (path.is_symlink() or not path.is_file() or record["format"] != REQUIRED_FILES[record["name"]]
                or path.stat().st_size != record["byteSize"] or sha256_file(path) != record["sha256"]):
            raise GeometryError("artifact_integrity", f"Export integrity failed for {record['name']}.")
