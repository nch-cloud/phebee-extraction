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
- `tests/context_cases.py` and `tests/test_mock_context.py`: expected context flags for every
  note in the sample data and inline test notes, checked against the mock context classifier
  (no Docker), with a guard that fails when sample data is added without an expectation.
- `extract.py review` shows PheBee-format results next to the original notes (each note, then
  every matched span with its text, term and context flags). It reads any input format and
  column options `run` does, so it works on real CSV/Parquet data, and replaces the `jq`
  snippet in the Try It walkthrough. `tests/test_review.py` checks it, including that the
  README's displayed output is exactly what the command prints.
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

- The mock extractor missed plainly stated phenotypes in the demo notes (chest pain, hearing
  loss, recurrent ear infections, walking unsteady), so the Try It output had visible gaps and
  no correctly negated finding. It now recognises them, with IDs and labels checked against HPO,
  and reports terms in text order. The label for `HP:0001263` is corrected to "Global
  developmental delay". A test fails if any demo note yields no match.

- The mock context classifier used a 20-character window that ignored sentence boundaries,
  so it got several sample notes wrong (e.g. in "Patient presents with fever and cough.
  Denies chest pain." both terms were marked negated; in "Chronic headaches, family history
  of migraines." headaches was marked family) and never matched the multi-word cue "rule
  out". A cue now applies to terms after it in the same sentence, stopping at "but" and
  similar words. The Docker chain test now asserts the exact flags instead of only checking
  that the keys exist.
- The integration fixtures reused any existing `phebee-mock-*:test` image, so edits to the
  mock scripts were tested against a stale image. They now rebuild (Docker's layer cache
  keeps this fast).
- Removed a stale `--evidence-creator-id` flag from the `--help` examples.
- A relative `--output` directory (e.g. `--output results/`) made every container fail:
  the batch directory was passed to Docker as a relative path, which Docker interprets as a
  named volume. The mount path is now made absolute. Absolute paths were unaffected.
- `evidence_creator_id`/`_name` lost the image tag (`clinphen:latest` became `clinphen`)
  because the reference was taken from Docker's `RepoDigests`, which has no tag. The image
  name is now kept exactly as given and Docker is used only for the digest. Tags often encode
  tool and ontology versions, so this matters for provenance.
- README quick start used `sample_notes.jsonl`, which has no `subject_id`, so the example
  produced an empty PheBee-format output. It now uses `sample_notes_with_subjects.jsonl`.
- README: added a "Try It" walkthrough using the mock tools, and a note that PheBee format
  needs a subject identifier on each note.

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
