"""Local end-to-end orchestration: run every batch through every stage, then aggregate."""

import logging
import os
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime

from tqdm import tqdm

from phebee_extraction.batching import get_stage_paths
from phebee_extraction.interface import merge_original_fields, read_batch_output
from phebee_extraction.models import BatchInfo, BatchProcessingError, ExtractionError
from phebee_extraction.runner import StageRunner
from phebee_extraction.transform import PheBeeConfig, write_flat_jsonl, write_phebee_jsonl

logger = logging.getLogger(__name__)

# Name of the aggregated output written by `run`. It is JSON Lines, but PheBee's bulk import
# only reads files ending in .json (it globs the upload prefix with *.json), so the default
# name is import-ready as is.
OUTPUT_FILENAME = "aggregated.json"


@dataclass
class ExecutionContext:
    """Global execution context"""

    input_file: str
    output_dir: str
    docker_images: list[str]
    batch_size: int
    gpu: bool
    temp_dir: str
    keep_temp: bool
    continue_on_error: bool
    docker_timeout: int

    # Output format options
    flat_format: bool = False
    project_id: str | None = None
    subject_id_column: str = "subject_id"
    docker_image_digests: list[str] = field(default_factory=list)

    # Runtime state
    runner: StageRunner | None = None
    batches: list[BatchInfo] = field(default_factory=list)
    start_time: datetime = field(default_factory=datetime.now)
    total_records: int = 0

    def get_successful_batches(self) -> list[BatchInfo]:
        return [b for b in self.batches if b.status == "success"]

    def get_failed_batches(self) -> list[BatchInfo]:
        return [b for b in self.batches if b.status == "failed"]

    def has_failures(self) -> bool:
        return len(self.get_failed_batches()) > 0

    def phebee_config(self) -> PheBeeConfig:
        """Build the transform config for PheBee-format output"""
        if self.project_id is None:
            raise ExtractionError("project_id is required for PheBee format output")
        return PheBeeConfig(
            project_id=self.project_id,
            subject_id_column=self.subject_id_column,
            image_refs=self.docker_image_digests,
        )


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


def run_batch_pipeline(ctx: ExecutionContext, batch: BatchInfo, progress: ProgressTracker) -> None:
    """Execute all pipeline stages for a single batch"""
    if ctx.runner is None:
        raise ExtractionError("ExecutionContext.runner is not set")

    batch.status = "running"
    batch_start_time = time.time()

    num_stages = len(ctx.docker_images)

    for stage_num, docker_image in enumerate(ctx.docker_images, start=1):
        input_path, output_path = get_stage_paths(batch, stage_num, num_stages)

        logger.info(f"Processing {batch.batch_id} stage {stage_num}/{num_stages}: {docker_image}")

        result = ctx.runner.run_stage(docker_image, input_path, output_path)

        # A stage only counts as successful if it left its output behind for the next step
        if result.ok and not os.path.exists(output_path):
            result.status = "failed"
            result.error = f"Stage reported success but did not write {output_path}"

        progress.update(batch, stage_num, docker_image.split(":")[0])

        if not result.ok:
            batch.status = "failed"
            batch.error_message = result.error
            logger.error(f"Batch {batch.batch_id} stage {stage_num} failed: {result.error}")
            if result.logs:
                logger.error(f"Container logs:\n{result.logs}")

            if not ctx.continue_on_error:
                raise BatchProcessingError(batch, result.error or "unknown error")
            return

    # All stages succeeded - merge original fields back into output
    merge_original_fields(batch.input_path, output_path, batch.merged_path)

    batch.status = "success"
    batch.runtime_ms = int((time.time() - batch_start_time) * 1000)
    logger.info(f"Batch {batch.batch_id} completed successfully in {batch.runtime_ms}ms")


def aggregate_results(ctx: ExecutionContext, output_file: str) -> None:
    """Aggregate all batch results into single JSONL output"""
    logger.info("Aggregating batch results...")

    successful_batches = ctx.get_successful_batches()
    if not successful_batches:
        raise ExtractionError("No successful batches to aggregate")

    # Read all batch outputs
    batch_outputs = [
        read_batch_output(batch.merged_path or batch.output_path) for batch in successful_batches
    ]

    if ctx.flat_format:
        write_flat_jsonl(batch_outputs, ctx.docker_images, output_file, ctx.get_failed_batches())
    else:
        write_phebee_jsonl(batch_outputs, ctx.phebee_config(), output_file)


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


def temp_dir_for(output_dir: str, explicit: str | None = None) -> str:
    """Resolve the temp directory: an explicit path, or a timestamped dir under output_dir"""
    if explicit:
        return explicit
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return os.path.join(output_dir, f".temp_batches_{timestamp}")
