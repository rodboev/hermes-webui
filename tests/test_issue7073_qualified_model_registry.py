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

CUSTOM_OWNER_CONFIG = {
    "model": {
        "provider": "custom:backup",
        "default": "org/model",
        "base_url": "https://backup.example/v1",
    },
    "custom_providers": [
        {
            "name": "backup",
            "base_url": "https://backup.example/v1",
            "key_env": "BACKUP_KEY",
            "models": "org/model",
        }
    ],
}


def test_owner_reconciliation_matrix():
    valid = config.resolve_owner_model_state(
        "@custom:backup:org/model", "stale", config_obj=CUSTOM_OWNER_CONFIG
    )
    repaired = config.resolve_owner_model_state(
        "@removed:mistral-large", "removed", config_obj=CUSTOM_OWNER_CONFIG
    )
    matching = config.resolve_owner_model_state(
        "org/model", "custom:backup", config_obj=CUSTOM_OWNER_CONFIG
    )
    assert (valid.outbound_model, valid.provider) == ("org/model", "custom:backup")
    assert repaired.repaired is True
    assert (repaired.outbound_model, repaired.provider) == ("org/model", "custom:backup")
    assert (matching.outbound_model, matching.provider) == ("org/model", "custom:backup")


def test_explicit_pick_unknown_qualifier_repairs_to_owner_default():
    state = config.resolve_owner_model_state(
        "@removed:mistral-large",
        "removed",
        config_obj=CUSTOM_OWNER_CONFIG,
        explicitly_picked=True,
    )
    assert state.repaired is True
    assert state.provider != "removed"


def test_session_reload_prefers_qualified_owner_connection(monkeypatch):
    monkeypatch.setenv("BACKUP_KEY", "owner-secret")
    state = config.resolve_owner_model_state(
        "@custom:backup:org/model", "custom", config_obj=CUSTOM_OWNER_CONFIG
    )
    assert (state.provider, state.base_url, state.api_key) == (
        "custom:backup",
        "https://backup.example/v1",
        "owner-secret",
    )


def test_owner_connection_uses_env_backed_key_from_same_entry(monkeypatch):
    monkeypatch.setenv("BACKUP_KEY", "env-secret")
    assert config.resolve_custom_provider_connection(
        "custom:backup", CUSTOM_OWNER_CONFIG
    ) == ("env-secret", "https://backup.example/v1")


def test_known_unconfigured_qualifier_is_not_removed():
    state = config.resolve_owner_model_state(
        "@openrouter:some/model", "openrouter", config_obj={}
    )
    assert state.provider == "openrouter"


def test_explicit_empty_owner_never_falls_back_to_ambient_config(monkeypatch):
    monkeypatch.setattr(config, "cfg", CUSTOM_OWNER_CONFIG)
    state = config.resolve_owner_model_state("org/model", config_obj={})
    catalog = config._static_models_catalog_without_live_probes({})
    assert state.provider is None
    assert state.base_url is None
    assert not catalog.get("groups")


def test_omitted_owner_config_preserves_ambient_resolver_contract(monkeypatch):
    monkeypatch.setattr(config, "cfg", CUSTOM_OWNER_CONFIG)
    resolved = config.resolve_model_provider("org/model")
    assert resolved[1] == "custom:backup"


def test_owner_connection_collision_still_fails_closed():
    owner = {
        "custom_providers": [
            {"name": "Backup", "base_url": "https://one.example"},
            {"name": "backup", "base_url": "https://two.example"},
        ]
    }
    try:
        config.resolve_custom_provider_connection("custom:backup", owner)
    except config.AmbiguousCustomProviderError:
        return
    raise AssertionError("colliding custom provider slugs must fail closed")


def test_reported_ollama_tagged_model_routes_with_owner(monkeypatch):
    """The reporter's tagged Ollama model stays whole and keeps its owner."""
    monkeypatch.setattr(config, "cfg", OLLAMA_CONFIG)
    resolved = config.resolve_owner_model_state(
        "@custom:hermes-reasoner:latest",
        stored_provider="custom",
        config_obj=OLLAMA_CONFIG,
    )
    assert (resolved.outbound_model, resolved.provider, resolved.base_url) == (
        "hermes-reasoner:latest",
        "ollama",
        "http://ollama-owner:11434/v1",
    )


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
    assert session["model_provider"] == "ollama"


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
