"""
PheBee Extraction - lightweight local phenotype extraction orchestrator.

The package is organised around the pipeline stages so each can be reused on its own:

    loading        read clinical notes from JSON/JSONL/CSV/Parquet
    batching       split notes into batch files and lay out per-stage paths
    interface      the Docker container contract (INTERFACE.md): files and env vars
    runner         the StageRunner protocol: how one stage gets executed
    docker_runner  StageRunner implementation on the local Docker daemon
    transform      convert tool output into PheBee or flat JSONL
    pipeline       local end-to-end orchestration
    cli            command-line entry point (run / split / transform)
"""

__version__ = "1.0.0"

from phebee_extraction.batching import create_batches, get_stage_paths, write_manifest
from phebee_extraction.docker_runner import (
    DockerRunner,
    initialize_docker_client,
    run_single_stage,
    validate_docker_images,
)
from phebee_extraction.interface import (
    CONTAINER_DATA_DIR,
    build_container_environment,
    merge_original_fields,
    merge_original_records,
    read_batch_output,
    validate_output_format,
    write_batch_file,
)
from phebee_extraction.loading import detect_input_format, load_input_data, validate_input_records
from phebee_extraction.models import BatchInfo, BatchProcessingError, DockerError, ExtractionError
from phebee_extraction.pipeline import (
    ExecutionContext,
    ProgressTracker,
    aggregate_results,
    cleanup_temp_files,
    generate_summary_report,
    run_batch_pipeline,
    temp_dir_for,
)
from phebee_extraction.runner import StageResult, StageRunner
from phebee_extraction.transform import (
    PheBeeConfig,
    PheBeeWriteStats,
    evidence_creator_fields,
    merge_run_metadata,
    record_to_phebee_format,
    term_id_to_iri,
    write_flat_jsonl,
    write_phebee_jsonl,
    write_phebee_records,
)

__all__ = [
    "__version__",
    # models
    "BatchInfo",
    "BatchProcessingError",
    "DockerError",
    "ExtractionError",
    # loading
    "detect_input_format",
    "load_input_data",
    "validate_input_records",
    # batching
    "create_batches",
    "get_stage_paths",
    "write_manifest",
    # interface
    "CONTAINER_DATA_DIR",
    "build_container_environment",
    "merge_original_fields",
    "merge_original_records",
    "read_batch_output",
    "validate_output_format",
    "write_batch_file",
    # runner
    "StageResult",
    "StageRunner",
    # docker_runner
    "DockerRunner",
    "initialize_docker_client",
    "run_single_stage",
    "validate_docker_images",
    # transform
    "PheBeeConfig",
    "PheBeeWriteStats",
    "evidence_creator_fields",
    "merge_run_metadata",
    "record_to_phebee_format",
    "term_id_to_iri",
    "write_flat_jsonl",
    "write_phebee_jsonl",
    "write_phebee_records",
    # pipeline
    "ExecutionContext",
    "ProgressTracker",
    "aggregate_results",
    "cleanup_temp_files",
    "generate_summary_report",
    "run_batch_pipeline",
    "temp_dir_for",
]
