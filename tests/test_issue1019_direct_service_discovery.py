"""Direct-service regressions for issue #1019."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import venv
from pathlib import Path


REPO_ROOT = Path(__file__).parents[1].resolve()


def _run_hidden(argv, *, cwd, env):
    kwargs = {"capture_output": True, "text": True, "cwd": cwd, "env": env}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    return subprocess.run(argv, **kwargs)


def _write_agent_stubs(venv_python: Path, root: Path) -> None:
    site_packages = Path(
        _run_hidden(
            [str(venv_python), "-c", "import site; print(site.getsitepackages()[0])"],
            cwd=REPO_ROOT,
            env=os.environ.copy(),
        ).stdout.strip()
    )
    site_packages.mkdir(parents=True, exist_ok=True)
    (site_packages / "run_agent.py").write_text(
        "DIRECT_SERVICE_STUB = True\n", encoding="utf-8"
    )
    hermes_cli = site_packages / "hermes_cli"
    hermes_cli.mkdir(exist_ok=True)
    (hermes_cli / "__init__.py").write_text("DIRECT_SERVICE_STUB = True\n", encoding="utf-8")
    (root / "isolated-webui").mkdir()


def test_direct_service_uses_active_agent_venv_identity(tmp_path):
    """A real Agent venv must produce one identity for both production wrappers."""
    venv_root = tmp_path / "agent-venv"
    venv.EnvBuilder(with_pip=False, clear=True).create(venv_root)
    venv_python = venv_root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    _write_agent_stubs(venv_python, tmp_path)

    child = textwrap.dedent(
        """
        import contextlib
        import io
        import json
        import os
        import pathlib
        import sys
        from unittest.mock import patch

        import bootstrap

        isolated = pathlib.Path(os.environ["ISSUE1019_ISOLATED_ROOT"])
        real_exists = pathlib.Path.exists

        def isolated_exists(path):
            agent_root = pathlib.Path(os.environ["ISSUE1019_AGENT_ROOT"])
            if "hermes-agent" in path.parts and agent_root not in path.parents:
                return False
            return real_exists(path)

        bootstrap.REPO_ROOT = isolated / "isolated-webui"
        with patch.object(pathlib.Path, "exists", isolated_exists):
            bootstrap_result = bootstrap.discover_agent_dir()
            import api.config as config

        config.REPO_ROOT = isolated / "isolated-webui"
        with contextlib.redirect_stdout(io.StringIO()) as captured:
            config.print_startup_config()
        print(json.dumps({
            "bootstrap": str(bootstrap_result) if bootstrap_result else None,
            "config": str(config._AGENT_DIR) if config._AGENT_DIR else None,
            "found": config._HERMES_FOUND,
            "imports": config.verify_hermes_imports(),
            "banner": captured.getvalue(),
        }))
        """
    )
    child_script = tmp_path / "direct-service-child.py"
    child_script.write_text(child, encoding="utf-8")
    env = os.environ.copy()
    env.update(
        {
            "PYTHONPATH": str(REPO_ROOT),
            "HERMES_HOME": str(tmp_path / "empty-hermes-home"),
            "XDG_DATA_HOME": str(tmp_path / "empty-xdg"),
            "HERMES_WEBUI_AGENT_DIR": "",
            "HERMES_WEBUI_PYTHON": "",
            "PATH": str(tmp_path / "empty-path"),
            "ISSUE1019_ISOLATED_ROOT": str(tmp_path),
            "ISSUE1019_AGENT_ROOT": str(tmp_path / "agent-root-never-used"),
        }
    )
    result = _run_hidden([str(venv_python), str(child_script)], cwd=REPO_ROOT, env=env)
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)

    agent_origin = next(
        line for line in result.stdout.splitlines() if line.startswith("{")
    )
    assert json.loads(agent_origin) == observed
    assert observed["bootstrap"] == observed["config"]
    assert observed["found"] is True
    assert observed["imports"][0] is True
    assert "agent dir   : " in observed["banner"]
    assert "NOT FOUND" not in observed["banner"]
    assert "agent-venv" in observed["bootstrap"]


def test_interpreter_only_identity_does_not_authorize_auto_install(monkeypatch, tmp_path):
    """The mutation lookup remains limited to explicit and HERMES_HOME roots."""
    from api import startup

    monkeypatch.delenv("HERMES_WEBUI_AGENT_DIR", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "empty-home"))
    monkeypatch.setenv("HERMES_WEBUI_PYTHON", str(tmp_path / "agent-python"))
    assert startup._agent_dir() is None


def test_discovery_checks_hermes_home_without_explicit_override(monkeypatch, tmp_path):
    from api import startup

    monkeypatch.delenv("HERMES_WEBUI_AGENT_DIR", raising=False)
    agent_root = tmp_path / "hermes-home" / "hermes-agent"
    agent_root.mkdir(parents=True)
    (agent_root / "run_agent.py").write_text("", encoding="utf-8")
    assert startup.discover_agent_dir(
        repo_root=tmp_path / "webui",
        hermes_home=tmp_path / "hermes-home",
        user_home=tmp_path / "home",
        launcher_finder=lambda: None,
        python_finder=lambda _python: None,
    ) == agent_root.resolve()
