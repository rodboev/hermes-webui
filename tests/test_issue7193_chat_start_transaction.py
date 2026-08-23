"""Behavioral coverage for the shared chat-start admission transaction."""

import json
import threading

import pytest

import api.config as config
import api.models as models
import api.routes as routes
from api.models import new_session


@pytest.fixture
def transaction_env(tmp_path, monkeypatch):
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    monkeypatch.setattr(models, "SESSION_DIR", session_dir)
    monkeypatch.setattr(models, "SESSION_INDEX_FILE", session_dir / "_index.json")
    monkeypatch.setattr(config, "SESSION_INDEX_FILE", session_dir / "_index.json", raising=False)
    monkeypatch.setattr(config, "cfg", {"webui": {"session_save_mode": "eager"}})
    monkeypatch.setattr(routes, "get_webui_session_save_mode", lambda: config.get_webui_session_save_mode(config.cfg))
    monkeypatch.setattr(routes, "_agent_runtime_barrier_response", lambda **_: None)
    monkeypatch.setattr(routes, "set_last_workspace", lambda _workspace: None)
    monkeypatch.setattr(routes, "publish_session_list_changed", lambda *args, **kwargs: None)
    monkeypatch.setattr(routes, "_active_run_stream_for_session", lambda _session_id: None)
    monkeypatch.setattr(routes, "create_stream_channel", lambda: object())
    monkeypatch.setattr(routes, "_run_agent_streaming", lambda *args, **kwargs: None)
    monkeypatch.setattr(routes, "_run_gateway_chat_streaming", lambda *args, **kwargs: None)
    config.STREAMS.clear()
    config.STREAM_GOAL_RELATED.clear()
    config.STREAM_SESSION_OWNERS.clear()
    config.SESSION_WRITEBACK_OWNERS.clear()
    routes.PENDING_GOAL_CONTINUATION.clear()
    routes.PENDING_BG_TASK_COMPLETIONS.clear()
    yield session_dir
    models.SESSIONS.clear()
    config.STREAMS.clear()
    config.STREAM_GOAL_RELATED.clear()
    config.STREAM_SESSION_OWNERS.clear()
    config.SESSION_WRITEBACK_OWNERS.clear()
    routes.PENDING_GOAL_CONTINUATION.clear()
    routes.PENDING_BG_TASK_COMPLETIONS.clear()


def _start(session, **overrides):
    values = {
        "msg": "retry me",
        "attachments": [],
        "workspace": "/tmp/workspace",
        "model": session.model,
        "model_provider": session.model_provider,
        "external_runtime_owned": False,
    }
    values.update(overrides)
    return routes._start_chat_stream_for_session(session, **values)


def _users(session):
    return [row for row in session.messages if row.get("role") == "user"]


def test_eager_rejected_start_retry_reload_has_one_user_prompt(transaction_env, monkeypatch):
    session = new_session(workspace=str(transaction_env.parent))
    monkeypatch.setattr(routes, "create_stream_channel", lambda: (_ for _ in ()).throw(RuntimeError("reject")))
    with pytest.raises(RuntimeError, match="reject"):
        _start(session)
    assert _users(session) == []
    assert not session.path.exists()
    if models.SESSION_INDEX_FILE.exists():
        assert all(row.get("session_id") != session.session_id for row in json.loads(models.SESSION_INDEX_FILE.read_text(encoding="utf-8")))
    monkeypatch.setattr(routes, "create_stream_channel", lambda: object())
    _start(session)
    reloaded = models.Session.load(session.session_id)
    assert [row["content"] for row in _users(reloaded)] == ["retry me"]


def test_fresh_session_thread_start_failure_removes_sidecar_and_index(transaction_env, monkeypatch):
    session = new_session(workspace=str(transaction_env.parent))
    monkeypatch.setattr(
        threading.Thread,
        "start",
        lambda _self: (_ for _ in ()).throw(RuntimeError("thread start rejected")),
    )

    with pytest.raises(RuntimeError, match="thread start rejected"):
        _start(session)

    assert not session.path.exists()
    if models.SESSION_INDEX_FILE.exists():
        assert all(row.get("session_id") != session.session_id for row in json.loads(models.SESSION_INDEX_FILE.read_text(encoding="utf-8")))


def test_rejected_start_preserves_persisted_composer_draft(transaction_env, monkeypatch):
    session = new_session(workspace=str(transaction_env.parent))
    session.composer_draft = {"text": "keep this draft", "files": []}
    session.save(touch_updated_at=False, skip_index=True)
    monkeypatch.setattr(
        threading.Thread,
        "start",
        lambda _self: (_ for _ in ()).throw(RuntimeError("thread start rejected")),
    )

    with pytest.raises(RuntimeError, match="thread start rejected"):
        _start(session)

    reloaded = models.Session.load(session.session_id)
    assert reloaded.composer_draft == {"text": "keep this draft", "files": []}
    assert _users(reloaded) == []


@pytest.mark.parametrize("backend", [False, True])
def test_worker_waits_for_durable_acceptance_matrix(transaction_env, monkeypatch, backend):
    observed = []
    done = threading.Event()
    session = new_session(workspace=str(transaction_env.parent))

    def worker(*args, **kwargs):
        observed.append((json.loads(session.path.read_text(encoding="utf-8")), kwargs))
        done.set()

    target = "_run_gateway_chat_streaming" if backend else "_run_agent_streaming"
    monkeypatch.setattr(routes, target, worker)
    _start(session, external_runtime_owned=backend)
    assert done.wait(2)
    assert observed and [row["content"] for row in observed[0][0]["messages"] if row.get("role") == "user"] == ["retry me"]
    assert "start_gate" not in observed[0][1]


def test_deferred_mode_leaves_messages_uncheckpointed_until_worker(transaction_env, monkeypatch):
    config.cfg = {"webui": {"session_save_mode": "deferred"}}
    observed = []
    done = threading.Event()
    session = new_session(workspace=str(transaction_env.parent))
    def worker(*args, **kwargs):
        observed.append(json.loads(session.path.read_text(encoding="utf-8")))
        done.set()
    monkeypatch.setattr(routes, "_run_agent_streaming", worker)
    _start(session)
    assert done.wait(2)
    assert observed[0]["messages"] == []


@pytest.mark.parametrize("marker", ["goal", "background"])
def test_marker_claim_rollback_is_additive(transaction_env, monkeypatch, marker):
    session = new_session(workspace=str(transaction_env.parent))
    marker_set = routes.PENDING_GOAL_CONTINUATION if marker == "goal" else routes.PENDING_BG_TASK_COMPLETIONS
    marker_set.add(session.session_id)
    monkeypatch.setattr(routes, "create_stream_channel", lambda: (_ for _ in ()).throw(RuntimeError("reject")))
    with pytest.raises(RuntimeError, match="reject"):
        _start(session)
    assert session.session_id in marker_set


def test_explicit_goal_related_start_does_not_claim_existing_goal_marker(transaction_env, monkeypatch):
    session = new_session(workspace=str(transaction_env.parent))
    config.add_pending_goal_continuation(session.session_id)
    seen = []
    done = threading.Event()
    def worker(*args, **kwargs):
        seen.append(kwargs)
        done.set()
    monkeypatch.setattr(routes, "_run_agent_streaming", worker)
    _start(session, goal_related=True)
    assert session.session_id in routes.PENDING_GOAL_CONTINUATION
    assert done.wait(2)
    assert seen[0]["goal_related"] is True


def test_marker_added_after_claim_survives_rollback(transaction_env, monkeypatch):
    session = new_session(workspace=str(transaction_env.parent))
    config.add_pending_goal_continuation(session.session_id)

    def reject_and_produce():
        config.add_pending_goal_continuation(session.session_id)
        raise RuntimeError("reject")

    monkeypatch.setattr(routes, "create_stream_channel", reject_and_produce)
    with pytest.raises(RuntimeError, match="reject"):
        _start(session)
    assert session.session_id in routes.PENDING_GOAL_CONTINUATION


def test_regeneration_admission_rejection_does_not_claim_markers(transaction_env, monkeypatch):
    from api.session_ops import RegenerationUnavailable

    session = new_session(workspace=str(transaction_env.parent))
    config.add_pending_goal_continuation(session.session_id)
    monkeypatch.setattr(
        "api.session_ops.plan_regeneration",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RegenerationUnavailable("no_regenerable_turn")
        ),
    )
    result = routes._start_regeneration_stream_locked(
        session,
        turn=type("Turn", (), {"revision": "revision"})(),
        workspace="/tmp/workspace",
        model=session.model,
        model_provider=session.model_provider,
        normalized_model=False,
        diag=None,
        goal_related=False,
        source="webui",
        moa_config=None,
        backend_is_gateway=False,
    )
    assert result["_status"] == 409
    assert session.session_id in routes.PENDING_GOAL_CONTINUATION


@pytest.mark.parametrize("boundary", ["prepare", "channel", "owner", "save", "thread"])
def test_precommit_failure_compensation_matrix(transaction_env, monkeypatch, boundary):
    session = new_session(workspace=str(transaction_env.parent))
    if boundary == "prepare":
        monkeypatch.setattr(routes, "_prepare_chat_start_session_for_stream", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError(boundary)))
    elif boundary == "channel":
        monkeypatch.setattr(routes, "create_stream_channel", lambda: (_ for _ in ()).throw(RuntimeError(boundary)))
    elif boundary == "owner":
        monkeypatch.setattr(routes, "register_stream_owner", lambda *args: (_ for _ in ()).throw(RuntimeError(boundary)))
    elif boundary == "save":
        monkeypatch.setattr(session, "save", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError(boundary)))
    else:
        monkeypatch.setattr(threading.Thread, "start", lambda _self: (_ for _ in ()).throw(RuntimeError(boundary)))
    with pytest.raises(RuntimeError, match=boundary):
        _start(session)
    assert session.active_stream_id is None
    assert not config.STREAMS
    assert config.session_writeback_owner(session.session_id) is None


def test_rejected_submission_is_interrupted_once(transaction_env, monkeypatch):
    events = []
    journal = __import__("api.turn_journal", fromlist=["append_turn_journal_event"])
    monkeypatch.setattr(journal, "append_turn_journal_event", lambda _sid, event: events.append(event) or {"turn_id": "turn-1"})
    monkeypatch.setattr(routes, "create_stream_channel", lambda: (_ for _ in ()).throw(RuntimeError("reject")))
    session = new_session(workspace=str(transaction_env.parent))
    with pytest.raises(RuntimeError, match="reject"):
        _start(session)
    assert [event["event"] for event in events] == ["submitted", "interrupted"]


def test_postcommit_failure_preserves_accepted_state(transaction_env, monkeypatch):
    observed = []
    done = threading.Event()
    session = new_session(workspace=str(transaction_env.parent))
    monkeypatch.setattr(routes, "_run_agent_streaming", lambda *args, **kwargs: (observed.append(kwargs), done.set()))
    monkeypatch.setattr(routes, "set_last_workspace", lambda _workspace: (_ for _ in ()).throw(RuntimeError("postcommit")))
    response = _start(session)
    assert response["session_id"] == session.session_id
    assert done.wait(2)
    assert observed
    assert session.active_stream_id
    assert config.session_writeback_owner(session.session_id) == session.active_stream_id


def test_session_list_publication_failure_does_not_reject_durable_start(transaction_env, monkeypatch):
    session = new_session(workspace=str(transaction_env.parent))
    monkeypatch.setattr(
        routes,
        "publish_session_list_changed",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("notify failed")),
    )
    response = _start(session)
    assert response["session_id"] == session.session_id
    assert session.path.exists()


def test_failed_admission_preserves_successor_owner_and_state(transaction_env, monkeypatch):
    session = new_session(workspace=str(transaction_env.parent))
    successor = "successor-stream"
    successor_workspace = str(transaction_env / "successor-workspace")
    failed_stream = {}

    def install_successor_before_compensation(_self):
        failed_stream["id"] = session.active_stream_id
        config.register_session_writeback_owner(session.session_id, successor)
        session.pending_user_message = "successor"
        session.title = "successor title"
        session.workspace = successor_workspace
        session.successor_only_state = {"kept": True}
        session.save()
        raise RuntimeError("thread start rejected")

    monkeypatch.setattr(threading.Thread, "start", install_successor_before_compensation)
    with pytest.raises(RuntimeError, match="thread start rejected"):
        _start(session)
    assert config.session_writeback_owner(session.session_id) == successor
    assert failed_stream["id"]
    assert session.active_stream_id == successor
    assert failed_stream["id"] not in config.STREAMS
    assert session.pending_user_message == "successor"
    assert session.title == "successor title"
    assert session.workspace == successor_workspace
    assert session.successor_only_state == {"kept": True}
    reloaded = models.Session.load(session.session_id)
    assert reloaded.pending_user_message == "successor"
    assert reloaded.title == "successor title"
    assert reloaded.workspace == successor_workspace
    assert reloaded.active_stream_id == successor
