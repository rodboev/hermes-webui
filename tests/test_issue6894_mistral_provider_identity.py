"""Behavioral regression coverage for #6894's Mistral provider split."""

from __future__ import annotations

import json
import sys
import types
from types import SimpleNamespace

import api.config as config
import api.providers as providers
import api.routes as routes


def _install_agent_without_mistral(monkeypatch):
    fake_pkg = types.ModuleType("hermes_cli")
    fake_pkg.__path__ = []
    fake_models = types.ModuleType("hermes_cli.models")
    fake_models._PROVIDER_ALIASES = {}
    fake_models.list_available_providers = lambda: [
        {"id": "anthropic", "authenticated": True},
    ]
    fake_models.provider_model_ids = lambda _provider: []
    fake_auth = types.ModuleType("hermes_cli.auth")
    fake_auth.get_auth_status = lambda _provider: {"key_source": "env"}
    monkeypatch.setitem(sys.modules, "hermes_cli", fake_pkg)
    monkeypatch.setitem(sys.modules, "hermes_cli.models", fake_models)
    monkeypatch.setitem(sys.modules, "hermes_cli.auth", fake_auth)
    fake_pkg.models = fake_models
    fake_pkg.auth = fake_auth


def _catalog(monkeypatch, tmp_path, *, providers_cfg=None, configured_env=True):
    _install_agent_without_mistral(monkeypatch)
    if configured_env:
        monkeypatch.setenv("MISTRAL_API_KEY", "mistral-secret-test-value")
    else:
        monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    monkeypatch.setattr(config, "_models_cache_path", lambda: tmp_path / "models-cache.json")
    monkeypatch.setattr(config, "_get_config_path", lambda: tmp_path / "config.yaml")
    monkeypatch.setattr("api.profiles.get_active_hermes_home", lambda: tmp_path)
    if configured_env:
        (tmp_path / ".env").write_text("MISTRAL_API_KEY=mistral-secret-test-value\n")
    old_cfg = dict(config.cfg)
    old_mtime = config._cfg_mtime
    config.cfg.clear()
    config.cfg.update({"model": {}, "providers": providers_cfg or {}})
    config._cfg_mtime = 0.0
    config.invalidate_models_cache()
    try:
        return config.get_available_models(force_refresh=True)
    finally:
        config.cfg.clear()
        config.cfg.update(old_cfg)
        config._cfg_mtime = old_mtime
        config.invalidate_models_cache()


def test_mistral_key_catalog_save_session_dispatch_chain(monkeypatch, tmp_path):
    data = _catalog(monkeypatch, tmp_path)
    groups = [group for group in data["groups"] if group["provider_id"] == "mistral"]
    assert len(groups) == 1
    assert groups[0]["models"]
    assert config._resolve_provider_alias("mistralai") == "mistral"
    assert "mistralai" not in config.model_with_provider_context(
        "mistral-small-latest", "mistralai"
    )


def test_legacy_mistralai_normalizes_without_duplicate_group(monkeypatch, tmp_path):
    data = _catalog(monkeypatch, tmp_path, providers_cfg={"mistralai": {"api_key": "legacy"}})
    groups = [group for group in data["groups"] if group["provider_id"] == "mistral"]
    assert len(groups) == 1
    assert config._canonicalise_provider_id("mistralai") == "mistral"


def test_mistral_catalog_identity_is_stable_when_inactive(monkeypatch, tmp_path):
    monkeypatch.setattr(
        config, "_get_providers_cfg", lambda: {"mistral": {"api_key": "configured"}}
    )
    data = _catalog(
        monkeypatch,
        tmp_path,
        providers_cfg={"mistral": {"api_key": "configured"}},
        configured_env=False,
    )
    groups = [group for group in data["groups"] if group["provider_id"] == "mistral"]
    assert len(groups) == 1


def test_default_model_persists_canonical_mistral_provider(monkeypatch, tmp_path):
    _install_agent_without_mistral(monkeypatch)
    written = {}
    monkeypatch.setattr(config, "_get_config_path", lambda: tmp_path / "config.yaml")
    monkeypatch.setattr(
        config,
        "_load_yaml_config_file",
        lambda _path: {"model": {"provider": "anthropic", "base_url": "https://old.example/v1"}},
    )
    monkeypatch.setattr(config, "_save_yaml_config_file", lambda _path, value: written.update(value))
    monkeypatch.setattr(config, "resolve_model_provider", lambda _model: ("mistral-small-latest", None, None))
    monkeypatch.setattr(config, "reload_config", lambda: None)
    monkeypatch.setattr(config, "invalidate_models_cache", lambda: None)
    monkeypatch.setattr(
        sys.modules["hermes_cli.models"],
        "list_available_providers",
        lambda: [{"id": "anthropic", "authenticated": True}],
    )

    result = config.set_hermes_default_model("mistral-small-latest", provider="mistralai")

    assert result["provider"] == "mistral"
    assert written["model"]["provider"] == "mistral"
    assert written["model"]["base_url"] == "https://api.mistral.ai/v1"


def test_legacy_session_dispatches_through_canonical_mistral(monkeypatch):
    monkeypatch.setattr(config, "cfg", {"model": {"provider": "mistral"}}, raising=False)
    assert routes._clean_session_model_provider("mistralai") == "mistral"
    assert "mistralai" not in config.model_with_provider_context(
        "mistral-small-latest", "mistralai"
    )


def test_mistral_alias_does_not_rewrite_custom_provider_or_model_vendor_path():
    assert config._resolve_provider_alias("custom:mistral") == "custom:mistral"
    assert config._canonicalise_provider_id("custom:mistral") == "custom:mistral"
    assert config._canonicalise_provider_id("mistralai/mistral-large-latest") == (
        "mistralai/mistral-large-latest"
    )
    assert config._resolve_provider_alias("totally-unknown-provider") == "totally-unknown-provider"
    assert config._canonicalise_provider_id("qwen") == "qwen"
    assert config.model_with_provider_context("qwen/qwen3-coder", "qwen") == "qwen/qwen3-coder"


def test_live_dispatch_canonicalizes_legacy_provider(monkeypatch):
    fake_models = types.ModuleType("hermes_cli.models")
    fake_models.provider_model_ids = lambda provider: (
        ["mistral-small-latest"] if provider == "mistral" else []
    )
    monkeypatch.setitem(sys.modules, "hermes_cli.models", fake_models)
    monkeypatch.setattr(config, "get_config", lambda: {"model": {}, "providers": {}})
    monkeypatch.setattr(routes, "_get_cached_live_models", lambda _key: None)
    monkeypatch.setattr(routes, "_set_cached_live_models", lambda _key, _value: None)
    monkeypatch.setattr(routes, "j", lambda _handler, payload: payload)

    payload = routes._handle_live_models(
        None, SimpleNamespace(query="provider=mistralai")
    )

    assert payload["provider"] == "mistralai"
    assert [model["id"] for model in payload["models"]] == ["mistral-small-latest"]
    assert fake_models.provider_model_ids("mistral") == ["mistral-small-latest"]


def test_mistral_key_never_enters_public_state(monkeypatch, tmp_path):
    secret = "mistral-secret-test-value"
    data = _catalog(monkeypatch, tmp_path)
    public = json.dumps(data, sort_keys=True)
    assert secret not in public
    assert providers._provider_env_var_for("mistralai") == "MISTRAL_API_KEY"
    assert routes._OPENAI_COMPAT_ENDPOINTS["mistral"] == "https://api.mistral.ai/v1"
