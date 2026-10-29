#!/usr/bin/env python3
"""
Mock extraction tool for testing

Performs keyword-based phenotype extraction for testing purposes.
Finds common clinical terms (fever, cough, headache, etc.) and maps
them to HPO terms. Creates realistic annotations for integration testing.
"""

import json
import os
import re
import sys
import time
from datetime import datetime

# Simple keyword → HPO term mapping
KEYWORD_MAP = {
    "fever": ("HP:0001945", "Fever"),
    "cough": ("HP:0012735", "Cough"),
    "headache": ("HP:0002315", "Headache"),
    "headaches": ("HP:0002315", "Headache"),
    "seizure": ("HP:0001250", "Seizure"),
    "seizures": ("HP:0001250", "Seizure"),
    "pneumonia": ("HP:0002090", "Pneumonia"),
    "developmental delay": ("HP:0001263", "Global developmental delay"),
    "migraine": ("HP:0002076", "Migraine"),
    "migraines": ("HP:0002076", "Migraine"),
    "chest pain": ("HP:0100749", "Chest pain"),
    "hearing loss": ("HP:0000365", "Hearing impairment"),
    "recurrent ear infections": ("HP:0410018", "Recurrent ear infections"),
    "walking unsteady": ("HP:0002317", "Unsteady gait"),
}


def extract_annotations(text):
    """Find simple keyword matches in text"""
    annotations = []
    text_lower = text.lower()

    for keyword, (term_id, label) in KEYWORD_MAP.items():
        # Find all occurrences of this keyword
        for match in re.finditer(r"\b" + re.escape(keyword) + r"\b", text_lower):
            annotations.append(
                {
                    "start": match.start(),
                    "end": match.end(),
                    "text": text[match.start() : match.end()],  # Original case
                    "term_id": term_id,
                    "label": label,
                    "ontology": "hpo",
                }
            )

    # Report terms in the order they appear in the note
    return sorted(annotations, key=lambda a: a["start"])


def main():
    # Read environment variables
    # Neutral names take precedence; INPUT_S3_URI/OUTPUT_S3_URI are the legacy names
    input_path = os.environ.get("INPUT_URI") or os.environ.get("INPUT_S3_URI")
    output_path = os.environ.get("OUTPUT_URI") or os.environ.get("OUTPUT_S3_URI")

    if not input_path or not output_path:
        print(
            "ERROR: INPUT_URI and OUTPUT_URI (or legacy INPUT_S3_URI and OUTPUT_S3_URI) "
            "environment variables required",
            file=sys.stderr,
        )
        sys.exit(1)

    # Read input
    try:
        with open(input_path) as f:
            data = json.load(f)
    except Exception as e:
        print(f"ERROR: Failed to read input: {e}", file=sys.stderr)
        sys.exit(2)

    # Validate input
    if "records" not in data:
        print("ERROR: Input missing 'records' field", file=sys.stderr)
        sys.exit(2)

    start_time = time.time()

    # Process records - find actual keywords
    for record in data["records"]:
        text = record.get("text", "")
        record["annotations"] = extract_annotations(text)
        record["error"] = None

    runtime_ms = int((time.time() - start_time) * 1000)

    # Create output with run_metadata
    output = {
        "records": data["records"],
        "run_metadata": {
            "run_id": f"mock-{int(time.time())}",
            "annotator": "MockExtractor",
            "annotator_version": "v1.0.0",
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "models": {
                "phenotype": "5b9339d49ae7ce047984b7459df596bc7be03e7bfca8208a3ca290de0b44a755",
                "context": None,
            },
            "runtime_ms": runtime_ms,
            "status": "success",
            "error_message": None,
        },
    }

    # Write output
    try:
        with open(output_path, "w") as f:
            json.dump(output, f, indent=2)
    except Exception as e:
        print(f"ERROR: Failed to write output: {e}", file=sys.stderr)
        sys.exit(4)

    total_annotations = sum(len(r["annotations"]) for r in data["records"])
    print(
        f"Mock extraction completed: {len(data['records'])} records, {total_annotations} annotations"
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
