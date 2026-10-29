"""
Review PheBee-format results against the original notes.

PheBee records carry only a note id and character offsets, so reading them next to the text
means joining them back to the input notes. This module does that join and formats each note
followed by what was matched in it.
"""

import json
import logging
from collections import defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class Match:
    """One extracted term in one note, from one PheBee evidence item"""

    start: int
    end: int
    term: str  # short form of the term IRI, e.g. HP_0001945
    contexts: dict[str, Any]
    subject: Optional[str]
    row_num: int


def read_results(path: str) -> dict[str, list[Match]]:
    """
    Read a PheBee-format JSONL file and index its matches by note id.

    Raises ValueError for a line that is not a PheBee-format record, for example the
    output of --flat-format, instead of silently showing no matches.
    """
    matches: dict[str, list[Match]] = defaultdict(list)

    with open(path) as f:
        for line_number, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            if "evidence" not in record or "term_iri" not in record:
                raise ValueError(
                    f"{path} line {line_number} is not a PheBee-format record. "
                    "`review` reads PheBee-format output; flat-format output (--flat-format) "
                    "already contains the note text and annotations."
                )
            for evidence in record["evidence"]:
                matches[str(evidence["clinical_note_id"])].append(
                    Match(
                        start=evidence["span_start"],
                        end=evidence["span_end"],
                        term=record["term_iri"].rsplit("/", 1)[-1],
                        contexts=evidence.get("contexts") or {},
                        subject=record.get("project_subject_id"),
                        row_num=record.get("row_num", 0),
                    )
                )

    for note_matches in matches.values():
        note_matches.sort(key=lambda m: (m.start, m.end, m.row_num))
    return dict(matches)


def _format_contexts(contexts: dict[str, Any]) -> str:
    return " ".join(f"{key}={json.dumps(value)}" for key, value in contexts.items())


def format_review(
    notes: Iterable[dict[str, Any]],
    matches_by_note: dict[str, list[Match]],
    subject_id_column: str = "subject_id",
) -> Iterator[str]:
    """
    Yield the review output line by line: each note, then its matches, then a blank line.

    Notes are shown in the order given, including notes with no matches. The subject comes
    from the results when the note has any, otherwise from the note's subject_id_column.
    """
    for note in notes:
        note_id = str(note["note_id"])
        text = note["text"] if isinstance(note["text"], str) else ""
        note_matches = matches_by_note.get(note_id, [])

        subject = note_matches[0].subject if note_matches else note.get(subject_id_column)
        header = f"{note_id} ({subject}): {text}" if subject else f"{note_id}: {text}"
        yield header

        for match in note_matches:
            matched_text = text[match.start : match.end]
            line = f'    [{match.start}:{match.end}] "{matched_text}"  {match.term}'
            if match.contexts:
                line += f"  {_format_contexts(match.contexts)}"
            yield line

        yield ""


def unmatched_note_ids(
    notes: Iterable[dict[str, Any]], matches_by_note: dict[str, list[Match]]
) -> list[str]:
    """Note ids that appear in the results but not in the given notes"""
    known = {str(note["note_id"]) for note in notes}
    return sorted(note_id for note_id in matches_by_note if note_id not in known)
