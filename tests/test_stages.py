"""
Tests for the stage-level API: the StageRunner protocol, and the `split` / `transform`
commands that let an external scheduler drive the pipeline. No Docker required.
"""

import json
import logging
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from phebee_extraction import cli  # noqa: E402
from phebee_extraction.batching import create_batches  # noqa: E402
from phebee_extraction.docker_runner import image_reference_with_digest  # noqa: E402
from phebee_extraction.interface import merge_original_records  # noqa: E402
from phebee_extraction.models import BatchProcessingError  # noqa: E402
from phebee_extraction.pipeline import (  # noqa: E402
    ExecutionContext,
    ProgressTracker,
    aggregate_results,
    run_batch_pipeline,
)
from phebee_extraction.runner import StageResult  # noqa: E402
from phebee_extraction.transform import evidence_creator_fields  # noqa: E402

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
    with open(batch.merged_path) as f:
        out = json.load(f)
    assert [r["subject_id"] for r in out["records"]] == ["subj-1", "subj-2"]
    assert out["records"][0]["annotations"] == [ANNOTATION]


class ReadOnlyOutputRunner(FakeRunner):
    """
    Like a container running as root on Linux: the files it creates in the shared directory
    belong to another user, so the host can read them but not rewrite them.
    """

    def run_stage(self, image, input_path, output_path):
        result = super().run_stage(image, input_path, output_path)
        os.chmod(output_path, 0o444)
        return result


def test_pipeline_never_rewrites_a_file_the_container_created(tmp_path):
    """
    Regression test: on Linux the container's output is owned by root, and merging the original
    fields back by rewriting that file in place failed with PermissionError. The merged records
    go to a separate file instead, and the container's own output is left untouched.
    """
    runner = ReadOnlyOutputRunner()
    ctx = make_ctx(tmp_path, ["tool-a:1"], runner)
    batch = ctx.batches[0]

    run_batch_pipeline(ctx, batch, ProgressTracker(1, 1))

    assert batch.status == "success"

    # the container's output is exactly what the tool wrote: no subject_id merged into it
    with open(batch.output_path) as f:
        raw = json.load(f)
    assert all("subject_id" not in record for record in raw["records"])

    # the merged copy is a different file, and has the original fields back
    assert batch.merged_path and batch.merged_path != batch.output_path
    with open(batch.merged_path) as f:
        merged = json.load(f)
    assert [r["subject_id"] for r in merged["records"]] == ["subj-1", "subj-2"]

    # aggregation reads the merged copy
    out = tmp_path / "aggregated.json"
    aggregate_results(ctx, str(out))
    lines = [json.loads(line) for line in out.read_text().splitlines()]
    assert [line["project_subject_id"] for line in lines] == ["subj-1", "subj-2"]


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


def test_run_single_stage_mounts_an_absolute_host_path(tmp_path, monkeypatch):
    """A relative --output used to produce a relative mount source, which Docker rejects"""
    from unittest.mock import MagicMock

    from phebee_extraction.docker_runner import run_single_stage

    monkeypatch.chdir(tmp_path)
    client = MagicMock()
    container = client.containers.run.return_value
    container.wait.return_value = {"StatusCode": 0}
    container.logs.return_value = b""

    relative_in = "results/.temp_batches_x/batch_000001_input.json"
    relative_out = "results/.temp_batches_x/batch_000001_output.json"
    result = run_single_stage(client, "tool:1", relative_in, relative_out, False, 0)

    assert result.ok
    (mount_source,) = client.containers.run.call_args.kwargs["volumes"].keys()
    assert os.path.isabs(mount_source)
    assert mount_source == str(tmp_path / "results/.temp_batches_x")
    # in-container paths are unaffected: just the file names under /data
    env = client.containers.run.call_args.kwargs["environment"]
    assert env["INPUT_URI"] == "/data/batch_000001_input.json"


REAL_NAME = "ods/phebee-clinphen:batch-v1.0.0_clinphen-1.28_hpo-v2025-10-22_c58cc41"


def test_image_reference_keeps_tag_and_takes_registry_digest():
    """The tag carries version info and must survive; RepoDigests only supplies the digest"""
    ref = image_reference_with_digest(
        REAL_NAME, ["ods/phebee-clinphen@sha256:aaa"], "sha256:imageid"
    )
    assert ref == f"{REAL_NAME}@sha256:aaa"


def test_image_reference_prefers_matching_repository():
    ref = image_reference_with_digest(
        "myrepo/tool:1.0", ["other/tool@sha256:bbb", "myrepo/tool@sha256:ccc"], None
    )
    assert ref == "myrepo/tool:1.0@sha256:ccc"


def test_image_reference_falls_back_to_first_digest_when_no_repository_matches():
    # e.g. the daemon normalises the repository name differently from what was typed
    ref = image_reference_with_digest("tool:1.0", ["docker.io/library/tool@sha256:ddd"], None)
    assert ref == "tool:1.0@sha256:ddd"


def test_image_reference_registry_port_is_not_mistaken_for_a_tag():
    ref = image_reference_with_digest(
        "localhost:5000/tool", ["localhost:5000/tool@sha256:eee"], "sha256:imageid"
    )
    assert ref == "localhost:5000/tool@sha256:eee"


def test_image_reference_uses_image_id_for_local_images():
    """Locally built images have no RepoDigests; the local image ID stands in"""
    assert image_reference_with_digest("clinphen:latest", [], "sha256:abc123") == (
        "clinphen:latest@sha256:abc123"
    )
    assert image_reference_with_digest("clinphen:latest", None, "abc123") == (
        "clinphen:latest@sha256:abc123"
    )


def test_image_reference_already_pinned_is_unchanged():
    pinned = "tool@sha256:fff"
    assert image_reference_with_digest(pinned, ["tool@sha256:other"], "sha256:x") == pinned


def test_image_reference_without_any_digest_is_the_bare_name():
    assert image_reference_with_digest("clinphen:latest", None, None) == "clinphen:latest"


def test_creator_fields_keep_the_full_tag():
    """End to end through the creator fields: a tagged real-world name is preserved"""
    ref = image_reference_with_digest(REAL_NAME, ["ods/phebee-clinphen@sha256:aaa"], None)
    fields = evidence_creator_fields([ref])
    assert fields["evidence_creator_id"] == REAL_NAME
    assert fields["evidence_creator_name"] == REAL_NAME
    assert fields["evidence_creator_digest"] == "sha256:aaa"


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
    """Subject comes from --original-input; creator fields from --image-ref; row_num is the line number"""
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
        ]
    )
    assert rc == 0

    lines = [json.loads(line) for line in out.read_text().splitlines()]
    assert len(lines) == len(tool_output["records"][0]["annotations"])
    assert [line["row_num"] for line in lines] == list(range(1, 1 + len(lines)))
    first = lines[0]
    assert first["project_id"] == "proj"
    assert first["project_subject_id"] == "subj-9"
    ev = first["evidence"][0]
    assert ev["evidence_creator_id"] == "tool:1"
    assert ev["evidence_creator_digest"] == "sha256:abc"
    assert ev["clinical_note_id"] == note_id


def test_transform_phebee_without_any_subject_fails_loudly(tmp_path, caplog):
    """Forgetting --original-input (or the subject column) must not look like an empty result"""
    out = tmp_path / "part.jsonl"
    with caplog.at_level(logging.ERROR):
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
    assert rc == 1
    assert "--subject-id-column" in caplog.text
    assert not out.exists()


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
