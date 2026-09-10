# Test fixtures

`manifest.synthetic.v1.json` is a cross-language schema fixture. Its zero hashes,
versions and IDs are placeholders, and it represents **no actual reconstruction**.
The tests create deterministic plane/cube/depth-step arrays and small color images
inside temporary directories. Those inputs check numerical and pipeline behavior
only; they cannot establish whether DA3 produces useful geometry.

No real user photographs or model checkpoints are committed. Record permitted
benchmark captures under the ignored `local-data/` directory using the capture
register in `docs/benchmarks/capture-register.csv`.
