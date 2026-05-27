# Testing Guide

Complete testing documentation for PheBee local extraction pipeline.

## Quick Start

```bash
# Install dev dependencies
pip install -r requirements-dev.txt

# Run unit tests (fast, no Docker needed)
pytest test_extract.py -v

# Run with coverage
pytest test_extract.py --cov=extract --cov-report=term-missing

# Build mock Docker images (first time only)
./build_mocks.sh

# Run integration tests (requires Docker)
pytest test_integration.py -v -s
```

## Test Suite Overview

- **34 unit tests** - Fast tests with no external dependencies (<1 second)
- **10+ integration tests** - End-to-end workflows with Docker (~10 seconds)
- **59% code coverage** - Unit tests alone, full coverage with integration tests

## Test Structure

### Unit Tests (`test_extract.py`)

Fast, isolated tests without Docker:

- **Input loading** (8 tests) - JSON/JSONL/CSV/Parquet, auto-detection, error handling
- **Input validation** (5 tests) - Required fields, duplicates, empty records
- **Batch creation** (5 tests) - Splitting, numbering, field preservation
- **Output validation** (4 tests) - Format validation, required fields
- **Aggregation** (5 tests) - JSONL output, metadata merging, record preservation
- **Tool chaining** (2 tests) - Stage path determination, multi-stage pipelines
- **CLI parsing** (4 tests) - Arguments, defaults, validation
- **Error handling** (2 tests) - Exception classes, context helpers

### Integration Tests (`test_integration.py`)

End-to-end workflows requiring Docker:

- Complete extraction workflow (single tool)
- Tool chaining (extraction → context classification)
- Different input formats
- Batch size variations
- GPU flag handling
- Container failure scenarios
- Output validation

## Mock Docker Tools

We provide **two mock Docker containers** for testing:

### 1. Mock Extractor (`phebee-mock-extractor:test`)

Keyword-based phenotype extraction for testing.

**What it does:**
- Finds clinical terms in text: fever, cough, headache, seizure, pneumonia, migraine
- Maps to correct HPO terms
- Creates realistic annotations with correct span positions
- Fast (milliseconds, no ML models needed)

**Example:**

Input:
```json
{
  "records": [
    {"note_id": "n1", "text": "Patient has fever and cough."}
  ]
}
```

Output:
```json
{
  "records": [
    {
      "note_id": "n1",
      "text": "Patient has fever and cough.",
      "annotations": [
        {"start": 12, "end": 17, "text": "fever", "term_id": "HP:0001945", "label": "Fever", "ontology": "hpo"},
        {"start": 22, "end": 27, "text": "cough", "term_id": "HP:0012735", "label": "Cough", "ontology": "hpo"}
      ]
    }
  ],
  "run_metadata": {
    "run_id": "mock-1234567890",
    "annotator": "MockExtractor",
    "annotator_version": "v1.0.0",
    ...
  }
}
```

**Build & run:**
```bash
# Build
docker build -f Dockerfile.mock -t phebee-mock-extractor:test .

# Run
docker run --rm \
  -v $(pwd)/data:/data \
  -e INPUT_S3_URI=/data/input.json \
  -e OUTPUT_S3_URI=/data/output.json \
  phebee-mock-extractor:test
```

### 2. Mock Context Classifier (`phebee-mock-context:test`)

Adds context qualifiers to existing annotations.

**What it does:**
- Takes annotations from previous tool
- Detects context keywords around each annotation (±20 characters)
- Adds qualifiers:
  - **negated: 1** - Detects: no, denies, denied, negative, without, absence
  - **family: 1** - Detects: family, mother, father, sibling, hereditary
  - **hypothetical: 1** - Detects: possible, suspected, may, could, rule out

**Example:**

Input (from extractor):
```json
{
  "records": [
    {
      "note_id": "n1",
      "text": "Family history of migraines. Patient has headaches.",
      "annotations": [
        {"start": 18, "end": 27, "text": "migraines", "term_id": "HP:0002076", ...},
        {"start": 42, "end": 51, "text": "headaches", "term_id": "HP:0002315", ...}
      ]
    }
  ]
}
```

Output (with contexts):
```json
{
  "records": [
    {
      "note_id": "n1",
      "text": "Family history of migraines. Patient has headaches.",
      "annotations": [
        {
          "start": 18, "end": 27, "text": "migraines", "term_id": "HP:0002076",
          "contexts": {"negated": 0, "family": 1, "hypothetical": 0}  ← Detected "Family history"
        },
        {
          "start": 42, "end": 51, "text": "headaches", "term_id": "HP:0002315",
          "contexts": {"negated": 0, "family": 0, "hypothetical": 0}
        }
      ]
    }
  ]
}
```

**Build & run:**
```bash
# Build
docker build -f Dockerfile.mock-context -t phebee-mock-context:test .

# Run (requires input with existing annotations)
docker run --rm \
  -v $(pwd)/data:/data \
  -e INPUT_S3_URI=/data/annotations.json \
  -e OUTPUT_S3_URI=/data/with_contexts.json \
  phebee-mock-context:test
```

### Tool Chaining

Test extraction → context pipeline:

```bash
# Manual chaining
docker run --rm -v $(pwd)/data:/data \
  -e INPUT_S3_URI=/data/input.json \
  -e OUTPUT_S3_URI=/data/stage1.json \
  phebee-mock-extractor:test

docker run --rm -v $(pwd)/data:/data \
  -e INPUT_S3_URI=/data/stage1.json \
  -e OUTPUT_S3_URI=/data/final.json \
  phebee-mock-context:test

# Or use extract.py
python extract.py \
  --input data/input.jsonl \
  --output results/ \
  --docker-images phebee-mock-extractor:test,phebee-mock-context:test
```

## How Integration Tests Work

Integration tests validate the entire pipeline end-to-end:

### Architecture

```
Test → extract.py → Docker container → Mock tool
                         ↓
              Writes output via volume mount
                         ↓
extract.py ← Reads output ← Aggregates → JSONL
                         ↓
                    Test validates
```

### Key Concepts

**1. Volume Mounting**
- Host directory mounted inside container at `/data`
- Files written by container appear on host automatically
- No copying needed - shared filesystem

**2. Environment Variables**
- `INPUT_S3_URI=/data/batch_input.json` tells container where to read
- `OUTPUT_S3_URI=/data/batch_output.json` tells container where to write
- Despite "S3_URI" name, local paths work (AWS compatibility)

**3. INTERFACE.md Compliance**
- Mock tools follow same spec as real extraction tools
- Input: `{"records": [...]}`
- Output: `{"records": [...], "run_metadata": {...}}`
- Exit code 0 = success

## Running Tests

### Unit Tests Only

```bash
# All unit tests
pytest test_extract.py -v

# Specific test
pytest test_extract.py::test_load_json_input -v

# With coverage report
pytest test_extract.py --cov=extract --cov-report=html
# Open htmlcov/index.html in browser
```

### Integration Tests

Requires Docker daemon running:

```bash
# Build mock images (first time)
./build_mocks.sh

# Run all integration tests
pytest test_integration.py -v -s

# Run specific test
pytest test_integration.py::test_end_to_end_single_tool -v -s
```

### All Tests

```bash
# Run everything
pytest -v

# With coverage
pytest --cov=extract --cov-report=term-missing
```

## Test Fixtures

Located in `fixtures/`:
- `sample_input.json` - 3 test notes in JSON format
- `sample_output.json` - Expected output structure
- `sample_notes.csv` - CSV format with extra columns

## Extending the Mocks

### Add Keywords to Extractor

Edit `mock_extractor.py`:

```python
KEYWORD_MAP = {
    'fever': ('HP:0001945', 'Fever'),
    'your_term': ('HP:XXXXXXX', 'Your Label'),  # Add here
    ...
}
```

### Add Context Types

Edit `mock_context.py`:

```python
SEVERITY_WORDS = {'severe', 'mild', 'moderate'}

def classify_context(text, annotation):
    # ... existing code ...
    severity = detect_severity(words)
    annotation['contexts']['severity'] = severity
```

## Coverage Analysis

Current coverage: **59%** from unit tests alone

**Well-covered areas:**
- Input loading: 100%
- Validation: 100%
- Batch creation: 100%
- CLI parsing: 100%
- Aggregation: 95%

**Integration test coverage:**
- Docker operations
- Container execution
- Pipeline orchestration
- Main execution flow

## Troubleshooting

### Docker Permission Errors

```bash
# Add user to docker group
sudo usermod -aG docker $USER
# Then log out and back in
```

### Module Import Errors

```bash
# Run from project root
cd /path/to/phebee-extraction
pytest tests/
```

### Docker Daemon Not Running

```bash
# Mac: Start Docker Desktop
# Linux: sudo systemctl start docker
```

### Mock Images Not Found

```bash
# Rebuild images
./build_mocks.sh
```

## Continuous Integration

Example GitHub Actions workflow:

```yaml
name: Tests
on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: '3.10'
      
      # Unit tests
      - run: pip install -r requirements-dev.txt
      - run: pytest tests/test_extract.py --cov=extract
      
      # Integration tests
      - run: ./tests/build_mocks.sh
      - run: pytest tests/test_integration.py -v
```

## Mock vs Real Tools

| Feature | Mock Tools | Real Tools (e.g., FastHPOCR) |
|---------|------------|------------------------------|
| Speed | Milliseconds | Seconds to minutes |
| Accuracy | Keyword matching | NLP models, embeddings |
| GPU | Not needed | Often required |
| Terms | ~7 hardcoded | 10,000+ HPO terms |
| Memory | ~50MB | 1-8GB |
| Use case | Testing | Production |

**For production:** Swap in real tools like `fasthpocr:batch-latest` or `phenocontext:batch-latest` - same interface, better quality!

## Summary

- **34 unit tests** - Test individual functions (fast, no Docker)
- **10+ integration tests** - Test full pipeline (requires Docker)
- **2 mock tools** - Extractor (keyword-based) + Context classifier
- **59% coverage** - From unit tests alone
- **Same tests work with real tools** - Just change Docker image name!

The test suite ensures the extraction pipeline works correctly across different input formats, batch sizes, tool chains, and error scenarios.
