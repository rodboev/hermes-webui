from __future__ import annotations

import sys
import subprocess
import textwrap
import types
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[1]


def _profile_row(name: str, path: Path, *, is_default: bool = False):
    return SimpleNamespace(
        name=name,
        path=path,
        is_default=is_default,
        gateway_running=False,
        model=None,
        provider=None,
        has_env=False,
    )


def _install_fake_hermes_profiles(monkeypatch, rows):
    hermes_cli = types.ModuleType("hermes_cli")
    profiles_mod = types.ModuleType("hermes_cli.profiles")
    profiles_mod.list_profiles = lambda: rows
    monkeypatch.setitem(sys.modules, "hermes_cli", hermes_cli)
    monkeypatch.setitem(sys.modules, "hermes_cli.profiles", profiles_mod)


def test_profile_yaml_visible_false_is_exposed_as_hidden(monkeypatch, tmp_path):
    import api.profiles as profiles

    profiles._profile_visible_from_meta_cached.cache_clear()
    hidden = tmp_path / "profiles" / "worker-coder"
    visible = tmp_path / "profiles" / "human"
    missing = tmp_path / "profiles" / "missing-meta"
    malformed = tmp_path / "profiles" / "malformed"
    string_false = tmp_path / "profiles" / "string-false"
    for path in (hidden, visible, missing, malformed, string_false):
        path.mkdir(parents=True)
    (hidden / "profile.yaml").write_text("visible: false\n", encoding="utf-8")
    (visible / "profile.yaml").write_text("visible: true\n", encoding="utf-8")
    (malformed / "profile.yaml").write_text("visible: [\n", encoding="utf-8")
    (string_false / "profile.yaml").write_text('visible: "false"\n', encoding="utf-8")

    rows = [
        _profile_row("worker-coder", hidden),
        _profile_row("human", visible),
        _profile_row("missing-meta", missing),
        _profile_row("malformed", malformed),
        _profile_row("string-false", string_false),
    ]
    _install_fake_hermes_profiles(monkeypatch, rows)
    monkeypatch.setattr(profiles, "_get_profile_skills_stats", lambda _path: (0, 0))
    monkeypatch.setattr(profiles, "get_active_profile_name", lambda: "human")

    result = {row["name"]: row["visible"] for row in profiles.list_profiles_api()}

    assert result == {
        "worker-coder": False,
        "human": True,
        "missing-meta": True,
        "malformed": True,
        "string-false": True,
    }


def test_profile_yaml_visibility_cache_invalidates_when_file_changes(tmp_path):
    import api.profiles as profiles

    profiles._profile_visible_from_meta_cached.cache_clear()
    profile = tmp_path / "profiles" / "worker-coder"
    profile.mkdir(parents=True)
    meta = profile / "profile.yaml"

    meta.write_text("visible: false\n", encoding="utf-8")
    assert profiles._profile_visible_from_meta(profile) is False

    meta.write_text("visible: true\n", encoding="utf-8")
    assert profiles._profile_visible_from_meta(profile) is True


def test_default_profile_fallback_stays_visible(monkeypatch):
    import api.profiles as profiles

    monkeypatch.setattr(profiles, "_get_profile_skills_stats", lambda _path: (0, 0))

    assert profiles._default_profile_dict()["visible"] is True


def _panels_js() -> str:
    return (REPO_ROOT / "static" / "panels.js").read_text(encoding="utf-8")


def _run_node(script: str) -> None:
    result = subprocess.run(
        ["node", "-e", textwrap.dedent(script)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_profile_dropdown_filters_hidden_profiles_but_preserves_active():
    _run_node(
        r"""
        const fs = require('fs');
        const src = fs.readFileSync('static/panels.js', 'utf8');

        function extractFunction(signature) {
          const start = src.indexOf(signature);
          if (start < 0) throw new Error(signature + ' not found');
          const open = src.indexOf('{', start);
          let depth = 0;
          for (let i = open; i < src.length; i++) {
            if (src[i] === '{') depth++;
            else if (src[i] === '}') {
              depth--;
              if (depth === 0) return src.slice(start, i + 1);
            }
          }
          throw new Error('could not find end of ' + signature);
        }

        const dropdown = {
          children: [],
          _html: '',
          set innerHTML(value) { this._html = value; if (value === '') this.children = []; },
          get innerHTML() { return this._html; },
          appendChild(el) { this.children.push(el); },
        };
        function $(id) { return id === 'profileDropdown' ? dropdown : null; }
        const S = { activeProfile: 'worker-active' };
        const document = {
          createElement() {
            return {
              className: '',
              _html: '',
              set innerHTML(value) { this._html = value; },
              get innerHTML() { return this._html; },
              onclick: null,
            };
          },
        };
        function esc(value) { return String(value); }
        function t(key, value) { return key === 'profile_skill_count' ? `${value} skills` : key; }
        function li() { return ''; }
        function closeProfileDropdown() {}
        function switchToProfile() {}
        function mobileSwitchPanel() {}

        eval(extractFunction('function renderProfileDropdown(data)'));
        renderProfileDropdown({
          active: 'worker-active',
          profiles: [
            { name: 'worker-idle', visible: false, gateway_running: false },
            { name: 'worker-active', visible: false, gateway_running: false },
            { name: 'human', visible: true, gateway_running: true },
          ],
        });

        const profileOptions = dropdown.children.filter((child) => (
          child.className.startsWith('profile-opt') && !child.className.includes('ws-manage')
        ));
        const html = profileOptions.map((child) => child.innerHTML).join('\n');
        if (profileOptions.length !== 2) throw new Error('expected two rendered profile options');
        if (!html.includes('worker-active')) throw new Error('active hidden profile was not preserved');
        if (!html.includes('human')) throw new Error('visible profile was not rendered');
        if (html.includes('worker-idle')) throw new Error('inactive hidden profile was rendered');
        if (!profileOptions[0].className.includes('active')) throw new Error('active option was not marked active');
        """
    )


def test_profiles_management_panel_renders_all_profiles_and_marks_hidden():
    _run_node(
        r"""
        const fs = require('fs');
        const src = fs.readFileSync('static/panels.js', 'utf8');

        function extractFunction(signature) {
          const start = src.indexOf(signature);
          if (start < 0) throw new Error(signature + ' not found');
          const open = src.indexOf('{', start);
          let depth = 0;
          for (let i = open; i < src.length; i++) {
            if (src[i] === '{') depth++;
            else if (src[i] === '}') {
              depth--;
              if (depth === 0) return src.slice(start, i + 1);
            }
          }
          throw new Error('could not find end of ' + signature);
        }

        const panel = {
          children: [],
          _html: '',
          set innerHTML(value) { this._html = value; if (value === '') this.children = []; },
          get innerHTML() { return this._html; },
          appendChild(el) { this.children.push(el); },
        };
        function $(id) { return id === 'profilesPanel' ? panel : null; }
        var _profilesCache = null;
        var _profileMode = 'read';
        var _currentProfileDetail = null;
        const S = { activeProfile: 'human' };
        const document = {
          createElement() {
            return {
              className: '',
              dataset: {},
              style: {},
              _html: '',
              set innerHTML(value) { this._html = value; },
              get innerHTML() { return this._html; },
              onclick: null,
            };
          },
        };
        function esc(value) { return String(value); }
        function t(key, value) {
          const labels = {
            profile_active: 'ACTIVE',
            profile_hidden_from_chat: 'Hidden from chat',
            profile_no_configuration: 'No configuration',
            profile_gateway_running: 'Gateway running',
            profile_gateway_stopped: 'Gateway stopped',
            profile_skill_count: `${value} skills`,
          };
          return labels[key] || key;
        }
        async function api() {
          return {
            active: 'human',
            profiles: [
              { name: 'worker-idle', visible: false, gateway_running: false },
              { name: 'human', visible: true, gateway_running: true },
            ],
          };
        }

        eval(extractFunction('async function loadProfilesPanel()'));
        (async () => {
          await loadProfilesPanel();
          const cards = panel.children.filter((child) => child.dataset && child.dataset.name);
          const names = cards.map((card) => card.dataset.name).sort().join(',');
          if (names !== 'human,worker-idle') throw new Error('management panel did not render all profiles: ' + names);
          const hidden = cards.find((card) => card.dataset.name === 'worker-idle');
          const human = cards.find((card) => card.dataset.name === 'human');
          if (!hidden.innerHTML.includes('Hidden from chat')) throw new Error('hidden management badge missing');
          if (human.innerHTML.includes('Hidden from chat')) throw new Error('visible profile was marked hidden');
        })().catch((err) => {
          console.error(err.stack || err.message);
          process.exit(1);
        });
        """
    )
