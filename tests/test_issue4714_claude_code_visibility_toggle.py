"""Regression coverage for Issue #4714: hide Claude Code imports independently.

The route must keep `show_cli_sessions` as the parent gate while allowing
`show_claude_code_sessions` to filter only Claude Code rows.
"""

import io
import json
from pathlib import Path
from urllib.parse import urlparse

import api.routes as routes
import api.profiles as profiles
import pytest

ROOT = Path(__file__).resolve().parents[1]
PANELS_JS = ROOT / "static" / "panels.js"
INDEX_HTML = ROOT / "static" / "index.html"


class _FakeHandler:
    def __init__(self):
        self.status = None
        self.headers = {}
        self.wfile = io.BytesIO()

    def send_response(self, status):
        self.status = status

    def send_header(self, key, value):
        self.headers[key] = value

    def end_headers(self):
        pass

    def json_body(self):
        return json.loads(self.wfile.getvalue().decode("utf-8"))


def _handle_sessions(url):
    handler = _FakeHandler()
    routes.handle_get(handler, urlparse(url))
    return handler


@pytest.fixture(autouse=True)
def _clear_cache():
    routes._session_list_cache_clear()
    yield
    routes._session_list_cache_clear()


def _common_monkeypatches(monkeypatch, rows, cli_rows):
    monkeypatch.setattr(routes, "load_settings", lambda: {
        "show_cli_sessions": True,
        "show_previous_messaging_sessions": False,
        "show_cron_sessions": False,
    })
    monkeypatch.setattr(routes, "all_sessions", lambda diag=None: list(rows))
    monkeypatch.setattr(routes, "_reconcile_stale_stream_state_for_session_rows", lambda _rows: False)
    monkeypatch.setattr(routes, "_enrich_sidebar_lineage_metadata", lambda rows: None)
    monkeypatch.setattr(routes, "agent_session_rows_existing", lambda ids, profile=None: {row["session_id"] for row in rows})
    monkeypatch.setattr(routes, "get_cli_sessions", lambda source_filter=None, all_profiles=False, include_claude_code=True: cli_rows)
    monkeypatch.setattr(profiles, "get_active_profile_name", lambda: "default")


def _rows_webui():
    return [
        {
            "session_id": "webui-1",
            "title": "WebUI",
            "profile": "default",
            "archived": False,
            "message_count": 5,
            "updated_at": 1000,
            "last_message_at": 1000,
            "source": "webui",
            "raw_source": "webui",
            "session_source": "webui",
            "source_tag": "webui",
            "is_cli_session": False,
        }
    ]


def _row(session_id, source, raw_source, source_tag="cli", source_label=None, is_cli_session=True):
    return {
        "session_id": session_id,
        "title": f"{session_id} title",
        "profile": "default",
        "archived": False,
        "message_count": 2,
        "updated_at": 2000,
        "last_message_at": 2000,
        "source": source,
        "raw_source": raw_source,
        "session_source": raw_source,
        "source_tag": source_tag,
        "source_label": source_label or source,
        "is_cli_session": is_cli_session,
    }


def test_show_cli_sessions_false_hides_all_imported_rows(monkeypatch):
    """When the parent toggle is off, no imported rows appear."""
    rows = _rows_webui()
    cli_rows = [_row("external-cli", "cli", "cli"), _row("external-claude", "cli", "claude_code")]
    _common_monkeypatches(monkeypatch, rows, cli_rows)
    monkeypatch.setattr(routes, "load_settings", lambda: {
        "show_cli_sessions": False,
        "show_claude_code_sessions": False,
        "show_previous_messaging_sessions": False,
        "show_cron_sessions": False,
    })

    handler = _handle_sessions("http://example.com/api/sessions")
    body = handler.json_body()

    assert handler.status == 200
    assert len(body["sessions"]) == 1
    assert body["sessions"][0]["session_id"] == "webui-1"
    assert body["cli_count"] == 0


def test_show_claude_code_sessions_false_hides_only_claude_code_rows(monkeypatch):
    """Turning the Claude-specific toggle off should hide Claude Code while keeping
    other imported rows."""
    rows = _rows_webui()
    all_cli_rows = [
        _row("external-cli", "cli", "cli", source_tag="cli"),
        _row("external-claude", "cli", "claude_code", source_tag="cli"),
    ]
    include_claude = True

    def fake_get_cli_sessions(source_filter=None, all_profiles=False, include_claude_code=True):
        nonlocal include_claude
        include_claude = include_claude_code
        if include_claude_code:
            return list(all_cli_rows)
        return [row for row in all_cli_rows if row["raw_source"] != "claude_code"]

    _common_monkeypatches(monkeypatch, rows, [])
    monkeypatch.setattr(routes, "get_cli_sessions", fake_get_cli_sessions)
    monkeypatch.setattr(routes, "load_settings", lambda: {
        "show_cli_sessions": True,
        "show_claude_code_sessions": False,
        "show_previous_messaging_sessions": False,
        "show_cron_sessions": False,
    })

    handler = _handle_sessions("http://example.com/api/sessions")
    body = handler.json_body()

    assert handler.status == 200
    assert include_claude is False
    assert {row["session_id"] for row in body["sessions"]} == {"webui-1", "external-cli"}


def test_show_claude_code_sessions_true_keeps_claude_code_rows_visible(monkeypatch):
    """With both toggles enabled, Claude Code rows are visible."""
    rows = _rows_webui()
    all_cli_rows = [
        _row("external-cli", "cli", "cli"),
        _row("external-claude", "cli", "claude_code"),
    ]
    _common_monkeypatches(monkeypatch, rows, all_cli_rows)
    monkeypatch.setattr(routes, "load_settings", lambda: {
        "show_cli_sessions": True,
        "show_claude_code_sessions": True,
        "show_previous_messaging_sessions": False,
        "show_cron_sessions": False,
    })

    handler = _handle_sessions("http://example.com/api/sessions")
    body = handler.json_body()

    assert handler.status == 200
    assert {row["session_id"] for row in body["sessions"]} == {
        "webui-1",
        "external-cli",
        "external-claude",
    }


def test_session_list_cache_key_changes_with_claude_code_toggle():
    """Cache keys must encode the Claude Code toggle."""
    key_false = routes._session_list_cache_key(
        active_profile="default",
        all_profiles=False,
        show_cli_sessions=True,
        show_claude_code_sessions=False,
        show_previous_messaging_sessions=False,
        show_cron_sessions=False,
    )
    key_true = routes._session_list_cache_key(
        active_profile="default",
        all_profiles=False,
        show_cli_sessions=True,
        show_claude_code_sessions=True,
        show_previous_messaging_sessions=False,
        show_cron_sessions=False,
    )
    assert key_false != key_true


def test_preferences_autosave_maps_claude_code_toggle():
    """Preferences autosave wiring includes the Claude Code toggle payload fields."""
    panels = PANELS_JS.read_text(encoding="utf-8")
    index = INDEX_HTML.read_text(encoding="utf-8")

    assert "settingsShowClaudeCodeSessions" in panels
    assert "show_claude_code_sessions" in panels
    assert "settingsShowClaudeCodeSessions" in index


def test_claude_code_checkbox_is_parent_gated_in_ui():
    """The child checkbox must follow the parent non-WebUI toggle."""
    panels = PANELS_JS.read_text(encoding="utf-8")

    assert "showClaudeCodeCb.disabled=showCliCb?!showCliCb.checked:true;" in panels
    assert "payload.show_claude_code_sessions=!!(showCliCb&&showCliCb.checked&&showClaudeCodeCb.checked);" in panels
    assert "if(showClaudeCodeCb) showClaudeCodeCb.disabled=!enabled;" in panels


def test_locale_keys_exist_in_every_locale_block():
    """Every locale block should carry the Claude Code label and description keys."""
    i18n = (ROOT / "static" / "i18n.js").read_text(encoding="utf-8")

    assert i18n.count("settings_label_claude_code_sessions:") == i18n.count("settings_label_api_redact:")
    assert i18n.count("settings_desc_claude_code_sessions:") == i18n.count("settings_desc_previous_messaging_sessions:")
