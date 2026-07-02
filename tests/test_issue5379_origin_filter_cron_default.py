"""Regression coverage for issue #5379: cron origin filter should default off."""

import json
import pathlib
import shutil
import subprocess

import pytest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SESSIONS_JS = ROOT / "static" / "sessions.js"
NODE = shutil.which("node")


def _extract_function(source_text, function_name):
    marker = f"function {function_name}("
    start = source_text.index(marker)
    brace_start = source_text.index("{", start)
    depth = 0
    for index in range(brace_start, len(source_text)):
        char = source_text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source_text[start:index + 1]
    raise AssertionError(f"Could not extract {function_name}")


def _run_node(script):
    proc = subprocess.run([NODE, "-e", script], capture_output=True, text=True, check=True)
    return json.loads(proc.stdout)


def _origin_filter_functions():
    src = SESSIONS_JS.read_text(encoding="utf-8")
    names = [
        "_normalizeSidebarOriginId",
        "_originFilterSignature",
        "_persistOriginFilters",
        "_restoreOriginFilters",
        "_toggleOriginFilter",
        "_ensureOriginFilterDefaults",
        "_captureOriginFilterCronDefaultSeed",
        "_originFilterDefaultsInclude",
        "_sidebarOriginLabelForId",
        "_humanizeSidebarOriginLabel",
        "_sidebarOriginOptions",
    ]
    return src, "\n".join(_extract_function(src, name) for name in names)


def _base_state_script():
    return """
  let _activeOriginFilters = new Set(['webui']);
  let _originFiltersHydrated = false;
  let _originFiltersLoadedFromStorage = false;
  let _originFilterCronDefaultSeed = null;
  let _selectedSessions = new Set();
  let _sessionSelectMode = false;
  let _activeProject = null;
  let _sidebarOriginCatalog = [];
"""


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_origin_filter_defaults_excludes_cron_for_seed_false():
    src, fn_src = _origin_filter_functions()
    script = f"""
global.window = {{ _showCliSessions: true, _showCronSessions: false }};
{_base_state_script()}
global.localStorage = {{
  writes: [],
  setItem(key, value) {{ this.writes.push([key, value]); }},
  getItem() {{ return null; }},
}};
function renderSessionListFromCache() {{}}
{fn_src}
const changed = _ensureOriginFilterDefaults([
  {{ id: 'webui' }},
  {{ id: 'cli' }},
  {{ id: 'cron' }},
]);
console.log(JSON.stringify({{ changed, active: [..._activeOriginFilters], writes: localStorage.writes }}));
"""
    body = _run_node(script)
    assert body["changed"] is True
    assert body["active"] == ["webui", "cli"]
    assert body["writes"] == [["hermes-origin-filters", "[\"webui\",\"cli\"]"]]


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_origin_filter_defaults_includes_cron_for_seed_true():
    _, fn_src = _origin_filter_functions()
    script = f"""
global.window = {{ _showCliSessions: true, _showCronSessions: true }};
{_base_state_script()}
global.localStorage = {{
  writes: [],
  setItem(key, value) {{ this.writes.push([key, value]); }},
  getItem() {{ return null; }},
}};
function renderSessionListFromCache() {{}}
{fn_src}
const changed = _ensureOriginFilterDefaults([
  {{ id: 'cli' }},
  {{ id: 'cron' }},
  {{ id: 'teams' }},
]);
console.log(JSON.stringify({{ changed, active: [..._activeOriginFilters], writes: localStorage.writes }}));
"""
    body = _run_node(script)
    assert body["changed"] is True
    assert body["active"] == ["webui", "cli", "cron", "teams"]
    assert body["writes"] == [["hermes-origin-filters", "[\"webui\",\"cli\",\"cron\",\"teams\"]"]]


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_origin_filter_defaults_stays_off_after_live_flag_flip():
    _, fn_src = _origin_filter_functions()
    script = f"""
global.window = {{ _showCliSessions: true, _showCronSessions: false }};
{_base_state_script()}
global.localStorage = {{
  writes: [],
  setItem(key, value) {{ this.writes.push([key, value]); }},
  getItem() {{ return null; }},
}};
function renderSessionListFromCache() {{}}
{fn_src}
const first = _ensureOriginFilterDefaults([
  {{ id: 'cli' }},
  {{ id: 'cron' }},
]);
window._showCronSessions = true;
const second = _ensureOriginFilterDefaults([
  {{ id: 'cli' }},
  {{ id: 'cron' }},
]);
console.log(JSON.stringify({{
  firstChanged: first,
  secondChanged: second,
  firstActive: ['webui', 'cli'],
  secondActive: [..._activeOriginFilters],
  writes: localStorage.writes,
}}));
"""
    body = _run_node(script)
    assert body["firstChanged"] is True
    assert body["secondChanged"] is False
    assert body["firstActive"] == body["secondActive"]
    assert body["firstActive"] == ["webui", "cli"]


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_origin_filter_defaults_preserves_stored_selection():
    _, fn_src = _origin_filter_functions()
    script = f"""
global.window = {{ _showCliSessions: true, _showCronSessions: false }};
{_base_state_script()}
global.localStorage = {{
  stored: '["whatsapp"]',
  writes: [],
  getItem(key) {{ return key === 'hermes-origin-filters' ? this.stored : null; }},
  setItem(key, value) {{ this.writes.push([key, value]); }},
}};
function renderSessionListFromCache() {{}}
{fn_src}
_restoreOriginFilters();
const changed = _ensureOriginFilterDefaults([
  {{ id: 'webui' }},
  {{ id: 'cli' }},
  {{ id: 'cron' }},
]);
console.log(JSON.stringify({{
  changed,
  active: [..._activeOriginFilters],
  hydrated: _originFiltersHydrated,
  loadedFromStorage: _originFiltersLoadedFromStorage,
  writes: localStorage.writes,
}}));
"""
    body = _run_node(script)
    assert body["changed"] is False
    assert body["active"] == ["webui", "whatsapp"]
    assert body["hydrated"] is True
    assert body["loadedFromStorage"] is True
    assert body["writes"] == []


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_toggle_origin_filter_keeps_cron_and_persists():
    _, fn_src = _origin_filter_functions()
    script = f"""
global.window = {{ _showCliSessions: true, _showCronSessions: false }};
{_base_state_script()}
global.localStorage = {{
  writes: [],
  setItem(key, value) {{ this.writes.push([key, value]); }},
  getItem() {{ return null; }},
}};
function renderSessionListFromCache() {{}}
{fn_src}
_toggleOriginFilter('cron');
const changed = _ensureOriginFilterDefaults([
  {{ id: 'cli' }},
  {{ id: 'cron' }},
]);
console.log(JSON.stringify({{
  changed,
  active: [..._activeOriginFilters],
  loadedFromStorage: _originFiltersLoadedFromStorage,
  writes: localStorage.writes,
}}));
"""
    body = _run_node(script)
    assert body["active"] == ["webui", "cron"]
    assert body["loadedFromStorage"] is True
    assert body["changed"] is False
    assert body["writes"] == [["hermes-origin-filters", "[\"webui\",\"cron\"]"]]


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_origin_filter_defaults_keeps_webui_and_cli_when_cron_gated_off():
    _, fn_src = _origin_filter_functions()
    script = f"""
global.window = {{ _showCliSessions: true, _showCronSessions: false }};
{_base_state_script()}
global.localStorage = {{
  writes: [],
  setItem(key, value) {{ this.writes.push([key, value]); }},
  getItem() {{ return null; }},
}};
function renderSessionListFromCache() {{}}
{fn_src}
const changed = _ensureOriginFilterDefaults([
  {{ id: 'cli' }},
  {{ id: 'cron' }},
]);
console.log(JSON.stringify({{
  changed,
  active: [..._activeOriginFilters],
  writes: localStorage.writes,
}}));
"""
    body = _run_node(script)
    assert body["changed"] is True
    assert body["active"] == ["webui", "cli"]
    assert body["writes"] == [["hermes-origin-filters", "[\"webui\",\"cli\"]"]]


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_sidebar_origin_options_keeps_cron_vocabulary_with_zero_count():
    src = SESSIONS_JS.read_text(encoding="utf-8")
    script = f"""
{_extract_function(src, "_humanizeSidebarOriginLabel")}
{_extract_function(src, "_sidebarOriginLabelForId")}
{_extract_function(src, "_normalizeSidebarOriginId")}
{_extract_function(src, "_sidebarOriginOptions")}
const _SIDEBAR_FIXED_ORIGIN_IDS = ['webui', 'cli', 'cron'];
const _SIDEBAR_ORIGIN_LABELS = {{ webui: 'WebUI', cli: 'CLI', cron: 'Cron' }};
let _sidebarOriginCatalog = [];
const options = _sidebarOriginOptions(new Map(), new Map());
const cron = options.find(entry => entry.id === 'cron');
console.log(JSON.stringify({{
  labels: options.map(entry => entry.id),
  cronCount: cron && cron.count,
  cronLocked: cron && !!cron.locked,
}}));
"""
    body = _run_node(script)
    assert body["labels"] == ["webui", "cli", "cron"]
    assert body["cronCount"] == 0
    assert body["cronLocked"] is False


def test_origin_filter_defaults_capture_and_routing_is_present_in_source():
    src = SESSIONS_JS.read_text(encoding="utf-8")
    assert "let _originFilterCronDefaultSeed = null" in src
    assert "function _captureOriginFilterCronDefaultSeed()" in src
    assert "function _originFilterDefaultsInclude(originId)" in src
    assert src.count("_captureOriginFilterCronDefaultSeed();") >= 2
    assert src.count("if (_originFilterDefaultsInclude(originId)) next.add(originId);") >= 2
    assert "return originId !== 'cron' || _captureOriginFilterCronDefaultSeed();" in src
