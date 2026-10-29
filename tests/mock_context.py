#!/usr/bin/env python3
"""
Mock context classification tool

Takes annotations from a previous tool and adds context qualifiers:
- negated: 1 if a negation cue ("no", "denies", ...) comes before the term
- family: 1 if a family-history cue ("mother", "family", ...) comes before the term
- hypothetical: 1 if a speculative cue ("possible", "rule out", ...) comes before the term

A cue affects a term when it appears earlier in the same sentence, with no scope-breaking
word ("but", "however", ...) in between. This is a deliberately small version of the NegEx
idea: it handles the notes used by this repo's tests correctly, but it is not a real
clinical context model (no cues that follow the term, no negation inside lists, no
abbreviations that end in a period).
"""

import json
import os
import re
import sys
import time
from datetime import datetime

# Cue phrases. Matched as whole words or phrases, case-insensitively.
NEGATION_WORDS = {"no", "not", "denies", "denied", "negative", "without", "absence", "absent"}

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

# Words that end the reach of a cue earlier in the same sentence ("no fever but cough")
SCOPE_BREAK_WORDS = {"but", "however", "although", "though", "except"}

# A sentence ends at . ! ? or ; when followed by whitespace (so "98.6" is not a break), or
# at a newline
SENTENCE_BREAK = re.compile(r"[.!?;]+(?=\s|$)|\n+")


def _phrase_pattern(phrases):
    """Regex matching any phrase as a whole word/phrase, longest first"""
    ordered = sorted(phrases, key=len, reverse=True)
    return re.compile(r"\b(?:" + "|".join(re.escape(p) for p in ordered) + r")(?!\w)")


NEGATION_PATTERN = _phrase_pattern(NEGATION_WORDS)
FAMILY_PATTERN = _phrase_pattern(FAMILY_WORDS)
HYPOTHETICAL_PATTERN = _phrase_pattern(HYPOTHETICAL_WORDS)
SCOPE_BREAK_PATTERN = _phrase_pattern(SCOPE_BREAK_WORDS)


def sentence_start(text, position):
    """Offset where the sentence containing `position` begins"""
    start = 0
    for match in SENTENCE_BREAK.finditer(text):
        if match.start() >= position:
            break
        start = match.end()
    return start


def preceding_scope(text, position):
    """
    Lower-cased text before `position` that can still affect a term starting there: from the
    start of its sentence, restarting after the last scope-breaking word.
    """
    prefix = text[sentence_start(text, position) : position].lower()
    scope_start = 0
    for match in SCOPE_BREAK_PATTERN.finditer(prefix):
        scope_start = match.end()
    return prefix[scope_start:]


def classify_context(text, annotation):
    """Add context qualifiers to an annotation"""
    scope = preceding_scope(text, annotation["start"])

    annotation["contexts"] = {
        "negated": 1 if NEGATION_PATTERN.search(scope) else 0,
        "family": 1 if FAMILY_PATTERN.search(scope) else 0,
        "hypothetical": 1 if HYPOTHETICAL_PATTERN.search(scope) else 0,
    }

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
