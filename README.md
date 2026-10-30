# PheBee Extraction (Local)

[![License](https://img.shields.io/badge/License-BSD%203--Clause-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

Lightweight, local alternative to the enterprise AWS PheBee extraction pipeline. Process clinical notes on a single machine without AWS infrastructure.

**What is phenotype extraction?** Automated identification and extraction of clinical phenotypes (observable characteristics like symptoms, diseases, lab findings) from unstructured clinical notes, mapped to standardized ontology terms (e.g., Human Phenotype Ontology - HPO).

## Overview

PheBee Extraction orchestrates Docker containers to extract phenotype annotations from clinical notes. Supports:

- **Multiple input formats**: JSON, JSONL, Parquet, CSV
- **Tool chaining**: Run extraction → context classification in sequence
- **Batch processing**: Process large datasets in manageable chunks
- **GPU support**: Optional GPU acceleration for compatible models
- **Zero AWS dependencies**: Runs entirely on local machine

## Quick Start

### Prerequisites

- Python 3.11+
- Docker Engine 20.10+
- NVIDIA Docker runtime (for GPU support)

### Installation

```bash
# Clone repository
git clone https://github.com/nch-cloud/phebee-extraction.git
cd phebee-extraction

# Install with uv (recommended)
uv sync

# Or with pip
pip install -e .
```

If you don't have `uv` installed:
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Installing the package also provides a `phebee-extract` command. The examples below use `python extract.py` from a checkout; if you installed the package, `phebee-extract` takes the same arguments.

### Installing as a Dependency

Other projects (for example an orchestrator for a cloud or cluster, see [ARCHITECTURE.md](ARCHITECTURE.md)) can depend on this package directly from a release tag:

```bash
# pip
pip install "phebee-extraction @ git+https://github.com/nch-cloud/phebee-extraction.git@v1.0.0"

# uv
uv add "git+https://github.com/nch-cloud/phebee-extraction.git" --tag v1.0.0
```

Or in `pyproject.toml`:

```toml
dependencies = [
  "phebee-extraction @ git+https://github.com/nch-cloud/phebee-extraction.git@v1.0.0",
]
```

The public API is what `phebee_extraction.__all__` exports (`from phebee_extraction import PheBeeConfig, write_phebee_jsonl, ...`). Module-level names not listed there may change between minor versions.

### Try It (No Model Required)

The repo includes small keyword-based mock tools, so you can see the whole flow work before setting up a real extraction image. This needs only Docker and the sample files in [examples/](examples/):

```bash
# Build the mock extractor and mock context classifier
docker build -f tests/Dockerfile.mock -t phebee-mock-extractor:test tests/
docker build -f tests/Dockerfile.mock-context -t phebee-mock-context:test tests/

# Extract, in PheBee format (the sample input includes subject_id)
python extract.py \
  --input examples/sample_notes_with_subjects.jsonl \
  --output results/ \
  --docker-images phebee-mock-extractor:test \
  --project-id demo

# Or chain extraction -> context classification
python extract.py \
  --input examples/sample_notes_with_subjects.jsonl \
  --output results-chained/ \
  --docker-images phebee-mock-extractor:test,phebee-mock-context:test \
  --project-id demo
```

Each run writes `aggregated.json` (11 records from the 5 sample notes). The mock tools find phenotypes by matching a small built-in list of terms, so the results are only useful for checking the plumbing, not as real extractions.

**Reading the results against the original notes.** Each PheBee-format record points back to its note and the matched span, so `review` can join the results to the input file and show each note with what was matched in it:

```bash
python extract.py review \
  --results results-chained/aggregated.json \
  --input examples/sample_notes_with_subjects.jsonl
```

which prints each note followed by its matches (`[start:end] "matched text"  term  contexts`):

```
note_001 (patient-001): Patient presents with fever and cough. Denies chest pain.
    [22:27] "fever"  HP_0001945  negated=false family=false hypothetical=false
    [32:37] "cough"  HP_0012735  negated=false family=false hypothetical=false
    [46:56] "chest pain"  HP_0100749  negated=true family=false hypothetical=false

note_002 (patient-001): Patient admitted with pneumonia and requires supplemental oxygen.
    [22:31] "pneumonia"  HP_0002090  negated=false family=false hypothetical=false

note_003 (patient-002): Chronic headaches, possibly migraine. Family history of seizures.
    [8:17] "headaches"  HP_0002315  negated=false family=false hypothetical=false
    [28:36] "migraine"  HP_0002076  negated=false family=false hypothetical=true
    [56:64] "seizures"  HP_0001250  negated=false family=true hypothetical=false

note_004 (patient-003): Developmental delay noted at 18 months. Walking unsteady.
    [0:19] "Developmental delay"  HP_0001263  negated=false family=false hypothetical=false
    [40:56] "Walking unsteady"  HP_0002317  negated=false family=false hypothetical=false

note_005 (patient-003): Recurrent ear infections. No hearing loss detected.
    [0:24] "Recurrent ear infections"  HP_0410018  negated=false family=false hypothetical=false
    [29:41] "hearing loss"  HP_0000365  negated=true family=false hypothetical=false
```

Notes are listed in input order, and a note with no matches would still appear, with nothing under it. Note how the context flags follow the sentence: "possibly" marks only `migraine`, "Family history" marks only `seizures`, "Denies chest pain" negates `chest pain` but not `fever` or `cough` (a different sentence), and "No hearing loss" negates `hearing loss` but not the ear infections. The mock classifier is a small cue-word model that gets the sample notes right; it is not a real clinical context tool.

### Basic Usage

**PheBee format (default)** - Ready for PheBee bulk import. The input needs a subject identifier per note (`subject_id` by default, or name another column with `--subject-id-column`). Notes without one are skipped with a warning, and a run in which *no* note has one fails with an error naming the flag, rather than writing an empty file:

```bash
# Single extraction tool
python extract.py \
  --input examples/sample_notes_with_subjects.jsonl \
  --output results/ \
  --docker-images fasthpocr:batch-latest \
  --project-id my-project

# Tool chaining (extraction + context)
python extract.py \
  --input notes.jsonl \
  --output results/ \
  --docker-images fasthpocr:batch-latest,phenocontext:batch-latest \
  --project-id my-project

# GPU-enabled extraction
python extract.py \
  --input notes.parquet \
  --output results/ \
  --docker-images phenobert:batch-latest \
  --project-id my-project \
  --gpu \
  --batch-size 500
```

**Flat format** - Note-centric output for exploration, not importable to PheBee:

```bash
python extract.py \
  --input notes.jsonl \
  --output results/ \
  --docker-images fasthpocr:batch-latest \
  --flat-format
```

## CLI Reference

### Required Arguments

- `--input`: Input file path (JSON/JSONL/Parquet/CSV)
- `--output`: Output directory for results
- `--docker-images`: Comma-separated list of Docker images for pipeline
- `--project-id`: PheBee project identifier (required unless --flat-format specified)

### Output Format Options

- `--flat-format`: Output note-centric format instead of PheBee format
- `--subject-id-column`: Input column name for subject ID (default: subject_id)

**Note on Provenance**: `evidence_creator_id` and `evidence_creator_name` are derived from the Docker image names and tags in the pipeline, and `evidence_creator_digest` from their SHA256 digests; chained tools are joined with `+` in pipeline order (e.g. `fasthpocr:latest+phenocontext:latest` and `sha256:abc...+sha256:def...`). PheBee stores the id and name but currently drops the digest, so use immutable tags for imported runs. See [Creator fields](#creator-fields-and-evidence_creator_digest) for the field contract.

### Processing Options

- `--batch-size`: Notes per batch (default: 1000)
- `--gpu`: Enable GPU support
- `--temp-dir`: Custom temporary directory
- `--keep-temp`: Keep temporary files for debugging
- `--input-format`: Force format (auto|json|jsonl|parquet|csv)
- `--id-column`: Column name for note IDs in CSV/Parquet (default: note_id)
- `--text-column`: Column name for note text in CSV/Parquet (default: text)
- `--string-columns`: Comma-separated input columns to convert to strings, for numeric identifiers a tool expects as text. `note_id`, `encounter_id` and the subject column are always converted
- `--continue-on-error`: Continue if batch fails
- `--docker-timeout`: Maximum seconds per batch stage (default: 3600)

### Running the Stages Separately

The default command runs the whole pipeline. Two further commands expose the stages on their own, so a different scheduler (a cloud batch service, Slurm, Kubernetes, ...) can run the containers while reusing the batching and output logic:

```bash
# 1. Split notes into batch files (writes batch_NNNNNN_input.json + manifest.json)
python extract.py split --input notes.csv --output-dir batches/ \
  --id-column note_id --text-column note_text --batch-size 1000

# 2. Run each batch through the container(s) with your scheduler of choice.
#    Each container reads INPUT_URI and writes OUTPUT_URI (see INTERFACE.md).

# 3. Convert the tool outputs to PheBee JSONL
python extract.py transform \
  --input batches/batch_000001_output.json batches/batch_000002_output.json \
  --original-input batches/batch_000001_input.json batches/batch_000002_input.json \
  --output evidence.json --project-id my-project --subject-id-column mrn \
  --image-ref clinphen:latest@sha256:0da85be...
```

`transform` notes:
- `--original-input` merges fields the tool dropped (e.g. `subject_id`) back from the batch inputs; list one file per `--input`, in the same order.
- `--image-ref` supplies the provenance that `run` captures automatically. Pass the full `name:tag@sha256:...` reference for each tool in pipeline order.
- Each output file's `row_num` is its 1-based line number. PheBee does not read it, so part files written separately may each start at 1.
- `--flat-format` produces the note-centric format instead; `--project-id` is then not required.

### Reviewing Results

`review` shows PheBee-format results next to the original notes: each note, then every matched span with its text, term and context flags. Pass the results file and the same input file that was extracted, with the same column options:

```bash
python extract.py review --results results/aggregated.json --input notes.csv \
  --id-column note_id --text-column note_text
```

- `--note-id ID [ID ...]` shows only those notes; `--limit N` shows the first N.
- Notes are listed in input order, including notes with no matches.
- It warns if the results mention notes that are not in `--input` (usually the wrong input file).
- Flat-format output (`--flat-format`) already contains the text and annotations, and is rejected with an explanatory error.
- The results file is read into memory, which is fine for reviewing a sample and not meant for very large outputs.

## Input Formats

**For PheBee format output**, your input must include `subject_id`:

```jsonl
{"note_id": "note_001", "subject_id": "patient-001", "text": "Patient presents with fever..."}
{"note_id": "note_002", "subject_id": "patient-001", "text": "Chronic headaches..."}
```

**For flat format output**, only `note_id` and `text` are required:

### JSONL (Recommended)
```jsonl
{"note_id": "note_001", "text": "Patient presents with fever..."}
{"note_id": "note_002", "text": "Chronic headaches..."}
```

### JSON
```json
{
  "records": [
    {"note_id": "note_001", "text": "Patient presents with fever..."},
    {"note_id": "note_002", "text": "Chronic headaches..."}
  ]
}
```

### CSV
```csv
note_id,text
note_001,"Patient presents with fever..."
note_002,"Chronic headaches..."
```

### Parquet
Use `--id-column` and `--text-column` to specify column names.

## Output Format

### PheBee Format (Default)

Output is written as JSONL (`aggregated.json`) with one record per annotation, ready for PheBee bulk import:

```jsonl
{"project_id": "my-project", "project_subject_id": "patient-001", "term_iri": "http://purl.obolibrary.org/obo/HP_0001945", "evidence": [{...}], "row_num": 1}
{"project_id": "my-project", "project_subject_id": "patient-001", "term_iri": "http://purl.obolibrary.org/obo/HP_0012735", "evidence": [{...}], "row_num": 2}
```

Each line represents one (subject, term) pair with associated evidence. Multiple annotations of the same term for the same subject appear on separate lines. `row_num` is the line number within the file, for referring to a record; PheBee's import does not read it, and derives its own `batch_id` from the file name.

The `contexts` object holds three boolean flags, `negated`, `family` and `hypothetical`, written as `true`/`false`. A missing flag is written as `false`. PheBee's bulk import reads exactly these three flags (it ignores any other context key, so they are not written) and treats `true`, `1` and `1.0` the same, so the boolean form does not change how evidence is identified. A context value that is not a recognisable flag, such as a score of `0.7`, causes that annotation to be skipped with a warning.

#### Creator fields and `evidence_creator_digest`

Each evidence item carries four creator fields, all derived from the Docker images in the pipeline, in pipeline order:

| Field | Type | Value for images `a:1` then `b:2` |
|---|---|---|
| `evidence_creator_id` | string | `a:1+b:2` (image names exactly as given, including tags) |
| `evidence_creator_name` | string | same as `evidence_creator_id` |
| `evidence_creator_type` | string | always `automated` |
| `evidence_creator_digest` | string or null | `sha256:<hex>+sha256:<hex>`, one digest per image |

`evidence_creator_digest` is this tool's addition to the PheBee format. Its contract:

- One `sha256:<64 hex>` per image, in the same order as `evidence_creator_id`, joined with `+`, so the *n*-th segment of each field describes the same image.
- `null` if the digest of any image could not be determined; never a partial list.
- The digest is the registry manifest digest when the image was pulled (Docker's `RepoDigests`; for a cloud runner, the registry's `describe-images` equivalent). For an image built locally and never pushed, it falls back to the local image ID, which identifies the build on that machine but will not match a registry.

**PheBee does not store this field yet.** Bulk import accepts the record and drops the field: the evidence table's `creator` struct holds only `creator_id`, `creator_type` and `creator_name`. The name follows PheBee's `evidence_creator_*` → `creator_*` convention so that a future `creator_digest` column can read it without any change here. Until then, two practical consequences:

- Use **immutable tags** for anything you import. With `clinphen:latest`, PheBee records only `clinphen:latest`, and two imports from different builds of `latest` are indistinguishable in the table. A tag like `ods/phebee-clinphen:batch-v1.0.0_clinphen-1.28_hpo-v2025-10-22_c58cc41` identifies the build by itself.
- **Keep the uploaded file.** It is the only place the digest is recorded for that run.

### Flat Format (--flat-format)

Output is written as JSONL (`aggregated.json`) with one record per note:

```jsonl
{"note_id": "note_001", "text": "...", "annotations": [...], "error": null}
{"note_id": "note_002", "text": "...", "annotations": [...], "error": null}
{"_metadata": {"execution_name": "local-extraction-...", "total_records": 2, "tools_used": ["fasthpocr:batch-latest"], "processing_timestamp": "2026-05-26T10:00:00Z", "status": "success"}}
```

Final line contains metadata with `_metadata` key (PheBee format has no metadata line).

## Docker Container Interface

Extraction containers must follow the [INTERFACE.md](INTERFACE.md) specification:

- Accept `INPUT_URI` and `OUTPUT_URI` environment variables (local paths or URIs). The legacy `INPUT_S3_URI`/`OUTPUT_S3_URI` names are also set for compatibility with older images
- Read input JSON from mounted volume
- Write output JSON with annotations
- Exit with code 0 on success

See [INTERFACE.md](INTERFACE.md) for complete specification.

## Tool Chaining

Chain multiple tools by providing comma-separated Docker images:

```bash
python extract.py \
  --input notes.jsonl \
  --output results/ \
  --docker-images tool1:latest,tool2:latest,tool3:latest \
  --project-id my-project \
  --evidence-creator-id my-extractor
```

Each batch goes through all pipeline stages sequentially. Common pattern:
1. **Extraction**
2. **Context**

## PheBee Import Workflow

Extract directly to PheBee format (one step):

```bash
# Extract phenotypes ready for PheBee bulk import
python extract.py \
  --input notes.jsonl \
  --output results/ \
  --docker-images fasthpocr:batch-latest,phenocontext:batch-latest \
  --project-id my-phebee-project

# Upload for PheBee bulk import (example with S3), then trigger the import via API/console
aws s3 cp results/aggregated.json \
  "s3://${BUCKET}/bulk-imports/projects/my-phebee-project/runs/${RUN_ID}/jsonl/evidence.json"
```

**PheBee naming requirements** (see PheBee's `docs/bulk-import-guide.md`): the uploaded key must include a path component named `jsonl/`, and the file must end in `.json`. The validator accepts `.jsonl` too, but the import jobs read the prefix with a `*.json` glob, so a `.jsonl` upload is silently skipped and the run reports success having imported nothing. The output is written as `aggregated.json` for this reason, even though its content is JSON Lines; keep the `.json` extension when you upload or rename it.

**Required Input Fields:**

Your input notes must include `subject_id`:

```jsonl
{"note_id": "n1", "subject_id": "patient-001", "encounter_id": "enc-001", "text": "..."}
{"note_id": "n2", "subject_id": "patient-001", "encounter_id": "enc-002", "text": "..."}
```

## Examples

See [examples/](examples/) directory for:
- [sample_notes.jsonl](examples/sample_notes.jsonl) - Example input (no subject_id, use with --flat-format)
- [sample_notes_with_subjects.jsonl](examples/sample_notes_with_subjects.jsonl) - Example input with subject_id for PheBee format
- [sample_phebee_output.jsonl](examples/sample_phebee_output.jsonl) - Example PheBee format output
- [sample_flat_output.jsonl](examples/sample_flat_output.jsonl) - Example flat format output

## Development

### Running Tests

```bash
# Run all tests
uv run pytest tests/ -v

# Run specific test files
uv run pytest tests/test_extract.py -v           # Unit tests (42 tests)
uv run pytest tests/test_stages.py -v            # Runner / split / transform tests (24 tests)
uv run pytest tests/test_mock_context.py -v      # Mock context classifier on all sample data (33 tests)
uv run pytest tests/test_review.py -v            # review command (12 tests)
uv run pytest tests/test_context_flags.py -v     # Boolean context flags (30 tests)
uv run pytest tests/test_integration.py -v       # Integration tests with Docker (12 tests)

# Run with coverage
uv run pytest tests/ --cov=phebee_extraction --cov-report=term-missing

# Run ruff linter
uv run ruff check .

# Run ruff formatter
uv run ruff format .
```

### Project Structure

```
extract.py                # Command-line entry point (thin wrapper around the package)
phebee_extraction/        # Implementation, one module per pipeline stage
  loading.py              #   read notes from JSON/JSONL/CSV/Parquet
  batching.py             #   split notes into batch files, per-stage paths
  interface.py            #   the container contract (INTERFACE.md): files, env vars
  runner.py               #   StageRunner protocol: how one stage gets executed
  docker_runner.py        #   StageRunner implementation on local Docker
  transform.py            #   tool output -> PheBee or flat JSONL
  pipeline.py             #   local end-to-end orchestration
  review.py               #   join results to notes for the review command
  cli.py                  #   run / split / transform / review commands
  models.py               #   BatchInfo and exceptions
pyproject.toml            # Project metadata and dependencies
INTERFACE.md              # Docker container interface spec
examples/                 # Sample data
tests/                    # Unit and integration tests (153 tests)
  test_extract.py         # Unit tests (42 tests)
  test_stages.py          # Runner protocol, split, transform (24 tests, no Docker)
  test_mock_context.py    # Mock context classifier vs. expected flags (33 tests, no Docker)
  context_cases.py        # Expected context flags for every sample note
  test_review.py          # review command (12 tests, no Docker)
  test_context_flags.py   # boolean context flags in PheBee output (30 tests, no Docker)
  test_integration.py     # Integration tests with Docker (12 tests)
```

Only `docker_runner` depends on Docker. Everything else is reusable by other orchestrators, which supply their own `StageRunner` (see `runner.py`) or drive the stages from the command line with `split` and `transform`. [ARCHITECTURE.md](ARCHITECTURE.md) describes the pipeline model and the integration points in detail.

## Design Philosophy

- **Minimal dependencies**: Simple Python scripts, no complex frameworks
- **Single machine**: Designed for 10K-100K notes on one machine
- **User responsibility**: GPU setup, Docker configuration handled by user
- **Simple chunking**: No subject grouping, just batch by size

## Related Projects

- [phebee](https://github.com/nch-cloud/phebee) - PheBee storage/query system
- [phebee-tool-wrappers](https://github.com/nch-cloud/phebee-tool-wrappers) - Reference extraction tool implementations

## Troubleshooting

**Docker daemon not running:**
```bash
# Mac: Start Docker Desktop
# Linux: sudo systemctl start docker
```

**Permission denied:**
```bash
# Add user to docker group (Linux)
sudo usermod -aG docker $USER
# Log out and back in
```

**GPU not available:**
- Install [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html)
- Script will fail gracefully if GPU unavailable

**Module not found:**
```bash
uv sync  # or: pip install -e .
```

## Known Limitations

- Single machine processing (no distributed execution)
- No resume capability (restart required on crash)
- Sequential batch processing (no parallelization)
- Entire input loaded into memory during batching

## License

BSD 3-Clause License - see [LICENSE](LICENSE) file for details.

## Contributing

Issues and pull requests welcome. For major changes, please open an issue first to discuss what you'd like to change.

Run tests and linting before submitting:
```bash
uv run pytest tests/ -v
uv run ruff check .
```
