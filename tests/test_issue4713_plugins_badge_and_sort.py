"""Tests for issue #4713: dark-mode disabled badge color and active-first plugin sort."""
import json
import os
import re
import subprocess


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STYLE_CSS = os.path.join(REPO_ROOT, 'static', 'style.css')
PANELS_JS = os.path.join(REPO_ROOT, 'static', 'panels.js')


def test_css_dark_mode_disabled_badge_compound_rule():
    """style.css must contain a compound selector that applies var(--muted) to
    disabled badges in dark mode at specificity (0,4,0)."""
    with open(STYLE_CSS, encoding='utf-8') as f:
        css = f.read()
    # Must have :root.dark combined with .provider-card-badge and .plugin-card-badge-disabled
    pattern = r':root\.dark\s+\.provider-card-badge\.plugin-card-badge-disabled\b[^}]*color\s*:\s*var\(--muted\)'
    assert re.search(pattern, css), (
        'style.css is missing a compound :root.dark .provider-card-badge.plugin-card-badge-disabled '
        'rule with color:var(--muted)'
    )


def test_panels_js_render_loop_uses_partition():
    """panels.js loadPluginsPanel render loop must call _partitionPluginsActiveFirst."""
    with open(PANELS_JS, encoding='utf-8') as f:
        js = f.read()
    assert '_partitionPluginsActiveFirst' in js, (
        'panels.js is missing _partitionPluginsActiveFirst'
    )
    # The for-of loop inside loadPluginsPanel must use the partition helper
    assert 'for(const plugin of _partitionPluginsActiveFirst(plugins))' in js or \
           'for (const plugin of _partitionPluginsActiveFirst(plugins))' in js, (
        'loadPluginsPanel render loop does not call _partitionPluginsActiveFirst(plugins)'
    )


def _extract_helpers(js_source):
    """Extract the two helper functions from panels.js source."""
    start = js_source.find('function _pluginActivationState(')
    assert start != -1, '_pluginActivationState not found in panels.js'
    end = js_source.find('\nasync function loadPluginsPanel(', start)
    assert end != -1, 'Could not find end boundary for helpers'
    return js_source[start:end].strip()


def _run_node(script):
    result = subprocess.run(
        ['node', '-e', script],
        capture_output=True, text=True, timeout=15
    )
    assert result.returncode == 0, f'node exited {result.returncode}: {result.stderr}'
    return json.loads(result.stdout.strip())


def test_partition_stability():
    """_partitionPluginsActiveFirst preserves relative order within each bucket."""
    with open(PANELS_JS, encoding='utf-8') as f:
        js = f.read()
    helpers = _extract_helpers(js)
    script = helpers + r"""
const input = [
  {activation:'enabled', key:'a'},
  {activation:'disabled', key:'b'},
  {activation:'provider', key:'c'},
  {activation:'disabled', key:'d'},
  {activation:'enabled', key:'e'}
];
const copy = JSON.parse(JSON.stringify(input));
const result = _partitionPluginsActiveFirst(input);
console.log(JSON.stringify({result: result.map(p=>p.key)}));
"""
    data = _run_node(script)
    assert data['result'] == ['a', 'c', 'e', 'b', 'd'], (
        f'Expected [a,c,e,b,d] but got {data["result"]}'
    )


def test_partition_input_immutability():
    """_partitionPluginsActiveFirst must not mutate the input array."""
    with open(PANELS_JS, encoding='utf-8') as f:
        js = f.read()
    helpers = _extract_helpers(js)
    script = helpers + r"""
const input = [
  {activation:'enabled', key:'a'},
  {activation:'disabled', key:'b'},
  {activation:'provider', key:'c'},
  {activation:'disabled', key:'d'}
];
const copy = JSON.parse(JSON.stringify(input));
_partitionPluginsActiveFirst(input);
console.log(JSON.stringify({inputUnchanged: JSON.stringify(input) === JSON.stringify(copy)}));
"""
    data = _run_node(script)
    assert data['inputUnchanged'] is True, 'Input array was mutated by _partitionPluginsActiveFirst'
