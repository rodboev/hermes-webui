"""Production-composed contract tests for issue #6610 RESPEC-4."""

import copy
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
    return provider_error_dismissal_plan(session, index)


def test_reference_is_fixed_size_for_long_ascii_and_multibyte_content():
    from api.session_ops import provider_error_dismissal_plan
    for content in ["a" * 8191, "b" * 8192, "c" * 9000, "x" * 65536, "é" * 9000]:
        session = _FakeSession([_message(content)])
        plan = provider_error_dismissal_plan(session, 0)
        assert len(plan.dismiss_ref) == 64 and plan.dismiss_ref == plan.dismiss_ref.lower()


def test_dismissal_changes_only_owner_and_uses_private_save_flags():
    from api.session_ops import apply_provider_error_dismissal
    session = _FakeSession([{"role": "user", "content": "prompt"}, _message(), {"role": "assistant", "content": "later"}])
    plan = _plan(session, 1)
    with patch.object(session, "save", wraps=session.save) as save:
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
    with pytest.raises(ProviderErrorDismissalUnavailable):
        apply_provider_error_dismissal(first, first_plan.dismiss_ref)
    with pytest.raises(ProviderErrorDismissalUnavailable):
        apply_provider_error_dismissal(first, "x" * 64)


def test_control_imported_busy_and_ambiguous_rows_have_no_capability():
    from api.session_ops import provider_error_dismissal_ref
    for row in (_message(provider_details_label="Cancellation details"), _message(type="interrupted"), _message(_compressionRecovery={"kind": "retry"})):
        assert provider_error_dismissal_ref(_FakeSession([row]), 0) is None
    assert provider_error_dismissal_ref(_FakeSession([_message()], is_cli_session=True), 0) is None
    assert provider_error_dismissal_ref(_FakeSession([_message()], active_stream_id="run"), 0) is None
    assert provider_error_dismissal_ref(_FakeSession([_message()], pending_started_at=12.0), 0) is None
    assert provider_error_dismissal_ref(_FakeSession([_message()], pending_attachments=[{"name": "x"}]), 0) is None
    unknown = _FakeSession([_message()])
    unknown.session_source = unknown.raw_source = unknown.source_tag = None
    assert provider_error_dismissal_ref(unknown, 0) is None


def test_repeated_reference_is_idempotent_after_the_row_is_dismissed():
    from api.session_ops import apply_provider_error_dismissal

    session = _FakeSession([_message()])
    plan = _plan(session)
    first = apply_provider_error_dismissal(session, plan.dismiss_ref)
    second = apply_provider_error_dismissal(session, plan.dismiss_ref)
    assert first.dismiss_ref == second.dismiss_ref == plan.dismiss_ref
    assert session.save_count == 1


def test_reference_requires_stable_id_and_complete_row_digest():
    from api.session_ops import project_provider_error_dismissal_capabilities

    first = _message("first", id="same-id")
    second = _message("second", id="same-id")
    session = _FakeSession([first, second])
    projected = project_provider_error_dismissal_capabilities(
        session,
        {"messages": [copy.deepcopy(second)]},
    )
    assert projected["messages"][0]["_provider_error_dismiss_ref"]
    projected["messages"][0]["content"] = "changed"
    assert "_provider_error_dismiss_ref" not in project_provider_error_dismissal_capabilities(
        session,
        projected,
    )["messages"][0]


def test_compression_lineage_projects_and_mutates_the_physical_owner():
    from api.session_ops import apply_provider_error_dismissal, project_provider_error_dismissal_capabilities

    parent = _FakeSession([_message("parent")], session_id="parent-session")
    parent.pre_compression_snapshot = True
    child = _FakeSession([_message("child")], session_id="child-session", parent_session_id=parent.session_id)
    with patch("api.session_ops.get_session", side_effect=lambda sid: parent if sid == parent.session_id else child):
        projected = project_provider_error_dismissal_capabilities(
            child,
            {"messages": copy.deepcopy(parent.messages + child.messages)},
        )
        ref = projected["messages"][0]["_provider_error_dismiss_ref"]
        plan = apply_provider_error_dismissal(child, ref)
    assert plan.owner_session_id == parent.session_id
    assert parent.messages[0]["_dismissed"] is True
    assert child.messages[0].get("_dismissed") is None


def test_settlement_restores_producer_snapshot_before_a_later_unrelated_save():
    from api.session_ops import settle_provider_error_session

    session = _FakeSession([{"role": "user", "content": "prompt"}], pending_user_message="draft")
    producer_snapshot = copy.deepcopy(session.__dict__)
    session.messages.append({"role": "assistant", "content": "partial"})
    session.pending_user_message = None
    session.save_error = OSError("disk")
    assert settle_provider_error_session(session, _message(), snapshot=producer_snapshot) is False
    assert session.__dict__ == producer_snapshot
    session.save_error = None
    session.messages.append({"role": "user", "content": "later"})
    session.save()
    assert session.messages == producer_snapshot["messages"] + [{"role": "user", "content": "later"}]


def test_real_sidecar_reload_stays_clean_after_failed_dismissal_then_unrelated_save(tmp_path, monkeypatch):
    from api import models
    from api.session_ops import ProviderErrorDismissalUnavailable, apply_provider_error_dismissal, provider_error_dismissal_plan

    monkeypatch.setattr(models, "SESSION_DIR", tmp_path)
    session = models.Session(
        session_id="issue6610-real",
        workspace=tmp_path,
        messages=[_message()],
        source_tag="webui",
    )
    session.save(touch_updated_at=False, skip_index=True)
    loaded = models.Session.load(session.session_id)
    plan = provider_error_dismissal_plan(loaded, 0)
    with patch.object(models, "_safe_replace", side_effect=OSError("sidecar unavailable")):
        with pytest.raises(ProviderErrorDismissalUnavailable):
            apply_provider_error_dismissal(loaded, plan.dismiss_ref)
    assert models.Session.load(session.session_id).messages[0].get("_dismissed") is None
    assert not list(tmp_path.glob("*.tmp.*"))
    loaded.messages.append({"role": "user", "content": "later"})
    loaded.save(touch_updated_at=False, skip_index=True)
    reloaded = models.Session.load(session.session_id)
    assert reloaded.messages[0].get("_dismissed") is None
    assert reloaded.messages[-1]["content"] == "later"


def test_settlement_helper_rolls_back_failed_producer_and_stamps_successful_rows():
    from api.session_ops import settle_provider_error_session
    session = _FakeSession([])
    session.save_error = OSError("disk")
    assert settle_provider_error_session(session, _message()) is False and session.messages == []
    session.save_error = None
    assert settle_provider_error_session(session, _message()) is True and isinstance(session.messages[0].get("id"), int)


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
         patch.object(routes, "bad", side_effect=response), patch("api.config._evict_session_agent"):
        assert routes.handle_post(handler, urlparse("/api/session/message/dismiss-error")) is True
    assert captured["status"] == 200 and captured["data"] == {"ok": True}


def test_public_projection_contains_only_reference_and_no_private_owner_fields():
    from api.session_ops import project_provider_error_dismissal_capabilities
    session = _FakeSession([_message()])
    plan = _plan(session)
    projected = project_provider_error_dismissal_capabilities(session, {"messages": copy.deepcopy(session.messages)})
    row = projected["messages"][0]
    assert row["_provider_error_dismiss_ref"] == plan.dismiss_ref
    assert not {"owner_session_id", "owner_index", "row_digest"} & set(row)


def test_redacted_http_projection_keeps_the_capability_from_the_raw_owner_row():
    from api.helpers import redact_session_data
    from api.session_ops import project_provider_error_dismissal_capabilities

    session = _FakeSession([_message(provider_details="Bearer secret-token")])
    raw = {"messages": copy.deepcopy(session.messages)}
    projected = project_provider_error_dismissal_capabilities(session, raw)
    public = redact_session_data(projected)
    assert public["messages"][0]["_provider_error_dismiss_ref"]
