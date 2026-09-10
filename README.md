# Shaiper

The offline **Block 0** implementation from
[the project plan](image-to-3d-implementation-plan.md): one JPEG/PNG photograph →
real estimated depth → colored PLY and an open partial OBJ, with numerical data
and provenance. The React/Firebase release follows the plan's real-geometry gate.

**Implementation status:** code and local synthetic tests are provided. Real
inference, independent viewer inspection and the object benchmark remain pending.
No packages, weights, cloud resources, builds or deployments were created during
implementation.

## Run in Windows

Use an existing Python 3.11 environment with the dependencies and local checkpoint
described in [Windows setup](docs/windows-geometry.md). Run from this repository's
root in PowerShell:

```powershell
Set-Location 'C:\Users\382xlinxus\Desktop\next\shaiper'
python -m workers.geometry doctor --model-dir '.\models\da3-small' --device cuda
python -m workers.geometry run --image '.\local-data\object.jpg' --model-dir '.\models\da3-small' --output '.\runs\object-001' --device cuda
```

`doctor` does not install packages, download weights or run inference. `run`
loads only a verified local DA3 Small checkpoint. Existing output directories are
refused. CUDA is the explicit default; `--device cpu` selects CPU deliberately,
subject to support in the installed backend. There is no automatic fallback.

## Outputs

| File | Contents |
| --- | --- |
| `pointcloud.ply` | All valid depth samples as colored points; binary little-endian PLY |
| `surface.obj` | Camera-facing triangles connecting supported neighboring samples; vertex normals; geometry only |
| `depth.npz` | Native depth/confidence, derived optical-axis depth, validity mask, RGB, camera matrices and source-pixel indices |
| `camera.json` | Intrinsics, explicit poses, pixel transforms and export-frame transform |
| `working.png` | Oriented/resized RGB image with embedded metadata removed |
| `manifest.json` | Versioned provenance, counts, scale status, operations and file checksums; success marker |
| `benchmark.json`, `benchmark.md` | Observed timings/memory and an unfilled independent-inspection checklist |
| `export.zip`, `export.zip.sha256` | Shareable result bundle including its manifest, with a separate bundle checksum |

All results say `single_image_estimated`, `scaleStatus: unknown` and
`metersPerUnit: null`. Geometry remains in arbitrary units. By default, a
documented rotation maps the camera frame to a right-handed Y-up export frame.
No object-size normalization, backside filling, physical-accuracy score or volume
claim is applied. Use the JSON sidecars when transferring PLY/OBJ between tools.

Failed runs return a nonzero exit code and retain `failure.json` in their reserved
directory when possible. They have no successful `manifest.json`; partial files
and a pending manifest are diagnostic data. Choose a fresh directory to retry.

## Code and verification

| Path | Responsibility |
| --- | --- |
| `workers/geometry/` | CLI, image processing, DA3 adapter, camera math, mesh, exports, reports |
| `packages/contracts/` | Shared JSON Schema for manifests and camera metadata |
| `tests/` | Exact synthetic geometry, image transforms, export integrity and failure tests |
| `docs/benchmarks/` | Honest initial benchmark status and capture register |

```powershell
python -B -m unittest discover -s tests -v
```

Tests use temporary synthetic inputs. NumPy and independent-importer tests are
reported as skipped when their dependencies are absent; skipped tests and
synthetic results never pass the real reconstruction gate. See
[the benchmark status](docs/benchmarks/block-0-status.md) and
[the geometry contract](docs/geometry-contract.md) for the remaining checks.
