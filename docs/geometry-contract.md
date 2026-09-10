# Geometry and artifact contract, version 1

The authoritative schema is
`packages/contracts/artifact-manifest.v1.schema.json` (JSON Schema Draft 7).
Python validates it before publication. Future TypeScript should consume the
same schema, with generated types if needed. Its reserved `.invalid` schema ID
is an identifier; validation uses local definitions and does not fetch it.

Version 1 intentionally describes the implemented Block 0 mode only. Other
provenance types, measured units, transformed revisions and GLB outputs require
an explicit contract extension when their work blocks are implemented.

## Images and cameras

The original input is read once into a bounded byte buffer. Its exact bytes are
hashed and never overwritten. Local originals have `storageGeneration: null`;
the content SHA-256 identifies the source. Original filenames, GPS and general
EXIF are excluded from downloadable metadata.

EXIF orientation (all eight values, including mirrors) is applied first. The
worker records its integer-pixel-center affine transform and original/oriented
dimensions. Images are converted to RGB, using an embedded ICC profile when
present, otherwise explicitly assuming sRGB. Alpha, when present, is composited
on white and recorded. The working PNG has no embedded source metadata.

The working size is rounded to the model's patch grid, with a recorded Lanczos
resize and independent x/y scale factors. The pixel transform is
`u' = sx * (u + 0.5) - 0.5` and likewise for v. No crop, padding or undistortion
is applied in Block 0. Intrinsics are predicted for these exact working pixels,
not transformed from EXIF focal-length metadata.

DA3 receives an already patch-aligned image and an equal processing resolution.
The adapter verifies returned RGB/depth dimensions and that processed pixels
differ by at most one uint8 level (normalization rounding). Unexpected geometric
preprocessing fails instead of silently guessing a new camera transform.
The reviewed behavior is in the
[upstream input processor](https://github.com/ByteDance-Seed/Depth-Anything-3/blob/3d835ec1a5802d64a8b8b15f817a1ab54809bfe4/src/depth_anything_3/utils/io/input_processor.py).

`K_working` is a finite pinhole matrix in pixels. Focal lengths must be positive,
and the matrix must have the expected homogeneous row. These sanity checks
cannot prove that estimated intrinsics are physically correct. Lens distortion
is explicitly recorded as unmodeled, not falsely reported as calibrated away.

## Depth and coordinates

The selected DA3 adapter treats its returned depth as **relative optical-axis
depth**, consistent with the publisher's
[back-projection code](https://github.com/ByteDance-Seed/Depth-Anything-3/blob/3d835ec1a5802d64a8b8b15f817a1ab54809bfe4/src/depth_anything_3/utils/geometry.py).
Relative here describes scale. It does not mean inverse depth or disparity.
The core also handles normalized-ray distance explicitly; inverse-depth adapters
are refused until an appropriate conversion exists.

Camera and reconstruction coordinates are right-handed: x right, y down,
z forward. `p = z K^-1 [u, v, 1]` back-projects an optical-axis depth sample.
For ray distance, normalize `K^-1 [u, v, 1]` before multiplying by distance.
The single input camera is the reconstruction frame, with an identity
`T_reconstruction_from_camera`.

DA3's predicted world-to-camera matrix is retained as
`T_model_camera_from_world`, with its explicitly named inverse. It is not applied
to single-image geometry. This keeps an arbitrary model world alignment separate
from the chosen reconstruction frame.

The default export applies `diag(1, -1, -1, 1)` once. It is a proper rotation
with determinant +1, giving x right, y up and camera forward along negative z.
The same transform is stored for future camera/annotation rendering; the camera
in export coordinates is `T_export_from_reconstruction * T_reconstruction_from_camera`.
No translation, bounding-box normalization or physical-unit conversion occurs.

## Points, mesh and numerical arrays

Positive finite depth samples with finite supported coordinates become colored
points. Invalid native samples are retained in the numerical bundle but excluded
from geometry. Coordinates beyond the worker's `1e15` arbitrary-unit numerical
guard are invalid. The validity mask is authoritative for derived depth.

The mesh connects each depth-grid cell with two triangles. Missing samples,
degenerate faces and depth discontinuities are rejected. Every triangle checks
all three edges, including its diagonal, using
`max(z) - min(z) <= threshold * min(z)`. Faces point toward the camera. Surface
normals are accumulated from these faces. No holes or unseen backs are filled.

PLY retains every valid point and RGB. OBJ removes isolated vertices, so its
vertex count and bounds can differ; both are recorded per format in the manifest. OBJ
contains faces and normals without materials/textures. The export read-back
checks are supplemented by an optional independent trimesh importer test.

`depth.npz` contains only numeric/bool arrays and is reopened with
`allow_pickle=False` before publication:

| Array | Shape / meaning |
| --- | --- |
| `model_native_depth` | H × W, returned model dtype and values, including invalid samples |
| `optical_axis_depth` | H × W float64, derived z values; invalid entries zero |
| `valid_mask` | H × W bool; must be used with derived depth |
| `model_native_confidence` | Optional H × W model confidence; no calibration or threshold claim |
| `rgb` | H × W × 3 uint8, aligned working colors |
| `K_working` | 3 × 3 float64 |
| `T_model_camera_from_world` | 4 × 4 float64, recorded model pose |
| `T_reconstruction_from_camera` | 4 × 4 identity |
| `T_export_from_reconstruction` | 4 × 4 selected export transform |
| `point_pixel_indices` | int64 source indices for every PLY point; `v * width + u` |

## Publication and limits

Each CLI call reserves a new output directory exclusively. It validates geometry,
read-back formats, the shared metadata schema and each file's SHA-256. The ZIP is
assembled with relative member paths and CRC-checked. Only then is the pending
manifest atomically renamed to `manifest.json`. A failed attempt can leave
diagnostic files, but has no valid success marker. There are no automatic retries.

This is local job execution, not a cloud job dispatcher or authorization layer.
Firestore, leases, quotas, private uploads, calibration, masks, GLB and multi-view
processing remain their own planned blocks. The local synchronous adapter only
performs numerical prediction; it does not imitate a paid-job transport API.
