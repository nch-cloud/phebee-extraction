# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Added

- `phebee-extract` console script, installed with the package. `python extract.py` still
  works from a checkout; the wheel no longer ships a top-level `extract` module.
- `StageRunner` protocol (`phebee_extraction.runner`) describing how one pipeline stage is
  executed, with `DockerRunner` as the local implementation. Other orchestrators can supply
  their own runner and reuse the rest of the pipeline.
- `extract.py split` writes batch files plus a `manifest.json` for an external scheduler.
- `extract.py transform` converts tool output files into PheBee or flat JSONL, with
  `--original-input` to merge back dropped fields, `--image-ref` for provenance, and
  `--start-row-num` for per-batch part files.
- `tests/test_stages.py`: pipeline tests using a fake runner (no Docker), plus `split` and
  `transform` command tests.
- `ARCHITECTURE.md`: the split / run / transform model, artifacts, the `StageRunner`
  contract, provenance, and where other orchestrators plug in.

### Changed

- Split `extract.py` into the `phebee_extraction` package, one module per pipeline stage
  (`loading`, `batching`, `interface`, `docker_runner`, `transform`, `pipeline`, `cli`).
  `extract.py` remains as the command-line entry point and re-exports the public API, so
  existing commands and `import extract` keep working.
- PheBee-format transformation now takes a `PheBeeConfig` instead of the full execution
  context, so it can be reused by other orchestrators.
- Stage containers now receive the neutral `INPUT_URI`/`OUTPUT_URI` environment variables
  alongside the legacy `INPUT_S3_URI`/`OUTPUT_S3_URI` names (same values). The legacy names
  are deprecated in INTERFACE.md; containers should prefer the neutral names.

### Fixed

- Removed a stale `--evidence-creator-id` flag from the `--help` examples.

## [1.0.0] - 2026-05-27

### Initial Release

- Local extraction orchestrator for clinical notes
- Support for JSON, JSONL, Parquet, CSV input formats
- Tool chaining support (e.g., extraction → context classification)
- Batch processing with configurable batch sizes
- GPU support via Docker
- **PheBee format output by default** - ready for bulk import (streaming transformation)
- **Flat format option** - note-centric output with --flat-format flag
- Comprehensive test suite (50 tests: 38 extract unit tests, 12 integration tests)
- Mock extraction tools for testing
- INTERFACE.md specification for Docker containers
- Modern Python tooling: uv package manager, ruff linter/formatter
- pyproject.toml for standardized project configuration
