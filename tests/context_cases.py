"""
Expected context flags for every note text used as test data.

One source of truth, shared by the unit tests (tests/test_mock_context.py) and the
Docker chain test (tests/test_integration.py). The expectations are what a careful reader
would say about each sentence, not a record of what the mock currently outputs.

Each case is (text, expected), where expected lists, in order of position in the text,
(matched_text, negated, family, hypothetical) for every term the mock extractor finds.
"""

# Notes in examples/ and tests/fixtures/, plus every note text used inline by the tests.
CASES = [
    # --- examples/sample_notes*.jsonl ---
    (
        "Patient presents with fever and cough. Denies chest pain.",
        # "Denies" is in the next sentence, so it does not negate fever or cough; it does
        # negate chest pain.
        [("fever", 0, 0, 0), ("cough", 0, 0, 0), ("chest pain", 1, 0, 0)],
    ),
    (
        "Patient admitted with pneumonia and requires supplemental oxygen.",
        [("pneumonia", 0, 0, 0)],
    ),
    (
        "Chronic headaches, possibly migraine. Family history of seizures.",
        # "possibly" qualifies migraine only; "Family history" is the next sentence.
        [("headaches", 0, 0, 0), ("migraine", 0, 0, 1), ("seizures", 0, 1, 0)],
    ),
    (
        "Developmental delay noted at 18 months. Walking unsteady.",
        [("Developmental delay", 0, 0, 0), ("Walking unsteady", 0, 0, 0)],
    ),
    (
        "Recurrent ear infections. No hearing loss detected.",
        # "No" is in the second sentence: it negates hearing loss, not the infections.
        [("Recurrent ear infections", 0, 0, 0), ("hearing loss", 1, 0, 0)],
    ),
    # --- tests/fixtures/sample_input.json ---
    (
        "Patient presents with fever and cough.",
        [("fever", 0, 0, 0), ("cough", 0, 0, 0)],
    ),
    (
        "Chronic headaches, family history of migraines.",
        # "family history" comes after headaches, so it only applies to migraines.
        [("headaches", 0, 0, 0), ("migraines", 0, 1, 0)],
    ),
    (
        "Developmental delay noted at 18 months.",
        [("Developmental delay", 0, 0, 0)],
    ),
    # --- notes written inline in tests/test_integration.py and tests/test_extract.py ---
    ("Patient presents with fever.", [("fever", 0, 0, 0)]),
    ("Chronic headaches noted.", [("headaches", 0, 0, 0)]),
    ("Developmental delay at 18 months.", [("Developmental delay", 0, 0, 0)]),
    ("Recurrent ear infections.", [("Recurrent ear infections", 0, 0, 0)]),
    ("Family history of migraines.", [("migraines", 0, 1, 0)]),
    ("Patient has fever", [("fever", 0, 0, 0)]),
    ("Patient has fever and cough.", [("fever", 0, 0, 0), ("cough", 0, 0, 0)]),
    ("No cough reported", [("cough", 1, 0, 0)]),
    (
        "Family history of migraines. Patient has chronic headaches.",
        # The family-history sentence ends before headaches.
        [("migraines", 0, 1, 0), ("headaches", 0, 0, 0)],
    ),
    (
        "Suspected pneumonia, rule out tuberculosis.",
        [("pneumonia", 0, 0, 1)],  # tuberculosis is not a term the mock extractor finds
    ),
    (
        "Mother had seizures. Patient developmentally normal.",
        [("seizures", 0, 1, 0)],
    ),
    (
        "May have fever, possible infection.",
        [("fever", 0, 0, 1)],
    ),
]

# Cases that exercise the mechanics (scope, multi-word cues, sentence splitting) rather
# than appearing in any sample data.
EDGE_CASES = [
    # a scope-breaking word ends a cue's reach
    (
        "No fever but cough present.",
        [("fever", 1, 0, 0), ("cough", 0, 0, 0)],
    ),
    # a cue negates every term after it in the sentence, "and" does not end the scope
    (
        "Denies fever and cough.",
        [("fever", 1, 0, 0), ("cough", 1, 0, 0)],
    ),
    # multi-word and abbreviated cues
    ("Rule out pneumonia.", [("pneumonia", 0, 0, 1)]),
    ("R/O seizures.", [("seizures", 0, 0, 1)]),
    # a decimal point is not a sentence break
    ("Temp 98.6, no fever.", [("fever", 1, 0, 0)]),
    # newlines and semicolons separate sentences
    ("No fever\nCough noted", [("fever", 1, 0, 0), ("Cough", 0, 0, 0)]),
    ("No fever; cough noted", [("fever", 1, 0, 0), ("cough", 0, 0, 0)]),
    # several contexts can apply to one term
    (
        "Possible family history of seizures.",
        [("seizures", 0, 1, 1)],
    ),
    # cue words only count as whole words: "not" inside "notable" is not a negation
    ("Patient has a notable fever.", [("fever", 0, 0, 0)]),
]
