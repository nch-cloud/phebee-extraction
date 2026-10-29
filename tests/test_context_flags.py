"""
Tests for how context qualifiers are written to PheBee-format output.

PheBee's bulk import reads three flags (negated, family, hypothetical) and accepts
true/false, 1/0, 1.0/0.0 and "true"/"false", hashing them all identically. These flags are
boolean concepts, so this tool writes real booleans. See context_flag in transform.py.
"""

import json
import logging
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from phebee_extraction.transform import (  # noqa: E402
    PheBeeConfig,
    context_flag,
    context_flags,
    record_to_phebee_format,
)

CONFIG = PheBeeConfig(project_id="p", image_refs=["tool:1@sha256:abc"])


def annotated(contexts, **extra):
    """A tool-output record with one fever annotation carrying the given contexts"""
    annotation = {"start": 0, "end": 5, "term_id": "HP:0001945", "text": "fever", **extra}
    if contexts is not ...:
        annotation["contexts"] = contexts
    return {"note_id": "n1", "subject_id": "s1", "text": "fever", "annotations": [annotation]}


@pytest.mark.parametrize(
    "value,expected",
    [
        (True, True),
        (False, False),
        (1, True),
        (0, False),
        (1.0, True),
        (0.0, False),
        ("true", True),
        ("True", True),
        ("TRUE", True),
        (" true ", True),
        ("1", True),
        ("false", False),
        ("False", False),
        ("0", False),
        (None, False),  # null and absent both mean false, as in PheBee
    ],
)
def test_context_flag_accepts_the_spellings_phebee_accepts(value, expected):
    assert context_flag(value) is expected


@pytest.mark.parametrize("value", [0.7, 2, -1, "maybe", "", [], {}, math.nan])
def test_context_flag_rejects_values_that_are_not_flags(value):
    """A score like 0.7 must not be silently rounded to true or false"""
    assert context_flag(value) is None


def test_context_flags_defaults_missing_flags_to_false():
    assert context_flags(None) == {"negated": False, "family": False, "hypothetical": False}
    assert context_flags({}) == {"negated": False, "family": False, "hypothetical": False}
    assert context_flags({"family": 1}) == {
        "negated": False,
        "family": True,
        "hypothetical": False,
    }


def test_context_flags_is_none_if_any_flag_is_invalid():
    assert context_flags({"negated": 1, "family": 0.7}) is None


def test_output_contexts_are_real_booleans():
    """Not 1.0 / 0.0, and not ints: the JSON that is written contains true / false"""
    (record,) = record_to_phebee_format(
        annotated({"negated": 1, "family": 0, "hypothetical": 0}), CONFIG
    )
    contexts = record["evidence"][0]["contexts"]

    assert all(type(value) is bool for value in contexts.values())
    assert contexts == {"negated": True, "family": False, "hypothetical": False}
    assert '"contexts": {"negated": true, "family": false, "hypothetical": false}' in json.dumps(
        record["evidence"][0]
    )


def test_equivalent_spellings_produce_identical_output():
    """Mirrors PheBee, which hashes 1, 1.0, "true" and true the same"""
    spellings = [
        {"negated": True, "family": False, "hypothetical": None},
        {"negated": 1, "family": 0, "hypothetical": 0},
        {"negated": 1.0, "family": 0.0},
        {"negated": "True", "family": "false"},
    ]
    outputs = {
        json.dumps(
            record_to_phebee_format(annotated(contexts), CONFIG)[0]["evidence"][0]["contexts"]
        )
        for contexts in spellings
    }
    assert len(outputs) == 1


def test_missing_or_null_contexts_are_all_false():
    for contexts in (..., None, {}):
        (record,) = record_to_phebee_format(annotated(contexts), CONFIG)
        assert record["evidence"][0]["contexts"] == {
            "negated": False,
            "family": False,
            "hypothetical": False,
        }


def test_only_the_three_flags_phebee_reads_are_written():
    """PheBee's bulk pipeline ignores other context keys, so they are not emitted"""
    (record,) = record_to_phebee_format(
        annotated({"negated": 1, "severity": 0.9, "temporal": "past"}), CONFIG
    )
    assert set(record["evidence"][0]["contexts"]) == {"negated", "family", "hypothetical"}


def test_annotation_with_a_non_flag_context_value_is_skipped_with_a_warning(caplog):
    """A confidence score would otherwise be silently rounded; other annotations still count"""
    record = annotated({"negated": 0.7})
    record["annotations"].append(
        {
            "start": 6,
            "end": 11,
            "term_id": "HP:0012735",
            "text": "cough",
            "contexts": {"negated": 1},
        }
    )

    with caplog.at_level(logging.WARNING):
        records = record_to_phebee_format(record, CONFIG)

    assert [r["term_iri"].rsplit("/", 1)[-1] for r in records] == ["HP_0012735"]
    assert "not a 0/1 flag" in caplog.text
