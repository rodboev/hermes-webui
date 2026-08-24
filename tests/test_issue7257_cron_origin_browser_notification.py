"""Behavioral coverage for cron-origin browser notifications in hidden tabs."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
PANELS_JS = (ROOT / "static" / "panels.js").read_text(encoding="utf-8")
NODE = shutil.which("node")


def _function_source(name: str) -> str:
    marker = f"function {name}("
    start = PANELS_JS.find(marker)
    assert start >= 0, f"{name} not found"
    brace = PANELS_JS.find("{", PANELS_JS.find(")", start))
    depth = 0
    for index in range(brace, len(PANELS_JS)):
        if PANELS_JS[index] == "{":
            depth += 1
        elif PANELS_JS[index] == "}":
            depth -= 1
            if depth == 0:
                return PANELS_JS[start : index + 1]
    raise AssertionError(f"{name} body did not terminate")


def _run_node(case: str) -> dict:
    if NODE is None:
        pytest.skip("node is required for the behavioral harness")
    helper = _function_source("_cronBrowserNotificationsDeliverable")
    poller = _function_source("startCronPolling")
    script = f"""
const caseName = {json.dumps(case)};
const helperSource = {json.dumps(helper)};
const pollerSource = {json.dumps(poller)};

async function runCase() {{
  let _cronPollSince = 0;
  let _cronPollTimer = null;
  let _cronPollGeneration = 0;
  const _cronNewJobIds = new Set();
  const toasts = [];
  const notifications = [];
  let apiCalls = 0;
  let resolveApi;
  const document = {{
    hidden: caseName !== 'visible',
    querySelector: () => ({{
      querySelector: () => null,
      appendChild: () => {{}},
      style: {{}},
    }}),
  }};
  const unavailableCases = new Set(['unavailable', 'setting-off', 'permission-denied', 'permission-default', 'missing-api']);
  const window = {{_notificationsEnabled: !['unavailable', 'setting-off'].includes(caseName)}};
  if(caseName !== 'missing-api') {{
    const permission = caseName === 'permission-denied' ? 'denied' : caseName === 'permission-default' ? 'default' : 'granted';
    globalThis.Notification = {{permission}};
  }} else delete globalThis.Notification;
  const t = (key, ...args) => key + ':' + args.join('|');
  const showToast = (message) => toasts.push(message);
  const sendBrowserNotification = (title, body, options) => notifications.push({{title, body, options}});
  const updateCronBadge = () => {{}};
  const _markSessionCompletionUnreadIfBackground = () => {{}};
  const completions = [{{name: 'Nightly', status: 'success', completed_at: 42, job_id: 'job-1', session_id: caseName === 'no-session' ? '' : 'sid-1', message_count: 7, toast_notifications: caseName !== 'muted'}}];
  let rejectApi;
  const api = () => {{
    apiCalls += 1;
    return new Promise((resolve, reject) => {{
      if(caseName === 'reject' && apiCalls === 1) rejectApi = () => reject(new Error('poll failed'));
      else resolveApi = () => resolve({{completions}});
    }});
  }};
  const setInterval = callback => {{ _cronPollTimer = {{callback}}; return _cronPollTimer; }};
  const clearInterval = () => {{}};
  eval(helperSource);
  eval(pollerSource);
  startCronPolling();
  if (unavailableCases.has(caseName)) {{
    await _cronPollTimer.callback();
  }} else if (caseName === 'transition') {{
    const pending = _cronPollTimer.callback();
    await Promise.resolve();
    window._notificationsEnabled = false;
    resolveApi();
    await pending;
  }} else if (caseName === 'reject') {{
    const failed = _cronPollTimer.callback();
    await Promise.resolve();
    rejectApi();
    await failed;
    const recovered = _cronPollTimer.callback();
    resolveApi();
    await recovered;
  }} else if (caseName === 'stale') {{
    const pending = _cronPollTimer.callback();
    await Promise.resolve();
    _cronPollGeneration += 1;
    resolveApi();
    await pending;
  }} else if (caseName === 'overlap') {{
    const first = _cronPollTimer.callback();
    await Promise.resolve();
    const second = _cronPollTimer.callback();
    resolveApi();
    await Promise.all([first, second]);
  }} else {{
    const pending = _cronPollTimer.callback();
    resolveApi();
    await pending;
  }}
  process.stdout.write(JSON.stringify({{apiCalls, toasts, notifications, since: _cronPollSince, ids: [..._cronNewJobIds]}}));
}}
runCase().catch(error => {{ console.error(error); process.exit(1); }});
"""
    result = subprocess.run([NODE, "-e", script], cwd=ROOT, capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def test_hidden_tab_cron_completion_fires_browser_notification():
    result = _run_node("hidden")
    assert result["apiCalls"] == 1
    assert result["toasts"] == []
    assert result["notifications"] == [{
        "title": "Nightly",
        "body": "cron_completion_status:Nightly|status_completed:",
        "options": {"forceHidden": True, "sid": "sid-1"},
    }]


def test_hidden_completion_preserves_cursor_unread_and_badge():
    result = _run_node("hidden")
    assert result["since"] == 42
    assert result["ids"] == ["job-1"]


def test_visible_completion_keeps_toast_only():
    result = _run_node("visible")
    assert result["toasts"] == ["cron_completion_status:Nightly|status_completed:"]
    assert result["notifications"] == []


def test_muted_completion_keeps_unread_without_alert():
    result = _run_node("muted")
    assert result["toasts"] == []
    assert result["notifications"] == []
    assert result["since"] == 42
    assert result["ids"] == ["job-1"]


def test_hidden_without_permission_or_setting_preserves_backlog():
    result = _run_node("unavailable")
    assert result == {"apiCalls": 0, "toasts": [], "notifications": [], "since": 0, "ids": []}


@pytest.mark.parametrize("case", ["setting-off", "permission-denied", "permission-default", "missing-api"])
def test_hidden_unavailable_delivery_modes_preserve_backlog(case):
    result = _run_node(case)
    assert result == {"apiCalls": 0, "toasts": [], "notifications": [], "since": 0, "ids": []}


def test_hidden_transition_to_unavailable_preserves_backlog():
    result = _run_node("transition")
    assert result == {"apiCalls": 1, "toasts": [], "notifications": [], "since": 0, "ids": []}


def test_rejected_poll_clears_in_flight_guard():
    result = _run_node("reject")
    assert result["apiCalls"] == 2
    assert len(result["notifications"]) == 1
    assert result["since"] == 42


def test_hidden_completion_without_session_id_skips_origin_notification():
    result = _run_node("no-session")
    assert result["notifications"] == []
    assert result["since"] == 42
    assert result["ids"] == ["job-1"]


def test_stale_generation_drops_completion():
    result = _run_node("stale")
    assert result["apiCalls"] == 1
    assert result["notifications"] == []
    assert result["since"] == 0
    assert result["ids"] == []


def test_overlapping_polls_do_not_duplicate_completion():
    result = _run_node("overlap")
    assert result["apiCalls"] == 1
    assert len(result["notifications"]) == 1
