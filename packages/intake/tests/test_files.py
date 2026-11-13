"""Teacher files become validated, pseudonymised contracts without losing answer text."""

import csv
from pathlib import Path

import pytest

from mothusi_intake import load_memo, load_scripts


def csv_file(path: Path, rows: list[tuple[str, str, str]]) -> Path:
    with path.open("w", newline="", encoding="utf-8") as out:
        writer = csv.writer(out)
        writer.writerow(("learner", "question_id", "answer"))
        writer.writerows(rows)
    return path


def test_class_order_noncontiguous_rows_and_missing_answers(tmp_path: Path) -> None:
    path = csv_file(
        tmp_path / "scripts.csv",
        [
            ("  Test   A ", " q1 ", "one"),
            ("Test B", "q1", "two"),
            ("Test A", "q2", "three"),
            ("Test C", "q2", "four"),
        ],
    )
    scripts, mapping = load_scripts(path)
    assert mapping == {"Test A": "L-001", "Test B": "L-002", "Test C": "L-003"}
    assert [s.script_id for s in scripts] == ["L-001", "L-002", "L-003"]
    assert all(s.script_id == s.learner_pseudonym for s in scripts)
    assert [a.question_id for a in scripts[0].answers] == ["q1", "q2"]
    assert [a.question_id for a in scripts[1].answers] == ["q1"]
    for script in scripts:
        assert all(name not in script.model_dump_json() for name in mapping)


def test_quotes_unicode_and_internal_whitespace(tmp_path: Path) -> None:
    answer = '  x = 2, "shown"\n so   y=3  '
    scripts, _ = load_scripts(
        csv_file(tmp_path / "s.csv", [("Test learner", "q", answer), ("Test learner", "q2", "")])
    )
    # Ends stripped by the contract; the learner's own spacing and line break inside kept.
    assert scripts[0].answers[0].text == 'x = 2, "shown"\n so   y=3'
    assert scripts[0].answers[1].text == ""


@pytest.mark.parametrize(
    ("text", "row", "reason"),
    [
        ("learner,question_id\nA,q\n", 1, "answer"),
        ("learner,question_id,answer\n ,q,a\n", 2, "learner"),
        ("learner,question_id,answer\nA, ,a\n", 2, "question_id"),
        ("learner,question_id,answer\nA,q,a\n A , q ,b\n", 3, "duplicate"),
        ("learner,question_id,answer\nA,q\n", 2, "answer"),
        ("learner,question_id,answer\nA,q,a,extra\n", 2, "column"),
    ],
)
def test_row_errors(tmp_path: Path, text: str, row: int, reason: str) -> None:
    path = tmp_path / "bad.csv"
    path.write_text(text)
    with pytest.raises(ValueError, match=f"row {row}:.*{reason}"):
        load_scripts(path)


def test_empty_class(tmp_path: Path) -> None:
    assert load_scripts(csv_file(tmp_path / "empty.csv", [])) == ((), {})


def memo_file(tmp_path: Path, total: int = 4) -> Path:
    path = tmp_path / "memo.toml"
    path.write_text(
        f'assessment_id="test"\ntotal_marks={total}\n[[item]]\n'
        'question_id="1"\nmax_marks=4\nanswer="x=3"\nlevel="knowledge"\n'
    )
    return path


def test_memo_loads_optional_notes(tmp_path: Path) -> None:
    memo = load_memo(memo_file(tmp_path))
    assert memo.total_marks == 4 and memo.items[0].marking_notes == ""
    assert memo.items[0].question_id == "1"


def test_memo_validation_errors_unchanged(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="memo items add up to 4, not total_marks 5"):
        load_memo(memo_file(tmp_path, 5))
