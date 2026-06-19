"""Regression tests for the #4449 state-dir contract folded into #4454."""

from __future__ import annotations

import importlib
import os
from pathlib import Path

import api.config as config


def test_config_state_dir_defaults_to_hermes_home_webui(tmp_path):
    hermes_home = tmp_path / ".hermes" / "profiles" / "isolated"
    hermes_home.mkdir(parents=True)

    old_home = os.environ.get("HERMES_HOME")
    old_state_dir = os.environ.get("HERMES_WEBUI_STATE_DIR")
    try:
        os.environ["HERMES_HOME"] = str(hermes_home)
        os.environ.pop("HERMES_WEBUI_STATE_DIR", None)

        reloaded = importlib.reload(config)

        assert reloaded.STATE_DIR == (hermes_home / "webui").resolve()
    finally:
        if old_home is None:
            os.environ.pop("HERMES_HOME", None)
        else:
            os.environ["HERMES_HOME"] = old_home
        if old_state_dir is None:
            os.environ.pop("HERMES_WEBUI_STATE_DIR", None)
        else:
            os.environ["HERMES_WEBUI_STATE_DIR"] = old_state_dir
        importlib.reload(config)
