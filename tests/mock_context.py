#!/usr/bin/env python3
"""
Mock context classification tool

Takes annotations from a previous tool and adds context qualifiers:
- negated: 1 if negation words nearby
- family: 1 if family history words nearby
- hypothetical: 1 if speculative words nearby
"""

import json
import os
import re
import sys
import time
from datetime import datetime

# Simple negation indicators
NEGATION_WORDS = {"no", "denies", "denied", "negative", "without", "absence", "absent"}

# Family history indicators
FAMILY_WORDS = {
    "family",
    "mother",
    "father",
    "parent",
    "sibling",
    "brother",
    "sister",
    "grandfather",
    "grandmother",
    "familial",
    "hereditary",
}

# Hypothetical/speculative indicators
HYPOTHETICAL_WORDS = {
    "possible",
    "possibly",
    "suspected",
    "suspect",
    "may",
    "might",
    "could",
    "likely",
    "potential",
    "rule out",
    "r/o",
    "consider",
}


def get_context_window(text, start, end, window_size=20):
    """Get text around annotation for context detection"""
    # Get words before and after the annotation
    before_start = max(0, start - window_size)
    after_end = min(len(text), end + window_size)

    context_before = text[before_start:start].lower()
    context_after = text[end:after_end].lower()

    return context_before, context_after


def classify_context(text, annotation):
    """Add context qualifiers to an annotation"""
    start = annotation["start"]
    end = annotation["end"]

    # Get surrounding text
    before, after = get_context_window(text, start, end)
    context_text = before + " " + after

    # Split into words for checking
    words = set(re.findall(r"\b\w+\b", context_text))

    # Check for context indicators
    negated = 1 if words & NEGATION_WORDS else 0
    family = 1 if words & FAMILY_WORDS else 0
    hypothetical = 1 if words & HYPOTHETICAL_WORDS else 0

    # Add contexts field
    annotation["contexts"] = {"negated": negated, "family": family, "hypothetical": hypothetical}

    return annotation


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

    # Read input (should have annotations from previous tool)
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

    # Process records - add context to existing annotations
    total_annotations = 0
    for record in data["records"]:
        text = record.get("text", "")
        annotations = record.get("annotations", [])

        # Add context to each annotation
        for annotation in annotations:
            classify_context(text, annotation)
            total_annotations += 1

        record["annotations"] = annotations
        record["error"] = None

    runtime_ms = int((time.time() - start_time) * 1000)

    # Preserve phenotype model from input if present (for tool chaining)
    phenotype_model = None
    if "run_metadata" in data and "models" in data["run_metadata"]:
        phenotype_model = data["run_metadata"]["models"].get("phenotype")

    # Create output with run_metadata
    output = {
        "records": data["records"],
        "run_metadata": {
            "run_id": f"mock-context-{int(time.time())}",
            "annotator": "MockContextClassifier",
            "annotator_version": "v0.0.1",
            "timestamp": datetime.now().isoformat() + "Z",
            "models": {
                "phenotype": phenotype_model,  # Preserved from extraction tool
                "context": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
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

    print(
        f"Context classification completed: {len(data['records'])} records, {total_annotations} annotations classified"
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
