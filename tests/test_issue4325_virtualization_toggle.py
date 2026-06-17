"""Regression coverage for #4325/#4343: transcript virtualization toggle.

The stream-end freeze/jump fix (#4328, semantic viewport anchoring) is covered by
test_issue500_message_list_virtualization.py. This file covers the Preferences
toggle that lets a user enable virtualization (opt-in, default OFF since #4343).
"""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INDEX = REPO_ROOT / "static" / "index.html"
PANELS = REPO_ROOT / "static" / "panels.js"
BOOT = REPO_ROOT / "static" / "boot.js"
UI = REPO_ROOT / "static" / "ui.js"
I18N = REPO_ROOT / "static" / "i18n.js"
CONFIG = REPO_ROOT / "api" / "config.py"


def test_virtualize_transcript_setting_is_default_off_and_allowed():
    """Opt-in model: default False (virtualization off), and whitelisted as a bool key."""
    src = CONFIG.read_text(encoding="utf-8")
    assert '"virtualize_transcript": False' in src, "must default OFF (opt-in)"
    assert '"virtualize_transcript",' in src, "must be in _SETTINGS_BOOL_KEYS"


def test_settings_preferences_expose_virtualize_toggle():
    html = INDEX.read_text(encoding="utf-8")
    assert 'id="settingsVirtualizeTranscript"' in html
    assert 'data-i18n="settings_label_virtualize_transcript"' in html
    assert 'data-i18n="settings_desc_virtualize_transcript"' in html
    # Checkbox must not have the checked attribute (opt-in, default OFF).
    cb_start = html.index('id="settingsVirtualizeTranscript"')
    tag_start = html.rfind('<input', 0, cb_start)
    tag_end = html.index('>', cb_start)
    cb_tag = html[tag_start:tag_end + 1]
    assert ' checked' not in cb_tag, f"checkbox must be unchecked by default, got: {cb_tag}"


def test_boot_applies_saved_virtualize_preference_default_off():
    js = BOOT.read_text(encoding="utf-8")
    # Default-off semantics: ===true (only enabled when explicitly true).
    assert "window._virtualizeTranscript=s.virtualize_transcript===true" in js
    # Settings-load-failed fallback also defaults OFF.
    assert "window._virtualizeTranscript=false" in js


def test_ui_gate_forces_full_render_when_opted_out():
    js = UI.read_text(encoding="utf-8")
    start = js.index("function _currentMessageVirtualWindow(")
    body = js[start:start + 900]
    assert "_virtualizeTranscript===false" in body
    assert "virtualized:false" in body


def test_panels_round_trip_and_hot_apply_virtualize_toggle():
    js = PANELS.read_text(encoding="utf-8")
    assert "const virtualizeTranscriptCb=$('settingsVirtualizeTranscript');" in js
    assert "payload.virtualize_transcript=virtualizeTranscriptCb.checked;" in js
    assert "virtualizeTranscriptCb.checked=settings.virtualize_transcript===true;" in js
    assert "window._virtualizeTranscript=virtualizeTranscriptCb.checked;" in js
    # Hot-apply: toggling re-renders the open transcript immediately.
    assert "renderMessages({preserveScroll:true})" in js


def test_virtualize_toggle_i18n_all_locales():
    js = I18N.read_text(encoding="utf-8")
    assert js.count("settings_label_virtualize_transcript:") == 13
    assert js.count("settings_desc_virtualize_transcript:") == 13
