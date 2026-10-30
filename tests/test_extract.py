"""
Unit tests for extract.py

Tests individual functions without requiring Docker.
"""

import json
import logging
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Add parent directory to path so we can import extract
sys.path.insert(0, str(Path(__file__).parent.parent))

import extract

# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def fixtures_dir():
    """Return path to test fixtures directory"""
    return Path(__file__).parent / "fixtures"


@pytest.fixture
def sample_records():
    """Return sample note records for testing"""
    return [
        {"note_id": "note_001", "text": "Patient presents with fever."},
        {"note_id": "note_002", "text": "Chronic headaches noted."},
        {"note_id": "note_003", "text": "Developmental delay at 18 months."},
    ]


@pytest.fixture
def sample_batch_outputs():
    """Return sample batch outputs for aggregation testing"""
    return [
        {
            "records": [
                {"note_id": "note_001", "text": "Text 1", "annotations": [], "error": None}
            ],
            "run_metadata": {
                "run_id": "run-001",
                "annotator": "TestTool",
                "annotator_version": "v1.0",
                "timestamp": "2026-05-23T10:00:00Z",
                "models": {"phenotype": "abc123", "context": None},
                "runtime_ms": 100,
                "status": "success",
            },
        },
        {
            "records": [
                {"note_id": "note_002", "text": "Text 2", "annotations": [], "error": None}
            ],
            "run_metadata": {
                "run_id": "run-002",
                "annotator": "TestTool",
                "annotator_version": "v1.0",
                "timestamp": "2026-05-23T10:00:01Z",
                "models": {"phenotype": "abc123", "context": None},
                "runtime_ms": 150,
                "status": "success",
            },
        },
    ]


# ============================================================================
# Input Loading Tests
# ============================================================================


def test_detect_input_format():
    """Test format detection from file extension"""
    assert extract.detect_input_format("notes.json") == "json"
    assert extract.detect_input_format("notes.jsonl") == "jsonl"
    assert extract.detect_input_format("notes.parquet") == "parquet"
    assert extract.detect_input_format("notes.csv") == "csv"
    assert extract.detect_input_format("/path/to/notes.JSONL") == "jsonl"


def test_detect_input_format_unsupported():
    """Test error on unsupported format"""
    with pytest.raises(ValueError, match="Unsupported file extension"):
        extract.detect_input_format("notes.txt")


def test_load_json_input(fixtures_dir):
    """Test loading JSON format input"""
    input_file = fixtures_dir / "sample_input.json"
    records = extract.load_input_data(str(input_file), format="json")

    assert len(records) == 3
    assert records[0]["note_id"] == "test_001"
    assert "text" in records[0]


def test_load_jsonl_input(tmp_path):
    """Test loading JSONL format input"""
    # Create temp JSONL file
    jsonl_file = tmp_path / "test.jsonl"
    with open(jsonl_file, "w") as f:
        f.write('{"note_id": "n1", "text": "Text 1"}\n')
        f.write('{"note_id": "n2", "text": "Text 2"}\n')

    records = extract.load_input_data(str(jsonl_file), format="jsonl")

    assert len(records) == 2
    assert records[0]["note_id"] == "n1"
    assert records[1]["note_id"] == "n2"


def test_load_csv_input(fixtures_dir):
    """Test loading CSV format input"""
    csv_file = fixtures_dir / "sample_notes.csv"
    records = extract.load_input_data(str(csv_file), format="csv")

    assert len(records) == 3
    assert records[0]["note_id"] == "test_001"
    assert "text" in records[0]
    # Extra columns should be preserved
    assert "patient_id" in records[0]


def test_load_csv_custom_columns(tmp_path):
    """Test loading CSV with custom column names"""
    csv_file = tmp_path / "custom.csv"
    with open(csv_file, "w") as f:
        f.write("id,content\n")
        f.write("n1,Text 1\n")
        f.write("n2,Text 2\n")

    records = extract.load_input_data(
        str(csv_file), format="csv", id_column="id", text_column="content"
    )

    assert len(records) == 2
    assert records[0]["note_id"] == "n1"
    assert records[0]["text"] == "Text 1"


def test_load_converts_named_identifier_columns_to_strings(tmp_path):
    """note_id and encounter_id always; the caller names the rest (subject column, others)"""
    csv = tmp_path / "notes.csv"
    csv.write_text("note_id,mrn,encounter_id,visit_no,text\n101,3520819,7,42,fever\n")

    records = extract.load_input_data(str(csv), string_columns={"mrn"})

    (r,) = records
    assert r["note_id"] == "101" and r["mrn"] == "3520819" and r["encounter_id"] == "7"
    assert r["visit_no"] == 42  # not named, left as the reader produced it


def test_load_leaves_unnamed_numeric_columns_alone(tmp_path):
    """No hidden list of organisation-specific column names"""
    csv = tmp_path / "notes.csv"
    csv.write_text("note_id,mrn,pat_id,text\n1,2,3,fever\n")

    (r,) = extract.load_input_data(str(csv))

    assert r["note_id"] == "1"
    assert r["mrn"] == 2 and r["pat_id"] == 3


def test_load_missing_file():
    """Test error on missing input file"""
    with pytest.raises(FileNotFoundError):
        extract.load_input_data("nonexistent.json")


def test_load_csv_missing_column(tmp_path):
    """Test error when required column missing in CSV"""
    csv_file = tmp_path / "missing_col.csv"
    with open(csv_file, "w") as f:
        f.write("id,content\n")
        f.write("n1,Text 1\n")

    with pytest.raises(ValueError, match="not found in CSV"):
        extract.load_input_data(str(csv_file), format="csv")


# ============================================================================
# Input Validation Tests
# ============================================================================


def test_validate_input_records_success(sample_records):
    """Test validation passes for valid records"""
    extract.validate_input_records(sample_records)  # Should not raise


def test_validate_empty_records():
    """Test error on empty records list"""
    with pytest.raises(ValueError, match="No records found"):
        extract.validate_input_records([])


def test_validate_missing_note_id():
    """Test error on missing note_id field"""
    records = [{"text": "Some text"}]
    with pytest.raises(ValueError, match="missing required field 'note_id'"):
        extract.validate_input_records(records)


def test_validate_missing_text():
    """Test error on missing text field"""
    records = [{"note_id": "n1"}]
    with pytest.raises(ValueError, match="missing required field 'text'"):
        extract.validate_input_records(records)


def test_validate_duplicate_note_ids():
    """Test error on duplicate note_id"""
    records = [
        {"note_id": "n1", "text": "Text 1"},
        {"note_id": "n1", "text": "Text 2"},  # Duplicate
    ]
    with pytest.raises(ValueError, match="Duplicate note_id"):
        extract.validate_input_records(records)


# ============================================================================
# Batch Creation Tests
# ============================================================================


def test_create_batches(sample_records, tmp_path):
    """Test batch creation with multiple batches"""
    # Create 2500 records to generate 3 batches (batch_size=1000)
    records = []
    for i in range(2500):
        records.append({"note_id": f"note_{i:04d}", "text": f"Text {i}"})

    batches = extract.create_batches(records, batch_size=1000, temp_dir=str(tmp_path))

    assert len(batches) == 3
    assert batches[0].record_count == 1000
    assert batches[1].record_count == 1000
    assert batches[2].record_count == 500

    # Check batch files were created
    assert os.path.exists(batches[0].input_path)
    assert os.path.exists(batches[1].input_path)


def test_create_single_batch(sample_records, tmp_path):
    """Test batch creation when all records fit in one batch"""
    batches = extract.create_batches(sample_records, batch_size=1000, temp_dir=str(tmp_path))

    assert len(batches) == 1
    assert batches[0].record_count == 3
    assert batches[0].batch_id == "batch_000001"


def test_batch_numbering(sample_records, tmp_path):
    """Test zero-padded batch numbering"""
    records = []
    for i in range(50):
        records.append({"note_id": f"note_{i:04d}", "text": f"Text {i}"})

    batches = extract.create_batches(records, batch_size=10, temp_dir=str(tmp_path))

    assert batches[0].batch_id == "batch_000001"
    assert batches[4].batch_id == "batch_000005"
    assert batches[0].batch_num == 1
    assert batches[4].batch_num == 5


def test_write_batch_file(sample_records, tmp_path):
    """Test batch file writing matches INTERFACE.md format"""
    output_path = tmp_path / "batch_test.json"
    extract.write_batch_file(sample_records, str(output_path))

    with open(output_path) as f:
        data = json.load(f)

    assert "records" in data
    assert len(data["records"]) == 3
    assert data["records"][0]["note_id"] == "note_001"


def test_batch_preserves_fields(tmp_path):
    """Test that extra fields in records are preserved"""
    records = [{"note_id": "n1", "text": "Text", "patient_id": "P001", "encounter": "E001"}]

    batches = extract.create_batches(records, batch_size=10, temp_dir=str(tmp_path))

    with open(batches[0].input_path) as f:
        data = json.load(f)

    assert data["records"][0]["patient_id"] == "P001"
    assert data["records"][0]["encounter"] == "E001"


# ============================================================================
# Output Validation Tests
# ============================================================================


def test_validate_output_format_success(fixtures_dir):
    """Test validation passes for valid output"""
    with open(fixtures_dir / "sample_output.json") as f:
        output = json.load(f)

    extract.validate_output_format(output)  # Should not raise


def test_validate_output_missing_records():
    """Test error on missing records field"""
    output = {"run_metadata": {}}
    with pytest.raises(ValueError, match="missing required 'records'"):
        extract.validate_output_format(output)


def test_validate_output_missing_metadata():
    """Test error on missing run_metadata field"""
    output = {"records": []}
    with pytest.raises(ValueError, match="missing required 'run_metadata'"):
        extract.validate_output_format(output)


def test_validate_output_missing_metadata_fields():
    """Test error on missing required metadata fields"""
    output = {
        "records": [],
        "run_metadata": {
            "run_id": "test"
            # Missing other required fields
        },
    }
    with pytest.raises(ValueError, match="missing required field"):
        extract.validate_output_format(output)


# ============================================================================
# Aggregation Tests
# ============================================================================


def test_merge_run_metadata(sample_batch_outputs):
    """Test metadata merging from multiple batches (AWS pipeline format)"""
    metadata_list = [out["run_metadata"] for out in sample_batch_outputs]
    tools_used = ["tool1:latest", "tool2:latest"]
    merged = extract.merge_run_metadata(metadata_list, tools_used)

    assert "execution_name" in merged
    assert merged["execution_name"].startswith("local-extraction-")
    assert merged["tools_used"] == tools_used
    assert "processing_timestamp" in merged
    assert merged["batch_count"] == 2
    assert merged["status"] == "success"


def test_merge_run_metadata_earliest_timestamp():
    """Test that earliest timestamp is used"""
    metadata_list = [
        {
            "run_id": "r1",
            "annotator": "Tool",
            "annotator_version": "v1",
            "timestamp": "2026-05-23T10:05:00Z",
            "runtime_ms": 100,
            "models": {"phenotype": None, "context": None},
        },
        {
            "run_id": "r2",
            "annotator": "Tool",
            "annotator_version": "v1",
            "timestamp": "2026-05-23T10:00:00Z",  # Earlier
            "runtime_ms": 100,
            "models": {"phenotype": None, "context": None},
        },
    ]

    tools_used = ["tool:latest"]
    merged = extract.merge_run_metadata(metadata_list, tools_used)
    assert merged["processing_timestamp"] == "2026-05-23T10:00:00Z"


def test_aggregate_results_jsonl(sample_batch_outputs, tmp_path):
    """Test aggregation to JSONL flat format"""
    # Setup mock context
    ctx = extract.ExecutionContext(
        input_file="test.json",
        output_dir=str(tmp_path),
        docker_images=["test:latest"],
        batch_size=1000,
        gpu=False,
        temp_dir=str(tmp_path),
        keep_temp=False,
        continue_on_error=False,
        docker_timeout=3600,
        flat_format=True,  # Use flat format for this test
    )

    # Create mock batches
    batch1 = extract.BatchInfo(1, "batch_000001", "", str(tmp_path / "b1_out.json"), 1, "success")
    batch2 = extract.BatchInfo(2, "batch_000002", "", str(tmp_path / "b2_out.json"), 1, "success")
    ctx.batches = [batch1, batch2]

    # Write batch outputs
    with open(batch1.output_path, "w") as f:
        json.dump(sample_batch_outputs[0], f)
    with open(batch2.output_path, "w") as f:
        json.dump(sample_batch_outputs[1], f)

    # Aggregate
    output_file = tmp_path / "aggregated.json"
    extract.aggregate_results(ctx, str(output_file))

    # Verify JSONL format
    with open(output_file) as f:
        lines = f.readlines()

    assert len(lines) == 3  # 2 records + 1 metadata

    # Check records
    record1 = json.loads(lines[0])
    assert record1["note_id"] == "note_001"

    record2 = json.loads(lines[1])
    assert record2["note_id"] == "note_002"

    # Check metadata
    metadata_line = json.loads(lines[2])
    assert "_metadata" in metadata_line
    assert metadata_line["_metadata"]["total_records"] == 2
    assert metadata_line["_metadata"]["status"] == "success"


def test_aggregate_preserves_all_records(sample_batch_outputs, tmp_path):
    """Test that all records are preserved in aggregation"""
    # Modify sample to have multiple records per batch
    sample_batch_outputs[0]["records"] = [
        {"note_id": "n1", "text": "T1", "annotations": [], "error": None},
        {"note_id": "n2", "text": "T2", "annotations": [], "error": None},
    ]
    sample_batch_outputs[1]["records"] = [
        {"note_id": "n3", "text": "T3", "annotations": [], "error": None},
        {"note_id": "n4", "text": "T4", "annotations": [], "error": None},
        {"note_id": "n5", "text": "T5", "annotations": [], "error": None},
    ]

    ctx = extract.ExecutionContext(
        input_file="test.json",
        output_dir=str(tmp_path),
        docker_images=["test:latest"],
        batch_size=1000,
        gpu=False,
        temp_dir=str(tmp_path),
        keep_temp=False,
        continue_on_error=False,
        docker_timeout=3600,
        flat_format=True,  # Use flat format for this test
    )

    batch1 = extract.BatchInfo(1, "batch_000001", "", str(tmp_path / "b1_out.json"), 2, "success")
    batch2 = extract.BatchInfo(2, "batch_000002", "", str(tmp_path / "b2_out.json"), 3, "success")
    ctx.batches = [batch1, batch2]

    with open(batch1.output_path, "w") as f:
        json.dump(sample_batch_outputs[0], f)
    with open(batch2.output_path, "w") as f:
        json.dump(sample_batch_outputs[1], f)

    output_file = tmp_path / "aggregated.json"
    extract.aggregate_results(ctx, str(output_file))

    # Count records
    with open(output_file) as f:
        lines = f.readlines()

    assert len(lines) == 6  # 5 records + 1 metadata

    # Check metadata has correct count
    metadata = json.loads(lines[-1])
    assert metadata["_metadata"]["total_records"] == 5


def test_aggregate_phebee_format(sample_batch_outputs, tmp_path):
    """Test aggregation to PheBee format (streaming)"""
    # Add subject_id to records and more annotations
    sample_batch_outputs[0]["records"][0]["subject_id"] = "subj-001"
    sample_batch_outputs[0]["records"][0]["annotations"] = [
        {
            "start": 0,
            "end": 5,
            "text": "fever",
            "term_id": "HP:0001945",
            "label": "Fever",
            "ontology": "hpo",
            "contexts": {"negated": 0, "family": 0, "hypothetical": 0},
        },
        {
            "start": 10,
            "end": 15,
            "text": "cough",
            "term_id": "HP:0012735",
            "label": "Cough",
            "ontology": "hpo",
            "contexts": {"negated": 1, "family": 0, "hypothetical": 0},
        },
    ]

    sample_batch_outputs[1]["records"][0]["subject_id"] = "subj-002"
    sample_batch_outputs[1]["records"][0]["annotations"] = [
        {
            "start": 0,
            "end": 8,
            "text": "headache",
            "term_id": "HP:0002315",
            "label": "Headache",
            "ontology": "hpo",
            "contexts": {"negated": 0, "family": 1, "hypothetical": 0},
        }
    ]

    ctx = extract.ExecutionContext(
        input_file="test.json",
        output_dir=str(tmp_path),
        docker_images=["test:latest"],
        batch_size=1000,
        gpu=False,
        temp_dir=str(tmp_path),
        keep_temp=False,
        continue_on_error=False,
        docker_timeout=3600,
        flat_format=False,  # PheBee format
        project_id="test-project",
        docker_image_digests=["test:latest@sha256:abc123"],
    )

    batch1 = extract.BatchInfo(1, "batch_000001", "", str(tmp_path / "b1_out.json"), 1, "success")
    batch2 = extract.BatchInfo(2, "batch_000002", "", str(tmp_path / "b2_out.json"), 1, "success")
    ctx.batches = [batch1, batch2]

    with open(batch1.output_path, "w") as f:
        json.dump(sample_batch_outputs[0], f)
    with open(batch2.output_path, "w") as f:
        json.dump(sample_batch_outputs[1], f)

    output_file = tmp_path / "phebee.jsonl"
    extract.aggregate_results(ctx, str(output_file))

    # Read output
    with open(output_file) as f:
        lines = f.readlines()

    # Should have 3 PheBee records (2 annotations from batch1 + 1 from batch2)
    assert len(lines) == 3

    # Check first record
    rec1 = json.loads(lines[0])
    assert rec1["project_id"] == "test-project"
    assert rec1["project_subject_id"] == "subj-001"
    assert rec1["term_iri"] == "http://purl.obolibrary.org/obo/HP_0001945"
    assert len(rec1["evidence"]) == 1
    assert rec1["evidence"][0]["type"] == "clinical_note"
    assert rec1["evidence"][0]["clinical_note_id"] == "note_001"
    assert rec1["evidence"][0]["evidence_creator_id"] == "test:latest"
    assert rec1["evidence"][0]["evidence_creator_name"] == "test:latest"
    assert rec1["evidence"][0]["evidence_creator_digest"] == "sha256:abc123"
    assert rec1["evidence"][0]["contexts"]["negated"] is False
    assert rec1["row_num"] == 1
    assert "batch_id" not in rec1  # PheBee derives batch_id from the file name itself

    # Check second record (different term, same subject)
    rec2 = json.loads(lines[1])
    assert rec2["project_subject_id"] == "subj-001"
    assert rec2["term_iri"] == "http://purl.obolibrary.org/obo/HP_0012735"
    assert rec2["evidence"][0]["contexts"]["negated"] is True
    assert rec2["row_num"] == 2

    # Check third record (different subject)
    rec3 = json.loads(lines[2])
    assert rec3["project_subject_id"] == "subj-002"
    assert rec3["term_iri"] == "http://purl.obolibrary.org/obo/HP_0002315"
    assert rec3["evidence"][0]["contexts"]["family"] is True
    assert rec3["row_num"] == 3


def test_aggregate_phebee_fails_when_no_note_has_a_subject(sample_batch_outputs, tmp_path):
    """The 'Wrote 0 PheBee records' trap: a misconfigured subject column must fail, not succeed"""
    for out in sample_batch_outputs:
        for rec in out["records"]:
            rec.pop("subject_id", None)
            rec["annotations"] = [
                {"start": 0, "end": 5, "text": "fever", "term_id": "HP:0001945", "label": "Fever"}
            ]
    ctx = extract.ExecutionContext(
        input_file="test.json",
        output_dir=str(tmp_path),
        docker_images=["test:latest"],
        batch_size=1000,
        gpu=False,
        temp_dir=str(tmp_path),
        keep_temp=False,
        continue_on_error=False,
        docker_timeout=3600,
        project_id="test-project",
        subject_id_column="patient_id",
        docker_image_digests=["test:latest@sha256:abc123"],
    )
    batch1 = extract.BatchInfo(1, "batch_000001", "", str(tmp_path / "b1_out.json"), 1, "success")
    ctx.batches = [batch1]
    with open(batch1.output_path, "w") as f:
        json.dump(sample_batch_outputs[0], f)
    output_file = tmp_path / "phebee.json"

    with pytest.raises(extract.ExtractionError, match="patient_id.*--subject-id-column"):
        extract.aggregate_results(ctx, str(output_file))
    assert not output_file.exists()


def test_aggregate_phebee_only_warns_when_some_notes_lack_a_subject(
    sample_batch_outputs, tmp_path, caplog
):
    sample_batch_outputs[0]["records"][0]["subject_id"] = "subj-001"
    sample_batch_outputs[0]["records"][0]["annotations"] = [
        {"start": 0, "end": 5, "text": "fever", "term_id": "HP:0001945", "label": "Fever"}
    ]
    sample_batch_outputs[1]["records"][0].pop("subject_id", None)
    ctx = extract.ExecutionContext(
        input_file="test.json",
        output_dir=str(tmp_path),
        docker_images=["test:latest"],
        batch_size=1000,
        gpu=False,
        temp_dir=str(tmp_path),
        keep_temp=False,
        continue_on_error=False,
        docker_timeout=3600,
        project_id="test-project",
        docker_image_digests=["test:latest@sha256:abc123"],
    )
    b1 = extract.BatchInfo(1, "batch_000001", "", str(tmp_path / "b1_out.json"), 1, "success")
    b2 = extract.BatchInfo(2, "batch_000002", "", str(tmp_path / "b2_out.json"), 1, "success")
    ctx.batches = [b1, b2]
    for b, out in zip((b1, b2), sample_batch_outputs, strict=False):
        with open(b.output_path, "w") as f:
            json.dump(out, f)
    output_file = tmp_path / "phebee.json"

    with caplog.at_level(logging.WARNING):
        extract.aggregate_results(ctx, str(output_file))

    assert output_file.exists() and len(output_file.read_text().splitlines()) == 1
    assert "Skipped 1 notes without subject_id" in caplog.text


# ============================================================================
# Tool Chaining Tests
# ============================================================================


def test_get_stage_paths_single_stage(tmp_path):
    """Test stage path determination for single-stage pipeline"""
    batch = extract.BatchInfo(
        1,
        "batch_000001",
        str(tmp_path / "batch_000001_input.json"),
        str(tmp_path / "batch_000001_output.json"),
        10,
    )

    input_path, output_path = extract.get_stage_paths(batch, stage_num=1, num_stages=1)

    assert "batch_000001_input.json" in input_path
    assert "batch_000001_output.json" in output_path


def test_build_container_environment_sets_neutral_and_legacy_names(tmp_path):
    """Both neutral and legacy env var pairs are set, with identical in-container paths"""
    env = extract.build_container_environment(
        str(tmp_path / "batch_000001_stage1.json"), str(tmp_path / "batch_000001_output.json")
    )

    assert env == {
        "INPUT_URI": "/data/batch_000001_stage1.json",
        "OUTPUT_URI": "/data/batch_000001_output.json",
        "INPUT_S3_URI": "/data/batch_000001_stage1.json",
        "OUTPUT_S3_URI": "/data/batch_000001_output.json",
    }


def test_get_stage_paths_multi_stage(tmp_path):
    """Test stage path determination for multi-stage pipeline"""
    batch = extract.BatchInfo(
        1,
        "batch_000001",
        str(tmp_path / "batch_000001_input.json"),
        str(tmp_path / "batch_000001_output.json"),
        10,
    )

    # Stage 1
    input_path, output_path = extract.get_stage_paths(batch, stage_num=1, num_stages=3)
    assert "batch_000001_input.json" in input_path
    assert "batch_000001_stage1.json" in output_path

    # Stage 2
    input_path, output_path = extract.get_stage_paths(batch, stage_num=2, num_stages=3)
    assert "batch_000001_stage1.json" in input_path
    assert "batch_000001_stage2.json" in output_path

    # Stage 3 (final)
    input_path, output_path = extract.get_stage_paths(batch, stage_num=3, num_stages=3)
    assert "batch_000001_stage2.json" in input_path
    assert "batch_000001_output.json" in output_path


def test_aggregate_phebee_format_chained_tools(sample_batch_outputs, tmp_path):
    """Test PheBee format with chained tools (multiple digests)"""
    # Add subject_id and annotations
    sample_batch_outputs[0]["records"][0]["subject_id"] = "subj-001"
    sample_batch_outputs[0]["records"][0]["annotations"] = [
        {
            "start": 0,
            "end": 5,
            "text": "fever",
            "term_id": "HP:0001945",
            "label": "Fever",
            "ontology": "hpo",
            "contexts": {"negated": 0, "family": 0, "hypothetical": 0},
        }
    ]

    ctx = extract.ExecutionContext(
        input_file="test.json",
        output_dir=str(tmp_path),
        docker_images=["tool1:latest", "tool2:latest"],
        batch_size=1000,
        gpu=False,
        temp_dir=str(tmp_path),
        keep_temp=False,
        continue_on_error=False,
        docker_timeout=3600,
        flat_format=False,
        project_id="test-project",
        docker_image_digests=["tool1:latest@sha256:abc123", "tool2:latest@sha256:def456"],
    )

    batch1 = extract.BatchInfo(1, "batch_000001", "", str(tmp_path / "b1_out.json"), 1, "success")
    ctx.batches = [batch1]

    with open(batch1.output_path, "w") as f:
        json.dump(sample_batch_outputs[0], f)

    output_file = tmp_path / "phebee_chained.jsonl"
    extract.aggregate_results(ctx, str(output_file))

    # Read output
    with open(output_file) as f:
        lines = f.readlines()

    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["evidence"][0]["evidence_creator_id"] == "tool1:latest+tool2:latest"
    assert rec["evidence"][0]["evidence_creator_name"] == "tool1:latest+tool2:latest"
    assert rec["evidence"][0]["evidence_creator_digest"] == "sha256:abc123+sha256:def456"


# ============================================================================
# CLI Argument Parsing Tests
# ============================================================================


def test_parse_single_image():
    """Test parsing single Docker image with PheBee format (default)"""
    args = [
        "--input",
        "test.json",
        "--output",
        "out/",
        "--docker-images",
        "fasthpocr:latest",
        "--project-id",
        "test-project",
    ]

    with patch("sys.argv", ["extract.py"] + args):
        parsed = extract.parse_arguments()

    assert parsed.input == "test.json"
    assert parsed.output == "out/"
    assert parsed.docker_images == "fasthpocr:latest"
    assert parsed.project_id == "test-project"
    assert parsed.flat_format is False


def test_parse_multiple_images():
    """Test parsing comma-separated Docker images"""
    args = [
        "--input",
        "test.json",
        "--output",
        "out/",
        "--docker-images",
        "fasthpocr:latest,phenocontext:latest",
        "--project-id",
        "test-project",
    ]

    with patch("sys.argv", ["extract.py"] + args):
        parsed = extract.parse_arguments()

    assert parsed.docker_images == "fasthpocr:latest,phenocontext:latest"


def test_parse_default_values():
    """Test default values for optional arguments"""
    args = [
        "--input",
        "test.json",
        "--output",
        "out/",
        "--docker-images",
        "test:latest",
        "--project-id",
        "test-project",
    ]

    with patch("sys.argv", ["extract.py"] + args):
        parsed = extract.parse_arguments()

    assert parsed.batch_size == 1000
    assert parsed.gpu is False
    assert parsed.temp_dir is None
    assert parsed.keep_temp is False
    assert parsed.input_format == "auto"
    assert parsed.flat_format is False
    assert parsed.subject_id_column == "subject_id"
    assert parsed.id_column == "note_id"
    assert parsed.text_column == "text"
    assert parsed.continue_on_error is False
    assert parsed.docker_timeout == 3600


def test_parse_flat_format():
    """Test --flat-format doesn't require PheBee args"""
    args = [
        "--input",
        "test.json",
        "--output",
        "out/",
        "--docker-images",
        "test:latest",
        "--flat-format",
    ]

    with patch("sys.argv", ["extract.py"] + args):
        parsed = extract.parse_arguments()

    assert parsed.flat_format is True
    assert parsed.project_id is None  # Not required with --flat-format


def test_parse_invalid_batch_size():
    """Test error on invalid batch size"""
    args = [
        "--input",
        "test.json",
        "--output",
        "out/",
        "--docker-images",
        "test:latest",
        "--batch-size",
        "0",
    ]

    with patch("sys.argv", ["extract.py"] + args):
        with pytest.raises(SystemExit):
            extract.parse_arguments()


# ============================================================================
# Error Handling Tests
# ============================================================================


def test_batch_processing_error():
    """Test BatchProcessingError exception"""
    batch = extract.BatchInfo(1, "batch_000001", "", "", 10)
    error = extract.BatchProcessingError(batch, "Test error")

    assert "batch_000001" in str(error)
    assert "Test error" in str(error)
    assert error.batch == batch


def test_execution_context_helpers():
    """Test ExecutionContext helper methods"""
    ctx = extract.ExecutionContext(
        input_file="test.json",
        output_dir="out/",
        docker_images=["test:latest"],
        batch_size=1000,
        gpu=False,
        temp_dir="/tmp",
        keep_temp=False,
        continue_on_error=False,
        docker_timeout=3600,
    )

    # Add some batches
    batch1 = extract.BatchInfo(1, "batch_000001", "", "", 10, "success")
    batch2 = extract.BatchInfo(2, "batch_000002", "", "", 10, "failed")
    batch3 = extract.BatchInfo(3, "batch_000003", "", "", 10, "success")
    ctx.batches = [batch1, batch2, batch3]

    # Test helpers
    assert len(ctx.get_successful_batches()) == 2
    assert len(ctx.get_failed_batches()) == 1
    assert ctx.has_failures() is True

    # Test with no failures
    batch2.status = "success"
    assert ctx.has_failures() is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
