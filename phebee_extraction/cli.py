"""
Command-line entry point.

Four commands:

    extract.py [run] ...   full local pipeline: load -> batch -> run containers -> aggregate
    extract.py split ...   load notes and write batch files (plus manifest.json) and stop
    extract.py transform   convert tool output files into PheBee or flat JSONL
    extract.py review ...  show PheBee-format results next to the original notes

``run`` is the default when no command is given, so the original ``extract.py --input ...``
invocation is unchanged. ``split`` and ``transform`` exist so an external scheduler can run
the container stages itself and still reuse the batching and output logic.
"""

import argparse
import json
import logging
import os
import sys
from typing import Optional

from phebee_extraction.batching import create_batches, write_manifest
from phebee_extraction.docker_runner import DockerRunner
from phebee_extraction.interface import merge_original_records, read_batch_output
from phebee_extraction.loading import load_input_data, validate_input_records
from phebee_extraction.models import BatchProcessingError
from phebee_extraction.pipeline import (
    ExecutionContext,
    ProgressTracker,
    aggregate_results,
    cleanup_temp_files,
    generate_summary_report,
    run_batch_pipeline,
    temp_dir_for,
)
from phebee_extraction.review import format_review, read_results, unmatched_note_ids
from phebee_extraction.transform import PheBeeConfig, write_flat_jsonl, write_phebee_jsonl

logger = logging.getLogger(__name__)

SUBCOMMANDS = ("run", "split", "transform", "review")


def _prog() -> str:
    """Name to show in usage: `extract.py` from a checkout, `phebee-extract` when installed"""
    return os.path.basename(sys.argv[0]) or "phebee-extract"


# ----------------------------------------------------------------------------
# Shared argument groups
# ----------------------------------------------------------------------------


def _add_input_arguments(parser: argparse.ArgumentParser, batch_size: bool = True) -> None:
    parser.add_argument("--input", required=True, help="Input file (JSON/JSONL/Parquet/CSV)")
    parser.add_argument(
        "--input-format",
        choices=["auto", "json", "jsonl", "parquet", "csv"],
        default="auto",
        help="Input file format (default: auto-detect)",
    )
    parser.add_argument(
        "--id-column",
        default="note_id",
        help="Column name for note IDs in CSV/Parquet (default: note_id)",
    )
    parser.add_argument(
        "--text-column",
        default="text",
        help="Column name for note text in CSV/Parquet (default: text)",
    )
    if batch_size:
        parser.add_argument(
            "--batch-size",
            type=int,
            default=1000,
            help="Number of notes per batch (default: 1000)",
        )


def _add_phebee_output_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--flat-format",
        action="store_true",
        help="Output flat note-centric format instead of PheBee format (default: PheBee format)",
    )
    parser.add_argument(
        "--project-id",
        help="PheBee project identifier (required unless --flat-format specified)",
    )
    parser.add_argument(
        "--subject-id-column",
        default="subject_id",
        help="Input column name for subject ID (default: subject_id)",
    )
    parser.add_argument(
        "--batch-id",
        type=int,
        default=0,
        help="PheBee batch identifier (default: 0)",
    )


def _validate_phebee_output_arguments(parser: argparse.ArgumentParser, args) -> None:
    if not args.flat_format and not args.project_id:
        parser.error("--project-id is required unless --flat-format is specified")


# ----------------------------------------------------------------------------
# run
# ----------------------------------------------------------------------------


def build_run_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=_prog(),
        description="PheBee Local Extraction Orchestrator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Basic extraction (PheBee format - default)
  %(prog)s --input notes.jsonl --output results/ \\
    --docker-images fasthpocr:latest --project-id my-project

  # Tool chaining (extraction + context)
  %(prog)s --input notes.jsonl --output results/ \\
    --docker-images fasthpocr:latest,phenocontext:latest --project-id my-project

  # Flat format output (note-centric, no PheBee fields required)
  %(prog)s --input notes.jsonl --output results/ \\
    --docker-images fasthpocr:latest --flat-format

  # GPU-enabled extraction
  %(prog)s --input notes.parquet --output results/ \\
    --docker-images phenobert:latest --gpu --batch-size 500 --project-id my-project

Other commands (see `%(prog)s <command> --help`):
  split       write batch files for an external scheduler to run
  transform   convert tool output files into PheBee or flat JSONL
  review      show PheBee-format results next to the original notes
        """,
    )

    _add_input_arguments(parser)
    parser.add_argument("--output", required=True, help="Output directory")
    parser.add_argument(
        "--docker-images", required=True, help="Comma-separated list of Docker images for pipeline"
    )
    parser.add_argument(
        "--gpu", action="store_true", help="Enable GPU support (adds --gpus all flag)"
    )
    parser.add_argument(
        "--temp-dir",
        type=str,
        default=None,
        help="Temporary directory for batch files (default: {output}/.temp_batches_{timestamp})",
    )
    parser.add_argument(
        "--keep-temp", action="store_true", help="Keep temporary files after completion"
    )
    parser.add_argument(
        "--continue-on-error", action="store_true", help="Continue processing if a batch fails"
    )
    parser.add_argument(
        "--docker-timeout",
        type=int,
        default=3600,
        help="Maximum seconds per batch stage (default: 3600, 0=no timeout)",
    )
    _add_phebee_output_arguments(parser)
    return parser


def parse_arguments(argv: Optional[list[str]] = None) -> argparse.Namespace:
    """Parse and validate arguments for the full pipeline (the `run` command)"""
    parser = build_run_parser()
    args = parser.parse_args(argv)

    if args.batch_size < 1:
        parser.error("--batch-size must be >= 1")

    if args.docker_timeout < 0:
        parser.error("--docker-timeout must be >= 0")

    _validate_phebee_output_arguments(parser, args)

    return args


def run_command(args: argparse.Namespace) -> int:
    """Full local pipeline"""
    # Setup output directory
    output_dir = args.output
    os.makedirs(output_dir, exist_ok=True)

    # Setup temp directory
    temp_dir = temp_dir_for(output_dir, args.temp_dir)

    # Parse docker images
    docker_images = [img.strip() for img in args.docker_images.split(",")]

    # Create execution context
    ctx = ExecutionContext(
        input_file=args.input,
        output_dir=output_dir,
        docker_images=docker_images,
        batch_size=args.batch_size,
        gpu=args.gpu,
        temp_dir=temp_dir,
        keep_temp=args.keep_temp,
        continue_on_error=args.continue_on_error,
        docker_timeout=args.docker_timeout,
        flat_format=args.flat_format,
        project_id=args.project_id,
        subject_id_column=args.subject_id_column,
        batch_id=args.batch_id,
    )

    # Initialize the stage runner (local Docker)
    logger.info("Initializing Docker client...")
    ctx.runner = DockerRunner(gpu=args.gpu, timeout=args.docker_timeout)
    ctx.docker_image_digests = ctx.runner.prepare(ctx.docker_images)

    # Load input data
    records = load_input_data(
        args.input,
        format=args.input_format,
        id_column=args.id_column,
        text_column=args.text_column,
    )
    validate_input_records(records)
    ctx.total_records = len(records)

    # Create batches
    ctx.batches = create_batches(records, args.batch_size, temp_dir)

    # Process batches
    logger.info(
        f"Processing {len(ctx.batches)} batches with {len(docker_images)}-stage pipeline..."
    )
    progress = ProgressTracker(len(ctx.batches), len(docker_images))

    for batch in ctx.batches:
        try:
            run_batch_pipeline(ctx, batch, progress)
        except BatchProcessingError as e:
            logger.error(str(e))
            if not args.continue_on_error:
                progress.close()
                cleanup_temp_files(temp_dir, args.keep_temp)
                return 1

    progress.close()

    # Aggregate results
    output_file = os.path.join(output_dir, "aggregated.jsonl")
    aggregate_results(ctx, output_file)

    # Cleanup
    cleanup_temp_files(temp_dir, args.keep_temp)

    # Generate and display summary
    summary = generate_summary_report(ctx, output_file)
    print(summary)

    # Exit code
    return 1 if ctx.has_failures() else 0


# ----------------------------------------------------------------------------
# split
# ----------------------------------------------------------------------------


def build_split_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=f"{_prog()} split",
        description="Load clinical notes and write batch files (plus manifest.json) for an "
        "external scheduler to run through the extraction containers.",
    )
    _add_input_arguments(parser)
    parser.add_argument("--output-dir", required=True, help="Directory to write batch files into")
    return parser


def parse_split_arguments(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = build_split_parser()
    args = parser.parse_args(argv)
    if args.batch_size < 1:
        parser.error("--batch-size must be >= 1")
    return args


def split_command(args: argparse.Namespace) -> int:
    records = load_input_data(
        args.input,
        format=args.input_format,
        id_column=args.id_column,
        text_column=args.text_column,
    )
    validate_input_records(records)

    batches = create_batches(records, args.batch_size, args.output_dir)
    manifest_path = write_manifest(batches, args.output_dir, len(records), args.batch_size)

    print(f"Wrote {len(batches)} batch files ({len(records)} records) to {args.output_dir}")
    print(f"Manifest: {manifest_path}")
    return 0


# ----------------------------------------------------------------------------
# transform
# ----------------------------------------------------------------------------


def build_transform_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=f"{_prog()} transform",
        description="Convert one or more tool output files (INTERFACE.md format) into PheBee "
        "bulk-import JSONL, or flat note-centric JSONL with --flat-format.",
    )
    parser.add_argument(
        "--input",
        required=True,
        nargs="+",
        help="Tool output JSON file(s), in the order they should appear in the output",
    )
    parser.add_argument(
        "--original-input",
        nargs="+",
        help="Batch input file(s) matching --input, one per output, in the same order. "
        "Fields the tool dropped (subject_id, encounter_id, ...) are merged back from these.",
    )
    parser.add_argument("--output", required=True, help="Output JSONL file")
    parser.add_argument(
        "--image-ref",
        nargs="+",
        default=[],
        help="Image reference(s) for the tool(s) that produced the output, in pipeline order, "
        "ideally with digests (name:tag@sha256:...). Used for evidence creator fields "
        "(PheBee format) and tools_used (flat format). Required unless --flat-format.",
    )
    parser.add_argument(
        "--start-row-num",
        type=int,
        default=1,
        help="First row_num to assign (default: 1). Use when writing one part file per batch.",
    )
    _add_phebee_output_arguments(parser)
    return parser


def parse_transform_arguments(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = build_transform_parser()
    args = parser.parse_args(argv)
    _validate_phebee_output_arguments(parser, args)
    if not args.flat_format and not args.image_ref:
        parser.error("--image-ref is required unless --flat-format is specified")
    if args.original_input and len(args.original_input) != len(args.input):
        parser.error("--original-input must list one file per --input, in the same order")
    if args.start_row_num < 1:
        parser.error("--start-row-num must be >= 1")
    return args


def transform_command(args: argparse.Namespace) -> int:
    outputs = [read_batch_output(path) for path in args.input]

    if args.original_input:
        for output, original_path in zip(outputs, args.original_input):
            with open(original_path) as f:
                original_records = json.load(f)["records"]
            merge_original_records(original_records, output["records"])

    if args.flat_format:
        write_flat_jsonl(outputs, args.image_ref, args.output)
    else:
        config = PheBeeConfig(
            project_id=args.project_id,
            subject_id_column=args.subject_id_column,
            batch_id=args.batch_id,
            image_refs=args.image_ref,
        )
        write_phebee_jsonl(outputs, config, args.output, start_row_num=args.start_row_num)

    return 0


# ----------------------------------------------------------------------------
# review
# ----------------------------------------------------------------------------


def build_review_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=f"{_prog()} review",
        description="Show PheBee-format results next to the original notes: each note, then "
        "the text matched for every term, with its span and context flags. Reads the results "
        "of `run` (aggregated.jsonl) and the same input file that was extracted.",
    )
    parser.add_argument(
        "--results", required=True, help="PheBee-format results file (aggregated.jsonl)"
    )
    _add_input_arguments(parser, batch_size=False)
    parser.add_argument(
        "--subject-id-column",
        default="subject_id",
        help="Input column for subject ID, used for notes with no results (default: subject_id)",
    )
    parser.add_argument(
        "--note-id", nargs="+", help="Only show these notes (default: all, in input order)"
    )
    parser.add_argument("--limit", type=int, help="Show at most this many notes")
    return parser


def parse_review_arguments(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = build_review_parser()
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be >= 1")
    return args


def review_command(args: argparse.Namespace) -> int:
    notes = load_input_data(
        args.input,
        format=args.input_format,
        id_column=args.id_column,
        text_column=args.text_column,
    )
    validate_input_records(notes)
    matches_by_note = read_results(args.results)

    missing = unmatched_note_ids(notes, matches_by_note)
    if missing:
        logger.warning(
            f"{len(missing)} note(s) in the results are not in --input and cannot be shown "
            f"(is this the file that was extracted?): {', '.join(missing[:5])}"
            + (" ..." if len(missing) > 5 else "")
        )

    if args.note_id:
        wanted = set(args.note_id)
        absent = sorted(wanted - {str(n["note_id"]) for n in notes})
        if absent:
            logger.warning(f"--note-id not found in --input: {', '.join(absent)}")
        notes = [n for n in notes if str(n["note_id"]) in wanted]
    if args.limit is not None:
        notes = notes[: args.limit]

    for line in format_review(notes, matches_by_note, args.subject_id_column):
        print(line)
    return 0


# ----------------------------------------------------------------------------
# dispatch
# ----------------------------------------------------------------------------


def main(argv: Optional[list[str]] = None) -> int:
    """Main entry point"""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

    if argv is None:
        argv = sys.argv[1:]

    # `run` is the default command so the original invocation keeps working
    if argv and argv[0] in SUBCOMMANDS:
        command, rest = argv[0], argv[1:]
    else:
        command, rest = "run", argv

    try:
        if command == "split":
            return split_command(parse_split_arguments(rest))
        if command == "transform":
            return transform_command(parse_transform_arguments(rest))
        if command == "review":
            return review_command(parse_review_arguments(rest))
        return run_command(parse_arguments(rest))

    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
