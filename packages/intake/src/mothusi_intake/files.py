"""Load teacher memos and scripts, keeping learner identity outside agent contracts."""

import csv
import tomllib
from pathlib import Path

from mothusi_contracts import Answer, Memo, Script
from mothusi_intake.pseudonyms import pseudonymise


def load_memo(path: Path) -> Memo:
    """Validate the memo's paper-ordered item tables with the domain contract."""
    with path.open("rb") as source:
        data = tomllib.load(source)
    data["items"] = data.pop("item", [])
    return Memo.model_validate(data)


def load_scripts(path: Path) -> tuple[tuple[Script, ...], dict[str, str]]:
    """Group CSV answers in first-appearance order, then replace names with pseudonyms."""
    grouped: dict[str, list[Answer]] = {}
    seen: set[tuple[str, str]] = set()
    required = ("learner", "question_id", "answer")
    with path.open(encoding="utf-8", newline="") as source:
        reader = csv.DictReader(source, strict=True)
        headers = reader.fieldnames or []
        missing = [name for name in required if name not in headers]
        if missing:
            raise ValueError(f"row 1: missing header {', '.join(missing)}")
        if len(headers) != len(required) or set(headers) != set(required):
            raise ValueError("row 1: headers must be exactly learner, question_id, answer")
        try:
            for row in reader:
                number = reader.line_num
                if None in row:
                    raise ValueError(f"row {number}: too many columns")
                name = " ".join((row["learner"] or "").split())
                question = " ".join((row["question_id"] or "").split())
                if not name:
                    raise ValueError(f"row {number}: blank learner")
                if not question:
                    raise ValueError(f"row {number}: blank question_id")
                if row["answer"] is None:
                    raise ValueError(f"row {number}: missing answer column")
                pair = name, question
                if pair in seen:
                    raise ValueError(f"row {number}: duplicate learner/question_id pair")
                seen.add(pair)
                # The contract strips leading and trailing whitespace from every string (so a
                # quote and the answer it is checked against agree at the ends); internal
                # whitespace is kept exactly as the learner wrote it (intake.md §6).
                answer = Answer(question_id=question, text=row["answer"])
                grouped.setdefault(name, []).append(answer)
        except csv.Error as error:
            raise ValueError(f"row {reader.line_num}: invalid CSV: {error}") from error
    mapping = pseudonymise(tuple(grouped))
    scripts = tuple(
        Script(script_id=mapping[name], learner_pseudonym=mapping[name], answers=tuple(answers))
        for name, answers in grouped.items()
    )
    return scripts, mapping


def load_class_list(path: Path) -> tuple[str, ...]:
    """A class list CSV (header exactly `learner`) for pages mode (console-ui.md §11.5).

    Names keep the class-list order, which is the pseudonym order (pseudonymise refuses blank or
    duplicate names).
    """
    with path.open(encoding="utf-8", newline="") as source:
        reader = csv.DictReader(source, strict=True)
        if (reader.fieldnames or []) != ["learner"]:
            raise ValueError("row 1: the header must be exactly learner")
        names: list[str] = []
        for row in reader:
            if None in row:
                raise ValueError(f"row {reader.line_num}: too many columns")
            name = " ".join((row["learner"] or "").split())
            if not name:
                raise ValueError(f"row {reader.line_num}: blank learner")
            names.append(name)
    if not names:
        raise ValueError("the class list holds no learners")
    pseudonymise(names)  # refuses duplicates with its own message
    return tuple(names)
