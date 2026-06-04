import pathlib

_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_memory_skill_tools_constant():
    src = (_ROOT / "static" / "ui.js").read_text(encoding="utf-8")
    assert "_MEMORY_SKILL_TOOLS" in src
    assert "'memory'" in src or '"memory"' in src


def test_is_memory_skill_write_helper():
    src = (_ROOT / "static" / "ui.js").read_text(encoding="utf-8")
    assert "_isMemorySkillWrite" in src


def test_sync_summary_includes_memory_suffix():
    src = (_ROOT / "static" / "ui.js").read_text(encoding="utf-8")
    assert "memories" in src
    assert "saved" in src


def test_build_tool_card_attaches_tc_data():
    src = (_ROOT / "static" / "ui.js").read_text(encoding="utf-8")
    assert "_tcData" in src
