# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

## [1.0.0] - 2026-10-29

First release. A lightweight, local alternative to the AWS PheBee extraction pipeline: it runs
extraction containers over clinical notes on a single machine and writes PheBee-ready output.

### Features

- **Four commands** (`python extract.py ...` from a checkout, or `phebee-extract ...` once the
  package is installed):
  - `run` (the default): load notes, batch them, run the extraction containers, and aggregate
    the results.
  - `split`: write batch files plus a `manifest.json`, for an external scheduler to run.
  - `transform`: convert tool output files to PheBee or flat JSONL, with `--original-input` to
    merge back fields a tool dropped, `--image-ref` for provenance, and `--start-row-num` for
    per-batch part files.
  - `review`: show PheBee-format results next to the original notes (each note, then every
    matched span with its text, term and context flags).
- **Input formats**: JSON, JSONL, CSV and Parquet, with `--id-column`, `--text-column` and
  `--subject-id-column` for tabular data. Numeric IDs are converted to strings.
- **Tool chaining**: run containers in sequence per batch (e.g. extraction, then context
  classification), batching, GPU support, per-stage timeouts, and `--continue-on-error`.
- **PheBee format output by default**, ready for bulk import, streamed one record at a time.
  `--flat-format` gives note-centric output with a `_metadata` line instead.
- **Provenance**: `evidence_creator_id` and `evidence_creator_name` are derived from the Docker
  image names and tags (chained tools joined with `+`), and `evidence_creator_digest` from their
  SHA256 digests. Original input fields, such as the subject identifier, are merged back into
  tool output, because containers often drop them.
- **Reusable as a library**: the package is split into one module per pipeline stage, and a
  `StageRunner` protocol (`prepare` / `run_stage`) describes how a stage is executed, with
  `DockerRunner` as the local implementation. Other orchestrators can supply their own runner,
  or drive the stages through `split` and `transform`. `phebee_extraction.__all__` is the public
  API. See `ARCHITECTURE.md`.
- **Container interface**: containers receive the neutral `INPUT_URI` / `OUTPUT_URI` environment
  variables alongside the legacy `INPUT_S3_URI` / `OUTPUT_S3_URI` (same values), so older images
  keep working. The legacy names are deprecated in `INTERFACE.md`.
- **Try It walkthrough** in the README using keyword-based mock tools, so the whole flow can be
  run with only Docker. The mock context classifier applies cues within a sentence, and the
  expected output for every sample note is tested.

### PheBee compatibility

These were checked against PheBee's bulk-import docs and code, and a 135-record output was
imported into a PheBee instance.

- The aggregated output is named `aggregated.json`, although its content is JSON Lines. PheBee's
  validator accepts `.jsonl`, but its import jobs only read files ending in `.json` under a
  `jsonl/` key prefix, so a `.jsonl` upload is silently skipped and the run reports success.
- `contexts` holds the three flags PheBee reads, `negated`, `family` and `hypothetical`, as
  booleans. A missing flag is `false`; a value that is not a 0/1 flag (e.g. a score of 0.7)
  skips that annotation with a warning.

### Known limitations

- PheBee's bulk import accepts `evidence_creator_digest` and drops it: the evidence table stores
  only `creator_id`, `creator_type` and `creator_name`. Use immutable image tags for imported
  runs. The field is named so a future `creator_digest` column can read it unchanged.
- The whole input is loaded into memory when splitting, batches run sequentially, and there is
  no resume. Larger or distributed runs are what an orchestrator built on the `StageRunner`
  interface is for.

### Requirements

- Python 3.9 or later (the CI matrix covers 3.9 to 3.13), and a local Docker daemon for `run`.
