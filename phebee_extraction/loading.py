"""Load clinical notes from local files (JSON, JSONL, Parquet, CSV)."""

import json
import logging
import os
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

# Identifier fields converted to strings on every load. Containers validate identifiers as
# strings, and CSV/Parquet readers turn numeric-looking ids into numbers. The subject column
# and any other identifier columns are named by the caller (string_columns).
ALWAYS_STRING_FIELDS = ("note_id", "encounter_id")


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
    file_path: str,
    format: str = "auto",
    id_column: str = "note_id",
    text_column: str = "text",
    string_columns: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    """
    Load clinical notes from various file formats

    Returns list of note dictionaries with 'note_id' and 'text' keys. Identifier fields are
    converted to strings: note_id, encounter_id, and every column named in string_columns
    (the subject column at least; pass any other numeric identifier a tool expects as text).
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

    # Convert identifier fields to strings (they may be numeric in input data)
    to_string = set(ALWAYS_STRING_FIELDS) | set(string_columns or ())
    for record in records:
        for name in to_string:
            if name in record and record[name] is not None:
                record[name] = str(record[name])

    logger.info(f"Loaded {len(records)} records")
    return records


def validate_input_records(records: list[dict[str, Any]]) -> None:
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
