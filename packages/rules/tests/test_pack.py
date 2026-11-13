"""Contract acceptance tests for the delegated rules task."""

from pathlib import Path

import pytest

from mothusi_rules import PACK_WEIGHTS, check_pack_weights, load_pack

PACK = Path(__file__).resolve().parents[3] / "packs/dbe-caps/mathematics-gr10.toml"


def test_real_pack() -> None:
    pack = load_pack(PACK)
    result = check_pack_weights(pack)
    assert result.passed
    assert result.check == PACK_WEIGHTS == "pack_weights"
    assert result.about == "dbe-caps/Mathematics/gr10"


def test_wrong_weights_list_every_task(tmp_path: Path) -> None:
    text = PACK.read_text().replace("weight_percent = 14", "weight_percent = 15", 1)
    path = tmp_path / "pack.toml"
    path.write_text(text)
    pack = load_pack(path)
    result = check_pack_weights(pack)
    assert not result.passed
    assert "101" in result.detail
    for task in pack.poa:
        assert f"{task.name} (term {task.term}): {task.weight_percent}%" in result.detail


def test_missing_exam(tmp_path: Path) -> None:
    path = tmp_path / "bad.toml"
    text = PACK.read_text()
    start, rest = text.split("[exam]")
    path.write_text(start + "[cognitive]" + rest.split("[cognitive]", 1)[1])
    with pytest.raises(ValueError, match="exam"):
        load_pack(path)


def test_relative_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "pack.toml").write_text(PACK.read_text())
    monkeypatch.chdir(tmp_path)
    assert load_pack(Path("pack.toml")).grade == 10


def test_accounting_pack_carries_the_caps_grid() -> None:
    """CAPS FET Accounting §4.3 Table 1 (p 41): 30/40/30 across three levels."""
    pack = load_pack(PACK.parent / "accounting-gr10.toml")
    assert check_pack_weights(pack).passed
    assert [t.name for t in pack.poa][:2] == ["Presentation", "Controlled Test 1"]
    assert pack.cognitive is not None
    assert {k: int(v) for k, v in pack.cognitive.levels.items()} == {
        "knowledge_comprehension": 30,
        "application_analysis": 40,
        "synthesis_evaluation": 30,
    }
    assert any("CAPS FET Accounting §4.3" in s for s in pack.sources)
    assert any("principle 11" in c for c in pack.conventions)


def test_a_pack_without_a_grid_still_loads(tmp_path: Path) -> None:
    """SPEC US7 scenario 2: a missing slot loads as None; the Planner logs the skip."""
    start, rest = (PACK.parent / "accounting-gr10.toml").read_text().split("[cognitive]", 1)
    path = tmp_path / "no-grid.toml"
    path.write_text(start + "[moderation]" + rest.split("[moderation]", 1)[1])
    pack = load_pack(path)
    assert pack.cognitive is None
    assert check_pack_weights(pack).passed
