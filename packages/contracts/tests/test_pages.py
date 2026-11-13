"""Page-reader contracts follow intake.md §11.3 and §11.6."""

import pytest
from pydantic import ValidationError

from mothusi_contracts import Answer, Contract, Script


def test_existing_answers_and_scripts_still_load(script: Script) -> None:
    restored = Script.model_validate_json(script.model_dump_json())
    assert restored == script
    for answer in restored.answers:
        assert answer.uncertain_lines == ()
        assert answer.unreadable is False
    assert Answer.model_validate({"question_id": "1", "text": ""}).text == ""


def test_uncertain_indices_identify_duplicate_and_blank_lines() -> None:
    answer = Answer(question_id="1", text="x\n\nx\ny", uncertain_lines=(0, 1, 2))
    assert answer.uncertain_lines == (0, 1, 2)
    assert Answer.model_validate_json(answer.model_dump_json()) == answer
    assert Answer(question_id="1", text="", uncertain_lines=(0,)).uncertain_lines == (0,)


@pytest.mark.parametrize("indices", [(-1,), (3,), (0, 0), (2, 0)])
def test_invalid_uncertain_indices_are_refused(indices: tuple[int, ...]) -> None:
    with pytest.raises(ValidationError, match="uncertain_lines"):
        Answer(question_id="1", text="x\n\nx", uncertain_lines=indices)


def test_indices_follow_their_lines_when_blank_ends_are_stripped() -> None:
    # The reader flagged "x" (line 1); stripping the leading blank line must not move the flag
    # onto "y".
    answer = Answer(question_id="1", text="\nx\ny", uncertain_lines=(1,))
    assert answer.text == "x\ny"
    assert answer.uncertain_lines == (0,)
    assert answer.text.split("\n")[answer.uncertain_lines[0]] == "x"


def test_a_flag_on_a_stripped_blank_end_line_is_dropped_not_refused() -> None:
    answer = Answer(question_id="1", text="x\n\n", uncertain_lines=(0, 2))
    assert (answer.text, answer.uncertain_lines) == ("x", (0,))


def test_an_index_past_the_text_is_still_refused() -> None:
    with pytest.raises(ValidationError, match="uncertain_lines"):
        Answer(question_id="1", text="x\ny", uncertain_lines=(2,))


def test_unreadable_answer_is_empty_and_has_no_uncertain_lines() -> None:
    answer = Answer(question_id="1", text="", unreadable=True)
    assert answer.unreadable is True
    assert answer.uncertain_lines == ()
    assert Answer.model_validate_json(answer.model_dump_json()) == answer


@pytest.mark.parametrize("text,indices", [("x", ()), ("", (0,)), ("x", (0,))])
def test_unreadable_answer_refuses_text_or_lines(text: str, indices: tuple[int, ...]) -> None:
    with pytest.raises(ValidationError, match="unreadable"):
        Answer(question_id="1", text=text, uncertain_lines=indices, unreadable=True)


def test_transcription_keeps_line_order_uncertainty_and_blank_lines() -> None:
    from mothusi_contracts import TranscribedLine, Transcription

    first = TranscribedLine(text="x = 2", uncertain=False)
    blank = TranscribedLine(text="", uncertain=True)
    transcription = Transcription(lines=(first, blank, first))
    assert isinstance(first, Contract)
    assert isinstance(transcription, Contract)
    assert transcription.lines == (first, blank, first)
    assert Transcription.model_validate_json(transcription.model_dump_json()) == transcription
    assert Transcription(lines=()).lines == ()
    with pytest.raises(ValidationError, match="uncertain"):
        TranscribedLine.model_validate({"text": "x"})
    with pytest.raises(ValidationError, match="lines"):
        Transcription.model_validate({})


def test_page_image_and_crop_have_the_designed_fields() -> None:
    from mothusi_contracts import AnswerCrop, PageImage

    page = PageImage(
        assessment_id="a", learner_pseudonym="L-014", page_no=1, source_key="pages/a/014.png"
    )
    crop = AnswerCrop(learner_pseudonym="L-014", question_id="1.1", page_no=1, png=b"\x89PNG")
    assert isinstance(page, Contract)
    assert isinstance(crop, Contract)
    assert page.model_dump() == {
        "assessment_id": "a",
        "learner_pseudonym": "L-014",
        "page_no": 1,
        "source_key": "pages/a/014.png",
    }
    assert crop.model_dump() == {
        "learner_pseudonym": "L-014",
        "question_id": "1.1",
        "page_no": 1,
        "png": b"\x89PNG",
    }
    assert PageImage.model_validate_json(page.model_dump_json()) == page
    assert AnswerCrop.model_validate(crop.model_dump()) == crop
    with pytest.raises(ValidationError, match="frozen"):
        PageImage.model_validate(page.model_dump()).page_no = 2  # type: ignore[misc]
    with pytest.raises(ValidationError, match="extra"):
        AnswerCrop.model_validate({**crop.model_dump(), "name": "forbidden"})


@pytest.mark.parametrize("page_no", [0, -1])
def test_page_numbers_start_at_one(page_no: int) -> None:
    from mothusi_contracts import AnswerCrop, PageImage

    with pytest.raises(ValidationError, match="page_no"):
        PageImage(assessment_id="a", learner_pseudonym="L", page_no=page_no, source_key="page")
    with pytest.raises(ValidationError, match="page_no"):
        AnswerCrop(learner_pseudonym="L", question_id="1", page_no=page_no, png=b"png")


@pytest.mark.parametrize("action", ["confirm", "correct"])
def test_reading_answer_targets_a_line(action: str) -> None:
    from mothusi_contracts import ReadingAnswer

    answer = ReadingAnswer.model_validate(
        {
            "kind": "reading",
            "script_id": "s",
            "question_id": "1",
            "line_no": 0,
            "action": action,
        }
    )
    assert isinstance(answer, Contract)
    assert answer.line_no == 0
    assert answer.text == ""
    assert ReadingAnswer.model_validate_json(answer.model_dump_json()) == answer
    corrected = ReadingAnswer(
        kind="reading",
        script_id="s",
        question_id="1",
        line_no=2,
        action="correct",
        text="x = 2\nx = 3",
    )
    assert corrected.text == "x = 2\nx = 3"


@pytest.mark.parametrize("text", ["", "x = 2\nx = 3"])
def test_unreadable_reading_answer_allows_blank_or_multiline_correction(text: str) -> None:
    from mothusi_contracts import ReadingAnswer

    answer = ReadingAnswer(
        kind="reading", script_id="s", question_id="1", line_no=None, action="correct", text=text
    )
    assert answer.line_no is None
    assert answer.text == text


@pytest.mark.parametrize(
    "change",
    [
        {"line_no": None, "action": "confirm"},
        {"line_no": -1},
        {"kind": "memo"},
        {"action": "skip"},
    ],
)
def test_reading_answer_refuses_invalid_decisions(change: dict[str, object]) -> None:
    from mothusi_contracts import ReadingAnswer

    with pytest.raises(ValidationError):
        ReadingAnswer.model_validate(
            {
                "kind": "reading",
                "script_id": "s",
                "question_id": "1",
                "line_no": 0,
                "action": "correct",
                **change,
            }
        )


def test_reading_answer_requires_explicit_line_number_or_none() -> None:
    from mothusi_contracts import ReadingAnswer

    with pytest.raises(ValidationError, match="line_no"):
        ReadingAnswer.model_validate(
            {
                "kind": "reading",
                "script_id": "s",
                "question_id": "1",
                "action": "correct",
            }
        )
