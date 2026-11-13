"""Setter numeric equivalence: expressions, equations and finite solution sets."""

import pytest

from mothusi_rules import equivalent


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("x=3 or x=-2", "{-2, 3}"),
        ("x = 3; x = -2", "x ∈ {3, -2}"),
        ("0.5", "1/2"),
        ("y = 2x + 1", "y=1+2x"),
        ("x^2-5x+6", "(x-2)*(x-3)"),
        ("x^2-5x+6", "(x-2)(x-3)"),
        ("2\u00d73", "6"),
        ("6÷2", "3"),
        ("\u22122", "-2"),
        ("√(4)", "2"),
        ("1", "1.000000001"),
        ("sqrt(2)", "2^0.5"),
        ("{1/2, 2, 2}", "{0.5, 2}"),
        ("{}", "{}"),
    ],
)
def test_equivalent(a: str, b: str) -> None:
    assert equivalent(a, b)
    assert equivalent(b, a)


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("2x+1", "2x-1"),
        ("1", "1.000000002"),
        ("{1, 2}", "{1, 3}"),
        ("{1}", "{1, 2}"),
        ("x", "y"),
        ("{1}", "1"),
    ],
)
def test_different(a: str, b: str) -> None:
    assert not equivalent(a, b)


@pytest.mark.parametrize(
    "bad",
    [
        "???",
        "",
        "x=",
        "{1,}",
        "1/0",
        "sqrt(",
        '__import__("os")',
        "x.__class__",
        "[1,2]",
        "x=3 or y=2",
    ],
)
def test_parse_errors_name_side(bad: str) -> None:
    with pytest.raises(ValueError, match="a"):
        equivalent(bad, "1")
    with pytest.raises(ValueError, match="b"):
        equivalent("1", bad)


@pytest.mark.parametrize(("a", "b"), [("2e-3", "0.002"), ("2E3", "2000"), ("2 x", "x+x")])
def test_scientific_and_spaced_coefficients(a: str, b: str) -> None:
    assert equivalent(a, b)


@pytest.mark.parametrize(
    "hostile",
    ["9**9**9", "2**(10**9)", "sin(9**9**9)", "99999999999**99999999", "2**101", "x" + "+x" * 300],
)
def test_resource_exhaustion_is_refused_not_evaluated(hostile: str) -> None:
    """Sympy evaluates powers eagerly; unbounded, a six-character answer hangs the marker."""
    with pytest.raises(ValueError, match="a"):
        equivalent(hostile, "1")


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("2^0.5", "sqrt(2)"),
        ("x**2", "x*x"),
        ("2**(1/2)", "sqrt(2)"),
        ("10**-3", "0.001"),
        ("2**100", "1267650600228229401496703205376"),
    ],
)
def test_ordinary_exponents_still_work(a: str, b: str) -> None:
    assert equivalent(a, b)


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("3^(n-1)", "3**(n-1)"),
        ("2^(x+1)", "2*2^x"),
        ("x = 2 and y = 3", "y = 3, x = 2"),
        ("x <= 2", "2 >= x"),
        ("x < 2", "-x > -2"),
        ("x = 3 or x = -2", "x = -2 or x = 3"),
    ],
)
def test_memo_forms_a_grade_10_paper_uses(a: str, b: str) -> None:
    """The fixture memo had three answers equivalent could not read: a symbolic exponent, a system,
    an inequality."""
    assert equivalent(a, b) and equivalent(b, a)


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("x <= 2", "x < 2"),
        ("x = 2 and y = 3", "x = 3 and y = 2"),
        ("x = 2 and y = 3", "x = 2"),
        ("x <= 2", "x = 2"),
    ],
)
def test_different_memo_forms(a: str, b: str) -> None:
    assert not equivalent(a, b)


def test_exponent_bounds_still_hold() -> None:
    with pytest.raises(ValueError):
        equivalent("2**(10**9)", "1")
    with pytest.raises(ValueError):
        equivalent("2**(sin(1))", "1")


@pytest.mark.parametrize(
    "hostile",
    ["2**(100*100*100*100*100)", "2**(x - x + 100*100*100)", "2**(-(50*50))", "x = 2 and x = 3"],
)
def test_review_cases_folded_exponents_and_contradictions(hostile: str) -> None:
    """Codex review: constant subtrees in an exponent are folded and bounded; 'and' between two
    values of one variable is a contradiction, not a solution set."""
    with pytest.raises(ValueError, match="a"):
        equivalent(hostile, "1")


def test_semicolon_and_comma_still_read_as_solution_sets() -> None:
    assert equivalent("x = 3; x = -2", "{3, -2}") and equivalent("x = 3, x = -2", "{3, -2}")
    assert equivalent("2**(x + 1)", "2*2**x") and equivalent("2**(3*x)", "8**x")
