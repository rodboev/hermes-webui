"""Regression test: credential_pool phantom providers with non-model credentials.

Guards the regression from #4247 where non-model credential_pool keys
(e.g., Photon plugin messaging tokens) were incorrectly treated as phantom
model providers and assigned the full global model catalog.

The pool detection loop must gate pool keys to known model providers.
The group-builder else fallback must not assign the global catalog to
unknown providers.

Note: `_build_available_models_uncached` does `from agent.credential_pool
import load_pool` at call time. The bundled `agent` package is importable
locally but NOT in CI's environment, so the test injects a stub
`agent.credential_pool` module into sys.modules.
"""
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import api.config as config  # noqa: E402


class _FakeEntry:
    def __init__(self, source="config_yaml", label="anthropic", key_source="config_yaml"):
        self.source = source
        self.label = label
        self.key_source = key_source


class _FakePool:
    def __init__(self, entries):
        self._entries = entries

    def entries(self):
        return list(self._entries)


def test_phantom_providers_excluded_from_model_picker(monkeypatch, tmp_path):
    """Non-model credential_pool keys must not appear as phantom providers.

    The Photon plugin writes credential_pool.photon, credential_pool.photon_project,
    and credential_pool.photon_user (messaging-platform credentials). These are NOT
    model providers and should not appear in the picker, regardless of whether they
    have pool entries.

    A real model provider (e.g., anthropic) with pool credentials SHOULD still appear.
    """
    import json
    from pathlib import Path

    config._CREDENTIAL_POOL_CACHE.clear()

    # Create mock auth.json with both phantom and real providers
    auth_store = {
        "credential_pool": {
            "anthropic": [{"key": "sk-ant-test-123", "source": "config_yaml", "label": "anthropic"}],
            "photon": [{"key": "photon_token", "source": "config_yaml", "label": "photon"}],
            "photon_project": [{"key": "proj_id", "source": "config_yaml", "label": "photon_project"}],
            "photon_user": [{"key": "user_id", "source": "config_yaml", "label": "photon_user"}],
        },
        "active_provider": None,
    }

    # Write auth.json to temp directory
    auth_store_path = tmp_path / "auth.json"
    auth_store_path.write_text(json.dumps(auth_store), encoding="utf-8")

    # Mock the auth store path
    monkeypatch.setattr(config, "_get_auth_store_path", lambda: Path(str(auth_store_path)))
    monkeypatch.setattr(config, "_resolve_provider_alias", lambda p: p)
    monkeypatch.setattr(config, "_get_provider_cfg", lambda p: {})
    monkeypatch.setattr(config, "_read_live_provider_model_ids", lambda p: [])

    # Inject stub agent.credential_pool
    def _fake_load_pool(provider):
        if provider == "anthropic":
            return _FakePool([_FakeEntry(label="anthropic")])
        elif provider in ("photon", "photon_project", "photon_user"):
            return _FakePool([_FakeEntry(label=provider)])
        return _FakePool([])

    fake_cp = types.ModuleType("agent.credential_pool")
    fake_cp.load_pool = _fake_load_pool
    fake_agent = sys.modules.get("agent")
    created_agent = False
    if fake_agent is None:
        fake_agent = types.ModuleType("agent")
        fake_agent.__path__ = []
        monkeypatch.setitem(sys.modules, "agent", fake_agent)
        created_agent = True
    monkeypatch.setitem(sys.modules, "agent.credential_pool", fake_cp)
    if not created_agent:
        monkeypatch.setattr(fake_agent, "credential_pool", fake_cp, raising=False)

    # Call the model-building function
    models_result = config.get_available_models()

    # Extract provider IDs from the returned groups
    groups = models_result.get("groups", [])
    provider_ids = {group.get("provider_id") for group in groups if isinstance(group, dict)}

    # Photon* entries should NOT be in the picker (check both raw and hyphenated forms
    # since _resolve_provider_alias may normalize underscores to hyphens in production)
    assert "photon" not in provider_ids, "photon should not appear as a phantom provider"
    assert "photon_project" not in provider_ids, "photon_project should not appear as a phantom provider"
    assert "photon_user" not in provider_ids, "photon_user should not appear as a phantom provider"
    assert "photon-project" not in provider_ids, "photon-project should not appear as a phantom provider"
    assert "photon-user" not in provider_ids, "photon-user should not appear as a phantom provider"

    # Real provider SHOULD still be in the picker
    assert "anthropic" in provider_ids, "anthropic should appear in the picker"

    config._CREDENTIAL_POOL_CACHE.clear()


def test_is_known_model_provider():
    """_is_known_model_provider must correctly identify real vs phantom providers."""
    # Real providers should return True
    assert config._is_known_model_provider("anthropic") is True
    assert config._is_known_model_provider("openai") is True
    assert config._is_known_model_provider("openai-api") is True
    assert config._is_known_model_provider("custom:myendpoint") is True

    # Phantom/unknown providers should return False
    assert config._is_known_model_provider("photon") is False
    assert config._is_known_model_provider("photon_project") is False
    assert config._is_known_model_provider("photon_user") is False
    assert config._is_known_model_provider("unknown_provider") is False

    # Empty/None should return False
    assert config._is_known_model_provider("") is False
    assert config._is_known_model_provider(None) is False
