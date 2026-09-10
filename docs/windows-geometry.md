# Windows geometry workflow

This repository is edited through WSL; run the model from your Windows Python
environment. The worker uses `pathlib`, binary file I/O and Python entry points,
so it does not depend on Bash, WSL path translation, npm or Firebase.

## Environment inputs

Use Python 3.11 for the initial environment. The worker's direct runtime
dependencies are in `workers/geometry/requirements-runtime.txt`; the selected
DA3 source is pinned in `workers/geometry/requirements-da3.txt`. These are
installation inputs for your Windows environment, **not a tested, fully resolved
CUDA lockfile**. No dependency installation was run during this coding task.

DA3's upstream package also depends on PyTorch, torchvision, xformers, OpenCV,
Open3D and other packages. Choose a mutually compatible Windows/Python/GPU
combination when preparing that environment. The adapter checks the installed
distribution's VCS commit from `direct_url.json`, so install the pinned VCS
requirement rather than an editable checkout or an unversioned archive.
The upstream dependency list is in its
[pinned package definition](https://github.com/ByteDance-Seed/Depth-Anything-3/blob/3d835ec1a5802d64a8b8b15f817a1ab54809bfe4/pyproject.toml).

The source pin is `3d835ec1a5802d64a8b8b15f817a1ab54809bfe4`. Once the environment
works on your machine, record the exact resolved packages alongside the benchmark
before treating it as reproducible across machines. Each real run already records
key installed package versions and device information in `benchmark.json`.

## Local model snapshot

Prepare these files outside the worker, under `models/da3-small/` or another local
directory:

| File | Expected value |
| --- | --- |
| `config.json` | DA3 config with `model_name` equal to `da3-small` |
| `model.safetensors` | 137,248,940 bytes |
| Weight SHA-256 | `364492e38a3a06d221ac75da7f6621ada3f2361cd24fde11ba79091e9f40efcf` |
| Model revision | `e08cab65ca0ec38e7826075418411ab90cab4da3` |

The model pin and weight hash come from the
[publisher's snapshot commit](https://huggingface.co/depth-anything/DA3-SMALL/commit/e08cab65ca0ec38e7826075418411ab90cab4da3).
The selected Small checkpoint is listed as Apache-2.0 on its
[model card](https://huggingface.co/depth-anything/DA3-SMALL).
The snapshot must contain actual safetensors bytes, not a Git LFS pointer.
`workers/geometry/da3-small.lock.json` is the canonical pin; the config's actual
hash is also recorded in each result. Model files are ignored by Git.

The worker enables Hub offline mode before loading DA3 and passes an existing,
verified local directory with `local_files_only=True`. It never invokes a paid
provider or retrieves an alternative model after an error.

## Commands

After preparing your environment and permitted photograph, use PowerShell from
the repository root:

```powershell
python -m workers.geometry doctor --model-dir '.\models\da3-small' --device cuda
python -m workers.geometry run --image '.\local-data\textured-object.jpg' --model-dir '.\models\da3-small' --output '.\runs\textured-object-001' --device cuda --max-edge 504 --max-relative-edge 0.1 --frame viewer
```

Use `--frame camera` to export x right, y down, z forward directly. Use a new
output directory for each run. `--max-edge` must be a multiple of 14 between 140
and 1008; `--max-relative-edge` must be greater than zero and at most one.
These are fixed, bounded Block 0 settings, not tuned quality promises.

`--device cpu` is an explicit alternative if the installed DA3/PyTorch stack
supports it. CPU inference and CUDA execution were both untested during this
coding task. A backend/import/device failure is returned as an error, with no
silent device switch or fabricated result.

The input limit is one single-frame JPEG/PNG, at most 25 MiB and 40 megapixels,
at least 28 pixels per side. Extreme aspect ratios that cannot retain two patches
on each axis are refused. PNG alpha is explicitly composited on white; object
masking belongs to a later block.

Shaiper stage events go to stderr and its final JSON result goes to stdout.
The upstream model may print its own loader/progress messages. Exit codes are
0 for success, 2 for an actionable input/configuration/backend failure, 1 for an
unexpected worker failure, and 130 for a user interrupt. Check `manifest.json`
and the exit code rather than treating any model progress message as success.

## Inspect and accept a run

Open `pointcloud.ply` and `surface.obj` in an independent tool you already use,
such as CloudCompare or Blender. Record that tool's version and import settings.
Compare point/face counts and bounds with `manifest.json`; inspect front-side
winding, RGB alignment, stretched edges, the open boundary and missing geometry.
Keep the complete ZIP or JSON sidecars with the geometry.

Fill in a separate review using the capture register and generated benchmark
checklist. Generated reports are immutable evidence; record review notes beside
the run rather than editing hashed files in place. Unknown scale means physical
dimension errors cannot be assessed as calibrated measurements. A later explicit
scale/alignment experiment must distinguish dimensions used to set scale from
held-out dimensions.

Run `python -B -m unittest discover -s tests -v` in Windows as well. NumPy enables
the numerical NPZ and complete synthetic-pipeline tests; trimesh enables the
independent OBJ-import test. A synthetic pipeline pass only verifies the code
path. The plan's Block 0 gate still requires useful results from real photographs.
