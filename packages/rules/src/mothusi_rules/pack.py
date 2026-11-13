"""Load curriculum facts and check programme-of-assessment weights."""

import tomllib
from decimal import Decimal
from pathlib import Path

from mothusi_contracts import CheckResult, SubjectPack

PACK_WEIGHTS = "pack_weights"


def load_pack(path: Path) -> SubjectPack:
    with path.open("rb") as source:
        return SubjectPack.model_validate(tomllib.load(source))


def check_pack_weights(pack: SubjectPack) -> CheckResult:
    total = sum((task.weight_percent for task in pack.poa), Decimal(0))
    tasks = "; ".join(
        f"{task.name} (term {task.term}): {task.weight_percent}%" for task in pack.poa
    )
    return CheckResult(
        check=PACK_WEIGHTS,
        about=f"{pack.pack_id}/{pack.subject}/gr{pack.grade}",
        passed=total == 100,
        detail=f"PoA weights add up to {total}%: {tasks}",
    )
