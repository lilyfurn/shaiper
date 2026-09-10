# Shared artifact contracts

`artifact-manifest.v1.schema.json` is the source of truth for the local Block 0
bundle. It uses JSON Schema Draft 7, has no remote references, and includes a
`definitions.cameraMetadata` schema used for the camera sidecar. The `.invalid`
URL is a logical schema ID, not a service dependency.

Python validates this schema before publishing results. The future web/control
layer should compile this same file with its JSON Schema validator and derive
TypeScript types from it. No second handwritten TypeScript contract exists yet.

`tests/fixtures/manifest.synthetic.v1.json` is a shared valid-shape fixture with
placeholder identities/checksums and explicit synthetic labels. It proves
serialization compatibility, not artifact integrity or reconstruction quality.
