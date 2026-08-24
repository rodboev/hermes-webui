"""Production-composed contract tests for issue #6610 RESPEC-4."""

import copy
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlparse

import pytest


def _message(content="provider failed", **extra):
    row = {"role": "assistant", "content": content, "_error": True,
           "_provider_error_type": "error", "timestamp": 7}
    row.update(extra)
    return row


class _FakeSession:
    def __init__(self, messages, **flags):
        self.session_id = flags.pop("session_id", "issue6610-session")
        self.messages = messages
        self.profile = "default"
        self.session_source = "webui"
        self.source_tag = "webui"
        self.raw_source = "webui"
        self.is_cli_session = False
        self.read_only = False
        self.active_stream_id = None
        self.pending_user_message = None
        self.pending_started_at = None
        self.updated_at = 11.0
        self.save_count = 0
        self.save_error = None
        for key, value in flags.items():
            setattr(self, key, value)

    def save(self, **kwargs):
        self.save_count += 1
        if self.save_error:
            raise self.save_error


def _plan(session, index=0):
    from api.session_ops import provider_error_dismissal_plan
    with patch("api.session_ops.regeneration_state", return_value=(session.messages, [])):
        return provider_error_dismissal_plan(session, index)


def test_reference_is_fixed_size_for_long_ascii_and_multibyte_content():
    from api.session_ops import provider_error_dismissal_plan
    for content in ["a" * 8191, "b" * 8192, "c" * 9000, "x" * 65536, "é" * 9000]:
        session = _FakeSession([_message(content)])
        with patch("api.session_ops.regeneration_state", return_value=(session.messages, [])):
            plan = provider_error_dismissal_plan(session, 0)
        assert len(plan.dismiss_ref) == 64 and plan.dismiss_ref == plan.dismiss_ref.lower()


def test_dismissal_changes_only_owner_and_uses_private_save_flags():
    from api.session_ops import apply_provider_error_dismissal
    session = _FakeSession([{"role": "user", "content": "prompt"}, _message(), {"role": "assistant", "content": "later"}])
    plan = _plan(session, 1)
    with patch.object(session, "save", wraps=session.save) as save:
        with patch("api.session_ops.regeneration_state", return_value=(session.messages, [])):
            apply_provider_error_dismissal(session, plan.dismiss_ref)
    assert session.messages[1]["_dismissed"] is True
    assert session.messages[0]["content"] == "prompt" and session.messages[2]["content"] == "later"
    save.assert_called_once_with(touch_updated_at=False, skip_index=True)
    assert session.updated_at == 11.0


@pytest.mark.parametrize("initial", [None, False])
def test_forced_save_failure_restores_exact_absent_or_false_marker(initial):
    from api.session_ops import ProviderErrorDismissalUnavailable, apply_provider_error_dismissal
    row = _message()
    if initial is not None:
        row["_dismissed"] = initial
    session = _FakeSession([row])
    session.save_error = OSError("sidecar unavailable")
    plan = _plan(session)
    before = copy.deepcopy(session.__dict__)
    with pytest.raises(ProviderErrorDismissalUnavailable):
        with patch("api.session_ops.regeneration_state", return_value=(session.messages, [])):
            apply_provider_error_dismissal(session, plan.dismiss_ref)
    assert session.messages == before["messages"]
    assert session.updated_at == before["updated_at"]
    session.save_error = None
    session.save()
    assert session.messages[0].get("_dismissed") is initial


def test_stale_duplicate_cross_session_and_malformed_references_fail_closed():
    from api.session_ops import ProviderErrorDismissalUnavailable, apply_provider_error_dismissal
    first = _FakeSession([_message("same")])
    second = _FakeSession([_message("same")], session_id="other")
    first_plan = _plan(first)
    with pytest.raises(ProviderErrorDismissalUnavailable):
        apply_provider_error_dismissal(second, first_plan.dismiss_ref)
    first.messages.insert(0, {"role": "user", "content": "stale"})
    with patch("api.session_ops.regeneration_state", return_value=(first.messages, [])), pytest.raises(ProviderErrorDismissalUnavailable):
        apply_provider_error_dismissal(first, first_plan.dismiss_ref)
    with pytest.raises(ProviderErrorDismissalUnavailable):
        apply_provider_error_dismissal(first, "x" * 64)


def test_control_imported_busy_and_ambiguous_rows_have_no_capability():
    from api.session_ops import provider_error_dismissal_ref
    for row in (_message(provider_details_label="Cancellation details"), _message(type="interrupted"), _message(_compressionRecovery={"kind": "retry"})):
        assert provider_error_dismissal_ref(_FakeSession([row]), 0) is None
    assert provider_error_dismissal_ref(_FakeSession([_message()], is_cli_session=True), 0) is None
    assert provider_error_dismissal_ref(_FakeSession([_message()], active_stream_id="run"), 0) is None


def test_settlement_helper_rolls_back_failed_producer_and_stamps_successful_rows():
    from api.session_ops import settle_provider_error_session
    session = _FakeSession([])
    session.save_error = OSError("disk")
    assert settle_provider_error_session(session, _message()) is False and session.messages == []
    session.save_error = None
    assert settle_provider_error_session(session, _message()) is True and session.messages[0]["id"]


def test_route_accepts_only_capability_reference():
    import api.routes as routes
    session = _FakeSession([_message()])
    plan = _plan(session)
    captured = {}
    def response(_handler, data, status=200, extra_headers=None):
        captured.update(data=data, status=status)
        return True
    handler = SimpleNamespace(headers={})
    body = {"session_id": session.session_id, "dismiss_ref": plan.dismiss_ref}
    with patch.object(routes, "read_body", return_value=body), patch.object(routes, "_check_csrf", return_value=True), \
         patch.object(routes, "_guard_request_session_visibility", return_value=True), patch.object(routes, "_get_or_materialize_session", return_value=session), \
         patch.object(routes, "_session_visible_to_active_profile", return_value=True), patch.object(routes, "j", side_effect=response), \
         patch.object(routes, "bad", side_effect=response), patch("api.config._evict_session_agent"), \
         patch("api.session_ops.regeneration_state", return_value=(session.messages, [])), \
         patch("api.session_ops._get_session_agent_lock", return_value=nullcontext()):
        assert routes.handle_post(handler, urlparse("/api/session/message/dismiss-error")) is True
    assert captured["status"] == 200 and captured["data"] == {"ok": True}


def test_public_projection_contains_only_reference_and_no_private_owner_fields():
    from api.session_ops import project_provider_error_dismissal_capabilities
    session = _FakeSession([_message()])
    plan = _plan(session)
    with patch("api.session_ops.regeneration_state", return_value=(session.messages, [])):
        projected = project_provider_error_dismissal_capabilities(session, {"messages": copy.deepcopy(session.messages)})
    row = projected["messages"][0]
    assert row["_provider_error_dismiss_ref"] == plan.dismiss_ref
    assert not {"owner_session_id", "owner_index", "row_digest"} & set(row)
