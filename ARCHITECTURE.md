# Architecture

This document explains how the pipeline is put together and where to plug in if you want to
run it in an environment other than a single machine with Docker. For how to *use* the local
tool, see [README.md](README.md). For the contract a tool container must satisfy, see
[INTERFACE.md](INTERFACE.md).

## The pipeline in one picture

Extraction is three stages. Only the middle one needs compute beyond a laptop.

```
                 split                      run stages (per batch)                  transform
 clinical  ───────────────►  batch files  ─────────────────────────────►  tool outputs  ─────────────►  JSONL
 notes                        + manifest     container 1 → container 2 …                                 (PheBee or flat)
 (JSON/JSONL/CSV/Parquet)
```

1. **split** — read the notes, validate them, cut them into batches of N, write one
   `batch_NNNNNN_input.json` per batch plus a `manifest.json` listing them.
2. **run stages** — for each batch, run each tool container in order. A container reads one
   JSON file and writes one JSON file; the output of container *k* is the input of container
   *k+1*. The last container's output is the batch's tool output.
3. **transform** — turn the tool outputs into the final JSONL. The default is PheBee
   bulk-import format (one line per annotation); `--flat-format` keeps one line per note.

The local tool (`extract.py`) runs all three in one process with Docker as the stage
executor. Everything else in this document is about running stage 2 somewhere else while
reusing stages 1 and 3 unchanged.

## Why this shape

Two properties make the pipeline easy to distribute:

- **Batches are independent.** Nothing in stage 2 or 3 needs to see more than one batch at a
  time. There is no subject grouping, no global sort and no shuffle. A batch can run on any
  machine, in any order, in parallel with the others.
- **The PheBee transform is per-record.** PheBee's bulk import does not require evidence for
  the same subject and term to be grouped on one line, so each tool-output record is
  converted on its own. The transform can run per batch and write one part file per batch.

The consequence is that the only state the orchestrator must carry between steps is the
list of batches, which is what `manifest.json` records.

## Artifacts

All artifacts are plain JSON or JSONL files. The core never assumes object storage; moving
files to and from S3, GCS or a shared filesystem is the wrapper's job.

| Artifact | Produced by | Consumed by | Format |
|---|---|---|---|
| `batch_NNNNNN_input.json` | split | first container | `{"records": [{note_id, text, ...}]}` ([INTERFACE.md](INTERFACE.md)) |
| `manifest.json` | split | the scheduler | see below |
| `batch_NNNNNN_stageK.json` | container K | container K+1 | same shape as output, below |
| `batch_NNNNNN_output.json` | last container | transform | `{"records": [...], "run_metadata": {...}}` ([INTERFACE.md](INTERFACE.md)) |
| `aggregated.json` / `*.json` | transform | PheBee import, or you | JSON Lines, PheBee or flat format ([README.md](README.md#output-format)). Named `.json` because PheBee's import only reads `*.json` |

`manifest.json`:

```json
{
  "total_records": 2500,
  "batch_size": 1000,
  "batch_count": 3,
  "batches": [
    {"batch_id": "batch_000001", "input_file": "batch_000001_input.json",
     "output_file": "batch_000001_output.json", "record_count": 1000},
    ...
  ]
}
```

File names are relative to the batch directory. `output_file` is the name the pipeline
expects the *final* stage to write; intermediate stage files are
`batch_NNNNNN_stage1.json`, `_stage2.json`, and so on (`batching.get_stage_paths`).

### Input fields survive the containers

Tool containers are only required to preserve `note_id` and `text`; in practice some drop
everything else, including the subject identifier the PheBee format needs. The pipeline
therefore merges the original input record's fields back into each output record after the
last stage, matching on `note_id` (`interface.merge_original_records`). Fields the tool did
write win. If you run the stages yourself, do the same, or pass the batch input files to
`transform --original-input`.

## Where to plug in

There are three levels, from least to most code.

### 1. Command line: `split` and `transform`

No Python needed. Use when your scheduler is a workflow engine or a job array (Slurm,
Nextflow, Snakemake, Argo, a shell loop).

```bash
python extract.py split --input notes.csv --output-dir batches/ --batch-size 1000 \
  --id-column note_id --text-column note_text

# For every batch in manifest.json, for every tool in order:
#   run the container with INPUT_URI=<previous output> OUTPUT_URI=<next file>

python extract.py transform \
  --input batches/batch_*_output.json \
  --original-input batches/batch_*_input.json \
  --output evidence.json --project-id my-project --subject-id-column mrn \
  --image-ref tool-a:1.0@sha256:... tool-b:2.0@sha256:...
```

`transform` can also be called once per batch with `--start-row-num` to produce
non-overlapping part files, which is the natural fit for a fan-out/fan-in workflow.

### 2. Python: implement `StageRunner`

Use when you are writing an orchestrator in Python and want the pipeline loop, progress
reporting and aggregation for free. The runner is the only thing that knows how a container
gets executed.

```python
from typing import List
from phebee_extraction import StageResult

class MyRunner:
    def prepare(self, images: List[str]) -> List[str]:
        """Check the images exist; return each as name[:tag]@sha256:... for provenance."""

    def run_stage(self, image: str, input_path: str, output_path: str) -> StageResult:
        """Run `image` so it reads input_path and writes output_path; report the outcome."""
```

Both methods are synchronous: `run_stage` returns when the stage has finished. If your
backend is asynchronous (submit a job, wait for it), the wait goes inside `run_stage`.

The runner is handed to `ExecutionContext.runner`, and the rest of the local pipeline
(`pipeline.run_batch_pipeline`, `pipeline.aggregate_results`) is unchanged. `cli.run_command`
is the reference for wiring it together. `ExecutionContext` is the *local* orchestration
object and carries local settings (`temp_dir`, `gpu`, `docker_timeout`); a runner is free to
ignore the ones that do not apply.

The contract `run_stage` must honour:

- Set the container's `INPUT_URI` and `OUTPUT_URI` to wherever the batch file and output
  should live *from the container's point of view*. Set the legacy `INPUT_S3_URI` /
  `OUTPUT_S3_URI` to the same values for older images (`interface.build_container_environment`
  does this for the local mount layout).
- Return `StageResult(status="success", ...)` when the container exited 0. The output must
  then exist at `output_path`; the pipeline checks this and treats a missing file as a failed
  stage, since the next step will read it. If your backend writes output to remote storage,
  `run_stage` must fetch it to `output_path` before returning.
- On failure, put the container logs in `StageResult.logs`; the pipeline prints them.
- Never raise for an ordinary failure; return a failed result so `--continue-on-error` works.

### 3. Python: use the stage functions directly

Use when you want neither the local loop nor `ExecutionContext`, for example in a serverless
function that only does one step.

| Step | Functions |
|---|---|
| split | `loading.load_input_data`, `loading.validate_input_records`, `batching.create_batches`, `batching.write_manifest` |
| merge-back | `interface.merge_original_records` |
| transform | `transform.PheBeeConfig`, `transform.write_phebee_jsonl` / `write_phebee_records`, `transform.write_flat_jsonl` |
| validation | `interface.read_batch_output` (checks the output shape) |

None of these import Docker. `write_phebee_records` streams to an open file handle, so it
does not need to hold a batch in memory beyond the current record.

## Provenance

PheBee evidence records carry three creator fields, all derived from the image references
the runner's `prepare` returns, in pipeline order:

| Field | Value for `a:1@sha256:x` then `b:2@sha256:y` |
|---|---|
| `evidence_creator_id` | `a:1+b:2` |
| `evidence_creator_name` | same as `evidence_creator_id` |
| `evidence_creator_digest` | `sha256:x+sha256:y`, or `null` if any image lacks a digest |

The same tool chain therefore always yields the same `evidence_creator_id`, and the digest
records exactly which builds ran. `DockerRunner.prepare` takes digests from the local Docker
daemon; a cloud runner should take them from its registry (for ECR, `describe-images`).
When driving `transform` from the command line, pass the same references with `--image-ref`.

`evidence_creator_digest` is this tool's addition to the PheBee format, and PheBee's bulk
import currently accepts and drops it: the evidence table stores `creator_id`, `creator_type`
and `creator_name` only. The field is named to PheBee's `evidence_creator_*` → `creator_*`
convention so a future `creator_digest` column can read it unchanged. Until that exists, the
digest lives only in the uploaded file, and the tag in `evidence_creator_id` is the only
provenance PheBee keeps; prefer immutable tags for imported runs. The field contract is in
[README.md](README.md#creator-fields-and-evidence_creator_digest).

## Ordering and `row_num`

PheBee records carry a `row_num`. Within one `transform` call it is a 1-based running count
starting at `--start-row-num`. If you write part files per batch, choose offsets so they do
not overlap (for example `batch_index * batch_size * max_annotations_per_note`, or run the
batches' transforms sequentially and pass the previous `next_row_num` along). This tool only
guarantees that values are increasing within one call; check what your importer expects
(unique, dense, or neither) before relying on gaps between part files.

Records with an error, no subject identifier, or no annotations are skipped and counted
(`transform.PheBeeWriteStats`), not written.

## Sketch: a cloud deployment

The shape below is what a slim cloud wrapper looks like. It is deliberately generic; the
concrete AWS version lives in its own repository and pins a version of this package.

```
 object store ──► function: split ──► workflow: for each batch (parallel)
                                          │  job: container 1  ─┐  (runner or
                                          │  job: container 2  ─┘   job dependencies)
                                          │  function: merge-back + transform ──► part file
                                          ▼
                                      function: concatenate parts (if the importer needs one file)
                                                 and trigger import
```

Substitute Batch/Step Functions, Cloud Run Jobs/Workflows, or a Kubernetes Job plus Argo.
The split and transform functions import this package; the jobs run the unchanged tool
images; the workflow engine supplies retries and visibility.

## What the core deliberately leaves out

These belong to the wrapper or the scheduler, and keeping them out is what keeps the core
portable:

- **Object storage.** The core reads and writes local paths. Wrappers stage files.
- **Parallelism and queueing.** `run` processes batches sequentially; the batches are
  independent so a wrapper may run them concurrently.
- **Status tracking and resume.** There is no state beyond `manifest.json` and the files on
  disk. Re-run a batch by re-running its stages; the workflow engine is the place for retry
  policy.
- **Scale beyond a single machine's memory for `split`.** `load_input_data` reads the whole
  input. For inputs too large for one process, shard the input first and run `split` per shard
  (batch ids are per split call, so prefix or namespace the batch directories).

## Module map

```
phebee_extraction/
  loading.py        read notes from JSON/JSONL/CSV/Parquet; coerce ID fields to strings
  batching.py       create_batches, write_manifest, get_stage_paths
  interface.py      the container contract: batch file I/O, env vars, output validation,
                    merge_original_records
  runner.py         StageRunner protocol, StageResult
  docker_runner.py  DockerRunner (the only module that imports docker)
  transform.py      PheBeeConfig, record_to_phebee_format, write_phebee_jsonl,
                    write_flat_jsonl, evidence_creator_fields
  pipeline.py       ExecutionContext, run_batch_pipeline, aggregate_results (local loop)
  review.py         read_results, format_review: join PheBee output back to the input notes
  cli.py            run / split / transform / review commands
  models.py         BatchInfo and exceptions
extract.py          thin entry point; re-exports the package API
```
