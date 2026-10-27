"""
Tests for the stage-level API: the StageRunner protocol, and the `split` / `transform`
commands that let an external scheduler drive the pipeline. No Docker required.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from phebee_extraction import cli  # noqa: E402
from phebee_extraction.batching import create_batches  # noqa: E402
from phebee_extraction.interface import merge_original_records  # noqa: E402
from phebee_extraction.models import BatchProcessingError  # noqa: E402
from phebee_extraction.pipeline import (  # noqa: E402
    ExecutionContext,
    ProgressTracker,
    run_batch_pipeline,
)
from phebee_extraction.runner import StageResult  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"

ANNOTATION = {
    "start": 0,
    "end": 5,
    "text": "fever",
    "term_id": "HP:0001945",
    "label": "Fever",
    "ontology": "hpo",
    "contexts": {"negated": 0, "family": 0, "hypothetical": 0},
}
RUN_METADATA = {
    "run_id": "fake-run",
    "annotator": "FakeTool",
    "annotator_version": "0",
    "timestamp": "2026-01-01T00:00:00Z",
}


class FakeRunner:
    """
    StageRunner that writes a canned tool output instead of running a container.

    Like a real tool, it keeps only note_id/text and drops any other input fields, so the
    pipeline's merge-back of original fields is exercised.
    """

    def __init__(self, fail_on_call=None):
        self.calls = []
        self.fail_on_call = fail_on_call

    def prepare(self, images):
        return [f"{image}@sha256:{'0' * 64}" for image in images]

    def run_stage(self, image, input_path, output_path):
        self.calls.append((image, input_path, output_path))
        if self.fail_on_call == len(self.calls):
            return StageResult("failed", 1, "something broke", 5, "Container exited with code 1")

        with open(input_path) as f:
            records = json.load(f)["records"]
        output = {
            "records": [
                {
                    "note_id": r["note_id"],
                    "text": r["text"],
                    "annotations": [ANNOTATION],
                    "error": None,
                }
                for r in records
            ],
            "run_metadata": RUN_METADATA,
        }
        with open(output_path, "w") as f:
            json.dump(output, f)
        return StageResult("success", 0, "", 5)


def make_ctx(tmp_path, images, runner, continue_on_error=False):
    records = [
        {"note_id": "n1", "subject_id": "subj-1", "text": "fever"},
        {"note_id": "n2", "subject_id": "subj-2", "text": "cough"},
    ]
    ctx = ExecutionContext(
        input_file="unused",
        output_dir=str(tmp_path),
        docker_images=images,
        batch_size=10,
        gpu=False,
        temp_dir=str(tmp_path),
        keep_temp=True,
        continue_on_error=continue_on_error,
        docker_timeout=0,
        project_id="proj",
        runner=runner,
    )
    ctx.batches = create_batches(records, 10, str(tmp_path))
    return ctx


# ============================================================================
# StageRunner / run_batch_pipeline
# ============================================================================


def test_stage_result_ok():
    assert StageResult("success", 0, "", 1).ok
    assert not StageResult("failed", 1, "", 1, "boom").ok


def test_pipeline_chains_stages_through_runner(tmp_path):
    """Two stages run in order, stage N's output is stage N+1's input, fields merge back"""
    runner = FakeRunner()
    ctx = make_ctx(tmp_path, ["tool-a:1", "tool-b:1"], runner)
    batch = ctx.batches[0]

    run_batch_pipeline(ctx, batch, ProgressTracker(1, 2))

    assert batch.status == "success"
    assert [c[0] for c in runner.calls] == ["tool-a:1", "tool-b:1"]
    # stage 1 writes the intermediate file that stage 2 reads
    assert runner.calls[0][2] == runner.calls[1][1]
    assert runner.calls[0][2].endswith("batch_000001_stage1.json")
    assert runner.calls[1][2] == batch.output_path

    # the fake tool dropped subject_id; the pipeline merged it back from the input
    with open(batch.output_path) as f:
        out = json.load(f)
    assert [r["subject_id"] for r in out["records"]] == ["subj-1", "subj-2"]
    assert out["records"][0]["annotations"] == [ANNOTATION]


def test_pipeline_stage_failure_raises_by_default(tmp_path):
    runner = FakeRunner(fail_on_call=1)
    ctx = make_ctx(tmp_path, ["tool-a:1", "tool-b:1"], runner)

    with pytest.raises(BatchProcessingError):
        run_batch_pipeline(ctx, ctx.batches[0], ProgressTracker(1, 2))

    assert ctx.batches[0].status == "failed"
    assert len(runner.calls) == 1  # second stage never ran


def test_pipeline_stage_failure_continue_on_error(tmp_path):
    runner = FakeRunner(fail_on_call=1)
    ctx = make_ctx(tmp_path, ["tool-a:1"], runner, continue_on_error=True)

    run_batch_pipeline(ctx, ctx.batches[0], ProgressTracker(1, 1))  # does not raise

    assert ctx.batches[0].status == "failed"
    assert "exited with code 1" in ctx.batches[0].error_message


def test_pipeline_treats_missing_output_as_failure(tmp_path):
    """A runner that says 'success' without writing the output file is a failed stage"""

    class SilentRunner(FakeRunner):
        def run_stage(self, image, input_path, output_path):
            self.calls.append((image, input_path, output_path))
            return StageResult("success", 0, "", 1)

    runner = SilentRunner()
    ctx = make_ctx(tmp_path, ["tool-a:1", "tool-b:1"], runner, continue_on_error=True)

    run_batch_pipeline(ctx, ctx.batches[0], ProgressTracker(1, 2))

    assert ctx.batches[0].status == "failed"
    assert "did not write" in ctx.batches[0].error_message
    assert len(runner.calls) == 1  # pipeline stopped before stage 2


def test_merge_original_records_output_takes_precedence():
    original = [{"note_id": "n1", "text": "ORIGINAL", "subject_id": "s1", "mrn": "123"}]
    output = [{"note_id": "n1", "text": "tool text", "annotations": []}]

    merge_original_records(original, output)

    assert output[0]["subject_id"] == "s1"
    assert output[0]["mrn"] == "123"
    assert output[0]["text"] == "tool text"  # not overwritten


# ============================================================================
# split
# ============================================================================


def test_split_writes_batches_and_manifest(tmp_path):
    out_dir = tmp_path / "batches"
    rc = cli.main(
        [
            "split",
            "--input",
            str(FIXTURES / "sample_input.json"),
            "--output-dir",
            str(out_dir),
            "--batch-size",
            "2",
        ]
    )
    assert rc == 0

    assert sorted(p.name for p in out_dir.glob("batch_*")) == [
        "batch_000001_input.json",
        "batch_000002_input.json",
    ]
    with open(out_dir / "manifest.json") as f:
        manifest = json.load(f)
    assert manifest["total_records"] == 3
    assert manifest["batch_count"] == 2
    assert manifest["batches"][0] == {
        "batch_id": "batch_000001",
        "input_file": "batch_000001_input.json",
        "output_file": "batch_000001_output.json",
        "record_count": 2,
    }
    assert manifest["batches"][1]["record_count"] == 1


# ============================================================================
# transform
# ============================================================================


@pytest.fixture
def tool_output():
    with open(FIXTURES / "sample_output.json") as f:
        return json.load(f)


def test_transform_phebee_with_original_input(tmp_path, tool_output):
    """Subject comes from --original-input; creator fields from --image-ref; row_num offset"""
    note_id = tool_output["records"][0]["note_id"]
    original = tmp_path / "orig.json"
    with open(original, "w") as f:
        json.dump({"records": [{"note_id": note_id, "text": "x", "subject_id": "subj-9"}]}, f)
    out = tmp_path / "part.jsonl"

    rc = cli.main(
        [
            "transform",
            "--input",
            str(FIXTURES / "sample_output.json"),
            "--original-input",
            str(original),
            "--output",
            str(out),
            "--project-id",
            "proj",
            "--image-ref",
            "tool:1@sha256:abc",
            "--start-row-num",
            "5",
        ]
    )
    assert rc == 0

    lines = [json.loads(line) for line in out.read_text().splitlines()]
    assert len(lines) == len(tool_output["records"][0]["annotations"])
    assert [line["row_num"] for line in lines] == list(range(5, 5 + len(lines)))
    first = lines[0]
    assert first["project_id"] == "proj"
    assert first["project_subject_id"] == "subj-9"
    ev = first["evidence"][0]
    assert ev["evidence_creator_id"] == "tool:1"
    assert ev["evidence_creator_digest"] == "sha256:abc"
    assert ev["clinical_note_id"] == note_id


def test_transform_phebee_without_subject_writes_nothing(tmp_path):
    out = tmp_path / "part.jsonl"
    rc = cli.main(
        [
            "transform",
            "--input",
            str(FIXTURES / "sample_output.json"),
            "--output",
            str(out),
            "--project-id",
            "proj",
            "--image-ref",
            "tool:1",
        ]
    )
    assert rc == 0
    assert out.read_text() == ""


def test_transform_flat_format(tmp_path, tool_output):
    out = tmp_path / "flat.jsonl"
    rc = cli.main(
        [
            "transform",
            "--input",
            str(FIXTURES / "sample_output.json"),
            str(FIXTURES / "sample_output.json"),
            "--output",
            str(out),
            "--flat-format",
            "--image-ref",
            "tool:1",
        ]
    )
    assert rc == 0

    lines = [json.loads(line) for line in out.read_text().splitlines()]
    assert len(lines) == 2 * len(tool_output["records"]) + 1
    meta = lines[-1]["_metadata"]
    assert meta["tools_used"] == ["tool:1"]
    assert meta["batch_count"] == 2
    assert meta["status"] == "success"


@pytest.mark.parametrize(
    "argv",
    [
        # PheBee format needs a project id
        ["transform", "--input", "x.json", "--output", "o.jsonl", "--image-ref", "t"],
        # ... and an image ref
        ["transform", "--input", "x.json", "--output", "o.jsonl", "--project-id", "p"],
        # original inputs must pair with inputs
        [
            "transform",
            "--input",
            "a.json",
            "b.json",
            "--original-input",
            "a0.json",
            "--output",
            "o.jsonl",
            "--project-id",
            "p",
            "--image-ref",
            "t",
        ],
    ],
)
def test_transform_argument_validation(argv):
    with pytest.raises(SystemExit) as exc:
        cli.main(argv)
    assert exc.value.code == 2


def test_no_subcommand_defaults_to_run():
    """`extract.py --help` still shows the full-pipeline usage"""
    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"])
    assert exc.value.code == 0
