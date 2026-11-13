"""Deterministic Accounting operations.

The first tests use made-up inputs. The tests after them take their figures and caps from the
DBE NSC November 2025 Accounting marking guidelines (preparation/docs/sources/dbe/exams/, question
and page cited on each case). The cap belongs to the memo question: passed in, never in a pack.
"""

from decimal import Decimal
from typing import Literal

import pytest

from mothusi_contracts import LedgerLine
from mothusi_rules import foreign_entry_penalty, one_part_correct


def line(scored: int, foreign: bool = True) -> LedgerLine:
    return LedgerLine(line_id="l", description="test entry", foreign=foreign, marks_scored=scored)


def test_foreign_line_worked_example() -> None:
    lines = (line(2), line(0), line(1), line(10, False))
    assert foreign_entry_penalty(lines, 2) == 2
    assert foreign_entry_penalty(lines, 5) == 2
    assert foreign_entry_penalty((*lines, line(3)), 5) == 3


def test_penalty_caps_and_empty() -> None:
    assert foreign_entry_penalty((line(5), line(4)), 1) == 1
    assert foreign_entry_penalty((line(5),), 0) == 0
    assert foreign_entry_penalty((), 10) == 0
    assert foreign_entry_penalty((line(0), line(10, False)), 10) == 0
    with pytest.raises(ValueError):
        foreign_entry_penalty((), -1)


@pytest.mark.parametrize(
    ("operation", "parts", "expected", "passed"),
    [
        ("sum", ("10", "3", "2"), "15", True),
        ("difference", ("10", "3", "2"), "5", True),
        ("sum", ("10", "-3"), "7.005", True),
        ("sum", ("10", "-3"), "6.995", True),
        ("sum", ("10", "-3"), "7.0051", False),
        ("difference", ("10", "3"), "6.9949", False),
        ("difference", ("-10", "-3"), "-7", True),
        ("difference", ("4",), "4", True),
        ("sum", ("4",), "4", True),
        ("sum", ("10", "3"), "99", False),
    ],
)
def test_operations(
    operation: Literal["sum", "difference"], parts: tuple[str, ...], expected: str, passed: bool
) -> None:
    assert one_part_correct(Decimal(expected), tuple(map(Decimal, parts)), operation) is passed


@pytest.mark.parametrize("operation", ["sum", "difference"])
def test_empty_parts(operation: Literal["sum", "difference"]) -> None:
    with pytest.raises(ValueError):
        one_part_correct(Decimal(0), (), operation)


# --------------------------------------------------------------- cases from the Nov 2025 NSC memos


def rands(*figures: str) -> tuple[Decimal, ...]:
    return tuple(Decimal(f.replace(" ", "")) for f in figures)


@pytest.mark.parametrize(
    ("where", "parts", "operation", "answer"),
    [
        # P2 Q2.2.1, p 6: closing stock, specific identification; the memo's second route.
        ("P2 2.2.1", ("465 000", "1 395 000", "-1 224 500"), "sum", "635 500"),
        # P2 Q4.1.2(i), p 11: bad debts, "1 417 500 - 1 332 450".
        ("P2 4.1.2(i)", ("1 417 500", "1 332 450"), "difference", "85 050"),
        # P2 Q4.1.3, p 11: cash purchases of trading stock, "1 268 750 - 1 015 000".
        ("P2 4.1.3", ("1 268 750", "1 015 000"), "difference", "253 750"),
        # P1 Q1.3, p 3: mortgage loan, non-current part, "(3 200 000 + 290 000 - 540 000)".
        ("P1 1.3 loan", ("3 200 000", "290 000", "-540 000"), "sum", "2 950 000"),
        # P1 Q1.3, p 3: trade and other payables, "(878 500 + 5 000 + 77 000)".
        ("P1 1.3 payables", ("878 500", "5 000", "77 000"), "sum", "960 500"),
        # P1 Q1.3, p 3: current portion of loan, "(540 000 - 290 000)".
        ("P1 1.3 current portion", ("540 000", "290 000"), "difference", "250 000"),
        # P2 Q1.2.1, p 4: creditors' ledger, 270 140 - 3 600 - 32 400 + 1 410.
        ("P2 1.2.1 ledger", ("270 140", "-3 600", "-32 400", "1 410"), "sum", "235 550"),
        # P2 Q1.2.1, p 4: statement of account, 287 600 + 60 000 + 42 000 - 154 380 + 330.
        (
            "P2 1.2.1 statement",
            ("287 600", "60 000", "42 000", "-154 380", "330"),
            "sum",
            "235 550",
        ),
        # P2 Q1.1.2, p 3: one-column bank reconciliation, statement balance to account balance.
        (
            "P2 1.1.2",
            ("119 080", "41 850", "-7 650", "-10 800", "11 000", "-13 000"),
            "sum",
            "140 480",
        ),
    ],
)
def test_memo_workings_reproduce_the_memo_answer(
    where: str, parts: tuple[str, ...], operation: Literal["sum", "difference"], answer: str
) -> None:
    (expected,) = rands(answer)
    assert one_part_correct(expected, rands(*parts), operation), where
    assert not one_part_correct(expected + 1, rands(*parts), operation), where


@pytest.mark.parametrize(
    ("where", "multiplied", "parts", "operation"),
    [
        ("P2 2.2.1", Decimal(41) * 15500, ("465 000", "1 395 000", "-1 224 500"), "sum"),
        ("P2 4.1.2(i)", Decimal(1417500) * 6 / 100, ("1 417 500", "1 332 450"), "difference"),
        ("P2 4.1.3", Decimal(2030000) * 20 / 160, ("1 268 750", "1 015 000"), "difference"),
    ],
)
def test_the_memos_two_routes_agree(
    where: str, multiplied: Decimal, parts: tuple[str, ...], operation: Literal["sum", "difference"]
) -> None:
    """Each answer is printed with an 'OR': the multiplication route and the subtraction route."""
    assert one_part_correct(multiplied, rands(*parts), operation), where


def test_a_learner_whose_figures_do_not_give_their_answer_earns_no_method_mark() -> None:
    """P2 Q1.1.2, p 3: a learner who writes 140 480 but left out the incorrect EFT of 11 000."""
    parts = rands("119 080", "41 850", "-7 650", "-10 800", "-13 000")
    assert not one_part_correct(Decimal(140480), parts, "sum")
    assert one_part_correct(Decimal(129480), parts, "sum")  # their own figures, their own total


def entries(*scored: int) -> tuple[LedgerLine, ...]:
    return tuple(
        LedgerLine(line_id=f"f{i}", description="foreign entry", foreign=True, marks_scored=s)
        for i, s in enumerate(scored)
    )


def test_cash_journals_cap_foreign_entries_at_one() -> None:
    """P2 Q1.1.1, p 2: "-1 foreign (max -1) (only if marks are scored elsewhere for the same
    figure)"."""
    assert foreign_entry_penalty(entries(1, 1, 1), cap=1) == 1
    assert foreign_entry_penalty(entries(0, 0), cap=1) == 0


@pytest.mark.parametrize("where", ["P2 1.1.2 (p 2-3)", "P1 1.3 (p 3)"])
def test_reconciliation_and_position_statement_cap_foreign_items_at_two(where: str) -> None:
    """P2 Q1.1.2 prints "-1 for foreign items (max -2) if marks awarded elsewhere"; P1 Q1.3 prints
    "-1 foreign items (max -2)", and marking principle 1 supplies the same condition."""
    assert foreign_entry_penalty(entries(1, 2, 1), cap=2) == 2, where
    assert foreign_entry_penalty(entries(1, 0), cap=2) == 1, where
    assert foreign_entry_penalty(entries(0, 0, 0), cap=2) == 0, where


def test_creditors_reconciliation_charges_each_scored_foreign_line() -> None:
    """P2 Q1.2.1, p 4: "Foreign entries, -1 per line if a mark has been scored on that line"; the
    memo states no maximum, so the question's 11 marks are passed as the cap (principle 9 keeps the
    section from going negative)."""
    lines = (
        LedgerLine(
            line_id="i", description="(i)", amount=Decimal(-3600), foreign=False, marks_scored=2
        ),
        LedgerLine(
            line_id="iii",
            description="(iii)",
            amount=Decimal(-32400),
            foreign=False,
            marks_scored=2,
        ),
        *entries(1, 0, 2),
    )
    assert foreign_entry_penalty(lines, cap=11) == 2
