from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def test_use_entry_in_commands_array():
    src = read("static/commands.js")
    assert "{name:'use'," in src, "COMMANDS must contain a {name:'use', ...} entry"


def test_use_entry_precedes_stop_entry():
    src = read("static/commands.js")
    use_pos = src.index("{name:'use',")
    stop_pos = src.index("{name:'stop',")
    assert use_pos < stop_pos, "/use must be registered before /stop in COMMANDS"


def test_cmdUse_function_defined():
    src = read("static/commands.js")
    assert "async function cmdUse(args)" in src, "cmdUse function must be defined"


def test_forced_skill_directive_declared():
    src = read("static/commands.js")
    assert "let _forcedSkillDirective=null;" in src, "_forcedSkillDirective must be declared at module scope"


def test_forced_skill_directive_set_in_cmdUse():
    src = read("static/commands.js")
    assert "_forcedSkillDirective =" in src, "_forcedSkillDirective must be assigned inside cmdUse"


def test_use_entry_has_noEcho():
    src = read("static/commands.js")
    # Extract the /use entry line and check noEcho:true is present
    idx = src.index("{name:'use',")
    line_end = src.index("}", idx)
    entry = src[idx:line_end + 1]
    assert "noEcho:true" in entry, "/use entry must have noEcho:true"


def test_use_entry_has_subArgs_skills():
    src = read("static/commands.js")
    idx = src.index("{name:'use',")
    line_end = src.index("}", idx)
    entry = src[idx:line_end + 1]
    assert "subArgs:'skills'" in entry, "/use entry must have subArgs:'skills' for autocomplete"


def test_directive_cleared_in_finally_block():
    src = read("static/messages.js")
    assert "_forcedSkillDirective=null;" in src, "_forcedSkillDirective must be cleared in messages.js"
    # Confirm it appears in the finally block alongside _sendInProgress
    assert "_sendInProgress=false; _sendInProgressSid=null; _forcedSkillDirective=null;" in src, \
        "_forcedSkillDirective=null must appear in the same finally line as _sendInProgress=false"


def test_directive_injection_before_empty_guard():
    src = read("static/messages.js")
    inject_pos = src.index("typeof _forcedSkillDirective==='string'")
    guard_pos = src.index("if(!msgText){setComposerStatus('Nothing to send');return;}")
    assert inject_pos < guard_pos, "directive injection must appear before the if(!msgText) guard"


def test_directive_text_uses_match_name():
    src = read("static/commands.js")
    assert "match.name" in src, "directive must use match.name (canonical casing), not raw user input"
    assert "[USER OVERRIDE] You MUST consult skill '" in src, "directive text must match the specified format"
