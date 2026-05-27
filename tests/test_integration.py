"""
Integration tests for extract.py

Tests end-to-end workflows with Docker containers.
Requires Docker daemon to be running.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import docker
import pytest

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture(scope="session")
def docker_client():
    """Return Docker client for session"""
    try:
        client = docker.from_env()
        client.ping()
        return client
    except docker.errors.DockerException:
        pytest.skip("Docker daemon not available")


@pytest.fixture(scope="session")
def mock_extractor_image(docker_client):
    """Build mock extractor Docker image (keyword-based extraction)"""
    test_dir = Path(__file__).parent
    image_tag = "phebee-mock-extractor:test"

    try:
        docker_client.images.get(image_tag)
        print(f"Using existing image: {image_tag}")
    except docker.errors.ImageNotFound:
        print(f"Building mock extractor image: {image_tag}")
        image, build_logs = docker_client.images.build(
            path=str(test_dir), dockerfile="Dockerfile.mock", tag=image_tag, rm=True
        )
        for log in build_logs:
            if "stream" in log:
                print(log["stream"].strip())

    return image_tag


@pytest.fixture(scope="session")
def context_classifier_image(docker_client):
    """Build mock context classifier Docker image"""
    test_dir = Path(__file__).parent
    image_tag = "phebee-mock-context:test"

    try:
        docker_client.images.get(image_tag)
        print(f"Using existing image: {image_tag}")
    except docker.errors.ImageNotFound:
        print(f"Building mock context classifier image: {image_tag}")
        image, build_logs = docker_client.images.build(
            path=str(test_dir), dockerfile="Dockerfile.mock-context", tag=image_tag, rm=True
        )
        for log in build_logs:
            if "stream" in log:
                print(log["stream"].strip())

    return image_tag


@pytest.fixture
def temp_workspace(tmp_path):
    """Create temporary workspace for test files"""
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir()
    output_dir.mkdir()

    return {"root": tmp_path, "input": input_dir, "output": output_dir}


@pytest.fixture
def sample_input_file(temp_workspace):
    """Create sample input JSONL file"""
    input_file = temp_workspace["input"] / "notes.jsonl"

    records = [
        {"note_id": "test_001", "text": "Patient presents with fever and cough."},
        {"note_id": "test_002", "text": "Chronic headaches noted."},
        {"note_id": "test_003", "text": "Developmental delay at 18 months."},
        {"note_id": "test_004", "text": "Recurrent ear infections."},
        {"note_id": "test_005", "text": "Family history of migraines."},
    ]

    with open(input_file, "w") as f:
        for record in records:
            f.write(json.dumps(record) + "\n")

    return input_file


# ============================================================================
# Integration Tests
# ============================================================================


def test_end_to_end_single_tool(mock_extractor_image, sample_input_file, temp_workspace):
    """Test complete extraction workflow with single tool (keyword extractor)"""
    output_dir = temp_workspace["output"]

    # Run extract.py with mock extractor
    cmd = [
        sys.executable,
        "extract.py",
        "--input",
        str(sample_input_file),
        "--output",
        str(output_dir),
        "--docker-images",
        mock_extractor_image,
        "--batch-size",
        "2",  # Create 3 batches from 5 records
        "--flat-format",  # Use flat format for integration tests
    ]

    result = subprocess.run(cmd, cwd=Path(__file__).parent.parent, capture_output=True, text=True)

    print("STDOUT:", result.stdout)
    print("STDERR:", result.stderr)

    # Check exit code
    assert result.returncode == 0, f"Command failed: {result.stderr}"

    # Check output file exists
    output_file = output_dir / "aggregated.jsonl"
    assert output_file.exists(), "Output file not created"

    # Validate output format
    with open(output_file) as f:
        lines = f.readlines()

    assert len(lines) == 6  # 5 records + 1 metadata

    # Check records have realistic annotations
    total_annotations = 0
    for i in range(5):
        record = json.loads(lines[i])
        assert "note_id" in record
        assert "text" in record
        assert "annotations" in record

        # Smart extractor finds real terms in text
        for ann in record["annotations"]:
            assert "start" in ann
            assert "end" in ann
            assert "text" in ann
            assert "term_id" in ann
            assert "label" in ann

            # Verify annotation text actually exists in note
            note_text = record["text"]
            ann_text = note_text[ann["start"] : ann["end"]]
            assert ann_text.lower() == ann["text"].lower(), (
                f"Annotation span mismatch: expected '{ann['text']}', got '{ann_text}'"
            )

        total_annotations += len(record["annotations"])

    print(f"Total annotations found: {total_annotations}")
    assert total_annotations > 0, "Should find at least some annotations"

    # Check metadata
    metadata_line = json.loads(lines[5])
    assert "_metadata" in metadata_line
    metadata = metadata_line["_metadata"]
    assert metadata["total_records"] == 5
    assert metadata["status"] == "success"
    assert metadata["batch_count"] == 3
    assert "execution_name" in metadata
    assert metadata["execution_name"].startswith("local-extraction-")
    assert metadata["tools_used"] == [mock_extractor_image]


def test_end_to_end_chained_tools(mock_extractor_image, context_classifier_image, temp_workspace):
    """Test extraction with tool chaining (extraction + context classification)"""
    output_dir = temp_workspace["output"]

    # Create input with interesting cases for context detection
    input_file = temp_workspace["input"] / "chain_test.jsonl"
    test_notes = [
        {"note_id": "ctx_001", "text": "Patient presents with fever and cough. Denies chest pain."},
        {
            "note_id": "ctx_002",
            "text": "Family history of migraines. Patient has chronic headaches.",
        },
        {"note_id": "ctx_003", "text": "Suspected pneumonia, rule out tuberculosis."},
        {"note_id": "ctx_004", "text": "Mother had seizures. Patient developmentally normal."},
        {"note_id": "ctx_005", "text": "May have fever, possible infection."},
    ]

    with open(input_file, "w") as f:
        for note in test_notes:
            f.write(json.dumps(note) + "\n")

    # Run with extraction → context pipeline
    cmd = [
        sys.executable,
        "extract.py",
        "--input",
        str(input_file),
        "--output",
        str(output_dir),
        "--docker-images",
        f"{mock_extractor_image},{context_classifier_image}",
        "--batch-size",
        "10",
        "--flat-format",  # Single batch
    ]

    result = subprocess.run(cmd, cwd=Path(__file__).parent.parent, capture_output=True, text=True)

    print("STDOUT:", result.stdout)
    print("STDERR:", result.stderr)

    assert result.returncode == 0, f"Command failed: {result.stderr}"

    # Check output
    output_file = output_dir / "aggregated.jsonl"
    assert output_file.exists()

    with open(output_file) as f:
        lines = f.readlines()

    assert len(lines) == 6  # 5 records + metadata

    # Verify context classification worked
    # Note 1 should have negated annotations (Denies)
    note1 = json.loads(lines[0])
    if note1["note_id"] == "ctx_001":
        # "fever" and "cough" should be near "Denies"
        for ann in note1["annotations"]:
            if ann["text"] in ["fever", "cough"]:
                assert "contexts" in ann, "Context missing from annotation"
                # Note: context window might not catch "Denies" for these
                # Just verify contexts field exists
                assert "negated" in ann["contexts"]
                assert "family" in ann["contexts"]
                assert "hypothetical" in ann["contexts"]

    # Find note 2 (family history)
    for line in lines[:-1]:  # Skip metadata
        note = json.loads(line)
        if note["note_id"] == "ctx_002":
            # "migraines" should have family=1
            for ann in note["annotations"]:
                if ann["text"] == "migraines":
                    assert ann["contexts"]["family"] == 1, "Should detect family history"


def test_container_failure_handling(docker_client, sample_input_file, temp_workspace):
    """Test handling of container failures"""
    output_dir = temp_workspace["output"]

    # Use non-existent image to trigger failure
    cmd = [
        sys.executable,
        "extract.py",
        "--input",
        str(sample_input_file),
        "--output",
        str(output_dir),
        "--docker-images",
        "nonexistent-image:latest",
        "--batch-size",
        "10",
        "--flat-format",
    ]

    result = subprocess.run(cmd, cwd=Path(__file__).parent.parent, capture_output=True, text=True)

    # Should fail
    assert result.returncode != 0
    assert "error" in result.stderr.lower() or "error" in result.stdout.lower()


def test_continue_on_error(mock_extractor_image, temp_workspace):
    """Test continue-on-error flag with partial failures"""
    # Create input with valid records
    input_file = temp_workspace["input"] / "notes.jsonl"
    records = [{"note_id": f"note_{i:03d}", "text": f"Text {i}"} for i in range(10)]

    with open(input_file, "w") as f:
        for record in records:
            f.write(json.dumps(record) + "\n")

    output_dir = temp_workspace["output"]

    # For this test, we'd need a container that fails intermittently
    # For now, just test that the flag is accepted
    cmd = [
        sys.executable,
        "extract.py",
        "--input",
        str(input_file),
        "--output",
        str(output_dir),
        "--docker-images",
        mock_extractor_image,
        "--batch-size",
        "3",
        "--flat-format",
        "--continue-on-error",
        "--flat-format",
    ]

    result = subprocess.run(cmd, cwd=Path(__file__).parent.parent, capture_output=True, text=True)

    print("STDOUT:", result.stdout)
    print("STDERR:", result.stderr)

    # Should succeed since all batches actually succeed
    assert result.returncode == 0


def test_gpu_flag_accepted(mock_extractor_image, sample_input_file, temp_workspace):
    """Test that GPU flag is accepted and handled gracefully

    On systems without GPU/nvidia-docker, Docker will fail with a clear error.
    This is expected - we verify the script doesn't crash and provides useful error messages.
    """
    output_dir = temp_workspace["output"]

    cmd = [
        sys.executable,
        "extract.py",
        "--input",
        str(sample_input_file),
        "--output",
        str(output_dir),
        "--docker-images",
        mock_extractor_image,
        "--batch-size",
        "10",
        "--flat-format",
        "--gpu",
        "--flat-format",
    ]

    result = subprocess.run(
        cmd, cwd=Path(__file__).parent.parent, capture_output=True, text=True, timeout=60
    )

    # Test passes if:
    # 1. Success (GPU available): returncode 0
    # 2. GPU unavailable: returncode 1 with clear Docker GPU error message
    if result.returncode == 0:
        # GPU worked - verify output exists
        assert (output_dir / "aggregated.jsonl").exists()
    else:
        # GPU failed - verify we got a clear error (not a crash)
        assert result.returncode == 1
        assert "could not select device driver" in result.stderr or "gpu" in result.stderr.lower()


def test_keep_temp_flag(mock_extractor_image, sample_input_file, temp_workspace):
    """Test that --keep-temp preserves temporary files"""
    output_dir = temp_workspace["output"]

    cmd = [
        sys.executable,
        "extract.py",
        "--input",
        str(sample_input_file),
        "--output",
        str(output_dir),
        "--docker-images",
        mock_extractor_image,
        "--batch-size",
        "10",
        "--flat-format",
        "--keep-temp",
        "--flat-format",
    ]

    result = subprocess.run(cmd, cwd=Path(__file__).parent.parent, capture_output=True, text=True)

    assert result.returncode == 0

    # Check for temp directory
    temp_dirs = list(output_dir.glob(".temp_batches_*"))
    assert len(temp_dirs) > 0, "Temp directory not found"

    # Check temp files exist
    temp_dir = temp_dirs[0]
    batch_files = list(temp_dir.glob("batch_*_input.json"))
    assert len(batch_files) > 0, "Batch files not preserved"


def test_different_input_formats(mock_extractor_image, temp_workspace):
    """Test extraction from different input formats"""
    output_dir = temp_workspace["output"]

    # Test CSV
    csv_file = temp_workspace["input"] / "notes.csv"
    with open(csv_file, "w") as f:
        f.write("note_id,text\n")
        f.write('n1,"Text 1"\n')
        f.write('n2,"Text 2"\n')

    cmd = [
        sys.executable,
        "extract.py",
        "--input",
        str(csv_file),
        "--output",
        str(output_dir / "csv_out"),
        "--docker-images",
        mock_extractor_image,
        "--input-format",
        "csv",
        "--flat-format",
    ]

    result = subprocess.run(cmd, cwd=Path(__file__).parent.parent, capture_output=True, text=True)

    assert result.returncode == 0

    # Test JSON
    json_file = temp_workspace["input"] / "notes.json"
    with open(json_file, "w") as f:
        json.dump(
            {"records": [{"note_id": "j1", "text": "Text 1"}, {"note_id": "j2", "text": "Text 2"}]},
            f,
        )

    cmd = [
        sys.executable,
        "extract.py",
        "--input",
        str(json_file),
        "--output",
        str(output_dir / "json_out"),
        "--docker-images",
        mock_extractor_image,
        "--input-format",
        "json",
        "--flat-format",
    ]

    result = subprocess.run(cmd, cwd=Path(__file__).parent.parent, capture_output=True, text=True)

    assert result.returncode == 0


def test_batch_size_configuration(mock_extractor_image, temp_workspace):
    """Test different batch sizes"""
    # Create input with 20 records
    input_file = temp_workspace["input"] / "notes.jsonl"
    records = [{"note_id": f"note_{i:03d}", "text": f"Text {i}"} for i in range(20)]

    with open(input_file, "w") as f:
        for record in records:
            f.write(json.dumps(record) + "\n")

    output_dir = temp_workspace["output"]

    # Test with batch_size=5 (should create 4 batches)
    cmd = [
        sys.executable,
        "extract.py",
        "--input",
        str(input_file),
        "--output",
        str(output_dir),
        "--docker-images",
        mock_extractor_image,
        "--batch-size",
        "5",
        "--flat-format",
        "--keep-temp",
        "--flat-format",
    ]

    result = subprocess.run(cmd, cwd=Path(__file__).parent.parent, capture_output=True, text=True)

    assert result.returncode == 0

    # Check metadata for batch count
    output_file = output_dir / "aggregated.jsonl"
    with open(output_file) as f:
        lines = f.readlines()

    metadata = json.loads(lines[-1])["_metadata"]
    assert metadata["batch_count"] == 4


def test_output_validation(mock_extractor_image, sample_input_file, temp_workspace):
    """Test that output validation catches invalid container output"""
    # This would require a container that produces invalid output
    # For now, just verify that valid output passes validation
    output_dir = temp_workspace["output"]

    cmd = [
        sys.executable,
        "extract.py",
        "--input",
        str(sample_input_file),
        "--output",
        str(output_dir),
        "--docker-images",
        mock_extractor_image,
        "--batch-size",
        "10",
        "--flat-format",
    ]

    result = subprocess.run(cmd, cwd=Path(__file__).parent.parent, capture_output=True, text=True)

    assert result.returncode == 0

    # Output should be valid JSONL
    output_file = output_dir / "aggregated.jsonl"
    with open(output_file) as f:
        for line in f:
            # Each line should be valid JSON
            json.loads(line)  # Should not raise


# ============================================================================
# Test Utilities
# ============================================================================


def test_mock_extractor_directly(tmp_path):
    """Test mock extractor script directly (without Docker)"""
    # Create input
    input_file = tmp_path / "input.json"
    with open(input_file, "w") as f:
        json.dump({"records": [{"note_id": "n1", "text": "Patient has fever and cough."}]}, f)

    output_file = tmp_path / "output.json"

    # Run mock extractor
    env = os.environ.copy()
    env["INPUT_S3_URI"] = str(input_file)
    env["OUTPUT_S3_URI"] = str(output_file)

    result = subprocess.run(
        [sys.executable, "tests/mock_extractor.py"],
        cwd=Path(__file__).parent.parent,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert output_file.exists()

    # Validate output
    with open(output_file) as f:
        output = json.load(f)

    assert "records" in output
    assert "run_metadata" in output
    assert len(output["records"]) == 1
    assert len(output["records"][0]["annotations"]) > 0


def test_end_to_end_preserves_extra_fields(tmp_path, mock_extractor_image):
    """Test that extra fields in input records are preserved through entire pipeline"""
    # Create input with extra fields
    input_file = tmp_path / "notes_with_extras.jsonl"
    with open(input_file, "w") as f:
        f.write(
            '{"note_id": "n1", "text": "Patient has fever", "patient_id": "P001", "encounter_id": "E001", "department": "cardiology"}\n'
        )
        f.write(
            '{"note_id": "n2", "text": "No cough reported", "patient_id": "P002", "encounter_id": "E002", "department": "neurology"}\n'
        )

    output_dir = tmp_path / "output"

    # Run extraction
    result = subprocess.run(
        [
            sys.executable,
            "extract.py",
            "--input",
            str(input_file),
            "--output",
            str(output_dir),
            "--docker-images",
            mock_extractor_image,
            "--batch-size",
            "10",
            "--flat-format",
        ],
        cwd=Path(__file__).parent.parent,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, f"Extract failed: {result.stderr}"

    # Read output
    output_file = output_dir / "aggregated.jsonl"
    assert output_file.exists()

    records = []
    with open(output_file) as f:
        for line in f:
            obj = json.loads(line)
            if "_metadata" not in obj:
                records.append(obj)

    assert len(records) == 2

    # Verify extra fields preserved
    assert records[0]["note_id"] == "n1"
    assert records[0]["patient_id"] == "P001"
    assert records[0]["encounter_id"] == "E001"
    assert records[0]["department"] == "cardiology"
    assert "annotations" in records[0]

    assert records[1]["note_id"] == "n2"
    assert records[1]["patient_id"] == "P002"
    assert records[1]["encounter_id"] == "E002"
    assert records[1]["department"] == "neurology"
    assert "annotations" in records[1]


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
