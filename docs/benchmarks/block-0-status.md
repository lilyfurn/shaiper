# Block 0 benchmark status

Recorded: September 7, 2026. Target assumption: opaque tabletop objects, initially
relative visible shape. **The real-image geometry gate has not passed.**

The repository initially contained only the implementation plan. No permitted
photographs, model weights, independent measurements or prior benchmark results
were supplied. This coding task did not install packages, download models,
provision compute, run builds or deploy anything.

## Current evidence

The local preflight command was `python3 -B -m workers.geometry doctor`. It exited
with code 2 and identified these blockers:

| Requirement | WSL observation |
| --- | --- |
| Python | 3.10.12, Linux x86_64 |
| NumPy | Not installed |
| PyTorch / torchvision / xformers | Not installed |
| Depth Anything 3 | Not installed |
| Pillow | 12.0.0 already available |
| JSON Schema validator | 3.2.0 already available; Draft 7 supported |
| Local model checkpoint | Not supplied |
| Permitted real photographs | Not supplied |
| Windows GPU/CUDA compatibility | Not evaluated |

The selected implementation is DA3 Small with an exact model revision, published
weight checksum and reviewed source commit in `workers/geometry/da3-small.lock.json`.
Its suitability for these object classes remains unmeasured.

## Code verification

The test suite covers pinhole projection and cube/plane fixtures, optical-axis
versus ray-distance depth, all EXIF orientations, RGB alignment, resize transforms,
pose inversion, handedness/winding, invalid samples, discontinuities, open meshes,
colored PLY/OBJ read-back, schema invariants, corruption, existing-file protection,
failed publication and cancellation. Synthetic tests check the implementation only.

Local verification: **61 tests discovered, 58 passed, 3 skipped** with
`python3 -B -m unittest discover -s tests -v`. Python syntax and JSON parsing
checks also passed. No build command was run.

Three dependency-sensitive checks require the Windows environment: numerical NPZ
round-trip, the complete synthetic pipeline (including a publication/cancellation
race), and the independent trimesh OBJ import.

## Measurements not yet collected

| Benchmark item | Result |
| --- | --- |
| Real photographs processed | 0 |
| Usable real PLY / partial OBJ inspected | 0 |
| Real inference runtime | Not measured |
| Real model-load time | Not measured |
| Peak inference RAM / VRAM | Not measured |
| Real export file sizes | Not measured |
| Independent physical dimension errors | Not measured; current mode has unknown scale |
| Visible geometry failures | Not evaluated |
| Independent viewer round-trip | Pending |

## Complete the gate in Windows

Prepare the local environment and exact checkpoint using
`docs/windows-geometry.md`. Add permitted captures under ignored `local-data/`
and fill `capture-register.csv`. Its ten entries are **planned captures**, not
existing assets. Include a smaller multi-view collection for later comparison;
this worker accepts exactly one photograph per run.

Run each object into a fresh directory. Retain the generated timing/memory report
and all checksummed artifacts. Record viewer/version, input permission, dimensions,
measurement method, visible issues and an accept/reject decision separately.
At least one usable real colored PLY and partial OBJ must be reproduced and
independently inspected before advancing the application UI under the plan's gate.

The run report measures process stages, model-load time, PyTorch allocator memory
and process peak RAM through export validation. It leaves machine cold-start time,
total device VRAM, accuracy and independent inspection unset when unmeasured.
Successful file validation alone never changes `geometryGate` to passed.
