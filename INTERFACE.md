# PheBee Extraction Docker Container Interface Specification

**Version:** 1.0  
**Last Updated:** 2026-05-22

This document defines the interface contract between extraction orchestrators (AWS pipeline or local script) and Docker containers that perform clinical note phenotype extraction.

## Overview

Extraction containers must:
1. Accept input batch via S3 URI or local file path (environment variable)
2. Process notes and extract phenotype annotations
3. Write output results to S3 URI or local file path (environment variable)
4. Exit with code 0 on success, non-zero on failure

## Container Execution

### Environment Variables

The container **MUST** accept these environment variables:

| Variable | Required | Description | Example |
|----------|----------|-------------|---------|
| `INPUT_S3_URI` | Yes | Location of input batch file (local path or S3 URI) | `/data/input.json` or `s3://bucket/batches/batch_000001.json` |
| `OUTPUT_S3_URI` | Yes | Location to write results (local path or S3 URI) | `/data/output.json` or `s3://bucket/results/batch_000001.json` |

**Note:** Despite the `S3_URI` naming for compatibility with AWS pipelines, these variables accept both local file paths and S3 URIs. For local extraction, use mounted volume paths like `/data/input.json`. Containers should detect the format and handle accordingly.

### Docker Run Examples

**Local (typical use case):**
```bash
docker run --rm \
  -v /local/data:/data \
  -e INPUT_S3_URI=/data/input.json \
  -e OUTPUT_S3_URI=/data/output.json \
  fasthpocr:batch-latest
```

**Local with GPU:**
```bash
docker run --rm --gpus all \
  -v /local/data:/data \
  -e INPUT_S3_URI=/data/input.json \
  -e OUTPUT_S3_URI=/data/output.json \
  phenobert:batch-latest
```

**AWS (S3):**
```bash
docker run --rm \
  -e INPUT_S3_URI=s3://mybucket/runs/exec-123/batches/batch_000001.json \
  -e OUTPUT_S3_URI=s3://mybucket/runs/exec-123/results/batch_000001_result.json \
  -e AWS_DEFAULT_REGION=us-east-2 \
  fasthpocr:batch-latest
```

### Exit Codes

- `0` - Success
- Non-zero - Failure (container logs should contain error details)

## Input Format

The input file is a JSON file conforming to the [input schema](https://github.com/nch-cloud/phebee-tool-wrappers/blob/main/phenotype_adapter/src/phenotype_adapter/schemas/input.schema.json):

```json
{
  "records": [
    {
      "note_id": "note_12345",
      "text": "Patient presents with fever and cough. Denies chest pain."
    },
    {
      "note_id": "note_12346",
      "text": "Patient admitted with pneumonia and requires supplemental oxygen."
    }
  ]
}
```

### Input Field Specifications

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `records` | Array | Yes | Array of clinical notes to process |
| `records[].note_id` | String | Yes | Unique identifier for the note |
| `records[].text` | String | Yes | Full text content of the clinical note |
| `records[].annotations` | Array | No | Pre-computed annotations (for context classifiers or chained tools) |
| `records[].annotations[].start` | Integer | Yes* | Character offset where annotation starts (0-indexed) |
| `records[].annotations[].end` | Integer | Yes* | Character offset where annotation ends (exclusive) |
| `records[].annotations[].text` | String | Yes* | Exact text span that was annotated |
| `records[].annotations[].term_id` | String | Yes* | Ontology term identifier (e.g., "HP:0001945") |
| `records[].annotations[].label` | String | Yes* | Human-readable label for the term |
| `records[].annotations[].ontology` | String | No | Ontology source (e.g., "HPO", "MONDO", "ORPHA") |
| `ontologies` | Array | No | List of ontologies to use for annotation (default: ["HPO"]) |

*Required if `annotations` array is present

**Notes:**
- **Annotation tools** Require only `note_id` and `text`
- **Context classifiers** Require pre-existing `annotations` to classify
- Additional metadata fields (subject_id, encounter_id, mrn, etc.) are preserved if present but not required by the tool wrappers

## Output Format

The output file **MUST** be a JSON file conforming to the [output schema](https://github.com/nch-cloud/phebee-tool-wrappers/blob/main/phenotype_adapter/src/phenotype_adapter/schemas/output.schema.json):

```json
{
  "records": [
    {
      "note_id": "note_12345",
      "text": "Patient presents with fever and cough. Denies chest pain.",
      "annotations": [
        {
          "start": 22,
          "end": 27,
          "text": "fever",
          "term_id": "HP:0001945",
          "label": "Fever",
          "ontology": "hpo",
          "contexts": {
            "negated": 0,
            "family": 0,
            "hypothetical": 0
          }
        },
        {
          "start": 32,
          "end": 37,
          "text": "cough",
          "term_id": "HP:0012735",
          "label": "Cough",
          "ontology": "hpo",
          "contexts": {
            "negated": 0,
            "family": 0,
            "hypothetical": 0
          }
        },
        {
          "start": 46,
          "end": 56,
          "text": "chest pain",
          "term_id": "HP:0001962",
          "label": "Chest pain",
          "ontology": "hpo",
          "contexts": {
            "negated": 1,
            "family": 0,
            "hypothetical": 0
          }
        }
      ],
      "error": null
    },
    {
      "note_id": "note_12346",
      "text": "Patient admitted with pneumonia and requires supplemental oxygen.",
      "annotations": [
        {
          "start": 22,
          "end": 31,
          "text": "pneumonia",
          "term_id": "HP:0002090",
          "label": "Pneumonia",
          "ontology": "hpo"
        }
      ],
      "error": null
    }
  ],
  "run_metadata": {
    "run_id": "550e8400-e29b-41d4-a716-446655440000",
    "annotator": "FastHPOCR",
    "annotator_version": "v0.0.1",
    "timestamp": "2026-05-22T14:30:00Z",
    "models": {
      "phenotype": "5b9339d49ae7ce047984b7459df596bc7be03e7bfca8208a3ca290de0b44a755",
      "context": null
    },
    "runtime_ms": 8927,
    "status": "success",
    "error_message": null
  }
}
```

### Output Field Specifications

#### Record Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `records` | Array | Yes | Array of processed records (matches input records) |
| `records[].note_id` | String | Yes | Unique identifier (matches input) |
| `records[].text` | String | No | Original text content (may be omitted by some tools) |
| `records[].annotations` | Array | Yes | Array of extracted/classified annotations |
| `records[].error` | String/null | Yes | Error message if processing this record failed, otherwise null |

**Notes:**
- All input fields are preserved in output records
- If a note has no annotations, `annotations` must be an empty array: `[]`
- Additional metadata fields from input (subject_id, encounter_id, etc.) are preserved if present

#### Annotation Object Fields

Each annotation in the `annotations` array **MUST** have:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `start` | Integer | Yes | Character offset where annotation starts (0-indexed) |
| `end` | Integer | Yes | Character offset where annotation ends (exclusive) |
| `text` | String | Yes | Exact text span from the note that was annotated |
| `term_id` | String | Yes | Ontology term identifier (e.g., "HP:0001945", "MONDO:0005015") |
| `label` | String | Yes | Human-readable label for the term |
| `ontology` | String | Yes | Ontology code (e.g., "hpo", "mondo", "orpha") |
| `contexts` | Object | No | Context qualifiers (added by context classifiers like PhenoContext) |

#### Context/Qualifier Fields

The `contexts` object contains qualifiers that modify the interpretation of the annotation. PheBee is agnostic to context keys, so tools may add any context keys.  Some common contexts include:

| Key | Type | Values | Description |
|-----|------|--------|-------------|
| `negated` | Integer | 0, 1 | 1 if the term is negated, 0 otherwise |
| `family` | Integer | 0, 1 | 1 if the term refers to a family member, 0 otherwise |
| `hypothetical` | Integer | 0, 1 | 1 if the term's use is speculative, 0 otherwise |

**Note:** The `contexts` field is optional and only present when annotations have been processed by a context classifier.

#### Run Metadata

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `run_metadata` | Object | Yes | Metadata about the processing run |
| `run_metadata.run_id` | String | Yes | Unique identifier for this run (UUID) |
| `run_metadata.annotator` | String | Yes | Name of the tool (e.g., "FastHPOCR", "PhenoContext") |
| `run_metadata.annotator_version` | String | Yes | Version of the tool wrapper |
| `run_metadata.timestamp` | String | Yes | ISO 8601 timestamp when processing occurred |
| `run_metadata.models` | Object | Yes | Model identifiers/checksums used |
| `run_metadata.models.phenotype` | String/null | Yes | Checksum of phenotype extraction model (null if not applicable) |
| `run_metadata.models.context` | String/null | Yes | Checksum of context classification model (null if not applicable) |
| `run_metadata.runtime_ms` | Integer | Yes | Total processing time in milliseconds |
| `run_metadata.status` | String | Yes | "success" or "error" |
| `run_metadata.error_message` | String/null | Yes | Error description if status is "error", otherwise null |

## Validation Rules

The output is validated against the [JSON schema](https://github.com/nch-cloud/phebee-tool-wrappers/blob/main/phenotype_adapter/src/phenotype_adapter/schemas/output.schema.json) and these business rules:

1. **All input records must have corresponding output records** - Same `note_id` (and preserved metadata fields)
2. **No duplicate note_ids** - Each note_id should appear exactly once in the output
3. **Text spans must be valid** - `start < end` and both must be within note text length
4. **Required fields must be present** - All required annotation fields must be non-null
5. **Ontology codes must be lowercase** - Use "hpo" not "HP", "mondo" not "MONDO"
6. **Context values must be integers** - Use `0` or `1`, not `"false"` or `"true"`
7. **Status must be valid** - `run_metadata.status` must be either "success" or "error"

## Error Handling

If the container encounters an error:

1. **Log to stderr** - Write detailed error information to standard error
2. **Exit with non-zero code** - Use appropriate exit codes:
   - `1` - General error
   - `2` - Invalid input format
   - `3` - Model/tool error
   - `4` - I/O error (cannot read input or write output)
3. **Partial results** - Do not write partial output files on failure

## Tool Chaining

Tools can be chained by using the output of one tool as input to another. The most common pattern is extraction → context classification:

```
FastHPOCR (extraction) → PhenoContext (context)
```

**Chaining behavior:**

1. **Records**: Downstream tools receive all `records` with annotations from upstream tools
2. **Annotations**: Context classifiers add `contexts` field to existing annotations; they do not extract new terms
3. **Metadata**: 
   - Extraction tools set `models.phenotype` and leave `models.context = null`
   - Context classifiers should preserve `models.phenotype` from input and set `models.context`

**Example for context classifier:**
```python
# Read input
input_data = json.load(input_file)

# Preserve phenotype model from previous tool
phenotype_model = None
if "run_metadata" in input_data:
    phenotype_model = input_data["run_metadata"]["models"]["phenotype"]

# Create output metadata
output = {
    "records": processed_records,
    "run_metadata": {
        "models": {
            "phenotype": phenotype_model,  # Preserved from input
            "context": "your_context_model_checksum"
        },
        ...
    }
}
```