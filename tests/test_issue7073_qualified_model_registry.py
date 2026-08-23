"""Regression coverage for profile-owned qualified model parsing (#7073)."""

import json
from types import SimpleNamespace
from urllib.parse import urlparse

import api.config as config
import api.gateway_chat as gateway_chat
import api.profiles as profiles
import api.routes as routes


OLLAMA_CONFIG = {
    "model": {
        "provider": "ollama",
        "default": "hermes-reasoner:latest",
        "base_url": "http://ollama-owner:11434/v1",
    },
    "providers": {"ollama": {"models": ["hermes-reasoner:latest"]}},
}


def test_issue_model_is_one_built_in_custom_value_in_owning_registry():
    assert config._parse_provider_qualified_model_id(
        "@custom:hermes-reasoner:latest", OLLAMA_CONFIG
    ) == ("hermes-reasoner:latest", "custom")


def test_named_custom_provider_wins_over_built_in_custom_model():
    owner = {
        **OLLAMA_CONFIG,
        "custom_providers": [{"name": "hermes-reasoner"}],
    }
    assert config._parse_provider_qualified_model_id(
        "@custom:hermes-reasoner:latest", owner
    ) == ("latest", "custom:hermes-reasoner")


def test_profile_context_returns_owner_and_config_as_one_pair(monkeypatch):
    monkeypatch.setattr(profiles, "get_active_profile_name", lambda: "tls-owner")
    monkeypatch.setattr(
        profiles, "get_hermes_home_for_profile", lambda name: f"/profiles/{name}"
    )
    monkeypatch.setattr(
        config,
        "get_config_for_profile_home",
        lambda home: OLLAMA_CONFIG if str(home).endswith("tls-owner") else {"poison": True},
    )
    assert profiles.resolve_profile_config_context() == ("tls-owner", OLLAMA_CONFIG)


def test_missing_owner_config_is_empty_and_never_ambient(monkeypatch):
    monkeypatch.setattr(profiles, "get_active_profile_name", lambda: "missing")
    monkeypatch.setattr(profiles, "get_hermes_home_for_profile", lambda name: "/missing")
    monkeypatch.setattr(config, "get_config_for_profile_home", lambda home: {})
    assert profiles.resolve_profile_config_context() == ("missing", {})


def test_profile_validation_uses_live_catalog_with_source_config_for_parsing(monkeypatch):
    catalog = {
        "groups": [{
            "provider_id": "openai-codex",
            "models": [{"id": "gpt-5.5"}],
            "extra_models": [],
        }]
    }
    monkeypatch.setattr(profiles, "_get_available_models_for_profile_validation", lambda: catalog)
    profiles._validate_profile_model_selection(
        "gpt-5.5",
        "openai-codex",
        config_obj={"model": {"provider": "other", "default": "not-in-catalog"}},
    )


def test_invalid_body_profile_binds_persisted_owner_to_active_config(monkeypatch):
    monkeypatch.setattr(profiles, "get_active_profile_name", lambda: "live-owner")
    monkeypatch.setattr(profiles, "get_hermes_home_for_profile", lambda name: f"/{name}")
    monkeypatch.setattr(config, "get_config_for_profile_home", lambda home: OLLAMA_CONFIG)
    assert profiles.resolve_profile_config_context("../../other") == (
        "live-owner",
        OLLAMA_CONFIG,
    )


def test_isolated_context_persists_pinned_owner_with_pinned_config(monkeypatch):
    monkeypatch.setattr(profiles, "_is_isolated_profile_mode", lambda: True)
    monkeypatch.setattr(profiles, "_isolated_profile_name", lambda: "pinned")
    monkeypatch.setattr(profiles, "get_hermes_home_for_profile", lambda name: f"/{name}")
    monkeypatch.setattr(config, "get_config_for_profile_home", lambda home: OLLAMA_CONFIG)

    assert profiles.resolve_profile_config_context("requested") == ("pinned", OLLAMA_CONFIG)


def test_session_model_state_uses_explicit_owner_config(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        routes,
        "_resolve_compatible_session_model_state",
        lambda model, provider, **kwargs: captured.update(kwargs) or (model, provider, False),
    )
    routes._session_model_state_from_request(
        "@custom:hermes-reasoner:latest", None, profile_config=OLLAMA_CONFIG
    )
    assert captured["profile_config"] is OLLAMA_CONFIG


def test_gateway_boundary_uses_the_same_owner_config():
    assert gateway_chat._gateway_model_field(
        "@custom:hermes-reasoner:latest", OLLAMA_CONFIG
    ) == "hermes-reasoner:latest"


def test_context_length_lookup_keeps_persisted_owner_config(monkeypatch):
    captured = {}

    def lookup(model, provider, *, base_url=None, cfg):
        captured["cfg"] = cfg
        return SimpleNamespace(provider=provider, base_url="", api_key="")

    monkeypatch.setattr(routes, "_context_length_lookup_inputs_for_model", lookup)
    monkeypatch.setattr(
        config,
        "resolve_model_provider",
        lambda model: ("hermes-reasoner:latest", "custom", ""),
    )
    monkeypatch.setattr(
        config,
        "model_with_provider_context",
        lambda model, provider=None: model,
    )
    routes._session_context_length_lookup_state(
        "@custom:hermes-reasoner:latest", "custom", OLLAMA_CONFIG
    )
    assert captured["cfg"] is OLLAMA_CONFIG


def test_parser_preserves_endpoint_and_known_provider_shapes():
    assert config._parse_provider_qualified_model_id(
        "@custom:localhost:11434:llama3.2", OLLAMA_CONFIG
    ) == ("llama3.2", "custom:localhost:11434")
    assert config._parse_provider_qualified_model_id(
        "@openrouter:model-a:free", OLLAMA_CONFIG
    ) == ("model-a:free", "openrouter")


def test_gateway_body_contract_keeps_issue_model_bytes():
    body = {"model": gateway_chat._gateway_model_field(
        "@custom:hermes-reasoner:latest", OLLAMA_CONFIG
    ), "provider": "custom"}
    assert json.dumps(body, sort_keys=True) == (
        '{"model": "hermes-reasoner:latest", "provider": "custom"}'
    )


def test_session_new_route_uses_owner_config_when_body_profile_is_omitted(monkeypatch, tmp_path):
    """The production route must bind parsing before real Session creation."""
    captured = {}
    monkeypatch.setattr(
        profiles,
        "resolve_profile_config_context",
        lambda profile=None: ("tls-owner", OLLAMA_CONFIG),
    )
    monkeypatch.setattr(routes, "read_body", lambda _handler: {
        "model": "@custom:hermes-reasoner:latest",
        "worktree": False,
        "workspace": str(tmp_path),
    })
    monkeypatch.setattr(routes, "_check_csrf", lambda _handler: True)
    monkeypatch.setattr(routes, "_csrf_exempt_path", lambda _path: False)
    monkeypatch.setattr(routes, "_handle_extension_sidecar_proxy", lambda *_a, **_k: False)
    monkeypatch.setattr(routes, "_guard_request_session_visibility", lambda *_a, **_k: True)
    monkeypatch.setattr(routes, "_resolve_new_session_workspace", lambda *_a: str(tmp_path))
    monkeypatch.setattr(routes, "_session_id_visible_to_request_profile", lambda *_a, **_k: True)
    monkeypatch.setattr(routes, "j", lambda _handler, payload, **_kwargs: captured.setdefault("payload", payload))
    monkeypatch.setattr(routes, "bad", lambda _handler, message, status=400, **_kwargs: captured.setdefault("error", (status, message)))

    handler = SimpleNamespace(command="POST", headers={})
    routes.handle_post(handler, urlparse("/api/session/new"))

    session = captured["payload"]["session"]
    assert session["profile"] == "tls-owner"
    assert session["model"] == "@custom:hermes-reasoner:latest"
    assert session["model_provider"] == "custom"


def test_gateway_route_composes_bare_issue_model_from_session_owner(monkeypatch, tmp_path):
    """The real legacy Gateway builder must use the persisted session owner."""
    from tests.test_issue6722_provider_qualified_model_leak import _run_legacy_gateway_chat

    monkeypatch.setattr(
        profiles,
        "resolve_profile_config_context",
        lambda profile=None: ("tls-owner", OLLAMA_CONFIG),
    )
    original_get_session = gateway_chat.get_session

    def owner_session(session_id):
        session = original_get_session(session_id)
        session.profile = "tls-owner"
        return session

    monkeypatch.setattr(gateway_chat, "get_session", owner_session)
    payload = _run_legacy_gateway_chat(
        tmp_path, monkeypatch, "@custom:hermes-reasoner:latest"
    )
    assert payload["model"] == "hermes-reasoner:latest"
