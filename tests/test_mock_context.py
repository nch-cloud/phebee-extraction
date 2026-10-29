"""
Tests for the mock context classifier (tests/mock_context.py).

The mock is crude on purpose, but it must be correct on all of this repo's test data, since
the docs and the chain tests show its output. These tests run each note through the mock
extractor and then the mock classifier and compare with the expectations in
tests/context_cases.py. No Docker required.
"""

import json
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).parent
sys.path.insert(0, str(TESTS_DIR))

import mock_context  # noqa: E402
import mock_extractor  # noqa: E402
from context_cases import CASES, EDGE_CASES  # noqa: E402

REPO_ROOT = TESTS_DIR.parent


def classify_note(text):
    """(matched_text, negated, family, hypothetical) per term, in text order"""
    annotations = sorted(mock_extractor.extract_annotations(text), key=lambda a: a["start"])
    for annotation in annotations:
        mock_context.classify_context(text, annotation)
    return [
        (
            a["text"],
            a["contexts"]["negated"],
            a["contexts"]["family"],
            a["contexts"]["hypothetical"],
        )
        for a in annotations
    ]


@pytest.mark.parametrize("text,expected", CASES, ids=[text[:50] for text, _ in CASES])
def test_context_is_correct_on_test_data(text, expected):
    assert classify_note(text) == expected


@pytest.mark.parametrize("text,expected", EDGE_CASES, ids=[text[:50] for text, _ in EDGE_CASES])
def test_context_edge_cases(text, expected):
    assert classify_note(text) == expected


def sample_note_texts():
    """Every note text in the sample input files (not the sample outputs)"""
    texts = []
    for path in sorted((REPO_ROOT / "examples").glob("sample_notes*.jsonl")):
        with open(path) as f:
            texts += [json.loads(line)["text"] for line in f if line.strip()]
    with open(TESTS_DIR / "fixtures" / "sample_input.json") as f:
        texts += [r["text"] for r in json.load(f)["records"]]
    return texts


def test_every_sample_note_has_an_expectation():
    """Adding sample data without saying what its contexts should be fails here"""
    known = {text for text, _ in CASES}
    missing = sorted({t for t in sample_note_texts() if t not in known})
    assert not missing, f"Add these notes to tests/context_cases.py: {missing}"


def test_every_demo_note_produces_a_match():
    """The Try It walkthrough must not show a note the mock extracts nothing from"""
    empty = [t for t in sample_note_texts() if not mock_extractor.extract_annotations(t)]
    assert not empty, f"Mock extractor finds nothing in these demo notes: {empty}"


def test_cases_cover_every_context_flag():
    """The expectations would be vacuous if no case ever set a flag"""
    used = set()
    for _, expected in CASES:
        for _text, negated, family, hypothetical in expected:
            for index, value in enumerate((negated, family, hypothetical)):
                if value:
                    used.add(index)
    assert used == {0, 1, 2}  # negated, family, hypothetical all appear set at least once


def test_main_writes_classified_output(tmp_path, monkeypatch):
    """The script's own entry point, end to end without Docker"""
    note = "Chronic headaches, possibly migraine. Family history of seizures."
    annotations = mock_extractor.extract_annotations(note)
    input_file = tmp_path / "in.json"
    output_file = tmp_path / "out.json"
    input_file.write_text(
        json.dumps({"records": [{"note_id": "n1", "text": note, "annotations": annotations}]})
    )
    monkeypatch.setenv("INPUT_URI", str(input_file))
    monkeypatch.setenv("OUTPUT_URI", str(output_file))

    with pytest.raises(SystemExit) as exc:
        mock_context.main()
    assert exc.value.code == 0

    record = json.loads(output_file.read_text())["records"][0]
    by_text = {a["text"]: a["contexts"] for a in record["annotations"]}
    assert by_text["migraine"] == {"negated": 0, "family": 0, "hypothetical": 1}
    assert by_text["seizures"] == {"negated": 0, "family": 1, "hypothetical": 0}
    assert by_text["headaches"] == {"negated": 0, "family": 0, "hypothetical": 0}
