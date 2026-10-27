"""Load clinical notes from local files (JSON, JSONL, Parquet, CSV)."""

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

logger = logging.getLogger(__name__)

# Common ID fields that may be numeric in input data but must be strings for the
# container JSON schema
ID_FIELDS = [
    "note_id",
    "subject_id",
    "pat_id",
    "mrn",
    "encounter_id",
    "enc_id",
    "note_csn_id",
    "raw_note_id",
]


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

    elif format in ("parquet", "csv"):
        df = pd.read_parquet(file_path) if format == "parquet" else pd.read_csv(file_path)
        label = "Parquet" if format == "parquet" else "CSV"
        if id_column not in df.columns:
            raise ValueError(f"Column '{id_column}' not found in {label} file")
        if text_column not in df.columns:
            raise ValueError(f"Column '{text_column}' not found in {label} file")

        # Convert ID columns to strings (they may be numeric in tabular formats)
        df[id_column] = df[id_column].astype(str)

        # Convert to records, renaming columns if needed
        df = df.rename(columns={id_column: "note_id", text_column: "text"})
        records = df.to_dict("records")

    else:
        raise ValueError(f"Unsupported format: {format}")

    # Convert common ID fields to strings (they may be numeric in input data)
    # This ensures compatibility with Docker container JSON schema validation
    for record in records:
        for id_field in ID_FIELDS:
            if id_field in record and record[id_field] is not None:
                record[id_field] = str(record[id_field])

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
