#!/usr/bin/env python3
"""
PheBee Local Extraction Orchestrator

Orchestrates Docker containers for clinical note phenotype extraction.
Supports tool chaining, batch processing, and multiple input formats.
"""

__version__ = "1.0.0"

import argparse
import json
import logging
import os
import shutil
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import docker
import pandas as pd
from tqdm import tqdm

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


# ============================================================================
# Data Structures
# ============================================================================


@dataclass
class BatchInfo:
    """Metadata for a single batch"""

    batch_num: int
    batch_id: str  # e.g., "batch_000001"
    input_path: str
    output_path: str
    record_count: int
    status: str = "pending"  # 'pending'|'running'|'success'|'failed'
    error_message: Optional[str] = None
    runtime_ms: Optional[int] = None


@dataclass
class ExecutionContext:
    """Global execution context"""

    input_file: str
    output_dir: str
    docker_images: List[str]
    batch_size: int
    gpu: bool
    temp_dir: str
    keep_temp: bool
    continue_on_error: bool
    docker_timeout: int

    # Output format options
    flat_format: bool = False
    project_id: Optional[str] = None
    subject_id_column: str = "subject_id"
    batch_id: int = 0
    docker_image_digests: List[str] = field(default_factory=list)

    # Runtime state
    docker_client: Optional[docker.DockerClient] = None
    batches: List[BatchInfo] = field(default_factory=list)
    start_time: datetime = field(default_factory=datetime.now)
    total_records: int = 0

    def get_successful_batches(self) -> List[BatchInfo]:
        return [b for b in self.batches if b.status == "success"]

    def get_failed_batches(self) -> List[BatchInfo]:
        return [b for b in self.batches if b.status == "failed"]

    def has_failures(self) -> bool:
        return len(self.get_failed_batches()) > 0


class ProgressTracker:
    """Progress tracking with tqdm"""

    def __init__(self, total_batches: int, num_stages: int):
        self.total_steps = total_batches * num_stages
        self.pbar = tqdm(total=self.total_steps, unit="step", desc="Processing")
        self.num_stages = num_stages

    def update(self, batch: BatchInfo, stage: int, stage_name: str) -> None:
        """Update progress bar with batch and stage status"""
        status_symbol = "✓" if batch.status != "failed" else "✗"
        stage_desc = (
            f"[{stage}/{self.num_stages}] {stage_name}" if self.num_stages > 1 else stage_name
        )
        self.pbar.set_postfix_str(f"{status_symbol} {batch.batch_id} {stage_desc}")
        self.pbar.update(1)

    def close(self) -> None:
        self.pbar.close()


# ============================================================================
# Exception Classes
# ============================================================================


class ExtractionError(Exception):
    """Base exception for extraction errors"""

    pass


class BatchProcessingError(ExtractionError):
    """Exception raised when batch processing fails"""

    def __init__(self, batch: BatchInfo, message: str):
        self.batch = batch
        self.message = message
        super().__init__(f"Batch {batch.batch_id} failed: {message}")


class DockerError(ExtractionError):
    """Exception raised for Docker-related errors"""

    pass


# ============================================================================
# CLI Argument Parsing
# ============================================================================


def parse_arguments() -> argparse.Namespace:
    """Parse and validate command-line arguments"""
    parser = argparse.ArgumentParser(
        description="PheBee Local Extraction Orchestrator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Basic extraction (PheBee format - default)
  %(prog)s --input notes.jsonl --output results/ \\
    --docker-images fasthpocr:latest \\
    --project-id my-project --evidence-creator-id fasthpocr-local

  # Tool chaining (extraction + context)
  %(prog)s --input notes.jsonl --output results/ \\
    --docker-images fasthpocr:latest,phenocontext:latest \\
    --project-id my-project --evidence-creator-id fasthpocr-local

  # Flat format output (note-centric, no PheBee fields required)
  %(prog)s --input notes.jsonl --output results/ \\
    --docker-images fasthpocr:latest --flat-format

  # GPU-enabled extraction
  %(prog)s --input notes.parquet --output results/ \\
    --docker-images phenobert:latest --gpu --batch-size 500 \\
    --project-id my-project --evidence-creator-id phenobert-local
        """,
    )

    # Required arguments
    parser.add_argument("--input", required=True, help="Input file (JSON/JSONL/Parquet/CSV)")
    parser.add_argument("--output", required=True, help="Output directory")
    parser.add_argument(
        "--docker-images", required=True, help="Comma-separated list of Docker images for pipeline"
    )

    # Optional arguments
    parser.add_argument(
        "--batch-size", type=int, default=1000, help="Number of notes per batch (default: 1000)"
    )
    parser.add_argument(
        "--gpu", action="store_true", help="Enable GPU support (adds --gpus all flag)"
    )
    parser.add_argument(
        "--temp-dir",
        type=str,
        default=None,
        help="Temporary directory for batch files (default: {output}/.temp_batches_{timestamp})",
    )
    parser.add_argument(
        "--keep-temp", action="store_true", help="Keep temporary files after completion"
    )
    parser.add_argument(
        "--input-format",
        choices=["auto", "json", "jsonl", "parquet", "csv"],
        default="auto",
        help="Input file format (default: auto-detect)",
    )
    parser.add_argument(
        "--id-column",
        default="note_id",
        help="Column name for note IDs in CSV/Parquet (default: note_id)",
    )
    parser.add_argument(
        "--text-column",
        default="text",
        help="Column name for note text in CSV/Parquet (default: text)",
    )
    parser.add_argument(
        "--continue-on-error", action="store_true", help="Continue processing if a batch fails"
    )
    parser.add_argument(
        "--docker-timeout",
        type=int,
        default=3600,
        help="Maximum seconds per batch stage (default: 3600, 0=no timeout)",
    )

    # Output format options
    parser.add_argument(
        "--flat-format",
        action="store_true",
        help="Output flat note-centric format instead of PheBee format (default: PheBee format)",
    )
    parser.add_argument(
        "--project-id",
        help="PheBee project identifier (required unless --flat-format specified)",
    )
    parser.add_argument(
        "--subject-id-column",
        default="subject_id",
        help="Input column name for subject ID (default: subject_id)",
    )
    parser.add_argument(
        "--batch-id",
        type=int,
        default=0,
        help="PheBee batch identifier (default: 0)",
    )

    args = parser.parse_args()

    # Validate arguments
    if args.batch_size < 1:
        parser.error("--batch-size must be >= 1")

    if args.docker_timeout < 0:
        parser.error("--docker-timeout must be >= 0")

    # PheBee format requires project-id
    if not args.flat_format and not args.project_id:
        parser.error("--project-id is required unless --flat-format is specified")

    return args


# ============================================================================
# Input Data Loading
# ============================================================================


def detect_input_format(file_path: str) -> str:
    """Auto-detect input file format from extension"""
    ext = Path(file_path).suffix.lower()

    format_map = {".json": "json", ".jsonl": "jsonl", ".parquet": "parquet", ".csv": "csv"}

    if ext not in format_map:
        raise ValueError(
            f"Unsupported file extension: {ext}. Supported: .json, .jsonl, .parquet, .csv"
        )

    return format_map[ext]


def load_input_data(
    file_path: str, format: str = "auto", id_column: str = "note_id", text_column: str = "text"
) -> List[Dict[str, Any]]:
    """
    Load clinical notes from various file formats

    Returns list of note dictionaries with 'note_id' and 'text' keys
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Input file not found: {file_path}")

    # Auto-detect format
    if format == "auto":
        format = detect_input_format(file_path)

    logger.info(f"Loading input data from {file_path} (format: {format})")

    records = []

    if format == "json":
        with open(file_path) as f:
            data = json.load(f)
            if isinstance(data, dict) and "records" in data:
                records = data["records"]
            elif isinstance(data, list):
                records = data
            else:
                raise ValueError("JSON file must be an array or object with 'records' key")

    elif format == "jsonl":
        with open(file_path) as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))

    elif format == "parquet":
        df = pd.read_parquet(file_path)
        if id_column not in df.columns:
            raise ValueError(f"Column '{id_column}' not found in Parquet file")
        if text_column not in df.columns:
            raise ValueError(f"Column '{text_column}' not found in Parquet file")

        # Convert ID columns to strings (they may be numeric in Parquet)
        df[id_column] = df[id_column].astype(str)

        # Convert to records, renaming columns if needed
        df = df.rename(columns={id_column: "note_id", text_column: "text"})
        records = df.to_dict("records")

    elif format == "csv":
        df = pd.read_csv(file_path)
        if id_column not in df.columns:
            raise ValueError(f"Column '{id_column}' not found in CSV file")
        if text_column not in df.columns:
            raise ValueError(f"Column '{text_column}' not found in CSV file")

        # Convert ID columns to strings (they may be numeric in CSV)
        df[id_column] = df[id_column].astype(str)

        # Convert to records, renaming columns if needed
        df = df.rename(columns={id_column: "note_id", text_column: "text"})
        records = df.to_dict("records")

    else:
        raise ValueError(f"Unsupported format: {format}")

    # Convert common ID fields to strings (they may be numeric in input data)
    # This ensures compatibility with Docker container JSON schema validation
    id_fields = [
        "note_id",
        "subject_id",
        "pat_id",
        "mrn",
        "encounter_id",
        "enc_id",
        "note_csn_id",
        "raw_note_id",
    ]
    for record in records:
        for field in id_fields:
            if field in record and record[field] is not None:
                record[field] = str(record[field])

    logger.info(f"Loaded {len(records)} records")
    return records


def validate_input_records(records: List[Dict[str, Any]]) -> None:
    """Validate that records have required fields"""
    if not records:
        raise ValueError("No records found in input file")

    # Check required fields
    note_ids = set()
    for i, record in enumerate(records):
        if "note_id" not in record:
            raise ValueError(f"Record {i} missing required field 'note_id'")
        if "text" not in record:
            raise ValueError(f"Record {i} missing required field 'text'")

        # Check for duplicates
        note_id = record["note_id"]
        if note_id in note_ids:
            raise ValueError(f"Duplicate note_id found: {note_id}")
        note_ids.add(note_id)

    logger.info("Input validation passed")


# ============================================================================
# Batch Management
# ============================================================================


def create_batches(
    records: List[Dict[str, Any]], batch_size: int, temp_dir: str
) -> List[BatchInfo]:
    """Split records into batches and write batch files"""
    os.makedirs(temp_dir, exist_ok=True)

    batches = []
    for i in range(0, len(records), batch_size):
        batch_num = len(batches) + 1
        batch_id = f"batch_{batch_num:06d}"
        batch_records = records[i : i + batch_size]

        input_path = os.path.join(temp_dir, f"{batch_id}_input.json")
        output_path = os.path.join(temp_dir, f"{batch_id}_output.json")

        # Write batch input file
        write_batch_file(batch_records, input_path)

        batch = BatchInfo(
            batch_num=batch_num,
            batch_id=batch_id,
            input_path=input_path,
            output_path=output_path,
            record_count=len(batch_records),
        )
        batches.append(batch)

    logger.info(f"Created {len(batches)} batches in {temp_dir}")
    return batches


def write_batch_file(records: List[Dict[str, Any]], output_path: str) -> None:
    """Write batch records to JSON file per INTERFACE.md spec"""
    batch_data = {"records": records}
    with open(output_path, "w") as f:
        json.dump(batch_data, f, indent=2)


# ============================================================================
# Docker Operations
# ============================================================================


def initialize_docker_client() -> docker.DockerClient:
    """Initialize and validate Docker client connection"""
    try:
        client = docker.from_env()
        client.ping()
        logger.info("Docker client initialized successfully")
        return client
    except docker.errors.DockerException as e:
        raise DockerError(f"Failed to connect to Docker daemon: {e}") from e


def validate_docker_images(client: docker.DockerClient, images: List[str]) -> List[str]:
    """
    Verify Docker images exist locally or can be pulled

    Returns list of image references with digests (image@sha256:...)
    """
    image_digests = []

    for image in images:
        try:
            img = client.images.get(image)
            logger.info(f"Docker image found: {image}")
        except docker.errors.ImageNotFound:
            logger.warning(f"Docker image not found locally: {image}")
            logger.info(f"Attempting to pull {image}...")
            try:
                img = client.images.pull(image)
                logger.info(f"Successfully pulled {image}")
            except docker.errors.DockerException as e:
                raise DockerError(f"Failed to pull Docker image {image}: {e}") from e

        # Get the image digest (RepoDigests is a list, take first if available)
        if img.attrs.get("RepoDigests"):
            # Use the full digest reference from registry
            digest_ref = img.attrs["RepoDigests"][0]
            image_digests.append(digest_ref)
        elif img.id:
            # Fall back to local image ID if no registry digest
            # Format: image@sha256:...
            image_id = img.id.replace("sha256:", "")
            digest_ref = f"{image}@sha256:{image_id}"
            image_digests.append(digest_ref)
        else:
            # Should never happen, but fallback to original name
            logger.warning(f"Could not determine digest for {image}, using name only")
            image_digests.append(image)

    return image_digests


def get_stage_paths(batch: BatchInfo, stage_num: int, num_stages: int) -> Tuple[str, str]:
    """Determine input/output paths for a pipeline stage"""
    temp_dir = os.path.dirname(batch.input_path)

    if stage_num == 1:
        # First stage: use original input
        input_file = f"{batch.batch_id}_input.json"
    else:
        # Subsequent stages: use previous stage output
        input_file = f"{batch.batch_id}_stage{stage_num - 1}.json"

    if stage_num == num_stages:
        # Final stage: use final output name
        output_file = f"{batch.batch_id}_output.json"
    else:
        # Intermediate stages: use stage-numbered output
        output_file = f"{batch.batch_id}_stage{stage_num}.json"

    return (os.path.join(temp_dir, input_file), os.path.join(temp_dir, output_file))


def run_single_stage(
    client: docker.DockerClient,
    docker_image: str,
    input_path: str,
    output_path: str,
    gpu: bool,
    timeout: int,
) -> Dict[str, Any]:
    """
    Run Docker container for a single pipeline stage

    Returns dict with: status, exit_code, logs, runtime_ms, error
    """
    start_time = time.time()

    # Prepare volume mounts
    temp_dir = os.path.dirname(input_path)
    volumes = {temp_dir: {"bind": "/data", "mode": "rw"}}

    # Prepare environment variables
    input_filename = os.path.basename(input_path)
    output_filename = os.path.basename(output_path)
    environment = {
        "INPUT_S3_URI": f"/data/{input_filename}",
        "OUTPUT_S3_URI": f"/data/{output_filename}",
    }

    # Prepare device requests for GPU
    device_requests = None
    if gpu:
        device_requests = [docker.types.DeviceRequest(count=-1, capabilities=[["gpu"]])]

    try:
        # Run container
        container = client.containers.run(
            docker_image,
            volumes=volumes,
            environment=environment,
            device_requests=device_requests,
            detach=True,
            remove=False,  # Don't auto-remove so we can inspect exit code
        )

        # Wait for completion
        result = container.wait(timeout=timeout if timeout > 0 else None)
        exit_code = result["StatusCode"]

        # Collect logs
        logs = container.logs().decode("utf-8", errors="replace")

        # Remove container
        container.remove()

        runtime_ms = int((time.time() - start_time) * 1000)

        if exit_code == 0:
            return {
                "status": "success",
                "exit_code": exit_code,
                "logs": logs,
                "runtime_ms": runtime_ms,
                "error": None,
            }
        else:
            return {
                "status": "failed",
                "exit_code": exit_code,
                "logs": logs,
                "runtime_ms": runtime_ms,
                "error": f"Container exited with code {exit_code}",
            }

    except Exception as e:
        runtime_ms = int((time.time() - start_time) * 1000)
        return {
            "status": "failed",
            "exit_code": -1,
            "logs": "",
            "runtime_ms": runtime_ms,
            "error": str(e),
        }


def merge_original_fields(input_path: str, output_path: str) -> None:
    """
    Merge original input fields back into output records.

    Docker containers may not preserve all input fields (subject_id, encounter_id, etc.).
    This function merges them back based on note_id matching.
    """
    # Read original input
    with open(input_path) as f:
        input_data = json.load(f)

    # Read container output
    with open(output_path) as f:
        output_data = json.load(f)

    # Create lookup of original records by note_id
    original_by_id = {rec["note_id"]: rec for rec in input_data["records"]}

    # Merge fields back into output records
    for output_rec in output_data["records"]:
        note_id = output_rec.get("note_id")
        if note_id and note_id in original_by_id:
            original = original_by_id[note_id]
            # Merge all original fields except those already in output
            # (output takes precedence for note_id, text, annotations, error)
            for key, value in original.items():
                if key not in output_rec:
                    output_rec[key] = value

    # Write merged output back
    with open(output_path, "w") as f:
        json.dump(output_data, f, indent=2)


def run_batch_pipeline(ctx: ExecutionContext, batch: BatchInfo, progress: ProgressTracker) -> None:
    """Execute all pipeline stages for a single batch"""
    batch.status = "running"
    batch_start_time = time.time()

    num_stages = len(ctx.docker_images)

    for stage_num, docker_image in enumerate(ctx.docker_images, start=1):
        input_path, output_path = get_stage_paths(batch, stage_num, num_stages)

        logger.info(f"Processing {batch.batch_id} stage {stage_num}/{num_stages}: {docker_image}")

        result = run_single_stage(
            ctx.docker_client, docker_image, input_path, output_path, ctx.gpu, ctx.docker_timeout
        )

        progress.update(batch, stage_num, docker_image.split(":")[0])

        if result["status"] == "failed":
            batch.status = "failed"
            batch.error_message = result["error"]
            logger.error(f"Batch {batch.batch_id} stage {stage_num} failed: {result['error']}")
            if result["logs"]:
                logger.error(f"Container logs:\n{result['logs']}")

            if not ctx.continue_on_error:
                raise BatchProcessingError(batch, result["error"])
            return

    # All stages succeeded - merge original fields back into output
    merge_original_fields(batch.input_path, output_path)

    batch.status = "success"
    batch.runtime_ms = int((time.time() - batch_start_time) * 1000)
    logger.info(f"Batch {batch.batch_id} completed successfully in {batch.runtime_ms}ms")


# ============================================================================
# Output Validation and Aggregation
# ============================================================================


def read_batch_output(output_path: str) -> Dict[str, Any]:
    """Read and validate batch output JSON"""
    if not os.path.exists(output_path):
        raise FileNotFoundError(f"Batch output file not found: {output_path}")

    with open(output_path) as f:
        output = json.load(f)

    validate_output_format(output)
    return output


def validate_output_format(output: Dict[str, Any]) -> None:
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


def merge_run_metadata(
    metadata_list: List[Dict[str, Any]], tools_used: List[str]
) -> Dict[str, Any]:
    """
    Merge run_metadata from multiple batches into AWS pipeline-style metadata

    Returns metadata compatible with phebee-extraction-pipeline format
    """
    if not metadata_list:
        raise ValueError("No metadata to merge")

    # Generate execution name (timestamp-based)
    execution_name = f"local-extraction-{datetime.now().strftime('%Y%m%d-%H%M%S')}"

    # Find earliest timestamp
    timestamps = [m["timestamp"] for m in metadata_list]
    earliest_timestamp = min(timestamps)

    # Create aggregate metadata (AWS pipeline format)
    aggregate_metadata = {
        "execution_name": execution_name,
        "tools_used": tools_used,
        "processing_timestamp": earliest_timestamp,
        "batch_count": len(metadata_list),
        "status": "success",
    }

    return aggregate_metadata


def term_id_to_iri(term_id: str) -> str:
    """
    Convert term ID to IRI format

    Examples:
        HP:0001250 -> http://purl.obolibrary.org/obo/HP_0001250
        MONDO:0005015 -> http://purl.obolibrary.org/obo/MONDO_0005015
    """
    if term_id.startswith("http"):
        return term_id  # Already an IRI

    # Replace colon with underscore for OBO format
    obo_id = term_id.replace(":", "_")
    return f"http://purl.obolibrary.org/obo/{obo_id}"


def record_to_phebee_format(record: Dict[str, Any], ctx: ExecutionContext) -> List[Dict[str, Any]]:
    """
    Convert a single extraction record to PheBee format records.
    Returns one PheBee record per annotation (streaming approach).
    Note: row_num and batch_id will be set by the caller.
    """
    phebee_records = []

    # Get subject_id
    subject_id = record.get(ctx.subject_id_column)
    if not subject_id:
        logger.warning(
            f"Note {record.get('note_id', 'unknown')} missing {ctx.subject_id_column}, skipping"
        )
        return []

    # Get note metadata
    note_id = record["note_id"]
    encounter_id = record.get("encounter_id", note_id)
    note_timestamp = record.get("note_timestamp")
    if not note_timestamp:
        note_timestamp = datetime.now().strftime("%Y-%m-%d")

    # Process each annotation
    annotations = record.get("annotations", [])
    if not annotations:
        return []

    for annotation in annotations:
        term_id = annotation.get("term_id")
        if not term_id:
            logger.warning(f"Annotation in note {note_id} missing term_id, skipping")
            continue

        # Get span positions (support multiple field name variants)
        # Use explicit None checks to handle start=0 correctly
        span_start = annotation.get("start")
        if span_start is None:
            span_start = annotation.get("span_start")
        if span_start is None:
            span_start = annotation.get("begin")

        span_end = annotation.get("end")
        if span_end is None:
            span_end = annotation.get("span_end")

        if span_start is None or span_end is None:
            logger.warning(
                f"Annotation in note {note_id} missing span positions (tried 'start'/'end', "
                f"'span_start'/'span_end', 'begin'), skipping. Available fields: {list(annotation.keys())}"
            )
            continue

        # Convert contexts integers to floats
        contexts = annotation.get("contexts", {})
        contexts_floats = {
            "negated": float(contexts.get("negated", 0)),
            "family": float(contexts.get("family", 0)),
            "hypothetical": float(contexts.get("hypothetical", 0)),
        }

        # Build evidence creator from pipeline
        # Split image references into name and digest parts
        creator_names = []
        creator_digests = []
        for image_ref in ctx.docker_image_digests:
            if "@" in image_ref:
                name, digest = image_ref.split("@", 1)
                creator_names.append(name)
                creator_digests.append(digest)
            else:
                # No digest available
                creator_names.append(image_ref)
                creator_digests.append(None)

        evidence_creator_id = "+".join(creator_names)
        evidence_creator_name = evidence_creator_id  # Same as creator_id
        # Only include digest if all images have digests
        evidence_creator_digest = "+".join(creator_digests) if all(creator_digests) else None

        # Create evidence item
        evidence_item = {
            "type": "clinical_note",
            "clinical_note_id": note_id,
            "encounter_id": encounter_id,
            "evidence_creator_id": evidence_creator_id,
            "evidence_creator_type": "automated",
            "evidence_creator_name": evidence_creator_name,
            "evidence_creator_digest": evidence_creator_digest,
            "note_timestamp": note_timestamp,
            "note_type": record.get("note_type"),
            "provider_type": record.get("provider_type"),
            "author_specialty": record.get("author_specialty"),
            "span_start": int(span_start),
            "span_end": int(span_end),
            "contexts": contexts_floats,
        }

        # Create PheBee record with single evidence item (row_num will be set by caller)
        phebee_record = {
            "project_id": ctx.project_id,
            "project_subject_id": subject_id,
            "term_iri": term_id_to_iri(term_id),
            "evidence": [evidence_item],
            "batch_id": ctx.batch_id,
        }

        phebee_records.append(phebee_record)

    return phebee_records


def aggregate_results(ctx: ExecutionContext, output_file: str) -> None:
    """Aggregate all batch results into single JSONL output"""
    logger.info("Aggregating batch results...")

    successful_batches = ctx.get_successful_batches()
    if not successful_batches:
        raise ExtractionError("No successful batches to aggregate")

    # Read all batch outputs
    batch_outputs = []
    for batch in successful_batches:
        output = read_batch_output(batch.output_path)
        batch_outputs.append(output)

    if ctx.flat_format:
        # Flat format: note-centric output with metadata
        total_records = 0
        with open(output_file, "w") as f:
            # Write records (one per line)
            for output in batch_outputs:
                for record in output["records"]:
                    f.write(json.dumps(record) + "\n")
                    total_records += 1

            # Merge metadata (AWS pipeline format)
            metadata_list = [out["run_metadata"] for out in batch_outputs]
            aggregate_metadata = merge_run_metadata(metadata_list, ctx.docker_images)
            aggregate_metadata["total_records"] = total_records

            # Add failure info if any
            failed_batches = ctx.get_failed_batches()
            if failed_batches:
                aggregate_metadata["status"] = "partial"
                aggregate_metadata["failed_batches"] = [b.batch_num for b in failed_batches]
                error_messages = [f"Batch {b.batch_num}: {b.error_message}" for b in failed_batches]
                aggregate_metadata["error_message"] = "; ".join(error_messages)
            else:
                aggregate_metadata["failed_batches"] = []
                aggregate_metadata["error_message"] = None

            # Write metadata as final line
            metadata_record = {"_metadata": aggregate_metadata}
            f.write(json.dumps(metadata_record) + "\n")

        logger.info(f"Aggregated {total_records} records to {output_file}")

    else:
        # PheBee format: stream transform to subject+term-centric
        total_phebee_records = 0
        skipped_no_subject = 0
        skipped_no_annotations = 0
        row_num = 1

        with open(output_file, "w") as f:
            for output in batch_outputs:
                for record in output["records"]:
                    # Skip records with errors
                    if record.get("error"):
                        continue

                    # Check for subject_id
                    if not record.get(ctx.subject_id_column):
                        skipped_no_subject += 1
                        continue

                    # Check for annotations
                    if not record.get("annotations"):
                        skipped_no_annotations += 1
                        continue

                    # Transform to PheBee format (one record per annotation)
                    phebee_records = record_to_phebee_format(record, ctx)
                    for phebee_record in phebee_records:
                        phebee_record["row_num"] = row_num
                        f.write(json.dumps(phebee_record) + "\n")
                        total_phebee_records += 1
                        row_num += 1

        logger.info(f"Wrote {total_phebee_records} PheBee records to {output_file}")
        if skipped_no_subject > 0:
            logger.warning(f"Skipped {skipped_no_subject} notes without {ctx.subject_id_column}")
        if skipped_no_annotations > 0:
            logger.info(f"Skipped {skipped_no_annotations} notes with no annotations")


# ============================================================================
# Cleanup and Reporting
# ============================================================================


def cleanup_temp_files(temp_dir: str, keep_temp: bool = False) -> None:
    """Remove temporary batch files"""
    if keep_temp:
        logger.info(f"Keeping temporary files in: {temp_dir}")
        return

    try:
        shutil.rmtree(temp_dir)
        logger.info(f"Cleaned up temporary files from: {temp_dir}")
    except OSError as e:
        logger.warning(f"Failed to cleanup temp files: {e}")


def generate_summary_report(ctx: ExecutionContext, output_file: str) -> str:
    """Generate execution summary for display"""
    elapsed_time = (datetime.now() - ctx.start_time).total_seconds()

    successful = len(ctx.get_successful_batches())
    failed = len(ctx.get_failed_batches())
    total = len(ctx.batches)

    summary = f"""
{"=" * 60}
EXTRACTION SUMMARY
{"=" * 60}
Total records:      {ctx.total_records}
Total batches:      {total}
Successful batches: {successful}
Failed batches:     {failed}
Total runtime:      {elapsed_time:.2f}s
Output file:        {output_file}
"""

    if failed > 0:
        summary += "\nFailed batches:\n"
        for batch in ctx.get_failed_batches():
            summary += f"  - {batch.batch_id}: {batch.error_message}\n"

    summary += "=" * 60

    return summary


# ============================================================================
# Main Execution
# ============================================================================


def main() -> int:
    """Main entry point"""
    try:
        # Parse arguments
        args = parse_arguments()

        # Setup output directory
        output_dir = args.output
        os.makedirs(output_dir, exist_ok=True)

        # Setup temp directory
        if args.temp_dir:
            temp_dir = args.temp_dir
        else:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            temp_dir = os.path.join(output_dir, f".temp_batches_{timestamp}")

        # Parse docker images
        docker_images = [img.strip() for img in args.docker_images.split(",")]

        # Create execution context
        ctx = ExecutionContext(
            input_file=args.input,
            output_dir=output_dir,
            docker_images=docker_images,
            batch_size=args.batch_size,
            gpu=args.gpu,
            temp_dir=temp_dir,
            keep_temp=args.keep_temp,
            continue_on_error=args.continue_on_error,
            docker_timeout=args.docker_timeout,
            flat_format=args.flat_format,
            project_id=args.project_id,
            subject_id_column=args.subject_id_column,
            batch_id=args.batch_id,
        )

        # Initialize Docker
        logger.info("Initializing Docker client...")
        ctx.docker_client = initialize_docker_client()
        ctx.docker_image_digests = validate_docker_images(ctx.docker_client, ctx.docker_images)

        # Load input data
        records = load_input_data(
            args.input,
            format=args.input_format,
            id_column=args.id_column,
            text_column=args.text_column,
        )
        validate_input_records(records)
        ctx.total_records = len(records)

        # Create batches
        ctx.batches = create_batches(records, args.batch_size, temp_dir)

        # Process batches
        logger.info(
            f"Processing {len(ctx.batches)} batches with {len(docker_images)}-stage pipeline..."
        )
        progress = ProgressTracker(len(ctx.batches), len(docker_images))

        for batch in ctx.batches:
            try:
                run_batch_pipeline(ctx, batch, progress)
            except BatchProcessingError as e:
                logger.error(str(e))
                if not args.continue_on_error:
                    progress.close()
                    cleanup_temp_files(temp_dir, args.keep_temp)
                    return 1

        progress.close()

        # Aggregate results
        output_file = os.path.join(output_dir, "aggregated.jsonl")
        aggregate_results(ctx, output_file)

        # Cleanup
        cleanup_temp_files(temp_dir, args.keep_temp)

        # Generate and display summary
        summary = generate_summary_report(ctx, output_file)
        print(summary)

        # Exit code
        return 1 if ctx.has_failures() else 0

    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
