"""Check decoded dispatch inputs for learner identity and first-mark contamination."""

import json
import unicodedata
from collections.abc import Collection, Sequence
from typing import cast

from mothusi_contracts import AgentRole, Brief, CheckResult, Contract

BLIND = "blind_brief"


def _normalise(text: str) -> str:
    """NFKC folds fullwidth and ligature forms; format characters (zero-width, soft hyphen) go."""
    folded = unicodedata.normalize("NFKC", text)
    visible = "".join(ch for ch in folded if unicodedata.category(ch) != "Cf")
    return " ".join(visible.split()).casefold()


def _decoded(text: str) -> object | None:
    """A string that is itself JSON (a serialised MarkedScript, say) is walked as data too."""
    stripped = text.strip()
    if not stripped or stripped[0] not in "{[":
        return None
    try:
        return json.loads(stripped)
    except ValueError:
        return None


def check_brief_blind(brief: Brief, names: Collection[str]) -> CheckResult:
    """Walk nested inputs without JSON encoding, including dictionary keys and models."""
    stack: list[object] = [brief.inputs]
    visited: set[int] = set()
    strings: set[str] = set()
    first_marks = 0
    while stack:
        value = stack.pop()
        if isinstance(value, str):
            strings.add(_normalise(value))
            inner = _decoded(value)
            if inner is not None:
                stack.append(inner)
            continue
        if isinstance(value, bytes):
            stack.append(value.decode("utf-8", errors="replace"))
            continue
        identity = id(value)
        if (
            not isinstance(value, (dict, list, tuple, set, frozenset, Contract))
            or identity in visited
        ):
            continue
        visited.add(identity)
        if isinstance(value, (list, tuple, set, frozenset)):
            stack.extend(cast(Sequence[object] | set[object] | frozenset[object], value))
            continue
        if isinstance(value, Contract):
            mapping: dict[object, object] = {
                field: getattr(value, field) for field in type(value).model_fields
            }
        else:
            mapping = cast(dict[object, object], value)
        if "marked" in mapping or {"marks", "total", "marker"} <= mapping.keys():
            first_marks += 1
        stack.extend(mapping.keys())
        stack.extend(mapping.values())
    failures: list[str] = []
    if brief.agent not in (AgentRole.WRITER, AgentRole.EXECUTOR):
        for name in names:
            normalised = _normalise(name)
            if len(normalised) >= 3 and any(normalised in text for text in strings):
                failures.append(f"learner name '{name}' appears in the brief")
    if brief.agent == AgentRole.MODERATOR and first_marks:
        failures.append(f"moderator brief carries first marks ({first_marks} objects)")
    return CheckResult(
        check=BLIND,
        about=brief.brief_id,
        passed=not failures,
        detail="; ".join(sorted(failures)) or "brief is blind",
    )
