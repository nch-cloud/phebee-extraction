"""
Tests for the `review` command and phebee_extraction.review. No Docker required.

Results are produced in-process (mock extractor, mock context classifier, the real PheBee
transform) so the command is checked against real PheBee-format output.
"""

import json
import logging
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).parent
REPO_ROOT = TESTS_DIR.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(TESTS_DIR))

import mock_context  # noqa: E402
import mock_extractor  # noqa: E402

from phebee_extraction import cli  # noqa: E402
from phebee_extraction.loading import load_input_data  # noqa: E402
from phebee_extraction.review import read_results  # noqa: E402
from phebee_extraction.transform import PheBeeConfig, write_phebee_jsonl  # noqa: E402

SAMPLE_NOTES = REPO_ROOT / "examples" / "sample_notes_with_subjects.jsonl"


def write_results(tmp_path, notes, subject_id_column="subject_id", name="results.jsonl"):
    """Extract + classify the notes with the mocks and write PheBee-format results"""
    records = []
    for note in notes:
        text = note["text"] if isinstance(note["text"], str) else ""
        annotations = sorted(mock_extractor.extract_annotations(text), key=lambda a: a["start"])
        for annotation in annotations:
            mock_context.classify_context(text, annotation)
        records.append({**note, "annotations": annotations, "error": None})

    config = PheBeeConfig(
        project_id="demo",
        subject_id_column=subject_id_column,
        image_refs=["mock-extractor:test@sha256:abc"],
    )
    path = tmp_path / name
    write_phebee_jsonl([{"records": records}], config, str(path))
    return path


def run_review(capsys, *argv):
    rc = cli.main(["review", *argv])
    return rc, capsys.readouterr().out


@pytest.fixture
def sample_review(tmp_path, capsys):
    notes = load_input_data(str(SAMPLE_NOTES))
    results = write_results(tmp_path, notes)
    return run_review(capsys, "--results", str(results), "--input", str(SAMPLE_NOTES))


def test_review_shows_each_note_with_its_matches(sample_review):
    rc, out = sample_review
    assert rc == 0
    lines = out.splitlines()

    assert (
        lines[0]
        == "note_001 (patient-001): Patient presents with fever and cough. Denies chest pain."
    )
    assert lines[1] == (
        '    [22:27] "fever"  HP_0001945  negated=false family=false hypothetical=false'
    )
    assert (
        '    [46:56] "chest pain"  HP_0100749  negated=true family=false hypothetical=false'
        in lines
    )
    # notes are separated by a blank line and listed in input order
    note_headers = [line.split(" (")[0] for line in lines if line.startswith("note_")]
    assert note_headers == [f"note_00{i}" for i in range(1, 6)]
    assert out.endswith("\n\n")


def test_review_output_matches_the_readme(sample_review):
    """The output shown in the README's Try It section is exactly what the command prints"""
    _, out = sample_review
    readme = (REPO_ROOT / "README.md").read_text()
    after_marker = readme[readme.index("which prints each note followed by its matches") :]
    shown = after_marker.split("```")[1].strip()
    assert shown == out.strip()


def test_review_reads_csv_with_custom_columns_and_numeric_ids(tmp_path, capsys):
    """The case the old jq snippet could not handle: CSV, renamed columns, numeric IDs"""
    csv_path = tmp_path / "notes.csv"
    csv_path.write_text(
        "mrn,note_id,note_text\n"
        "3520819,2209509581,NICU RT shift note. No fever noted. Possible seizures.\n"
        "3520819,2209509582,Mother had migraines.\n"
    )
    notes = load_input_data(
        str(csv_path), id_column="note_id", text_column="note_text", string_columns={"mrn"}
    )
    results = write_results(tmp_path, notes, subject_id_column="mrn")

    rc, out = run_review(
        capsys,
        "--results", str(results),
        "--input", str(csv_path),
        "--id-column", "note_id",
        "--text-column", "note_text",
    )  # fmt: skip

    assert rc == 0
    lines = out.splitlines()
    text = "NICU RT shift note. No fever noted. Possible seizures."
    assert lines[0] == f"2209509581 (3520819): {text}"
    fever, seizures = text.index("fever"), text.index("seizures")
    assert (
        f'    [{fever}:{fever + 5}] "fever"  HP_0001945  negated=true family=false hypothetical=false'
        in lines
    )
    assert (
        f'    [{seizures}:{seizures + 8}] "seizures"  HP_0001250  '
        "negated=false family=false hypothetical=true" in lines
    )
    assert "2209509582 (3520819): Mother had migraines." in lines


def test_review_lists_notes_with_no_matches(tmp_path, capsys):
    notes = [
        {"note_id": "n1", "subject_id": "s1", "text": "Nothing of note here."},
        {"note_id": "n2", "subject_id": "s1", "text": "Has fever."},
    ]
    results = write_results(tmp_path, notes)
    input_path = tmp_path / "in.jsonl"
    input_path.write_text("".join(json.dumps(n) + "\n" for n in notes))

    _, out = run_review(capsys, "--results", str(results), "--input", str(input_path))

    # n1 has no results, so its subject comes from the input's subject_id column
    assert out.splitlines()[:3] == [
        "n1 (s1): Nothing of note here.",
        "",
        "n2 (s1): Has fever.",
    ]


def test_review_note_without_any_subject(tmp_path, capsys):
    input_path = tmp_path / "in.jsonl"
    input_path.write_text(json.dumps({"note_id": "n1", "text": "Nothing here."}) + "\n")
    results = tmp_path / "empty.jsonl"
    results.write_text("")

    _, out = run_review(capsys, "--results", str(results), "--input", str(input_path))

    assert out.splitlines()[0] == "n1: Nothing here."


def test_review_note_id_filter_and_limit(tmp_path, capsys):
    notes = load_input_data(str(SAMPLE_NOTES))
    results = write_results(tmp_path, notes)
    base = ["--results", str(results), "--input", str(SAMPLE_NOTES)]

    _, only = run_review(capsys, *base, "--note-id", "note_003", "note_001")
    assert [ln.split(" (")[0] for ln in only.splitlines() if ln.startswith("note_")] == [
        "note_001",
        "note_003",
    ]  # input order, not the order given

    _, limited = run_review(capsys, *base, "--limit", "2")
    assert [ln.split(" (")[0] for ln in limited.splitlines() if ln.startswith("note_")] == [
        "note_001",
        "note_002",
    ]


def test_review_warns_about_results_for_notes_not_in_input(tmp_path, capsys, caplog):
    all_notes = load_input_data(str(SAMPLE_NOTES))
    results = write_results(tmp_path, all_notes)
    shorter = tmp_path / "shorter.jsonl"
    shorter.write_text("".join(json.dumps(n) + "\n" for n in all_notes[:2]))

    with caplog.at_level(logging.WARNING):
        rc, out = run_review(capsys, "--results", str(results), "--input", str(shorter))

    assert rc == 0
    assert "3 note(s) in the results are not in --input" in caplog.text
    assert "note_001" in out and "note_003" not in out


def test_review_warns_about_unknown_note_id_filter(tmp_path, capsys, caplog):
    notes = load_input_data(str(SAMPLE_NOTES))
    results = write_results(tmp_path, notes)

    with caplog.at_level(logging.WARNING):
        run_review(
            capsys, "--results", str(results), "--input", str(SAMPLE_NOTES), "--note-id", "nope"
        )

    assert "--note-id not found in --input: nope" in caplog.text


def test_review_rejects_flat_format_results(tmp_path, capsys, caplog):
    flat = tmp_path / "flat.jsonl"
    flat.write_text(
        json.dumps({"note_id": "n1", "text": "x", "annotations": [], "error": None}) + "\n"
    )

    with pytest.raises(ValueError, match="not a PheBee-format record"):
        read_results(str(flat))

    with caplog.at_level(logging.ERROR):
        rc, _ = run_review(capsys, "--results", str(flat), "--input", str(SAMPLE_NOTES))
    assert rc == 1
    assert "flat-format output" in caplog.text


def test_review_tolerates_empty_note_text(tmp_path, capsys):
    """An empty cell in a CSV loads as NaN, not a string"""
    csv_path = tmp_path / "notes.csv"
    csv_path.write_text("note_id,subject_id,text\nn1,s1,\nn2,s1,Has fever.\n")
    notes = load_input_data(str(csv_path))
    results = write_results(tmp_path, notes)

    rc, out = run_review(capsys, "--results", str(results), "--input", str(csv_path))

    assert rc == 0
    assert out.splitlines()[0] == "n1 (s1): "
    assert "HP_0001945" in out


def test_review_shows_whatever_context_keys_the_results_contain(tmp_path, capsys):
    """PheBee is agnostic to context keys; review must not hardcode the three the mock uses"""
    results = tmp_path / "custom.jsonl"
    results.write_text(
        json.dumps(
            {
                "project_id": "demo",
                "project_subject_id": "s1",
                "term_iri": "http://purl.obolibrary.org/obo/HP_0001945",
                "evidence": [
                    {
                        "clinical_note_id": "n1",
                        "span_start": 4,
                        "span_end": 9,
                        "contexts": {"severity": 0.7, "temporal": 1.0},
                    }
                ],
                "row_num": 1,
            }
        )
        + "\n"
    )
    input_path = tmp_path / "in.jsonl"
    input_path.write_text(json.dumps({"note_id": "n1", "text": "Has fever now."}) + "\n")

    _, out = run_review(capsys, "--results", str(results), "--input", str(input_path))

    assert '    [4:9] "fever"  HP_0001945  severity=0.7 temporal=1.0' in out.splitlines()


def test_review_limit_must_be_positive():
    with pytest.raises(SystemExit) as exc:
        cli.main(["review", "--results", "r", "--input", "i", "--limit", "0"])
    assert exc.value.code == 2
