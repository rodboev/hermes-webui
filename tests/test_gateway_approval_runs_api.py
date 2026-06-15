"""Tests for the gateway runs-API approval bridge (#4203)."""
from __future__ import annotations

import io
import json
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# 1. Capability detection
# ---------------------------------------------------------------------------

def test_gateway_capability_detection():
    """get_gateway_caps / gateway_supports_approval correctly parse /health/detailed."""
    from api.config import (
        gateway_supports_approval,
        invalidate_gateway_caps,
    )

    # Clear any leftover cache state.
    invalidate_gateway_caps()

    def _fake_urlopen_capable(req, *, timeout=None):
        body = json.dumps({
            "approval_events": True,
            "run_approval_response": True,
        }).encode()
        resp = MagicMock()
        resp.read.return_value = body
        resp.__enter__ = lambda s: s
        resp.__exit__ = lambda s, *a: None
        return resp

    with patch("urllib.request.urlopen", side_effect=_fake_urlopen_capable):
        assert gateway_supports_approval("http://fake:1234") is True

    invalidate_gateway_caps()

    def _fake_urlopen_incapable(req, *, timeout=None):
        body = json.dumps({}).encode()
        resp = MagicMock()
        resp.read.return_value = body
        resp.__enter__ = lambda s: s
        resp.__exit__ = lambda s, *a: None
        return resp

    with patch("urllib.request.urlopen", side_effect=_fake_urlopen_incapable):
        assert gateway_supports_approval("http://fake:5678") is False

    invalidate_gateway_caps()


# ---------------------------------------------------------------------------
# 2. Runs-API submission path
# ---------------------------------------------------------------------------

def test_gateway_runs_api_submission():
    """When gateway_supports_approval returns True, the runs-API path is used."""
    from api.config import STREAMS, STREAMS_LOCK
    from api.gateway_chat import _run_gateway_chat_streaming

    events = []
    q = MagicMock()
    q.put_nowait = lambda item: events.append(item)

    stream_id = "sid-test-runs"
    with STREAMS_LOCK:
        STREAMS[stream_id] = q

    runs_called = {"called": False}
    original_text = "hello from runs"

    def fake_runs_streaming(*args, **kwargs):
        runs_called["called"] = True
        return (original_text, {"input_tokens": 10, "output_tokens": 5})

    mock_session = MagicMock()
    mock_session.active_stream_id = stream_id
    mock_session.workspace = "/tmp"
    mock_session.model = "test"
    mock_session.model_provider = None
    mock_session.profile = None
    mock_session.context_messages = []
    mock_session.messages = []
    mock_session.pending_user_message = None
    mock_session.pending_attachments = None
    mock_session.pending_started_at = None

    try:
        with patch.dict("os.environ", {"HERMES_WEBUI_CHAT_BACKEND": "gateway"}):
            with patch("api.gateway_chat.gateway_supports_approval", lambda _: True), \
                 patch("api.gateway_chat._run_gateway_runs_api_streaming", fake_runs_streaming), \
                 patch("api.gateway_chat.get_session", return_value=mock_session), \
                 patch("api.gateway_chat._stream_writeback_is_current", return_value=True), \
                 patch("api.gateway_chat.merge_session_messages_append_only", return_value=[]):
                _run_gateway_chat_streaming(
                    session_id="sess1",
                    msg_text="hi",
                    model="test-model",
                    workspace="/tmp",
                    stream_id=stream_id,
                )
    finally:
        with STREAMS_LOCK:
            STREAMS.pop(stream_id, None)

    assert runs_called["called"], "The runs-API streaming path should have been invoked"


# ---------------------------------------------------------------------------
# 3. Approval event translation
# ---------------------------------------------------------------------------

def test_gateway_approval_event_translation():
    """_gateway_runs_approval_event maps fields correctly and returns None on missing tool/command."""
    from api.gateway_chat import _gateway_runs_approval_event

    payload = {
        "tool": "bash",
        "command": "rm -rf /tmp/x",
        "args": ["--force"],
        "risk_level": "critical",
        "run_id": "run-999",
        "approval_id": "appr-1",
    }
    result = _gateway_runs_approval_event(payload)
    assert result is not None
    assert result["tool"] == "bash"
    assert result["command"] == "rm -rf /tmp/x"
    assert result["args"] == ["--force"]
    assert result["risk_level"] == "critical"
    assert result["run_id"] == "run-999"
    assert result["approval_id"] == "appr-1"

    # Missing both tool and command should return None.
    assert _gateway_runs_approval_event({"risk_level": "high"}) is None
    assert _gateway_runs_approval_event({}) is None


# ---------------------------------------------------------------------------
# 4. Approval response relay
# ---------------------------------------------------------------------------

def test_gateway_approval_response_relay():
    """_handle_approval_respond relays to /v1/runs/{run_id}/approval when a run_id exists."""
    from api.gateway_chat import _STREAM_RUN_IDS

    # Seed the mapping.
    _STREAM_RUN_IDS["sid-relay"] = "run-abc"

    mock_session = MagicMock()
    mock_session.active_stream_id = "sid-relay"

    captured = {}

    def fake_urlopen(req, *, timeout=None):
        captured["url"] = req.full_url
        captured["body"] = json.loads(req.data)
        resp = MagicMock()
        resp.status = 200
        resp.__enter__ = lambda s: s
        resp.__exit__ = lambda s, *a: None
        return resp

    handler = MagicMock()
    handler.wfile = io.BytesIO()

    body = {"session_id": "sess-relay", "choice": "once", "approval_id": "appr-x"}

    with patch("api.routes.get_session", return_value=mock_session), \
         patch("urllib.request.urlopen", side_effect=fake_urlopen), \
         patch("api.gateway_chat._gateway_base_url", return_value="http://gw:8642"), \
         patch("api.gateway_chat._gateway_api_key", return_value=""):
        from api.routes import _handle_approval_respond
        _handle_approval_respond(handler, body)

    assert "/v1/runs/run-abc/approval" in captured.get("url", "")
    assert captured["body"]["choice"] == "once"
    assert captured["body"]["approval_id"] == "appr-x"

    # Cleanup.
    _STREAM_RUN_IDS.pop("sid-relay", None)


# ---------------------------------------------------------------------------
# 5. Empty chat/completions response emits gateway_empty_response (not a
#    misleading approval-unsupported banner)
# ---------------------------------------------------------------------------

def test_gateway_empty_response_no_approval_banner():
    """Empty response from chat/completions path emits gateway_empty_response, not gateway_approval_unsupported."""
    from api.config import STREAMS, STREAMS_LOCK
    from api.gateway_chat import _run_gateway_chat_streaming

    events = []
    q = MagicMock()
    q.put_nowait = lambda item: events.append(item)

    stream_id = "sid-fb"
    with STREAMS_LOCK:
        STREAMS[stream_id] = q

    # Simulate an SSE stream that returns only [DONE] with no content.
    sse_body = b"data: [DONE]\n\n"

    def fake_urlopen(req, *, timeout=None):
        resp = MagicMock()
        resp.__iter__ = lambda s: iter(sse_body.split(b"\n"))
        resp.__enter__ = lambda s: s
        resp.__exit__ = lambda s, *a: None
        return resp

    try:
        with patch.dict("os.environ", {"HERMES_WEBUI_CHAT_BACKEND": "gateway"}):
            with patch("api.gateway_chat.gateway_supports_approval", return_value=False), \
                 patch("urllib.request.urlopen", side_effect=fake_urlopen), \
                 patch("api.gateway_chat.get_session", return_value=MagicMock(
                     active_stream_id=stream_id, workspace="/tmp",
                     profile=None, context_messages=[], messages=[],
                 )):
                _run_gateway_chat_streaming(
                    session_id="sess-fb",
                    msg_text="do something risky",
                    model="test",
                    workspace="/tmp",
                    stream_id=stream_id,
                )
    finally:
        with STREAMS_LOCK:
            STREAMS.pop(stream_id, None)

    apperrors = [e for e in events if isinstance(e, tuple) and e[0] == "apperror"]
    # The misleading gateway_approval_unsupported banner should no longer fire;
    # the generic gateway_empty_response handler covers this case correctly.
    assert not any(
        isinstance(ev[1], dict) and ev[1].get("type") == "gateway_approval_unsupported"
        for ev in apperrors
    ), f"gateway_approval_unsupported should not fire for generic empty responses: {apperrors}"
    assert any(
        isinstance(ev[1], dict) and ev[1].get("type") == "gateway_empty_response"
        for ev in apperrors
    ), f"Expected gateway_empty_response apperror, got events: {apperrors}"


# ---------------------------------------------------------------------------
# 6. Chat/completions path unchanged for normal responses
# ---------------------------------------------------------------------------

def test_gateway_chat_completions_path_unchanged():
    """Non-stalling chat/completions turn completes without apperror events."""
    from api.config import STREAMS, STREAMS_LOCK
    from api.gateway_chat import _run_gateway_chat_streaming

    events = []
    q = MagicMock()
    q.put_nowait = lambda item: events.append(item)

    stream_id = "sid-ok"

    with STREAMS_LOCK:
        STREAMS[stream_id] = q

    # Simulate a normal SSE response with content.
    sse_body = (
        b'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n'
        b"data: [DONE]\n\n"
    )

    mock_session = MagicMock()
    mock_session.active_stream_id = stream_id
    mock_session.workspace = "/tmp"
    mock_session.model = "test"
    mock_session.model_provider = None
    mock_session.profile = None
    mock_session.context_messages = []
    mock_session.messages = []
    mock_session.pending_user_message = None
    mock_session.pending_attachments = None
    mock_session.pending_started_at = None

    def fake_urlopen(req, *, timeout=None):
        resp = MagicMock()
        resp.__iter__ = lambda s: iter(sse_body.split(b"\n"))
        resp.__enter__ = lambda s: s
        resp.__exit__ = lambda s, *a: None
        return resp

    try:
        with patch.dict("os.environ", {"HERMES_WEBUI_CHAT_BACKEND": "gateway"}):
            with patch("api.gateway_chat.gateway_supports_approval", return_value=False), \
                 patch("urllib.request.urlopen", side_effect=fake_urlopen), \
                 patch("api.gateway_chat.get_session", return_value=mock_session), \
                 patch("api.gateway_chat._stream_writeback_is_current", return_value=True), \
                 patch("api.gateway_chat.merge_session_messages_append_only", return_value=[]):
                _run_gateway_chat_streaming(
                    session_id="sess-ok",
                    msg_text="hello",
                    model="test",
                    workspace="/tmp",
                    stream_id=stream_id,
                )
    finally:
        with STREAMS_LOCK:
            STREAMS.pop(stream_id, None)

    apperrors = [e for e in events if isinstance(e, tuple) and e[0] == "apperror"]
    assert not apperrors, f"No apperror expected for a normal response, got: {apperrors}"
    tokens = [e for e in events if isinstance(e, tuple) and e[0] == "token"]
    assert tokens, "Expected at least one token event"
