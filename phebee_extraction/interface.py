"""
The Docker container contract (see INTERFACE.md).

Everything that reads or writes the files and environment variables a stage container
sees lives here, so the contract has a single home in the code.
"""

import json
import os
from typing import Any

# Where the batch directory is mounted inside a stage container
CONTAINER_DATA_DIR = "/data"


def write_batch_file(records: list[dict[str, Any]], output_path: str) -> None:
    """Write batch records to JSON file per INTERFACE.md spec"""
    batch_data = {"records": records}
    with open(output_path, "w") as f:
        json.dump(batch_data, f, indent=2)


def read_batch_output(output_path: str) -> dict[str, Any]:
    """Read and validate batch output JSON"""
    if not os.path.exists(output_path):
        raise FileNotFoundError(f"Batch output file not found: {output_path}")

    with open(output_path) as f:
        output = json.load(f)

    validate_output_format(output)
    return output


def validate_output_format(output: dict[str, Any]) -> None:
    """Validate output matches INTERFACE.md specification"""
    if "records" not in output:
        raise ValueError("Output missing required 'records' field")
    if "run_metadata" not in output:
        raise ValueError("Output missing required 'run_metadata' field")

    metadata = output["run_metadata"]
    required_metadata_fields = ["run_id", "annotator", "annotator_version", "timestamp"]
    for required_field in required_metadata_fields:
        if required_field not in metadata:
            raise ValueError(f"Output run_metadata missing required field '{required_field}'")


def build_container_environment(input_path: str, output_path: str) -> dict[str, str]:
    """
    Build the environment variables passed to a stage container.

    Sets the neutral names (INPUT_URI/OUTPUT_URI) and the legacy AWS-pipeline names
    (INPUT_S3_URI/OUTPUT_S3_URI) so both older and newer images work. Paths are the
    in-container locations under the CONTAINER_DATA_DIR mount.
    """
    container_input = f"{CONTAINER_DATA_DIR}/{os.path.basename(input_path)}"
    container_output = f"{CONTAINER_DATA_DIR}/{os.path.basename(output_path)}"
    return {
        "INPUT_URI": container_input,
        "OUTPUT_URI": container_output,
        "INPUT_S3_URI": container_input,
        "OUTPUT_S3_URI": container_output,
    }


def merge_original_records(
    original_records: list[dict[str, Any]], output_records: list[dict[str, Any]]
) -> None:
    """
    Merge original input fields into tool output records, in place, matching on note_id.

    Docker containers may not preserve all input fields (subject_id, encounter_id, etc.).
    Fields already present in the output (note_id, text, annotations, error) take precedence.
    """
    original_by_id = {rec["note_id"]: rec for rec in original_records}

    for output_rec in output_records:
        note_id = output_rec.get("note_id")
        if note_id and note_id in original_by_id:
            original = original_by_id[note_id]
            for key, value in original.items():
                if key not in output_rec:
                    output_rec[key] = value


def merge_original_fields(input_path: str, output_path: str) -> None:
    """File-level wrapper for merge_original_records: rewrites output_path in place"""
    with open(input_path) as f:
        input_data = json.load(f)

    with open(output_path) as f:
        output_data = json.load(f)

    merge_original_records(input_data["records"], output_data["records"])

    with open(output_path, "w") as f:
        json.dump(output_data, f, indent=2)
