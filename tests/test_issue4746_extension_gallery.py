"""Behavioral tests for the extension gallery install/uninstall/registry feature."""

import hashlib
import io
import json
import zipfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest


def _make_zip(files: dict) -> bytes:
    """Build an in-memory zip containing the given {name: content} mapping."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


def _setup_ext_env(monkeypatch, tmp_path):
    """Point extension root and state dir at tmp_path subdirectories."""
    ext_dir = tmp_path / "extensions"
    ext_dir.mkdir()
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    monkeypatch.setenv("HERMES_WEBUI_EXTENSION_DIR", str(ext_dir))
    monkeypatch.setenv("HERMES_WEBUI_STATE_DIR", str(state_dir))
    import api.extensions as ext_mod
    monkeypatch.setattr(ext_mod, "_extension_state_dir", lambda: state_dir)
    return ext_dir, state_dir


def test_install_valid(monkeypatch, tmp_path):
    ext_dir, state_dir = _setup_ext_env(monkeypatch, tmp_path)
    import api.extensions as ext_mod

    files = {
        "manifest.json": json.dumps({"version": "1.2.3"}),
        "index.js": "console.log('hello');",
    }
    zip_bytes = _make_zip(files)
    sha = hashlib.sha256(zip_bytes).hexdigest()

    mock_resp = MagicMock()
    mock_resp.read.return_value = zip_bytes
    monkeypatch.setattr(ext_mod, "urlopen", lambda *a, **kw: mock_resp)

    result = ext_mod.install_extension(
        "my-ext",
        "https://hermes-webui.github.io/exts/my-ext.zip",
        sha,
    )
    assert result["installed"] is True
    assert result["id"] == "my-ext"
    assert result["version"] == "1.2.3"
    assert (ext_dir / "my-ext" / "index.js").exists()
    manifest = ext_mod._load_install_manifest()
    assert "my-ext" in manifest["installed"]
    assert "index.js" in manifest["installed"]["my-ext"]["files"]


def test_install_bad_hash(monkeypatch, tmp_path):
    ext_dir, state_dir = _setup_ext_env(monkeypatch, tmp_path)
    import api.extensions as ext_mod

    zip_bytes = _make_zip({"index.js": "code"})
    wrong_sha = "a" * 64

    mock_resp = MagicMock()
    mock_resp.read.return_value = zip_bytes
    monkeypatch.setattr(ext_mod, "urlopen", lambda *a, **kw: mock_resp)

    with pytest.raises(ext_mod.ExtensionInstallError, match="SHA-256"):
        ext_mod.install_extension(
            "bad-ext",
            "https://hermes-webui.github.io/exts/bad-ext.zip",
            wrong_sha,
        )
    assert not (ext_dir / "bad-ext").exists()


def test_install_zipslip(monkeypatch, tmp_path):
    ext_dir, state_dir = _setup_ext_env(monkeypatch, tmp_path)
    import api.extensions as ext_mod

    # Build a zip with a path-traversal member
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../../evil.txt", "pwned")
    zip_bytes = buf.getvalue()
    sha = hashlib.sha256(zip_bytes).hexdigest()

    mock_resp = MagicMock()
    mock_resp.read.return_value = zip_bytes
    monkeypatch.setattr(ext_mod, "urlopen", lambda *a, **kw: mock_resp)

    with pytest.raises(ext_mod.ExtensionInstallError):
        ext_mod.install_extension(
            "slip-ext",
            "https://hermes-webui.github.io/exts/slip-ext.zip",
            sha,
        )
    assert not (tmp_path / "evil.txt").exists()


def test_uninstall(monkeypatch, tmp_path):
    ext_dir, state_dir = _setup_ext_env(monkeypatch, tmp_path)
    import api.extensions as ext_mod

    files = {"index.js": "code", "style.css": "body{}"}
    zip_bytes = _make_zip(files)
    sha = hashlib.sha256(zip_bytes).hexdigest()

    mock_resp = MagicMock()
    mock_resp.read.return_value = zip_bytes
    monkeypatch.setattr(ext_mod, "urlopen", lambda *a, **kw: mock_resp)

    ext_mod.install_extension(
        "rm-ext",
        "https://hermes-webui.github.io/exts/rm-ext.zip",
        sha,
    )
    assert (ext_dir / "rm-ext" / "index.js").exists()

    result = ext_mod.uninstall_extension("rm-ext")
    assert result["uninstalled"] is True
    assert not (ext_dir / "rm-ext" / "index.js").exists()
    assert not (ext_dir / "rm-ext" / "style.css").exists()
    manifest = ext_mod._load_install_manifest()
    assert "rm-ext" not in manifest["installed"]


def test_install_rollback(monkeypatch, tmp_path):
    ext_dir, state_dir = _setup_ext_env(monkeypatch, tmp_path)
    import api.extensions as ext_mod

    files = {"first.js": "a", "second.js": "b"}
    zip_bytes = _make_zip(files)
    sha = hashlib.sha256(zip_bytes).hexdigest()

    mock_resp = MagicMock()
    mock_resp.read.return_value = zip_bytes
    monkeypatch.setattr(ext_mod, "urlopen", lambda *a, **kw: mock_resp)

    write_count = [0]
    original_write_bytes = Path.write_bytes

    def patched_write_bytes(self, data):
        write_count[0] += 1
        if write_count[0] >= 2:
            raise OSError("simulated write failure")
        return original_write_bytes(self, data)

    monkeypatch.setattr(Path, "write_bytes", patched_write_bytes)

    with pytest.raises(ext_mod.ExtensionInstallError, match="Extraction failed"):
        ext_mod.install_extension(
            "roll-ext",
            "https://hermes-webui.github.io/exts/roll-ext.zip",
            sha,
        )
    remaining = list((ext_dir / "roll-ext").glob("**/*")) if (ext_dir / "roll-ext").exists() else []
    assert remaining == []


def test_gallery_render(monkeypatch, tmp_path):
    import api.extensions as ext_mod

    registry_data = [
        {"id": "ext-one", "name": "Extension One", "version": "0.1.0", "description": "First"},
        {"id": "ext-two", "name": "Extension Two", "version": "0.2.0", "description": "Second"},
    ]

    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(registry_data).encode("utf-8")

    # Clear cache so we always do a fresh fetch
    ext_mod._REGISTRY_CACHE.clear()
    monkeypatch.setattr(ext_mod, "urlopen", lambda *a, **kw: mock_resp)

    result = ext_mod.get_extension_registry()
    assert "entries" in result
    assert len(result["entries"]) == 2
    assert result["entries"][0]["id"] == "ext-one"
    assert result["entries"][1]["id"] == "ext-two"
