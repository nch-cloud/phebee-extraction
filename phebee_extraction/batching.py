"""Split notes into batch files and lay out per-stage paths."""

import json
import logging
import os
from typing import Any, Dict, List, Tuple

from phebee_extraction.interface import write_batch_file
from phebee_extraction.models import BatchInfo

logger = logging.getLogger(__name__)


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


def write_manifest(
    batches: List[BatchInfo], batch_dir: str, total_records: int, batch_size: int
) -> str:
    """
    Write manifest.json next to the batch files so an external scheduler can drive them.

    Lists each batch's input file and the output file name the pipeline expects the final
    stage to write. Paths are relative to batch_dir.
    """
    manifest = {
        "total_records": total_records,
        "batch_size": batch_size,
        "batch_count": len(batches),
        "batches": [
            {
                "batch_id": b.batch_id,
                "input_file": os.path.basename(b.input_path),
                "output_file": os.path.basename(b.output_path),
                "record_count": b.record_count,
            }
            for b in batches
        ],
    }
    manifest_path = os.path.join(batch_dir, "manifest.json")
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)
    return manifest_path


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
