"""
Convert tool output (INTERFACE.md records) into the final JSONL formats.

These functions are pure with respect to the pipeline: they take tool output records plus
a small config object and write JSONL. They do not know about Docker or batching, so they
can be reused by other orchestrators.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, TextIO

from phebee_extraction.models import BatchInfo

logger = logging.getLogger(__name__)


@dataclass
class PheBeeConfig:
    """Settings needed to produce PheBee bulk-import records"""

    project_id: str
    subject_id_column: str = "subject_id"
    batch_id: int = 0
    # Image references for the tools that produced the annotations, in pipeline order.
    # Each is "name[:tag]" optionally followed by "@sha256:..." (see validate_docker_images).
    image_refs: List[str] = field(default_factory=list)


@dataclass
class PheBeeWriteStats:
    """Counts from a PheBee JSONL write"""

    records_written: int = 0
    skipped_no_subject: int = 0
    skipped_no_annotations: int = 0
    next_row_num: int = 1


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


# The context flags PheBee's bulk import reads; any other key in `contexts` is ignored by it
CONTEXT_FLAGS = ("negated", "family", "hypothetical")


def context_flag(value: Any) -> Optional[bool]:
    """
    Interpret one context value as a boolean flag, the way PheBee's bulk import does.

    None (null or absent) is False, matching PheBee, which treats a missing flag as false.
    True/False, 1/0, 1.0/0.0 and the strings "true"/"false" (any case) are accepted.
    Returns None for anything else, e.g. a score like 0.7, so the caller can decide what to
    do instead of silently rounding it.
    """
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        if value == 0:
            return False
        if value == 1:
            return True
        return None
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "1", "1.0"):
            return True
        if lowered in ("false", "0", "0.0"):
            return False
    return None


def context_flags(contexts: Optional[Dict[str, Any]]) -> Optional[Dict[str, bool]]:
    """All three context flags as booleans, or None if any value is not a recognisable flag"""
    contexts = contexts or {}
    flags = {}
    for name in CONTEXT_FLAGS:
        flag = context_flag(contexts.get(name))
        if flag is None:
            return None
        flags[name] = flag
    return flags


def evidence_creator_fields(image_refs: List[str]) -> Dict[str, Optional[str]]:
    """
    Derive the evidence creator fields from the pipeline's image references.

    creator_id and creator_name are the image names joined with '+'; creator_digest is the
    digests joined the same way, or None if any image lacks a digest.
    """
    creator_names = []
    creator_digests = []
    for image_ref in image_refs:
        if "@" in image_ref:
            name, digest = image_ref.split("@", 1)
            creator_names.append(name)
            creator_digests.append(digest)
        else:
            # No digest available
            creator_names.append(image_ref)
            creator_digests.append(None)

    creator_id = "+".join(creator_names)
    return {
        "evidence_creator_id": creator_id,
        "evidence_creator_type": "automated",
        "evidence_creator_name": creator_id,  # Same as creator_id
        # Only include digest if all images have digests
        "evidence_creator_digest": "+".join(creator_digests) if all(creator_digests) else None,
    }


def _annotation_span(annotation: Dict[str, Any]):
    """Return (start, end) supporting several field-name variants, or (None, None)"""
    # Use explicit None checks to handle start=0 correctly
    span_start = annotation.get("start")
    if span_start is None:
        span_start = annotation.get("span_start")
    if span_start is None:
        span_start = annotation.get("begin")

    span_end = annotation.get("end")
    if span_end is None:
        span_end = annotation.get("span_end")

    return span_start, span_end


def record_to_phebee_format(record: Dict[str, Any], config: PheBeeConfig) -> List[Dict[str, Any]]:
    """
    Convert a single extraction record to PheBee format records.
    Returns one PheBee record per annotation (streaming approach).
    Note: row_num will be set by the caller.
    """
    phebee_records = []

    # Get subject_id
    subject_id = record.get(config.subject_id_column)
    if not subject_id:
        logger.warning(
            f"Note {record.get('note_id', 'unknown')} missing {config.subject_id_column}, skipping"
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

    creator_fields = evidence_creator_fields(config.image_refs)

    for annotation in annotations:
        term_id = annotation.get("term_id")
        if not term_id:
            logger.warning(f"Annotation in note {note_id} missing term_id, skipping")
            continue

        span_start, span_end = _annotation_span(annotation)
        if span_start is None or span_end is None:
            logger.warning(
                f"Annotation in note {note_id} missing span positions (tried 'start'/'end', "
                f"'span_start'/'span_end', 'begin'), skipping. Available fields: {list(annotation.keys())}"
            )
            continue

        # Context flags are boolean concepts, so write them as booleans
        flags = context_flags(annotation.get("contexts"))
        if flags is None:
            logger.warning(
                f"Annotation in note {note_id} has a context value that is not a 0/1 flag "
                f"({annotation.get('contexts')}), skipping"
            )
            continue

        # Create evidence item
        evidence_item = {
            "type": "clinical_note",
            "clinical_note_id": note_id,
            "encounter_id": encounter_id,
            **creator_fields,
            "note_timestamp": note_timestamp,
            "note_type": record.get("note_type"),
            "provider_type": record.get("provider_type"),
            "author_specialty": record.get("author_specialty"),
            "span_start": int(span_start),
            "span_end": int(span_end),
            "contexts": flags,
        }

        # Create PheBee record with single evidence item (row_num will be set by caller)
        phebee_record = {
            "project_id": config.project_id,
            "project_subject_id": subject_id,
            "term_iri": term_id_to_iri(term_id),
            "evidence": [evidence_item],
            "batch_id": config.batch_id,
        }

        phebee_records.append(phebee_record)

    return phebee_records


def write_phebee_records(
    records: Iterable[Dict[str, Any]],
    config: PheBeeConfig,
    fh: TextIO,
    start_row_num: int = 1,
) -> PheBeeWriteStats:
    """
    Stream tool-output records to an open file as PheBee JSONL (one line per annotation).

    Records with an error, no subject, or no annotations are skipped and counted.
    """
    stats = PheBeeWriteStats(next_row_num=start_row_num)

    for record in records:
        # Skip records with errors
        if record.get("error"):
            continue

        # Check for subject_id
        if not record.get(config.subject_id_column):
            stats.skipped_no_subject += 1
            continue

        # Check for annotations
        if not record.get("annotations"):
            stats.skipped_no_annotations += 1
            continue

        # Transform to PheBee format (one record per annotation)
        for phebee_record in record_to_phebee_format(record, config):
            phebee_record["row_num"] = stats.next_row_num
            fh.write(json.dumps(phebee_record) + "\n")
            stats.records_written += 1
            stats.next_row_num += 1

    return stats


def write_phebee_jsonl(
    batch_outputs: Iterable[Dict[str, Any]],
    config: PheBeeConfig,
    output_file: str,
    start_row_num: int = 1,
) -> PheBeeWriteStats:
    """Write the records from a sequence of tool outputs to a PheBee JSONL file"""
    totals = PheBeeWriteStats(next_row_num=start_row_num)

    with open(output_file, "w") as f:
        for output in batch_outputs:
            stats = write_phebee_records(output["records"], config, f, totals.next_row_num)
            totals.records_written += stats.records_written
            totals.skipped_no_subject += stats.skipped_no_subject
            totals.skipped_no_annotations += stats.skipped_no_annotations
            totals.next_row_num = stats.next_row_num

    logger.info(f"Wrote {totals.records_written} PheBee records to {output_file}")
    if totals.skipped_no_subject > 0:
        logger.warning(
            f"Skipped {totals.skipped_no_subject} notes without {config.subject_id_column}"
        )
    if totals.skipped_no_annotations > 0:
        logger.info(f"Skipped {totals.skipped_no_annotations} notes with no annotations")

    return totals


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


def write_flat_jsonl(
    batch_outputs: List[Dict[str, Any]],
    tools_used: List[str],
    output_file: str,
    failed_batches: Optional[List[BatchInfo]] = None,
) -> int:
    """
    Write note-centric JSONL: one line per record, then a final `_metadata` line.

    Returns the number of records written (excluding the metadata line).
    """
    failed_batches = failed_batches or []
    total_records = 0

    with open(output_file, "w") as f:
        # Write records (one per line)
        for output in batch_outputs:
            for record in output["records"]:
                f.write(json.dumps(record) + "\n")
                total_records += 1

        # Merge metadata (AWS pipeline format)
        metadata_list = [out["run_metadata"] for out in batch_outputs]
        aggregate_metadata = merge_run_metadata(metadata_list, tools_used)
        aggregate_metadata["total_records"] = total_records

        # Add failure info if any
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
    return total_records
